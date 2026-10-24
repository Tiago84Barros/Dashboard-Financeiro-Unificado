from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import views.previsao_lucros_b3 as vpl

RAIZ = Path(__file__).parents[1]

_VEREDITO = {"modelo": "gbm", "veredito": "REPROVADO", "sinal": "REPROVADO",
             "mae_modelo": 0.101, "mae_ingenuo": 0.100, "n_sinal": 700,
             "dif_erro_abs": {"ic_low": -0.002, "ic_high": 0.004},
             "acerto_sinal": 0.53, "acerto_sinal_majoritario": 0.55}

ARTEFATO = {
    "versao_metodologia": "1.0.0", "config_hash": "abc123", "gerado_em": "2026-10-24",
    "holdout_execucoes": 1,
    "configuracao": {"safras_dev": [2014, 2021], "safras_holdout": [2022, 2025],
                     "safra_viva": 2026},
    "dev": {"por_safra": [{"safra": 2021, "n_previstas": 262, "n_sem_alvo": 7,
                           "gbm": {"mae": 0.13, "acerto_sinal": 0.56},
                           "ingenuo": {"mae": 0.14}}]},
    "holdout": {"veredito": _VEREDITO,
                "por_safra": [{"safra": 2022, "n_previstas": 250, "n_sem_alvo": 4,
                               "gbm": {"mae": 0.09, "acerto_sinal": 0.51},
                               "ingenuo": {"mae": 0.08}}],
                "cobertura_intervalo": {"n": 900, "cobertura": 0.79}},
    "rank_ic": {"status": "medido", "veredito": "REPROVADO", "cobertura_previsao": 0.9,
                "score": {"mean": 0.07, "ci_low": -0.01, "ci_high": 0.14},
                "previsao": {"mean": 0.01, "ci_low": -0.05, "ci_high": 0.06},
                "combinado": {"mean": 0.06, "ci_low": -0.02, "ci_high": 0.13},
                "delta": {"mean": -0.01, "ci_low": -0.04, "ci_high": 0.02},
                "ic_por_ano": {"2022": {"score": 0.1, "previsao": 0.0,
                                        "combinado": 0.08, "delta": -0.02}}},
    "previsoes_atuais": [
        {"cd_cvm": 1, "tickers": ["GRND3"], "nome": "Grendene", "setor": "Calçados",
         "lucro_base": 5e8, "lucro_previsto": 5.2e8, "y_previsto": 0.01,
         "ic_low": -0.05, "ic_high": 0.06},
        {"cd_cvm": 2, "tickers": ["SBSP3"], "nome": "Sabesp", "setor": "Saneamento",
         "lucro_base": 3e9, "lucro_previsto": 2.9e9, "y_previsto": -0.003,
         "ic_low": -0.04, "ic_high": 0.03},
    ],
    "limitacoes": ["Valores da DFP sao os da ULTIMA versao."],
}

_SCRIPT = """
import time
import streamlit as st
st.session_state.setdefault("_app4_user",
    {"id": "11111111-1111-1111-1111-111111111111", "expires_at": time.time() + 600})
from views import previsao_lucros_b3
previsao_lucros_b3.render(show_header=False)
"""


@pytest.fixture
def registros(monkeypatch):
    lista = []
    monkeypatch.setattr(vpl, "detalhe_tecnico",
                        lambda msg, *, codigo, entidade=None: lista.append((codigo, msg)))
    monkeypatch.setattr(vpl, "aviso_lacuna",
                        lambda msg, *, codigo, **kw: lista.append((codigo, msg)))
    return lista


def _rodar(monkeypatch, art):
    monkeypatch.chdir(RAIZ)
    monkeypatch.setattr(vpl, "carregar", lambda: art)
    return AppTest.from_string(_SCRIPT).run(timeout=60)


def _texto(app) -> str:
    partes = [m.value for m in app.markdown] + [c.value for c in app.caption]
    return "\n".join(str(p) for p in partes)


def test_reprovado_aparece_na_tela_sem_maquiagem(monkeypatch, registros):
    app = _rodar(monkeypatch, ARTEFATO)
    assert not app.exception
    txt = _texto(app)
    assert "Veredito do holdout: REPROVADO" in txt
    assert txt.count("REPROVADO") >= 3        # erro, sinal e Rank-IC
    assert "APROVADO</div>" not in txt
    assert "não recomendação" in txt


def test_tabelas_por_safra_previsoes_e_rank_ic(monkeypatch, registros):
    app = _rodar(monkeypatch, ARTEFATO)
    assert len(app.dataframe) == 3
    safras = app.dataframe[0].value
    assert list(safras["Fase"]) == ["Desenvolvimento", "Holdout"]
    assert safras["Erro / ingênuo"].iloc[1] == pytest.approx(0.09 / 0.08)
    prev = app.dataframe[1].value
    assert list(prev["Ticker"]) == ["GRND3", "SBSP3"]   # maior variação prevista primeiro
    assert prev["Lucro previsto (R$ mi)"].iloc[0] == pytest.approx(520.0)


def test_busca_filtra_previsoes(monkeypatch, registros):
    app = _rodar(monkeypatch, ARTEFATO)
    app.text_input(key="pl_b3_busca").input("sabesp").run(timeout=60)
    assert list(app.dataframe[1].value["Ticker"]) == ["SBSP3"]


def test_detalhe_tecnico_vai_para_restricoes_nao_para_a_tela(monkeypatch, registros):
    app = _rodar(monkeypatch, ARTEFATO)
    txt = _texto(app)
    assert "abc123" not in txt and "ULTIMA versao" not in txt
    codigos = {c for c, _ in registros}
    assert "tela.b3.previsao_lucros.procedencia" in codigos
    assert "tela.b3.previsao_lucros.limitacao_0" in codigos


def test_sem_artefato_estado_vazio_e_lacuna_registrada(monkeypatch, registros):
    app = _rodar(monkeypatch, None)
    assert not app.exception
    assert "ainda não foi publicada" in _texto(app)
    assert "tela.b3.previsao_lucros.artefato_ausente" in {c for c, _ in registros}


def test_rank_ic_nao_medido_nao_inventa_numero(monkeypatch, registros):
    art = {**ARTEFATO, "rank_ic": {"status": "nao_medido", "motivo": "sem pares"}}
    app = _rodar(monkeypatch, art)
    assert not app.exception
    assert "Não medido" in _texto(app)
    assert len(app.dataframe) == 2


def test_aba_so_existe_na_vitrine_b3():
    import design.market_companies as mc
    assert mc.NAV_ITEMS_B3[-1] == ("🔮", "Previsão de Lucros")
    assert ("🔮", "Previsão de Lucros") not in mc.NAV_ITEMS
    b3 = (RAIZ / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    usa = (RAIZ / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    assert "itens=NAV_ITEMS_B3" in b3 and "previsao_lucros_b3.render" in b3
    assert "NAV_ITEMS_B3" not in usa


def test_trilho_b3_desenha_seis_abas():
    app = AppTest.from_string("""
from design.market_companies import NAV_ITEMS_B3, render_market_tabs
render_market_tabs(state_key='t_active', key_prefix='t', itens=NAV_ITEMS_B3)
""").run(timeout=60)
    assert not app.exception
    assert [b.label for b in app.button][-1] == "🔮 Previsão de Lucros"
    assert len(app.button) == 6


def test_artefato_publicado_e_legivel_pelo_nucleo():
    p = RAIZ / vpl.pl.ARTEFATO
    if not p.exists():
        pytest.skip("artefato ainda não publicado")
    art = vpl.pl.carregar_artefato(str(p))
    assert art["holdout"]["veredito"]["veredito"] in {"APROVADO", "REPROVADO"}
    assert art["holdout_execucoes"] >= 1
