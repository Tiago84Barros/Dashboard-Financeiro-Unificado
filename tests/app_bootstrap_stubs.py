"""Dublês dos módulos que `app.py` importa no bootstrap.

Três arquivos de teste executavam `app.py` via `runpy` com a mesma lista de
`monkeypatch.setitem(sys.modules, ...)` copiada à mão. Quando o trabalho de
multiusuário acrescentou `from core.auth import encerrar_sessao` e
`from core.user_context import principal` à sidebar, as três cópias ficaram
desatualizadas de uma vez e a suíte passou a quebrar com
`ImportError: cannot import name 'encerrar_sessao'`.

A lista mora aqui, em cópia única, e `tests/test_app_bootstrap_stubs.py`
compara este dicionário com o que `app.py` de fato importa.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

# Módulos que os testes de bootstrap deixam executar de verdade. Não são
# dublados de propósito: `core.app_test_mode` é a regra sob teste, e
# `core.erro_diagnostico` produz o texto que o teste do handler de erro
# inspeciona. Um import novo fora desta lista e fora de STUBS faz
# `test_app_bootstrap_stubs.py` falhar — que é o ponto.
MODULOS_EXECUTADOS_DE_VERDADE = frozenset({
    "core.app_test_mode",
    "core.erro_diagnostico",
})

# Identidade sintética: `principal()` real lê `st.session_state`, que não
# existe sob `runpy`. O duble devolve uma pessoa conectada porque o bootstrap
# só chega à sidebar depois de `verificar_autenticacao()`.
USUARIO_SINTETICO = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Teste",
}


def stubs_de_bootstrap(**overrides) -> dict[str, SimpleNamespace]:
    """Mapa módulo → duble, com a superfície que `app.py` importa.

    `overrides` substitui um duble inteiro (ex.: `design_componentes=` com um
    `mensagem_erro` que grava as chamadas). A chave usa `_` no lugar de `.`.
    """
    stubs = {
        "core.auth": SimpleNamespace(
            verificar_autenticacao=lambda: None,
            encerrar_sessao=lambda: None,
        ),
        "core.config": SimpleNamespace(
            settings=SimpleNamespace(validate=lambda: []),
        ),
        "core.user_context": SimpleNamespace(
            principal=lambda: dict(USUARIO_SINTETICO),
        ),
        "design.componentes": SimpleNamespace(mensagem_erro=lambda *_args: None),
        "design.tema": SimpleNamespace(aplicar_tema=lambda: None),
    }
    for chave, duble in overrides.items():
        stubs[chave.replace("_", ".", 1)] = duble
    return stubs


def instalar_stubs_de_bootstrap(monkeypatch, fake_streamlit, **overrides) -> None:
    """Injeta Streamlit falso e os dubles em `sys.modules`."""
    monkeypatch.setitem(sys.modules, "streamlit", fake_streamlit)
    for nome, duble in stubs_de_bootstrap(**overrides).items():
        monkeypatch.setitem(sys.modules, nome, duble)
