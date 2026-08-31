# -*- coding: utf-8 -*-
"""O painel deixa de descartar a empresa morta -- sem passar a inventar retorno.

Cada teste aqui trava uma direcao em que o erro nao levanta excecao: produz um
painel maior e um numero errado.
"""
from __future__ import annotations

import pandas as pd

from data_pipeline.us.scoring_history import build_annual_panel

ASOF = "2020-06-30"


def _vintages(*symbols: str) -> pd.DataFrame:
    return pd.DataFrame([{"as_of_date": ASOF, "symbol": s, "score": 50.0}
                         for s in symbols])


def _precos(symbol: str, datas: list[str], valores: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"symbol": symbol, "month_end": datas,
                         "adjusted_close": valores})


# VIVA existe para que o dataset "continue" depois da morte das outras -- sem
# ela, `fim_do_dado` seria a propria ultima cotacao e nada seria censurado.
VIVA = _precos("VIVA", ["2020-06-30", "2021-06-30", "2022-06-30"], [10.0, 11.0, 12.0])


def test_sem_saidas_o_comportamento_e_o_de_antes() -> None:
    # A correcao nao pode entrar por padrao em quem chama sem saber dela.
    p = build_annual_panel(_vintages("VIVA", "MORTA"), VIVA)
    assert set(p["symbol"]) == {"VIVA"}
    assert p.attrs["n_convencionado"] == 0


def test_morta_sem_cotacao_nenhuma_entra_pela_convencao() -> None:
    saidas = {"MORTA": {"delisted_date": "2020-11-30", "cause": "sumiu"}}
    p = build_annual_panel(_vintages("VIVA", "MORTA"), VIVA, saidas=saidas,
                           cenario="piso")
    linha = p[p["symbol"] == "MORTA"].iloc[0]
    assert linha["fwd_return"] == -1.0
    assert bool(linha["censored"]) is True
    assert p.attrs["n_convencionado"] == 1


def test_adquirida_nao_recebe_premio_mas_tambem_nao_perde() -> None:
    saidas = {"COMPRADA": {"delisted_date": "2020-11-30", "cause": "adquirida"}}
    p = build_annual_panel(_vintages("COMPRADA"), VIVA, saidas=saidas)
    assert p[p["symbol"] == "COMPRADA"].iloc[0]["fwd_return"] == 0.0


def test_causa_indefinida_continua_fora_da_conta() -> None:
    # A regra central: sem evidencia, a linha sai. Se um dia ela entrar como
    # zero ou como -100%, o painel passa a afirmar o que ninguem apurou.
    for causa in ("indefinido", None):
        saidas = {"MORTA": {"delisted_date": "2020-11-30", "cause": causa}}
        p = build_annual_panel(_vintages("VIVA", "MORTA"), VIVA, saidas=saidas)
        assert set(p["symbol"]) == {"VIVA"}, causa


def test_saida_fora_do_horizonte_nao_recebe_convencao() -> None:
    # Quem saiu em 2024 estava viva o horizonte inteiro de 2020: a falta de
    # preco ali e outro problema, e convencionar seria inventar.
    saidas = {"MORTA": {"delisted_date": "2024-01-31", "cause": "sumiu"}}
    p = build_annual_panel(_vintages("VIVA", "MORTA"), VIVA, saidas=saidas)
    assert set(p["symbol"]) == {"VIVA"}


def test_falencia_com_cotacao_nao_para_na_ultima_negociada() -> None:
    # A acao ainda negociava a caminho do zero: a ultima cotacao (-20%)
    # superestima o desfecho.
    precos = pd.concat([VIVA, _precos("QUEBROU", ["2020-06-30", "2020-09-30"],
                                      [10.0, 8.0])], ignore_index=True)
    saidas = {"QUEBROU": {"delisted_date": "2020-10-31", "cause": "sumiu"}}
    sem = build_annual_panel(_vintages("QUEBROU"), precos)
    com = build_annual_panel(_vintages("QUEBROU"), precos, saidas=saidas,
                             cenario="piso")
    assert round(float(sem.iloc[0]["fwd_return"]), 4) == -0.2
    assert float(com.iloc[0]["fwd_return"]) == -1.0


def test_aquisicao_com_cotacao_mantem_o_preco_do_negocio() -> None:
    # Aqui a ultima cotacao E o preco do negocio: substituir por 0% apagaria um
    # ganho observado.
    precos = pd.concat([VIVA, _precos("COMPRADA", ["2020-06-30", "2020-09-30"],
                                      [10.0, 13.0])], ignore_index=True)
    saidas = {"COMPRADA": {"delisted_date": "2020-10-31", "cause": "adquirida"}}
    p = build_annual_panel(_vintages("COMPRADA"), precos, saidas=saidas)
    assert round(float(p.iloc[0]["fwd_return"]), 4) == 0.3


def test_cenario_muda_o_numero_e_a_banda_e_o_resultado() -> None:
    saidas = {"MORTA": {"delisted_date": "2020-11-30", "cause": "sumiu"}}
    v, m = _vintages("VIVA", "MORTA"), VIVA
    piso = build_annual_panel(v, m, saidas=saidas, cenario="piso")
    crsp = build_annual_panel(v, m, saidas=saidas, cenario="crsp")
    fora = build_annual_panel(v, m, saidas=saidas, cenario="descartar")
    assert float(piso[piso.symbol == "MORTA"].iloc[0]["fwd_return"]) == -1.0
    assert float(crsp[crsp.symbol == "MORTA"].iloc[0]["fwd_return"]) == -0.30
    assert "MORTA" not in set(fora["symbol"])


def test_backtest_reporta_quantas_linhas_vieram_da_convencao() -> None:
    # Sem este numero, "a convencao nao mudou nada" e "a tabela de desfechos nao
    # foi publicada" chegam a tela como o mesmo silencio.
    from core.us_backtest import walk_forward
    v = pd.concat([_vintages("VIVA", "MORTA"),
                   pd.DataFrame([{"as_of_date": "2021-06-30", "symbol": "VIVA",
                                  "score": 60.0}])], ignore_index=True)
    saidas = {"MORTA": {"delisted_date": "2020-11-30", "cause": "sumiu"}}
    p = build_annual_panel(v, VIVA, saidas=saidas, cenario="piso")
    res = walk_forward(p, top_n=1)
    assert res["censura"]["n_convencionado"] == 1


def test_tela_nao_afirma_ultima_cotacao_quando_houve_convencao() -> None:
    # A frase fixa "elas entram pela ultima cotacao" envelhece invertida no dia
    # em que parte das linhas deixa de entrar assim.
    from pathlib import Path
    fonte = Path("views/empresas_americanas.py").read_text(encoding="utf-8")
    i = fonte.index('if censura.get("n_censurado")')
    bloco = fonte[i:i + 2200]
    assert "n_convencionado" in bloco
    assert "frase_convencao" in bloco
    assert "n_desfechos" in bloco, "ausencia de desfecho publicado precisa se nomear"
