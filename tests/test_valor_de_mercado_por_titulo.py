"""Valor de mercado por título: preço da classe × ações emitidas ÷ ações por
título (data_pipeline/market/acoes.py) e os três caminhos que o usam.

Tickers e números sintéticos (repositório público).
"""
import pandas as pd
import pytest

from data_pipeline.market import acoes as ac
from data_pipeline.market import ingest as ig
from data_pipeline.market import normalize as nz


def _quote(tk, preco, acoes=None, lpa_titulo=None, lucro=None, mc=None, eps_topo=None):
    nome = ("Companhia Sintetica SA Units Cons of 1 Sh + 2 Pfd Shs"
            if tk.endswith("11") else "Companhia Sintetica SA")
    dks = {}
    if acoes is not None:
        dks["sharesOutstanding"] = acoes
    if lpa_titulo is not None:
        dks["trailingEps"] = lpa_titulo
    if lucro is not None:
        dks["netIncomeToCommon"] = lucro
    q = {"symbol": tk, "regularMarketPrice": preco, "defaultKeyStatistics": dks,
         "longName": nome}
    if mc is not None:
        q["marketCap"] = mc
    if eps_topo is not None:
        q["earningsPerShare"] = eps_topo
    return q


# ── a conta ───────────────────────────────────────────────────────────────────

def test_acao_usa_acoes_emitidas_e_nao_o_marketcap_da_brapi():
    # Duas classes, mesmas ações emitidas da companhia, preços diferentes: o
    # marketCap da brapi é um só (e errado nas duas).
    on = _quote("XPTO3", 10.0, acoes=1_000.0, mc=7_000.0)
    pn = _quote("XPTO4", 12.0, acoes=1_000.0, mc=7_000.0)
    assert ac.valor_de_mercado_do_quote(on, unit=False) == pytest.approx(10_000.0)
    assert ac.valor_de_mercado_do_quote(pn, unit=False) == pytest.approx(12_000.0)


def test_unit_divide_pelas_acoes_que_cada_unit_carrega():
    # 3.000 ações, lucro 3.000 → LPA por ação 1; LPA da unit 3 → k = 3.
    un = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    assert ac.acoes_por_titulo(un, unit=True) == 3
    assert ac.valor_de_mercado_do_quote(un, unit=True) == pytest.approx(30_000.0)


def test_k_com_prejuizo_tambem_fecha():
    un = _quote("XPTO11", 10.0, acoes=5_000.0, lpa_titulo=-5.0, lucro=-5_000.0)
    assert ac.acoes_por_titulo(un, unit=True) == 5


@pytest.mark.parametrize("lpa_titulo, lucro", [
    (None, 3_000.0),        # sem LPA da unit
    (3.0, None),            # sem lucro
    (3.0, 0.0),             # lucro zero: razão indefinida
    (2.6, 2_000.0),         # k = 3,9: não fecha inteiro
    (300.0, 3_000.0),       # k = 300: lixo de unidade
])
def test_unit_sem_k_confiavel_fica_sem_valor_de_mercado(lpa_titulo, lucro):
    un = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=lpa_titulo, lucro=lucro)
    assert ac.acoes_por_titulo(un, unit=True) is None
    # preço da unit × ações emitidas daria k vezes o valor: fica ausente
    assert ac.valor_de_mercado_do_quote(un, unit=True) is None


def test_unit_sem_k_usa_o_marketcap_da_companhia_se_a_brapi_der():
    un = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=2.6, lucro=2_000.0,
                mc=31_000.0)
    assert ac.valor_de_mercado_do_quote(un, unit=True) == 31_000.0


def test_acao_sem_acoes_emitidas_cai_no_marketcap_da_brapi():
    q = _quote("XPTO3", 10.0, mc=7_000.0)
    assert ac.valor_de_mercado_do_quote(q, unit=False) == 7_000.0


def test_acao_nao_deriva_k():
    q = _quote("XPTO3", 10.0, acoes=1_000.0, lpa_titulo=9.0, lucro=1_000.0)
    assert ac.acoes_por_titulo(q, unit=False) == 1


# ── caminho 1: normalizador (linha spot) ──────────────────────────────────────

def _spot(q):
    return {r["metric_name"]: r["metric_value"] for r in nz.metric_rows(q)}


def test_metric_rows_grava_valor_de_mercado_por_titulo_e_a_base():
    un = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    by = _spot(un)
    assert nz._infer_asset_type("XPTO11", un) == "unit"
    assert by["marketCap"] == pytest.approx(30_000.0)
    assert by["Acoes_Emitidas"] == 3_000.0
    assert by["Acoes_por_Titulo"] == 3.0

    on = _spot(_quote("XPTO3", 10.0, acoes=1_000.0, mc=7_000.0))
    assert on["marketCap"] == pytest.approx(10_000.0)
    assert on["Acoes_por_Titulo"] == 1.0


def test_metric_rows_unit_sem_k_nao_grava_marketcap_nem_k():
    by = _spot(_quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=2.6, lucro=2_000.0))
    assert "marketCap" not in by and "Acoes_por_Titulo" not in by
    assert by["Acoes_Emitidas"] == 3_000.0


def test_metric_rows_lpa_e_o_do_titulo():
    # earningsPerShare do topo do quote não é confiável por classe (na ação
    # da companhia com unit ele traz o LPA da unit); trailingEps é o do título.
    by = _spot(_quote("XPTO3", 10.0, acoes=3_000.0, lpa_titulo=1.0, lucro=3_000.0,
                      eps_topo=3.0))
    assert by["LPA"] == 1.0


# ── caminho 2: reprocess_metrics ──────────────────────────────────────────────

def test_reprocess_valor_de_mercado_prefere_preco_de_hoje_vezes_acoes():
    # Preço do armazém (mais novo que o quote) × ações ÷ k.
    assert ig._valor_de_mercado_atual(
        preco=33.0, acoes=3_000.0, por_titulo=3.0, mc_spot=30_000.0,
        unit=True, lucro_anual=3_000.0, lpa_anual=1.0) == pytest.approx(33_000.0)


def test_reprocess_unit_sem_k_nao_usa_o_reserva_por_lucro_e_lpa():
    # Antes: preço da unit × lucro / LPA por ação = 3x o valor.
    assert ig._valor_de_mercado_atual(
        preco=30.0, acoes=3_000.0, por_titulo=None, mc_spot=None,
        unit=True, lucro_anual=3_000.0, lpa_anual=1.0) is None


def test_reprocess_acao_sem_acoes_mantem_os_reservas():
    assert ig._valor_de_mercado_atual(
        preco=10.0, acoes=None, por_titulo=None, mc_spot=7_000.0,
        unit=False, lucro_anual=None, lpa_anual=None) == 7_000.0
    assert ig._valor_de_mercado_atual(
        preco=10.0, acoes=None, por_titulo=None, mc_spot=None,
        unit=False, lucro_anual=1_000.0, lpa_anual=1.0) == pytest.approx(10_000.0)


def test_reprocess_acoes_do_ano_da_unit_sao_por_unit():
    # lucro / LPA por ação = ações emitidas; o título é a unit, que carrega 3.
    acoes, lpa = ig._acoes_e_lpa_do_ano(lucro=3_000.0, lpa=1.0, por_titulo=3.0,
                                        acoes_atuais=1_000.0)
    assert acoes == pytest.approx(1_000.0)
    assert lpa == pytest.approx(3.0)
    # Ação: inalterado.
    acoes, lpa = ig._acoes_e_lpa_do_ano(lucro=3_000.0, lpa=1.0, por_titulo=1.0,
                                        acoes_atuais=3_000.0)
    assert (acoes, lpa) == (pytest.approx(3_000.0), 1.0)
    # Guarda 0,2–5x das ações atuais continua valendo.
    assert ig._acoes_e_lpa_do_ano(lucro=3_000.0, lpa=0.01, por_titulo=1.0,
                                  acoes_atuais=3_000.0) == (None, None)


# ── caminho 3: publish_valuation_historico ────────────────────────────────────

def test_historico_b3_unit_divide_por_k():
    from scripts import publish_valuation_historico as pub
    anos = [2024, 2025]
    fech = pd.DataFrame({"ticker": ["XPTO3"] * 2 + ["XPTO11"] * 2,
                         "ano": anos * 2, "close": [10.0, 10.0, 30.0, 30.0]})
    demos = pd.DataFrame({"ticker": ["XPTO3"] * 2 + ["XPTO11"] * 2,
                          "ano": anos * 2, "lucro": [3_000.0] * 4,
                          "lpa": [1.0] * 4, "patrimonio": [15_000.0] * 4})
    prov = pd.DataFrame({"ticker": [], "ano": [], "total": []})
    saida, _ = pub.historico_b3(fech, demos, prov, acoes_por_titulo={"XPTO11": 3})
    on, un = saida["XPTO3"]["historico"], saida["XPTO11"]["historico"]
    # A unit vale 3 ações: mesmo P/L e mesmo P/VP da ação.
    assert un["p_l"] == on["p_l"] == [[2024, 10.0], [2025, 10.0]]
    assert un["p_vp"] == on["p_vp"] == [[2024, 2.0], [2025, 2.0]]


def test_historico_b3_unit_sem_k_fica_sem_multiplo():
    from scripts import publish_valuation_historico as pub
    fech = pd.DataFrame({"ticker": ["XPTO11"], "ano": [2025], "close": [30.0]})
    demos = pd.DataFrame({"ticker": ["XPTO11"], "ano": [2025], "lucro": [3_000.0],
                          "lpa": [1.0], "patrimonio": [15_000.0]})
    prov = pd.DataFrame({"ticker": ["XPTO11"], "ano": [2025], "total": [1.5]})
    saida, excl = pub.historico_b3(fech, demos, prov, acoes_por_titulo={},
                                   units={"XPTO11"})
    h = saida["XPTO11"]["historico"]
    assert h["p_l"] == [] and h["p_vp"] == []
    # DY é preço contra provento do mesmo título: não depende de k.
    assert h["dividend_yield"] == [[2025, 5.0]]
    assert excl["unit_sem_acoes_por_titulo"] == 1


def test_porte_sai_de_preco_vezes_acoes_e_cobre_unit():
    from datetime import datetime, timezone

    from scripts import publish_valuation_historico as pub
    hoje = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    linhas = [{"ticker": "XPTO11", "fetched_at": hoje, "market_cap": 30_000.0,
               "origem": "acoes"},
              {"ticker": "XPTO3", "fetched_at": hoje, "market_cap": 10_000.0,
               "origem": "brapi"}]
    porte, _ = pub.valor_de_mercado_brapi(linhas, ["XPTO11", "XPTO3"], hoje)
    assert porte["XPTO11"]["porte"] == 30_000.0
    assert "ações emitidas" in porte["XPTO11"]["porte_ref"]
    assert porte["XPTO3"]["porte_ref"].startswith("valor de mercado da brapi")


def test_linhas_do_porte_usam_a_conta_unica():
    from datetime import datetime, timezone

    from scripts import publish_valuation_historico as pub
    hoje = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    fotos = [
        {"ticker": "XPTO11", "fetched_at": hoje,
         "payload": _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)},
        {"ticker": "XPTO3", "fetched_at": hoje,
         "payload": _quote("XPTO3", 10.0, acoes=1_000.0, mc=7_000.0)},
    ]
    linhas, k = pub.linhas_de_valor_de_mercado(fotos)
    by = {r["ticker"]: (r["market_cap"], r["origem"]) for r in linhas}
    assert by == {"XPTO11": (pytest.approx(30_000.0), "acoes"),
                  "XPTO3": (pytest.approx(10_000.0), "acoes")}
    assert k == {"XPTO11": 3, "XPTO3": 1}


def test_linhas_do_porte_unit_sem_k_fica_fora_e_marcada():
    from datetime import datetime, timezone

    from scripts import publish_valuation_historico as pub
    hoje = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    fotos = [{"ticker": "XPTO11", "fetched_at": hoje,
              "payload": _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=2.6,
                                lucro=2_000.0)},
             {"ticker": "XPTO3", "fetched_at": hoje,
              "payload": _quote("XPTO3", 10.0, mc=7_000.0)}]
    linhas, k = pub.linhas_de_valor_de_mercado(fotos)
    assert [(r["ticker"], r["origem"]) for r in linhas] == [("XPTO3", "brapi")]
    assert k == {"XPTO11": None, "XPTO3": 1}


# ── quote diário sem a composição (Codex, PR #710) ────────────────────────────
# O refresh diário pede o quote sem ``defaultKeyStatistics``. k e as ações
# emitidas saem do quote completo mais novo; o preço, do mais novo de todos.

def _diario(tk, preco):
    q = _quote(tk, preco)
    q.pop("defaultKeyStatistics")
    return q


def test_composicao_do_quote_completo_vale_no_quote_diario():
    completo = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    q = ac.quote_com_composicao(_diario("XPTO11", 33.0),
                                completo["defaultKeyStatistics"])
    assert ac.acoes_por_titulo(q, unit=True) == 3
    assert ac.valor_de_mercado_do_quote(q, unit=True) == pytest.approx(33_000.0)


def test_composicao_propria_do_quote_nao_e_trocada_pela_antiga():
    novo = _quote("XPTO11", 30.0, acoes=6_000.0, lpa_titulo=3.0, lucro=6_000.0)
    antigo = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=1_000.0)
    q = ac.quote_com_composicao(novo, antigo["defaultKeyStatistics"])
    assert ac.acoes_por_titulo(q, unit=True) == 3
    assert ac.acoes_emitidas(q) == 6_000.0


def test_metric_rows_do_quote_diario_usa_a_composicao_e_nao_o_lpa_antigo():
    completo = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    rows = nz.metric_rows(_diario("XPTO11", 33.0),
                          composicao=completo["defaultKeyStatistics"])
    by = {r["metric_name"]: r["metric_value"] for r in rows}
    assert by["Acoes_por_Titulo"] == 3
    assert by["Acoes_Emitidas"] == 3_000.0
    assert by["marketCap"] == pytest.approx(33_000.0)
    # O LPA é do quote que se normaliza; o da composição é de outra data.
    assert "LPA" not in by


def test_linhas_do_porte_quote_diario_mantem_k_da_ultima_composicao():
    from datetime import datetime, timezone

    from scripts import publish_valuation_historico as pub
    hoje = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    completo = _quote("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    fotos = [{"ticker": "XPTO11", "fetched_at": hoje,
              "payload": _diario("XPTO11", 33.0),
              "composicao": completo["defaultKeyStatistics"]}]
    linhas, k = pub.linhas_de_valor_de_mercado(fotos)
    assert k == {"XPTO11": 3}
    assert [(r["ticker"], r["market_cap"], r["origem"]) for r in linhas] == [
        ("XPTO11", pytest.approx(33_000.0), "acoes")]


# ── título 11 de tipo desconhecido (Codex, PR #710) ───────────────────────────
# Sufixo 11 sem perfil e sem nome que o classifique sai como "other". Pode ser
# unit: k vem da razão, nunca é 1 por suposição.

def _sem_classe(tk, preco, **kw):
    q = _quote(tk, preco, **kw)
    q["longName"] = "Companhia Sintetica UNT"
    return q


def test_titulo_11_de_tipo_desconhecido_deriva_k():
    q = _sem_classe("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    assert nz._infer_asset_type("XPTO11", q) == "other"
    by = _spot(q)
    assert by["Acoes_por_Titulo"] == 3
    assert by["marketCap"] == pytest.approx(30_000.0)


def test_titulo_11_de_tipo_desconhecido_sem_k_fica_sem_k():
    by = _spot(_sem_classe("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=2.6,
                           lucro=2_000.0))
    assert "Acoes_por_Titulo" not in by and "marketCap" not in by


def test_exige_k_na_unit_e_no_tipo_desconhecido():
    assert ac.exige_k("unit") and ac.exige_k("other")
    assert not ac.exige_k("stock") and not ac.exige_k("fii")
    # Sem tipo gravado: o sufixo 11 decide, como no publicador.
    assert ac.exige_k(None, "XPTO11") and not ac.exige_k(None, "XPTO3")


def test_linhas_do_porte_titulo_11_de_tipo_desconhecido_deriva_k():
    from datetime import datetime, timezone

    from scripts import publish_valuation_historico as pub
    hoje = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    q = _sem_classe("XPTO11", 30.0, acoes=3_000.0, lpa_titulo=3.0, lucro=3_000.0)
    _, k = pub.linhas_de_valor_de_mercado(
        [{"ticker": "XPTO11", "fetched_at": hoje, "payload": q}])
    assert k == {"XPTO11": 3}


# ── composição gravada em bloco (Codex, PR #710) ──────────────────────────────
# O upsert das linhas spot não apaga. Um quote completo cujo k não fecha não
# pode deixar o k antigo junto das ações novas.

class _Repo:
    def __init__(self):
        self.apagadas = []

    def append_metric_vintages(self, conn, rows):
        return 0

    def upsert(self, conn, tabela, rows):
        return len(rows)

    def apagar_composicao_omitida(self, conn, ticker, rows):
        self.apagadas.append((ticker, sorted(
            m for m in ac.METRICAS_DE_COMPOSICAO
            if m not in {r["metric_name"] for r in rows})))
        return 0


def test_quote_completo_sem_k_apaga_o_k_antigo(monkeypatch):
    fake = _Repo()
    monkeypatch.setattr(ig, "repo", fake)
    q = _quote("XPTO11", 30.0, acoes=6_000.0, lpa_titulo=2.6, lucro=2_000.0)
    ig._gravar_indicadores(None, nz.normalize_all(q), "XPTO11", q)
    assert fake.apagadas == [("XPTO11", ["Acoes_por_Titulo", "marketCap"])]


def test_quote_diario_sem_composicao_nao_apaga(monkeypatch):
    fake = _Repo()
    monkeypatch.setattr(ig, "repo", fake)
    q = _diario("XPTO11", 33.0)
    ig._gravar_indicadores(None, nz.normalize_all(q), "XPTO11", q)
    assert fake.apagadas == []


def test_apagar_composicao_omitida_so_apaga_o_que_faltou():
    from data_pipeline.market import repository as repo

    class _Conn:
        def __init__(self):
            self.chamadas = []

        def execute(self, sql, params):
            self.chamadas.append((str(sql), params))

            class _R:
                rowcount = 1
            return _R()

    conn = _Conn()
    linhas = [{"period": "spot", "metric_name": "Acoes_Emitidas"},
              {"period": "spot", "metric_name": "LPA"}]
    assert repo.apagar_composicao_omitida(conn, "xpto11", linhas) == 1
    (sql, params), = conn.chamadas
    assert "DELETE FROM market.calculated_metrics" in sql and "'spot'" in sql
    assert params == {"t": "XPTO11", "m": ["marketCap", "Acoes_por_Titulo"]}

    conn = _Conn()
    cheias = [{"period": "spot", "metric_name": m} for m in ac.METRICAS_DE_COMPOSICAO]
    assert repo.apagar_composicao_omitida(conn, "XPTO11", cheias) == 0
    assert conn.chamadas == []
