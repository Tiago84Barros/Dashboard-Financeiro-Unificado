"""Banco do Controle CNPJ: tabelas ``pj_*``, sempre filtradas pelo dono.

Cinco tabelas (migrations 084 e 085, também criadas aqui na primeira leitura):

- ``pj_empresas`` -- uma linha por dono: CNPJ, razão social, anexo do Simples
  e o saldo de abertura da conta (deduzido do primeiro extrato com saldo);
- ``pj_lancamentos`` -- tudo que entra e sai: pago ou previsto, do extrato ou
  manual. ``chave`` é a identidade do movimento no extrato (FITID no OFX):
  reimportar o mesmo arquivo não duplica nada;
- ``pj_saldos_extrato`` -- o saldo que o banco informou em cada extrato, para
  a conferência contra o saldo calculado;
- ``pj_regras`` -- contraparte -> categoria, aprendida quando o usuário
  classifica um movimento na importação;
- ``pj_categorias`` -- categorias criadas pelo dono (migration 085), cada uma
  presa a um grupo da DRE. O código (``u_...``) não muda ao renomear.

Conciliação: o movimento do extrato que casa com um lançamento manual (pago
ou previsto) não vira linha nova -- o lançamento existente recebe a ``chave``
do extrato, a data do banco e ``status = 'pago'``. A mesma regra de par do
financeiro pessoal (``core.conciliacao_bancaria.sugerir_vinculos``).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, timedelta
from typing import Any

from core.conciliacao_bancaria import JANELA_DIAS, sugerir_vinculos
from core.config import settings
from core.pj import relatorios
from core.pj.plano_contas import (
    GRUPOS_DRE,
    POR_CODIGO,
    SEM_CATEGORIA,
    Categoria,
    PlanoDeContas,
    codigo_para_propria,
    nome_reservado,
    normalizar,
)
from core.previstos import datas_mensais
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

STATUS = ("pago", "previsto")
ANEXO_PADRAO = "III"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS pj_empresas (
        user_id            UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
        cnpj               TEXT,
        razao_social       TEXT,
        anexo_simples      TEXT NOT NULL DEFAULT 'III',
        saldo_inicial      NUMERIC(15,2),
        data_saldo_inicial DATE,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pj_lancamentos (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        data           DATE NOT NULL,
        descricao      TEXT NOT NULL,
        valor          NUMERIC(15,2) NOT NULL,
        categoria      TEXT,
        status         TEXT NOT NULL DEFAULT 'pago' CHECK (status IN ('pago', 'previsto')),
        origem         TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'extrato')),
        contraparte    TEXT,
        chave          TEXT,
        grupo_serie    UUID,
        parcela        INT NOT NULL DEFAULT 1,
        total_parcelas INT NOT NULL DEFAULT 1,
        arquivo        TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, chave)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pj_lancamentos_user_data
    ON pj_lancamentos (user_id, data)
    """,
    """
    CREATE TABLE IF NOT EXISTS pj_saldos_extrato (
        user_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        data       DATE NOT NULL,
        saldo      NUMERIC(15,2) NOT NULL,
        arquivo    TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, data)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pj_regras (
        user_id     UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        contraparte TEXT NOT NULL,
        categoria   TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, contraparte)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pj_categorias (
        user_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        codigo     TEXT NOT NULL,
        nome       TEXT NOT NULL,
        nome_chave TEXT NOT NULL,
        grupo      TEXT NOT NULL CHECK (grupo IN ('receita_bruta', 'deducoes', 'custos',
                       'despesas_operacionais', 'pessoal', 'financeiro', 'fora_do_resultado')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, codigo),
        UNIQUE (user_id, nome_chave)
    )
    """,
    *[
        f"""
        DO $$ BEGIN
            IF NOT (SELECT relrowsecurity FROM pg_class
                    WHERE relname = '{t}' AND relnamespace = 'public'::regnamespace) THEN
                ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_policies
                           WHERE schemaname = 'public' AND tablename = '{t}'
                             AND policyname = '{t}_owner_all') THEN
                CREATE POLICY {t}_owner_all ON {t}
                    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
            END IF;
        END; $$
        """
        for t in ("pj_empresas", "pj_lancamentos", "pj_saldos_extrato", "pj_regras",
                  "pj_categorias")
    ],
]

_lock = threading.Lock()
_criadas = False


def _texto(sql: str):
    from sqlalchemy import text
    return text(sql)


def garantir_tabelas(conn) -> None:
    global _criadas
    if _criadas:
        return
    with _lock:
        if _criadas:
            return
        for ddl in DDL:
            conn.execute(_texto(ddl))
        _criadas = True


def _engine_e_dono():
    """(engine, dono) ou (None, None). Sem dono não se lê nem grava nada."""
    if settings.MOCK_MODE:
        return None, None
    from core.database import get_engine
    engine = get_engine()
    dono = settings.OWNER_USER_ID
    if engine is None or not dono:
        return None, None
    return engine, str(dono)


def limpar_caches() -> None:
    for fn in (obter_empresa, listar_lancamentos, listar_saldos_extrato, listar_categorias):
        if hasattr(fn, "clear"):
            fn.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Leitura
# ─────────────────────────────────────────────────────────────────────────────

@user_cache_data(ttl=60)
def obter_empresa() -> dict | None:
    engine, dono = _engine_e_dono()
    if engine is None:
        return None
    with engine.begin() as conn:
        garantir_tabelas(conn)
        r = conn.execute(_texto("""
            SELECT cnpj, razao_social, anexo_simples, saldo_inicial, data_saldo_inicial
            FROM pj_empresas WHERE user_id = CAST(:uid AS uuid)"""), {"uid": dono}).fetchone()
    if not r:
        return None
    return {"cnpj": r.cnpj, "razao_social": r.razao_social,
            "anexo_simples": r.anexo_simples or ANEXO_PADRAO,
            "saldo_inicial": float(r.saldo_inicial) if r.saldo_inicial is not None else None,
            "data_saldo_inicial": r.data_saldo_inicial}


@user_cache_data(ttl=60)
def listar_lancamentos() -> list[dict]:
    """Todos os lançamentos do dono. Empresa pequena tem poucos milhares por
    ano; ler tudo uma vez e fatiar em Python mantém DRE, série e saldo na
    mesma fonte."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return []
    with engine.begin() as conn:
        garantir_tabelas(conn)
        # grupo_categoria: o grupo da DRE de uma categoria criada pelo usuário,
        # que core.pj.relatorios não acha em POR_CODIGO.
        rows = conn.execute(_texto("""
            SELECT l.id::text, l.data, l.descricao, l.valor, l.categoria, l.status, l.origem,
                   l.contraparte, l.chave, l.parcela, l.total_parcelas,
                   c.grupo AS grupo_categoria
            FROM pj_lancamentos l
            LEFT JOIN pj_categorias c ON c.user_id = l.user_id AND c.codigo = l.categoria
            WHERE l.user_id = CAST(:uid AS uuid)
            ORDER BY l.data, l.created_at"""), {"uid": dono}).fetchall()
    return [{"id": r.id, "data": r.data, "descricao": r.descricao, "valor": float(r.valor),
             "categoria": r.categoria, "status": r.status, "origem": r.origem,
             "contraparte": r.contraparte, "chave": r.chave,
             "parcela": int(r.parcela or 1), "total_parcelas": int(r.total_parcelas or 1),
             "grupo_categoria": r.grupo_categoria}
            for r in rows]


@user_cache_data(ttl=60)
def listar_categorias() -> list[dict]:
    """Categorias criadas pelo dono, com quantos lançamentos usam cada uma."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return []
    with engine.begin() as conn:
        garantir_tabelas(conn)
        rows = conn.execute(_texto("""
            SELECT c.codigo, c.nome, c.grupo,
                   (SELECT count(*) FROM pj_lancamentos l
                    WHERE l.user_id = c.user_id AND l.categoria = c.codigo) AS usos
            FROM pj_categorias c WHERE c.user_id = CAST(:uid AS uuid)
            ORDER BY c.nome"""), {"uid": dono}).fetchall()
    return [{"codigo": r.codigo, "nome": r.nome, "grupo": r.grupo, "usos": int(r.usos)}
            for r in rows]


def plano_de_contas() -> PlanoDeContas:
    """Fixas + criadas pelo dono. Sem banco, só as fixas."""
    return PlanoDeContas(tuple(Categoria(c["codigo"], c["nome"], c["grupo"])
                               for c in listar_categorias()))


def _codigos_validos(conn, dono: str) -> set[str]:
    """Fixas + próprias do dono, com as próprias travadas em FOR SHARE até o
    fim da transação de quem grava o lançamento.

    Sem a trava, excluir uma categoria e gravar um lançamento nela em duas
    sessões ao mesmo tempo passava nas duas checagens: o lançamento ficava
    com uma categoria que não existe mais e sumia da DRE (achado do Codex no
    PR #687). ``excluir_categoria`` trava a mesma linha em FOR UPDATE antes de
    contar os usos, então uma das duas espera a outra terminar. Não há chave
    estrangeira possível: ``pj_lancamentos.categoria`` guarda também as fixas.
    """
    proprias = {r.codigo for r in conn.execute(_texto("""
        SELECT codigo FROM pj_categorias WHERE user_id = CAST(:uid AS uuid)
        FOR SHARE"""), {"uid": dono})}
    return set(POR_CODIGO) | proprias


@user_cache_data(ttl=60)
def listar_saldos_extrato() -> list[dict]:
    engine, dono = _engine_e_dono()
    if engine is None:
        return []
    with engine.begin() as conn:
        garantir_tabelas(conn)
        rows = conn.execute(_texto("""
            SELECT data, saldo, arquivo FROM pj_saldos_extrato
            WHERE user_id = CAST(:uid AS uuid) ORDER BY data DESC"""), {"uid": dono}).fetchall()
    return [{"data": r.data, "saldo": float(r.saldo), "arquivo": r.arquivo} for r in rows]


def saldo_calculado(empresa: dict | None, lancs: list[dict], data: date) -> float | None:
    """Saldo da conta na data, ou ``None`` enquanto não há saldo de abertura."""
    if not empresa or empresa.get("saldo_inicial") is None:
        return None
    return relatorios.saldo_ate(empresa["saldo_inicial"], empresa.get("data_saldo_inicial"),
                                lancs, data)


# ─────────────────────────────────────────────────────────────────────────────
# Importação do extrato
# ─────────────────────────────────────────────────────────────────────────────

def _como_lancamento_para_par(x: dict) -> dict:
    return {"id": x["id"], "due_date": x["data"], "amount": x["valor"],
            "description": x["descricao"], "category_id": x["categoria"],
            "status": "pending" if x["status"] == "previsto" else "settled"}


def preparar_importacao(movimentos: list[dict]) -> dict[str, Any]:
    """O que a prévia precisa: chaves já gravadas, regras aprendidas e o par
    de cada movimento com lançamento manual (pago ou previsto) ainda sem
    extrato. Sem banco devolve ``ok=False`` e a prévia segue sem conciliação."""
    vazio = {"ok": False, "ja_importadas": set(), "regras": {}, "vinculos": {}}
    engine, dono = _engine_e_dono()
    if engine is None or not movimentos:
        return vazio
    datas = [m["data"] for m in movimentos]
    with engine.begin() as conn:
        garantir_tabelas(conn)
        chaves = [m["chave"] for m in movimentos]
        ja = {r.chave for r in conn.execute(_texto("""
            SELECT chave FROM pj_lancamentos
            WHERE user_id = CAST(:uid AS uuid) AND chave = ANY(:chaves)"""),
            {"uid": dono, "chaves": chaves})}
        regras = {r.contraparte: r.categoria for r in conn.execute(_texto("""
            SELECT contraparte, categoria FROM pj_regras
            WHERE user_id = CAST(:uid AS uuid)"""), {"uid": dono})}
        candidatos = [dict(r._mapping) for r in conn.execute(_texto("""
            SELECT id::text, data, valor::float AS valor, descricao, categoria, status
            FROM pj_lancamentos
            WHERE user_id = CAST(:uid AS uuid) AND origem = 'manual' AND chave IS NULL
              AND data BETWEEN :ini AND :fim"""),
            {"uid": dono, "ini": min(datas) - timedelta(days=JANELA_DIAS),
             "fim": max(datas) + timedelta(days=JANELA_DIAS)})]
    novos = [i for i, m in enumerate(movimentos) if m["chave"] not in ja]
    pares = sugerir_vinculos(
        [{"data_movimento": movimentos[i]["data"], "valor": movimentos[i]["valor"],
          "descricao_original": movimentos[i]["descricao"]} for i in novos],
        [_como_lancamento_para_par(c) for c in candidatos])
    return {"ok": True, "ja_importadas": ja, "regras": regras,
            "vinculos": {novos[k]: v for k, v in pares.items()}}


def _aprender_regra(conn, dono: str, contraparte: object, categoria: str | None) -> None:
    """Contraparte -> categoria. Um lugar só para os três caminhos que
    classificam: movimento novo, movimento conciliado e correção manual.

    Devolução não sobrescreve regra existente: o cliente com regra de receita
    já recebe "devolução" nas saídas (``categoria_no_sentido``), e trocar a
    regra por devolução faria o próximo pagamento dele perder a receita certa
    (de vendas, por exemplo) e cair na sugestão genérica.
    """
    cp = normalizar(contraparte)
    if not categoria or not cp:
        return
    conflito = ("DO NOTHING" if categoria == "devolucoes" else
                "DO UPDATE SET categoria = EXCLUDED.categoria, updated_at = NOW()")
    conn.execute(_texto(f"""
        INSERT INTO pj_regras (user_id, contraparte, categoria)
        VALUES (CAST(:uid AS uuid), :cp, :cat)
        ON CONFLICT (user_id, contraparte) {conflito}"""),
        {"uid": dono, "cp": cp, "cat": categoria})


def importar_extrato(movimentos: list[dict], arquivo: str,
                     saldo_final: dict | None = None) -> dict:
    """Grava os movimentos (com categoria e vínculo já decididos na prévia).

    Cada movimento: ``data``, ``valor``, ``descricao``, ``contraparte``,
    ``chave``, ``categoria`` (código ou None) e, opcional, ``vincular_id``.
    """
    engine, dono = _engine_e_dono()
    if engine is None:
        return {"ok": False, "message": "Banco não configurado ou sessão sem dono."}
    novos = vinculados = repetidos = 0
    with engine.begin() as conn:
        garantir_tabelas(conn)
        validos = _codigos_validos(conn, dono)
        for m in movimentos:
            categoria = m.get("categoria") if m.get("categoria") in validos else None
            if m.get("vincular_id"):
                r = conn.execute(_texto("""
                    UPDATE pj_lancamentos
                    SET status = 'pago', data = :data, chave = :chave,
                        categoria = COALESCE(categoria, :cat),
                        contraparte = COALESCE(contraparte, :contraparte),
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid) AND user_id = CAST(:uid AS uuid)
                      AND chave IS NULL
                      AND NOT EXISTS (SELECT 1 FROM pj_lancamentos o
                                      WHERE o.user_id = CAST(:uid AS uuid) AND o.chave = :chave)
                    RETURNING categoria"""),
                    {"data": m["data"], "chave": m["chave"], "cat": categoria,
                     "contraparte": m.get("contraparte"),
                     "id": m["vincular_id"], "uid": dono}).fetchone()
                if r is not None:
                    vinculados += 1
                    # A contraparte do banco passa a morar no lançamento e
                    # aprende a categoria que ficou nele (achado do Codex, #680).
                    _aprender_regra(conn, dono, m.get("contraparte"), r.categoria)
                    continue
            r = conn.execute(_texto("""
                INSERT INTO pj_lancamentos
                    (user_id, data, descricao, valor, categoria, status, origem,
                     contraparte, chave, arquivo)
                VALUES (CAST(:uid AS uuid), :data, :descricao, :valor, :cat, 'pago',
                        'extrato', :contraparte, :chave, :arquivo)
                ON CONFLICT (user_id, chave) DO NOTHING"""),
                {"uid": dono, "data": m["data"], "descricao": m["descricao"][:300],
                 "valor": round(float(m["valor"]), 2), "cat": categoria,
                 "contraparte": m.get("contraparte"), "chave": m["chave"], "arquivo": arquivo})
            if r.rowcount:
                novos += 1
            else:
                repetidos += 1
            _aprender_regra(conn, dono, m.get("contraparte"), categoria)
        if saldo_final and saldo_final.get("data") and saldo_final.get("valor") is not None:
            conn.execute(_texto("""
                INSERT INTO pj_saldos_extrato (user_id, data, saldo, arquivo)
                VALUES (CAST(:uid AS uuid), :data, :saldo, :arquivo)
                ON CONFLICT (user_id, data) DO UPDATE
                    SET saldo = EXCLUDED.saldo, arquivo = EXCLUDED.arquivo"""),
                {"uid": dono, "data": saldo_final["data"], "saldo": saldo_final["valor"],
                 "arquivo": arquivo})
            # Saldo de abertura a partir de TODOS os movimentos do banco já
            # gravados (chave preenchida = veio do extrato ou foi conciliado),
            # não só dos novos deste arquivo: um extrato anterior sem saldo
            # que se sobrepõe a este já tem linhas no banco, e o saldo
            # calculado depois as soma (achado do Codex no PR #680).
            gravados = [{"data": r.data, "valor": float(r.valor)} for r in conn.execute(_texto("""
                SELECT data, valor FROM pj_lancamentos
                WHERE user_id = CAST(:uid AS uuid) AND chave IS NOT NULL
                  AND status = 'pago'"""), {"uid": dono})]
            abertura = relatorios.saldo_inicial_pelo_extrato(
                saldo_final["valor"], gravados, saldo_final["data"])
            if abertura:
                # Só na primeira vez: o saldo de abertura não muda a cada extrato.
                conn.execute(_texto("""
                    INSERT INTO pj_empresas (user_id, saldo_inicial, data_saldo_inicial)
                    VALUES (CAST(:uid AS uuid), :saldo, :data)
                    ON CONFLICT (user_id) DO UPDATE
                        SET saldo_inicial = EXCLUDED.saldo_inicial,
                            data_saldo_inicial = EXCLUDED.data_saldo_inicial,
                            updated_at = NOW()
                        WHERE pj_empresas.saldo_inicial IS NULL"""),
                    {"uid": dono, "saldo": abertura[0], "data": abertura[1]})
    limpar_caches()
    return {"ok": True, "novos": novos, "vinculados": vinculados, "repetidos": repetidos,
            "message": (f"{novos} movimento(s) novo(s); {vinculados} conciliado(s) com "
                        f"lançamento existente; {repetidos} já importado(s) antes.")}


# ─────────────────────────────────────────────────────────────────────────────
# Lançamentos manuais, previstos e edição
# ─────────────────────────────────────────────────────────────────────────────

def criar_lancamento(data: date, descricao: str, valor: float, entrada: bool,
                     categoria: str | None, status: str = "pago",
                     repeticoes: int = 1) -> tuple[bool, str]:
    """Lançamento manual. ``repeticoes`` > 1 só para previsto (série mensal)."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    if status not in STATUS:
        return False, "Situação inválida."
    if float(valor or 0) <= 0:
        return False, "Informe um valor maior que zero."
    if repeticoes > 1 and status != "previsto":
        return False, "Repetir vale só para previsto."
    sinal = 1.0 if entrada else -1.0
    datas = datas_mensais(data, repeticoes)
    grupo = str(uuid.uuid4()) if len(datas) > 1 else None
    with engine.begin() as conn:
        garantir_tabelas(conn)
        if categoria is not None and categoria not in _codigos_validos(conn, dono):
            return False, "Categoria inválida."
        for i, d in enumerate(datas, start=1):
            conn.execute(_texto("""
                INSERT INTO pj_lancamentos
                    (user_id, data, descricao, valor, categoria, status, origem,
                     grupo_serie, parcela, total_parcelas)
                VALUES (CAST(:uid AS uuid), :data, :descricao, :valor, :cat, :status,
                        'manual', CAST(:grupo AS uuid), :parcela, :total)"""),
                {"uid": dono, "data": d, "descricao": descricao.strip()[:300],
                 "valor": round(sinal * abs(float(valor)), 2), "cat": categoria,
                 "status": status, "grupo": grupo, "parcela": i, "total": len(datas)})
    limpar_caches()
    return True, ""


def classificar(lancamento_id: str, categoria: str | None,
                lembrar: bool = True) -> tuple[bool, str]:
    """Troca a categoria; com ``lembrar``, a contraparte vira regra."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    with engine.begin() as conn:
        garantir_tabelas(conn)
        if categoria is not None and categoria not in _codigos_validos(conn, dono):
            return False, "Categoria inválida."
        r = conn.execute(_texto("""
            UPDATE pj_lancamentos SET categoria = :cat, updated_at = NOW()
            WHERE id = CAST(:id AS uuid) AND user_id = CAST(:uid AS uuid)
            RETURNING contraparte"""), {"cat": categoria, "id": lancamento_id, "uid": dono}).fetchone()
        if r is None:
            return False, "Lançamento não encontrado."
        if lembrar:
            _aprender_regra(conn, dono, r.contraparte, categoria)
    limpar_caches()
    return True, ""


def dar_baixa(lancamento_id: str, data_pagamento: date,
              valor_pago: float | None = None) -> tuple[bool, str]:
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    with engine.begin() as conn:
        garantir_tabelas(conn)
        r = conn.execute(_texto("""
            UPDATE pj_lancamentos
            SET status = 'pago', data = :data,
                valor = CASE WHEN CAST(:valor AS numeric) IS NULL THEN valor
                             ELSE SIGN(valor) * ABS(CAST(:valor AS numeric)) END,
                updated_at = NOW()
            WHERE id = CAST(:id AS uuid) AND user_id = CAST(:uid AS uuid)
              AND status = 'previsto'"""),
            {"data": data_pagamento, "valor": valor_pago, "id": lancamento_id, "uid": dono})
        if r.rowcount == 0:
            return False, "Previsto não encontrado ou já baixado."
    limpar_caches()
    return True, ""


def excluir(lancamento_id: str) -> tuple[bool, str]:
    """Apaga lançamento manual. O que veio do extrato não sai por aqui: o
    banco registrou o movimento, e apagá-lo deixaria o saldo errado."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    with engine.begin() as conn:
        garantir_tabelas(conn)
        r = conn.execute(_texto("""
            DELETE FROM pj_lancamentos
            WHERE id = CAST(:id AS uuid) AND user_id = CAST(:uid AS uuid)
              AND origem = 'manual' AND chave IS NULL"""), {"id": lancamento_id, "uid": dono})
        if r.rowcount == 0:
            return False, "Só lançamento manual, ainda não conciliado com o extrato, pode ser excluído."
    limpar_caches()
    return True, ""


def salvar_empresa(cnpj: str, razao_social: str, anexo: str) -> tuple[bool, str]:
    from core.pj.simples import TABELAS
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    if anexo not in TABELAS:
        return False, "Anexo inválido."
    digitos = "".join(c for c in str(cnpj or "") if c.isdigit())
    if digitos and len(digitos) != 14:
        return False, "CNPJ precisa de 14 dígitos."
    with engine.begin() as conn:
        garantir_tabelas(conn)
        conn.execute(_texto("""
            INSERT INTO pj_empresas (user_id, cnpj, razao_social, anexo_simples)
            VALUES (CAST(:uid AS uuid), :cnpj, :razao, :anexo)
            ON CONFLICT (user_id) DO UPDATE
                SET cnpj = EXCLUDED.cnpj, razao_social = EXCLUDED.razao_social,
                    anexo_simples = EXCLUDED.anexo_simples, updated_at = NOW()"""),
            {"uid": dono, "cnpj": digitos or None, "razao": (razao_social or "").strip() or None,
             "anexo": anexo})
    limpar_caches()
    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# Categorias criadas pelo usuário
# ─────────────────────────────────────────────────────────────────────────────

_GRUPOS = dict(GRUPOS_DRE)


def _nome_em_uso(conn, dono: str, nome: str, exceto: str | None = None) -> bool:
    """Checagem amigável, antes de gravar. Quem garante é o banco: o índice
    único (user_id, nome_chave) recusa o nome repetido que duas sessões
    gravem ao mesmo tempo, quando as duas passam por aqui antes de qualquer
    uma gravar (achado do Codex no PR #687). As fixas não estão no banco, por
    isso continuam checadas aqui -- e não mudam, então não há corrida com elas."""
    alvo = normalizar(nome)
    if any(normalizar(c.nome) == alvo for c in POR_CODIGO.values()):
        return True
    return any(normalizar(r.nome) == alvo for r in conn.execute(_texto("""
        SELECT codigo, nome FROM pj_categorias
        WHERE user_id = CAST(:uid AS uuid) AND codigo <> COALESCE(:exceto, '')"""),
        {"uid": dono, "exceto": exceto}))


def criar_categoria(nome: str, grupo: str) -> tuple[bool, str]:
    """Nova categoria do dono, presa a um grupo da DRE. O grupo decide onde
    ela soma: Receita bruta entra no DAS, Fora do resultado não entra na DRE."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    nome = " ".join(str(nome or "").split())[:60]
    if not nome:
        return False, "Informe o nome da categoria."
    if nome_reservado(nome):
        return False, f"\"{SEM_CATEGORIA}\" é reservado para o que ainda não tem categoria."
    if grupo not in _GRUPOS:
        return False, "Escolha a linha da DRE."
    from sqlalchemy.exc import IntegrityError
    try:
        with engine.begin() as conn:
            garantir_tabelas(conn)
            if _nome_em_uso(conn, dono, nome):
                return False, f"Já existe uma categoria chamada \"{nome}\"."
            existentes = {r.codigo for r in conn.execute(_texto("""
                SELECT codigo FROM pj_categorias WHERE user_id = CAST(:uid AS uuid)"""),
                {"uid": dono})}
            conn.execute(_texto("""
                INSERT INTO pj_categorias (user_id, codigo, nome, nome_chave, grupo)
                VALUES (CAST(:uid AS uuid), :codigo, :nome, :chave, :grupo)"""),
                {"uid": dono, "codigo": codigo_para_propria(nome, existentes),
                 "nome": nome, "chave": normalizar(nome), "grupo": grupo})
    except IntegrityError:
        # Outra sessão gravou o mesmo nome (ou código) entre a checagem e o INSERT.
        return False, f"Já existe uma categoria chamada \"{nome}\"."
    limpar_caches()
    return True, ""


def renomear_categoria(codigo: str, novo_nome: str) -> tuple[bool, str]:
    """Só o nome muda: o código fica, e com ele os lançamentos e as regras."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    novo_nome = " ".join(str(novo_nome or "").split())[:60]
    if not novo_nome:
        return False, "Informe o novo nome."
    if nome_reservado(novo_nome):
        return False, f"\"{SEM_CATEGORIA}\" é reservado para o que ainda não tem categoria."
    from sqlalchemy.exc import IntegrityError
    try:
        with engine.begin() as conn:
            garantir_tabelas(conn)
            if _nome_em_uso(conn, dono, novo_nome, exceto=codigo):
                return False, f"Já existe uma categoria chamada \"{novo_nome}\"."
            r = conn.execute(_texto("""
                UPDATE pj_categorias SET nome = :nome, nome_chave = :chave, updated_at = NOW()
                WHERE user_id = CAST(:uid AS uuid) AND codigo = :codigo"""),
                {"uid": dono, "codigo": codigo, "nome": novo_nome, "chave": normalizar(novo_nome)})
            if not r.rowcount:
                return False, "Categoria não encontrada."
    except IntegrityError:
        return False, f"Já existe uma categoria chamada \"{novo_nome}\"."
    limpar_caches()
    return True, ""


def excluir_categoria(codigo: str) -> tuple[bool, str]:
    """Só categoria sem lançamento. Com lançamento, apagar deixaria o
    dinheiro dele sem linha na DRE sem o usuário ver: ele reclassifica antes.
    As regras que apontavam para ela saem junto (o próximo extrato daquela
    contraparte volta para a sugestão padrão)."""
    engine, dono = _engine_e_dono()
    if engine is None:
        return False, "Banco não configurado ou sessão sem dono."
    with engine.begin() as conn:
        garantir_tabelas(conn)
        # Trava antes de contar: quem está gravando um lançamento nela segura
        # a linha em FOR SHARE (``_codigos_validos``) e esta espera o commit.
        existe = conn.execute(_texto("""
            SELECT 1 FROM pj_categorias
            WHERE user_id = CAST(:uid AS uuid) AND codigo = :codigo
            FOR UPDATE"""), {"uid": dono, "codigo": codigo}).fetchone()
        if existe is None:
            return False, "Categoria não encontrada."
        usos = int(conn.execute(_texto("""
            SELECT count(*) FROM pj_lancamentos
            WHERE user_id = CAST(:uid AS uuid) AND categoria = :codigo"""),
            {"uid": dono, "codigo": codigo}).scalar() or 0)
        if usos:
            return False, (f"{usos} lançamento(s) usam esta categoria. Troque a categoria "
                           "deles em 'Corrigir categoria' antes de excluí-la.")
        r = conn.execute(_texto("""
            DELETE FROM pj_categorias
            WHERE user_id = CAST(:uid AS uuid) AND codigo = :codigo"""),
            {"uid": dono, "codigo": codigo})
        if not r.rowcount:
            return False, "Categoria não encontrada."
        conn.execute(_texto("""
            DELETE FROM pj_regras WHERE user_id = CAST(:uid AS uuid) AND categoria = :codigo"""),
            {"uid": dono, "codigo": codigo})
    limpar_caches()
    return True, ""
