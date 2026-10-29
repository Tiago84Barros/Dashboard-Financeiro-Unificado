"""Proventos de renda da B3: a resposta mais nova da brapi como fonte da soma anual.

``market.dividends`` e unica por (ticker, event_date, tipo, valor) e o upsert
nao apaga. Quando a brapi corrige a data de pagamento, a linha antiga fica ao
lado da nova, e a soma crua por ano conta o mesmo provento duas vezes -- ou o
conta no ano errado, quando a correcao atravessa a virada do ano. Medido em
29/10/2026 no armazem local contra a resposta mais nova arquivada em
``market.brapi_raw_payloads``: a soma crua batia em 65,6% dos ticker-anos, e a
melhor regra de leitura em 86,1%.

Por isso o card de dividendos, o grafico, a serie da LLM, o adaptador de FII
e o DY do valuation leem o arquivo publicado a partir dessa resposta, e o
banco so entra para o ticker que o arquivo nao cobre.

Tickers e valores aqui sao sinteticos.
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core import proventos_b3 as pb
from core.dividend_types import renda_por_evento, soma_por_ano

ROOT = Path(__file__).resolve().parents[1]


def _item(rate, ex, pay, label="DIVIDENDO", remarks=""):
    """Uma linha de ``cashDividends`` no formato da brapi."""
    return {"rate": rate, "label": label, "remarks": remarks,
            "lastDatePrior": f"{ex}T00:00:00.000Z" if ex else None,
            "paymentDate": f"{pay}T00:00:00.000Z" if pay else None}


def _foto(ticker, itens, quando=datetime(2026, 10, 20, 3, tzinfo=timezone.utc)):
    """Uma tupla como ``SQL_FOTOS`` devolve."""
    return (ticker, quando, ticker, {"cashDividends": itens})


FOTO_A = _foto("AAAA3", [
    _item(0.5, "2024-03-10", "2024-04-15"),
    _item(0.5, "2024-03-10", "2024-04-15"),               # a mesma linha repetida
    _item(0.3, "2024-03-10", "2024-04-15"),               # outra declaracao, mesmo dia
    _item(0.2, "2024-03-10", "2024-04-15", label="JCP"),  # outro tipo, mesmo dia
    _item(1.0, "2024-05-02", "2024-05-20", label="AMORTIZAÇÃO"),
    _item(0.4, "2024-08-25", "2024-09-12"),               # calendario real
    _item(0.4, "2024-09-01", "2024-09-01"),               # a safra colapsada dele
    _item(0.1, "2023-12-28", None),                       # sem pagamento informado
])


def _anos(df, ticker):
    sel = df[df["ticker"] == ticker]
    return {int(a): round(float(t), 6) for a, t in zip(sel["ano"], sel["total"])}


def _linha_banco(ticker, amount, ex, pay, criado="2026-07-01", tipo="DIVIDENDO"):
    ex_d = pd.Timestamp(ex)
    pay_d = pd.Timestamp(pay) if pay else pd.NaT
    return {"ticker": ticker, "type": tipo, "amount": amount, "ex_date": ex_d,
            "payment_date": pay_d, "event_date": pay_d if pay else ex_d,
            "created_at": pd.Timestamp(criado, tz="UTC")}


# --------------------------------------------------------------------------
# a foto vira eventos
# --------------------------------------------------------------------------

def test_foto_so_renda_sem_copia_e_com_declaracoes_distintas():
    ev = pb.eventos_das_fotos([FOTO_A])

    assert list(ev.columns) == list(pb.COLUNAS)
    assert "AMORTIZAÇÃO" not in set(ev["type"])
    # a linha repetida conta uma vez; valor e tipo diferentes no mesmo dia somam
    abril = ev[ev["event_date"] == pd.Timestamp("2024-04-15")]
    assert sorted(abril["amount"]) == [0.2, 0.3, 0.5]
    # a safra colapsada sai porque o mes tem o calendario real
    setembro = ev[ev["event_date"].dt.month == 9]
    assert list(setembro["payment_date"]) == [pd.Timestamp("2024-09-12")]
    # sem pagamento, o evento cai na data-ex
    assert pd.Timestamp("2023-12-28") in set(ev["event_date"])

    assert _anos(soma_por_ano(ev), "AAAA3") == {2023: 0.1, 2024: 1.4}


def test_foto_vazia_devolve_quadro_com_as_colunas():
    ev = pb.eventos_das_fotos([_foto("AAAA3", [])])
    assert ev.empty and list(ev.columns) == list(pb.COLUNAS)


def test_foto_com_ticker_minusculo_sai_em_maiusculas():
    ev = pb.eventos_das_fotos([_foto("aaaa3", [_item(0.5, "2024-03-10", "2024-04-15")])])
    assert set(ev["ticker"]) == {"AAAA3"}


# --------------------------------------------------------------------------
# a regra de leitura do banco, para quem a foto nao cobre
# --------------------------------------------------------------------------

def test_revisao_da_data_de_pagamento_conta_uma_vez_e_fica_a_mais_nova():
    """A brapi corrigiu o pagamento de 28/12 para 10/01: a soma crua contava o
    provento em 2024 E em 2025."""
    df = pd.DataFrame([
        _linha_banco("BBBB4", 0.5, "2024-12-10", "2024-12-28", criado="2026-07-01"),
        _linha_banco("BBBB4", 0.5, "2024-12-10", "2025-01-10", criado="2026-08-01"),
    ])
    ev = renda_por_evento(df)
    assert len(ev) == 1
    assert ev["payment_date"].iloc[0] == pd.Timestamp("2025-01-10")
    assert _anos(soma_por_ano(ev), "BBBB4") == {2025: 0.5}


def test_parcelas_da_mesma_declaracao_somam():
    df = pd.DataFrame([
        _linha_banco("BBBB4", 0.5, "2024-03-10", "2024-06-15"),
        _linha_banco("BBBB4", 0.5, "2024-03-10", "2024-12-15"),
    ])
    assert len(renda_por_evento(df)) == 2


def test_linha_sem_pagamento_se_junta_a_uma_parcela_so():
    df = pd.DataFrame([
        _linha_banco("BBBB4", 0.5, "2024-03-10", "2024-06-15"),
        _linha_banco("BBBB4", 0.5, "2024-03-10", "2024-12-15"),
        _linha_banco("BBBB4", 0.5, "2024-03-10", None),
    ])
    ev = renda_por_evento(df)
    assert len(ev) == 2
    assert ev["payment_date"].notna().all()


def test_linha_sem_pagamento_e_sem_gemea_fica():
    df = pd.DataFrame([_linha_banco("BBBB4", 0.5, "2024-03-10", None)])
    ev = renda_por_evento(df)
    assert len(ev) == 1 and ev["event_date"].iloc[0] == pd.Timestamp("2024-03-10")


def test_valores_distintos_na_mesma_data_somam():
    df = pd.DataFrame([
        _linha_banco("BBBB4", 0.5, "2024-03-10", "2024-04-15"),
        _linha_banco("BBBB4", 0.3, "2024-03-10", "2024-04-15"),
    ])
    assert sorted(renda_por_evento(df)["amount"]) == [0.3, 0.5]


def test_faixa_encadeada_nao_funde_valores_a_mais_de_0_1_por_cento():
    """1,0000 -> 1,0009 -> 1,0018 cai numa faixa so, porque cada vizinho fica a
    0,1% do anterior. O par das pontas, nas mesmas datas, fica a 0,18% e e
    outra declaracao; a do meio, de outro ano, nao o torna o mesmo provento."""
    df = pd.DataFrame([
        _linha_banco("BBBB4", 1.0000, "2024-03-10", "2024-04-15"),
        _linha_banco("BBBB4", 1.0018, "2024-03-10", "2024-04-15"),
        _linha_banco("BBBB4", 1.0009, "2022-03-10", "2022-04-15"),
    ])
    ev = renda_por_evento(df)
    assert sorted(ev["amount"]) == [1.0, 1.0009, 1.0018]


def test_linha_sem_pagamento_nao_se_junta_por_faixa_encadeada():
    df = pd.DataFrame([
        _linha_banco("BBBB4", 1.0000, "2024-03-10", "2024-04-15"),
        _linha_banco("BBBB4", 1.0018, "2024-03-10", None),
        _linha_banco("BBBB4", 1.0009, "2022-03-10", "2022-04-15"),
        _linha_banco("BBBB4", 2.0000, "2023-03-10", None),
        _linha_banco("BBBB4", 2.0036, "2023-03-10", None),
        _linha_banco("BBBB4", 2.0018, "2021-03-10", "2021-04-15"),
    ])
    assert sorted(renda_por_evento(df)["amount"]) == [1.0, 1.0009, 1.0018,
                                                      2.0, 2.0018, 2.0036]


def test_regra_de_leitura_aceita_quadro_sem_created_at():
    df = pd.DataFrame([
        _linha_banco("BBBB4", 0.5, "2024-12-10", "2024-12-28"),
        _linha_banco("BBBB4", 0.5, "2024-12-10", "2025-01-10"),
    ]).drop(columns="created_at")
    assert len(renda_por_evento(df)) == 1


def test_soma_por_ano_pela_coluna_pedida_e_sem_a_data_fica_de_fora():
    ev = pd.DataFrame({
        "ticker": ["BBBB4", "BBBB4"], "amount": [0.5, 0.2],
        "ex_date": [pd.Timestamp("2023-12-20"), pd.NaT],
        "event_date": [pd.Timestamp("2024-01-10"), pd.Timestamp("2024-02-10")],
    })
    assert _anos(soma_por_ano(ev), "BBBB4") == {2024: 0.7}
    assert _anos(soma_por_ano(ev, data="ex_date"), "BBBB4") == {2023: 0.5}
    assert list(soma_por_ano(ev.iloc[0:0]).columns) == ["ticker", "ano", "total"]


# --------------------------------------------------------------------------
# a leitura: arquivo para quem ele cobre, banco para o resto
# --------------------------------------------------------------------------

PUBLICADO = {"por_ticker": {
    "AAAA3": {"foto_em": "2026-10-20", "anos": {"2023": 0.1, "2024": 1.4}},
    "CCCC11": {"foto_em": "2026-10-20", "anos": {}},
}}


@pytest.fixture
def banco(monkeypatch):
    import core.market_read as mr

    chamadas: list[dict] = []
    linhas = [
        _linha_banco("BBBB4", 0.5, "2024-12-10", "2024-12-28", criado="2026-07-01"),
        _linha_banco("BBBB4", 0.5, "2024-12-10", "2025-01-10", criado="2026-08-01"),
        _linha_banco("AAAA3", 9.9, "2024-03-10", "2024-04-15"),
    ]

    def _q(sql, params=None, engine=None):
        chamadas.append({"sql": sql, "params": dict(params or {})})
        pedidos = set((params or {}).get("tks") or [])
        return pd.DataFrame([r for r in linhas if r["ticker"] in pedidos])

    monkeypatch.setattr(mr, "_q", _q)
    return chamadas


def test_ticker_coberto_sai_do_arquivo_e_o_resto_do_banco(monkeypatch, banco):
    monkeypatch.setattr(pb, "carregar", lambda: PUBLICADO)

    out = pb.renda_anual(["AAAA3.SA", "bbbb4", None, ""])

    assert _anos(out, "AAAA3") == {2023: 0.1, 2024: 1.4}  # nada do 9,9 do banco
    assert _anos(out, "BBBB4") == {2025: 0.5}
    assert [c["params"]["tks"] for c in banco] == [["BBBB4"]]
    assert out.attrs["fonte"] == f"{pb.FONTE_ARQUIVO} + {pb.FONTE_BANCO}"
    assert "load_error" not in out.attrs


def test_coberto_sem_renda_nao_consulta_o_banco(monkeypatch, banco):
    """Cobertura vazia e a fonte dizendo que nao houve renda; o banco traria
    de volta a copia que o arquivo existe para nao ter."""
    monkeypatch.setattr(pb, "carregar", lambda: PUBLICADO)

    out = pb.renda_anual(["CCCC11"])

    assert out.empty and list(out.columns) == ["ticker", "ano", "total"]
    assert banco == []
    assert out.attrs["fonte"] == pb.FONTE_ARQUIVO


def test_sem_arquivo_tudo_vem_do_banco_pela_regra_de_leitura(banco):
    # o conftest ja troca `carregar` por um arquivo ausente
    out = pb.renda_anual(["BBBB4"])
    assert _anos(out, "BBBB4") == {2025: 0.5}
    assert out.attrs["fonte"] == pb.FONTE_BANCO


def test_falha_do_banco_chega_no_attrs(monkeypatch):
    import core.market_read as mr

    def _q(sql, params=None, engine=None):
        frame = pd.DataFrame()
        frame.attrs["load_error"] = "query_failed"
        return frame

    monkeypatch.setattr(mr, "_q", _q)
    monkeypatch.setattr(pb, "carregar", lambda: PUBLICADO)

    out = pb.renda_anual(["AAAA3", "BBBB4"])

    assert _anos(out, "AAAA3") == {2023: 0.1, 2024: 1.4}
    assert out.attrs["load_error"] == "query_failed"


def test_load_dividendos_anuais_carrega_a_fonte_para_o_rotulo(monkeypatch, banco):
    import core.market_read as mr

    monkeypatch.setattr(pb, "carregar", lambda: PUBLICADO)
    mr.load_dividendos_anuais.clear()
    try:
        out = mr.load_dividendos_anuais("AAAA3")
    finally:
        mr.load_dividendos_anuais.clear()

    assert list(out["Dividendos"]) == [0.1, 1.4]
    assert list(out["Data"]) == [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")]
    assert out.attrs["fonte"] == pb.FONTE_ARQUIVO
    assert out.attrs["foto_em"] == {"AAAA3": "2026-10-20"}


def test_exercicio_no_ano_civil_aberto_fica_sem_total_de_proventos(monkeypatch):
    """O exercício encerrado fora de dezembro já tem demonstração do ano civil
    em curso; a soma de proventos desse ano é parcial e não vira o total."""
    import datetime as dt

    import core.market_read as mr

    ano = dt.date.today().year
    numericas = ("Receita_Liquida", "EBIT", "EBITDA", "Lucro_Liquido", "LPA",
                 "Patrimonio_Liquido", "Divida_Liquida", "Divida_Total",
                 "Ativo_Total", "Caixa", "FCO", "FCI", "FCF")
    demo = pd.DataFrame({"Ticker": ["AAAA3", "AAAA3"], "_year": [ano - 1, ano],
                         **{c: [1.0, 1.0] for c in numericas}})
    monkeypatch.setattr(mr, "_q", lambda sql, params=None, engine=None: demo.copy())
    monkeypatch.setattr(mr, "_renda_anual", lambda tks: pd.DataFrame(
        {"ticker": ["AAAA3", "AAAA3"], "ano": [ano - 1, ano], "total": [1.4, 0.6]}))
    mr.load_demonstracoes.clear()
    mr.load_demonstracoes_batch.clear()
    try:
        um = mr.load_demonstracoes("AAAA3")
        lote = mr.load_demonstracoes_batch(("AAAA3",))["AAAA3"]
    finally:
        mr.load_demonstracoes.clear()
        mr.load_demonstracoes_batch.clear()

    for out in (um, lote):
        assert out["Dividendos"].iloc[0] == 1.4
        assert pd.isna(out["Dividendos"].iloc[1])
        assert out["Receita_Liquida"].notna().all()  # o exercício fica, sem o total parcial


def test_demonstracoes_levam_a_procedencia_dos_dividendos_por_ticker(monkeypatch):
    """Patch 5, LLM e snapshot leem Dividendos daqui: sem o dia da resposta, a
    que a brapi parou de renovar passaria por atual."""
    import core.market_read as mr

    numericas = ("Receita_Liquida", "EBIT", "EBITDA", "Lucro_Liquido", "LPA",
                 "Patrimonio_Liquido", "Divida_Liquida", "Divida_Total",
                 "Ativo_Total", "Caixa", "FCO", "FCI", "FCF")
    demo = pd.DataFrame({"Ticker": ["AAAA3", "BBBB4"], "_year": [2023, 2023],
                         **{c: [1.0, 1.0] for c in numericas}})

    def _divs(tks):
        out = pd.DataFrame({"ticker": ["AAAA3", "BBBB4"], "ano": [2023, 2023],
                            "total": [1.4, 0.6]})
        out = out[out["ticker"].isin(tks)].reset_index(drop=True)
        out.attrs["foto_em"] = {"AAAA3": "2026-07-03"}
        return out

    def _q(sql, params=None, engine=None):
        pedidos = params.get("tks") or [params.get("t")]
        return demo[demo["Ticker"].isin(pedidos)].copy()

    monkeypatch.setattr(mr, "_q", _q)
    monkeypatch.setattr(mr, "_renda_anual", _divs)
    mr.load_demonstracoes.clear()
    mr.load_demonstracoes_batch.clear()
    try:
        um = mr.load_demonstracoes("AAAA3")
        lote = mr.load_demonstracoes_batch(("AAAA3", "BBBB4"))
    finally:
        mr.load_demonstracoes.clear()
        mr.load_demonstracoes_batch.clear()

    for out in (um, lote["AAAA3"]):
        assert out.attrs == {"fonte": pb.FONTE_ARQUIVO, "foto_em": {"AAAA3": "2026-07-03"}}
    assert lote["BBBB4"].attrs == {"fonte": pb.FONTE_BANCO, "foto_em": {}}


def test_renda_anual_traz_o_dia_da_resposta_so_de_quem_o_arquivo_cobre(monkeypatch, banco):
    monkeypatch.setattr(pb, "carregar", lambda: PUBLICADO)
    out = pb.renda_anual(["AAAA3", "BBBB4", "CCCC11"])
    assert out.attrs["foto_em"] == {"AAAA3": "2026-10-20", "CCCC11": "2026-10-20"}


def test_procedencia_data_a_resposta_e_marca_a_que_parou_de_renovar():
    attrs = {"fonte": pb.FONTE_ARQUIVO, "foto_em": {"AAAA3": "2026-10-20"}}
    no_limite = date(2026, 10, 20) + timedelta(days=pb.FOTO_MAXIMA_DIAS)
    assert pb.procedencia(attrs, hoje=no_limite) == "brapi, resposta de 20/10/2026"
    assert pb.procedencia(attrs, hoje=no_limite + timedelta(days=1)) == (
        f"brapi, resposta de 20/10/2026 — VELHA: {pb.FOTO_MAXIMA_DIAS + 1} dias sem renovação")


def test_procedencia_pela_resposta_mais_velha_e_sem_arquivo_inalterada():
    misto = {"fonte": f"{pb.FONTE_ARQUIVO} + {pb.FONTE_BANCO}",
             "foto_em": {"AAAA3": "2026-10-20", "DDDD3": "2026-07-03", "EEEE3": None}}
    assert pb.idade_da_foto(misto, hoje=date(2026, 10, 29)) == (date(2026, 7, 3), 118)
    assert pb.procedencia(misto, hoje=date(2026, 10, 29)) == (
        f"brapi, resposta de 03/07/2026 — VELHA: 118 dias sem renovação + {pb.FONTE_BANCO}")
    assert pb.procedencia({"fonte": pb.FONTE_BANCO, "foto_em": {}}) == pb.FONTE_BANCO
    assert pb.idade_da_foto({"foto_em": {"EEEE3": None}}) is None
    assert pb.procedencia({}) == ""


def test_eventos_do_armazem_partilha_como_a_leitura(monkeypatch):
    pedidos = {}

    class _Resultado:
        def fetchall(self):
            return [FOTO_A]

    class _Conn:
        def execute(self, sql):
            return _Resultado()

    def _read_sql_query(sql, conn, params=None):
        pedidos["sql"], pedidos["params"] = str(sql), params
        return pd.DataFrame([_linha_banco("BBBB4", 0.5, "2024-12-10", "2025-01-10")])

    monkeypatch.setattr(pd, "read_sql_query", _read_sql_query)

    ev = pb.eventos_do_armazem(_Conn())

    assert pedidos["params"] == {"cobertos": ["AAAA3"]}
    assert "NOT (upper(d.ticker) = ANY(:cobertos))" in pedidos["sql"]
    assert "foto_em" not in ev.columns
    assert _anos(soma_por_ano(ev), "AAAA3") == {2023: 0.1, 2024: 1.4}
    assert _anos(soma_por_ano(ev), "BBBB4") == {2025: 0.5}


# --------------------------------------------------------------------------
# o publicador
# --------------------------------------------------------------------------

def _publicador():
    from scripts import publish_proventos_b3 as pub
    return pub


def test_payload_cobre_quem_so_amortizou_com_anos_vazios():
    pub = _publicador()
    so_amortizou = _foto("CCCC11", [_item(1.0, "2024-05-02", "2024-05-20",
                                           label="AMORTIZAÇÃO")],
                         quando=datetime(2026, 10, 18, 3, tzinfo=timezone.utc))
    agora = datetime(2026, 10, 21, tzinfo=timezone.utc)

    p = pub.montar_payload([FOTO_A, so_amortizou], agora)

    assert p["n_tickers"] == 2 and p["n_com_renda"] == 1
    assert p["foto_mais_nova"] == "2026-10-20"
    assert p["por_ticker"]["AAAA3"] == {"foto_em": "2026-10-20",
                                        "anos": {"2023": 0.1, "2024": 1.4}}
    assert p["por_ticker"]["CCCC11"] == {"foto_em": "2026-10-18", "anos": {}}
    assert p["gerado_em"] == "2026-10-21T00:00:00+00:00"


def test_recusa_coleta_parada_ou_vazia():
    pub = _publicador()
    hoje = date(2026, 10, 30)
    em_dia = {"n_tickers": 1, "foto_mais_nova": "2026-10-20"}
    velha = {"n_tickers": 1, "foto_mais_nova": "2026-10-19"}

    assert pub.foto_velha(em_dia, hoje) is None
    assert "11 dias" in pub.foto_velha(velha, hoje)
    assert pub.foto_velha({"n_tickers": 0, "foto_mais_nova": None}, hoje)


def test_arquivo_gravado_e_o_que_a_leitura_le(tmp_path, monkeypatch, banco):
    from core.inteligencia_ativos import arquivo_publicado

    pub = _publicador()
    caminho = tmp_path / "proventos_b3.json.gz"
    payload = pub.montar_payload([FOTO_A], datetime(2026, 10, 21, tzinfo=timezone.utc))

    pub.serializar(payload, caminho)
    primeiro = caminho.read_bytes()
    pub.serializar(payload, caminho)
    assert caminho.read_bytes() == primeiro  # mesmo conteudo, mesmos bytes

    lido = arquivo_publicado.ler(str(caminho), "proventos_b3")
    monkeypatch.setattr(pb, "carregar", lambda: lido)
    assert _anos(pb.renda_anual(["AAAA3"]), "AAAA3") == {2023: 0.1, 2024: 1.4}
    assert banco == []


class _ArmazemFalso:
    def __init__(self, fotos):
        self.fotos, self.sqls = fotos, []

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sqls.append(str(sql))
        fotos = self.fotos

        class _R:
            def fetchall(self):
                return fotos
        return _R()


@pytest.fixture
def armazem(monkeypatch):
    import sqlalchemy

    from scripts import publish_fii_selection_from_local as origem

    estado = {}

    def _create_engine(url, *a, **k):
        return estado["armazem"]

    monkeypatch.setattr(sqlalchemy, "create_engine", _create_engine)
    monkeypatch.setattr(origem, "_warehouse_url", lambda: "postgresql://armazem-falso")
    return estado


def test_main_publica_so_leitura(tmp_path, armazem, capsys):
    pub = _publicador()
    ontem = datetime.now(timezone.utc) - timedelta(days=1)
    armazem["armazem"] = falso = _ArmazemFalso([_foto("AAAA3", FOTO_A[3]["cashDividends"],
                                                      quando=ontem)])
    saida = tmp_path / "p.json.gz"

    assert pub.main(["--saida", str(saida)]) == 0

    assert falso.sqls[0] == "SET TRANSACTION READ ONLY"
    with gzip.open(saida, "rb") as fh:
        assert json.loads(fh.read())["por_ticker"]["AAAA3"]["anos"] == {"2023": 0.1,
                                                                       "2024": 1.4}
    assert json.loads(capsys.readouterr().out)["n_com_renda"] == 1


def test_main_nao_grava_sobre_coleta_parada(tmp_path, armazem, capsys):
    pub = _publicador()
    velha = datetime.now(timezone.utc) - timedelta(days=pub.FOTO_MAXIMA_DIAS + 5)
    armazem["armazem"] = _ArmazemFalso([_foto("AAAA3", FOTO_A[3]["cashDividends"],
                                              quando=velha)])
    saida = tmp_path / "p.json.gz"

    assert pub.main(["--saida", str(saida)]) == 1

    assert not saida.exists()
    assert "erro" in json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------
# nenhum leitor volta a somar a coluna crua
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "core/market_read.py",
    "core/portfolio/adapters/fii.py",
    "scripts/publish_valuation_historico.py",
])
def test_leitores_nao_somam_market_dividends_cru(rel):
    fonte = (ROOT / rel).read_text(encoding="utf-8")
    assert not re.search(r"(?i)\b(sum|min)\s*\(\s*(\w+\.)?amount\s*\)", fonte), rel


def test_leitores_anuais_passam_pela_mesma_fonte():
    fonte = (ROOT / "core/market_read.py").read_text(encoding="utf-8")
    for nome in ("load_demonstracoes", "load_demonstracoes_batch", "load_dividendos_anuais"):
        corpo = fonte.split(f"def {nome}(", 1)[1].split("\ndef ", 1)[0]
        assert "_renda_anual(" in corpo, nome


# --------------------------------------------------------------------------
# a resposta velha da brapi chega a quem lê Dividendos das demonstrações
# --------------------------------------------------------------------------

def test_patch5_registra_a_resposta_velha_em_restricoes(monkeypatch):
    import views.portfolio_b3 as view

    avisos = []
    monkeypatch.setattr(view, "aviso_lacuna", lambda msg, **k: avisos.append((msg, k)))
    hoje = date.today()
    velha = pd.DataFrame({"Dividendos": [1.0]})
    velha.attrs.update(fonte=pb.FONTE_ARQUIVO, foto_em={
        "AAAA3": (hoje - timedelta(days=pb.FOTO_MAXIMA_DIAS + 1)).isoformat()})
    nova = pd.DataFrame({"Dividendos": [1.0]})
    nova.attrs.update(fonte=pb.FONTE_ARQUIVO, foto_em={"BBBB4": hoje.isoformat()})
    do_banco = pd.DataFrame({"Dividendos": [1.0]})
    do_banco.attrs.update(fonte=pb.FONTE_BANCO, foto_em={})

    view._avisar_proventos_velhos({"AAAA3": velha, "BBBB4": nova, "CCCC3": do_banco,
                                   "DDDD3": pd.DataFrame()})

    assert len(avisos) == 1
    msg, k = avisos[0]
    assert f"{pb.FOTO_MAXIMA_DIAS + 1} dias sem renovação" in msg
    assert k == {"codigo": "tela.portfolio_b3.proventos_foto_velha", "entidade": "AAAA3"}


def _lote_com_uma_resposta_velha(hoje):
    velha = pd.DataFrame({"Dividendos": [1.0]})
    velha.attrs.update(fonte=pb.FONTE_ARQUIVO, foto_em={
        "AAAA3": (hoje - timedelta(days=pb.FOTO_MAXIMA_DIAS + 1)).isoformat()})
    no_limite = pd.DataFrame({"Dividendos": [1.0]})
    no_limite.attrs.update(fonte=pb.FONTE_ARQUIVO, foto_em={
        "BBBB4": (hoje - timedelta(days=pb.FOTO_MAXIMA_DIAS)).isoformat()})
    do_banco = pd.DataFrame({"Dividendos": [1.0]})
    do_banco.attrs.update(fonte=pb.FONTE_BANCO, foto_em={})
    return {"AAAA3": velha, "BBBB4": no_limite, "CCCC3": do_banco, "DDDD3": pd.DataFrame()}


def test_fotos_velhas_lista_so_a_resposta_que_passou_do_limite():
    hoje = date(2026, 10, 29)
    velhas = pb.fotos_velhas(_lote_com_uma_resposta_velha(hoje), hoje)

    assert [tk for tk, _ in velhas] == ["AAAA3"]
    dia = hoje - timedelta(days=pb.FOTO_MAXIMA_DIAS + 1)
    assert velhas[0][1] == (f"Proventos de AAAA3: a resposta mais nova da brapi é de "
                            f"{dia:%d/%m/%Y}, {pb.FOTO_MAXIMA_DIAS + 1} dias sem renovação.")
    assert pb.fotos_velhas({}) == [] and pb.fotos_velhas(None) == []


def test_comparacao_de_dre_registra_a_resposta_velha_so_em_dividendos(monkeypatch):
    import views.empresas_b3 as view

    avisos = []
    monkeypatch.setattr(view, "aviso_lacuna", lambda msg, **k: avisos.append((msg, k)))
    lote = _lote_com_uma_resposta_velha(date.today())

    view._avisar_comparacao_dre(lote, "Receita_Liquida")
    assert avisos == []

    view._avisar_comparacao_dre(lote, "Dividendos")
    assert len(avisos) == 1
    assert avisos[0][1] == {"codigo": "tela.b3.proventos_foto_velha", "entidade": "AAAA3"}


def test_comparacao_de_dre_avisa_antes_do_grafico():
    # O lote volta do session_state a cada rerun; o aviso vale para o gráfico
    # que ele desenha, então é chamado sobre o mesmo lote antes do px.bar.
    fonte = (ROOT / "views/empresas_b3.py").read_text(encoding="utf-8")
    trecho = fonte.split('batch_dre = st.session_state.get("b3_av_dre_batch"', 1)[1]
    trecho = trecho.split('key="b3_av_dre_chart"', 1)[0]
    assert trecho.index("_avisar_comparacao_dre(batch_dre, dre_col_s)") < trecho.index("px.bar(")
