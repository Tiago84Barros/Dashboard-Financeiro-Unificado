"""
core/config.py
Carregamento e validacao de variaveis de ambiente.
Le o arquivo .env na raiz do projeto via python-dotenv.

Estrategia de banco (Fase 4.0):
  Prioridade de db_url: SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL
  - SUPABASE_UNIFICADO_URL  : projeto Supabase Dashboard Financeiro (banco central)
  - SUPABASE_ORIGEM_CONTROLE_URL: projeto Supabase Controle Financeiro (migracao)
  - SOURCE_DB_APP2          : SQLite do Dashboard-Investimentos (migracao)
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Banco unificado (Dashboard Financeiro — banco central do App 4) ───────
    # Connection string do pooler Supabase (Transaction Mode, porta 6543).
    # Formato: postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres
    SUPABASE_UNIFICADO_URL: str = os.getenv("SUPABASE_UNIFICADO_URL", "")
    SUPABASE_UNIFICADO_ANON_KEY: str = os.getenv("SUPABASE_UNIFICADO_ANON_KEY", "")

    # ── Banco de origem (Controle Financeiro — leitura durante migracao) ──────
    # Usado apenas para importar dados historicos. Nunca para gravacao.
    SUPABASE_ORIGEM_CONTROLE_URL: str = os.getenv("SUPABASE_ORIGEM_CONTROLE_URL", "")
    SUPABASE_ORIGEM_CONTROLE_ANON_KEY: str = os.getenv("SUPABASE_ORIGEM_CONTROLE_ANON_KEY", "")

    # ── Variaveis legadas (retrocompatibilidade) ──────────────────────────────
    # Mantidas para nao quebrar .env existentes. Preferir SUPABASE_UNIFICADO_URL.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "")

    # ── Inteligencia artificial ───────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_S: int = int(os.getenv("AI_TIMEOUT_S", "45"))
    AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "2"))

    # ── Ambiente ──────────────────────────────────────────────────────────────
    APP_ENV: str = os.getenv("APP_ENV", "development")
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"

    # ── Autenticacao simples (Streamlit Cloud) ────────────────────────────────
    # Texto simples ou hash SHA-256 da senha. Vazio = sem senha (dev local).
    # Gerar hash: python -c "import hashlib; print(hashlib.sha256(b'senha').hexdigest())"
    APP_PASSWORD: str = os.getenv("APP_PASSWORD", "")

    # ── Usuario proprietario dos dados ────────────────────────────────────────
    # UUID do usuario na tabela `usuarios`. Todas as queries filtram por este ID.
    OWNER_USER_ID: str = os.getenv("OWNER_USER_ID", "")

    # ── Fontes de importacao (apps originais — somente leitura) ──────────────
    SOURCE_DB_APP1: str = os.getenv("SOURCE_DB_APP1", "")  # Dashboard (PostgreSQL)
    SOURCE_DB_APP2: str = os.getenv("SOURCE_DB_APP2", "")  # Dashboard-Investimentos (SQLite)
    SOURCE_DB_APP3: str = os.getenv("SOURCE_DB_APP3", "")  # Controle_Financeiro (alias legado)

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
            warnings.append("DATABASE_URL nao configurada — banco desativado.")
        if not self.has_openai:
            warnings.append("OPENAI_API_KEY nao configurada — IA desativada.")
        if self.MOCK_MODE:
            warnings.append("MOCK_MODE=true — dados mockados em uso.")
        if not self.has_owner and not self.MOCK_MODE:
            warnings.append("OWNER_USER_ID nao configurado — filtro de usuario inativo.")
        return warnings


settings = Settings()
