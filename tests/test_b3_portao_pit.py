"""O portão de LLM medido na data da safra (`core.b3_portao_pit`) e a mecânica
do portão no OOS da carteira (`core.b3_oos_carteira.aplicar_portao_medido`).

Nada aqui chama LLM nem banco: o avaliador recebe funções puras, e a
anonimização recebe um dossiê bruto montado à mão."""
from __future__ import annotations

import json
import math

import pytest

import core.b3_oos_carteira as oos
import core.b3_portao_pit as pit


def _seg(setor, sel, pesos, ranking):
    return (setor, list(sel), dict(pesos), [(tk, float(10 - k)) for k, tk in enumerate(ranking)])


# ─────────────────────────────────────────────────────────────────────────────
# aplicar_portao_medido — a mecânica de `_aplicar_gate_qualitativo`
# ─────────────────────────────────────────────────────────────────────────────

def test_vetado_sai_e_substituto_herda_o_peso_sem_mutar_a_entrada():
    segs = [_seg("Financeiro", ["AAAA3", "BBBB3"], {"AAAA3": 0.6, "BBBB3": 0.4},
                 ["AAAA3", "BBBB3", "CCCC3"])]
    cls = {"AAAA3": "vetar", "BBBB3": "aprovar", "CCCC3": "aprovar_com_ressalvas"}
    itens, log = oos.aplicar_portao_medido(segs, cls.get)
    assert itens == [("Financeiro", ["CCCC3", "BBBB3"],   # na vaga do vetado
                      {"AAAA3": 0.6, "BBBB3": 0.4, "CCCC3": 0.6})]
    assert log["trocas"] == [{"sai": "AAAA3", "entra": "CCCC3", "setor": "Financeiro"}]
    assert log["vetados"] == [{"tk": "AAAA3", "setor": "Financeiro"}]
    assert segs[0][1] == ["AAAA3", "BBBB3"] and "CCCC3" not in segs[0][2]


def test_substituto_com_peso_proprio_mantem_o_proprio():
    segs = [_seg("Energia", ["AAAA3"], {"AAAA3": 0.7, "CCCC3": 0.2},
                 ["AAAA3", "CCCC3"])]
    itens, _ = oos.aplicar_portao_medido(
        segs, {"AAAA3": "vetar", "CCCC3": "aprovar"}.get)
    assert itens[0][2]["CCCC3"] == 0.2


def test_sem_substituto_o_vetado_so_sai():
    segs = [_seg("Energia", ["AAAA3", "BBBB3"], {"AAAA3": 0.5, "BBBB3": 0.5},
                 ["AAAA3", "BBBB3"])]
    itens, log = oos.aplicar_portao_medido(
        segs, {"AAAA3": "vetar", "BBBB3": "aprovar"}.get)
    assert itens[0][1] == ["BBBB3"]
    assert log["trocas"] == [{"sai": "AAAA3", "entra": None, "setor": "Energia"}]


def test_busca_para_depois_de_dois_candidatos_vetados():
    segs = [_seg("Varejo", ["AAAA3"], {"AAAA3": 1.0},
                 ["AAAA3", "CCCC3", "DDDD3", "EEEE3"])]
    vistos = []

    def avaliar(tk):
        vistos.append(tk)
        return "aprovar" if tk == "EEEE3" else "vetar"
    itens, log = oos.aplicar_portao_medido(segs, avaliar)
    assert itens[0][1] == []
    assert "EEEE3" not in vistos            # o terceiro candidato nunca é avaliado
    assert [v["tk"] for v in log["vetados"]] == ["AAAA3", "CCCC3", "DDDD3"]


def test_parecer_indisponivel_nunca_veta():
    segs = [_seg("Saúde", ["AAAA3"], {"AAAA3": 1.0}, ["AAAA3", "CCCC3"])]
    for cls in (pit.FALHOU, pit.PENDENTE, None, "outra coisa"):
        itens, log = oos.aplicar_portao_medido(segs, lambda _tk, c=cls: c)
        assert itens[0][1] == ["AAAA3"]
        assert log["nao_avaliados"] == ["AAAA3"] and not log["vetados"]


# ─────────────────────────────────────────────────────────────────────────────
# Anonimização — helpers puros
# ─────────────────────────────────────────────────────────────────────────────

def test_rotulo_ano_e_relativo_e_recusa_o_futuro():
    assert pit.rotulo_ano(2019, 2019) == "A"
    assert pit.rotulo_ano(2018, 2019) == "A-1"
    assert pit.rotulo_ano("2010", 2019) == "A-9"
    with pytest.raises(ValueError):
        pit.rotulo_ano(2020, 2019)


def test_fator_escala_deterministico_e_na_faixa():
    fs = [pit.fator_escala(f"rs|T{i}|2019") for i in range(300)]
    assert all(pit.FAIXA_ESCALA[0] <= f <= pit.FAIXA_ESCALA[1] for f in fs)
    assert pit.fator_escala("rs|WEGE3|2019") == pit.fator_escala("rs|WEGE3|2019")
    assert len({round(f, 6) for f in fs}) > 250
    # log-uniforme: cerca de metade abaixo de 1
    assert 0.35 < sum(f < 1 for f in fs) / len(fs) < 0.65


def test_codigo_anonimo_estavel_e_diferente_por_safra():
    a = pit.codigo_anonimo("WEGE3", 2019)
    assert a == pit.codigo_anonimo("WEGE3", 2019)
    assert a != pit.codigo_anonimo("WEGE3", 2020)
    assert a.startswith("EMPRESA-") and "WEGE" not in a


def test_acertou_sonda():
    assert pit.acertou_sonda({"ticker": "wege3", "ano_da_decisao": 2019}, "WEGE3", 2019) == {
        "empresa": True, "ano": True, "ano_perto": True}
    # raiz de 4 letras: outra classe da mesma empresa conta como acerto
    assert pit.acertou_sonda({"ticker": "ITUB3", "ano_da_decisao": "2017"}, "ITUB4", 2018)[
        "empresa"] is True
    r = pit.acertou_sonda({"ticker": None, "ano_da_decisao": None}, "WEGE3", 2019)
    assert r == {"empresa": False, "ano": False, "ano_perto": False}
    assert pit.acertou_sonda(None, "WEGE3", 2019)["empresa"] is False


def test_faixa():
    cortes, rot = (7, 10, 13), ("a", "b", "c", "d")
    assert [pit._faixa(v, cortes, rot) for v in (2, 7, 9.9, 12, 13, 20)] == [
        "a", "b", "b", "c", "d", "d"]


def test_contexto_macro_em_faixas_sem_niveis():
    macro = {2017: {"selic": 0.07, "ipca": 2.95, "cambio": 3.31, "pib": 6.6e6},
             2018: {"selic": 0.065, "ipca": 3.75, "cambio": 3.87, "pib": 7.0e6}}
    txt = pit.contexto_macro_pit(2019, macro)
    assert "até 7% a.a., em queda no ano" in txt
    assert "IPCA 3% a 4,5%" in txt
    assert "PIB nominal a/a 5% a 10%" in txt
    assert "depreciou mais de 10%" in txt
    # nenhum nível que date o dossiê
    for nivel in ("6,5", "6.5", "3,87", "3.87", "3,75", "3.75", "2018", "2019"):
        assert nivel not in txt
    assert "lacuna de fonte, não calmaria" in txt


def test_contexto_macro_com_lacunas():
    macro = {2017: {"selic": float("nan"), "ipca": 2.95, "cambio": None, "pib": None}}
    txt = pit.contexto_macro_pit(2018, macro)
    assert "Selic n/d" in txt and "PIB nominal a/a n/d" in txt and "câmbio n/d" in txt
    assert "indisponível em public.macro" in pit.contexto_macro_pit(2030, macro)


def _bruto():
    serie = []
    for i, ano in enumerate(range(2014, 2019)):
        rec = 10_000.0 + 1_000 * i
        serie.append({"ano": ano, "receita_mi": rec, "ebit_mi": rec * 0.15,
                      "ebitda_mi": rec * 0.2, "lucro_mi": rec * 0.1, "pl_mi": 8_000.0 + 500 * i,
                      "caixa_mi": 1_234.0, "div_bruta_mi": 2_345.0, "div_liq_mi": 1_111.0,
                      "fco_mi": rec * 0.12, "lpa": 1.2345 + 0.1 * i,
                      "margem_liq_pct": 10.0, "roe_pct": 12.3})
    tris = [{"ano": a, "tri": t, "receita_mi": 2_500.0 + 10 * k, "lucro_mi": 250.0}
            for k, (a, t) in enumerate((a, t) for a in (2017, 2018) for t in (1, 2, 3, 4))]
    return {
        "ticker": "WEGE3", "safra": 2019, "corte": "2019-03-31",
        "setor": "Bens Industriais", "subsetor": "Máquinas e Equipamentos",
        "segmento": "Motores, Compressores e Outros",
        "serie_anual": serie,
        "trimestres": {"serie": tris[-6:], "yoy": {"receita_yoy_pct": 1.6,
                                                   "ref": "2018T4 vs 2017T4"}},
        "dividendos": {"por_ano": {2017: 0.5555, 2018: 0.6666}, "ult_12m_ps": 0.6666,
                       "devolucao_capital_12m_ps": None, "dy_12m_pct": 3.7},
        "valuation": {"market_cap_mi": 77_777.0, "preco": 18.0, "min_52s": 15.5,
                      "max_52s": 19.9, "data_preco": "2019-03-29", "ano_base_valuation": 2018,
                      "pl": 28.1, "pvp": 6.2},
    }


def test_anonimizar_esconde_identidade_valores_e_anos(monkeypatch):
    # `_checks` e `_sensibilidade_juros` são de produção; aqui basta que
    # recebam a série já anonimizada.
    import core.dossie_b3 as d
    recebidos = {}
    monkeypatch.setattr(d, "_checks", lambda serie, *a: recebidos.setdefault("s", serie) and [])
    monkeypatch.setattr(d, "_sensibilidade_juros", lambda serie: {})
    bruto = _bruto()
    anon = pit.anonimizar(bruto, "WEGE3", 2019)
    texto = json.dumps(anon, ensure_ascii=False)

    assert anon["ticker"] == pit.codigo_anonimo("WEGE3", 2019)
    assert anon["subsetor"] == anon["segmento"] == "(oculto)"
    assert anon["setor"] == "Bens Industriais"
    for proibido in ("WEGE", "Motores", "Máquinas", "2014", "2017", "2018", "2019",
                     "77777", "1234.0", "2345.0", "18.0", "0.5555"):
        assert proibido not in texto, proibido
    assert [s["ano"] for s in anon["serie_anual"]] == ["A-5", "A-4", "A-3", "A-2", "A-1"]
    assert anon["trimestres"]["yoy"]["ref"] == "A-1T4 vs A-2T4"
    assert set(anon["dividendos"]["por_ano"]) == {"A-2", "A-1"}
    assert anon["valuation"]["ano_base_valuation"] == "A-1"
    assert anon["valuation"]["data_preco"] == "data da decisão"

    # escala: R$ por f, por ação por g; razões ficam reais
    f = pit.fator_escala("rs|WEGE3|2019")
    g = pit.fator_escala("ps|WEGE3|2019")
    s0 = anon["serie_anual"][0]
    assert s0["receita_mi"] == round(10_000.0 * f, 1)
    assert s0["lpa"] == round(1.2345 * g, 4)
    assert s0["margem_liq_pct"] == 10.0 and s0["roe_pct"] == 12.3
    assert anon["valuation"]["preco"] == round(18.0 * g, 2)
    assert anon["valuation"]["pl"] == 28.1 and anon["dividendos"]["dy_12m_pct"] == 3.7
    # red flags recebem o ano NUMÉRICO relativo, na ordem
    assert [s["ano"] for s in recebidos["s"]] == [2014, 2015, 2016, 2017, 2018]
    assert all(c in anon["red_flags"] for c in pit.COBERTURA_PIT)


# ─────────────────────────────────────────────────────────────────────────────
# AvaliadorPortaoPIT — ondas, falha, repetição, cache, resumo
# ─────────────────────────────────────────────────────────────────────────────

def _avaliador(tmp_path, veredito, *, sonda=None, **kw):
    chamadas = []

    def parecer(e):
        chamadas.append(e["tk"])
        return veredito(e["tk"], e["safra"], len(chamadas))
    av = pit.AvaliadorPortaoPIT(
        tmp_path / "cache.json", lambda tk, s: {"tk": tk, "safra": s}, parecer, sonda,
        workers=2, intervalo_s=0, log=lambda *_: None, **kw)
    return av, chamadas


def _por_safra():
    return [{"safra": 2019, "segmentos": [
        _seg("Financeiro", ["AAAA3", "BBBB3"], {"AAAA3": 0.5, "BBBB3": 0.5},
             ["AAAA3", "BBBB3", "CCCC3", "DDDD3"])]}]


def test_ondas_buscam_o_substituto_so_de_quem_foi_vetado(tmp_path):
    cls = {"AAAA3": "vetar", "BBBB3": "aprovar", "CCCC3": "aprovar"}
    av, chamadas = _avaliador(tmp_path, lambda tk, s, n: {"classificacao": cls[tk]},
                              frac_repeticao=0)
    av.preparar(_por_safra())
    assert sorted(chamadas) == ["AAAA3", "BBBB3", "CCCC3"]   # DDDD3 nunca entrou
    itens, log = av(_por_safra()[0]["segmentos"], 2019)
    assert itens[0][1] == ["CCCC3", "BBBB3"]
    assert log["trocas"] == [{"sai": "AAAA3", "entra": "CCCC3", "setor": "Financeiro"}]


def test_falha_nunca_veta_e_nao_e_gravada(tmp_path):
    def veredito(tk, s, n):
        if tk == "AAAA3":
            raise TimeoutError("provedor fora")
        return {"classificacao": "aprovar"}
    av, chamadas = _avaliador(tmp_path, veredito, frac_repeticao=0)
    av.preparar(_por_safra())
    assert chamadas.count("AAAA3") == 2                   # duas tentativas
    assert av.classificacao("AAAA3", 2019) == pit.FALHOU
    itens, log = av(_por_safra()[0]["segmentos"], 2019)
    assert itens[0][1] == ["AAAA3", "BBBB3"] and log["nao_avaliados"] == ["AAAA3"]
    res = av.resumo({2019})
    assert res["falhas"] == 1 and res["tentativas_perdidas_nesta_rodada"] == 2
    assert "AAAA3|2019|0" not in json.loads((tmp_path / "cache.json").read_text("utf-8"))[
        "registros"]


def test_resposta_fora_do_esquema_ganha_segunda_tentativa(tmp_path):
    vistas = {}

    def veredito(tk, s, n):
        vistas[tk] = vistas.get(tk, 0) + 1
        if tk == "AAAA3" and vistas[tk] == 1:
            return {"classificacao": "talvez"}
        return {"classificacao": "aprovar"}
    av, _ = _avaliador(tmp_path, veredito, frac_repeticao=0)
    av.preparar(_por_safra())
    assert av.classificacao("AAAA3", 2019) == "aprovar"
    assert av.registros["AAAA3|2019|0"]["tentativa"] == 2
    res = av.resumo()
    assert res["pareceres_na_segunda_tentativa"] == 1 and res["falhas"] == 0


def test_cache_reaproveita_e_versao_diferente_descarta(tmp_path):
    av, chamadas = _avaliador(tmp_path, lambda tk, s, n: {"classificacao": "aprovar"},
                              frac_repeticao=0)
    av.preparar(_por_safra())
    assert len(chamadas) == 2

    av2, chamadas2 = _avaliador(tmp_path, lambda tk, s, n: {"classificacao": "vetar"},
                                frac_repeticao=0)
    av2.preparar(_por_safra())
    assert chamadas2 == [] and av2.classificacao("AAAA3", 2019) == "aprovar"

    dados = json.loads((tmp_path / "cache.json").read_text("utf-8"))
    dados["versao"] = "dossie-pit-0.0.0"
    (tmp_path / "cache.json").write_text(json.dumps(dados), encoding="utf-8")
    av3, _ = _avaliador(tmp_path, lambda tk, s, n: {"classificacao": "aprovar"})
    assert av3.registros == {}


def test_repeticao_e_sonda_entram_no_resumo(tmp_path):
    def sonda(e, tk, safra):
        return {"empresa": tk == "AAAA3", "ano": False, "ano_perto": True}
    cls = {"AAAA3": "vetar", "BBBB3": "aprovar", "CCCC3": "aprovar"}
    av, chamadas = _avaliador(
        tmp_path, lambda tk, s, n: {"classificacao": cls[tk] if n <= 3 else "aprovar"},
        sonda=sonda, frac_repeticao=1.0, frac_sonda=1.0)
    av.preparar(_por_safra())
    assert len(chamadas) == 6                              # 3 pareceres + 3 repetições
    res = av.resumo({2019})
    assert res["pareceres"] == 3 and res["repeticoes"] == 3
    assert res["taxa_veto"] == pytest.approx(1 / 3, abs=1e-3)
    assert res["concordancia_veto"] == pytest.approx(2 / 3, abs=1e-3)
    assert res["sondas"] == 3
    assert res["sonda_acerta_empresa"] == pytest.approx(1 / 3, abs=1e-3)
    assert res["vetos_com_empresa_identificada"] == 1
    assert res["taxa_veto_sonda_identificou"] == 1.0
    assert res["taxa_veto_sonda_nao_identificou"] == 0.0
    assert av.resumo({2020})["pareceres"] == 0


def test_resumo_do_perfil_so_conta_o_que_o_portao_dele_consultou(tmp_path):
    # O cache é compartilhado entre perfis: o "amplo" avaliou EEEE3 na mesma
    # safra, e o resumo do perfil enxuto não pode contá-lo nem sondá-lo.
    sondados = []

    def sonda(e, tk, safra):
        sondados.append(tk)
        return {"empresa": False, "ano": False, "ano_perto": False}
    av, _ = _avaliador(tmp_path, lambda tk, s, n: {"classificacao": "vetar" if tk == "EEEE3"
                                                   else "aprovar"},
                       sonda=sonda, frac_repeticao=0, frac_sonda=1.0)
    amplo = [{"safra": 2019, "segmentos": [
        _seg("Energia", ["EEEE3"], {"EEEE3": 1.0}, ["EEEE3", "FFFF3"])]}]
    av.preparar(amplo)
    assert av.consultados == {("EEEE3", 2019), ("FFFF3", 2019)}
    sondados.clear()
    av.preparar(_por_safra())
    assert av.consultados == {("AAAA3", 2019), ("BBBB3", 2019)}
    assert sorted(sondados) == ["AAAA3", "BBBB3"]
    res = av.resumo(pares=av.consultados)
    assert res["pareceres"] == 2 and res["taxa_veto"] == 0.0
    assert av.resumo({2019})["pareceres"] == 4             # a safra inteira, sem filtro

def test_resumo_vazio_nao_divide_por_zero(tmp_path):
    av, _ = _avaliador(tmp_path, lambda tk, s, n: {"classificacao": "aprovar"})
    res = av.resumo()
    assert res["pareceres"] == 0 and res["taxa_veto"] is None
    assert all(v is None or not (isinstance(v, float) and math.isnan(v))
               for v in res.values())


def test_falhas_separadas_por_causa(tmp_path):
    def entrada(tk, s):
        if tk == "AAAA3":
            raise LookupError("sem série anual até o exercício N-1")
        return {"tk": tk, "safra": s}

    def parecer(e):
        return {"classificacao": "nao_avaliado" if e["tk"] == "BBBB3" else "aprovar"}
    av = pit.AvaliadorPortaoPIT(tmp_path / "c.json", entrada, parecer, workers=2,
                                intervalo_s=0, frac_repeticao=0, log=lambda *_: None)
    av.preparar(_por_safra())
    res = av.resumo({2019})
    assert res["falhas"] == 2
    assert res["falhas_sem_dossie"] == 1 and res["falhas_modelo_nao_avaliou"] == 1
    assert av.resumo({2020})["falhas"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Empresas que saíram da B3 — dossiê pela DFP
# ─────────────────────────────────────────────────────────────────────────────

def _dem(ano, recebida, receita=1_000e6, lucro=100e6, lpa=0.5, pago=40e6):
    import pandas as pd

    from data_pipeline.market.b3_saidas import DemonstracaoAnual
    contas = {"3.01": ("Receita", receita), "3.05": ("EBIT", receita * 0.2),
              "3.11": ("Lucro", lucro), "2.03": ("PL", 1_000e6),
              "1.01.01": ("Caixa", 50e6), "2.01.04": ("Empréstimos e Financiamentos", 200e6),
              "6.01": ("FCO", 120e6), "3.99.01.01": ("ON", lpa),
              "6.03.01": ("Dividendos pagos", -pago)}
    return DemonstracaoAnual(cd_cvm=1, ano=ano, available_at=pd.Timestamp(recebida),
                             consolidado=False, contas=contas)


def test_serie_dfp_corta_no_exercicio_e_na_entrega():
    from datetime import date
    dems = {2016: _dem(2016, "2017-03-10"), 2017: _dem(2017, "2018-03-20"),
            2018: _dem(2018, "2019-04-15"),          # entregue depois do corte
            2019: _dem(2019, "2020-03-01")}          # exercício depois de N-1
    serie = pit.serie_anual_dfp(dems, "3", date(2019, 3, 31), 2018)
    assert [s["ano"] for s in serie] == [2016, 2017]
    s = serie[-1]
    assert s["receita_mi"] == 1000.0 and s["lucro_mi"] == 100.0 and s["ebitda_mi"] is None
    assert s["div_liq_mi"] == 150.0 and s["margem_liq_pct"] == 10.0 and s["roe_pct"] == 10.0


def test_dividendos_dfp_por_acao_implicita_no_lpa():
    from datetime import date
    serie = pit.serie_anual_dfp({2017: _dem(2017, "2018-03-01"),
                                 2018: _dem(2018, "2019-03-01", pago=80e6)},
                                "3", date(2019, 3, 31), 2018)
    dv = pit.dividendos_dfp(serie, preco=4.0)
    # 200 mi de ações (100 mi / 0,50): 0,20 e 0,40 por ação
    assert dv["por_ano"] == {"2017": 0.2, "2018": 0.4}
    assert dv["ult_12m_ps"] == 0.4 and dv["dy_12m_pct"] == 10.0
    assert pit.dividendos_dfp(serie, preco=None)["dy_12m_pct"] is None


def test_retorno_12m_some_quando_atravessa_evento_nao_detectado():
    from datetime import date
    idx = {f"2022-{m:02d}": 97.0 + m for m in range(3, 13)}   # 2022-03 = 100
    idx.update({"2023-01": 110.0, "2023-02": 112.0, "2023-03": 115.0})
    assert pit.retorno_12m_indice(idx, date(2023, 3, 31)) == 15.0
    idx["2023-02"] = 4_000.0                              # grupamento não detectado
    assert pit.retorno_12m_indice(idx, date(2023, 3, 31)) is None
    assert pit.retorno_12m_indice({"2023-03": 1.0}, date(2023, 3, 31)) is None


def test_anonimizar_declara_cobertura_da_saida(monkeypatch):
    import core.dossie_b3 as d
    monkeypatch.setattr(d, "_checks", lambda *a: [])
    monkeypatch.setattr(d, "_sensibilidade_juros", lambda serie: {})
    bruto = {**_bruto(), "trimestres": {}, "fonte": "dfp_cvm_saida"}
    anon = pit.anonimizar(bruto, "WEGE3", 2019)
    assert all(c in anon["red_flags"] for c in pit.COBERTURA_SAIDA)
    assert anon["trimestres"] == {"serie": [], "yoy": {}}
    assert not any(c in pit.anonimizar(_bruto(), "WEGE3", 2019)["red_flags"]
                   for c in pit.COBERTURA_SAIDA)


def test_parecer_fora_do_esquema_registra_o_valor_bruto(monkeypatch):
    # `_sanitizar_parecer` trocaria "aprovado" por "nao_avaliado"; a falha
    # precisa dizer o que o modelo devolveu de fato.
    monkeypatch.setattr(pit, "prompt_do_parecer", lambda texto, macro: "prompt")
    monkeypatch.setattr(pit, "_llm_json",
                        lambda prompt: ({"classificacao_selecao": "aprovado"}, "m/x"))
    with pytest.raises(ValueError, match="fora do esquema: 'aprovado'"):
        pit.parecer_pit({"codigo": "EMPRESA-000001", "texto": "", "macro": ""})
    monkeypatch.setattr(pit, "_llm_json",
                        lambda prompt: ({"classificacao_selecao": "vetar",
                                         "motivo_selecao": "x"}, "m/x"))
    assert pit.parecer_pit({"codigo": "EMPRESA-000001", "texto": "",
                            "macro": ""})["classificacao"] == "vetar"


def test_chamada_pendurada_vira_falha_no_prazo_e_nao_segura_o_parecer():
    import threading
    import time

    solta = threading.Event()
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="não respondeu"):
        pit._com_prazo(lambda: solta.wait(30), 0.2)
    assert time.monotonic() - t0 < 5
    solta.set()
    assert pit._com_prazo(lambda: 7, 1.0) == 7
    with pytest.raises(KeyError):
        pit._com_prazo(lambda: {}["x"], 1.0)
