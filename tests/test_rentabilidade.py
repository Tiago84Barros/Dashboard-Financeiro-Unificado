"""TIR, comparação com o CDI (PME) e conciliação do universo medido."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from core import rentabilidade as rt

# ── TIR ───────────────────────────────────────────────────────────────────────


def test_tir_de_um_ano_exato():
    fl = [(date(2024, 1, 1), -100.0), (date(2024, 12, 31), 110.0)]
    # 365 dias corridos = 1 ano na base da função.
    assert rt.xirr(fl) == pytest.approx(0.10, abs=1e-6)


def test_tir_de_dois_anos_compostos():
    fl = [(date(2020, 1, 1), -100.0), (date(2020, 1, 1) + timedelta(days=730), 121.0)]
    assert rt.xirr(fl) == pytest.approx(0.10, abs=1e-6)


def test_tir_ignora_ordem_de_entrada_e_zeros():
    fl = [(date(2024, 12, 31), 110.0), (date(2024, 6, 1), 0.0), (date(2024, 1, 1), -100.0)]
    assert rt.xirr(fl) == pytest.approx(0.10, abs=1e-6)


def test_tir_sem_troca_de_sinal_nao_existe():
    assert rt.xirr([(date(2024, 1, 1), -100.0), (date(2024, 6, 1), -50.0)]) is None
    assert rt.xirr([]) is None


def test_tir_negativa():
    fl = [(date(2024, 1, 1), -100.0), (date(2024, 12, 31), 80.0)]
    assert rt.xirr(fl) == pytest.approx(-0.20, abs=1e-6)


# ── CDI ───────────────────────────────────────────────────────────────────────


def _cdi_constante(inicio: date, fim: date, taxa_dia: float) -> dict[date, float]:
    out = {}
    d = inicio
    while d <= fim:
        if d.weekday() < 5:
            out[d] = taxa_dia
        d += timedelta(days=1)
    return out


def test_carteira_identica_ao_cdi_da_pme_um_e_diferenca_zero():
    ini, fim = date(2024, 1, 1), date(2025, 1, 1)
    cdi = _cdi_constante(ini, fim, 0.04)
    fator = rt.fator_cdi(cdi, ini, fim)
    fl = [(ini, -1000.0)]
    comp = rt.comparar_com_cdi(fl, 1000.0 * fator, fim, cdi)
    assert comp["cobertura_cdi"] is True
    assert comp["pme"] == pytest.approx(1.0, abs=1e-9)
    assert comp["diferenca"] == pytest.approx(0.0, abs=1e-6)
    assert comp["tir_carteira"] == pytest.approx(comp["tir_cdi"], abs=1e-6)


def test_venda_no_meio_sai_da_conta_cdi():
    ini, meio, fim = date(2024, 1, 1), date(2024, 7, 1), date(2025, 1, 1)
    cdi = _cdi_constante(ini, fim, 0.04)
    fl = [(ini, -1000.0), (meio, 500.0)]
    comp = rt.comparar_com_cdi(fl, 0.0, fim, cdi)
    esperado = 1000.0 * rt.fator_cdi(cdi, ini, fim) - 500.0 * rt.fator_cdi(cdi, meio, fim)
    assert comp["valor_cdi"] == pytest.approx(esperado)
    assert comp["diferenca"] == pytest.approx(-esperado)


def test_pme_existe_mesmo_com_saldo_cdi_negativo():
    # Vendeu com lucro enorme cedo: a conta CDI fica negativa, a TIR dela não
    # existe, mas o PME continua dizendo quem ganhou.
    ini, meio, fim = date(2024, 1, 1), date(2024, 2, 1), date(2025, 1, 1)
    cdi = _cdi_constante(ini, fim, 0.04)
    comp = rt.comparar_com_cdi([(ini, -100.0), (meio, 300.0)], 0.0, fim, cdi)
    assert comp["tir_cdi"] is None
    assert comp["pme"] > 1.0


def test_sem_serie_do_cdi_nao_inventa_comparacao():
    comp = rt.comparar_com_cdi([(date(2024, 1, 1), -100.0)], 110.0, date(2024, 12, 31), {})
    assert comp["tir_carteira"] == pytest.approx(0.10, abs=1e-6)
    assert comp["cobertura_cdi"] is False
    assert comp["pme"] is None and comp["valor_cdi"] is None


def test_serie_curta_demais_nao_cobre_o_periodo():
    ini, fim = date(2020, 1, 1), date(2025, 1, 1)
    cdi = _cdi_constante(date(2023, 1, 1), fim, 0.04)
    comp = rt.comparar_com_cdi([(ini, -100.0)], 150.0, fim, cdi)
    assert comp["cobertura_cdi"] is False
    assert comp["pme"] is None


def test_parse_sgs_ignora_linha_ruim():
    out = rt.parse_sgs([
        {"data": "02/01/2024", "valor": "0,043739"},
        {"data": "03/01/2024", "valor": "0.043739"},
        {"data": "lixo", "valor": "1"},
        {"valor": "1"},
    ])
    assert out == {date(2024, 1, 2): 0.043739, date(2024, 1, 3): 0.043739}


def test_carregar_cdi_divide_em_janelas_e_sobrevive_a_falha(monkeypatch):
    chamadas = []

    class _Resp:
        ok = True

        def __init__(self, url):
            self.url = url

        def json(self):
            return [{"data": "02/01/2012", "valor": "0,04"}]

    def fake_get(url, timeout):
        chamadas.append(url)
        if len(chamadas) > 1:
            raise OSError("rede")
        return _Resp(url)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("core.bcb_sgs.baixar_sgs_soap",
                        lambda *a, **k: ({}, "o SOAP do SGS não respondeu"))
    out = rt.carregar_cdi_diario(date(2010, 1, 1), date(2026, 1, 1))
    assert len(chamadas) == 2          # primeira janela + a que falhou
    assert "dataFinal=31/12/2018" in chamadas[0]
    assert out == {date(2012, 1, 2): 0.04}


# ── Conciliação ───────────────────────────────────────────────────────────────


def _tx(d, tk, tipo, q, p, taxas=0.0):
    return {"data": d, "ticker": tk, "tipo": tipo, "quantidade": q, "preco": p, "taxas": taxas}


def test_conciliacao_separa_por_motivo():
    d = date(2024, 1, 1)
    transacoes = [
        _tx(d, "OK3", "buy", 10, 10.0, 1.0),       # fecha com a posição
        _tx(d, "SAIU3", "buy", 5, 20.0),           # comprou e vendeu tudo
        _tx(d, "SAIU3", "sell", 5, 30.0, 0.5),
        _tx(d, "SUB11", "buy", 8, 100.0),          # posição 10: subscrição fora
        _tx(d, "VELHA3", "sell", 3, 50.0),         # vendeu o que não comprou
        _tx(d, "SUMIU3", "buy", 4, 10.0),          # comprou, não vendeu, sumiu
    ]
    proventos = [
        {"data": d, "ticker": "OK3", "valor": 2.0},
        {"data": d, "ticker": "SUB11", "valor": 9.0},  # fora do universo
    ]
    posicoes = {
        "OK3": {"quantidade": 10, "valor_mercado": 150.0},
        "SUB11": {"quantidade": 10, "valor_mercado": 1100.0},
        "HERDADA3": {"quantidade": 7, "valor_mercado": 70.0},
    }
    u = rt.conciliar_universo(transacoes, proventos, posicoes)
    assert u["incluidos"] == ["OK3", "SAIU3"]
    motivos = {e["ticker"]: e["motivo"] for e in u["excluidos"]}
    assert motivos == {
        "SUB11": rt.MOTIVO_QTD_DIVERGE,
        "VELHA3": rt.MOTIVO_VENDA_SEM_COMPRA,
        "SUMIU3": rt.MOTIVO_COMPRA_SEM_POSICAO,
        "HERDADA3": rt.MOTIVO_SEM_COMPRA,
    }
    assert sorted(v for _, v in u["fluxos"]) == sorted([-101.0, -100.0, 149.5, 2.0])
    assert u["valor_final"] == 150.0
    assert u["valor_total"] == 1320.0
    assert (u["n_em_carteira"], u["n_em_carteira_incluidos"]) == (3, 1)


def test_fracionario_dentro_da_tolerancia_concilia():
    d = date(2024, 1, 1)
    u = rt.conciliar_universo(
        [_tx(d, "ABC3", "buy", 100, 1.0), _tx(d, "ABC3", "buy", 0.3, 1.0)],
        [],
        {"ABC3": {"quantidade": 100, "valor_mercado": 120.0}},
    )
    assert u["incluidos"] == ["ABC3"]


# ── Aporte da evolução patrimonial ────────────────────────────────────────────


def test_aporte_vem_do_extrato_e_nao_da_variacao_de_mercado():
    from core.investimentos import _montar_evolucao_snapshot

    snaps = [
        SimpleNamespace(mes=date(2025, 12, 31), valor_mercado=1000, valor_investido_snapshot=900),
        SimpleNamespace(mes=date(2026, 1, 31), valor_mercado=1500, valor_investido_snapshot=900),
    ]
    tx = [SimpleNamespace(mes=date(2026, 1, 1), delta_investido=0.0),
          SimpleNamespace(mes=date(2026, 2, 1), delta_investido=250.0)]
    d = _montar_evolucao_snapshot(snaps, [], None, tx)
    # A foto subiu 500 sem nenhuma compra: isso é valorização, não aporte.
    assert [(f["ano"], f["mes"], f["aporte"]) for f in d["fluxo_mensal"]] == [
        (2026, 1, 0.0), (2026, 2, 250.0),
    ]
    assert len(d["snapshots"]) == 2


def test_sql_de_aporte_nao_mistura_moedas():
    from core.investimentos import _SQL_EVOLUCAO_TX

    assert "a.currency" in _SQL_EVOLUCAO_TX


# ── Carregador e tela ─────────────────────────────────────────────────────────


def test_carregador_falha_em_estado_explicito(monkeypatch):
    from core import investimentos

    monkeypatch.setattr(investimentos.settings, "MOCK_MODE", False)
    monkeypatch.setattr(investimentos, "_rentabilidade_rv_b3_real",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = investimentos.get_rentabilidade_rv_b3.__wrapped__()
    assert r["data_source"] == "error" and r["disponivel"] is False


def test_carregador_em_mock_nao_simula_rentabilidade(monkeypatch):
    from core import investimentos

    monkeypatch.setattr(investimentos.settings, "MOCK_MODE", True)
    r = investimentos.get_rentabilidade_rv_b3.__wrapped__()
    assert r["disponivel"] is False


def test_formato_percentual_nao_troca_ponto_da_unidade():
    from views.investimentos import _fmt_pct_aa

    assert _fmt_pct_aa(0.1234) == "+12,34% a.a."
    assert _fmt_pct_aa(-0.05) == "-5,00% a.a."
    assert _fmt_pct_aa(None) == "—"


# ── CDI publicado + BCB ───────────────────────────────────────────────────────


def _serie_cdi(ini, fim, valor=0.04):
    out, d = {}, ini
    while d <= fim:
        if d.weekday() < 5:
            out[d] = valor
        d += timedelta(days=1)
    return out


def test_cdi_publicado_ida_e_volta(tmp_path):
    from datetime import datetime, timezone
    serie = _serie_cdi(date(2024, 1, 1), date(2024, 1, 31))
    arq = tmp_path / "cdi.json.gz"
    arq.write_bytes(rt.serializar_cdi(serie, datetime(2024, 2, 1, tzinfo=timezone.utc)))
    assert rt.ler_cdi_publicado(arq) == serie
    assert rt.ler_cdi_publicado(tmp_path / "nao_existe.gz") == {}


def test_arquivo_commitado_abre():
    # O placeholder vazio, ou a série publicada: nunca exceção.
    assert isinstance(rt.ler_cdi_publicado(), dict)


def test_obter_cdi_usa_arquivo_e_so_pede_ao_bcb_o_que_falta():
    pedidos = []

    def baixar(ini, fim):
        pedidos.append((ini, fim))
        return _serie_cdi(ini, fim), None

    pub = _serie_cdi(date(2019, 12, 1), date(2026, 9, 20))
    out = rt.obter_cdi(date(2019, 12, 2), date(2026, 10, 3), publicado=pub, baixar=baixar)
    # 20/09 é domingo: o último dia útil publicado é 18/09.
    assert pedidos == [(date(2026, 9, 19), date(2026, 10, 3))]
    assert out["motivo"] is None
    assert "arquivo publicado" in out["fonte"] and "BCB" in out["fonte"]


def test_obter_cdi_arquivo_em_dia_nao_chama_o_bcb():
    def baixar(ini, fim):
        raise AssertionError("não devia chamar o BCB")

    pub = _serie_cdi(date(2019, 1, 1), date(2026, 10, 2))
    out = rt.obter_cdi(date(2020, 1, 1), date(2026, 10, 3), publicado=pub, baixar=baixar)
    assert out["motivo"] is None
    assert min(out["serie"]) >= date(2020, 1, 1)


def test_obter_cdi_sem_arquivo_e_sem_bcb_explica_o_motivo():
    out = rt.obter_cdi(date(2019, 12, 2), date(2026, 10, 3), publicado={},
                       baixar=lambda i, f: ({}, "o BCB não respondeu (ReadTimeout)"))
    assert out["serie"] == {}
    assert out["motivo"] == ("O CDI ainda não foi publicado pela rotina local "
                             "(scripts/publicar_cdi_diario.py) e "
                             "o BCB não respondeu (ReadTimeout).")


def test_obter_cdi_arquivo_que_comeca_tarde_diz_que_nao_cobre():
    pub = _serie_cdi(date(2024, 1, 1), date(2026, 10, 2))
    out = rt.obter_cdi(date(2019, 12, 2), date(2026, 10, 3), publicado=pub,
                       baixar=lambda i, f: ({}, "o BCB não respondeu (ReadTimeout)"))
    assert out["motivo"].startswith("O arquivo publicado do CDI não cobre o período")


def test_obter_cdi_arquivo_velho_e_bcb_fora_ainda_compara_se_couber_na_folga():
    pub = _serie_cdi(date(2019, 1, 1), date(2026, 9, 28))
    out = rt.obter_cdi(date(2020, 1, 1), date(2026, 10, 3), publicado=pub,
                       baixar=lambda i, f: ({}, "o BCB respondeu HTTP 503"))
    # 28/09 está a 5 dias de 03/10: a comparação (folga de 7 dias) ainda vale.
    assert out["motivo"] is None
    comp = rt.comparar_com_cdi([(date(2020, 1, 2), -100.0)], 120.0, date(2026, 10, 3),
                               out["serie"])
    assert comp["cobertura_cdi"] is True


def test_proventos_da_evolucao_acumulam_os_meses_sem_foto():
    from core.investimentos import _montar_evolucao_snapshot

    # Fotos anuais: o provento de março e o de novembro não caem em mês de foto.
    snaps = [
        SimpleNamespace(mes=date(2024, 12, 31), valor_mercado=1000, valor_investido_snapshot=900),
        SimpleNamespace(mes=date(2025, 12, 31), valor_mercado=1100, valor_investido_snapshot=900),
    ]
    divs = [SimpleNamespace(mes=date(2024, 3, 1), delta_dividendos=10.0),
            SimpleNamespace(mes=date(2024, 12, 1), delta_dividendos=5.0),
            SimpleNamespace(mes=date(2025, 11, 1), delta_dividendos=20.0)]
    d = _montar_evolucao_snapshot(snaps, divs, None, [])
    assert [s["valor_com_dividendos"] - s["valor_mercado"] for s in d["snapshots"]] == [15.0, 35.0]
    assert d["total_dividendos"] == 35.0


def test_proventos_do_ponto_atual_incluem_os_meses_depois_da_ultima_foto():
    from core.investimentos import _montar_evolucao_snapshot

    snaps = [SimpleNamespace(mes=date(2020, 12, 31), valor_mercado=1000,
                             valor_investido_snapshot=900)]
    divs = [SimpleNamespace(mes=date(2020, 12, 1), delta_dividendos=5.0),
            SimpleNamespace(mes=date(2021, 6, 1), delta_dividendos=7.0)]
    d = _montar_evolucao_snapshot(
        snaps, divs, {"total_mercado": 1200, "total_investido": 950}, [])
    assert d["total_dividendos"] == 12.0


def test_sql_de_proventos_da_evolucao_ignora_os_ainda_nao_pagos():
    from core.investimentos import _SQL_EVOLUCAO_DIV

    assert "payment_date <= CURRENT_DATE" in _SQL_EVOLUCAO_DIV
