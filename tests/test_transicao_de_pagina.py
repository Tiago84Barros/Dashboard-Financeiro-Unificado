"""Transição entre páginas: sem fantasma do render anterior e sempre no topo.

Os dois defeitos foram medidos no DOM em 29/09/2026, num app de reprodução com
uma página lenta (6 s):

* fantasma: 120 elementos do run anterior seguiam montados com
  ``data-stale="true"`` e visíveis debaixo do título da página nova, até o run
  terminar -- e o tema claro ainda subia a opacidade deles de ``.33`` para
  ``.88``;
* rolagem: ``scrollTop`` valia 900 antes, durante e depois da troca de seção, e
  2389 antes e depois de trocar de aba (``st.tabs`` troca no cliente, sem rerun).
"""
import ast
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _fonte(caminho: str) -> str:
    return (RAIZ / caminho).read_text(encoding="utf-8")


def _script_da_transicao() -> str:
    from design import componentes

    return componentes._JS_TRANSICAO.replace(
        "__CLASSE__", componentes.CLASSE_TRANSICAO
    )


# ── CSS ──────────────────────────────────────────────────────────────────────

def test_css_oculta_o_render_anterior_durante_a_transicao():
    from design.componentes import CLASSE_TRANSICAO
    from design.tema import _CSS

    css = "".join(_CSS.split())
    alvo = (
        f"body.{CLASSE_TRANSICAO}"
        '[data-testid="stMain"][data-stale="true"]{display:none!important;}'
    )
    assert alvo in css, "falta a regra que esconde o render anterior na transição"


def test_regra_da_transicao_vence_a_opacidade_do_tema_claro():
    """O claro deixa o stale a ``.88``; a transição tem de ganhar dele."""
    from design.componentes import CLASSE_TRANSICAO
    from design.tema import _CSS
    from design.theme_light import LIGHT_CSS

    assert "opacity:.88" in " ".join(LIGHT_CSS.split()).replace(" ", "")
    # `display:none` não compete com `opacity`: quem some não tem opacidade.
    alvo = f"body.{CLASSE_TRANSICAO}"
    assert alvo in _CSS and "!important" in _CSS.split(alvo, 1)[1][:400]


# ── Script ───────────────────────────────────────────────────────────────────

def test_script_leva_a_pagina_ao_topo_e_marca_o_corpo():
    from design.componentes import CLASSE_TRANSICAO

    js = _script_da_transicao()
    assert f"'{CLASSE_TRANSICAO}'" in js or f'"{CLASSE_TRANSICAO}"' in js
    assert "classList.add" in js and "scrollTo" in js


def test_script_desmarca_o_corpo_quando_o_run_termina():
    """Sem isto a classe fica presa e a página nova nunca aparece."""
    js = _script_da_transicao()
    assert "classList.remove" in js
    assert 'data-stale="true"' in js, "o vigia precisa contar os elementos velhos"
    assert "TETO_MS" in js, "falta o teto de segurança que solta a classe"


def test_script_rola_ao_trocar_de_aba_mesmo_sem_rerun():
    """``st.tabs`` troca no cliente: só um ouvinte de clique alcança isso."""
    js = _script_da_transicao()
    assert "addEventListener('click'" in js
    assert '[role="tab"]' in js


def test_ouvinte_nao_usa_instanceof_entre_realms():
    """O nó clicado vem do documento de cima; ``Element`` é o do iframe.

    Com ``ev.target instanceof Element`` o teste dá falso e o ouvinte sai sem
    fazer nada -- foi assim que o clique na aba deixou de rolar a página, medido
    no DOM em 29/09/2026.
    """
    js = _script_da_transicao()
    assert "typeof ev.target.closest === 'function'" in js
    codigo = chr(10).join(
        linha for linha in js.splitlines() if "/*" not in linha
    )
    assert "instanceof" not in codigo, "o comentário pode citar; o código não"


def test_script_reassume_transicao_em_curso_de_outro_run():
    """O iframe recarrega a cada run; o vigia do run anterior morre com ele."""
    js = _script_da_transicao()
    depois = js.split("__ATIVA__", 1)[1]
    assert "classList.contains" in depois and "vigiar()" in depois


def test_marca_muda_a_cada_run_para_o_iframe_recarregar(monkeypatch):
    import streamlit as st

    from design import componentes

    enviados: list[str] = []
    monkeypatch.setattr(
        componentes.components, "html",
        lambda html, **kw: enviados.append(html),
    )
    st.session_state.clear()
    componentes.transicao_de_pagina("📊 Dashboard Geral")
    componentes.transicao_de_pagina("📊 Dashboard Geral")
    assert len(enviados) == 2
    assert enviados[0] != enviados[1], "iframe com o mesmo HTML não recarrega"


def test_transicao_ativa_so_quando_a_rota_muda(monkeypatch):
    import streamlit as st

    from design import componentes

    enviados: list[str] = []
    monkeypatch.setattr(
        componentes.components, "html",
        lambda html, **kw: enviados.append(html),
    )
    st.session_state.clear()
    componentes.transicao_de_pagina("📊 Dashboard Geral")
    componentes.transicao_de_pagina("📊 Dashboard Geral")
    componentes.transicao_de_pagina("💰 Controle Financeiro")

    def ativa(html: str) -> bool:
        return re.search(r"if \(true\)", html) is not None

    assert ativa(enviados[0]), "primeira entrada na rota é uma transição"
    assert not ativa(enviados[1]), "rerun na mesma rota não pode esconder a tela"
    assert ativa(enviados[2]), "troca de rota é transição"


def test_rolar_para_topo_tambem_esconde_o_fantasma(monkeypatch):
    """Quem já pedia o topo (abas_secao, FIIs, Empresas) ganha o resto."""
    from design import componentes

    enviados: list[str] = []
    monkeypatch.setattr(
        componentes.components, "html",
        lambda html, **kw: enviados.append(html),
    )
    componentes.rolar_para_topo()
    assert len(enviados) == 1
    assert componentes.CLASSE_TRANSICAO in enviados[0]
    assert "if (true)" in enviados[0]


# ── Roteador ─────────────────────────────────────────────────────────────────

def test_app_instala_a_transicao_antes_de_renderizar_a_rota():
    fonte = _fonte("app.py")
    arvore = ast.parse(fonte)
    chamada = fonte.index("transicao_de_pagina(")
    render = fonte.index("modulo.render()")
    assert chamada < render, "a transição tem de ser entregue antes do conteúdo"
    assert "from design.componentes import" in fonte
    # Posição fixa: um elemento que só existe em alguns runs desloca o
    # `st.tabs` das views e o Streamlit devolve a seleção para a primeira aba.
    guardas = {
        ast.unparse(no.test)
        for no in ast.walk(arvore)
        if isinstance(no, ast.If)
        and no.body
        and "transicao_de_pagina" in ast.dump(no.body[0])
    }
    # A única condição aceitável é o modo sintético: ele vale para a sessão
    # inteira, então a posição do elemento não muda de um run para o outro.
    assert guardas <= {"not _APP_TEST_MODE"}, guardas
