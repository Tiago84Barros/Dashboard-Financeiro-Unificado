"""Rigor da análise de empresas americanas: procedência, confiança e câmbio.

Os três defeitos que estes testes travam são do mesmo tipo — o motor já
produzia a informação e o relatório não a consultava:

* macro literal no código exibido e enviado à LLM como observação de mercado;
* ``score_status``/``critical_missing`` calculados e ignorados, deixando um
  valuation ser concluído sobre cobertura de triagem;
* carteira em dólares avaliada por investidor em reais sem citar o câmbio.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import pandas as pd
import pytest

import core.portfolio_report_us as rel
import core.us_macro as um
from data_pipeline.us import macro as ing

_RAIZ = Path(__file__).resolve().parents[1]
_FONTE_BRUTA = (_RAIZ / "views" / "analise_portfolio_us.py").read_text(encoding="utf-8")

# Emenda literais adjacentes ("...de " \n "mercado") antes de procurar frases:
# o texto que o usuário lê não tem as quebras que o código-fonte tem, e um
# teste que falha por causa de onde a linha foi cortada não testa nada útil.
_FONTE_VIEW = re.sub(r'"\s*\n\s*"', "", re.sub(r"'\s*\n\s*'", "", _FONTE_BRUTA))


# ── Macro: premissa nunca vira fato ──────────────────────────────────────────

def test_snapshot_nasce_como_premissa():
    assert um.USMacroSnapshot().fonte == um.FONTE_PREMISSA
    assert um.evaluate_macro(um.USMacroSnapshot())["observado"] is False


def test_procedencia_atravessa_o_evaluate():
    saida = um.evaluate_macro(um.USMacroSnapshot(
        fonte=um.FONTE_OBSERVADO, as_of="2026-06-30",
    ))
    assert saida["observado"] is True
    assert saida["as_of"] == "2026-06-30"
    # A procedência não pode virar indicador numérico do regime.
    assert "fonte" not in saida["inputs"]
    assert set(saida["inputs"]) == set(um._CAMPOS_NUMERICOS)


def test_prompt_proibe_afirmar_premissa_como_fato():
    texto = rel.format_us_macro(um.evaluate_macro(um.USMacroSnapshot()))
    assert "PREMISSA DE SIMULAÇÃO" in texto
    assert "É PROIBIDO afirmá-los como fato" in texto
    assert "sob a premissa de" in texto


def test_macro_observado_pode_ser_afirmado():
    texto = rel.format_us_macro(um.evaluate_macro(um.USMacroSnapshot(
        fonte=um.FONTE_OBSERVADO, as_of="2026-06-30")))
    assert "séries oficiais (FRED)" in texto
    assert "data-base 2026-06-30" in texto
    assert "PREMISSA DE SIMULAÇÃO" not in texto


def test_tela_avisa_quando_o_macro_e_premissa():
    assert "premissa de simulação, não leitura de mercado" in _FONTE_VIEW
    assert "run_us_ingest.py macro" in _FONTE_VIEW


def test_alterar_controle_rebaixa_observado_para_premissa():
    """Mexer no número transforma leitura em cenário — o rótulo tem de seguir."""
    assert "intacto = all(" in _FONTE_VIEW
    assert "FONTE_OBSERVADO if (tem_observado and intacto) else FONTE_PREMISSA" in _FONTE_VIEW


def test_a_secao_inteira_parte_da_mesma_fonte_de_macro():
    """Duas abas da mesma seção não podem exibir taxas do Fed diferentes."""
    vitrine = (_RAIZ / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    corpo = vitrine[vitrine.index("def _macro_controls"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "us.macro_observado()" in corpo
    assert "FONTE_OBSERVADO if (observado and intacto) else FONTE_PREMISSA" in corpo
    # E nenhuma das duas telas ancora em literal quando há série observada.
    assert "4.25, 0.25" not in corpo


# ── Ingestão FRED: transformações puras ──────────────────────────────────────

def test_parse_fred_descarta_ausentes():
    csv = "DATE,FEDFUNDS\n2026-01-01,4.33\n2026-02-01,.\n2026-03-01,4.25\n"
    assert ing.parse_fred_csv(csv) == [
        (_dt.date(2026, 1, 1), 4.33), (_dt.date(2026, 3, 1), 4.25),
    ]


def test_parse_fred_tolera_lixo_e_cabecalho_ausente():
    assert ing.parse_fred_csv("") == []
    assert ing.parse_fred_csv("DATE,X\nnao-e-data,1.0\n2026-01-01,abc\n") == []


def test_yoy_casa_por_data_e_nao_por_posicao():
    """Série com buraco produziria janela de 11 ou 13 meses sem avisar."""
    serie = [(_dt.date(2025, 1, 1), 100.0),
             (_dt.date(2025, 6, 1), 105.0),   # buraco depois deste ponto
             (_dt.date(2026, 1, 1), 110.0)]
    saida = ing.yoy_from_index(serie)
    assert len(saida) == 1
    assert saida[0][0] == _dt.date(2026, 1, 1)
    assert saida[0][1] == pytest.approx(10.0)


def test_snapshot_carimba_a_data_mais_defasada():
    """O regime só vale até onde a série mais antiga alcança."""
    snapshot = ing.montar_snapshot({
        "fed_funds": (_dt.date(2026, 7, 1), 4.25),
        "real_gdp_yoy": (_dt.date(2026, 3, 1), 2.0),
    })
    assert snapshot["as_of"] == "2026-03-01"
    assert snapshot["fonte"] == um.FONTE_OBSERVADO


def test_snapshot_vazio_nao_inventa_procedencia():
    assert ing.montar_snapshot({}) == {}


# ── Grau de confiança ────────────────────────────────────────────────────────

def test_grau_de_confianca_le_a_linha_do_score():
    linha = pd.Series({"score_status": "screen_grade", "score_confidence": 0.31})
    status, rotulo, confianca = rel.grau_de_confianca(linha)
    assert status == "screen_grade"
    assert "triagem" in rotulo
    assert confianca == 0.31


def test_empresa_sem_pontuacao_cai_para_triagem():
    status, _rotulo, confianca = rel.grau_de_confianca(None)
    assert status == "screen_grade"
    assert confianca is None


def test_bloco_de_confianca_soma_o_peso_fragil():
    texto = rel.build_confidence_context([
        {"ticker": "AAA", "peso_pct": 60.0, "score_status": "decision_grade",
         "score_confidence": 0.9},
        {"ticker": "BBB", "peso_pct": 25.0, "score_status": "screen_grade",
         "score_confidence": 0.3, "critical_missing": ["valuation"]},
        {"ticker": "CCC", "peso_pct": 15.0, "score_status": "research_grade",
         "score_confidence": 0.6},
    ])
    assert "40.0% do peso da carteira está abaixo de grau de decisão" in texto
    assert "trilhas sem cobertura mínima: valuation" in texto
    assert "NÃO sustenta conclusão de valuation" in texto


def test_prompt_individual_manda_rebaixar_conclusao_sem_cobertura():
    texto = rel.build_company_provenance(
        None, pd.Series({"score_status": "screen_grade", "score_confidence": 0.2}),
    )
    assert "cobertura insuficiente" in texto
    assert "Cobertura baixa não é empresa ruim" in texto
    assert "9b." in rel._PROMPT_COMPANY_PORTFOLIO


def test_grau_de_decisao_nao_gera_ressalva():
    texto = rel.build_company_provenance(
        None, pd.Series({"score_status": "decision_grade", "score_confidence": 0.95}),
    )
    assert "ATENÇÃO" not in texto
    assert "decisão (cobertura alta)" in texto


def test_expander_sinaliza_cobertura_antes_de_abrir():
    assert "score_status" in _FONTE_VIEW
    assert "só triagem" in _FONTE_VIEW
    assert "cobertura parcial" in _FONTE_VIEW


# ── Procedência dos dados ────────────────────────────────────────────────────

def test_procedencia_declara_modo_e_exercicio():
    texto = rel.build_data_provenance_context(
        {"mode": "snapshot", "last_update": "2026-08-03", "companies": 512},
        {"AAPL": pd.DataFrame({"fiscal_year": [2022, 2023, 2024]}),
         "MSFT": pd.DataFrame({"fiscal_year": [2021, 2022, 2023]})},
    )
    assert "vitrine publicada" in texto
    assert "2026-08-03" in texto
    assert "512 empresas" in texto
    assert "FY2024" in texto and "FY2023" in texto   # mais recente e mais antigo


def test_procedencia_sem_data_declara_frescor_desconhecido():
    texto = rel.build_data_provenance_context({"mode": "warehouse"}, {})
    assert "frescor desconhecido" in texto


def test_prompt_consolidado_recebe_os_tres_blocos_novos():
    for slot in ("{provenance}", "{confidence}", "{fx_context}"):
        assert slot in rel._PROMPT_PORTFOLIO, slot
    assert "{provenance}" in rel._PROMPT_COMPANY_PORTFOLIO


def test_tela_mostra_a_base_ao_usuario():
    assert "última ingestão" in _FONTE_VIEW
    assert "data de ingestão não informada" in _FONTE_VIEW


# ── Exposição cambial ────────────────────────────────────────────────────────

def test_bloco_cambial_explica_a_composicao_do_retorno():
    texto = rel.build_fx_context(5.42)
    assert "patrimônio em reais" in texto
    assert "variação do USD/BRL" in texto
    assert "R$ 5.42" in texto
    assert "não a apresente só como proteção nem só como risco" in texto.lower()


def test_sem_cotacao_o_relatorio_nao_estima_o_cambio():
    texto = rel.build_fx_context(None)
    assert "não estime a taxa" in texto


def test_tela_declara_a_exposicao_cambial():
    assert "Carteira em dólares" in _FONTE_VIEW
    assert "segundo ativo embutido" in _FONTE_VIEW


def test_cotacao_vem_do_banco_e_nao_de_constante():
    assert "_usd_brl_da_base" in _FONTE_VIEW
    assert "asset_quotes" in _FONTE_VIEW


# ── Renderização real ────────────────────────────────────────────────────────

_CARTEIRA = """
import pandas as pd
import views.analise_portfolio_us as v

v.load_active_us_portfolio_model = lambda: {
    "name": "Portfolio EUA Modelo 2026", "ano_compra": 2026, "is_stale": False,
    "metrics_json": {"entry_score": 71.5},
    "items": [
        {"ticker": "AAPL", "symbol": "AAPL", "nome": "Apple", "setor": "Technology",
         "industria": "Consumer Electronics", "weight": 1.0, "entry_score": 74.0},
    ],
}
v.us.scored_universe = lambda *a, **k: pd.DataFrame([
    {"symbol": "AAPL", "name": "Apple", "sector": "Technology",
     "industry": "Consumer Electronics", "score": 74.0},
])
v.us.data_status = lambda: {"mode": "snapshot", "last_update": "2026-08-03",
                            "companies": 512}
v.llm_disponivel = lambda: False
v._usd_brl_da_base = lambda: 5.42
"""


def _roda(macro_observado: str) -> object:
    from streamlit.testing.v1 import AppTest

    return AppTest.from_string(
        _CARTEIRA + f"\nv.us.macro_observado = lambda: {macro_observado}\n"
        "v.render(show_header=False)\n"
    ).run(timeout=60)


def test_tela_alerta_quando_o_macro_e_so_premissa():
    app = _roda("{}")
    assert not app.exception
    avisos = "\n".join(w.value for w in app.warning)
    assert "premissa de simulação" in avisos
    assert "run_us_ingest.py macro" in avisos


def test_tela_nao_alerta_quando_ha_serie_observada():
    app = _roda(
        "{'fed_funds': 4.5, 'cpi_yoy': 2.1, 'real_gdp_yoy': 1.8, "
        "'unemployment': 4.0, 'yield_curve_10y_2y': 0.4, "
        "'high_yield_spread': 3.2, 'as_of': '2026-06-30'}"
    )
    assert not app.exception
    avisos = "\n".join(w.value for w in app.warning)
    assert "premissa de simulação" not in avisos
    markdown = "\n".join(str(m.value) for m in app.markdown)
    assert "observado" in markdown and "2026-06-30" in markdown


def test_tela_mostra_procedencia_da_base():
    app = _roda("{}")
    markdown = "\n".join(str(m.value) for m in app.markdown)
    assert "vitrine publicada" in markdown
    assert "2026-08-03" in markdown
    assert "512 empresas" in markdown
    assert "R$ 5.42" in markdown
