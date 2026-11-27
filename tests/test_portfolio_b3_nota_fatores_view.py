"""Partes puras da seção "nota + fatores" (views/portfolio_b3_nota_fatores.py)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import core.b3_nota_fatores as nf
import views.portfolio_b3_nota_fatores as v

ROOT = Path(__file__).resolve().parents[1]


def _precos(tickers, meses=48):
    idx = pd.date_range("2023-01-31", periods=meses, freq="ME")
    return pd.DataFrame({t: 100 * (1 + 0.001 * k) ** np.arange(meses)
                         for k, t in enumerate(tickers)}, index=idx)


def test_formar_hoje_usa_todo_nome_com_nota_de_qualquer_segmento():
    tickers = list("ABCDEFGHIJ")
    resultados = [{"score_proximo": {t: float(k) for k, t in enumerate(tickers[:5])}},
                  {"score_proximo": {t: float(k) for k, t in enumerate(tickers[5:], 5)}},
                  {"score_proximo": {}}]
    out = v.formar_hoje(resultados, _precos(tickers), {}, perfil="X",
                        hoje="2026-11-19")
    assert out["cobertura"]["n"] == 10
    assert len(out["pesos"]) == 7
    assert out["safra"] == 2026 and out["previa"] is False
    assert out["inicio"] == pd.Timestamp("2026-04-01")
    assert out["perfil"] == "X"


def test_formar_hoje_antes_de_abril_e_previa():
    out = v.formar_hoje([{"score_proximo": {"A": 1.0, "B": 2.0}}], _precos("AB"), {},
                        perfil="X", hoje="2027-02-10")
    assert out["safra"] == 2027 and out["previa"] is True
    assert out["inicio"] == pd.Timestamp("2027-02-01")


def test_formar_hoje_sem_nota_devolve_none():
    assert v.formar_hoje([{"score_proximo": {}}], pd.DataFrame(), {}, perfil="X") is None


def test_tabela_para_exibicao_so_quem_fica_em_ordem_de_sinal():
    out = nf.carteira_nota_fatores(list("ABCDEFGHIJ"),
                                   {t: float(k) for k, t in enumerate("ABCDEFGHIJ")},
                                   _precos("ABCDEFGHIJ"), {}, pd.Timestamp("2026-04-01"))
    t = v.tabela_para_exibicao(out["tabela"])
    fica = out["tabela"][out["tabela"]["fica"]]
    assert set(t["Ticker"]) == set(fica.index) == set(out["pesos"])
    esperado = sorted(fica.index, key=lambda k: (-fica.loc[k, "sinal"], k))
    assert list(t["Ticker"]) == esperado
    assert t["Sinal"].is_monotonic_decreasing
    assert (t["Volatilidade mensal (%)"] >= 0).all()      # sinal invertido do lowvol
    assert v.tabela_para_exibicao(pd.DataFrame()).empty


def test_avisos_da_medicao():
    assert v.avisos_da_medicao(None, [], "Equilibrado (padrão)")
    dados = {"perfil": "Equilibrado (padrão)"}
    assert v.avisos_da_medicao(dados, [], "Equilibrado (padrão)") == []
    avisos = v.avisos_da_medicao(dados, ["medição x ≠ y"], "Outro")
    assert len(avisos) == 2 and "versão anterior" in avisos[0] and "Outro" in avisos[1]


def test_medicao_gravada_casa_com_o_codigo():
    """A medição no repositório tem de ser da versão atual da regra."""
    import core.b3_oos_carteira as oos
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import RECOMENDADO
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

    dados = json.loads((ROOT / "data" / "oos_nota_fatores_b3.json").read_text(encoding="utf-8"))
    assert oos.vencida(dados, SCORE_VERSION, PRESETS_VERSION, versao_medicao=nf.VERSAO) == []
    assert dados["perfil"] == RECOMENDADO
    assert dados["regra"]["corte"] == pytest.approx(nf.CORTE)
    assert dados["nota_fatores"]["vs_equal_weight"]["n_safras"] == len(dados["safras"])


def test_grau_de_confianca_nomeia_a_medicao_gravada():
    from core.confianca_secao import nota_medicao_nota_fatores
    nota = nota_medicao_nota_fatores()
    assert nf.VERSAO in nota and "oos_nota_fatores_b3.json" in nota
    assert "VENCIDA" not in nota


def test_series_do_acumulado_da_medicao():
    dados = {"nota_fatores": {"safras": [
                 {"safra": 2017, "liquido": 0.10, "equal_weight": 0.08},
                 {"safra": 2018, "liquido": -0.05, "equal_weight": 0.01}]},
             "comparacoes": {"tirar_30_pela_nota": {"safras": [
                 {"safra": 2017, "liquido": 0.09}, {"safra": 2018, "liquido": 0.0}]}}}
    s = v.series_do_acumulado(dados)
    assert s == {"nota_fatores": {2017: 0.10, 2018: -0.05},
                 "equal_weight": {2017: 0.08, 2018: 0.01},
                 "so_nota": {2017: 0.09, 2018: 0.0}}
    assert v.series_do_acumulado(None) == {}
    assert v.series_do_acumulado({}) == {}


def test_acumulado_da_medicao_gravada():
    """Na medição do repositório, as três séries cobrem as mesmas safras."""
    from core.b3_safras import acumulado_das_safras
    dados = json.loads((ROOT / "data" / "oos_nota_fatores_b3.json").read_text(encoding="utf-8"))
    acum = acumulado_das_safras(v.series_do_acumulado(dados))
    assert acum["safras"] == dados["safras"]
    assert acum["pulados"] == [] and acum["fora"] == []
    nf_ = acum["series"]["nota_fatores"]
    esperado = float(np.prod([1 + t["liquido"] for t in dados["nota_fatores"]["safras"]]))
    assert nf_["valor_final"] == pytest.approx(esperado)


# ── Estratégia padrão (decisão de 26/11/2026: rota de valor) ────────────────

def test_rota_de_valor_e_a_estrategia_padrao():
    """O seletor abre na primeira opção (`index=0`); ela tem de ser a rota de
    valor, e a nota + fatores e a líder de cada segmento continuam nele."""
    import ast
    assert list(v.ESTRATEGIAS) == [v.ESTRATEGIA_ROTA_VALOR, v.ESTRATEGIA_NOTA_FATORES,
                                   v.ESTRATEGIA_LIDER]
    assert "(padrão)" in v.ESTRATEGIAS[v.ESTRATEGIA_ROTA_VALOR]
    assert all("padrão" not in r for k, r in v.ESTRATEGIAS.items()
               if k != v.ESTRATEGIA_ROTA_VALOR)
    import views.portfolio_b3_rota_valor as rv
    assert v.ESTRATEGIA_ROTA_VALOR == rv.BRACO == "rota_valor"
    arvore = ast.parse((ROOT / "views" / "portfolio_b3_nota_fatores.py")
                       .read_text(encoding="utf-8"))
    fn = next(no for no in ast.walk(arvore)
              if isinstance(no, ast.FunctionDef) and no.name == "escolher_estrategia")
    radio = next(no for no in ast.walk(fn) if isinstance(no, ast.Call)
                 and isinstance(no.func, ast.Attribute) and no.func.attr == "radio")
    kws = {kw.arg: kw.value for kw in radio.keywords}
    assert isinstance(kws["index"], ast.Constant) and kws["index"].value == 0
    assert isinstance(kws["key"], ast.Name) and kws["key"].id == "CHAVE_ESTRATEGIA"
    assert v.CHAVE_ESTRATEGIA == "pb3_estrategia"


@pytest.mark.parametrize("constante,rota", [
    ("ESTRATEGIA_ROTA_VALOR", "_rota_rota_valor"),
    ("ESTRATEGIA_NOTA_FATORES", "_rota_nota_fatores"),
])
def test_tela_b3_rotas_amplas_vem_antes_do_portao_e_retornam(constante, rota):
    """Na tela, a rota de valor (padrão) e a nota + fatores desviam cada uma
    para a sua rota e retornam antes do cabeçalho das líderes -- portanto
    antes do portão de LLM e do botão de salvar. A líder segue pelo caminho
    de antes."""
    import ast
    fonte = (ROOT / "views" / "portfolio_b3.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    render = next(no for no in arvore.body
                  if isinstance(no, ast.FunctionDef) and no.name == "render")
    desvio = [no for no in ast.walk(render) if isinstance(no, ast.If)
              and constante in ast.unparse(no.test)]
    assert len(desvio) == 1, f"render deve ter um único desvio para {constante}"
    corpo = desvio[0].body
    assert isinstance(corpo[-1], ast.Return)
    assert any(isinstance(no, ast.Expr) and isinstance(no.value, ast.Call)
               and isinstance(no.value.func, ast.Name)
               and no.value.func.id == rota for no in corpo)
    escolha = [no for no in ast.walk(render) if isinstance(no, ast.Assign)
               and "escolher_estrategia()" in ast.unparse(no.value)]
    assert len(escolha) == 1 and escolha[0].lineno < desvio[0].lineno
    assert "_estrategia" in ast.unparse(desvio[0].test)
    linhas = fonte.splitlines()
    cab = next(i for i, linha in enumerate(linhas, 1)
               if "Empresas líderes para o próximo ano" in linha and "_sec_hdr" in linha)
    assert desvio[0].lineno < cab
    portao = next(i for i, linha in enumerate(linhas, 1)
                  if "Gate qualitativo: pré-avalia" in linha)
    assert desvio[0].lineno < portao


@pytest.mark.parametrize("rota_nome,secao", [
    ("_rota_nota_fatores", "_nota_fatores.render_nota_fatores"),
    ("_rota_rota_valor", "_rota_valor.render_rota_valor"),
])
def test_rotas_amplas_mostram_secao_safras_e_metodologia(rota_nome, secao):
    import ast
    arvore = ast.parse((ROOT / "views" / "portfolio_b3.py").read_text(encoding="utf-8"))
    rota = next(no for no in arvore.body
                if isinstance(no, ast.FunctionDef) and no.name == rota_nome)
    chamadas = [ast.unparse(no.func) for no in ast.walk(rota) if isinstance(no, ast.Call)]
    for nome in (secao, "render_safras", "_render_metodologia_portfolio"):
        assert nome in chamadas, nome
    # Sem LLM nem gravação de carteira-modelo nesta rota.
    texto = ast.unparse(rota)
    for proibido in ("save_b3_portfolio", "avaliar", "_gate", "intel"):
        assert proibido not in texto, proibido
