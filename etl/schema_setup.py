"""
etl/schema_setup.py
Inicialização segura do schema do banco de dados.

Cria as 13 tabelas definidas em modelagem_inicial.md usando CREATE TABLE IF NOT EXISTS.
Seguro para executar múltiplas vezes — nunca sobrescreve dados existentes.
Respeita a ordem das dependências de chave estrangeira.

Tabelas (em ordem de criação):
  usuarios → contas → categorias → transacoes → orcamentos → metas
  ativos → operacoes → proventos → cotacoes
  [pipeline] data_update_registry → data_update_logs → data_freshness_status

Uso:
    from etl.schema_setup import criar_schema, verificar_schema
    status  = verificar_schema()    # {'usuarios': True, 'contas': False, ...}
    result  = criar_schema()        # {'ok': True, 'criadas': [...], 'erros': [...]}
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from core.database import get_engine

# ── DDL — espelha modelagem_inicial.md ───────────────────────────────────────
# Cada DDL é idempotente (IF NOT EXISTS). Índices também.

_DDL: list[tuple[str, str]] = [
    ("usuarios", """
        CREATE TABLE IF NOT EXISTS usuarios (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            nome        VARCHAR(150) NOT NULL,
            email       VARCHAR(255) UNIQUE NOT NULL,
            senha_hash  TEXT NOT NULL,
            criado_em   TIMESTAMPTZ DEFAULT NOW(),
            ativo       BOOLEAN DEFAULT TRUE
        );
    """),

    ("contas", """
        CREATE TABLE IF NOT EXISTS contas (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id      UUID NOT NULL REFERENCES usuarios(id),
            nome            VARCHAR(100) NOT NULL,
            tipo            VARCHAR(50)  NOT NULL,
            banco           VARCHAR(100),
            saldo_inicial   NUMERIC(15,2) DEFAULT 0,
            moeda           CHAR(3) DEFAULT 'BRL',
            ativo           BOOLEAN DEFAULT TRUE,
            criado_em       TIMESTAMPTZ DEFAULT NOW()
        );
    """),

    ("categorias", """
        CREATE TABLE IF NOT EXISTS categorias (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id  UUID REFERENCES usuarios(id),
            nome        VARCHAR(100) NOT NULL,
            tipo        VARCHAR(20)  NOT NULL,
            icone       VARCHAR(50),
            cor         CHAR(7),
            pai_id      UUID REFERENCES categorias(id)
        );
    """),

    ("transacoes", """
        CREATE TABLE IF NOT EXISTS transacoes (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id        UUID NOT NULL REFERENCES usuarios(id),
            conta_id          UUID NOT NULL REFERENCES contas(id),
            categoria_id      UUID REFERENCES categorias(id),
            descricao         VARCHAR(255) NOT NULL,
            valor             NUMERIC(15,2) NOT NULL,
            data_competencia  DATE NOT NULL,
            data_pagamento    DATE,
            tipo              VARCHAR(20)  NOT NULL,
            status            VARCHAR(20)  DEFAULT 'liquidado',
            recorrente        BOOLEAN DEFAULT FALSE,
            parcela_atual     SMALLINT,
            total_parcelas    SMALLINT,
            grupo_parcela     UUID,
            origem            VARCHAR(50)  DEFAULT 'manual',
            criado_em         TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_transacoes_usuario_data
            ON transacoes(usuario_id, data_competencia DESC);
        CREATE INDEX IF NOT EXISTS idx_transacoes_categoria
            ON transacoes(categoria_id);
    """),

    ("orcamentos", """
        CREATE TABLE IF NOT EXISTS orcamentos (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id   UUID NOT NULL REFERENCES usuarios(id),
            categoria_id UUID NOT NULL REFERENCES categorias(id),
            mes_ano      DATE NOT NULL,
            valor_limite NUMERIC(15,2) NOT NULL,
            UNIQUE(usuario_id, categoria_id, mes_ano)
        );
    """),

    ("metas", """
        CREATE TABLE IF NOT EXISTS metas (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id       UUID NOT NULL REFERENCES usuarios(id),
            nome             VARCHAR(150) NOT NULL,
            tipo             VARCHAR(50),
            valor_alvo       NUMERIC(15,2) NOT NULL,
            valor_acumulado  NUMERIC(15,2) DEFAULT 0,
            prazo            DATE,
            ativa            BOOLEAN DEFAULT TRUE,
            criado_em        TIMESTAMPTZ DEFAULT NOW()
        );
    """),

    ("ativos", """
        CREATE TABLE IF NOT EXISTS ativos (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ticker      VARCHAR(20)  UNIQUE NOT NULL,
            nome        VARCHAR(200) NOT NULL,
            classe      VARCHAR(50)  NOT NULL,
            setor       VARCHAR(100),
            moeda       CHAR(3) DEFAULT 'BRL',
            exchange    VARCHAR(20)
        );
    """),

    ("operacoes", """
        CREATE TABLE IF NOT EXISTS operacoes (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id      UUID NOT NULL REFERENCES usuarios(id),
            ativo_id        UUID NOT NULL REFERENCES ativos(id),
            tipo            VARCHAR(10)  NOT NULL,
            quantidade      NUMERIC(18,8) NOT NULL,
            preco_unitario  NUMERIC(15,6) NOT NULL,
            taxas           NUMERIC(15,2) DEFAULT 0,
            data_operacao   DATE NOT NULL,
            corretora       VARCHAR(100),
            criado_em       TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_operacoes_usuario_ativo
            ON operacoes(usuario_id, ativo_id);
    """),

    ("proventos", """
        CREATE TABLE IF NOT EXISTS proventos (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id      UUID NOT NULL REFERENCES usuarios(id),
            ativo_id        UUID NOT NULL REFERENCES ativos(id),
            tipo            VARCHAR(30)  NOT NULL,
            valor_por_cota  NUMERIC(15,6) NOT NULL,
            quantidade      NUMERIC(18,8) NOT NULL,
            valor_total     NUMERIC(15,2) NOT NULL,
            data_com        DATE,
            data_pagamento  DATE NOT NULL
        );
    """),

    ("cotacoes", """
        CREATE TABLE IF NOT EXISTS cotacoes (
            ativo_id    UUID NOT NULL REFERENCES ativos(id),
            timestamp   TIMESTAMPTZ NOT NULL,
            abertura    NUMERIC(15,6),
            maxima      NUMERIC(15,6),
            minima      NUMERIC(15,6),
            fechamento  NUMERIC(15,6) NOT NULL,
            volume      NUMERIC(20,2),
            PRIMARY KEY (ativo_id, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_cotacoes_ativo_timestamp
            ON cotacoes(ativo_id, timestamp DESC);
    """),

    # ── Tabelas administrativas do pipeline de dados ──────────────────────────
    ("data_update_registry", """
        CREATE TABLE IF NOT EXISTS data_update_registry (
            id          BIGSERIAL PRIMARY KEY,
            table_name  TEXT NOT NULL,
            source_name TEXT NOT NULL,
            job_name    TEXT UNIQUE,
            update_type TEXT NOT NULL DEFAULT 'incremental',
            frequency   TEXT NOT NULL DEFAULT 'diario',
            priority    INTEGER DEFAULT 1,
            is_active   BOOLEAN DEFAULT TRUE,
            description TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """),

    ("data_update_logs", """
        CREATE TABLE IF NOT EXISTS data_update_logs (
            id                      BIGSERIAL PRIMARY KEY,
            table_name              TEXT NOT NULL,
            source_name             TEXT NOT NULL,
            job_name                TEXT,
            started_at              TIMESTAMPTZ,
            finished_at             TIMESTAMPTZ,
            status                  TEXT,
            records_inserted        INTEGER DEFAULT 0,
            records_updated         INTEGER DEFAULT 0,
            records_failed          INTEGER DEFAULT 0,
            error_message           TEXT,
            execution_time_seconds  REAL,
            created_at              TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_dul_started_at
            ON data_update_logs(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_dul_job_name
            ON data_update_logs(job_name, started_at DESC);
    """),

    ("data_freshness_status", """
        CREATE TABLE IF NOT EXISTS data_freshness_status (
            id                    BIGSERIAL PRIMARY KEY,
            table_name            TEXT NOT NULL,
            source_name           TEXT NOT NULL,
            job_name              TEXT UNIQUE NOT NULL,
            last_success_at       TIMESTAMPTZ,
            last_attempt_at       TIMESTAMPTZ,
            last_status           TEXT,
            next_expected_update  TIMESTAMPTZ,
            freshness_status      TEXT DEFAULT 'never_updated',
            last_records_inserted INTEGER DEFAULT 0,
            last_records_updated  INTEGER DEFAULT 0,
            last_records_failed   INTEGER DEFAULT 0,
            last_error_message    TEXT,
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        );
    """),
]

TABELAS_ESPERADAS: list[str] = [nome for nome, _ in _DDL]


# ── Funções públicas ──────────────────────────────────────────────────────────

def verificar_schema() -> dict[str, bool]:
    """
    Verifica quais tabelas do schema unificado existem no banco conectado.

    Retorna:
        {'usuarios': True, 'contas': False, ...}  — False se não conectado
    """
    engine = get_engine()
    if engine is None:
        return {t: False for t in TABELAS_ESPERADAS}
    try:
        existentes = set(sa_inspect(engine).get_table_names())
        return {t: (t in existentes) for t in TABELAS_ESPERADAS}
    except Exception:
        return {t: False for t in TABELAS_ESPERADAS}


def criar_schema() -> dict[str, object]:
    """
    Cria todas as tabelas ausentes. Nunca altera ou apaga dados existentes.
    Seguro para executar múltiplas vezes.

    Retorna:
        {
            'ok':          bool,
            'criadas':     list[str],   # tabelas que não existiam e foram criadas
            'ja_existiam': list[str],   # tabelas que já existiam
            'erros':       list[str],
        }
    """
    engine = get_engine()
    if engine is None:
        return {
            "ok": False,
            "criadas": [],
            "ja_existiam": [],
            "erros": [
                "Sem conexao com banco. Configure SUPABASE_UNIFICADO_URL "
                "no .env local ou em Streamlit Secrets (Settings > Secrets)."
            ],
        }

    estado_antes = verificar_schema()
    erros: list[str] = []

    try:
        with engine.begin() as conn:
            for nome, ddl in _DDL:
                try:
                    # Cada statement é executado separadamente (DDL + índices)
                    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
                        conn.execute(text(stmt))
                except Exception as exc:
                    erros.append(f"{nome}: {exc}")
    except Exception as exc:
        return {"ok": False, "criadas": [], "ja_existiam": [], "erros": [str(exc)]}

    estado_depois = verificar_schema()

    criadas    = [t for t in TABELAS_ESPERADAS if not estado_antes[t] and estado_depois[t]]
    ja_existiam = [t for t in TABELAS_ESPERADAS if estado_antes[t]]

    return {
        "ok":          len(erros) == 0,
        "criadas":     criadas,
        "ja_existiam": ja_existiam,
        "erros":       erros,
    }
