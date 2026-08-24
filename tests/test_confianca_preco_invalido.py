"""A-124 e A-125: o pilar de integridade era cego, e o índice não tinha tela.

Medido no Supabase de produção em 24/08/2026: **11 tickers com 1.406
observações de preço <= 0** — PPAR3 266 de 287 (93%), NEMO3 224 de 226 (99%),
MMAQ4 174 de 242 (72%), e SANB3/SANB4 com 112 cada, que são bancos líquidos e
não cascas deslistadas. **Zero** flags em `market.data_quality_logs` para
qualquer um deles.

O resultado: MMAQ4 aparecia com confiança **100,0 "Alta"** — a nota máxima do
painel — enquanto exibia queda máxima de −2.638% (A-122). PPAR3, 81,2 "Alta".

A-125 é o defeito que estava por trás: `core.data_confidence` ficou SEM
CONSUMIDOR quando a página "Saúde dos Dados" foi removida (a7bbe35). O índice
honesto existia, estava correto, e não chegava a tela nenhuma.
"""
import pytest

import core.data_confidence as dc

CY = 2026
_PERFEITO = {"n_key_ttm": len(dc.KEY_METRICS), "ymax": 2026,
             "dias_preco": 1, "n_flags": 0}


def test_serie_corrompida_derruba_a_integridade():
    limpo = dc.score_ticker(_PERFEITO, CY)
    sujo = dc.score_ticker({**_PERFEITO, "frac_px_invalida": 0.719}, CY)
    assert limpo["integridade"] == 100.0
    assert sujo["integridade"] == pytest.approx(28.1, abs=0.1)
    assert sujo["px_invalida_pct"] == pytest.approx(71.9, abs=0.1)


def test_rotulo_nao_diz_alta_com_integridade_em_colapso():
    """A forma exata do MMAQ4: cobertura e frescor perfeitos, série podre.

    Integridade pesa 25% do score, então 82,0 continua sendo o número certo
    pela fórmula — mas "Alta" não é a leitura certa.
    """
    r = dc.score_ticker({**_PERFEITO, "frac_px_invalida": 0.719}, CY)
    assert r["score"] == pytest.approx(82.0, abs=0.5), "o score NÃO muda"
    assert r["label"] == "Baixa", "o rótulo não pode contradizer o pilar"


def test_ticker_saudavel_continua_alta():
    r = dc.score_ticker(_PERFEITO, CY)
    assert r["label"] == "Alta"
    assert r["px_invalida_pct"] == 0.0


def test_sem_o_sinal_o_comportamento_e_o_de_antes():
    assert dc.score_ticker(_PERFEITO, CY) == dc.score_ticker(
        {**_PERFEITO, "frac_px_invalida": 0.0}, CY)


# --- A-125: a porta de entrada ----------------------------------------

def test_alerta_e_silencioso_quando_os_dados_estao_bons():
    bons = [{"ticker": "PETR4", "label": "Alta", "px_invalida_pct": 0.0},
            {"ticker": "VALE3", "label": "Alta", "px_invalida_pct": 0.0}]
    assert dc.alerta_confianca(bons) is None
    assert dc.alerta_confianca([]) is None


def test_alerta_nomeia_o_ticker_e_a_fracao_corrompida():
    msg = dc.alerta_confianca([
        {"ticker": "PETR4", "label": "Alta", "px_invalida_pct": 0.0},
        {"ticker": "SANB4", "label": "Média", "px_invalida_pct": 33.9},
        {"ticker": "MMAQ4", "label": "Baixa", "px_invalida_pct": 71.9},
    ])
    assert msg is not None
    assert "MMAQ4 (72%)" in msg and "SANB4 (34%)" in msg
    assert "PETR4" not in msg, "ticker limpo não entra no alerta"
    # o pior primeiro
    assert msg.index("MMAQ4") < msg.index("SANB4")


def test_alerta_cobre_confianca_baixa_sem_preco_invalido():
    msg = dc.alerta_confianca([
        {"ticker": "OIBR4", "label": "Baixa", "px_invalida_pct": 0.0}])
    assert msg is not None and "OIBR4" in msg and "BAIXA" in msg
