"""Cenários análogos: "da última vez que o macro esteve assim, o que aconteceu?".

O que se prende aqui é o que faria a LLM contar uma história de "da última vez"
sem base: IPCA que ainda não tinha saído no mês (olhar o futuro), mês corrente
contado como desfecho, meses vizinhos contados como episódios independentes,
dimensão ausente pesando como discordância, faixa com três casos, e falha de
leitura que some do contexto em vez de ser nomeada.
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.contexto_mercado as cm  # noqa: E402
from core.memoria_mercado import cenarios_macro as cmac  # noqa: E402

HOJE = date(2026, 10, 8)


def _diaria(inicio, fim, valor):
    d, saida = inicio, {}
    while d <= fim:
        saida[d] = valor(d) if callable(valor) else valor
        d += timedelta(days=1)
    return saida


def _mensal(inicio, fim, valor):
    saida, m = {}, inicio
    while m <= fim:
        saida[m] = valor(m) if callable(valor) else valor
        m = cmac._soma_meses(m, 1)
    return saida


def _painel(**troca):
    base = dict(
        selic=_diaria(date(2015, 1, 1), HOJE, 10.0),
        ipca12=_mensal(date(2015, 1, 1), date(2026, 8, 1), 4.0),
        usd=_mensal(date(2015, 1, 1), date(2026, 9, 1), 5.0),
        us10=_diaria(date(2015, 1, 1), HOJE, 4.0),
        bova=_mensal(date(2015, 1, 1), date(2026, 9, 1), 100.0),
        pl={},
    )
    base.update(troca)
    return cmac.painel_mensal(hoje=HOJE, inicio=date(2016, 1, 1), **base)


def test_ipca_do_mes_so_entra_no_mes_seguinte():
    ipca = {date(2026, 8, 1): 4.0, date(2026, 9, 1): 9.9}   # setembro sai em outubro
    p = _painel(ipca12=ipca)
    set26 = next(x for x in p if x["mes"] == "2026-09")
    assert set26["estado"]["ipca12"] == 4.0 and set26["refs"]["ipca12"] == "2026-08"
    assert p[-1]["estado"]["ipca12"] == 9.9


def test_mes_corrente_nao_e_desfecho_de_ninguem():
    p = _painel()
    assert p[-1]["mes"] == "2026-10"
    # setembro/2025 + 12 = setembro/2026, fechado; outubro/2025 + 12 = mês corrente
    assert next(x for x in p if x["mes"] == "2025-09")["depois"]["bova_12"] == 0.0
    assert next(x for x in p if x["mes"] == "2025-10")["depois"]["bova_12"] is None
    assert next(x for x in p if x["mes"] == "2026-07")["depois"]["bova_3"] is None


def test_fechamento_mensal_no_dia_1_vale_o_fim_do_mes():
    bova = {date(2020, 3, 1): 50.0, date(2020, 4, 1): 80.0}
    s = cmac._Serie(cmac._bova_no_fim_do_mes(bova), 10)
    assert s.em(date(2020, 3, 15)) is None            # nem existia em 15/03
    assert s.em(date(2020, 3, 31)) == (50.0, date(2020, 3, 31))


def test_ausente_sai_do_denominador_e_a_cobertura_diz():
    hoje = {"selic": 10.0, "selic_6m": -1.0, "pl": None}
    fator, cob = cmac.similaridade(hoje, {"selic": 10.0, "selic_6m": -1.0, "pl": 15.0})
    assert fator == 100.0
    assert cob == pytest.approx(0.40)


def _linha(mes, selic, bova_12=0.1):
    return {"mes": mes, "estado": {"selic": selic, "selic_6m": 0.0},
            "refs": {}, "depois": {"bova_12": bova_12}}


def _meses(n, inicio=2010):
    return [f"{inicio + i // 12}-{i % 12 + 1:02d}" for i in range(n)]


def test_meses_vizinhos_sao_um_episodio_so_e_o_recente_fica_fora():
    meses = _meses(60)
    painel = [_linha(m, 10.0 if m.startswith("2011") else 30.0) for m in meses[:-1]]
    painel.append(_linha(meses[-1], 10.0))
    # os 12 meses de 2011 são idênticos a hoje: um episódio, não doze
    res = cmac.analogos(painel)
    assert [a.mes for a in res.episodios] == ["2011-01"]
    # o fim do painel imita hoje, mas está dentro da exclusão recente
    painel2 = [_linha(m, 30.0) for m in meses[:-12]] + [_linha(m, 10.0) for m in meses[-12:]]
    assert cmac.analogos(painel2).episodios == ()


def test_menos_de_oito_episodios_nao_tem_faixa():
    eps = [cmac.Analogo(m, 90.0, 1.0, {}, {"bova_12": v})
           for m, v in (("2011-01", 0.2), ("2013-01", -0.1), ("2015-01", 0.05))]
    texto = cmac._resumo(eps)
    assert "em 2 de 3 episódios (n=3, abaixo de 8" in texto
    assert "mediana" not in texto and "p10" not in texto
    eps = [cmac.Analogo(f"{2000 + 2 * i}-01", 90.0, 1.0, {}, {"bova_12": 0.01 * i})
           for i in range(10)]
    assert "mediana" in cmac._resumo(eps) and "experimental, abaixo de 30" in cmac._resumo(eps)


def test_sem_analogo_diz_que_nao_ha_e_mantem_o_contraponto():
    meses = _meses(60)
    painel = [_linha(m, 30.0) for m in meses[:-1]] + [_linha(meses[-1], 10.0)]
    texto = "\n".join(cmac.linhas_cenarios({"painel": painel}, "gerado em 08/10/2026"))
    assert "o cenário de hoje NÃO tem análogo no histórico" in texto
    assert ("Síntese para citar: o cenário de hoje não tem análogo no histórico "
            "desde 01/2010") in texto
    assert "não cite 'da última vez' sem base" in texto
    assert "Contraponto, todos os 60 meses" in texto
    assert "não é previsão" in texto or "não previsão" in texto


def test_texto_cola_a_ressalva_do_pl_e_lista_o_desfecho():
    meses = _meses(60)
    painel = [_linha(m, 10.0 if m == "2011-06" else 30.0, bova_12=0.25) for m in meses[:-1]]
    painel.append({**_linha(meses[-1], 10.0), "estado": {"selic": 10.0, "selic_6m": 0.0,
                                                          "pl": 20.9}})
    linhas = cmac.linhas_cenarios({"painel": painel}, "gerado em 08/10/2026")
    assert "P/L mediano 20,9x" in linhas[1] and "NÃO o P/L do Ibovespa" in linhas[1]
    ep = next(x for x in linhas if x.startswith("  - 06/2011"))
    assert "→ depois: BOVA11 +25,0% em 12m" in ep
    assert "em 1 de 1 episódios (n=1" in "\n".join(linhas)
    assert linhas[2] == (
        "  Síntese para citar: da última vez com cenário parecido, que é também o "
        "mais parecido, 06/2011 (similaridade 100/100): BOVA11 +25,0% em 12m; BOVA11 "
        "subiu nos 12 meses seguintes em 1 de 1 episódios com 12 meses completos "
        "(n=1 no critério, caso a caso, sem faixa); em todos os 60 meses desde "
        "01/2010, BOVA11 teve mediana +25,0% em 12 meses e subiu em 100% deles — "
        "análogo é contexto histórico, não previsão.")


def test_sintese_traz_o_mais_recente_e_o_mais_parecido():
    eps = (cmac.Analogo("2016-12", 81.0, 1.0, {}, {"bova_12": 0.268}),
           cmac.Analogo("2024-08", 70.0, 1.0, {}, {"bova_3": -0.078, "bova_12": 0.045}),
           cmac.Analogo("2019-03", 75.0, 1.0, {}, {"bova_12": -0.05}))
    s = cmac._sintese(cmac.Resultado(hoje={}, episodios=eps, base={"n": 0}))
    assert s.startswith("da última vez com cenário parecido, 08/2024 (similaridade "
                        "70/100): BOVA11 −7,8% em 3m, +4,5% em 12m; o episódio mais "
                        "parecido foi 12/2016 (similaridade 81/100)")
    assert "em 2 de 3 episódios" in s and s.endswith("não previsão.")


def test_arquivo_publicado_ida_e_volta_e_marca_velho(tmp_path):
    agora = datetime(2026, 10, 8, 22, tzinfo=timezone.utc)
    caminho = tmp_path / "c.json.gz"
    caminho.write_bytes(cmac.serializar(agora, [_linha("2026-10", 10.0)], {"selic": {"n": 1}}))
    carga, origem = cmac.carregar_publicado(caminho, agora=agora + timedelta(hours=3))
    assert carga["versao"] == cmac.CENARIOS_MACRO_VERSAO and origem == "gerado em 08/10/2026"
    _, origem = cmac.carregar_publicado(caminho, agora=agora + timedelta(days=4))
    assert "4 dias atrás — VELHO" in origem
    assert cmac.carregar_publicado(tmp_path / "x.gz") == (None, "arquivo cenarios_analogos ausente")
    (tmp_path / "e.gz").write_bytes(gzip.compress(json.dumps({"schema": "outro"}).encode()))
    assert "esquema desconhecido" in cmac.carregar_publicado(tmp_path / "e.gz")[1]


def test_contexto_nomeia_a_falha_e_nao_inventa(monkeypatch):
    def _quebra(*a, **k):
        raise RuntimeError("gz ruim")

    monkeypatch.setattr(cmac, "carregar_publicado", _quebra)
    texto = "\n".join(cm._cenarios_analogos())
    assert "indisponível (falha na leitura: gz ruim)" in texto
    assert "não invente episódios" in texto
    monkeypatch.setattr(cmac, "carregar_publicado",
                        lambda: (None, "arquivo cenarios_analogos ausente"))
    assert "indisponível (arquivo cenarios_analogos ausente)" in cm._cenarios_analogos()[1]


def test_bloco_de_contexto_traz_a_secao_depois_da_trajetoria(monkeypatch):
    for nome in ("_macro_supabase_cache", "_macro_brasil", "_macro_local"):
        monkeypatch.setattr(cm, nome, lambda: [])
    monkeypatch.setattr(cm, "_trajetoria_cache", lambda: ["TRAJ"])
    monkeypatch.setattr(cm, "_cenarios_analogos", lambda: ["CENARIOS"])
    bloco = cm.bloco_contexto_mercado(noticias_gerais=False)
    assert bloco.index("TRAJ") < bloco.index("CENARIOS")
    assert "**Da última vez**" in cm.REGRA_CADEIA_TRANSMISSAO
    regra = cm.REGRA_CADEIA_TRANSMISSAO
    assert "nunca descreva 'da última vez' de memória" in regra
    assert "Síntese para citar" in regra and "duas seções" in regra


def test_alvo_e_carimbo_da_rotina():
    import scripts.atualizar_vitrines as av
    from core.publicacao_agenda import ALVOS, POR_CHAVE

    alvo = POR_CHAVE["cenarios_analogos"]
    assert alvo.passos == (("scripts/publish_cenarios_analogos.py",),)
    assert alvo.artefatos == ("data/public/cenarios_analogos.json.gz",)
    assert av.CARIMBO["cenarios_analogos"] == ("arquivo", alvo.artefatos[0])
    ordem = [a.chave for a in ALVOS]
    assert ordem.index("macro_brasil") < ordem.index("cenarios_analogos")
    assert ordem.index("valuation_historico") < ordem.index("cenarios_analogos")


def test_publicador_recusa_selic_parada(monkeypatch, capsys, tmp_path):
    import scripts.publish_cenarios_analogos as pub

    class _Eng:
        def connect(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def dispose(self):
            pass

    hoje = datetime.now(timezone.utc).date()
    dados = {"selic": {hoje - timedelta(days=30): 10.0}, "ipca12": {}, "usd": {},
             "us10": {}, "bova": {hoje: 100.0}, "pl": {}}
    import core.macro_data.database as db
    import scripts.publish_fii_selection_from_local as fsl
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Eng())
    monkeypatch.setattr(fsl, "_warehouse_url", lambda: "x")
    monkeypatch.setattr("sqlalchemy.create_engine", lambda url: _Eng())
    monkeypatch.setattr(pub, "coletar", lambda a, b: dados)
    saida = tmp_path / "c.json.gz"
    assert pub.main(["--saida", str(saida)]) == 1
    assert "selic sem dado nos últimos 5 dias" in capsys.readouterr().out
    assert not saida.exists()


def test_sql_roda_no_armazem_de_verdade():
    """As consultas só se provam executando; só leitura, pulado sem armazém."""
    try:
        from sqlalchemy import create_engine, text

        from scripts.publish_fii_selection_from_local import _warehouse_url
        # o .env não chega aos testes: a base macro sai da mesma URL do container
        mercado = create_engine(_warehouse_url())
        macro = create_engine(_warehouse_url().rsplit("/", 1)[0] + "/macro_staging")
        with mercado.connect() as conn, macro.connect() as conn2:
            conn.execute(text("SELECT 1"))
            conn2.execute(text("SELECT 1"))
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")
    import scripts.publish_cenarios_analogos as pub
    try:
        with macro.connect() as cmc, mercado.connect() as cmerc:
            dados = pub.coletar(cmc, cmerc)
    finally:
        macro.dispose()
        mercado.dispose()
    assert all(v is not None for v in dados["us10"].values())
    assert len(dados["usd"]) > 200 and 1 < dados["usd"][date(2020, 1, 1)] < 10
    assert dados["bova"] and min(dados["bova"]).year <= 2010
    painel = cmac.painel_mensal(hoje=HOJE, **dados)
    assert len(painel) > 180
