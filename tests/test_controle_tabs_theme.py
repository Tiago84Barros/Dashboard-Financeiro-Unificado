"""Contrato visual da sub-navegação de seções (design.componentes.abas_secao).

A regra é: as abas do Controle Financeiro têm de ser indistinguíveis das abas
nativas usadas em Investimentos (st.tabs). O CSS vive uma vez só, casando pelo
prefixo de key, e não por tela — daí os seletores genéricos.
"""
import inspect

from design.componentes import NAV_KEY_PREFIX, abas_secao
from design.tema import _CSS

_ESCOPO = '[class*="st-key-appnav_"]'


def test_subnavegacao_usa_seletor_generico_por_prefixo_de_key():
    assert NAV_KEY_PREFIX == "appnav_"
    assert f'{_ESCOPO} [data-testid="stButtonGroup"]' in _CSS
    assert f'{_ESCOPO} [data-baseweb="button-group"]' in _CSS


def test_subnavegacao_replica_metricas_das_abas_nativas():
    """Mesmo tamanho, espaçamento, tipografia e cor das abas de Investimentos."""
    nativo = _CSS[_CSS.index('.stTabs [data-baseweb="tab"] {'):]
    nativo = nativo[:nativo.index("}")]
    for regra in ("min-height: 42px;", "padding: .62rem .9rem;",
                  "border-radius: 9px 9px 0 0;", "font-size: .78rem;"):
        assert regra in nativo, f"a aba nativa mudou: {regra}"

    botao = _CSS[_CSS.index(f'{_ESCOPO} [data-baseweb="button-group"] > button {{'):]
    botao = botao[:botao.index("}")]
    assert "min-height: 42px;" in botao
    assert "padding: .62rem .9rem !important;" in botao
    assert "border-radius: 9px 9px 0 0 !important;" in botao
    assert "font-size: .78rem;" in botao
    assert "font-weight: 650 !important;" in botao
    assert "color: var(--app-muted) !important;" in botao


def test_subnavegacao_marca_a_secao_ativa_como_a_aba_nativa():
    ativo = _CSS[_CSS.index(
        f'{_ESCOPO} [data-baseweb="button-group"] '
        '> [data-testid="stBaseButton-segmented_controlActive"] {'
    ):]
    ativo = ativo[:ativo.index("}")]
    assert "color: var(--app-primary) !important;" in ativo
    assert "background: rgba(0, 200, 150, .055) !important;" in ativo
    assert "inset 0 -2px 0 var(--app-primary)" in ativo


def test_subnavegacao_e_responsiva_e_respeita_reducao_de_movimento():
    assert "@media (max-width: 760px)" in _CSS
    assert "@media (prefers-reduced-motion: reduce)" in _CSS
    assert f'{_ESCOPO} [data-baseweb="button-group"] > button {{\n        transition: none;' in _CSS


def test_subnavegacao_mantem_foco_de_teclado_visivel():
    foco = _CSS[_CSS.index(
        f'{_ESCOPO} [data-baseweb="button-group"] > button:focus-visible {{'
    ):]
    foco = foco[:foco.index("}")]
    assert "outline: 2px solid #4A9EFF !important;" in foco


def test_abas_secao_rola_ao_topo_por_padrao():
    assinatura = inspect.signature(abas_secao)
    assert assinatura.parameters["rolar_ao_trocar"].default is True
