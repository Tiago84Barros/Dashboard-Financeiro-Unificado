"""
data_pipeline/market/troca_codigo.py — troca de código B3 no banco.

A regra (o que é troca, qual código é o atual, de onde sai a classificação
setorial) mora em ``core.b3_troca_codigo``. Aqui fica o que toca o banco:

* ``carregar_trocas`` lê as trocas já registradas em ``market.ticker_alias``;
* ``e_troca`` prova uma troca pelo cadastro (mesma companhia, mesma classe);
* ``unificar`` dá ao histórico um dono só: passa as linhas de fato do código
  antigo para o atual, desativa o antigo, registra o alias antigo → atual,
  apaga o alias legado invertido e alinha a classificação em
  ``public.setores``.

``unificar(aplicar=False)`` só conta — é o dry-run da migração. Com
``aplicar=True`` grava na transação do chamador. Idempotente: na segunda vez
não há linha a mover nem ativo a desativar.

Antes de mudar qualquer linha, ``aplicar=True`` guarda em
``market.troca_codigo_backup`` (um lote por par) a linha inteira de tudo o que
vai mexer: ativos, alias e setores dos dois códigos como estavam, e cada linha
de fato do código antigo, marcada ``mover`` ou ``descartar``. Confere o backup
contra a contagem antes de gravar e a gravação contra o backup depois; se não
fecham, levanta e a transação inteira volta. ``reverter(lote)`` devolve o
banco ao que o lote guardou.

O ``adjusted_close`` de cada código vem com a base de ajuste do provedor. Se
o do código atual estiver noutra base, o histórico movido emendaria com um
salto; ``unificar`` mede a razão atual/antigo nos pregões comuns (mediana) e
reescala as linhas movidas por ela.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from core.b3_troca_codigo import (
    MOTIVO_TROCA,
    PARES_CONHECIDOS,
    escolher_classificacao,
    fechar_cadeias,
    mesma_classe,
    normalizar,
)
from data_pipeline.market.repository import _NATURAL_KEY

logger = logging.getLogger(__name__)

# Tabelas de fato e o que identifica a linha além do ticker (as restrições
# únicas de repository). As safras de indicadores não têm chave única: mesma
# métrica, mesmo período, mesmo valor e mesma disponibilidade é a mesma safra.
FATOS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (t, tuple(c for c in _NATURAL_KEY[t] if c != "ticker"))
    for t in ("historical_prices", "historical_price_observations",
              "income_statements", "balance_sheets", "cash_flow_statements",
              "dividends", "calculated_metrics")
) + (("calculated_metric_vintages",
      ("period", "year", "quarter", "metric_name", "metric_value", "available_at")),)

_CLASSIFICACAO = ("SETOR", "SUBSETOR", "SEGMENTO")
_TK_SETORES = "UPPER(REPLACE(ticker, '.SA', ''))"

BACKUP = "market.troca_codigo_backup"
_DDL_BACKUP = f"""
CREATE TABLE IF NOT EXISTS {BACKUP} (
    id           bigserial PRIMARY KEY,
    lote         text NOT NULL,
    antigo       text NOT NULL,
    novo         text NOT NULL,
    tabela       text NOT NULL,
    acao         text NOT NULL,      -- abrir | antes | mover | descartar
    linha        jsonb NOT NULL,
    criado_em    timestamptz NOT NULL DEFAULT now(),
    revertido_em timestamptz
);
CREATE INDEX IF NOT EXISTS troca_codigo_backup_lote_idx ON {BACKUP} (lote);
"""
# Linhas guardadas como estavam (acao = 'antes'): o que unificar altera fora
# das tabelas de fato. Na reversão voltam inteiras.
_ESTADO = (("market.assets", "ticker IN (:a, :n)"),
           ("market.ticker_alias", "brapi_symbol IN (:a, :n)"),
           ("public.setores", f"{_TK_SETORES} IN (:a, :n)"))

# Razão de base do adjusted_close abaixo disto é arredondamento, não base.
TOLERANCIA_BASE = 1e-6


def carregar_trocas(conn) -> dict[str, str]:
    """{antigo: atual} das trocas gravadas com ``MOTIVO_TROCA``.

    Só o que está no banco — ``PARES_CONHECIDOS`` fica de fora: pular um
    código antigo que ainda não foi unificado deixaria o histórico dele
    parado. Sem a tabela, ``{}``.
    """
    try:
        if not conn.execute(text(
                "SELECT to_regclass('market.ticker_alias') IS NOT NULL")).scalar():
            return {}
        rows = conn.execute(text(
            "SELECT brapi_symbol, b3_ticker FROM market.ticker_alias WHERE motivo = :m"),
            {"m": MOTIVO_TROCA}).fetchall()
    except Exception as exc:
        logger.warning("trocas de código: market.ticker_alias ilegível: %s", exc)
        return {}
    mapa = {}
    for s, b in rows:
        s, b = normalizar(s), normalizar(b)
        if s and b and s != b and mesma_classe(s, b):
            mapa[s] = b
    return fechar_cadeias(mapa)


def mesma_companhia(conn, cvm_map: dict | None, a: str, b: str) -> bool:
    """Código CVM quando os dois são conhecidos; senão o company_id dos ativos."""
    ca, cb = (cvm_map or {}).get(a), (cvm_map or {}).get(b)
    if ca is not None and cb is not None:
        return int(ca) == int(cb)
    rows = conn.execute(text(
        "SELECT ticker, company_id FROM market.assets "
        "WHERE ticker IN (:a, :b) AND company_id IS NOT NULL"), {"a": a, "b": b}).fetchall()
    cid = {normalizar(t): c for t, c in rows}
    return a in cid and b in cid and cid[a] == cid[b]


def e_troca(conn, cvm_map: dict | None, antigo: str, novo: str,
            trocas: dict[str, str] | None = None) -> bool:
    """``novo`` substitui ``antigo``? Mesma classe, mesma companhia, e
    ``novo`` não é ele mesmo um código já substituído (a brapi devolver o
    código velho para o novo não pode inverter a troca)."""
    antigo, novo = normalizar(antigo), normalizar(novo)
    if antigo == novo or not mesma_classe(antigo, novo):
        return False
    if novo in PARES_CONHECIDOS or novo in (trocas or {}):
        return False
    return mesma_companhia(conn, cvm_map, antigo, novo)


def _colunas(conn) -> dict[str, set[str]]:
    nomes = [t for t, _ in FATOS] + ["assets", "ticker_alias", "setores"]
    lista = ", ".join(f"'{n}'" for n in nomes)
    rows = conn.execute(text(f"""
        SELECT table_schema || '.' || table_name, column_name
        FROM information_schema.columns
        WHERE table_schema IN ('market', 'public') AND table_name IN ({lista})
    """)).fetchall()
    out: dict[str, set[str]] = {}
    for tabela, coluna in rows:
        out.setdefault(tabela, set()).add(coluna)
    return out


def _base_ajuste(conn, cols: dict[str, set[str]], p: dict) -> dict | str:
    """Razão adjusted_close atual/antigo nos pregões comuns (mediana).

    Fator 1 é a mesma base. Outro fator reescala as linhas movidas; sem
    pregão comum não há como conferir e nada é reescalado."""
    if "adjusted_close" not in (cols.get("market.historical_prices") or set()):
        return "sem adjusted_close"
    fator, comuns = conn.execute(text("""
        SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (n.adjusted_close / o.adjusted_close)::float8),
               COUNT(*)
        FROM market.historical_prices o
        JOIN market.historical_prices n ON n.ticker = :n AND n.date = o.date
        WHERE o.ticker = :a AND o.adjusted_close > 0 AND n.adjusted_close > 0
    """), p).fetchone()
    comuns = int(comuns or 0)
    if not comuns or fator is None:
        return {"fator": None, "comuns": 0, "reescala": False}
    fator = float(fator)
    return {"fator": fator, "comuns": comuns, "reescala": abs(fator - 1.0) > TOLERANCIA_BASE}


def _fatos(conn, cols: dict[str, set[str]], p: dict, aplicar: bool,
           lote: str | None = None, base: dict | str | None = None) -> dict:
    reescala = isinstance(base, dict) and base["reescala"]
    out = {}
    for tabela, chave in FATOS:
        tem = cols.get(f"market.{tabela}")
        if not tem:
            out[tabela] = "tabela ausente"
            continue
        if not {"ticker", *chave} <= tem:
            out[tabela] = "sem as colunas da chave"
            continue
        casa = " AND ".join(f"n.{c} IS NOT DISTINCT FROM o.{c}" for c in chave)
        existe = f"EXISTS (SELECT 1 FROM market.{tabela} n WHERE n.ticker = :n AND {casa})"
        if aplicar:
            fator = base["fator"] if reescala and tabela == "historical_prices" else None
            out[tabela] = _mover_com_backup(conn, tabela, existe, p, lote, fator)
            continue
        mover, descartar = conn.execute(text(
            f"SELECT COUNT(*) FILTER (WHERE NOT {existe}), COUNT(*) FILTER (WHERE {existe}) "
            f"FROM market.{tabela} o WHERE o.ticker = :a"), p).fetchone()
        out[tabela] = {"mover": int(mover or 0), "descartar": int(descartar or 0)}
    return out


def _mover_com_backup(conn, tabela: str, existe: str, p: dict, lote: str,
                      fator: float | None) -> dict:
    """Guarda cada linha do código antigo, depois move ou descarta. As três
    contagens (linhas, backup, gravação) têm de fechar."""
    total = conn.execute(text(
        f"SELECT COUNT(*) FROM market.{tabela} WHERE ticker = :a"), p).scalar() or 0
    guardou = dict(conn.execute(text(f"""
        WITH g AS (
            INSERT INTO {BACKUP} (lote, antigo, novo, tabela, acao, linha)
            SELECT :lote, :a, :n, :t,
                   CASE WHEN {existe} THEN 'descartar' ELSE 'mover' END, to_jsonb(o)
            FROM market.{tabela} o WHERE o.ticker = :a
            RETURNING acao)
        SELECT acao, COUNT(*) FROM g GROUP BY acao"""),
        {**p, "lote": lote, "t": f"market.{tabela}"}).fetchall())
    if sum(guardou.values()) != total:
        raise RuntimeError(f"backup de market.{tabela} guardou {sum(guardou.values())} "
                           f"de {total} linhas de {p['a']}")
    ajuste = ", adjusted_close = adjusted_close * :f" if fator is not None else ""
    mover = conn.execute(text(
        f"UPDATE market.{tabela} AS o SET ticker = :n{ajuste} "
        f"WHERE o.ticker = :a AND NOT {existe}"), {**p, "f": fator}).rowcount
    descartar = conn.execute(text(
        f"DELETE FROM market.{tabela} WHERE ticker = :a"), p).rowcount
    if (mover, descartar) != (guardou.get("mover", 0), guardou.get("descartar", 0)):
        raise RuntimeError(f"market.{tabela}: gravou mover {mover} / descartar {descartar}, "
                           f"backup diz {guardou}")
    return {"mover": int(mover), "descartar": int(descartar)}


def _alias(conn, cols, p: dict, codigo_cvm, aplicar: bool) -> dict | str:
    if "market.ticker_alias" not in cols:
        return "tabela ausente"
    antigo, novo = p["a"], p["n"]
    linhas = [(normalizar(s), normalizar(b), m or "") for s, b, m in conn.execute(text(
        "SELECT brapi_symbol, b3_ticker, motivo FROM market.ticker_alias "
        "WHERE brapi_symbol IN (:a, :n)"), p).fetchall()]
    ja = any(s == antigo and b == novo and m == MOTIVO_TROCA for s, b, m in linhas)
    rel = {
        "gravar": None if ja else f"{antigo}->{novo}",
        # direção legada (brapi_symbol = novo): mandaria o histórico do
        # código atual para o antigo em fix_orphan_alias_tickers e nas notícias
        "apagar": [f"{s}->{b} ({m})" for s, b, m in linhas if s == novo and b == antigo],
        # alias do código atual para OUTRA companhia (incorporação): não é
        # troca, fica — e vai como pendência no relatório
        "terceiros": [f"{s}->{b} ({m})" for s, b, m in linhas if s == novo and b != antigo],
    }
    if aplicar:
        conn.execute(text("""
            INSERT INTO market.ticker_alias (brapi_symbol, b3_ticker, codigo_cvm, motivo)
            VALUES (:a, :n, :c, :m)
            ON CONFLICT (brapi_symbol) DO UPDATE
                SET b3_ticker = EXCLUDED.b3_ticker, motivo = EXCLUDED.motivo,
                    codigo_cvm = COALESCE(EXCLUDED.codigo_cvm, market.ticker_alias.codigo_cvm)
        """), {**p, "c": codigo_cvm, "m": MOTIVO_TROCA})
        conn.execute(text(
            "DELETE FROM market.ticker_alias WHERE brapi_symbol = :n AND b3_ticker = :a"), p)
    return rel


def _setores(conn, cols, p: dict, aplicar: bool) -> dict | str | None:
    tem = cols.get("public.setores")
    if not tem or not set(_CLASSIFICACAO) <= tem:
        return "tabela ausente"
    antigo, novo = p["a"], p["n"]
    listagem = '"LISTAGEM"' if "LISTAGEM" in tem else "NULL"
    linhas = {}
    for tk, nome, setor, sub, seg, lst in conn.execute(text(f"""
            SELECT {_TK_SETORES}, nome_empresa, "SETOR", "SUBSETOR", "SEGMENTO", {listagem}
            FROM public.setores WHERE {_TK_SETORES} IN (:a, :n) ORDER BY ticker
            """), p).fetchall():
        linhas.setdefault(tk, {"nome": nome, "classif": (setor, sub, seg), "listagem": lst})
    la, ln = linhas.get(antigo), linhas.get(novo)
    if la is None and ln is None:
        return None
    if ln is None:
        origem = antigo
    elif la is None:
        origem = novo
    else:
        origem = escolher_classificacao(la["listagem"], ln["listagem"])
        origem = antigo if origem == "antigo" else novo
    classif = linhas[origem]["classif"]
    alinhar = [tk for tk, r in ((antigo, la), (novo, ln))
               if r is not None and r["classif"] != classif]
    rel = {"origem": origem, "classificacao": " / ".join(str(c or "") for c in classif),
           "alinhar": alinhar, "inserir": novo if ln is None else None}
    if aplicar:
        valores = {"s": classif[0], "ss": classif[1], "seg": classif[2]}
        for tk in alinhar:
            conn.execute(text(f"""
                UPDATE public.setores SET "SETOR" = :s, "SUBSETOR" = :ss, "SEGMENTO" = :seg
                WHERE {_TK_SETORES} = :tk
            """), {**valores, "tk": tk})
        if ln is None:
            # sem a linha, o código atual sairia do universo da tela quando o
            # antigo fosse desativado (load_setores exige setor)
            conn.execute(text(f"""
                INSERT INTO public.setores (ticker, nome_empresa, "SETOR", "SUBSETOR", "SEGMENTO")
                SELECT :n, :nome, :s, :ss, :seg
                WHERE NOT EXISTS (SELECT 1 FROM public.setores WHERE {_TK_SETORES} = :n)
            """), {**valores, "n": novo, "nome": la["nome"]})
    return rel


def unificar(conn, antigo: str, novo: str, *, aplicar: bool = False,
             codigo_cvm: int | None = None) -> dict:
    """O histórico de ``antigo`` passa a ser de ``novo``. Devolve o relatório.

    Ordem: o ativo novo existe e está ativo (as FKs dos fatos apontam para
    ele) → fatos mudam de dono (na mesma chave vale a linha do código novo;
    a do antigo é descartada) → o antigo fica inativo → alias antigo → novo
    → ``public.setores`` com uma classificação só. Não toca na fita
    (b3_security_history), nos payloads brutos nem em ticker_cvm: são
    registro do que existiu sob o código antigo.
    """
    antigo, novo = normalizar(antigo), normalizar(novo)
    if antigo == novo or not mesma_classe(antigo, novo):
        raise ValueError(f"{antigo} -> {novo} não é troca de código da mesma classe")
    p = {"a": antigo, "n": novo}
    cols = _colunas(conn)

    ativos = {normalizar(t): {"company_id": cid, "ativo": bool(at)}
              for t, cid, at in conn.execute(text(
                  "SELECT ticker, company_id, is_active FROM market.assets "
                  "WHERE ticker IN (:a, :n)"), p).fetchall()}
    ca = (ativos.get(antigo) or {}).get("company_id")
    cn = (ativos.get(novo) or {}).get("company_id")
    if ca is not None and cn is not None and ca != cn:
        raise ValueError(f"{antigo} (company_id {ca}) e {novo} (company_id {cn}) "
                         "são companhias diferentes")
    rel = {"antigo": antigo, "novo": novo, "aplicado": bool(aplicar),
           "ativos": {antigo: ativos.get(antigo), novo: ativos.get(novo)}}
    rel["base_ajuste"] = _base_ajuste(conn, cols, p)

    lote = None
    if aplicar:
        lote = _abrir_lote(conn, p)
        rel["backup"] = {"lote": lote}
        if novo not in ativos and antigo in ativos:
            conn.execute(text("""
                INSERT INTO market.assets (company_id, ticker, asset_type, exchange, currency, is_active)
                SELECT company_id, :n, asset_type, exchange, currency, TRUE
                FROM market.assets WHERE ticker = :a
                ON CONFLICT (ticker) DO NOTHING
            """), p)
        elif novo in ativos:
            conn.execute(text("""
                UPDATE market.assets
                SET is_active = TRUE,
                    company_id = COALESCE(company_id,
                        (SELECT company_id FROM market.assets WHERE ticker = :a))
                WHERE ticker = :n AND (is_active IS NOT TRUE OR company_id IS NULL)
            """), p)

    rel["fatos"] = _fatos(conn, cols, p, aplicar, lote, rel["base_ajuste"])

    if aplicar:
        conn.execute(text(
            "UPDATE market.assets SET is_active = FALSE "
            "WHERE ticker = :a AND is_active IS NOT FALSE"), p)
    rel["alias"] = _alias(conn, cols, p, codigo_cvm, aplicar)
    rel["setores"] = _setores(conn, cols, p, aplicar)
    if aplicar:
        rel["backup"]["linhas"] = conn.execute(text(
            f"SELECT COUNT(*) FROM {BACKUP} WHERE lote = :l AND acao <> 'abrir'"),
            {"l": lote}).scalar()
        logger.info("troca de código %s -> %s unificada (lote %s): %s",
                    antigo, novo, lote, rel["fatos"])
    return rel


def _existe_tabela(conn, tabela: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": tabela}).scalar())


def _abrir_lote(conn, p: dict) -> str:
    """Cria o lote e guarda o estado de ativos, alias e setores dos dois códigos.

    A linha ``abrir`` existe sempre: o par sem ativo nem setor (só o alias
    novo) também precisa de um lote para ser listado e revertido."""
    conn.exec_driver_sql(_DDL_BACKUP)
    lote = (f"{p['a']}-{p['n']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-"
            f"{uuid.uuid4().hex[:6]}")
    conn.execute(text(f"""
        INSERT INTO {BACKUP} (lote, antigo, novo, tabela, acao, linha)
        VALUES (:lote, :a, :n, '{BACKUP}', 'abrir', '{{}}'::jsonb)
    """), {**p, "lote": lote})
    for tabela, onde in _ESTADO:
        if _existe_tabela(conn, tabela):
            conn.execute(text(f"""
                INSERT INTO {BACKUP} (lote, antigo, novo, tabela, acao, linha)
                SELECT :lote, :a, :n, :t, 'antes', to_jsonb(o) FROM {tabela} o WHERE {onde}
            """), {**p, "lote": lote, "t": tabela})
    return lote


def _gravaveis(conn, tabela: str) -> tuple[str, str]:
    """Colunas que um INSERT aceita (sem as geradas) e o OVERRIDING que uma
    identidade ALWAYS exige para o valor guardado voltar igual."""
    esquema, nome = tabela.split(".")
    rows = conn.execute(text("""
        SELECT column_name, is_identity = 'YES' AND identity_generation = 'ALWAYS'
        FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t AND is_generated = 'NEVER'
        ORDER BY ordinal_position"""), {"s": esquema, "t": nome}).fetchall()
    lista = ", ".join(f'"{c}"' for c, _ in rows)
    return lista, ("OVERRIDING SYSTEM VALUE" if any(a for _, a in rows) else "")


def _do_lote(tabela: str, acoes: str) -> str:
    """As linhas guardadas de ``tabela`` no lote ``:l``, de volta ao tipo dela."""
    return (f"(SELECT (jsonb_populate_record(NULL::{tabela}, linha)).* FROM {BACKUP} "
            f"WHERE lote = :l AND tabela = '{tabela}' AND acao IN ({acoes}))")


def _restaurar(conn, tabela: str, acoes: str, lote: str) -> int:
    lista, overriding = _gravaveis(conn, tabela)
    return conn.execute(text(
        f"INSERT INTO {tabela} ({lista}) {overriding} "
        f"SELECT {lista} FROM {_do_lote(tabela, acoes)} b"), {"l": lote}).rowcount


def lotes(conn) -> list[dict]:
    """Os lotes guardados, do mais novo ao mais velho."""
    if not _existe_tabela(conn, BACKUP):
        return []
    rows = conn.execute(text(f"""
        SELECT lote, antigo, novo, MIN(criado_em), MAX(revertido_em),
               COUNT(*) FILTER (WHERE acao <> 'abrir'),
               COUNT(*) FILTER (WHERE acao = 'mover'),
               COUNT(*) FILTER (WHERE acao = 'descartar')
        FROM {BACKUP} GROUP BY lote, antigo, novo
        ORDER BY MIN(criado_em) DESC, lote""")).fetchall()
    return [{"lote": r[0], "antigo": r[1], "novo": r[2], "criado_em": r[3],
             "revertido_em": r[4], "linhas": r[5], "mover": r[6], "descartar": r[7]}
            for r in rows]


def reverter(conn, lote: str) -> dict:
    """Devolve o banco ao que ``lote`` guardou, na transação do chamador.

    Fatos: as linhas movidas saem do código atual (pela chave) e todas as
    guardadas voltam inteiras ao antigo — ``adjusted_close`` original
    incluído. Ativos voltam com o ``is_active`` e o ``company_id`` de antes; o
    ativo novo que ``unificar`` criou sai, a menos que tenha ganhado dado
    depois (o DELETE em cascata apagaria esse dado). Alias e setores dos dois
    códigos voltam a ser exatamente as linhas guardadas. Lote desconhecido ou
    já revertido é recusado; contagem que não fecha levanta, e a transação
    inteira volta.
    """
    if not _existe_tabela(conn, BACKUP):
        raise ValueError(f"sem {BACKUP}: nenhum lote a reverter")
    q = {"l": lote}
    conn.execute(text(f"SELECT 1 FROM {BACKUP} WHERE lote = :l FOR UPDATE"), q)
    cab = conn.execute(text(f"""
        SELECT antigo, novo, bool_or(revertido_em IS NOT NULL) FROM {BACKUP}
        WHERE lote = :l GROUP BY antigo, novo"""), q).fetchall()
    if len(cab) != 1:
        raise ValueError(f"lote {lote} desconhecido")
    antigo, novo, revertido = cab[0]
    if revertido:
        raise ValueError(f"lote {lote} já foi revertido")
    p = {**q, "a": antigo, "n": novo}
    guardado = {(t, a): int(c) for t, a, c in conn.execute(text(f"""
        SELECT tabela, acao, COUNT(*) FROM {BACKUP} WHERE lote = :l
        GROUP BY tabela, acao"""), q).fetchall()}
    rel = {"lote": lote, "antigo": antigo, "novo": novo, "fatos": {}}

    # 1. ativos: o antigo volta ao estado de antes (as FKs das linhas que
    #    voltam apontam para ele)
    conn.execute(text(f"""
        UPDATE market.assets t SET is_active = b.is_active, company_id = b.company_id
        FROM {_do_lote("market.assets", "'antes'")} b WHERE t.ticker = b.ticker"""), p)

    # 2. fatos
    for tabela, chave in FATOS:
        nome = f"market.{tabela}"
        mover = guardado.get((nome, "mover"), 0)
        descartar = guardado.get((nome, "descartar"), 0)
        if not mover and not descartar:
            continue
        tirar = 0
        if mover:
            casa = " AND ".join(f"d.{c} IS NOT DISTINCT FROM b.{c}" for c in chave)
            movidas = _do_lote(nome, "'mover'")
            tirar = conn.execute(text(
                f"DELETE FROM {nome} d USING {movidas} b WHERE d.ticker = :n AND {casa}"),
                p).rowcount
        voltar = _restaurar(conn, nome, "'mover', 'descartar'", lote)
        if voltar != mover + descartar:
            raise RuntimeError(f"{nome}: voltaram {voltar} de {mover + descartar} "
                               "linhas guardadas")
        rel["fatos"][tabela] = {"tiradas_do_atual": tirar, "restauradas": voltar}

    # 3. alias e setores: as linhas dos dois códigos voltam a ser as guardadas
    for tabela, onde in _ESTADO[1:]:
        if not _existe_tabela(conn, tabela):
            continue
        conn.execute(text(f"DELETE FROM {tabela} WHERE {onde}"), p)
        voltar = _restaurar(conn, tabela, "'antes'", lote)
        if voltar != guardado.get((tabela, "antes"), 0):
            raise RuntimeError(f"{tabela}: voltaram {voltar} de "
                               f"{guardado.get((tabela, 'antes'), 0)} linhas guardadas")
        rel[tabela] = voltar

    # 4. o ativo novo que unificar criou
    tinha_novo = conn.execute(text(f"""
        SELECT COUNT(*) FROM {BACKUP} WHERE lote = :l AND tabela = 'market.assets'
          AND acao = 'antes' AND linha->>'ticker' = :n"""), p).scalar()
    rel["ativo_novo"] = "restaurado"
    if not tinha_novo:
        posteriores = sum(
            conn.execute(text(f"SELECT COUNT(*) FROM market.{t} WHERE ticker = :n"),
                         p).scalar() or 0
            for t, _ in FATOS if _existe_tabela(conn, f"market.{t}"))
        if posteriores:
            rel["ativo_novo"] = f"mantido: {posteriores} linhas gravadas depois sob {novo}"
        else:
            conn.execute(text("DELETE FROM market.assets WHERE ticker = :n"), p)
            rel["ativo_novo"] = "apagado (não existia antes)"

    conn.execute(text(f"UPDATE {BACKUP} SET revertido_em = now() WHERE lote = :l"), q)
    logger.info("troca de código %s -> %s revertida (lote %s): %s",
                antigo, novo, lote, rel["fatos"])
    return rel
