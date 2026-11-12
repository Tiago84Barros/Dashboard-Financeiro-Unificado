"""Valuation anual na base da época: ações do ano, preço do ano, proventos.

Os dois caminhos que fazem a conta -- o reprocess
(`data_pipeline.market.ingest.reprocess_metrics`, que grava
`market.calculated_metrics`) e o valuation histórico publicado
(`scripts.publish_valuation_historico.historico_b3`) -- passam pela mesma
`acoes.acoes_e_lpa_do_ano` e têm de dar o mesmo P/L, P/VP e DY sobre os mesmos
dados. Tickers e números sintéticos (repositório público).
"""
import datetime as dt

import pandas as pd
import pytest

from data_pipeline.market import acoes as ac
from data_pipeline.market import acoes_cotacao as ac_cot
from data_pipeline.market import b3_sucessao as suc
from data_pipeline.market import ingest as ig

# ── a regra das ações do ano (R3: época com teto, base de hoje só no desdobramento)

def test_epoca_passa_na_guarda_pelo_fator():
    # 500 ações na época, desdobramento 1:2 depois: 500 × 2 = 1 000 de hoje.
    r = ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=2.0, por_titulo=1.0,
                              acoes_atuais=1_000.0, fator_base=2.0)
    assert r == (pytest.approx(500.0), pytest.approx(2.0), ac.BASE_DA_EPOCA)


def test_lpa_na_base_de_hoje_e_resgatado_pelo_fator():
    # O LPA já veio dividido pelo desdobramento: lucro ÷ LPA dá as ações de
    # hoje, e a época (× 10) sai da guarda. Volta para a época pelo fator.
    r = ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=1.0, por_titulo=1.0,
                              acoes_atuais=1_000.0, fator_base=10.0)
    assert r == (pytest.approx(100.0), pytest.approx(10.0), ac.BASE_DE_HOJE)


def test_emissao_posterior_fica_na_epoca():
    # Grupamento de 1 000:1 e emissão enorme depois: os títulos da época, × o
    # fator, somem diante dos de hoje. Não há piso, e o P/L da época (preço
    # da época ÷ LPA da época) é coerente. A R2 resgatava pela base de hoje
    # (títulos ÷ atuais = 4,96 caía na faixa) e multiplicava o P/L por 1 000.
    r = ac.acoes_e_lpa_do_ano(lucro=4_960.0, lpa=1.0, por_titulo=1.0,
                              acoes_atuais=1_000.0, fator_base=0.001)
    assert r == (pytest.approx(4_960.0), pytest.approx(1.0), ac.BASE_DA_EPOCA)
    # Emissão sem evento de preço: 5× menos ações na época, sem fator.
    r = ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=5.5, por_titulo=1.0,
                              acoes_atuais=1_000.0)
    assert r[2] == ac.BASE_DA_EPOCA


def test_grupamento_nao_resgata_pela_base_de_hoje():
    # Títulos iguais aos de hoje com fator de grupamento (< 1): a época passa
    # no teto e não há resgate que divida o LPA por 200.
    r = ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=1.0, por_titulo=1.0,
                              acoes_atuais=1_000.0, fator_base=0.005)
    assert r == (pytest.approx(1_000.0), pytest.approx(1.0), ac.BASE_DA_EPOCA)


def test_base_de_hoje_so_com_titulos_proximos_dos_de_hoje():
    # Desdobramento de 10, mas os títulos sem o fator são 2× os de hoje: nem
    # época (20×) nem base de hoje (fora de 0,8–1,25).
    assert ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=0.5, por_titulo=1.0,
                                 acoes_atuais=1_000.0, fator_base=10.0) is None
    # Na borda da faixa ainda resgata.
    r = ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=0.8, por_titulo=1.0,
                              acoes_atuais=1_000.0, fator_base=10.0)
    assert r[2] == ac.BASE_DE_HOJE


def test_reprovado_nas_duas_bases_fica_sem_acoes():
    assert ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=0.01, por_titulo=1.0,
                                 acoes_atuais=1_000.0, fator_base=1.0) is None


def test_sem_fator_so_a_comparacao_direta():
    assert ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=1.0, por_titulo=1.0,
                                 acoes_atuais=1_000.0)[2] == ac.BASE_DA_EPOCA
    # 100 000 ÷ 1 000 = 100, fora da guarda, e sem fator não há resgate.
    assert ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=0.01, por_titulo=1.0,
                                 acoes_atuais=1_000.0) is None


def test_sem_acoes_atuais_vale_a_epoca_sem_guarda():
    r = ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=0.01, por_titulo=1.0,
                              acoes_atuais=None, fator_base=1.0)
    assert r == (pytest.approx(100_000.0), pytest.approx(0.01), ac.BASE_DA_EPOCA)


def test_unit_divide_as_acoes_por_k_e_multiplica_o_lpa():
    # 3 ações por unit: 3 000 ações = 1 000 units, LPA da unit = 3 × LPA.
    r = ac.acoes_e_lpa_do_ano(lucro=3_000.0, lpa=1.0, por_titulo=3.0,
                              acoes_atuais=1_000.0, fator_base=1.0)
    assert r == (pytest.approx(1_000.0), pytest.approx(3.0), ac.BASE_DA_EPOCA)


def test_lucro_ou_lpa_ausente_nao_tem_acoes():
    assert ac.acoes_e_lpa_do_ano(lucro=None, lpa=1.0, por_titulo=1.0,
                                 acoes_atuais=1_000.0) is None
    assert ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=None, por_titulo=1.0,
                                 acoes_atuais=1_000.0) is None
    assert ac.acoes_e_lpa_do_ano(lucro=1_000.0, lpa=1.0, por_titulo=None,
                                 acoes_atuais=1_000.0) is None


# ── o preço do ano ────────────────────────────────────────────────────────────

def test_preco_do_ano_prefere_a_fita_e_mede_o_fator():
    assert ig._preco_do_ano(sem_ajuste=20.0, ajustado=10.0) == (
        20.0, pytest.approx(2.0), ig.ORIGEM_PRECO_FITA)


def test_preco_do_ano_sem_ajustado_fica_sem_fator():
    assert ig._preco_do_ano(sem_ajuste=20.0, ajustado=None) == (
        20.0, None, ig.ORIGEM_PRECO_FITA)


def test_preco_do_ano_sem_fita_cai_no_ajustado_e_nomeia():
    assert ig._preco_do_ano(sem_ajuste=None, ajustado=10.0) == (
        10.0, None, ig.ORIGEM_PRECO_AJUSTADO)
    assert ig._preco_do_ano(sem_ajuste=None, ajustado=None) == (None, None, None)


# ── a fita lê a cadeia de sucessão ────────────────────────────────────────────

class _ConnFita:
    def __init__(self, linhas):
        self.linhas, self.params = linhas, None

    def execute(self, sql, params=None):
        self.params = params

        class _R:
            def fetchall(s):
                return list(self.linhas)
        return _R()


def test_fechamentos_leem_todos_os_codigos_da_cadeia():
    cadeia = suc.cadeias({"ANTG3": "NOVA3"})
    assert cadeia["NOVA3"] == ["ANTG3", "NOVA3"]
    conn = _ConnFita([(2020, 8.0), (2025, 12.0)])
    assert ac_cot.fechamentos_de_fim_de_ano(conn, "NOVA3", cadeia["NOVA3"]) == {
        2020: 8.0, 2025: 12.0}
    assert sorted(conn.params["codigos"]) == ["ANTG3", "NOVA3"]


def test_fechamentos_sem_cadeia_leem_so_o_proprio_codigo():
    conn = _ConnFita([])
    ac_cot.fechamentos_de_fim_de_ano(conn, "XPTO3")
    assert list(conn.params["codigos"]) == ["XPTO3"]


# ── os dois caminhos dão o mesmo número ───────────────────────────────────────
# XPTO3, 1 000 ações hoje, cotação 30.
#   2021: sem evento depois (fator 1), época passa.
#   2022: desdobramento 1:2 depois (fator 2), época passa (500 × 2 = 1 000).
#   2023: LPA na base de hoje, fator 10: resgatado pela base de hoje.
#   2024: LPA absurdo, acima do teto e sem resgate: sem P/L e sem P/VP.

ACOES_HOJE, PRECO_HOJE = 1_000.0, 30.0
ANOS = {  # ano: (lucro, lpa, patrimonio, fechamento sem ajuste, ajustado, provento)
    2021: (1_000.0, 1.0, 5_000.0, 10.0, 10.0, 0.5),
    2022: (1_000.0, 2.0, 5_000.0, 20.0, 10.0, 1.0),
    2023: (1_000.0, 1.0, 5_000.0, 100.0, 10.0, 2.0),
    2024: (1_000.0, 0.01, 5_000.0, 15.0, 15.0, 0.3),
}


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _ConnAnual:
    """Armazém de mentira com o spot, o tipo e as demonstrações por ano."""

    def execute(self, sql, params=None):
        s = str(sql)
        if "period='spot'" in s:
            return _Res([("Acoes_Emitidas", ACOES_HOJE, "brapi_field"),
                         ("LPA", 1.0, "brapi_field"),
                         ("marketCap", ACOES_HOJE * PRECO_HOJE,
                          "preco_x_acoes_emitidas_por_titulo")])
        if "asset_type FROM market.assets" in s:
            return _Res([("acao",)])
        ultimo = max(ANOS)
        lucro, lpa, pl, *_ = ANOS[ultimo]
        if "period='annual' ORDER BY year DESC LIMIT 1" in s:
            if "income_statements" in s:
                return _Res([(10_000.0, 2_000.0, 2_500.0, lucro, lpa)])
            if "balance_sheets" in s:
                return _Res([(20_000.0, pl, 100.0, 300.0, 200.0, 600.0, 400.0)])
            return _Res([])
        if "period='annual'" in s and "SELECT year" in s:
            if "income_statements" in s:
                return _Res([(y, 10_000.0, 2_000.0, 2_500.0, v[0], v[1])
                             for y, v in ANOS.items()])
            if "balance_sheets" in s:
                return _Res([(y, 20_000.0, v[2], 100.0, 300.0, 200.0, 600.0, 400.0)
                             for y, v in ANOS.items()])
            return _Res([])
        return _Res([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


class _Repo:
    def __init__(self):
        self.linhas = []

    def schema_exists(self, conn):
        return True

    def append_metric_vintages(self, conn, rows):
        return len(rows)

    def replace_metric_snapshot(self, conn, ticker, rows):
        self.linhas += rows
        return 0


def _reprocess(monkeypatch, eventos=None):
    repo = _Repo()
    monkeypatch.setattr(ig, "repo", repo)
    monkeypatch.setattr(ig, "_engine", lambda: _Engine(_ConnAnual()))
    monkeypatch.setattr(ac_cot, "tabela_existe", lambda conn: True)
    monkeypatch.setattr(ac_cot, "preco_atual",
                        lambda conn, tk, existe=True: (PRECO_HOJE, PRECO_HOJE))
    monkeypatch.setattr(ac_cot, "fita_existe", lambda conn: True)
    monkeypatch.setattr(suc, "carregar_sucessoes", lambda conn: {})
    monkeypatch.setattr(ac_cot, "fechamentos_de_fim_de_ano",
                        lambda conn, tk, codigos=None: {y: v[3] for y, v in ANOS.items()})
    monkeypatch.setattr(ac_cot, "ajustados_de_fim_de_ano",
                        lambda conn, tk: {y: v[4] for y, v in ANOS.items()})
    if eventos is None:
        eventos = [(dt.date(y, 6, 30), dt.date(y, 6, 15), v[5]) for y, v in ANOS.items()]
    monkeypatch.setattr(ig, "_eventos_de_renda", lambda conn: ({"XPTO3": eventos}, "fake"))
    prog = ig.reprocess_metrics(["XPTO3"])
    anual = {}
    for r in repo.linhas:
        if r["period"] == "annual":
            anual.setdefault(r["year"], {})[r["metric_name"]] = r
    return prog, anual


def _gemeo():
    from scripts import publish_valuation_historico as pub
    fech = pd.DataFrame([{"ticker": "XPTO3", "ano": y, "close": v[3]} for y, v in ANOS.items()])
    demos = pd.DataFrame([{"ticker": "XPTO3", "ano": y, "lucro": v[0], "lpa": v[1],
                           "patrimonio": v[2]} for y, v in ANOS.items()])
    prov = pd.DataFrame([{"ticker": "XPTO3", "ano": y, "total": v[5]} for y, v in ANOS.items()])
    ajust = pd.DataFrame([{"ticker": "XPTO3", "ano": y, "close_ajustado": v[4]}
                          for y, v in ANOS.items()])
    saida, excl = pub.historico_b3(fech, demos, prov, acoes_por_titulo={"XPTO3": 1.0},
                                   ajustados=ajust, titulos_atuais={"XPTO3": ACOES_HOJE})
    h = saida["XPTO3"]["historico"]
    return ({k: dict(map(tuple, h[k])) for k in ("p_l", "p_vp", "dividend_yield")}, excl)


def test_reprocess_e_valuation_publicado_dao_o_mesmo_numero(monkeypatch):
    prog, anual = _reprocess(monkeypatch)
    gemeo, excl = _gemeo()
    assert prog["erros"] == 0
    assert prog["anos_lpa_base_de_hoje"] == 1 == excl["anos_lpa_base_de_hoje"]
    assert excl["acoes_fora_da_guarda"] == 1
    assert prog["anos_preco_ajustado"] == 0

    # P/L 10 e P/VP 2 nos três anos que passam, nos dois caminhos.
    for y in (2021, 2022, 2023):
        assert anual[y]["P/L"]["metric_value"] == pytest.approx(10.0)
        assert anual[y]["P/L"]["metric_value"] == pytest.approx(gemeo["p_l"][y], rel=1e-3)
        assert anual[y]["P/VP"]["metric_value"] == pytest.approx(gemeo["p_vp"][y], rel=1e-3)
        assert "~aprox" not in anual[y]["P/L"]["calculation_method"]
    # Acima do teto e sem resgate: nenhum dos dois caminhos afirma P/L nem P/VP.
    assert "P/L" not in anual[2024] and 2024 not in gemeo["p_l"]
    assert "P/VP" not in anual[2024] and 2024 not in gemeo["p_vp"]
    # DY pelo fechamento sem ajuste, com o provento do ano da data-ex.
    for y in ANOS:
        assert 100.0 * anual[y]["DY"]["metric_value"] == pytest.approx(
            gemeo["dividend_yield"][y], rel=1e-3)


def test_reprocess_sem_fita_marca_o_preco_ajustado(monkeypatch):
    monkeypatch.setattr(ac_cot, "fita_existe", lambda conn: False)
    repo = _Repo()
    monkeypatch.setattr(ig, "repo", repo)
    monkeypatch.setattr(ig, "_engine", lambda: _Engine(_ConnAnual()))
    monkeypatch.setattr(ac_cot, "tabela_existe", lambda conn: True)
    monkeypatch.setattr(ac_cot, "preco_atual",
                        lambda conn, tk, existe=True: (PRECO_HOJE, PRECO_HOJE))
    monkeypatch.setattr(ac_cot, "ajustados_de_fim_de_ano",
                        lambda conn, tk: {y: v[4] for y, v in ANOS.items()})
    monkeypatch.setattr(ig, "_eventos_de_renda", lambda conn: ({}, "fake"))
    prog = ig.reprocess_metrics(["XPTO3"])
    assert prog["erros"] == 0
    assert prog["anos_preco_ajustado"] == len(ANOS)
    assert prog["tickers_com_sucessao"] == 0


def test_provento_sem_data_ex_nao_vira_payout_zero_no_ano_do_pagamento(monkeypatch):
    # 2021 com data-ex; 2022 só com o pagamento. 2022 fica sem DY e sem Payout,
    # como no publicador, que só soma pela data-ex. 2023, sem evento nenhum,
    # continua com payout zero. (2024 fica acima do teto: sem P/L, sem Payout.)
    eventos = [(dt.date(2021, 6, 30), dt.date(2021, 6, 15), 0.5),
               (dt.date(2022, 6, 30), None, 2.0)]
    prog, anual = _reprocess(monkeypatch, eventos)
    assert prog["erros"] == 0
    assert anual[2021]["Payout"]["metric_value"] > 0
    assert "Payout" not in anual[2022] and "DY" not in anual[2022]
    assert anual[2023]["Payout"]["metric_value"] == 0.0


def test_evento_sem_data_nenhuma_deixa_o_payout_desconhecido(monkeypatch):
    eventos = [(dt.date(2021, 6, 30), dt.date(2021, 6, 15), 0.5), (None, None, 1.0)]
    prog, anual = _reprocess(monkeypatch, eventos)
    assert prog["erros"] == 0
    assert anual[2021]["Payout"]["metric_value"] > 0
    assert all("Payout" not in anual[y] for y in (2022, 2023))


# ── proventos: o banco completa só o ano inteiro que a foto não tem ───────────

def _ev(tk, ex, pag, valor, tipo="DIVIDENDO"):
    return {"ticker": tk, "type": tipo, "ex_date": pd.Timestamp(ex),
            "payment_date": pd.Timestamp(pag) if pag else pd.NaT,
            "event_date": pd.Timestamp(pag or ex), "amount": valor}


def _anos(foto, banco):
    from core.proventos_b3 import anos_que_a_foto_nao_tem
    return anos_que_a_foto_nao_tem(pd.DataFrame(foto), pd.DataFrame(banco))


def test_banco_devolve_o_ano_que_a_foto_perdeu():
    foto = [_ev("XPTO3", "2022-05-02", "2022-05-20", 1.0)]
    banco = [_ev("XPTO3", "2021-05-03", "2021-05-21", 0.8),
             _ev("XPTO3", "2022-05-02", "2022-05-20", 1.0)]
    out = _anos(foto, banco)
    assert list(out["amount"]) == [0.8]
    assert "foto_em" not in out.columns


def test_ano_que_a_foto_cobre_nao_ganha_parcela_do_banco():
    foto = [_ev("XPTO3", "2022-05-02", "2022-05-20", 1.0)]
    banco = [_ev("XPTO3", "2022-11-03", "2022-11-21", 0.4)]
    assert _anos(foto, banco).empty


def test_copia_na_virada_do_ano_nao_conta_duas_vezes():
    # Data-ex regravada: 30/12 na foto, 03/01 no banco. Mesmo valor, 4 dias.
    foto = [_ev("XPTO3", "2022-12-30", None, 1.25)]
    banco = [_ev("XPTO3", "2023-01-03", None, 1.25)]
    assert _anos(foto, banco).empty


def test_jcp_e_dividendo_do_mesmo_valor_na_virada_sao_dois_proventos():
    # JCP com data-ex em 30/12 no banco, DIVIDENDO de mesmo valor em 03/01 na foto.
    foto = [_ev("XPTO3", "2023-01-03", None, 1.25, "DIVIDENDO")]
    banco = [_ev("XPTO3", "2022-12-30", None, 1.25, "JCP")]
    out = _anos(foto, banco)
    assert list(out["amount"]) == [1.25] and list(out["type"]) == ["JCP"]


def test_data_ex_vizinha_com_pagamentos_meses_a_parte_sao_dois_proventos():
    # JCP 29/12/2022 pago em jun/2023 e JCP 03/01/2023 pago em dez/2023.
    foto = [_ev("XPTO3", "2023-01-03", "2023-12-20", 1.25, "JCP")]
    banco = [_ev("XPTO3", "2022-12-29", "2023-06-20", 1.25, "JCP")]
    assert list(_anos(foto, banco)["amount"]) == [1.25]


def test_data_ex_vizinha_com_pagamentos_na_janela_e_copia():
    foto = [_ev("XPTO3", "2023-01-03", "2023-02-10", 1.25, "JCP")]
    banco = [_ev("XPTO3", "2022-12-29", "2023-02-20", 1.25, "JCP")]
    assert _anos(foto, banco).empty


def test_tipo_ausente_num_lado_nao_separa_a_copia():
    foto = [_ev("XPTO3", "2022-12-30", None, 1.25, None)]
    banco = [_ev("XPTO3", "2023-01-03", None, 1.25, "jcp ")]
    assert _anos(foto, banco).empty


def test_copia_pelo_mesmo_pagamento_mesmo_longe_na_data_ex():
    foto = [_ev("XPTO3", "2022-12-01", "2023-03-10", 1.25)]
    banco = [_ev("XPTO3", "2023-02-01", "2023-03-10", 1.25)]
    assert _anos(foto, banco).empty


def test_valor_diferente_perto_da_virada_fica():
    foto = [_ev("XPTO3", "2022-12-30", None, 1.25)]
    banco = [_ev("XPTO3", "2023-01-03", None, 0.40)]
    assert list(_anos(foto, banco)["amount"]) == [0.40]


def test_mesmo_valor_longe_e_com_outro_pagamento_fica():
    foto = [_ev("XPTO3", "2022-05-02", "2022-05-20", 1.0)]
    banco = [_ev("XPTO3", "2023-05-02", "2023-05-20", 1.0)]
    assert list(_anos(foto, banco)["amount"]) == [1.0]


def test_ticker_sem_foto_nao_entra_pelo_preenchimento():
    foto = [_ev("XPTO3", "2022-05-02", "2022-05-20", 1.0)]
    banco = [_ev("OUTR3", "2021-05-03", "2021-05-21", 0.8)]
    assert _anos(foto, banco).empty


def test_foto_ou_banco_vazios_devolvem_quadro_com_as_colunas():
    from core.proventos_b3 import COLUNAS, anos_que_a_foto_nao_tem
    esperadas = [c for c in COLUNAS if c != "foto_em"]
    assert list(anos_que_a_foto_nao_tem(pd.DataFrame(), pd.DataFrame()).columns) == esperadas
    foto = pd.DataFrame([_ev("XPTO3", "2022-05-02", "2022-05-20", 1.0)])
    assert list(anos_que_a_foto_nao_tem(foto, pd.DataFrame()).columns) == esperadas
