"""Detalhe por ação da B3 lido do armazém (direto ou pelo túnel).

Nada toca o Postgres: a leitura roda sobre uma conexão de mentira que devolve
linhas por consulta, e o servidor do túnel sobe em 127.0.0.1 com a leitura
trocada por dublê. O que se prende é o que faria a LLM ler número errado ou
achar que leu tudo: desdobramento do preço bruto lido como queda de 50 %,
retorno de 12 meses calculado com 3 meses de série, túnel fora do ar sumindo do
prompt em silêncio.
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_remoto as ar  # noqa: E402
import core.b3_detalhe_armazem as det  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

HOJE = date(2026, 9, 28)
TOKEN = "t" * 40
SPLIT = date(2026, 3, 2)


def _pregoes(fim: date = HOJE - timedelta(days=3), dias: int = 390):
    """Dias úteis com +0,1 %/dia e um desdobramento 2:1 em :data:`SPLIT`."""
    linhas, preco, d = [], 20.0, fim - timedelta(days=dias)
    while d <= fim:
        if d.weekday() < 5:
            preco *= 1.001
            if d == SPLIT:
                preco /= 2
            linhas.append({"date": d, "preco": Decimal(str(round(preco, 6))),
                           "negocios": 1000, "volume": Decimal("5000000")})
        d += timedelta(days=1)
    return linhas


def _evento(data: date, **extra):
    base = {"data": data, "pregao_zero": data, "tipo": "resultado_anual",
            "retornos": {"1": -0.02, "5": 0.01, "20": 0.03, "60": -0.5},
            "drawdown": -0.55, "pregoes_ate_o_pior": 40, "pregoes_ate_recuperar": None,
            "recuperou": False, "razao_volume": 1.25, "deriva_pre": 0.04}
    return {**base, **extra}


def _detalhe():
    return {"WEGE3": {
        "nome": "WEG",
        "pregoes": _pregoes(),
        "eventos": [_evento(date(2026, 1, 20)),
                    _evento(date(2025, 2, 20), retornos={"1": 0.01, "5": 0.02,
                                                          "20": 0.03, "60": 0.04},
                            drawdown=-0.08, recuperou=True, pregoes_ate_recuperar=30)],
        "saltos": [{"date": SPLIT, "ret": Decimal("-0.4995")}],
    }}


def test_desdobramento_sai_do_retorno_e_da_volatilidade():
    p = det.resumo_pregoes(_detalhe()["WEGE3"]["pregoes"])
    assert [d for d, _ in p["saltos"]] == [SPLIT]
    # 12 meses de +0,1 %/pregão, sem o -50 % do desdobramento.
    assert p["r12m"] == pytest.approx(1.001 ** (p["pregoes_ano"] - 2) - 1, rel=0.02)
    assert p["r12m"] > 0.2
    assert p["vol_anual"] == pytest.approx(0.0, abs=1e-6)


def test_liquidez_usa_mediana_das_ultimas_janelas():
    pregoes = _detalhe()["WEGE3"]["pregoes"]
    pregoes[-1]["volume"] = Decimal("9000000000")  # um bloco não move a mediana
    p = det.resumo_pregoes(pregoes)
    assert p["vol21"] == 5_000_000 and p["vol63"] == 5_000_000
    assert p["negocios21"] == 1000
    assert 60 <= p["pregoes_3m"] <= 67


def test_retorno_de_12_meses_exige_serie_de_12_meses():
    curta = _pregoes(dias=100)
    p = det.resumo_pregoes(curta)
    assert p["r12m"] is None and p["r1m"] is not None


def test_horizonte_que_atravessa_salto_e_omitido():
    ev = _detalhe()["WEGE3"]["eventos"][0]  # 20/01, desdobramento em 02/03
    linha = det._linha_evento(ev, [SPLIT])
    assert "1d -2.0%" in linha and "5d +1.0%" in linha and "20d +3.0%" in linha
    assert "60d -50" not in linha and "[60d omitido(s)" in linha
    assert "pior queda omitida" in linha
    assert "20 pregões antes +4.0%" in linha


def test_salto_antes_do_evento_tira_so_a_deriva():
    ev = _evento(date(2026, 3, 20), retornos={"1": 0.01})
    linha = det._linha_evento(ev, [SPLIT])
    assert "1d +1.0%" in linha and "20 pregões antes" not in linha
    assert "omitido" not in linha


def test_evento_sem_salto_mostra_tudo():
    ev = _detalhe()["WEGE3"]["eventos"][1]
    linha = det._linha_evento(ev, [SPLIT])
    assert "60d +4.0%" in linha and "recuperou em 30 pregões" in linha
    assert "volume 1.2× o anterior" in linha


def test_resumo_igual_com_datas_em_texto_do_tunel():
    """O túnel serializa datas e Decimal; o resumo não pode mudar por isso."""
    cru = _detalhe()
    via_tunel = json.loads(json.dumps(cru, default=srv._json_padrao))
    assert (det.resumo_para_prompt(cru, origem="x", hoje=HOJE)
            == det.resumo_para_prompt(via_tunel, origem="x", hoje=HOJE))


def test_prompt_traz_liquidez_preco_e_eventos():
    texto = det.resumo_para_prompt(_detalhe(), origem="x", hoje=HOJE)
    assert "WEGE3 (WEG):" in texto
    assert "R$ 5.0 mi/dia em 21 pregões" in texto
    assert "série do armazém parada" not in texto
    assert "excluídos 02/03/2026 (-49.9%)" in texto
    assert "índice de referência dela tem defeito conhecido" in texto
    assert texto.count("      ") == 2


def test_pregao_velho_e_avisado():
    d = _detalhe()
    d["WEGE3"]["pregoes"] = _pregoes(fim=date(2026, 9, 1))
    assert "27 dias atrás — série do armazém parada" in det.resumo_para_prompt(
        d, origem="x", hoje=HOJE)


def test_acao_sem_dado_diz_que_nao_tem():
    texto = det.resumo_para_prompt({"XXXX3": {}}, origem="x", hoje=HOJE)
    assert "não tem negócios deste papel" in texto
    assert "não tem eventos medidos" in texto


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


def test_leitura_monta_linhas_curtas_por_ticker():
    eng = _Engine({
        "LAG(": [{"ticker": "WEGE3", "trade_date": SPLIT, "ret": Decimal("-0.5")}],
        "tipo_evento": [{
            "simbolo": "WEGE3", "data_evento": date(2026, 2, 25),
            "data_pregao_zero": date(2026, 2, 26), "tipo_evento": "resultado_anual",
            "drawdown": Decimal("-0.1"), "pregoes_ate_o_pior": 5,
            "pregoes_ate_recuperar": 10, "recuperacao_observada": True,
            "razao_volume": Decimal("1.1"), "deriva_pre_evento": None,
            "janelas": {"1": {"retorno_ativo": 0.01, "retorno_anormal": 9.9},
                        "60": {"retorno_ativo": None}}}],
        "trades": [{"ticker": "WEGE3", "trade_date": date(2026, 9, 1),
                    "issuer_short_name": "WEG", "preco": Decimal("49.7"),
                    "trades": 19974, "financial_volume": Decimal("397600000")}],
    })
    d = det.ler_detalhe(eng, ["wege3", "WEGE3", " petr4 "], hoje=HOJE)
    assert list(d) == ["WEGE3", "PETR4"]
    assert d["WEGE3"]["nome"] == "WEG"
    assert d["WEGE3"]["pregoes"] == [{"date": date(2026, 9, 1), "preco": Decimal("49.7"),
                                      "negocios": 19974, "volume": Decimal("397600000")}]
    ev = d["WEGE3"]["eventos"][0]
    # Só o retorno do próprio papel: o anormal depende do índice com defeito.
    assert ev["retornos"] == {"1": 0.01, "5": None, "20": None, "60": None}
    assert d["WEGE3"]["saltos"] == [{"date": SPLIT, "ret": Decimal("-0.5")}]
    assert d["PETR4"] == {"nome": None, "pregoes": [], "eventos": [], "saltos": []}


def test_rota_recusa_pedido_sem_ticker_ou_largo_demais():
    assert srv.rota_b3_detalhe({})[0] == 400
    muitos = ",".join(f"T{i}3" for i in range(det.TICKERS_MAX + 1))
    assert srv.rota_b3_detalhe({"tickers": [muitos]})[0] == 400


def test_rota_sem_armazem_responde_503(monkeypatch):
    monkeypatch.setattr(srv, "_url_eua", lambda: "")
    assert srv.rota_b3_detalhe({"tickers": ["WEGE3"]})[0] == 503


@pytest.fixture
def tunel(monkeypatch):
    ar._limpar_memoria()
    pedidos = []

    def _ler(_engine, tickers, **_k):
        pedidos.append(list(tickers))
        return {t: _detalhe()["WEGE3"] for t in tickers}

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
    d = ar.detalhe_b3(["petr4", "WEGE3"])
    assert set(d) == {"WEGE3", "PETR4"}
    assert d["WEGE3"]["pregoes"][0]["date"].startswith("2025-")
    assert "excluídos 02/03/2026" in det.resumo_para_prompt(d, origem="x", hoje=HOJE)
    ar.detalhe_b3(["PETR4", "wege3"])  # mesma lista em outra ordem: memória
    assert tunel == [["PETR4", "WEGE3"]]


def test_contexto_da_llm_diz_quando_o_tunel_nao_existe(monkeypatch):
    import core.llm_context_b3 as ctx
    import core.us_read as ur

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert "túnel não configurado" in ctx.get_warehouse_detail_context(["WEGE3"])


def test_contexto_da_llm_diz_quando_o_tunel_caiu(monkeypatch):
    import core.llm_context_b3 as ctx
    import core.us_read as ur

    def _cai(_t):
        raise ar.ArmazemRemotoIndisponivel("sem resposta — PC ou túnel desligado?")

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "detalhe_b3", _cai)
    texto = ctx.get_warehouse_detail_context(["WEGE3"])
    assert "indisponível agora" in texto and "PC ou túnel desligado" in texto


def test_contexto_da_llm_le_direto_e_diz_quem_ficou_de_fora(monkeypatch):
    import core.llm_context_b3 as ctx
    import core.us_read as ur

    monkeypatch.setattr(ur, "_db_is_local", lambda: True)
    monkeypatch.setattr(ur, "_engine", lambda: object())
    monkeypatch.setattr(det, "ler_detalhe", lambda _e, t: {x: _detalhe()["WEGE3"] for x in t})
    monkeypatch.setattr(ar, "detalhe_b3", lambda _t: pytest.fail("não deveria usar o túnel"))
    pedidos = ["wege3", "WEGE3.SA", "", "A3", "B3", "C3", "D3", "E3", "F3"]
    texto = ctx.get_warehouse_detail_context(pedidos)
    assert "lido direto" in texto and texto.count("    Liquidez") == ctx._MAX_DETALHE
    assert "detalhe limitado a 6 ações; sem detalhe: F3" in texto


def test_citados_vem_antes_da_carteira_por_peso():
    import core.llm_context_b3 as ctx

    ordem = ctx.tickers_para_detalhe(
        ["VALE3", "wege3"], ["ITUB4", "WEGE3", "PETR4.SA"],
        {"ITUB4": 0.1, "WEGE3": 0.3, "PETR4.SA": "0.2"})
    assert ordem == ["VALE3", "WEGE3", "PETR4", "ITUB4"]
    assert ctx.tickers_para_detalhe([], ["B", "A"], None) == ["B", "A"]


def test_chat_da_carteira_recebe_o_bloco(monkeypatch):
    import core.conjuntura as conj
    import core.llm_context_b3 as ctx

    pedidos = []
    monkeypatch.setattr(ctx, "get_warehouse_detail_context",
                        lambda t: pedidos.append(list(t)) or "DETALHE DO ARMAZÉM LOCAL (x)")
    monkeypatch.setattr(ctx, "get_chunks_context", lambda *a, **k: "")
    monkeypatch.setattr(conj, "bloco_para_prompt", lambda **k: "")
    contexto, _ = ctx.build_llm_context_for_portfolio_chat(
        "e a VALE3?", "BASE", {"items": []}, {"WEGE3": 0.6, "ITUB4": 0.4},
        portfolio_tickers=["ITUB4", "WEGE3"])
    assert "DETALHE DO ARMAZÉM LOCAL (x)" in contexto
    assert pedidos == [["VALE3", "WEGE3", "ITUB4"]]


def test_sql_roda_no_armazem_de_verdade():
    """As consultas só se provam executando; só leitura, pulado sem armazém."""
    try:
        from sqlalchemy import create_engine, text

        from scripts.publish_fii_selection_from_local import _warehouse_url
        engine = create_engine(_warehouse_url())
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")
    try:
        d = det.ler_detalhe(engine, ["WEGE3", "XXXX3"])
    finally:
        engine.dispose()
    assert d["WEGE3"]["pregoes"] and d["WEGE3"]["eventos"]
    assert not any(d["XXXX3"].values())
    texto = det.resumo_para_prompt(d, origem="x")
    assert "WEGE3 (WEG" in texto and "Liquidez" in texto
