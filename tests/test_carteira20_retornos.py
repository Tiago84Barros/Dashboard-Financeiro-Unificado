import numpy as np
import pandas as pd
import pytest

import core.carteira20_retornos as cr

DIAS = [d.date() for d in pd.bdate_range("2026-10-01", periods=12)]
CORTE = DIAS[5]


def _linhas(ticker, closes, *, specs=None, bdi="02", fator=1):
    specs = specs or {}
    return [{"ticker": ticker, "trade_date": d.isoformat(), "bdi": bdi,
             "specification": specs.get(i, "ON      NM"), "close": c, "fator_cotacao": fator}
            for i, (d, c) in enumerate(zip(DIAS, closes)) if c is not None]


def _proventos(linhas):
    return pd.DataFrame(linhas, columns=["ticker", "ex_date", "payment_date", "amount", "type"])


def _calcular(linhas, proventos=None, ipt=None, depois_de=CORTE):
    ipt = ipt or {t: t[:4] for t in {x["ticker"] for x in linhas}}
    pregoes = cr.pregoes_de_acoes(linhas, set(ipt))
    return cr.calcular(pregoes, proventos if proventos is not None else _proventos([]),
                       ipt, depois_de)


def _dia(r, i, ativo="AAAA"):
    return r[(r["id"] == ativo) & (r["data"] == DIAS[i])].iloc[0]


def test_letras_da_marcacao_ex():
    assert cr.letras_marca("ON  EDJ NM") == "DJ"
    assert cr.letras_marca("ON *ED") == "D"
    assert cr.letras_marca("ON      NM") == ""
    assert cr.letras_marca(None) == ""


def test_so_devolve_depois_do_corte_e_com_pregao_anterior():
    r = _calcular(_linhas("AAAA3", [10.0] * 12))
    assert list(r.columns) == ["id", "data", "ret", "suspeito"]
    assert sorted(r["data"]) == DIAS[6:]
    assert (r["ret"] == 0).all() and not r["suspeito"].any()


def test_lote_padrao_vence_o_regime_especial_no_mesmo_dia():
    linhas = _linhas("AAAA3", [10.0] * 12) + _linhas("AAAA3", [99.0] * 12, bdi="06")
    p = cr.pregoes_de_acoes(linhas, {"AAAA3"})
    assert len(p) == 12 and (p["close_unitario"] == 10.0).all()


def test_fator_de_cotacao_vira_preco_por_acao():
    p = cr.pregoes_de_acoes(_linhas("AAAA3", [1000.0] * 12, fator=1000), {"AAAA3"})
    assert (p["close_unitario"] == 1.0).all()


def test_dividendo_entra_no_primeiro_pregao_depois_da_data_com():
    closes = [10.0] * 7 + [9.5] * 5
    prov = _proventos([("AAAA3", DIAS[6], DIAS[9], 0.5, "DIVIDENDO")])
    r = _calcular(_linhas("AAAA3", closes, specs={7: "ON  ED NM"}), prov)
    assert _dia(r, 7)["ret"] == pytest.approx(0.0)
    assert not r["suspeito"].any()


def test_mesmo_provento_sob_o_codigo_antigo_e_o_novo_conta_uma_vez():
    closes = [10.0] * 7 + [9.5] * 5
    prov = _proventos([("OLDA3", DIAS[6], DIAS[9], 0.5, "Dividendo"),
                       ("AAAA3", DIAS[6], DIAS[9], 0.5, "DIVIDENDO")])
    r = _calcular(_linhas("AAAA3", closes, specs={7: "ON  ED NM"}), prov,
                  ipt={"AAAA3": "AAAA", "OLDA3": "AAAA"})
    assert _dia(r, 7)["ret"] == pytest.approx(0.0)


def test_provento_so_sob_o_codigo_antigo_da_cadeia():
    closes = [10.0] * 7 + [9.5] * 5
    prov = _proventos([("OLDA3", DIAS[6], DIAS[9], 0.5, "JCP")])
    r = _calcular(_linhas("AAAA3", closes, specs={7: "ON  EJ NM"}), prov,
                  ipt={"AAAA3": "AAAA", "OLDA3": "AAAA"})
    assert _dia(r, 7)["ret"] == pytest.approx(0.0)


def test_desdobramento_nao_vira_queda():
    closes = [20.0] * 7 + [10.0] * 5
    r = _calcular(_linhas("AAAA3", closes, specs={7: "ON  EB NM"}))
    assert _dia(r, 7)["ret"] == pytest.approx(0.0)
    assert not _dia(r, 7)["suspeito"]


def test_grupamento_nao_vira_alta():
    closes = [1.0] * 7 + [10.0] * 5
    r = _calcular(_linhas("AAAA3", closes, specs={7: "ON  EG NM"}))
    assert _dia(r, 7)["ret"] == pytest.approx(0.0)


def test_estimar_fator():
    assert cr.estimar_fator("B", 2.0, 2.0) == (2.0, "marca_b3")
    assert cr.estimar_fator("G", 0.1, 0.1) == (0.1, "marca_b3")
    assert cr.estimar_fator("B", 1.005, 1.005) == (1.0, "marca_sem_efeito")
    # bonificações de 10%, 12% e 12,5% ficam todas a menos de 0,10 do salto
    assert cr.estimar_fator("B", 1.10, 1.10)[1] == "marca_b3_ambigua"
    assert cr.estimar_fator("B", 0.0, 1.0) == (1.0, "sem_preco")


def test_bonificacao_ambigua_sai_suspeita():
    closes = [11.0] * 7 + [10.0] * 5
    r = _calcular(_linhas("AAAA3", closes, specs={7: "ON  EB NM"}))
    assert _dia(r, 7)["suspeito"]
    assert abs(_dia(r, 7)["ret"]) < 0.02


def test_salto_sem_evento_fica_vazio_e_suspeito():
    closes = [10.0] * 7 + [100.0] * 5
    r = _calcular(_linhas("AAAA3", closes))
    assert np.isnan(_dia(r, 7)["ret"]) and _dia(r, 7)["suspeito"]
    assert not _dia(r, 8)["suspeito"]


def test_marca_de_provento_ainda_sem_valor_na_tabela_sai_suspeita():
    # a tabela cobre o ativo (provento anterior), mas não tem o da marca recente
    prov = _proventos([("AAAA3", DIAS[1], DIAS[3], 0.01, "DIVIDENDO")])
    r = _calcular(_linhas("AAAA3", [10.0] * 12, specs={8: "ON  EJ NM"}), prov)
    assert _dia(r, 8)["suspeito"] and _dia(r, 8)["ret"] == pytest.approx(0.0)


def test_fora_da_cobertura_da_tabela_o_provento_e_imputado_pela_queda():
    closes = [10.0] * 8 + [9.5] * 4
    r = _calcular(_linhas("AAAA3", closes, specs={8: "ON  ED NM"}))
    assert _dia(r, 8)["ret"] == pytest.approx(0.0)
    assert not _dia(r, 8)["suspeito"]


def test_proventos_duplicados_da_brapi_saem():
    ipt = {"AAAA3": "AAAA"}
    dv = _proventos([
        ("AAAA3", DIAS[1], DIAS[5], 0.5, "DIVIDENDO"),
        ("AAAA3", DIAS[1], DIAS[5], 0.5, "DIVIDENDO"),           # exata
        ("AAAA3", DIAS[1], DIAS[5], 0.50001, "DIVIDENDO"),       # a 0,01%
        ("AAAA3", DIAS[1], DIAS[8], 0.5, "DIVIDENDO"),           # pagamento deslocado
        ("AAAA3", DIAS[1], DIAS[5], 0.2, "BONIFICACAO"),         # não é dinheiro
        ("ZZZZ3", DIAS[1], DIAS[5], 0.5, "DIVIDENDO"),           # fora da cadeia
    ])
    x = cr.limpar_proventos(dv, ipt)
    assert len(x) == 1 and x.iloc[0]["amount"] == pytest.approx(0.5)


def test_linha_soma_de_outras_sai():
    dv = _proventos([("AAAA3", DIAS[1], DIAS[5], v, "JCP") for v in (0.1, 0.2, 0.3)])
    x = cr.limpar_proventos(dv, {"AAAA3": "AAAA"})
    assert sorted(x["amount"]) == pytest.approx([0.1, 0.2])
