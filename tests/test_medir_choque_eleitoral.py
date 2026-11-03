"""O medidor do Choque Eleitoral 2026, sem banco: só as contas e o relatório."""
from __future__ import annotations

from datetime import date

from core import stress_tests as S
from scripts import medir_choque_eleitoral as M

BASE = date(2026, 10, 23)
FIM = date(2026, 10, 30)


def test_retorno_usa_a_ultima_cotacao_ate_cada_ponta():
    serie = {date(2026, 10, 22): 90.0, BASE: 100.0, date(2026, 10, 29): 85.0,
             FIM: 82.0, date(2026, 11, 3): 70.0}
    m = M.retorno(serie, BASE, FIM)
    assert abs(m.valor - (-0.18)) < 1e-12
    assert "DEFASADA" not in m.detalhe


def test_ponta_inicial_longe_da_base_recusa_em_vez_de_usar_outro_dia():
    serie = {date(2026, 10, 10): 100.0, FIM: 80.0}
    m = M.retorno(serie, BASE, FIM)
    assert m.valor is None and "sem cotação" in m.detalhe


def test_ponta_final_velha_mede_mas_avisa():
    serie = {BASE: 100.0, date(2026, 10, 26): 90.0}
    m = M.retorno(serie, BASE, date(2026, 11, 10))
    assert abs(m.valor + 0.10) < 1e-12 and "DEFASADA" in m.detalhe


def test_sem_cotacao_depois_da_base_nao_mede():
    assert M.retorno({BASE: 100.0}, BASE, FIM).valor is None


def _linhas_tesouro():
    pu = {  # (tipo, vencimento) -> (PU na base, PU no fim)
        (M.TITULO_PRE, date(2029, 1, 1)): (800.0, 780.0),
        (M.TITULO_PRE, date(2032, 1, 1)): (500.0, 445.0),     # ~5,2 anos
        (M.TITULO_IPCA, date(2031, 8, 15)): (3000.0, 2760.0),  # ~4,8 anos
        (M.TITULO_SELIC, date(2029, 3, 1)): (17000.0, 16983.0),
    }
    return [(t, v, d, p) for (t, v), (a, b) in pu.items()
            for d, p in ((BASE, a), (FIM, b))]


def test_escolhe_o_vencimento_mais_perto_de_cinco_anos():
    venc, _ = M.escolher_titulo(_linhas_tesouro(), M.TITULO_PRE, BASE, FIM, 5.0)
    assert venc == date(2032, 1, 1)


def test_choque_do_titulo_e_escalado_para_a_duration_de_referencia():
    m = M.choque_titulo(_linhas_tesouro(), M.TITULO_PRE, BASE, FIM, 5.0, 5.0)
    prazo = M.prazo_anos(date(2032, 1, 1), BASE)
    assert abs(m.valor - (-0.11) * 5.0 / prazo) < 1e-12


def test_renda_fixa_e_a_media_de_prefixado_e_ipca():
    linhas = _linhas_tesouro()
    pre = M.choque_titulo(linhas, M.TITULO_PRE, BASE, FIM, 5.0, 5.0).valor
    ipca = M.choque_titulo(linhas, M.TITULO_IPCA, BASE, FIM, 5.0, 5.0).valor
    m = M.medir_renda_fixa(linhas, BASE, FIM, 5.0)
    assert abs(m.valor - (pre + ipca) / 2) < 1e-12


def test_renda_fixa_com_um_titulo_so_usa_o_que_existe():
    linhas = [x for x in _linhas_tesouro() if x[0] != M.TITULO_IPCA]
    m = M.medir_renda_fixa(linhas, BASE, FIM, 5.0)
    assert m.valor == M.choque_titulo(linhas, M.TITULO_PRE, BASE, FIM, 5.0, 5.0).valor
    assert "sem PU" in m.detalhe


def _medidas(**faltando):
    precos = {
        "shock_stock_br": ({BASE: 100.0, FIM: 82.0}, "warehouse"),
        "shock_fii": ({BASE: 100.0, FIM: 91.0}, "warehouse"),
        "shock_etf_intl": ({BASE: 100.0, FIM: 99.0}, "warehouse"),
        "cambio_usd_brl": ({BASE: 5.0, FIM: 5.45}, "supabase"),
    }
    for k in faltando:
        precos.pop(k)
    return M.medir(precos, (_linhas_tesouro(), "supabase"), BASE, FIM, 5.0)


def test_medir_cobre_todo_choque_do_cenario_e_fundo_rf_fica_proxy():
    med = _medidas()
    assert set(med) == set(S.ROTULO_CHOQUE) | {"cambio_usd_brl"}
    assert med["shock_fundo_rf"].valor is None
    assert abs(med["shock_tesouro"].valor - (-0.001)) < 1e-9


def test_serie_ausente_vira_motivo_nomeado():
    m = _medidas(shock_fii=True)["shock_fii"]
    assert m.valor is None and "não encontrada" in m.detalhe


def test_relatorio_compara_com_o_codigo_e_sugere_campos_validos():
    cen = next(c for c in S.SCENARIOS if c.nome == M.CENARIO_PADRAO)
    txt = M.relatorio(cen, _medidas(shock_fii=True), BASE, FIM)
    assert "shock_stock_br=-0.18," in txt
    assert "shock_fii=" not in txt.split("Para colar")[1]
    assert "choques_nao_medidos=('shock_fii', 'shock_fundo_rf')," in txt
    # O bloco sugerido tem de passar na validação do próprio cenário.
    import dataclasses
    dataclasses.replace(cen, choques_nao_medidos=("shock_fii", "shock_fundo_rf"))


def test_o_script_nao_grava_nada():
    fonte = open(M.__file__, encoding="utf-8").read().upper()
    for verbo in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE "):
        assert verbo not in fonte
