"""
core/config.py
Carregamento e validação de variáveis de ambiente.
Lê o arquivo .env na raiz do projeto via python-dotenv.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Banco de dados ────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "")

    # ── Inteligência artificial ───────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_S: int = int(os.getenv("AI_TIMEOUT_S", "45"))
    AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "2"))

    # ── Ambiente ──────────────────────────────────────────────────────
    APP_ENV: str = os.getenv("APP_ENV", "development")
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"

    @property
    def db_url(self) -> str:
        """Retorna a URL de conexão ativa (DATABASE_URL tem prioridade)."""
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

    def validate(self) -> list[str]:
        """
        Retorna lista de avisos sobre variáveis ausentes.
        Não lança exceção — o app decide como tratar.
        """
        warnings = []
        if not self.has_database:
            warnings.append("DATABASE_URL não configurada — funcionalidades de banco desativadas.")
        if not self.has_openai:
            warnings.append("OPENAI_API_KEY não configurada — funcionalidades de IA desativadas.")
        if self.MOCK_MODE:
            warnings.append("MOCK_MODE=true — dados mockados em uso.")
        return warnings


settings = Settings()
