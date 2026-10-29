"""Relatório de analista na tela (29/10/2026). Dados sintéticos.

Cada card sai numa string só e balanceada (abrir a ``<div>`` num
``st.markdown`` e fechar em outro já deu moldura vazia); texto de fora é
escapado e achatado; limitação, gatilho sem dado e procedência vão para
Configurações → Restrições e para o texto da LLM, nunca para a tela (regra de
05/10/2026).
"""
import contextlib
from dataclasses import replace

import pytest

import design.relatorio_analista as tela
from core.inteligencia_ativos import fontes_fundamentos as ff
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import relatorio_analista as ra
from core.inteligencia_ativos import veredito as vd
from tests.test_inteligencia_avaliacao import (  # noqa: F401
    BOA,
    _com,
    _fund,
    _linha,
    an,
)

PROVEDOR = "brapi financialData de 2026-10-26"
REF_DIVIDA = f"dívida do exercício 2025; EBITDA de 12 meses ({PROVEDOR})"


def _fund_com_ref(**valores):
    dados = {k: f.Dado(v, "teste", "2025") for k, v in valores.items()}
    dados["divida_liquida_ebitda"] = f.Dado(
        valores.get("divida_liquida_ebitda", 0.8), "base + provedor",
        REF_DIVIDA)
    dados["cobertura_juros"] = f.Dado(8.0, "provedor", PROVEDOR)
    return f.montar(f.ACAO, dados).como_dict()


def _noticia_hostil():
    return inf.Noticia(
        headline="<script>alert(1)</script> Lucro de $2 bi\n\nsobe $5",
        date="2026-10-20", source="Agência <b>X</b>", url=None,
        impact_level=inf.HIGH, affected_dimension=(), summary=None,
        categoria="resultado", motivo="m")


def _html(rel):
    return "\n".join(tela.cards(rel))


@pytest.fixture
def rel(an):  # noqa: F811
    return ra.relatorio_dos_insumos(
        _com(an["WEGE3"], _fund_com_ref(**BOA), noticias=[_noticia_hostil()]))


def test_cada_card_e_um_bloco_balanceado(rel):
    for card in tela.cards(rel):
        assert card.startswith('<div class="app-relatorio-card"')
        assert card.count("<div") == card.count("</div>")
        assert card.count("<span") == card.count("</span>")
        assert "\n" not in card


def test_texto_de_fora_e_escapado_e_achatado(rel):
    html = _html(rel)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>X</b>" not in html
    assert "$" not in html and "&#36;2 bi" in html


def test_procedencia_nao_vai_para_a_tela_mas_vai_para_a_llm(rel):
    html = _html(rel)
    assert "brapi" not in html and "2026-10-26" not in html
    g = next(g for g in rel.gatilhos if g.indicador == "Dívida líquida/EBITDA")
    assert g.atual == "0,80x" and g.referencia == REF_DIVIDA
    txt = ra.texto(rel)
    assert f"hoje 0,80x ({REF_DIVIDA})" in txt
    # O critério da avaliação também perde só os parênteses da referência.
    assert any("brapi" in t for t in rel.tese + rel.riscos + rel.contexto)
    assert "Dívida líquida/EBITDA de 0,80x" in html


def test_referencia_que_contem_outra_sai_inteira(rel):
    # " (brapi ...)" está dentro de " (dívida do ...; EBITDA (brapi ...))":
    # tirar a curta primeiro deixaria um parêntese órfão na tela.
    assert rel.procedencias.index(f" ({REF_DIVIDA})") < \
        rel.procedencias.index(f" ({PROVEDOR})")
    assert "EBITDA de 12 meses)" not in _html(rel)


def test_limitacoes_sao_registradas_e_nao_aparecem(rel, monkeypatch):
    registrado = []
    monkeypatch.setattr(tela, "registrar_limitacoes",
                        lambda obj, **kw: registrado.append((obj, kw)))
    saida = {"md": [], "cap": []}

    class _St:
        def markdown(self, x, unsafe_allow_html=False):
            assert unsafe_allow_html
            saida["md"].append(x)

        def caption(self, x):
            saida["cap"].append(x)

        def columns(self, n):
            return [contextlib.nullcontext() for _ in range(n)]

    monkeypatch.setattr(tela, "st", _St())
    tela.render_relatorio(rel)
    assert registrado == [(rel, {"modulo": tela.MODULO, "entidade": rel.ticker})]
    assert rel.limitacoes
    html = "\n".join(saida["md"])
    for li in rel.limitacoes:
        assert li not in html
    assert "Restrições" not in html and "sem dado" not in html
    assert saida["cap"] == [rel.aviso]


def test_gatilho_sem_dado_fica_fora_da_tela(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(an["WEGE3"])
    assert any(g.disparado is None for g in rel.gatilhos)
    html = tela.card_gatilhos(rel)
    for g in rel.gatilhos:
        if g.disparado is None:
            assert f"<strong>{g.indicador}</strong>" not in html
    assert any(li.startswith("Gatilho ") for li in rel.limitacoes)


def test_disparado_vem_primeiro_com_simbolo_e_texto(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(
        _com(an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0})))
    html = tela.card_gatilhos(rel)
    assert html.index("Dívida líquida/EBITDA") < html.index("Cobertura de juros")
    assert "⊘" in html and "disparado" in html
    assert "Limite: " + vd.ROTULO_LIMITE[vd.TROCAR] in tela.card_cabecalho(rel)


@pytest.mark.parametrize("limite", sorted(tela.APARENCIA_LIMITE))
def test_limite_nunca_so_pela_cor(limite):
    icone, cor, frase = tela.APARENCIA_LIMITE[limite]
    assert icone.strip() and frase and cor.startswith("var(--app-")


def test_fontes_divergentes_e_limitacao_nao_risco(an):  # noqa: F811
    nota = (f"{ff.NOTA_FONTES_DIVERGENTES}: a razão da brapi (6,44x) implica "
            "dívida maior que a bruta")
    dados = {k: f.Dado(v, "teste", "2025") for k, v in BOA.items()
             if k != "divida_liquida_ebitda"}
    dados["divida_liquida_ebitda"] = f.Dado(None, "provedor", PROVEDOR, nota)
    rel = ra.relatorio_dos_insumos(
        _com(an["WEGE3"], f.montar(f.ACAO, dados).como_dict()))
    assert not any(ff.NOTA_FONTES_DIVERGENTES in r for r in rel.riscos)
    assert any(ff.NOTA_FONTES_DIVERGENTES in li for li in rel.limitacoes)
    assert "brapi" not in _html(rel)


def _faixa(chave, vmin, vmax):
    return ra.FaixaImplicita(chave, chave.upper(), "1", "1 a 2", "8 obs",
                             vmin, vmax, "premissa")


def test_faixas_que_nao_se_cruzam_sao_ditas(rel):
    assert ra.divergencia_das_faixas([_faixa("p_l", -10, 20),
                                      _faixa("p_vp", -5, 5)]) == ""
    frase = ra.divergencia_das_faixas([_faixa("p_l", -10, 20),
                                       _faixa("dividend_yield", 50, 120)])
    assert "não se sobrepõem" in frase and "Nenhuma faixa resume" in frase
    com = replace(rel, faixas=(_faixa("p_l", -10, 20),
                               _faixa("dividend_yield", 50, 120)),
                  leitura_faixas=frase)
    assert "não se sobrepõem" in tela.card_faixas(com)
    assert frase in ra.texto(com)
    assert "não se sobrepõem" not in tela.card_faixas(
        replace(com, leitura_faixas=""))


class _StSecao:
    """``st`` mínimo para a seção: estado de sessão, spinner e as saídas."""

    def __init__(self):
        self.session_state = {}
        self.md = []
        self.cap = []

    def spinner(self, _texto):
        return contextlib.nullcontext()

    def markdown(self, x, unsafe_allow_html=False):
        self.md.append(x)

    def caption(self, x):
        self.cap.append(x)

    def columns(self, n):
        return [contextlib.nullcontext() for _ in range(n)]


@pytest.fixture
def secao(rel, monkeypatch):
    falso = _StSecao()
    chamadas, falhas = [], []
    monkeypatch.setattr(tela, "st", falso)
    monkeypatch.setattr(tela, "registrar_limitacoes", lambda obj, **kw: None)
    monkeypatch.setattr(tela, "falha_de_acao",
                        lambda msg, exc: falhas.append((msg, exc)))
    relogio = {"agora": 1_000.0}
    monkeypatch.setattr(tela.time, "time", lambda: relogio["agora"])

    def montar(tk, nome, setor, *, mercado):
        chamadas.append((tk, nome, setor, mercado))
        if falso.session_state.get("_quebrar"):
            raise RuntimeError("senha=segredo host=db")
        return rel

    monkeypatch.setattr(tela.ra, "montar_relatorio", montar)
    return falso, chamadas, falhas, relogio


def test_secao_mostra_e_devolve_o_texto_da_llm(secao, rel):
    falso, chamadas, falhas, _ = secao
    txt = tela.secao_relatorio(" wege3 ", "WEG", "Bens Industriais",
                               mercado="b3")
    assert txt == ra.texto(rel)
    assert chamadas == [("WEGE3", "WEG", "Bens Industriais", "b3")]
    assert falso.md and falso.cap == [rel.aviso] and not falhas


def test_secao_usa_o_cache_da_sessao_ate_o_ttl(secao):
    falso, chamadas, _, relogio = secao
    tela.secao_relatorio("WEGE3", mercado="b3")
    tela.secao_relatorio("wege3", mercado="b3")
    assert len(chamadas) == 1
    tela.secao_relatorio("WEGE3", mercado="us")   # outro mercado, outra chave
    assert len(chamadas) == 2
    relogio["agora"] += tela.TTL_CACHE_S
    tela.secao_relatorio("WEGE3", mercado="b3")
    assert len(chamadas) == 3


def test_falha_e_nomeada_e_nao_fica_no_cache(secao):
    falso, chamadas, falhas, _ = secao
    falso.session_state["_quebrar"] = True
    txt = tela.secao_relatorio("WEGE3", mercado="b3")
    # Só o tipo da exceção vai para o texto da LLM; o texto dela pode
    # carregar host ou credencial.
    assert txt == ("[Relatório de analista (regras) — WEGE3]: falha ao montar "
                   "(RuntimeError).")
    assert "segredo" not in txt and len(falhas) == 1 and not falso.md
    falso.session_state["_quebrar"] = False
    tela.secao_relatorio("WEGE3", mercado="b3")
    assert len(chamadas) == 2 and falso.md
