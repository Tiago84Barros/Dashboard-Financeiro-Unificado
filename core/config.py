"""
core/config.py
Carregamento e validacao de variaveis de ambiente.

Ordem de prioridade (da maior para a menor):
  1. st.secrets  — Streamlit Cloud (Settings > Secrets) ou .streamlit/secrets.toml local
  2. os.environ  — populado por load_dotenv() do arquivo .env local
  3. valor padrao embutido

Estrategia de banco (Fase 4.0):
  Prioridade de db_url: SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL
  - SUPABASE_UNIFICADO_URL      : projeto Supabase Dashboard Financeiro (banco central)
  - SUPABASE_ORIGEM_CONTROLE_URL: projeto Supabase Controle Financeiro (migracao)
  - SOURCE_DB_APP2              : SQLite do Dashboard-Investimentos (migracao)
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    """
    Le a variavel `key` em ordem de prioridade:
      1. st.secrets (Streamlit Cloud / .streamlit/secrets.toml)
      2. os.environ (populado por load_dotenv do .env local)
      3. default

    Nunca lanca excecao — seguro fora de contexto Streamlit (testes, CLI).
    Valores TOML nao-string (bool, int) sao convertidos para str antes de retornar.
    """
    try:
        import streamlit as st  # importacao local: evita falha em contextos sem Streamlit

        val = st.secrets.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.getenv(key, default)


class Settings:
    # ── Banco unificado (Dashboard Financeiro — banco central do App 4) ───────
    # Connection string do pooler Supabase (Transaction Mode, porta 6543).
    # Formato: postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres
    SUPABASE_UNIFICADO_URL: str = _get_secret("SUPABASE_UNIFICADO_URL")
    SUPABASE_UNIFICADO_ANON_KEY: str = _get_secret("SUPABASE_UNIFICADO_ANON_KEY")

    # ── Banco de origem (Controle Financeiro — leitura durante migracao) ──────
    # Usado apenas para importar dados historicos. Nunca para gravacao.
    SUPABASE_ORIGEM_CONTROLE_URL: str = _get_secret("SUPABASE_ORIGEM_CONTROLE_URL")
    SUPABASE_ORIGEM_CONTROLE_ANON_KEY: str = _get_secret("SUPABASE_ORIGEM_CONTROLE_ANON_KEY")

    # ── Variaveis legadas (retrocompatibilidade) ──────────────────────────────
    # Mantidas para nao quebrar .env existentes. Preferir SUPABASE_UNIFICADO_URL.
    DATABASE_URL: str = _get_secret("DATABASE_URL")
    SUPABASE_DB_URL: str = _get_secret("SUPABASE_DB_URL")

    # ── Inteligencia artificial ───────────────────────────────────────────────
    OPENAI_API_KEY: str = _get_secret("OPENAI_API_KEY")
    AI_PROVIDER: str = _get_secret("AI_PROVIDER", "openai")
    AI_MODEL: str = _get_secret("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_S: int = int(_get_secret("AI_TIMEOUT_S", "45"))
    AI_MAX_RETRIES: int = int(_get_secret("AI_MAX_RETRIES", "2"))

    # ── Gemini (provedor de fallback) ─────────────────────────────────────────
    # Usado automaticamente quando a OpenAI falha (ex.: cota 429). Acessado via
    # endpoint compatível com a API da OpenAI, então reaproveita o mesmo SDK.
    # Aceita GEMINI_API_KEY ou GOOGLE_API_KEY.
    GEMINI_API_KEY: str = _get_secret("GEMINI_API_KEY") or _get_secret("GOOGLE_API_KEY")
    GEMINI_MODEL: str = _get_secret("GEMINI_MODEL", "gemini-2.0-flash")
    GEMINI_BASE_URL: str = _get_secret(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

    # ── Ambiente ──────────────────────────────────────────────────────────────
    APP_ENV: str = _get_secret("APP_ENV", "development")
    MOCK_MODE: bool = _get_secret("MOCK_MODE", "true").lower() == "true"

    # ── Autenticacao simples (Streamlit Cloud) ────────────────────────────────
    # Texto simples ou hash SHA-256 da senha. Vazio = sem senha (dev local).
    # Gerar hash: python -c "import hashlib; print(hashlib.sha256(b'senha').hexdigest())"
    APP_PASSWORD: str = _get_secret("APP_PASSWORD")

    # ── Usuario proprietario dos dados ────────────────────────────────────────
    # UUID do usuario na tabela `usuarios`. Todas as queries filtram por este ID.
    OWNER_USER_ID: str = _get_secret("OWNER_USER_ID")

    # ── Fontes de importacao (apps originais — somente leitura) ──────────────
    SOURCE_DB_APP1: str = _get_secret("SOURCE_DB_APP1")  # Dashboard (PostgreSQL)
    SOURCE_DB_APP2: str = _get_secret("SOURCE_DB_APP2")  # Dashboard-Investimentos (SQLite)
    SOURCE_DB_APP3: str = _get_secret("SOURCE_DB_APP3")  # Controle_Financeiro (alias legado)

    # ── Propriedades derivadas ────────────────────────────────────────────────

    @property
    def db_url(self) -> str:
        """
        Retorna a URL do banco unificado.
        Prioridade: SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL
        """
        return (
            self.SUPABASE_UNIFICADO_URL
            or self.DATABASE_URL
            or self.SUPABASE_DB_URL
            or ""
        )

    @property
    def url_origem_controle(self) -> str:
        """
        URL do Supabase Controle Financeiro (fonte temporaria de migracao).
        SUPABASE_ORIGEM_CONTROLE_URL tem prioridade; SOURCE_DB_APP3 e alias legado.
        """
        return self.SUPABASE_ORIGEM_CONTROLE_URL or self.SOURCE_DB_APP3

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def has_database(self) -> bool:
        return bool(self.db_url)

    @property
    def has_supabase_unificado(self) -> bool:
        """True se SUPABASE_UNIFICADO_URL estiver configurada (banco unificado ativo)."""
        return bool(self.SUPABASE_UNIFICADO_URL)

    @property
    def has_openai(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def has_gemini(self) -> bool:
        return bool(self.GEMINI_API_KEY)

    @property
    def has_llm(self) -> bool:
        """True se houver ao menos um provedor LLM configurado (OpenAI ou Gemini)."""
        return bool(self.OPENAI_API_KEY or self.GEMINI_API_KEY)

    @property
    def has_owner(self) -> bool:
        """True se OWNER_USER_ID estiver configurado (necessario para queries reais)."""
        return bool(self.OWNER_USER_ID)

    @property
    def has_origem_controle(self) -> bool:
        """True se a URL de origem do Controle Financeiro estiver configurada."""
        return bool(self.url_origem_controle)

    @property
    def has_source_app1(self) -> bool:
        return bool(self.SOURCE_DB_APP1)

    @property
    def has_source_app2(self) -> bool:
        return bool(self.SOURCE_DB_APP2)

    @property
    def has_source_app3(self) -> bool:
        return bool(self.SOURCE_DB_APP3)

    def validate(self) -> list[str]:
        """
        Retorna lista de avisos sobre variaveis ausentes.
        Nao lanca excecao — o app decide como tratar.
        """
        warnings = []
        if not self.has_database:
            warnings.append(
                "Banco de dados nao configurado — defina SUPABASE_UNIFICADO_URL "
                "no .env local ou em Streamlit Secrets (Settings > Secrets)."
            )
        if not self.has_openai:
            warnings.append("OPENAI_API_KEY nao configurada — IA desativada.")
        if self.MOCK_MODE:
            warnings.append("MOCK_MODE=true — dados mockados em uso.")
        if not self.has_owner and not self.MOCK_MODE:
            warnings.append("OWNER_USER_ID nao configurado — filtro de usuario inativo.")
        return warnings


settings = Settings()
