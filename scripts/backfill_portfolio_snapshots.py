"""Reconstroi o snapshot analitico das carteiras-modelo ja salvas.

Simulacao por padrao; grava somente com --apply (padrao dos scripts do projeto).

LIMITE CONHECIDO: as vintages point-in-time em market.calculated_metric_vintages
sao hoje praticamente todas baseline, entao o backfill grava o valor ATUAL, nao
o da data da selecao. Por isso todo payload gerado aqui leva
provenance.backfilled = True. As gravacoes feitas a partir de agora, pelo
gancho em core/portfolio/capture.py, capturam o valor correto no momento certo.

Uso:
    python -m scripts.backfill_portfolio_snapshots
    python -m scripts.backfill_portfolio_snapshots --apply
    python -m scripts.backfill_portfolio_snapshots --apply --classe b3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.portfolio.registry import SPECS, get_spec, load_adapter
from core.portfolio.repository import save_snapshots

logger = logging.getLogger(__name__)


def _parse_json(valor, default):
    if isinstance(valor, dict):
        return valor
    if not valor:
        return default
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return default


def active_models(asset_class: str, *, engine, owner_id: str) -> list[dict]:
    """Modelos ativos do dono para a classe.

    Lista vazia se a tabela nao existir (classe nunca usada) ou se a consulta
    falhar por qualquer outro motivo (conexao caida, permissao, erro de SQL).
    Em qualquer um desses casos a falha e logada com exc_info, para que uma
    ausencia legitima de carteira nao se confunda, no log, com uma consulta
    quebrada — a contagem "0" sozinha nao distingue as duas coisas.
    """
    spec = get_spec(asset_class)
    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text(f"""
                    SELECT id, params_json FROM {spec.models_table}
                    WHERE user_id = :uid AND status = 'active'
                    ORDER BY created_at DESC, id DESC
                """),
                {"uid": str(owner_id)},
            ).mappings().all()
    except DBAPIError:
        logger.warning(
            "Falha ao consultar modelos ativos da classe %s (tabela %s); "
            "contribuicao considerada zero nesta rodada.",
            asset_class, spec.models_table, exc_info=True,
        )
        return []
    return [{"id": str(l["id"]), "params_json": _parse_json(l["params_json"], {})}
            for l in linhas]


def read_model_items(asset_class: str, model_id: str, *, engine) -> list[dict]:
    """Itens gravados do modelo, na ordem de peso decrescente."""
    spec = get_spec(asset_class)
    with engine.connect() as conn:
        linhas = conn.execute(
            text(f"""
                SELECT * FROM {spec.items_table}
                WHERE model_id = :mid
                ORDER BY weight DESC, {spec.symbol_column}
            """),
            {"mid": str(model_id)},
        ).mappings().all()
    return [dict(l) for l in linhas]


def backfill(*, engine, owner_id: str, apply: bool,
             classes: list[str] | None = None) -> dict[str, int]:
    """Reconstroi os snapshots. Devolve {classe: quantidade}."""
    alvo = sorted(classes) if classes else sorted(SPECS)
    resumo: dict[str, int] = {}
    hoje = dt.date.today()

    for key in alvo:
        total = 0
        for modelo in active_models(key, engine=engine, owner_id=owner_id):
            itens = read_model_items(key, modelo["id"], engine=engine)
            if not itens:
                continue
            snapshots = load_adapter(key).build_snapshots(
                itens, model_id=modelo["id"], params=modelo["params_json"], as_of=hoje,
            )
            for snap in snapshots:
                snap.payload["provenance"]["backfilled"] = True
            if apply:
                save_snapshots(snapshots, engine=engine, owner_id=owner_id)
            total += len(snapshots)
        resumo[key] = total
    return resumo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill de snapshots das carteiras-modelo.")
    parser.add_argument("--apply", action="store_true",
                        help="grava de fato (sem esta flag, apenas simula)")
    parser.add_argument("--classe", action="append", choices=sorted(SPECS),
                        help="limita a uma ou mais classes; pode repetir")
    args = parser.parse_args(argv)

    from core.config import settings
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        print("Banco unificado nao configurado (DATABASE_URL ausente).", file=sys.stderr)
        return 2
    if not settings.OWNER_USER_ID:
        print("OWNER_USER_ID nao configurado.", file=sys.stderr)
        return 2

    resumo = backfill(engine=engine, owner_id=str(settings.OWNER_USER_ID),
                      apply=args.apply, classes=args.classe)

    modo = "GRAVADO" if args.apply else "SIMULACAO (use --apply para gravar)"
    print(f"[{modo}]")
    for classe in sorted(resumo):
        print(f"  {classe:>4}: {resumo[classe]} snapshots")
    print("  Payloads marcados com provenance.backfilled = True "
          "(valor de hoje, nao da data da selecao).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
