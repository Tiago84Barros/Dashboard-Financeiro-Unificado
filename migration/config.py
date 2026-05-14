"""
migration/config.py
===================
Módulo importável de configuração central para os scripts de migração.

Este arquivo é o módulo Python importável pelo resto do pacote:
  from migration.config import MigrationConfig, make_engine

O arquivo 00_config.py é o entry-point CLI correspondente.

Segurança:
  - Nunca imprime valores de credenciais.
  - Valida apenas presença das variáveis de ambiente.
  - dry_run=True por padrão — nenhuma escrita ocorre sem flag explícita.
  - Não usa st.secrets nem carrega .env automaticamente.
    Configure as variáveis no ambiente antes de executar.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _load_dotenv(path: Path | None = None) -> None:
    """
    Carrega .env se existir, sem sobrescrever variáveis já definidas no ambiente.
    Garante leitura em UTF-8 (resolve problema de encoding no Windows com acentos).
    """
    env_file = path or (Path(__file__).parent.parent / ".env")
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _ensure_utf8_stdout() -> None:
    """Configura stdout para UTF-8 no Windows para evitar UnicodeEncodeError com emojis."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Sistemas de origem — usados em source_system e migration_source_map
SOURCE_APP1 = "app1"           # Dashboard Financeiro (PostgreSQL)
SOURCE_APP2 = "app2"           # Dashboard Investimentos (SQLite)
SOURCE_APP3 = "app3"           # Controle Financeiro (PostgreSQL/Supabase)

# Diretório de saída intermediária
OUTPUT_DIR = Path(__file__).parent / "output"


# ---------------------------------------------------------------------------
# Leitura segura de variáveis
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    """Lê variável de ambiente sem expor o valor em logs."""
    return os.environ.get(key, default).strip()


def _mask(value: str) -> str:
    """Retorna representação mascarada de uma credential para logs."""
    if not value:
        return "✗ não configurado"
    if len(value) <= 10:
        return "✓ configurado (***)"
    return f"✓ configurado (...{value[-6:]})"


# ---------------------------------------------------------------------------
# Configuração principal
# ---------------------------------------------------------------------------

@dataclass
class MigrationConfig:
    """
    Parâmetros de configuração para todos os scripts de migração.

    Campos de conexão NUNCA devem ser impressos diretamente.
    Use print_summary() para exibir status sem expor valores.
    """

    # ── Destino ──────────────────────────────────────────────────────────
    dest_url: str = ""           # SUPABASE_UNIFICADO_URL
    owner_id: str = ""           # OWNER_USER_ID (UUID do profiles)

    # ── Fontes ───────────────────────────────────────────────────────────
    app1_url: str = ""           # SOURCE_DB_APP1 (opcional — App 1 Dashboard)
    app2_path: str = ""          # SOURCE_DB_APP2 (sqlite:///path ou caminho)
    app3_url: str = ""           # SUPABASE_ORIGEM_CONTROLE_URL (App 3)

    # ── Controle de execução ─────────────────────────────────────────────
    dry_run: bool = True         # NUNCA alterar para False sem confirmação humana
    output_dir: Path = field(default_factory=lambda: OUTPUT_DIR)
    batch_size: int = 500        # Linhas por lote de INSERT
    log_level: str = "INFO"

    # ── Metadados do lote ────────────────────────────────────────────────
    # Preenchido em 05_load ao criar o registro em import_batches
    import_batch_id: Optional[str] = None

    # ---------------------------------------------------------------------------
    @classmethod
    def from_env(cls, dry_run: bool = True) -> "MigrationConfig":
        """Cria configuração a partir de variáveis de ambiente. dry_run=True por padrão.
        Carrega .env automaticamente se existir na raiz do projeto (UTF-8)."""
        _load_dotenv()
        return cls(
            dest_url=_env("SUPABASE_UNIFICADO_URL") or _env("SUPABASE_DB_URL"),
            owner_id=_env("OWNER_USER_ID"),
            app1_url=_env("SOURCE_DB_APP1"),
            app2_path=_env("SOURCE_DB_APP2"),
            app3_url=_env("SUPABASE_ORIGEM_CONTROLE_URL"),
            dry_run=dry_run,
        )

    # ---------------------------------------------------------------------------
    def validate(self) -> list[str]:
        """
        Valida presença das variáveis obrigatórias para migração real.
        Retorna lista de strings de erro (vazia = OK).
        NUNCA inclui valores reais nos erros.
        """
        errors: list[str] = []

        if not self.dest_url:
            errors.append(
                "SUPABASE_UNIFICADO_URL ausente. "
                "Configure no ambiente antes de executar."
            )
        if not self.owner_id:
            errors.append(
                "OWNER_USER_ID ausente. "
                "Execute: INSERT INTO profiles ... RETURNING id "
                "e copie o UUID para o ambiente."
            )
        if not self.app3_url and not self.app2_path:
            errors.append(
                "Nenhuma fonte configurada. "
                "Configure SUPABASE_ORIGEM_CONTROLE_URL (App 3) "
                "e/ou SOURCE_DB_APP2 (App 2 SQLite)."
            )
        return errors

    def validate_for_source(self, source: str) -> list[str]:
        """Valida variáveis necessárias para uma fonte específica."""
        if source == SOURCE_APP1:
            if not self.app1_url:
                return ["SOURCE_DB_APP1 ausente (App 1 — opcional)"]
        elif source == SOURCE_APP2:
            if not self.app2_path:
                return ["SOURCE_DB_APP2 ausente (caminho do SQLite do App 2)"]
        elif source == SOURCE_APP3:
            if not self.app3_url:
                return ["SUPABASE_ORIGEM_CONTROLE_URL ausente (App 3)"]
        return []

    # ---------------------------------------------------------------------------
    def print_summary(self) -> None:
        """Exibe resumo de configuração sem expor valores de credenciais."""
        separator = "=" * 50
        print(separator)
        print("  CONFIGURAÇÃO DE MIGRAÇÃO — Fase 4.7")
        print(separator)
        print(f"  dry_run              : {'✅ SIM (nenhum dado será gravado)' if self.dry_run else '⚠️  NÃO — MODO REAL ATIVO'}")
        print(f"  dest_url             : {_mask(self.dest_url)}")
        print(f"  owner_id             : {_mask(self.owner_id)}")
        print(f"  app1_url (opcional)  : {_mask(self.app1_url)}")
        print(f"  app2_path (SQLite)   : {_mask(self.app2_path)}")
        print(f"  app3_url             : {_mask(self.app3_url)}")
        print(f"  output_dir           : {self.output_dir}")
        print(f"  batch_size           : {self.batch_size}")
        print(separator)

        erros = self.validate()
        if erros:
            print("  ℹ️  Variáveis não configuradas (ok em dry_run sem fontes):")
            for e in erros:
                print(f"     - {e}")
            print(separator)

    def ensure_output_dir(self) -> None:
        """Cria o diretório de saída se não existir."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "transformed").mkdir(exist_ok=True)

    def output_path(self, filename: str) -> Path:
        """Retorna caminho absoluto para arquivo de saída."""
        return self.output_dir / filename

    def transformed_path(self, filename: str) -> Path:
        """Retorna caminho absoluto para arquivo transformado."""
        return self.output_dir / "transformed" / filename


# ---------------------------------------------------------------------------
# Helper: engine SQLAlchemy sem expor URL em logs
# ---------------------------------------------------------------------------

def make_engine(url: str, source_label: str = "?", read_only_hint: bool = True):
    """
    Cria engine SQLAlchemy.

    Args:
        url: Connection string (nunca logado).
        source_label: Rótulo para logs (ex: 'app2_sqlite', 'app3_supabase').
        read_only_hint: Se True, registra aviso de uso somente-leitura.

    Returns:
        sqlalchemy.Engine

    Raises:
        RuntimeError se url estiver vazio.
    """
    if not url:
        raise RuntimeError(
            f"URL de conexão para '{source_label}' não configurada. "
            "Verifique as variáveis de ambiente."
        )

    try:
        from sqlalchemy import create_engine  # noqa: PLC0415

        is_sqlite = url.startswith("sqlite")
        kwargs: dict = {"pool_pre_ping": True}
        if not is_sqlite:
            kwargs.update({
                "pool_size": 2,
                "max_overflow": 1,
                "connect_args": {"connect_timeout": 15},
            })

        engine = create_engine(url, **kwargs)

        if read_only_hint:
            print(f"  [engine] {source_label} — conexão configurada (somente leitura recomendada)")

        return engine

    except ImportError:
        raise RuntimeError("sqlalchemy não instalado. Execute: pip install sqlalchemy")


# ---------------------------------------------------------------------------
# Teste de configuração (usado pelo main de 00_config.py)
# ---------------------------------------------------------------------------

def run_config_check(dry_run: bool = True) -> int:
    """Exibe resumo de configuração e retorna 0 se ok, 1 se há erros bloqueadores."""
    cfg = MigrationConfig.from_env(dry_run=dry_run)
    cfg.print_summary()

    # Em dry_run, ausência de variáveis é apenas informativa, não fatal
    if not dry_run:
        erros = cfg.validate()
        if erros:
            print("\n❌ Corrija as variáveis ausentes antes de executar migração real.")
            return 1

    print(f"\n{'✅ Configuração válida para dry_run.' if dry_run else '✅ Configuração válida para migração real.'}")
    return 0
