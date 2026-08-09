"""Gancho de captura de snapshot chamado ao salvar uma carteira-modelo.

Contrato central: NUNCA levanta excecao. Persistir o snapshot e um beneficio
adicional; falhar nele nao pode impedir o salvamento da carteira, que e a
funcionalidade que ja existia e continua sendo a prioridade.

Coberto por tests/test_portfolio_capture.py.
"""
from __future__ import annotations

import datetime as dt
import logging

from core.portfolio.registry import load_adapter
from core.portfolio.repository import apply_retention, prune_orphans, save_snapshots

logger = logging.getLogger(__name__)


def capture_snapshots(asset_class: str, model_id: str, items: list[dict],
                      params: dict, *, as_of: dt.date | None = None,
                      engine=None, owner_id=None) -> int:
    """Monta e grava os snapshots da carteira. Devolve quantos gravou (0 em falha)."""
    if not items:
        return 0

    gravados = 0
    try:
        adapter = load_adapter(asset_class)
        snapshots = adapter.build_snapshots(
            items,
            model_id=model_id,
            params=params or {},
            as_of=as_of or dt.date.today(),
        )
        gravados = save_snapshots(snapshots, engine=engine, owner_id=owner_id)
    except Exception:
        logger.warning("Falha ao capturar snapshot da carteira %s/%s; "
                       "a carteira foi salva normalmente.",
                       asset_class, model_id, exc_info=True)
        return 0

    # Limpeza e retencao sao oportunistas: falhar aqui nao invalida a gravacao.
    try:
        prune_orphans(engine=engine)
        apply_retention(asset_class, engine=engine)
    except Exception:
        logger.warning("Falha na manutencao de snapshots (%s).", asset_class, exc_info=True)

    return gravados
