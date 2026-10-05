"""Valuation e comparação com pares da Inteligência dos Ativos.

Cobre core/inteligencia_ativos/valuation.py, pares.py, os construtores puros
de fontes_valuation.py, as funções puras do publicador
scripts/publish_valuation_historico.py, os provedores e os cartões da tela.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd
import pytest

from core.inteligencia_ativos import fontes_valuation as fv
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as p
from core.inteligencia_ativos import secoes
from core.inteligencia_ativos import valuation as v

VEREDITO = re.compile(r"\b(barat[oa]s?|car[oa]s?)\b", re.IGNORECASE)


def _hist(valores, inicio=2014):
    return tuple((str(inicio + i), x) for i, x in enumerate(valores))


# -- estatística ---------------------------------------------------------------------

def test_estatistica_quantis_como_numpy():
    e = v.estatistica([4, 1, 3, 2, None, float("nan")])
    assert (e.n, e.media, e.mediana, e.minimo, e.maximo) == (4, 2.5, 2.5, 1, 4)
    assert e.p25 == pytest.approx(1.75) and e.p75 == pytest.approx(3.25)
    assert v.estatistica([]) is None


def test_percentil_conta_empate_pela_metade():
    assert v.percentil(3, [1, 2, 3, 4]) == 62
    assert v.percentil(0, [1, 2]) == 0
    assert v.percentil(9, [1, 2]) == 100
    assert v.percentil(1, []) is None


def test_posicao_com_tolerancia():
    assert v.posicao(10.4, 10) == v.EM_LINHA
    assert v.posicao(10.6, 10) == v.ACIMA
    assert v.posicao(9.4, 10) == v.ABAIXO
    assert v.posicao(None, 10) == v.SEM_DADO
    # taxa do Tesouro: 0,3 p.p. sobre 7,4% não é "em linha"
    assert v.posicao(7.7, 7.4, v.TOLERANCIA["taxa_mercado"]) == v.ACIMA


def test_janela_fica_com_as_ultimas_observacoes_validas():
    obs, minimo = v.janela(_hist(range(15), 2000) + (("2015", None),), v.ANUAL)
    assert minimo == 5 and len(obs) == 10 and obs[0] == ("2005", 5.0)


# -- montagem ------------------------------------------------------------------------

def test_montar_compara_historico_e_pares_com_dado_e_interpretacao():
    val = v.montar(f.ACAO, {
        "p_l": v.Entrada(f.Dado(8.0, "Fonte", "exercício 2025"),
                         _hist([10, 12, 11, 9, 13, 10])),
        "ev_ebit": v.Entrada(excluida="financeira"),
    }, pares={"p_l": (15.0, 16.0, 14.0, 20.0)}, grupo_pares="4 pares do mesmo X")
    pl = val.linha("p_l")
    assert pl.posicao_historica == v.ABAIXO and pl.posicao_pares == v.ABAIXO
    assert pl.comparacao_historica.startswith(
        "O P/L atual (8,00x) está abaixo da média histórica dos últimos 6 anos")
    assert "percentil 0" in pl.comparacao_historica
    assert pl.comparacao_pares.startswith(
        "O P/L (8,00x) está abaixo da mediana dos pares comparáveis (15,50x")
    assert [fx.rotulo.split(" (")[0] for fx in pl.faixas] == [
        "Interquartil do histórico", "Mínimo–máximo do histórico",
        "Interquartil dos pares"]
    ev = val.linha("ev_ebit")
    assert not ev.aplicavel and ev.motivo.startswith("Não se aplica")
    assert val.linha("p_vp").motivo == f.NAO_DISPONIVEL
    assert any("Pares: 4 pares do mesmo X." == x for x in val.premissas)


def test_historico_curto_nao_compara():
    val = v.montar(f.ACAO, {"p_l": v.Entrada(f.Dado(8.0, "F"), _hist([9, 10]))})
    pl = val.linha("p_l")
    assert pl.posicao_historica == v.SEM_DADO and pl.historico is None
    assert "Histórico insuficiente" in pl.comparacao_historica
    assert "(mínimo 5)" in pl.comparacao_historica


def test_em_linha_e_artigo_da_taxa():
    val = v.montar(f.RENDA_FIXA, {"taxa_mercado": v.Entrada(
        f.Dado(7.40, "Tesouro"), tuple((f"d{i:03d}", 7.40) for i in range(80)),
        v.DIARIA)}, pares={"taxa_mercado": (7.41, 7.39, 7.40)})
    t = val.linha("taxa_mercado")
    assert t.comparacao_historica.startswith("A Taxa de mercado (venda) atual")
    assert "em linha com a média histórica" in t.comparacao_historica
    assert "em linha com a mediana dos pares" in t.comparacao_pares
    assert "1% na taxa do Tesouro" in " ".join(val.premissas)
    assert "em linha com a " in v.resumo(val)


def test_montar_recusa_metrica_fora_do_catalogo():
    with pytest.raises(ValueError):
        v.montar(f.FII, {"p_l": v.Entrada()})


def test_etf_e_classe_desconhecida_saem_com_motivo():
    assert v.montar(f.ETF, {}).motivo and not v.montar(f.ETF, {}).linhas
    assert "sem catálogo" in v.montar(None, {}).motivo


def test_valuation_ida_e_volta_pelo_dict():
    val = v.montar(f.ACAO, {"p_l": v.Entrada(f.Dado(8.0, "F"),
                                             _hist([10, 12, 11, 9, 13]))},
                   pares={"p_l": (15.0, 16.0, 14.0)}, grupo_pares="g")
    de_volta = v.Valuation.de_dict(val.como_dict())
    assert de_volta.como_dict() == val.como_dict()


def test_texto_separa_dado_de_interpretacao_e_nao_da_veredito():
    val = v.montar(f.ACAO, {"p_l": v.Entrada(f.Dado(8.0, "F"),
                                             _hist([10, 12, 11, 9, 13]))})
    t = v.texto(val, "WEGE3")
    assert t.index("[DADO") < t.index("[INTERPRETAÇÃO")
    assert v.AVISO in t
    assert not VEREDITO.search(v.resumo(val) + " ".join(
        ln.comparacao_historica + ln.comparacao_pares for ln in val.linhas))


# -- seleção de pares ----------------------------------------------------------------

def _c(tk, seg="S1", sub="Sub", porte=100.0, risco=30.0, raiz=None, ano=2025,
       tipo=f.ACAO, mercado="B3", **metricas):
    return p.Candidato(tk, tk, tipo, mercado,
                       (("segmento", seg), ("subsetor", sub), ("setor", "X")),
                       porte, risco, "volatilidade", metricas,
                       raiz or tk[:4], ano)


def test_selecao_deterministica_sem_mesma_empresa_e_sem_outra_classe():
    alvo = _c("AAAA3")
    universo = [alvo, _c("AAAA4"), _c("BBBB3", porte=110), _c("CCCC3", porte=90),
                _c("DDDD3", porte=120), _c("DDDD4", porte=120),
                _c("EEEE11", tipo=f.FII), _c("FFFF3", mercado="EUA")]
    g1 = p.selecionar(alvo, universo)
    g2 = p.selecionar(alvo, list(reversed(universo)))
    assert [x.ticker for x in g1.pares] == [x.ticker for x in g2.pares]
    tickers = {x.ticker for x in g1.pares}
    assert "AAAA4" not in tickers and "EEEE11" not in tickers
    assert "FFFF3" not in tickers
    assert len({t[:4] for t in tickers}) == len(tickers)
    assert g1.nivel == "segmento" and g1.valor == "S1"


def test_par_com_balanco_antigo_fica_fora():
    alvo = _c("AAAA3")
    universo = [_c("BBBB3"), _c("CCCC3"), _c("DDDD3"), _c("VELH3", ano=2019)]
    g = p.selecionar(alvo, universo)
    assert "VELH3" not in {x.ticker for x in g.pares}
    assert any("exercício de 2024" in c for c in g.criterios)


def test_nivel_mais_largo_quando_o_estreito_nao_tem_pares():
    alvo = _c("AAAA3", seg="Raro")
    universo = [_c("BBBB3"), _c("CCCC3"), _c("DDDD3")]
    g = p.selecionar(alvo, universo)
    assert g.nivel == "subsetor"
    assert any("mesmo segmento (Raro): nível mais largo" in r
               for r in g.relaxamentos)


def test_afrouxamento_de_porte_e_risco_fica_registrado():
    alvo = _c("AAAA3", porte=100, risco=30)
    # porte fora da banda de 10x; risco dentro
    so_risco = [_c(t, porte=5000, risco=31) for t in ("BBBB3", "CCCC3", "DDDD3")]
    g = p.selecionar(alvo, so_risco)
    assert len(g.pares) == 3
    assert any("tamanho não filtra" in r for r in g.relaxamentos)
    assert not any("risco não filtra" in r for r in g.relaxamentos)
    assert any(c.startswith("Risco:") for c in g.criterios)
    # risco fora da banda de 2x; porte dentro
    so_porte = [_c(t, porte=100, risco=200) for t in ("BBBB3", "CCCC3", "DDDD3")]
    g = p.selecionar(alvo, so_porte)
    assert any("risco não filtra" in r for r in g.relaxamentos)
    assert not any("tamanho não filtra" in r for r in g.relaxamentos)


def test_menos_de_tres_pares_nao_forma_grupo():
    g = p.selecionar(_c("AAAA3"), [_c("BBBB3"), _c("CCCC3")])
    assert not g.pares and "Menos de 3 pares" in g.motivo
    assert g.descricao is None


# -- comparação ----------------------------------------------------------------------

def test_comparar_monta_a_tabela_e_nao_conclui():
    alvo = _c("AAAA3", p_l=10.0, dividend_yield=6.0, roe=15.0)
    universo = [_c("BBBB3", p_l=8.0, dividend_yield=4.0),
                _c("CCCC3", p_l=12.0, dividend_yield=5.0),
                _c("DDDD3", p_l=20.0, dividend_yield=4.5)]
    c = p.comparar(alvo, p.selecionar(alvo, universo))
    pl = c.linha("p_l")
    assert (pl.ativo, pl.metrica, pl.valor, pl.mediana_pares, pl.n_pares) == (
        "AAAA3", "P/L", 10.0, 12.0, 3)
    assert pl.posicao == v.ABAIXO and pl.texto_diferenca() == "−2,00x (−17%)"
    assert "Não indica, sozinho, investimento melhor ou pior" in pl.interpretacao
    dy = c.linha("dividend_yield")
    assert dy.texto_diferenca() == "+1,5 p.p."
    roe = c.linha("roe")
    assert roe.n_pares == 0 and roe.interpretacao == "Nenhum par com este dado."
    txt = p.texto(c, "AAAA3")
    assert "Ativo | Métrica | Valor | Mediana dos pares | Diferença" in txt
    assert "(0)" not in txt and p.RODAPE in txt
    assert not VEREDITO.search(txt + p.resumo(c))
    d = c.como_dict()
    assert d["rodape"] == p.RODAPE and d["linhas"][0]["texto_diferenca"]
    assert p.ComparacaoPares.de_dict(d).como_dict() == d


def test_em_linha_da_taxa_cita_a_tolerancia_da_taxa():
    alvo = p.Candidato("TIPCA2035", None, f.RENDA_FIXA, "Tesouro Direto",
                       (("papel", "TIPCA"),), None, 9.0, "prazo",
                       {"taxa_mercado": 7.40})
    grupo = p.GrupoPares("papel", "TIPCA", tuple(
        p.Par(t, None, 0, "", {"taxa_mercado": 7.40}) for t in "abc"))
    linha = p.comparar(alvo, grupo).linha("taxa_mercado")
    assert linha.posicao == v.EM_LINHA and "até 1%" in linha.interpretacao


def test_sem_grupo_a_comparacao_diz_por_que():
    alvo = _c("AAAA3", p_l=10.0)
    c = p.comparar(alvo, p.selecionar(alvo, []))
    assert not c.com_dado and c.motivo and c.motivo in p.resumo(c)


# -- construtores das fontes ---------------------------------------------------------

def test_ano_de_varios_formatos():
    assert fv._ano(date(2024, 12, 31)) == 2024
    assert fv._ano("exercício 2025") == 2025
    assert fv._ano(None) is None and fv._ano("sem ano") is None


def test_candidatos_b3_taxonomia_financeira_e_escala():
    mult = [{"Ticker": "ITUB4", "P/L": 9, "P/VP": 1.8, "DY": 0.07,
             "EV_EBIT": 5, "ROE": 0.2, "data": date(2025, 12, 31)},
            {"Ticker": "WEGE3", "P/L": -3, "P/VP": 8, "DY": 0.02,
             "EV_EBIT": 20, "data": date(2025, 12, 31)}]
    setores = {"ITUB4": {"SETOR": "Fin", "SUBSETOR": "Intermediários Financeiros",
                         "SEGMENTO": "Bancos", "nome_empresa": "Itaú"},
               "WEGE3": {"SETOR": "Bens", "SUBSETOR": "Máquinas",
                         "SEGMENTO": "Motores"}}
    cand = fv.candidatos_b3(mult, setores, {"WEGE3": {"porte": 1e11,
                                                      "volatilidade": 25}})
    itub = cand["ITUB4"]
    assert "ev_ebit" not in itub.metricas
    assert itub.metricas["dividend_yield"] == pytest.approx(7.0)
    assert itub.taxonomia[0] == ("segmento", "Bancos")
    assert itub.raiz == "ITUB" and itub.ano_ref == 2025
    wege = cand["WEGE3"]
    assert "p_l" not in wege.metricas and wege.porte == 1e11 and wege.risco == 25


def test_entradas_b3_exclusoes_e_historico():
    e = fv.entradas_b3({"P/L": -2, "P/VP": 3, "DY": 0.05, "EV_EBIT": 7},
                       {"historico": {"p_vp": [[2020, 2.0], [2021, 2.5]]}}, True)
    assert e["p_l"].excluida == "lucro_negativo"
    assert e["ev_ebit"].excluida == "financeira"
    assert e["dividend_yield"].atual.valor == pytest.approx(5.0)
    assert e["p_vp"].historico == (("2020", 2.0), ("2021", 2.5))
    assert e["p_vp"].fonte_historico == fv.FONTE_HISTORICO
    assert e["dividend_yield"].fonte_historico is None


def test_fii_segmento_vago_sinonimo_e_cap_rate():
    linhas = [{"ticker": "HGLG11", "tipo": "tijolo", "sector": "Logístico",
               "mandate": "Renda", "pvp": 0.95, "dy_12m": 0.085,
               "implied_cap_rate": 0.09, "max_drawdown": -0.2,
               "patrimonio_liquido": 5e9},
              {"ticker": "KNCR11", "tipo": "papel", "sector": "Multicategoria",
               "mandate": "Títulos", "pvp": 1.0}]
    c = fv.candidatos_fii(linhas)
    assert c["HGLG11"].taxonomia[0][1] == "tijolo · Logística"
    assert c["HGLG11"].risco == pytest.approx(20.0)
    assert c["KNCR11"].taxonomia[0][1] is None
    assert c["KNCR11"].taxonomia[1][1] == "papel · Títulos"
    assert fv.entradas_fii(linhas[1], None)["cap_rate"].excluida == "fii_nao_tijolo"
    e = fv.entradas_fii(linhas[0], {"historico": {"p_vp": [["2026-07", 0.9]]}})
    assert e["cap_rate"].atual.valor == pytest.approx(9.0)
    assert e["p_vp"].frequencia == v.MENSAL


def test_eua_sic_financeiro_e_recencia():
    art = {"JPM": {"atual": {"p_l": 12, "ev_ebit": 9}, "sic": "6021",
                   "cik": "19617", "atual_ref": "exercício 2025"},
           "AAPL": {"atual": {"p_l": 30, "ev_ebit": 25}, "sic": "3571",
                    "sic_descricao": "Computers", "atual_ref": "exercício 2025"}}
    c = fv.candidatos_eua(art)
    assert "ev_ebit" not in c["JPM"].metricas and c["JPM"].raiz == "19617"
    assert c["AAPL"].taxonomia == (("SIC de 4 dígitos", "3571 Computers"),
                                   ("SIC de 3 dígitos", "357x"),
                                   ("SIC de 2 dígitos", "35xx"))
    assert c["AAPL"].ano_ref == 2025
    assert fv.entradas_eua(art["JPM"])["ev_ebit"].excluida == "financeira"
    assert fv.financeira_sic("6411") and not fv.financeira_sic("6500")
    assert not fv.financeira_sic(None)


def test_tesouro_papel_familia_e_chave():
    assert fv._partes_tesouro("TIPCAJ2032") == ("TIPCAJ", "TIPCA")
    assert fv._partes_tesouro("TSELIC2029") == ("TSELIC", "TSELIC")
    assert fv._partes_tesouro("CDB123") is None
    chaves = {"TIPCA2035", "TIPCAJ2035", "TSELIC2029", "TPRE2031"}
    assert fv.chave_tesouro("TSELIC2029", chaves) == "TSELIC2029"
    assert fv.chave_tesouro("TPREJ2031", chaves) == "TPRE2031"
    assert fv.chave_tesouro("TIPCAX2035", chaves) is None   # não é papel
    assert fv.chave_tesouro("TIPCAJ2035", {"TIPCA2035", "TIPCAJX"}) == "TIPCA2035"
    assert fv.chave_tesouro("TPRE2031", {"TPREJ2031"}) == "TPREJ2031"
    assert fv.chave_tesouro("TIPCA2040", {"TIPCA2045"}) is None


def test_candidatos_e_entradas_tesouro():
    hoje = date(2026, 9, 26)
    taxas = [{"security_key": "TIPCA2035", "sell_rate": 7.4,
              "maturity_date": "2035-05-15", "base_date": "2026-09-25"},
             {"security_key": "TSELIC2020", "sell_rate": 0.1,
              "maturity_date": "2020-03-01"},
             {"security_key": "LIXO", "sell_rate": 1}]
    c = fv.candidatos_tesouro(taxas, hoje)
    assert set(c) == {"TIPCA2035", "TSELIC2020"}
    assert c["TIPCA2035"].risco == pytest.approx(8.63, abs=0.01)
    assert "prazo" not in c["TSELIC2020"].metricas   # vencido
    e = fv.entradas_tesouro("TIPCA2035", taxas[0], [("2026-09-24", 7.3)])
    t = e["taxa_mercado"]
    assert t.atual.valor == 7.4 and t.frequencia == v.DIARIA
    assert "taxa real" in t.atual.nota


# -- despacho por classe -------------------------------------------------------------

_CARREGAR_FII_REAL = fv._carregar_fii


@pytest.fixture
def sem_io(monkeypatch):
    monkeypatch.setattr(fv, "arquivo", lambda: {})
    monkeypatch.setattr(fv, "_carregar_b3", lambda: ([], {}))
    monkeypatch.setattr(fv, "_carregar_fii", lambda: ([], None))
    monkeypatch.setattr(fv, "_carregar_taxas_tesouro", lambda: [])
    monkeypatch.setattr(fv, "_carregar_serie_tesouro", lambda k: [])


def test_ler_sem_cache_etf_cdb_e_eua_fora_do_universo(sem_io):
    val, comp = fv.ler_sem_cache("BOVA11", "BOVA11", "ETF Brasil", "BRL")
    assert val.tipo == f.ETF and "ETF" in val.motivo and comp.motivo
    val, comp = fv.ler_sem_cache("CDB-XYZ", "CDB Banco", "Renda Fixa", "BRL")
    assert val.linha("taxa_mercado").motivo.startswith("Não se aplica")
    assert "fora do Tesouro" in comp.motivo
    val, comp = fv.ler_sem_cache("ZZZZ", "Zzz", "Ações BR", "USD")
    assert "fora do universo americano" in comp.grupo.motivo


def test_ler_sem_cache_b3_com_universo(sem_io, monkeypatch):
    mult = [{"Ticker": t, "P/L": pl, "P/VP": 2, "DY": 0.03, "EV_EBIT": 10,
             "data": date(2025, 12, 31)}
            for t, pl in (("AAAA3", 10), ("BBBB3", 12), ("CCCC3", 14),
                          ("DDDD3", 16))]
    setores = {t["Ticker"]: {"SEGMENTO": "Seg", "SUBSETOR": "Sub",
                             "SETOR": "Set"} for t in mult}
    monkeypatch.setattr(fv, "_carregar_b3", lambda: (mult, setores))
    val, comp = fv.ler_sem_cache("AAAA3", "A", "Ações BR", "BRL")
    assert comp.linha("p_l").mediana_pares == 14
    assert val.linha("p_l").posicao_pares == v.ABAIXO
    assert val.grupo_pares == "3 pares do mesmo segmento (Seg)"


@pytest.mark.parametrize("erro, causa", [
    ("snapshot_deadline_exceeded", "estourou o prazo"),
    ("snapshot_hash_invalid", "não confere com o próprio hash"),
    ("codigo_novo_qualquer", "codigo_novo_qualquer"),
])
def test_ler_sem_cache_fii_nomeia_falha_da_vitrine(sem_io, monkeypatch, erro,
                                                   causa):
    # Em 26/09/2026 a vitrine falhou (prazo de 12 s, hash inválido) e a tela
    # mostrava P/VP "Dado não disponível" e pares "Ativo fora do universo":
    # o fundo parecia sem dado, quando o que falhou foi a leitura.
    vazio = pd.DataFrame()
    vazio.attrs["load_error"] = erro
    import core.market_read as mr
    monkeypatch.setattr(mr, "load_fii_methodology_inputs", lambda: vazio)
    monkeypatch.setattr(fv, "_carregar_fii", _CARREGAR_FII_REAL)
    val, comp = fv.ler_sem_cache("HGLG11", "HGLG11", "FII", "BRL")
    premissas = " ".join(val.premissas)
    assert any(x.startswith(fv.AVISO_VITRINE_FII_INDISPONIVEL)
               for x in val.premissas)
    assert causa in premissas and erro in premissas
    assert "fora do universo" not in comp.grupo.motivo
    assert causa in comp.grupo.motivo


def test_ler_sem_cache_fii_sem_falha_nao_inventa_aviso(sem_io):
    val, comp = fv.ler_sem_cache("HGLG11", "HGLG11", "FII", "BRL")
    assert "Vitrine de FIIs indisponível" not in " ".join(val.premissas)
    assert "fora do universo" in comp.grupo.motivo


def test_ler_nao_prende_falha_da_vitrine_no_cache(sem_io, monkeypatch):
    vazio = pd.DataFrame()
    vazio.attrs["load_error"] = "snapshot_deadline_exceeded"
    import core.market_read as mr
    monkeypatch.setattr(mr, "load_fii_methodology_inputs", lambda: vazio)
    monkeypatch.setattr(fv, "_carregar_fii", _CARREGAR_FII_REAL)
    chamadas = []

    def ler_contando(*args):
        chamadas.append(args)
        return fv._ler_como_dicts(*args)

    ler_contando.clear = lambda: chamadas.append("clear")
    monkeypatch.setattr(fv, "_ler_cache", ler_contando)
    fv.ler("HGLG11", "HGLG11", "FII", "BRL")
    assert chamadas[-1] == "clear"


def test_entradas_fii_tipo_desconhecido_nao_vira_nao_se_aplica():
    # Sem a linha do fundo (vitrine em falha) o tipo é desconhecido: dizer
    # que cap rate "só tem leitura em tijolo" para a HGLG11 era falso.
    assert fv.entradas_fii(None, None)["cap_rate"].excluida is None
    assert fv.entradas_fii({"tipo": "papel"}, None)["cap_rate"].excluida ==         "fii_nao_tijolo"


# -- publicador ----------------------------------------------------------------------

def test_publicador_historico_b3_descarta_lpa_implausivel():
    from scripts import publish_valuation_historico as pub
    anos = list(range(2015, 2021))
    fech = pd.DataFrame({"ticker": "AAAA3", "ano": anos,
                         "close": [10.0] * 6})
    demos = pd.DataFrame({"ticker": "AAAA3", "ano": anos,
                          "lucro": [100.0] * 6,
                          "lpa": [1.0, 1.0, 1.0, 0.001, 1.0, 1.0],
                          "patrimonio": [500.0] * 6})
    prov = pd.DataFrame({"ticker": ["AAAA3"], "ano": [2020], "total": [0.5]})
    saida, excl = pub.historico_b3(fech, demos, prov)
    h = saida["AAAA3"]["historico"]
    assert [a for a, _ in h["p_l"]] == [2015, 2016, 2017, 2019, 2020]
    assert excl["lpa_implausivel"] == 1
    assert h["dividend_yield"] == [[2020, 5.0]]
    assert h["p_vp"][0] == [2015, 2.0]


def test_publicador_volatilidade_precisa_da_janela():
    from scripts import publish_valuation_historico as pub
    precos = pd.DataFrame({"ticker": "A", "mes": range(40),
                           "preco": [10.0, 11.0] * 20})
    vol = pub.volatilidade(precos)
    assert vol["A"] > 0
    assert pub.volatilidade(precos.head(20)) == {}


def test_publicador_capitalizacao_corta_antes_do_degrau():
    from scripts import publish_valuation_historico as pub
    meses = pd.date_range("2019-01-31", periods=30, freq="ME")
    acoes = [1000.0] * 15 + [4000.0] * 15
    cap = pd.DataFrame({"symbol": "X", "date": meses,
                        "market_cap": [a * 10 for a in acoes]})
    precos = pd.DataFrame({"symbol": "X", "mes": meses, "close": 10.0})
    limpa, descartados = pub.capitalizacao_limpa(cap, precos)
    assert len(limpa) + descartados == 30
    assert (limpa["market_cap"] == 40000.0).all()


# -- seção e tela --------------------------------------------------------------------

def _info(classe="Ações BR", ticker="AAAA3", moeda="BRL"):
    return m.InfoBasica(ticker, ticker, classe, None, None, None, moeda,
                        None, None, 5.0)


def _leitor(info):
    alvo = _c("AAAA3", p_l=10.0)
    grupo = p.selecionar(alvo, [_c("BBBB3", p_l=12.0), _c("CCCC3", p_l=14.0),
                                _c("DDDD3", p_l=16.0)])
    val = v.montar(f.ACAO, {"p_l": v.Entrada(f.Dado(10.0, "Fonte <b>"),
                                             _hist([11, 12, 13, 12, 11]))},
                   pares={"p_l": grupo.valores("p_l")},
                   grupo_pares=grupo.descricao)
    return val, p.comparar(alvo, grupo)


def test_provedores_disponivel_e_sem_dados():
    sv = secoes.provedor_valuation(_info(), None, leitor=_leitor)
    sp = secoes.provedor_pares(_info(), None, leitor=_leitor)
    assert sv.estado == m.DISPONIVEL and sv.dados["linhas"][0]["chave"] == "p_l"
    assert sp.estado == m.DISPONIVEL and sp.dados["grupo"]["pares"]
    vazio = secoes.coletar(_info(), None)   # fixture do conftest: sem banco
    assert vazio["valuation"].estado == m.SEM_DADOS
    assert vazio["pares"].estado == m.SEM_DADOS


def test_cartoes_da_tela_so_com_tokens_e_escapados():
    from views import inteligencia_ativos as tela
    val, comp = _leitor(None)
    hv, hp = tela.corpo_valuation(val), tela.corpo_pares(comp)
    assert "Faixas de referência" in hv and "Premissas" not in hv
    assert "Interpretação" in hv and v.AVISO in hv
    assert "Fonte &lt;b&gt;" in hv or "Fonte <b>" not in hv
    for cab in ("Ativo", "Métrica", "Valor", "Mediana dos pares", "Diferença",
                "Interpretação"):
        assert cab in hp
    assert p.RODAPE in hp and "Como o grupo foi escolhido" not in hp
    for html in (hv, hp):
        assert "#" not in html.replace("&#", "")
        assert not VEREDITO.search(re.sub(r"<[^>]+>", " ", html))
