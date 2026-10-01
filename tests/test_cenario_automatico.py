"""Cenário lido dos dados (core/cenario/automatico.py): puro, sem banco."""
from __future__ import annotations

import datetime as dt

import pytest

from core.cenario import automatico as au
from core.cenario import modelo as cen
from core.inteligencia_ativos import resumida as rs
from views import inteligencia_ativos_resumida as tela

HOJE = dt.date(2026, 9, 30)


def _obs(pais, codigo, data, valor, previsao=False):
    return {"country_code": pais, "provider_code": codigo,
            "reference_period": data, "value": valor, "is_forecast": previsao}


def _insumos(**kw) -> au.Insumos:
    base = dict(
        observacoes=(
            _obs("BRA", "NY.GDP.MKTP.KD.ZG", dt.date(2025, 1, 1), 2.1),
            _obs("BRA", "GC.DOD.TOTL.GD.ZS", dt.date(2024, 1, 1), 80.0),
            _obs("BRA", "GC.DOD.TOTL.GD.ZS", dt.date(2023, 1, 1), 76.0),
            _obs("US", "FEDFUNDS", dt.date(2026, 8, 1), 4.0),
            _obs("US", "FEDFUNDS", dt.date(2026, 1, 1), 4.5),
            _obs("US", "DGS10", dt.date(2026, 9, 26), 4.1),
            _obs("US", "BAMLH0A0HYM2", dt.date(2026, 9, 26), 3.0),
            _obs("US", "BAMLH0A0HYM2", dt.date(2026, 6, 1), 3.6),
            _obs("US", "DGS10", dt.date(2027, 1, 1), 9.9, previsao=True),
        ),
        macro_anual={2026: {"selic": 0.15, "ipca": 3.6, "icc": 88.0,
                            "icc_delta": -2.0},
                     2025: {"selic": 0.1375, "ipca": 4.8},
                     2024: {"selic": 0.1225, "ipca": 4.0}},
        usdbrl=((dt.date(2026, 9, 29), 5.40), (dt.date(2026, 7, 15), 5.50),
                (dt.date(2026, 6, 20), 5.00)),
    )
    base.update(kw)
    return au.Insumos(**base)


def test_de_dados_monta_os_doze_itens_com_origem_dados():
    c = au.de_dados(_insumos(), hoje=HOJE)
    assert c.origem == cen.DADOS and set(c.itens) == set(cen.CHAVES)
    juros = c.item("interest_rate")
    assert juros.current_value.startswith("Selic 15,00% a.a.")
    assert juros.expected_direction == "alta"           # 13,75 → 15
    assert juros.confidence == "alta" and juros.last_updated == HOJE.isoformat()
    infl = c.item("inflation")
    assert "acumulado em 2026" in infl.current_value
    assert infl.expected_direction == "alta"            # 4,0 → 4,8 (fechados)
    fx = c.item("fx")
    assert "R$ 5,4000" in fx.current_value and fx.expected_direction == "alta"
    assert c.item("fiscal_policy").expected_direction == "alta"   # 76 → 80
    assert c.item("economic_activity").expected_direction == "queda"  # ICC -2
    glob = c.item("global_economy")
    assert glob.expected_direction == "queda" and "9,9" not in glob.current_value
    cap = c.item("capital_markets")
    assert cap.expected_direction == "queda"
    assert "apetite a risco" in cap.current_value


def test_sem_curva_os_itens_dela_saem_ausentes_e_nomeados():
    c = au.de_dados(_insumos(falhas={"curva": "curva do Tesouro ilegível (X)"}),
                    hoje=HOJE)
    for k in ("interest_rate_outlook", "inflation_outlook", "credit"):
        it = c.item(k)
        assert not it.preenchido and it.source == "curva do Tesouro ilegível (X)"
    for k in ("commodities", "geopolitical_risk"):
        assert c.item(k).source == au.SEM_SERIE


def test_sem_insumo_nenhum_tudo_ausente_e_nada_inventado():
    c = au.de_dados(au.Insumos(), hoje=HOJE)
    assert c.vazio and c.origem == cen.DADOS
    assert all(c.item(k).source for k in cen.CHAVES)


def test_cambio_cai_para_o_anual_sem_cotacao_diaria():
    ins = _insumos(usdbrl=(), macro_anual={2025: {"cambio": 5.5},
                                           2024: {"cambio": 5.0}})
    fx = au.de_dados(ins, hoje=HOJE).item("fx")
    assert "(2025)" in fx.current_value and fx.expected_direction == "alta"


def test_carregar_sem_banco_devolve_vazio_e_guarda_no_cache():
    c1 = au.carregar(hoje=HOJE)          # conftest: ler_insumos → Insumos()
    assert c1.vazio and c1.origem == cen.DADOS
    assert au.carregar(hoje=HOJE) is c1


def test_texto_para_llm_e_contexto_de_cenario_lido_dos_dados():
    c = au.de_dados(_insumos(), hoje=HOJE)
    txt = cen.texto_para_llm(c, hoje=HOJE)
    assert "lido dos dados pelo código" in txt
    assert "premissa do usuário" not in txt and "Selic 15,00%" in txt
    ctx = cen.para_contexto(c, hoje=HOJE)
    assert ctx["natureza"].startswith("lido dos dados")
    assert cen.ROTULO["commodities"] in ctx["sem_dado"]
    vazio = cen.texto_para_llm(au.de_dados(au.Insumos(), hoje=HOJE), hoje=HOJE)
    assert "não puderam ser lidas" in vazio and "Não presuma" in vazio


def test_revisar_nao_aceita_origem_dados():
    with pytest.raises(ValueError):
        cen.revisar(cen.Cenario(), {}, origem=cen.DADOS, hoje=HOJE,
                    agora=dt.datetime(2026, 9, 30))


def test_cenario_atual_e_cartao_mostram_valor_fonte_e_ausencia():
    linhas = rs.cenario_atual(au.de_dados(_insumos(), hoje=HOJE))
    assert len(linhas) == len(cen.CHAVES)
    selic = linhas[0]
    assert selic.valor.startswith("Selic") and selic.referencia == "30/09/2026"
    html = tela.cartao_cenario(linhas)
    assert "Selic 15,00%" in html and "Sem dado" in html
    assert "#" not in html.replace("&#", "")
    assert rs.cenario_atual(None) == ()
    assert "não puderam ser lidas" in tela.cartao_cenario(())
