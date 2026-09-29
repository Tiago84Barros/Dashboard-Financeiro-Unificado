"""Detalhe por FII lido do armazém (direto ou pelo túnel).

Nada toca o Postgres: a leitura roda sobre uma conexão de mentira que devolve
linhas por consulta, e o servidor do túnel sobe em 127.0.0.1 com a leitura
trocada por dublê. O que se prende é o que faria a LLM ler número errado ou
achar que leu tudo: retorno contado em pregões numa série que é mensal em
trechos, provento em dobro somado calado, versão 6.10 perdendo para 6.9 na
ordem de texto, túnel fora do ar sumindo do prompt em silêncio.
"""
from __future__ import annotations

import json
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
import core.fii_detalhe_armazem as det  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

HOJE = date(2026, 9, 28)
REF = date(2026, 8, 1)
TOKEN = "t" * 40


def _mes(meses_antes: int) -> date:
    return det._meses_antes(REF, meses_antes)


def _serie():
    linhas = []
    for m in range(40):
        ref = _mes(m)
        linhas += [
            {"metrica": "nav_per_share", "ref": ref, "valor": Decimal("100") - m},
            {"metrica": "total_investors", "ref": ref, "valor": 10_000 - 10 * m},
            {"metrica": "equity", "ref": ref, "valor": Decimal("1500000000")},
            {"metrica": "leverage", "ref": ref, "valor": Decimal("0.10") if m < 12 else 0.05},
            {"metrica": "liquidity_ratio", "ref": ref, "valor": 0.03},
            {"metrica": "dy_patrimonial_mes", "ref": ref, "valor": 0.01 if m < 12 else 0.008},
        ]
    return linhas


#: 70 pregões de mercado até HOJE (dias úteis, sem feriado).
CALENDARIO = []
_d = HOJE
while len(CALENDARIO) < 70:
    if _d.weekday() < 5:
        CALENDARIO.insert(0, _d)
    _d -= timedelta(days=1)


def _pregoes(dias, volume=Decimal("1000000"), negocios=500):
    return [{"date": d, "fechamento": Decimal("99"), "negocios": negocios,
             "volume": volume} for d in dias]


def _detalhe():
    return {"HGLG11": {
        "pregoes": _pregoes(CALENDARIO),
        "calendario": list(CALENDARIO),
        "serie": _serie(),
        "precos": [{"date": HOJE - timedelta(days=400), "preco": Decimal("80")},
                   {"date": HOJE - timedelta(days=200), "preco": Decimal("100")},
                   {"date": HOJE - timedelta(days=100), "preco": Decimal("90")},
                   {"date": HOJE - timedelta(days=5), "preco": Decimal("99")}],
        "proventos": [{"data_com": date(2026, 8, 29), "pagamento": date(2026, 9, 14),
                       "tipo": "Rendimento", "valor": Decimal("1.10")},
                      {"data_com": date(2025, 8, 29), "pagamento": None,
                       "tipo": "Rendimento", "valor": Decimal("1.00")}],
        "composicao": [{"tipo": "issuer", "nome": "12345678000190", "peso": 0.4,
                        "ref": date(2026, 6, 30), "fonte": "cvm", "itens_no_tipo": 10}],
        "imoveis": [{"nome_imovel": "Galpão A", "area_m2": 100, "vacancia": 0.1,
                     "pct_receita": 0.6},
                    {"nome_imovel": "Galpão B", "area_m2": 300, "vacancia": 0.0,
                     "pct_receita": 0.4}],
        "score": [{"ref": _mes(12 - i), "versao": "6.10.0", "tipo": "logistica",
                   "score": 60 + i, "confianca": 0.8} for i in range(13)],
    }}


def test_serie_do_informe_compara_12_e_36_meses_atras():
    s = det.resumo_serie(_serie())
    assert s["ref"] == REF and s["vpa"] == 100
    assert s["vpa12"] == pytest.approx(100 / 88 - 1)
    assert s["vpa36"] == pytest.approx(100 / 64 - 1)
    assert s["alavancagem"] == pytest.approx(0.10) and s["alavancagem12"] == pytest.approx(0.05)
    assert s["dy12m"] == pytest.approx(0.12) and s["dy12m_anterior"] == pytest.approx(0.096)


def test_rendimento_12m_exige_os_12_meses():
    """Com um mês faltando, somar 11 subestimaria o rendimento sem avisar."""
    serie = [r for r in _serie() if not (r["metrica"] == "dy_patrimonial_mes"
                                         and r["ref"] == _mes(5))]
    s = det.resumo_serie(serie)
    assert s["dy12m"] is None and s["meses_dy"] == 11


def test_retorno_de_preco_conta_por_data_e_nao_por_pregao():
    """Série mensal em trechos: 4 pontos ainda dão retorno de 3 e 12 meses."""
    p = det.resumo_precos(_detalhe()["HGLG11"]["precos"])
    assert p["ultimo"] == 99
    assert p["r3m"] == pytest.approx(99 / 90 - 1)  # 95 dias antes: o ponto de 100 dias
    assert p["r12m"] == pytest.approx(99 / 80 - 1)  # 360 dias antes: o de 400
    assert p["max52"] == 100 and p["min52"] == 90
    assert p["dd12m"] == pytest.approx(90 / 100 - 1)


def test_provento_com_dois_valores_na_mesma_data_e_denunciado():
    proventos = [{"data_com": date(2026, 5, 29), "valor": 1.0},
                 {"data_com": date(2026, 5, 29), "valor": 1.2},
                 {"data_com": date(2026, 4, 30), "valor": 1.0}]
    pv = det.resumo_proventos(proventos, HOJE)
    assert pv["soma12m"] == pytest.approx(3.2) and pv["datas_duplas"] == [date(2026, 5, 29)]
    texto = det.resumo_para_prompt({"X": {"proventos": proventos}}, origem="x", hoje=HOJE)
    assert "ATENÇÃO: 1 data(s)-com com dois valores" in texto


def test_cnpj_sai_pontuado_e_zero_vira_nao_identificado():
    assert det._nome_exposicao("12345678000190") == "12.345.678/0001-90"
    assert det._nome_exposicao("0") == "não identificado"
    assert det._nome_exposicao(" Logística ") == "Logística"


def test_versao_compara_numeros_e_nao_texto():
    assert max(["6.9.0", "6.10.0", "6.2.1"], key=det._versao) == "6.10.0"


def test_resumo_igual_com_datas_em_texto_do_tunel():
    """O túnel serializa datas e Decimal; o resumo não pode mudar por isso."""
    cru = _detalhe()
    via_tunel = json.loads(json.dumps(cru, default=srv._json_padrao))
    assert (det.resumo_para_prompt(cru, origem="x", hoje=HOJE)
            == det.resumo_para_prompt(via_tunel, origem="x", hoje=HOJE))


def test_prompt_traz_cada_bloco_do_fundo():
    texto = det.resumo_para_prompt(_detalhe(), origem="x", hoje=HOJE)
    assert "Informe mensal CVM (ref. 08/2026): VPA R$ 100.00" in texto
    assert "P/VP 0.99" in texto
    assert "série do armazém parada" not in texto  # 5 dias: ainda fresca
    assert "12.345.678/0001-90 40.0%" in texto and "(+9 menores)" in texto
    assert "vacância física ponderada pela área 2.5%" in texto
    assert "metodologia 6.10.0" in texto and "08/2026 72.0" in texto
    assert "02/2026 66.0" in texto and "08/2025 60.0" in texto


def test_preco_velho_e_avisado():
    d = _detalhe()
    d["HGLG11"]["precos"] = [{"date": date(2026, 6, 1), "preco": 90},
                             {"date": date(2026, 9, 1), "preco": 95}]
    assert "27 dias atrás — série do armazém parada" in det.resumo_para_prompt(
        d, origem="x", hoje=HOJE)


def test_fundo_sem_dado_diz_que_nao_tem():
    texto = det.resumo_para_prompt({"XXXX11": {}}, origem="x", hoje=HOJE)
    assert "não tem série deste fundo" in texto
    assert "não tem série recente" in texto
    assert "nenhum registrado" in texto


def test_liquidez_conta_pregao_sem_negocio_como_zero():
    # Negocia só nos pregões pares: a mediana dos 21 é zero, não o volume do dia.
    negociados = CALENDARIO[-21::2]
    liq = det.resumo_liquidez(_pregoes(negociados), CALENDARIO)
    assert liq["j21"]["negociados"] == 11 and liq["j21"]["volume_mediano"] == 1_000_000
    negociados = CALENDARIO[-20::2]
    liq = det.resumo_liquidez(_pregoes(negociados), CALENDARIO)
    assert liq["j21"]["negociados"] == 10 and liq["j21"]["volume_mediano"] == 0
    assert liq["j21"]["volume_total"] == 10_000_000
    assert liq["ultimo_pregao"] == HOJE and liq["ultimo_negocio"] == CALENDARIO[-2]


def test_liquidez_janela_curta_na_fita_nao_vira_numero():
    liq = det.resumo_liquidez(_pregoes(CALENDARIO[-30:]), CALENDARIO[-30:])
    assert liq["j21"]["negociados"] == 21 and liq["j63"] is None


def test_prompt_traz_liquidez_e_avisa_ultimo_negocio_antigo():
    d = _detalhe()
    d["HGLG11"]["pregoes"] = _pregoes(CALENDARIO[:-3])
    texto = det.resumo_para_prompt(d, origem="x", hoje=HOJE)
    assert ("Liquidez na B3 (COTAHIST até o pregão de 28/09/2026): 21 pregões: "
            "volume mediano R$ 1.0 mi/dia (total R$ 18.0 mi), 500 negócios/dia, "
            "negociado em 18 de 21") in texto
    assert f"último negócio em {CALENDARIO[-4]:%d/%m/%Y}" in texto
    assert "fita do armazém parada" not in texto


def test_fita_parada_e_avisada():
    d = _detalhe()
    d["HGLG11"]["calendario"] = CALENDARIO[:-10]
    assert "dias atrás — fita do armazém parada" in det.resumo_para_prompt(
        d, origem="x", hoje=HOJE)


def test_liquidez_ausente_distingue_servidor_antigo_de_fita_vazia():
    d = _detalhe()
    del d["HGLG11"]["calendario"]
    assert "versão antiga do código" in det.resumo_para_prompt(d, origem="x", hoje=HOJE)
    d["HGLG11"]["calendario"] = []
    assert "não tem a fita do COTAHIST" in det.resumo_para_prompt(d, origem="x", hoje=HOJE)
    d["HGLG11"]["calendario"], d["HGLG11"]["pregoes"] = CALENDARIO, []
    assert "nenhum negócio no recorte" in det.resumo_para_prompt(d, origem="x", hoje=HOJE)


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


def _score(versao, ref, score, publicado):
    return {"ticker": "HGLG11", "methodology_version": versao, "reference_date": ref,
            "available_at": publicado, "fii_type": "logistica", "type_score": score,
            "confidence": 0.8, "coverage": 1.0, "data_readiness_status": "ok"}


def test_leitura_fica_com_a_versao_mais_nova_e_a_ultima_publicacao_do_mes():
    antigo, novo = datetime(2026, 8, 1), datetime(2026, 9, 1)
    comp = [{"ticker": "HGLG11", "exposure_type": "sector", "exposure_name": f"S{i}",
             "exposure_weight": i / 100, "reference_date": date(2026, 6, 30),
             "source": "cvm"} for i in range(1, 11)]
    eng = _Engine({
        "fii_metric_observations": [], "historical_prices": [], "dividends": [],
        "fii_exposures": comp, "fii_imoveis": [],
        "financial_volume": [{"ticker": "HGLG11", "trade_date": HOJE, "close": 99,
                              "trades": 10, "financial_volume": 5}],
        "AS pregao": [{"pregao": HOJE - timedelta(days=1)}, {"pregao": HOJE}],
        "fii_pit_score_snapshots": [
            _score("6.9.0", date(2026, 8, 31), 90, novo),
            _score("6.10.0", date(2026, 8, 31), 70, antigo),
            _score("6.10.0", date(2026, 8, 31), 72, novo),
            _score("6.10.0", date(2026, 7, 31), 71, antigo)],
    })
    d = det.ler_detalhe(eng, ["hglg11", "HGLG11", " knri11 "], hoje=HOJE)
    assert list(d) == ["HGLG11", "KNRI11"]
    assert [(s["ref"], s["score"], s["versao"]) for s in d["HGLG11"]["score"]] == [
        (date(2026, 7, 31), 71, "6.10.0"), (date(2026, 8, 31), 72, "6.10.0")]
    setor = d["HGLG11"]["composicao"]
    assert len(setor) == det.ITENS_COMPOSICAO and setor[0]["nome"] == "S10"
    assert {c["itens_no_tipo"] for c in setor} == {10}
    assert d["HGLG11"]["pregoes"] == [{"date": HOJE, "fechamento": 99, "negocios": 10,
                                       "volume": 5}]
    # O calendário é do mercado: o fundo sem negócio também o recebe.
    assert d["KNRI11"] == {"serie": [], "precos": [], "proventos": [], "composicao": [],
                           "imoveis": [], "score": [], "pregoes": [],
                           "calendario": [HOJE - timedelta(days=1), HOJE]}


def test_rota_recusa_pedido_sem_ticker_ou_largo_demais():
    assert srv.rota_fii_detalhe({})[0] == 400
    muitos = ",".join(f"T{i}11" for i in range(det.TICKERS_MAX + 1))
    assert srv.rota_fii_detalhe({"tickers": [muitos]})[0] == 400


def test_rota_sem_armazem_responde_503(monkeypatch):
    monkeypatch.setattr(srv, "_url_eua", lambda: "")
    assert srv.rota_fii_detalhe({"tickers": ["HGLG11"]})[0] == 503


@pytest.fixture
def tunel(monkeypatch):
    ar._limpar_memoria()
    pedidos = []

    def _ler(_engine, tickers, **_k):
        pedidos.append(list(tickers))
        return {t: _detalhe()["HGLG11"] for t in tickers}

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
    d = ar.detalhe_fii(["knri11", "HGLG11"])
    assert set(d) == {"HGLG11", "KNRI11"}
    assert d["HGLG11"]["precos"][0]["date"].startswith("2025-")
    assert "VPA R$ 100.00" in det.resumo_para_prompt(d, origem="x", hoje=HOJE)
    ar.detalhe_fii(["KNRI11", "hglg11"])  # mesma lista em outra ordem: memória
    assert tunel == [["HGLG11", "KNRI11"]]


def test_contexto_da_llm_diz_quando_o_tunel_nao_existe(monkeypatch):
    import core.llm_context_fii as ctx
    import core.us_read as ur

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert "túnel não configurado" in ctx.get_warehouse_detail_context(["HGLG11"])


def test_contexto_da_llm_diz_quando_o_tunel_caiu(monkeypatch):
    import core.llm_context_fii as ctx
    import core.us_read as ur

    def _cai(_t):
        raise ar.ArmazemRemotoIndisponivel("sem resposta — PC ou túnel desligado?")

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "detalhe_fii", _cai)
    texto = ctx.get_warehouse_detail_context(["HGLG11"])
    assert "indisponível agora" in texto and "PC ou túnel desligado" in texto


def test_contexto_da_llm_le_direto_e_diz_quem_ficou_de_fora(monkeypatch):
    import core.llm_context_fii as ctx
    import core.us_read as ur

    monkeypatch.setattr(ur, "_db_is_local", lambda: True)
    monkeypatch.setattr(ur, "_engine", lambda: object())
    monkeypatch.setattr(det, "ler_detalhe", lambda _e, t: {x: _detalhe()["HGLG11"] for x in t})
    monkeypatch.setattr(ar, "detalhe_fii", lambda _t: pytest.fail("não deveria usar o túnel"))
    pedidos = ["hglg11", "HGLG11", "", "A11", "B11", "C11", "D11", "E11", "F11"]
    texto = ctx.get_warehouse_detail_context(pedidos)
    assert "lido direto" in texto and texto.count("    Preço") == ctx._MAX_DETALHE
    assert "detalhe limitado a 6 fundos; sem detalhe: F11" in texto


def test_citados_na_pergunta_vem_antes_dos_de_maior_peso():
    import core.llm_context_fii as ctx

    itens = [{"ticker": "KNRI11", "weight": 0.1}, {"ticker": "hglg11", "weight": 0.3},
             {"ticker": "MXRF11", "weight": "0.2"}, {"ticker": " ", "weight": 0.9}]
    ordem = ctx.tickers_para_detalhe("E o KNRI11, vale a pena?", itens)
    assert ordem == ["KNRI11", "HGLG11", "MXRF11"]


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
        d = det.ler_detalhe(engine, ["HGLG11", "XXXX11"])
    finally:
        engine.dispose()
    assert d["HGLG11"]["serie"] and d["HGLG11"]["composicao"]
    assert d["HGLG11"]["pregoes"] and len(d["HGLG11"]["calendario"]) >= 63
    assert d["XXXX11"]["calendario"] == d["HGLG11"]["calendario"]
    assert not any(v for k, v in d["XXXX11"].items() if k != "calendario")
    texto = det.resumo_para_prompt(d, origem="x")
    assert "HGLG11:" in texto and "VPA R$" in texto
