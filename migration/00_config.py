"""
migration/00_config.py
======================
Configuração central para os scripts de migração controlada.

Fase 4.6 — Dashboard Financeiro Unificado

Segurança:
  - Nunca imprime valores de credenciais.
  - Valida apenas presença das variáveis de ambiente.
  - dry_run=True por padrão — nenhuma escrita ocorre sem flag explícita.
  - Não usa st.secrets nem carrega .env automaticamente.
    Configure as variáveis no ambiente antes de executar.

Uso:
  from migration.config import MigrationConfig
  cfg = MigrationConfig.from_env()
  cfg.print_summary()
  erros = cfg.validate()
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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
        """Cria configuração a partir de variáveis de ambiente. dry_run=True por padrão."""
        return cls(
            dest_url=_env("SUPABASE_UNIFICADO_URL"),
            owner_id=_env("OWNER_USER_ID"),
            app1_url=_env("SOURCE_DB_APP1"),
            app2_path=_env("SOURCE_DB_APP2"),
            app3_url=_env("SUPABASE_ORIGEM_CONTROLE_URL"),
            dry_run=dry_run,
        )

    # ---------------------------------------------------------------------------
    def validate(self) -> list[str]:
        """
        Valida presença das variáveis obrigatórias.
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
        print("  CONFIGURAÇÃO DE MIGRAÇÃO — Fase 4.6")
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
            print("  ⚠️  VARIÁVEIS AUSENTES:")
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
        from sqlalchemy import create_engine, text

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
# Execução direta — teste de configuração
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = MigrationConfig.from_env()
    cfg.print_summary()

    erros = cfg.validate()
    if erros:
        print("\n❌ Corrija as variáveis ausentes antes de continuar.")
        sys.exit(1)
    else:
        print("\n✅ Configuração válida. Pronto para dry_run.")
