"""Detalhe por empresa americana lido do armazém (direto ou pelo túnel).

Nada toca o Postgres: a leitura roda sobre uma conexão de mentira que devolve
linhas por consulta, e o servidor do túnel sobe em 127.0.0.1 com a leitura
trocada por dublê. O que se prende é o que faria a LLM ler número errado ou
achar que leu tudo: duplicata somada duas vezes, data em texto (o túnel entrega
ISO) quebrando a conta, túnel fora do ar sumindo do prompt em silêncio.
"""
from __future__ import annotations

import pathlib
import sys
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_remoto as ar  # noqa: E402
import core.us_detalhe_armazem as det  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

HOJE = date(2026, 9, 28)
TOKEN = "t" * 40


def _precos(n=260, inicio=100.0, passo=0.001):
    dias, p, linhas = HOJE - timedelta(days=n), inicio, []
    for i in range(n):
        p *= 1 + (passo if i % 2 == 0 else -passo / 2)
        linhas.append({"date": dias + timedelta(days=i), "preco": Decimal(str(round(p, 6))),
                       "close": p, "volume": 1_000_000})
    return linhas


def _trimestre(fy, fq, receita, lucro, ref):
    return {"fiscal_year": fy, "fiscal_quarter": fq, "reference_date": ref,
            "available_at": ref, "revenue": Decimal(receita), "net_income": Decimal(lucro),
            "free_cash_flow": None, "gross_profit": None, "operating_income": None,
            "eps_diluted": None, "operating_cash_flow": None, "capex": None}


def _detalhe():
    return {"AAA": {
        "precos": _precos(),
        "trimestres": [_trimestre(2026, 2, "1200000000", "240000000", date(2026, 6, 30)),
                       _trimestre(2025, 2, "1000000000", "200000000", date(2025, 6, 30))],
        "exercicios": [],
        "proventos": [{"ex_date": date(2024, 3, 1), "valor": Decimal("0.2")},
                      {"ex_date": date(2025, 3, 1), "valor": Decimal("0.2")},
                      {"ex_date": date(2026, 3, 1), "valor": Decimal("0.3")}],
    }}


def test_retornos_e_queda_maxima_saem_da_serie():
    serie = [{"date": HOJE - timedelta(days=3 - i), "preco": v, "close": v, "volume": 10}
             for i, v in enumerate([100, 120, 90, 99])]
    p = det.resumo_precos(serie)
    assert p["ultimo"] == 99 and p["max52"] == 120 and p["min52"] == 90
    assert p["dd12m"] == pytest.approx(90 / 120 - 1)
    assert p["do_topo"] == pytest.approx(99 / 120 - 1)
    assert p["r1m"] is None  # só 4 pregões: sem retorno de 21
    assert p["vol"] is None  # menos de 20 retornos não viram volatilidade


def test_preco_nulo_ou_zero_nao_entra_na_conta():
    serie = [{"date": HOJE - timedelta(days=2), "preco": 0, "close": 0, "volume": 0},
             {"date": HOJE - timedelta(days=1), "preco": None},
             {"date": HOJE, "preco": 50, "close": 50, "volume": 1}]
    assert det.resumo_precos(serie) is None


def test_proventos_somam_12_meses_e_contam_anos_seguidos():
    pv = det.resumo_proventos(_detalhe()["AAA"]["proventos"], HOJE)
    assert pv["soma12m"] == pytest.approx(0.3)
    assert pv["anos_seguidos"] == 2  # 2025 e 2024; 2026 ainda não fechou
    assert pv["ultimo_valor"] == pytest.approx(0.3)


def test_resumo_igual_com_datas_em_texto_do_tunel():
    """O túnel serializa datas e Decimal; o resumo não pode mudar por isso."""
    import json

    cru = _detalhe()
    via_tunel = json.loads(json.dumps(cru, default=srv._json_padrao))
    assert (det.resumo_para_prompt(cru, origem="x", hoje=HOJE)
            == det.resumo_para_prompt(via_tunel, origem="x", hoje=HOJE))


def test_trimestre_compara_com_o_mesmo_trimestre_do_ano_anterior():
    texto = det.resumo_para_prompt(_detalhe(), origem="x", hoje=HOJE)
    assert "FY2026Q2" in texto and "a/a receita +20.0%" in texto and "a/a lucro +20.0%" in texto


def test_ticker_sem_dado_diz_que_nao_tem():
    texto = det.resumo_para_prompt(
        {"ZZZ": {"precos": [], "trimestres": [], "exercicios": [], "proventos": []}},
        origem="x", hoje=HOJE)
    assert "não tem série recente" in texto
    assert "sem demonstrativo trimestral" in texto
    assert "nenhum registrado" in texto
    assert "não prova que não pagou" in texto  # ETF fora do universo não vira "não paga"


class _Conexao:
    def __init__(self, respostas):
        self.respostas = respostas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        texto = str(sql)
        chave = next(k for k in self.respostas if k in texto)
        linhas = self.respostas[chave]

        class _R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return linhas
        return _R()


class _Engine:
    def __init__(self, respostas):
        self.respostas = respostas

    def connect(self):
        return _Conexao(self.respostas)


def test_leitura_deduplica_periodo_e_data_ex():
    antigo, novo = datetime(2026, 1, 1), datetime(2026, 8, 1)
    base = {"symbol": "AAA", "period": "quarterly", "fiscal_year": 2026,
            "fiscal_quarter": 2, "reference_date": date(2026, 6, 30),
            "available_at": date(2026, 7, 30), **{c: None for c in det._CONTAS}}
    eng = _Engine({
        "income_statements": [{**base, "revenue": 1, "updated_at": antigo},
                              {**base, "revenue": 2, "updated_at": novo}],
        "prices_daily": [],
        "dividends": [
            {"symbol": "AAA", "ex_date": date(2026, 3, 1), "valor": 0.5, "ingested_at": antigo},
            {"symbol": "AAA", "ex_date": date(2026, 3, 1), "valor": 0.3, "ingested_at": novo}],
    })
    d = det.ler_detalhe(eng, ["aaa", "AAA", " bbb "], hoje=HOJE)
    assert list(d) == ["AAA", "BBB"]
    assert [t["revenue"] for t in d["AAA"]["trimestres"]] == [2]
    assert d["AAA"]["proventos"] == [{"ex_date": date(2026, 3, 1), "valor": 0.3}]
    assert d["BBB"] == {"precos": [], "trimestres": [], "exercicios": [], "proventos": []}


def test_rota_recusa_pedido_sem_simbolo_ou_largo_demais():
    assert srv.rota_eua_detalhe({})[0] == 400
    muitos = ",".join(f"T{i}" for i in range(det.SIMBOLOS_MAX + 1))
    assert srv.rota_eua_detalhe({"simbolos": [muitos]})[0] == 400


@pytest.fixture
def tunel(monkeypatch):
    ar._limpar_memoria()
    pedidos = []

    def _ler(_engine, simbolos, **_k):
        pedidos.append(list(simbolos))
        return {s: _detalhe()["AAA"] for s in simbolos}

    monkeypatch.setattr(det, "ler_detalhe", _ler)
    monkeypatch.setattr(srv, "_url_eua", lambda: "postgresql://ninguem@127.0.0.1:1/x")
    monkeypatch.setattr(srv, "engine_leitura", lambda url: object())
    http = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.fabricar_handler(TOKEN))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{http.server_address[1]}"
    monkeypatch.setattr(ar, "_config", lambda: (url, TOKEN))
    yield pedidos
    http.shutdown()
    http.server_close()
    ar._limpar_memoria()


def test_cliente_traz_detalhe_pelo_tunel_e_guarda(tunel):
    d = ar.detalhe_eua(["kO", "aapl"])
    assert set(d) == {"AAPL", "KO"}
    assert d["KO"]["precos"][0]["date"].startswith("2026-")
    ar.detalhe_eua(["AAPL", "KO"])  # mesma lista em outra ordem: memória
    assert tunel == [["AAPL", "KO"]]


def test_contexto_da_llm_diz_quando_o_tunel_nao_existe(monkeypatch):
    import core.llm_context_us as ctx
    import core.us_read as ur

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert "túnel não configurado" in ctx.get_warehouse_detail_context(["AAPL"])


def test_contexto_da_llm_diz_quando_o_tunel_caiu(monkeypatch):
    import core.llm_context_us as ctx
    import core.us_read as ur

    def _cai(_s):
        raise ar.ArmazemRemotoIndisponivel("sem resposta — PC ou túnel desligado?")

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "detalhe_eua", _cai)
    texto = ctx.get_warehouse_detail_context(["AAPL"])
    assert "indisponível agora" in texto and "PC ou túnel desligado" in texto


def test_contexto_da_llm_le_direto_no_armazem(monkeypatch):
    import core.llm_context_us as ctx
    import core.us_read as ur

    monkeypatch.setattr(ur, "_db_is_local", lambda: True)
    monkeypatch.setattr(ur, "_engine", lambda: object())
    monkeypatch.setattr(det, "ler_detalhe", lambda _e, s: {t: _detalhe()["AAA"] for t in s})
    monkeypatch.setattr(ar, "detalhe_eua", lambda _s: pytest.fail("não deveria usar o túnel"))
    texto = ctx.get_warehouse_detail_context(["msft", "MSFT", "", "a", "b", "c", "d", "e"])
    assert "lido direto" in texto and texto.count("    Preço") == ctx._MAX_DETALHE
