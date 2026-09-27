"""
core/inteligencia_ativos/historico_repo.py
Leitura e gravação do histórico das análises.

Mora em ``user_settings.extra_settings`` (chave ``asset_analysis_history``),
como o Cenário de Investimentos: o Supabase está acima do teto de 500 MB e
algumas fotos compactas por ativo não justificam tabela nova nem migration.
O tamanho fica limitado por ``historico.MAX_POR_CHAVE``.

A gravação trava a linha (``FOR UPDATE``) e decide dentro da transação o que
entra: duas abas abertas não duplicam a mesma foto.
"""
from __future__ import annotations

from sqlalchemy import text

from core.inteligencia_ativos import historico as hist


def _uid(owner_id=None) -> str:
    if owner_id:
        return str(owner_id)
    from core.user_context import require_user
    return require_user()


def _eng(engine=None):
    if engine is not None:
        return engine
    from core.user_accounts import _engine
    return _engine()


def carregar(*, engine=None, owner_id=None) -> dict[str, list[hist.Snapshot]]:
    from core.user_accounts import _extra

    with _eng(engine).connect() as conn:
        valor = conn.execute(text(
            "SELECT extra_settings FROM user_settings WHERE user_id = :uid"
        ), {"uid": _uid(owner_id)}).scalar()
    return hist.ler(_extra(valor))


def registrar(fotos, *, forcar: str | None = None, engine=None,
              owner_id=None) -> tuple[dict[str, list[hist.Snapshot]], list[str]]:
    """(histórico depois da gravação, chaves gravadas). Sem nada material
    para gravar, não escreve."""
    from core.user_accounts import locked_preferences, write_preferences

    uid = _uid(owner_id)
    with _eng(engine).begin() as conn:
        extra = locked_preferences(conn, uid)
        historico, gravadas = hist.anexar(hist.ler(extra), fotos, forcar=forcar)
        if gravadas:
            write_preferences(conn, uid, hist.gravar_em(extra, historico))
    return historico, gravadas
