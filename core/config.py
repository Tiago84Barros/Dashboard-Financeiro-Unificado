"""
core/config.py
Carregamento e validacao de variaveis de ambiente.
Le o arquivo .env na raiz do projeto via python-dotenv.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Banco de dados ────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "")

    # ── Inteligencia artificial ───────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_S: int = int(os.getenv("AI_TIMEOUT_S", "45"))
    AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "2"))

    # ── Ambiente ──────────────────────────────────────────────────────
    APP_ENV: str = os.getenv("APP_ENV", "development")
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"

    # ── Autenticacao simples (Streamlit Cloud) ────────────────────────
    # Texto simples ou hash SHA-256 da senha. Vazio = sem senha (dev local).
    # Gerar hash: python -c "import hashlib; print(hashlib.sha256(b'senha').hexdigest())"
    APP_PASSWORD: str = os.getenv("APP_PASSWORD", "")

    # ── Usuario proprietario dos dados ────────────────────────────────
    # UUID do usuario na tabela `usuarios`. Todas as queries filtram por este ID.
    # Obtido apos criar o usuario no banco: SELECT id FROM usuarios LIMIT 1;
    OWNER_USER_ID: str = os.getenv("OWNER_USER_ID", "")

    # ── Fontes de importacao (apps originais) ─────────────────────────
    # Connection strings dos bancos de dados dos repositorios originais.
    # Usados apenas para importacao de dados historicos — nunca para gravacao.
    SOURCE_DB_APP1: str = os.getenv("SOURCE_DB_APP1", "")  # Tiago84Barros/Dashboard
    SOURCE_DB_APP2: str = os.getenv("SOURCE_DB_APP2", "")  # Tiago84Barros/Dashboard-Investimentos
    SOURCE_DB_APP3: str = os.getenv("SOURCE_DB_APP3", "")  # Tiago84Barros/Controle_Financeiro

    @property
    def db_url(self) -> str:
        """Retorna a URL de conexao ativa (DATABASE_URL tem prioridade)."""
        return self.DATABASE_URL or self.SUPABASE_DB_URL

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def has_database(self) -> bool:
        return bool(self.db_url)

    @property
    def has_openai(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def has_owner(self) -> bool:
        """True se OWNER_USER_ID estiver configurado (necessario para queries reais)."""
        return bool(self.OWNER_USER_ID)

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
