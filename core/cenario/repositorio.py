"""
core/cenario/repositorio.py
Leitura e gravação do Cenário de Investimentos do usuário.

Mora em ``user_settings.extra_settings`` (chave ``investment_scenario``), do
mesmo jeito que o tema (``core/user_preferences.py``): o Supabase está acima
do teto de 500 MB, e um JSON de 12 itens por usuário não justifica tabela
nova nem migration.

Gravar só por ``salvar``, com a versão que a tela leu e uma origem de
``modelo.ORIGENS``. Nenhum caminho de LLM chama este módulo.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from core.cenario import modelo as mod


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


def carregar(*, engine=None, owner_id=None) -> mod.Cenario:
    from core.user_accounts import _extra

    uid = _uid(owner_id)
    with _eng(engine).connect() as conn:
        valor = conn.execute(text(
            "SELECT extra_settings FROM user_settings WHERE user_id = :uid"
        ), {"uid": uid}).scalar()
    return mod.Cenario.de_dict(_extra(valor).get(mod.CHAVE_PREFERENCIA))


def salvar(entradas: dict, *, versao_esperada: int, origem: str,
           engine=None, owner_id=None,
           agora: dt.datetime | None = None
           ) -> tuple[mod.Cenario, list[str], list[str]]:
    """(cenário gravado, itens alterados, erros).

    A linha é travada (``FOR UPDATE``) e a versão conferida dentro da mesma
    transação: outra aba que salvou antes faz esta levantar
    ``ConflitoDeVersao`` em vez de apagar o que a outra gravou.
    """
    from core.user_accounts import locked_preferences, write_preferences

    if origem not in mod.ORIGENS:
        raise ValueError(f"origem de alteração não permitida: {origem!r}")
    uid = _uid(owner_id)
    agora = agora or dt.datetime.now(dt.timezone.utc)
    with _eng(engine).begin() as conn:
        extra = locked_preferences(conn, uid)
        atual = mod.Cenario.de_dict(extra.get(mod.CHAVE_PREFERENCIA))
        if atual.versao != versao_esperada:
            raise mod.ConflitoDeVersao(
                f"O cenário foi salvo em outra sessão (versão {atual.versao}; "
                f"esta tela leu a {versao_esperada}). Recarregue antes de "
                "editar.")
        novo, alterados, erros = mod.revisar(
            atual, entradas, origem=origem, hoje=agora.date(), agora=agora)
        if erros or not alterados:
            return atual, alterados, erros
        write_preferences(conn, uid, mod.gravar_em(
            extra, novo, versao_esperada=versao_esperada))
    return novo, alterados, []
