"""Trajetória das séries de mercado no contexto das LLMs.

O que se prende aqui é o que levaria a LLM a uma conclusão errada sem nada
quebrar: comparação com um ponto distante demais do alvo, janela de meses
chamada de "histórico", variação de taxa em % em vez de p.p., e fonte que
falha sumindo da seção. Nada aqui toca rede.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.contexto_mercado as cm  # noqa: E402
import core.trajetoria_mercado as tm  # noqa: E402

_TAXA = tm.Serie("Juro X", "fonte X", "taxa")
_PRECO = tm.Serie("Dólar X", "fonte Y", "preco", "R$ ", 2)


def _diaria(inicio: date, fim: date, valor):
    d, out = inicio, {}
    while d <= fim:
        out[d] = valor(d)
        d += timedelta(days=1)
    return out


def test_resumir_acha_os_pontos_de_comparacao_e_o_percentil():
    serie = {date(2026, 1, 1): 10.0, date(2026, 7, 1): 12.0,
             date(2026, 9, 1): 11.0, date(2026, 10, 1): 10.5}
    r = tm.resumir(serie, hoje=date(2026, 10, 1))
    assert r.data == date(2026, 10, 1) and r.valor == 10.5
    assert r.atras[1] == (date(2026, 9, 1), 11.0)
    assert r.atras[3] == (date(2026, 7, 1), 12.0)
    assert r.atras[12] is None
    assert (r.minimo, r.maximo, r.n) == (10.0, 12.0, 4)
    assert r.percentil == 50.0  # 10,0 e 10,5 de 4 observações


def test_resumir_corta_a_janela_em_dez_anos():
    serie = {date(2010, 1, 1): 1.0, date(2020, 1, 1): 5.0, date(2026, 1, 1): 3.0}
    r = tm.resumir(serie, hoje=date(2026, 1, 1))
    assert r.inicio == date(2020, 1, 1) and r.minimo == 3.0 and r.n == 2


def test_resumir_ignora_nan_e_vazio():
    assert tm.resumir({}) is None
    assert tm.resumir({date(2026, 1, 1): float("nan")}) is None


def test_taxa_varia_em_pontos_percentuais_com_virgula():
    serie = _diaria(date(2025, 9, 1), date(2026, 10, 1),
                    lambda d: 15.0 if d < date(2026, 8, 15) else 13.65)
    texto = tm.linha(_TAXA, tm.resumir(serie, hoje=date(2026, 10, 1)))
    assert "13,65% em 01/10/2026" in texto
    assert "há 1 mês 13,65% (+0,00 p.p.)" in texto
    assert "há 3 meses 15,00% (-1,35 p.p.)" in texto
    assert "p,p," not in texto


def test_preco_varia_em_percentual():
    serie = _diaria(date(2025, 9, 1), date(2026, 10, 1),
                    lambda d: 5.0 if d < date(2026, 9, 15) else 4.5)
    texto = tm.linha(_PRECO, tm.resumir(serie, hoje=date(2026, 10, 1)))
    assert "R$ 4,50 em 01/10/2026" in texto
    assert "há 12 meses R$ 5,00 (-10,0%)" in texto


def test_ponto_longe_demais_do_alvo_nao_vale_pela_comparacao():
    """Buraco de meses na série: o ponto de antes do buraco não é "há 1 mês"."""
    serie = {date(2026, 3, 1): 4.0, date(2026, 10, 1): 5.0}
    texto = tm.linha(_PRECO, tm.resumir(serie, hoje=date(2026, 10, 1)))
    assert "há 1 mês: sem dado (último ponto antes do alvo é de 01/03/2026)" in texto
    assert "há 12 meses: sem dado (série começa em 03/2026)" in texto


def test_serie_mensal_tolera_o_dia_1():
    serie = {date(2026, m, 1): float(m) for m in range(1, 10)}
    s = tm.Serie("IPCA X", "fonte", "taxa", mensal=True)
    texto = tm.linha(s, tm.resumir(serie, hoje=date(2026, 10, 8)), hoje=date(2026, 10, 8))
    assert "há 1 mês 8,00%" in texto
    assert "ÚLTIMO PONTO" not in texto  # 37 dias é a defasagem normal de divulgação


def test_janela_curta_e_ultimo_ponto_velho_sao_nomeados():
    serie = _diaria(date(2026, 4, 14), date(2026, 9, 1), lambda d: 100.0)
    texto = tm.linha(_PRECO, tm.resumir(serie), hoje=date(2026, 10, 1))
    assert "HISTÓRICO CURTO" in texto
    assert "ÚLTIMO PONTO HÁ 30 DIAS" in texto
    # A janela vai na mesma frase do percentil: a LLM copia só o trecho.
    assert "hoje no percentil 100 de 0,4 anos, histórico curto" in texto


def test_janela_longa_nao_e_marcada_como_curta():
    serie = _diaria(date(2016, 10, 1), date(2026, 10, 1), lambda d: float(d.year))
    texto = tm.linha(_TAXA, tm.resumir(serie, hoje=date(2026, 10, 1)))
    assert "10,0 anos" in texto and "HISTÓRICO CURTO" not in texto
    assert "percentil 100 de 10,0 anos." in texto


def test_cpi_vira_variacao_em_12_meses():
    indice = {date(2025, 8, 1): 100.0, date(2026, 7, 1): 102.0, date(2026, 8, 1): 103.0}
    yoy = tm.cpi_em_12_meses(indice)
    assert set(yoy) == {date(2026, 8, 1)}
    assert abs(yoy[date(2026, 8, 1)] - 3.0) < 1e-9


def test_resumo_da_linha_do_sql():
    r = tm._resumo_da_linha({
        "d0": datetime(2026, 10, 7, tzinfo=timezone.utc), "v0": 5.0, "n": 10,
        "inicio": date(2021, 8, 23), "minimo": 4.5, "maximo": 6.3, "percentil": 22.0,
        "d1": date(2026, 9, 5), "v1": 5.1, "d3": None, "v3": None,
        "d12": date(2025, 10, 6), "v12": 5.35})
    assert r.data == date(2026, 10, 7)
    assert r.atras[1] == (date(2026, 9, 5), 5.1) and r.atras[3] is None


def test_falhas_sao_nomeadas_e_nada_levanta():
    def _quebra():
        raise RuntimeError("caiu")

    linhas = tm.linhas_trajetoria(supabase=_quebra, cdi=dict,
                                  bcb=lambda: (None, "sem arquivo"),
                                  eua=lambda: (None, "sem armazém"),
                                  hoje=date(2026, 10, 8))
    texto = "\n".join(linhas)
    assert linhas[0].startswith("TRAJETÓRIA")
    assert "Dólar, bolsa e Tesouro: falha na leitura (caiu)" in texto
    assert "cdi_diario ausente ou ilegível" in texto
    assert "IPCA acumulado em 12 meses: indisponível (sem arquivo)" in texto
    assert "EUA: indisponíveis (sem armazém)" in texto


def test_sem_supabase_e_nomeado():
    assert "Supabase indisponível" in tm.linhas_supabase(None)[0]


def test_ipca_12m_das_observacoes_do_bcb():
    obs = [{"provider": "bcb_sgs", "provider_code": "13522",
            "reference_period": date(2026, m, 1), "value": 4.0 + m / 10}
           for m in range(1, 9)]
    obs.append({"provider": "bcb_sgs", "provider_code": "433",
                "reference_period": date(2026, 8, 1), "value": 0.5})
    texto = tm.linha_ipca_12m(obs, "arquivo publicado", hoje=date(2026, 10, 8))
    assert "4,80% em 01/08/2026" in texto and "arquivo publicado" in texto


def test_eua_cai_para_os_insumos_publicados_e_diz_a_janela(monkeypatch):
    import core.macro_data.database as db
    from core.macro_data import insumos_publicados as ip

    agora = datetime.now(timezone.utc)
    obs = [{"provider": "fred", "provider_code": "DGS10", "country_code": "US",
            "unit": "%", "reference_period": date.today() - timedelta(days=d),
            "value": 4.0, "retrieved_at": agora, "released_at": None,
            "vintage_date": None, "is_forecast": False, "is_preliminary": False}
           for d in (1, 2)]
    insumos = ip.desserializar(ip.serializar(agora, [], obs))
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: None)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda *a, **k: insumos)
    series, origem = cm._series_eua()
    assert set(series) == {"DGS10"}
    assert "só as últimas 24 observações" in origem
    assert "armazém local não configurado" in origem


def test_bloco_traz_a_secao_de_trajetoria(monkeypatch):
    monkeypatch.setattr(cm, "_macro_supabase_cache", lambda: ["  MACRO-SUPA"])
    monkeypatch.setattr(cm, "_macro_local", lambda: ["  MACRO-LOCAL"])
    monkeypatch.setattr(cm, "_macro_brasil", lambda: ["  MACRO-BCB"])
    monkeypatch.setattr(cm, "_trajetoria_cache", lambda: ["TRAJETÓRIA (x):", "  DÓLAR"])
    texto = cm.bloco_contexto_mercado(noticias_gerais=False)
    assert texto.index("MACRO-LOCAL") < texto.index("TRAJETÓRIA") < texto.index("DÓLAR")


def test_regra_da_cadeia_chega_aos_chats_de_texto_livre(monkeypatch):
    import core.llm_ativo as llm_ativo
    import core.llm_carteira as llm_carteira
    import core.llm_fii as llm_fii
    import core.llm_financeiro as llm_fin
    import core.llm_global as llm_global

    capturado = []

    def _falso(messages, **_):
        capturado.append(messages[0]["content"])
        return "ok"

    for mod in (llm_ativo, llm_carteira, llm_fii, llm_fin, llm_global):
        monkeypatch.setattr(mod, "_chat_complete", _falso)
    llm_ativo.chat_com_ativo("CTX", [], "oi", mercado="b3", ticker="BBAS3")
    llm_carteira.chat_com_carteira("CTX", [], "oi", classe="geral")
    llm_fii.chat_com_fiis("CTX", [], "oi")
    llm_fin.chat_com_financas("CTX", [], "oi")
    llm_global.chat_com_portfolio_global("CTX", [], "oi")
    assert len(capturado) == 5
    for system in capturado:
        assert cm.REGRA_CADEIA_TRANSMISSAO in system
        assert cm.REGRA_CONTEXTO_MERCADO in system


def test_regra_da_cadeia_fica_fora_dos_prompts_que_vetam():
    """Regra nova em prompt que veta muda o veto: precisa de medição própria."""
    for nome in ("b3_portao_pit", "dossie_b3", "llm_b3", "llm_estrategia",
                 "portfolio_report_b3", "portfolio_report_us",
                 "inteligencia_ativos/portfolio_fit", "inteligencia_ativos/leitura_relatorios"):
        fonte = (_RAIZ / "core" / f"{nome}.py").read_text(encoding="utf-8")
        assert "REGRA_CADEIA_TRANSMISSAO" not in fonte, nome
