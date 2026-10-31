"""
scripts/unificar_categorias_cartao.py
Leva para as categorias de sistema o que a fatura do cartão gravou com nomes
próprios.

Até 31/10/2026 o importador da fatura criava, na conta de cada usuário,
categorias com nomes que só ele usava (``Saúde & Bem-estar``, ``Lazer &
Entretenimento``, ``Transporte & Combustível``...), enquanto o extrato bancário
e o lançamento manual usavam as de sistema (``Saúde``, ``Lazer``,
``Combustível``...). O mesmo gasto caía em duas fatias conforme fosse pago no
débito ou no cartão. O importador já usa os nomes de sistema
(``core.card_categorization``); este script move o que ficou gravado.

O QUE ELE FAZ, por categoria antiga (só as criadas na conta de um usuário):
  1. Repõe cada referência no nome novo: lançamentos, movimentos de extrato
     (categoria sugerida e confirmada) e regras de classificação do extrato.
     ``Transporte & Combustível`` se divide pela descrição: posto vai para
     ``Combustível``, o resto para ``Transporte`` — a mesma regra do importador
     (``core.card_categorization.nome_unificado``).
  2. Orçamento: passa para a categoria nova. Se ela já tiver limite no mesmo
     mês, os dois limites SOMAM (o gasto das duas também passa a somar) e a
     linha antiga sai. O relatório lista cada soma.
  3. Regras aprendidas na tela do cartão: troca o nome antigo pelo novo.
  4. Apaga a categoria antiga, agora vazia — senão ela segue no seletor do
     lançamento manual e a divisão volta. Se alguma tabela que este script não
     conhece ainda apontar para ela, a categoria fica e o relatório avisa.

SEGURANÇA / REVERSIBILIDADE:
  - Dry-run por padrão: nada é gravado sem ``--aplicar``.
  - Tudo numa transação só. Antes de gravar, escreve o de-para completo em
    ``migration/backup/`` (fora do git), com a linha inteira de cada categoria
    e orçamento apagado; ``--reverter`` refaz o estado anterior a partir dele.
  - A reversão só devolve o que continua como a migração deixou: lançamento
    recategorizado à mão depois dela fica como está.

USO:
  python scripts/unificar_categorias_cartao.py                     # dry-run
  python scripts/unificar_categorias_cartao.py --aplicar           # aplica
  python scripts/unificar_categorias_cartao.py --reverter <backup.json>
  python scripts/unificar_categorias_cartao.py --usuario <uuid> ...  # uma conta só

Requer as credenciais do banco (.env / secrets) e MOCK_MODE desativado.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import text

# Permite rodar de qualquer diretório (adiciona a raiz do projeto ao path).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.card_categorization import (  # noqa: E402
    CATEGORY_TYPE,
    NOMES_LEGADOS,
    nome_unificado,
)

#: (tabela, coluna que aponta para a categoria, expressão do texto que decide
#: a divisão de ``Transporte & Combustível``). Expressões fixas, sem entrada de
#: usuário: entram no SQL por interpolação.
REFERENCIAS: tuple[tuple[str, str, str], ...] = (
    ("transactions", "category_id", "description"),
    ("bank_statement_movements", "categoria_id", "descricao_original"),
    ("bank_statement_movements", "categoria_confirmada_id", "descricao_original"),
    ("bank_statement_classification_rules", "category_id",
     "COALESCE(NULLIF(palavra_chave, ''), descricao_normalizada)"),
)

_SQL_LEGADAS = """
    SELECT c.id::text AS id, c.user_id::text AS user_id, c.name AS nome,
           c.type AS tipo, to_jsonb(c) AS linha
    FROM   categories c
    WHERE  c.name = ANY(:nomes)
    ORDER  BY c.user_id, c.name
"""

# Mesma busca de ``core.controle._get_or_create_category``: o nome, sem olhar
# o tipo, preferindo a categoria da pessoa à de sistema.
_SQL_ALVO = """
    SELECT id::text
    FROM   categories
    WHERE  LOWER(name) = LOWER(:nome)
      AND  (user_id = CAST(:uid AS uuid) OR user_id IS NULL)
    ORDER  BY CASE WHEN user_id = CAST(:uid AS uuid) THEN 0 ELSE 1 END
    LIMIT  1
"""

_DIR_BACKUP = os.path.join(os.path.dirname(__file__), "..", "migration", "backup")


# ── leitura ──────────────────────────────────────────────────────────────────

def _existe(conn, tabela: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"),
                             {"t": f"public.{tabela}"}).scalar())


def categorias_legadas(conn, usuario: str | None = None) -> tuple[list[dict], list[dict]]:
    """``(da_conta, de_sistema)``. Só as da conta são migradas: a de sistema é
    de todo mundo, e o importador nunca a criou — ela só é relatada."""
    linhas = [dict(r._mapping) for r in
              conn.execute(text(_SQL_LEGADAS), {"nomes": list(NOMES_LEGADOS)})]
    de_sistema = [r for r in linhas if not r["user_id"]]
    da_conta = [r for r in linhas if r["user_id"]
                and (usuario is None or r["user_id"] == usuario)]
    return da_conta, de_sistema


def resolvedor_somente_leitura(conn):
    """Acha a categoria-alvo sem criar. ``None``: será criada ao aplicar."""
    cache: dict[tuple[str, str], str | None] = {}

    def resolver(uid: str, nome: str) -> str | None:
        if (uid, nome) not in cache:
            cache[(uid, nome)] = conn.execute(
                text(_SQL_ALVO), {"nome": nome, "uid": uid}).scalar()
        return cache[(uid, nome)]

    return resolver


def resolvedor_que_cria(conn, criadas: list[dict]):
    """Acha ou cria a categoria-alvo — a mesma função do importador da fatura.

    Cada categoria criada vai para ``criadas``: a reversão a apaga se ela
    continuar vazia.
    """
    from core.controle import _get_or_create_category

    cache: dict[tuple[str, str], str | None] = {}

    def resolver(uid: str, nome: str) -> str | None:
        if (uid, nome) not in cache:
            existia = conn.execute(text(_SQL_ALVO), {"nome": nome, "uid": uid}).scalar()
            cid = _get_or_create_category(conn, uid, nome, CATEGORY_TYPE.get(nome, "expense"))
            if not cid:
                raise RuntimeError(f"Não foi possível obter/criar a categoria {nome!r}.")
            if not existia:
                criadas.append({"id": cid, "user_id": uid, "nome": nome})
            cache[(uid, nome)] = cid
        return cache[(uid, nome)]

    return resolver


# ── plano ────────────────────────────────────────────────────────────────────

def planejar(conn, legadas: list[dict], resolver) -> dict:
    """Tudo o que muda, sem gravar nada. ``para`` é ``None`` só no dry-run,
    quando a categoria-alvo ainda não existe."""
    plano: dict = {"categorias": [], "referencias": [], "orcamentos": [],
                   "regras_cartao": [], "mantidas": []}
    tabelas = {tabela for tabela, _, _ in REFERENCIAS} | {"budgets", "card_category_rules"}
    existentes = {t for t in tabelas if _existe(conn, t)}
    # (categoria-alvo, mês) -> {"id", "limite"}: o estado do orçamento-alvo
    # depois das operações já planejadas, para que duas somas no mesmo mês
    # partam do valor certo.
    alvo_orc: dict[tuple[str, str], dict | None] = {}

    for leg in legadas:
        uid, de, nome = leg["user_id"], leg["id"], leg["nome"]

        for tabela, coluna, texto in REFERENCIAS:
            if tabela not in existentes:
                continue
            for r in conn.execute(text(
                    f"SELECT id::text AS id, {texto} AS texto FROM {tabela} "
                    f"WHERE {coluna} = CAST(:cid AS uuid)"), {"cid": de}):
                novo = nome_unificado(nome, r.texto)
                plano["referencias"].append({
                    "tabela": tabela, "coluna": coluna, "id": r.id, "user_id": uid,
                    "de": de, "de_nome": nome, "para": resolver(uid, novo), "para_nome": novo,
                })

        if "budgets" in existentes:
            # Orçamento não tem descrição: o nome que se divide vai para o
            # destino padrão (``Transporte``).
            novo = nome_unificado(nome, "")
            orcamentos = conn.execute(text(
                "SELECT id::text AS id, month_year::text AS mes, amount_limit, "
                "to_jsonb(b) AS linha FROM budgets b "
                "WHERE category_id = CAST(:cid AS uuid) ORDER BY month_year"),
                {"cid": de}).fetchall()
            # Só resolve (e, ao aplicar, cria) o alvo se houver orçamento.
            para = resolver(uid, novo) if orcamentos else None
            for b in orcamentos:
                chave = (para or f"novo:{uid}:{novo}", b.mes)
                if chave not in alvo_orc:
                    alvo = None
                    if para:
                        alvo = conn.execute(text(
                            "SELECT id::text AS id, amount_limit FROM budgets "
                            "WHERE user_id = CAST(:uid AS uuid) AND category_id = CAST(:cid AS uuid) "
                            "AND month_year = CAST(:mes AS date)"),
                            {"uid": uid, "cid": para, "mes": b.mes}).fetchone()
                    alvo_orc[chave] = ({"id": alvo.id, "limite": alvo.amount_limit}
                                       if alvo else None)
                atual = alvo_orc[chave]
                base = {"id": b.id, "user_id": uid, "mes": b.mes, "de": de, "de_nome": nome,
                        "para": para, "para_nome": novo, "limite": b.amount_limit,
                        "linha": b.linha}
                if atual is None:
                    plano["orcamentos"].append({**base, "acao": "mover"})
                    alvo_orc[chave] = {"id": b.id, "limite": b.amount_limit}
                else:
                    soma = atual["limite"] + b.amount_limit
                    plano["orcamentos"].append({
                        **base, "acao": "somar", "alvo_id": atual["id"],
                        "alvo_limite_antigo": atual["limite"], "alvo_limite_novo": soma})
                    alvo_orc[chave] = {"id": atual["id"], "limite": soma}

        if "card_category_rules" in existentes:
            for r in conn.execute(text(
                    "SELECT id::text AS id, palavra_chave FROM card_category_rules "
                    "WHERE user_id = CAST(:uid AS uuid) AND category_name = :nome"),
                    {"uid": uid, "nome": nome}):
                plano["regras_cartao"].append({
                    "id": r.id, "user_id": uid, "palavra_chave": r.palavra_chave,
                    "de": nome, "para": nome_unificado(nome, r.palavra_chave)})

        filhas = conn.execute(text(
            "SELECT COUNT(*) FROM categories WHERE parent_id = CAST(:cid AS uuid)"),
            {"cid": de}).scalar()
        if filhas:
            plano["mantidas"].append({"id": de, "user_id": uid, "nome": nome,
                                      "motivo": f"{filhas} subcategoria(s) apontam para ela"})
        else:
            plano["categorias"].append({"id": de, "user_id": uid, "nome": nome,
                                        "linha": leg["linha"]})
    return plano


# ── relatório ────────────────────────────────────────────────────────────────

def relatar(plano: dict, de_sistema: list[dict]) -> None:
    if de_sistema:
        print("ATENÇÃO — categorias DE SISTEMA com nome antigo (não são tocadas):")
        for c in de_sistema:
            print(f"   {c['nome']} ({c['tipo']}) id={c['id']}")
        print()
    contas = sorted({c["user_id"] for c in plano["categorias"] + plano["mantidas"]})
    if not contas:
        print("Nenhuma categoria com nome antigo da fatura. Nada a migrar.")
        return
    for uid in contas:
        print(f"Conta {uid}")
        destinos: Counter = Counter()
        criadas = set()
        for ref in plano["referencias"]:
            if ref["user_id"] != uid:
                continue
            destinos[(ref["de_nome"], ref["para_nome"], ref["tabela"])] += 1
            if not ref["para"]:
                criadas.add(ref["para_nome"])
        for (de, para, tabela), n in sorted(destinos.items()):
            print(f"   {de:<34} -> {para:<14} {n:>5} em {tabela}")
        for o in plano["orcamentos"]:
            if o["user_id"] != uid:
                continue
            if not o["para"]:
                criadas.add(o["para_nome"])
            if o["acao"] == "somar":
                print(f"   orçamento {o['mes']}: {o['de_nome']} ({o['limite']}) SOMA em "
                      f"{o['para_nome']} ({o['alvo_limite_antigo']} -> {o['alvo_limite_novo']})")
            else:
                print(f"   orçamento {o['mes']}: {o['de_nome']} -> {o['para_nome']} ({o['limite']})")
        for r in plano["regras_cartao"]:
            if r["user_id"] == uid:
                print(f"   regra do cartão '{r['palavra_chave']}': {r['de']} -> {r['para']}")
        for c in plano["categorias"]:
            if c["user_id"] == uid:
                print(f"   categoria '{c['nome']}' será apagada (vazia depois da migração)")
        for c in plano["mantidas"]:
            if c["user_id"] == uid:
                print(f"   categoria '{c['nome']}' FICA: {c['motivo']}")
        if criadas:
            print(f"   categorias que serão criadas na conta: {', '.join(sorted(criadas))}")
        print()


# ── aplicar ──────────────────────────────────────────────────────────────────

def _caminho_backup() -> str:
    """Caminho NOVO para o de-para. Nunca reaproveita um existente: duas
    execuções no mesmo segundo sobrescreviam o backup da primeira, e com ele a
    única forma de reverter. O arquivo nasce vazio aqui (modo ``x``)."""
    os.makedirs(_DIR_BACKUP, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    caminho = os.path.abspath(os.path.join(_DIR_BACKUP, f"unificar_categorias_cartao_{carimbo}.json"))
    with open(caminho, "x", encoding="utf-8"):
        pass
    return caminho


def executar(conn, plano: dict) -> list[dict]:
    """Grava o plano. Devolve as categorias que não puderam ser apagadas."""
    por_coluna: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ref in plano["referencias"]:
        por_coluna[(ref["tabela"], ref["coluna"])].append(ref)
    for (tabela, coluna), refs in por_coluna.items():
        for ref in refs:
            conn.execute(text(
                f"UPDATE {tabela} SET {coluna} = CAST(:para AS uuid) "
                f"WHERE id = CAST(:id AS uuid) AND {coluna} = CAST(:de AS uuid)"),
                ref)

    for o in plano["orcamentos"]:
        if o["acao"] == "mover":
            conn.execute(text(
                "UPDATE budgets SET category_id = CAST(:para AS uuid) "
                "WHERE id = CAST(:id AS uuid)"), o)
        else:
            conn.execute(text(
                "UPDATE budgets SET amount_limit = :alvo_limite_novo "
                "WHERE id = CAST(:alvo_id AS uuid)"), o)
            conn.execute(text("DELETE FROM budgets WHERE id = CAST(:id AS uuid)"), o)

    for r in plano["regras_cartao"]:
        conn.execute(text(
            "UPDATE card_category_rules SET category_name = :para, updated_at = now() "
            "WHERE id = CAST(:id AS uuid)"), r)

    nao_apagadas = []
    for c in plano["categorias"]:
        # Savepoint: uma tabela desconhecida apontando para a categoria faz o
        # DELETE falhar; aí ela fica, e o resto da migração segue.
        try:
            with conn.begin_nested():
                conn.execute(text("DELETE FROM categories WHERE id = CAST(:id AS uuid)"), c)
        except Exception as exc:  # noqa: BLE001 - relatado, nunca engolido
            nao_apagadas.append({**c, "erro": type(exc).__name__})
    return nao_apagadas


def cmd_aplicar(engine, usuario: str | None) -> int:
    with engine.connect() as conn:
        legadas, de_sistema = categorias_legadas(conn, usuario)
    if not legadas:
        relatar({"categorias": [], "mantidas": [], "referencias": [], "orcamentos": [],
                 "regras_cartao": []}, de_sistema)
        return 0

    backup = _caminho_backup()
    try:
        with engine.begin() as conn:
            # Dentro da transação: as categorias-alvo que faltarem são criadas
            # aqui e somem junto se algo falhar.
            criadas: list[dict] = []
            plano = planejar(conn, legadas, resolvedor_que_cria(conn, criadas))
            plano["criadas"] = criadas
            with open(backup, "w", encoding="utf-8") as fh:
                json.dump({"gerado_em": datetime.now().isoformat(), "plano": plano},
                          fh, ensure_ascii=False, indent=1, default=str)
            nao_apagadas = executar(conn, plano)
            if nao_apagadas:
                plano["mantidas"].extend(
                    {**c, "motivo": f"DELETE recusado ({c['erro']}): ainda há referência"}
                    for c in nao_apagadas)
                plano["categorias"] = [c for c in plano["categorias"]
                                       if c["id"] not in {n["id"] for n in nao_apagadas}]
                with open(backup, "w", encoding="utf-8") as fh:
                    json.dump({"gerado_em": datetime.now().isoformat(), "plano": plano},
                              fh, ensure_ascii=False, indent=1, default=str)
    except Exception:
        # Nada foi gravado: um backup de algo que não aconteceu só confundiria
        # uma reversão futura.
        if os.path.exists(backup):
            os.remove(backup)
        raise

    relatar(plano, de_sistema)
    print(f"Aplicado. De-para gravado em: {backup}")
    print("   (guarde este arquivo — é o que permite reverter)")
    _limpar_caches()
    return 0


# ── reverter ─────────────────────────────────────────────────────────────────

def cmd_reverter(engine, caminho: str) -> int:
    if not os.path.isfile(caminho):
        print(f"Backup não encontrado: {caminho}")
        return 1
    with open(caminho, encoding="utf-8") as fh:
        plano = json.load(fh)["plano"]

    def _linha(valor) -> str:
        return valor if isinstance(valor, str) else json.dumps(valor, default=str)

    voltaram = Counter()
    ficaram = Counter()
    with engine.begin() as conn:
        # Primeiro as categorias: as referências precisam delas para voltar.
        for c in plano["categorias"]:
            conn.execute(text(
                "INSERT INTO categories SELECT * FROM "
                "jsonb_populate_record(NULL::categories, CAST(:linha AS jsonb)) "
                "ON CONFLICT (id) DO NOTHING"), {"linha": _linha(c["linha"])})
        for ref in plano["referencias"]:
            n = conn.execute(text(
                f"UPDATE {ref['tabela']} SET {ref['coluna']} = CAST(:de AS uuid) "
                f"WHERE id = CAST(:id AS uuid) AND {ref['coluna']} = CAST(:para AS uuid)"),
                ref).rowcount
            (voltaram if n else ficaram)[ref["tabela"]] += 1
        for o in reversed(plano["orcamentos"]):
            if o["acao"] == "mover":
                n = conn.execute(text(
                    "UPDATE budgets SET category_id = CAST(:de AS uuid) "
                    "WHERE id = CAST(:id AS uuid) AND category_id = CAST(:para AS uuid)"),
                    o).rowcount
            else:
                n = conn.execute(text(
                    "UPDATE budgets SET amount_limit = :alvo_limite_antigo "
                    "WHERE id = CAST(:alvo_id AS uuid) AND amount_limit = :alvo_limite_novo"),
                    o).rowcount
                if n:
                    conn.execute(text(
                        "INSERT INTO budgets SELECT * FROM "
                        "jsonb_populate_record(NULL::budgets, CAST(:linha AS jsonb)) "
                        "ON CONFLICT DO NOTHING"), {"linha": _linha(o["linha"])})
            (voltaram if n else ficaram)["budgets"] += 1
        for r in plano["regras_cartao"]:
            n = conn.execute(text(
                "UPDATE card_category_rules SET category_name = :de, updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND category_name = :para"), r).rowcount
            (voltaram if n else ficaram)["card_category_rules"] += 1

        # Por último, as que a migração criou: saem só se continuarem vazias.
        for c in plano.get("criadas", []):
            try:
                with conn.begin_nested():
                    conn.execute(text("DELETE FROM categories WHERE id = CAST(:id AS uuid)"), c)
                voltaram["categorias criadas pela migração (apagadas)"] += 1
            except Exception:  # noqa: BLE001 - já em uso: fica
                ficaram["categorias criadas pela migração (em uso, ficam)"] += 1

    print(f"Revertido. {len(plano['categorias'])} categoria(s) recriada(s).")
    if voltaram:
        print("Voltaram ao estado anterior:")
        for tabela, n in sorted(voltaram.items()):
            print(f"   {tabela}: {n}")
    if ficaram:
        print("Ficaram como estão (mudaram depois da migração, ou já estão em uso):")
        for tabela, n in sorted(ficaram.items()):
            print(f"   {tabela}: {n}")
    _limpar_caches()
    return 0


def _limpar_caches() -> None:
    try:
        from core.controle import _clear_controle_caches
        _clear_controle_caches()
    except Exception:  # noqa: BLE001 - cache é conveniência, não requisito
        pass


def main(argv: list[str] | None = None) -> int:
    from core.config import settings
    from core.database import get_engine

    ap = argparse.ArgumentParser(
        description="Unifica as categorias que a fatura do cartão criou com nomes próprios.")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--aplicar", action="store_true",
                     help="Aplica a migração (grava o de-para antes).")
    grp.add_argument("--reverter", metavar="BACKUP_JSON",
                     help="Desfaz a partir do de-para gravado pelo --aplicar.")
    ap.add_argument("--usuario", metavar="UUID",
                    help="Migra só esta conta (padrão: todas com nome antigo).")
    args = ap.parse_args(argv)

    if settings.MOCK_MODE:
        print("MOCK_MODE ativo — abortado (não há banco real para alterar).")
        return 1
    engine = get_engine()
    if engine is None:
        print("Banco não configurado — abortado.")
        return 1

    if args.reverter:
        return cmd_reverter(engine, args.reverter)
    if args.aplicar:
        return cmd_aplicar(engine, args.usuario)

    with engine.connect() as conn:
        legadas, de_sistema = categorias_legadas(conn, args.usuario)
        plano = planejar(conn, legadas, resolvedor_somente_leitura(conn))
    relatar(plano, de_sistema)
    if legadas:
        print("Nada foi alterado (dry-run). Use --aplicar para aplicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
