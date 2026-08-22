"""
tests/test_b3_db_resolve_url.py
A-009: core.b3_db._resolve_url() tem uma cadeia de prioridade de URL própria
(SUPABASE_DB_URL_B3 > SUPABASE_DB_URL > settings.db_url), independente da de
core.config.Settings.db_url (SUPABASE_UNIFICADO_URL > DATABASE_URL >
SUPABASE_DB_URL). Isso é mantido de propósito (scripts de ingestão sobrescrevem
SUPABASE_DB_URL_B3 só na sessão do shell — ver local_staging/README.md), mas
não pode ficar silencioso: quando diverge do que o resto do app usaria (ex.:
DATABASE_URL sobrescrito para isolar um teste), _resolve_url() deve registrar
um aviso estruturado (sem vazar credenciais) em vez de simplesmente ignorar.
"""
import logging

import core.b3_db as b3_db
from core.config import settings


def test_resolve_url_prioriza_supabase_db_url_b3(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL_B3", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL_B3", "postgresql://u:p@legacy-host:5432/app1")
    assert b3_db._resolve_url() == "postgresql://u:p@legacy-host:5432/app1"


def test_resolve_url_cai_para_settings_db_url_quando_legado_ausente(monkeypatch):
    # Sem SUPABASE_DB_URL_B3/SUPABASE_DB_URL no ambiente, _resolve_url() respeita
    # settings.db_url — que por sua vez respeita DATABASE_URL (SUPABASE_UNIFICADO_URL
    # ausente). Este é o caminho "feliz": nenhum desvio silencioso.
    monkeypatch.delenv("SUPABASE_DB_URL_B3", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_UNIFICADO_URL", "", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://x@localhost:5433/warehouse",
                         raising=False)
    monkeypatch.setattr(settings, "SUPABASE_DB_URL", "", raising=False)
    assert b3_db._resolve_url() == "postgresql://x@localhost:5433/warehouse"


def test_resolve_url_diverge_de_database_url_registra_warning(monkeypatch, caplog):
    """
    Reproduz o achado A-009: DATABASE_URL do processo aponta para um banco de
    teste (warehouse local), mas SUPABASE_DB_URL_B3 legado permanece definido
    (comum quando o .env de produção configura o Supabase original do App 1).
    _resolve_url() ainda retorna o legado (contrato preservado — ver docstring
    do módulo), mas não pode fazer isso silenciosamente: deve logar a
    divergência, mascarando a senha.
    """
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL_B3",
                        "postgresql://reader:s3cr3t@real-supabase-host:5432/postgres")
    monkeypatch.setattr(settings, "SUPABASE_UNIFICADO_URL", "", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL",
                         "postgresql://x@127.0.0.1:5433/warehouse_teste", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_DB_URL", "", raising=False)

    with caplog.at_level(logging.WARNING, logger="core.b3_db"):
        url = b3_db._resolve_url()

    # Contrato preservado: continua devolvendo o legado (não muda comportamento
    # de conexão sem autorização explícita de migração de prioridade).
    assert url == "postgresql://reader:s3cr3t@real-supabase-host:5432/postgres"

    # Mas a divergência tem que estar visível no log, e sem a senha em texto puro.
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("SUPABASE_DB_URL_B3" in msg and "DATABASE_URL" in msg for msg in warnings) or \
        any("SUPABASE_DB_URL_B3" in msg for msg in warnings)
    full_log = "\n".join(warnings)
    assert "s3cr3t" not in full_log
    assert "real-supabase-host" in full_log  # host visível para diagnóstico


def test_resolve_url_sem_divergencia_nao_loga_warning(monkeypatch, caplog):
    # Quando o legado e settings.db_url apontam para a MESMA URL, não há
    # divergência a reportar — evita ruído de log em uso normal/legítimo.
    same_url = "postgresql://reader:pw@same-host:5432/postgres"
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL_B3", same_url)
    monkeypatch.setattr(settings, "SUPABASE_UNIFICADO_URL", "", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", same_url, raising=False)
    monkeypatch.setattr(settings, "SUPABASE_DB_URL", "", raising=False)

    with caplog.at_level(logging.WARNING, logger="core.b3_db"):
        url = b3_db._resolve_url()

    assert url == same_url
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings


def test_mask_url_esconde_senha_mantem_host():
    masked = b3_db._mask_url("postgresql://reader:s3cr3t@real-supabase-host:5432/postgres")
    assert "s3cr3t" not in masked
    assert "real-supabase-host" in masked
    assert "reader" in masked


def test_mask_url_url_ilegivel_nao_lanca():
    assert b3_db._mask_url("not-a-valid-url") != ""
