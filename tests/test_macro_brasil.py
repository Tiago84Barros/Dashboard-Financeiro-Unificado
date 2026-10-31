"""Macro do BCB no contexto das LLMs: Selic meta, IPCA 12m, CDI, Focus e Fisher.

Auditoria app4 de 04/10/2026 (LLM-A2/A7): o bloco de mercado só tinha
``public.macro``, onde o IPCA de 2026 era o acumulado no ano (3,11% contra
4,22% em 12 meses) e o "juro real ex ante" era Selic − IPCA realizado (10,64%
contra ~7,3% do Tesouro IPCA+). Nada aqui toca rede nem banco real.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from core import macro_brasil as mb

HOJE = date(2026, 10, 4)
AGORA = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def _obs(provider, code, periodo, valor, vintage=None):
    return {"provider": provider, "provider_code": code, "reference_period": periodo,
            "vintage_date": vintage, "value": valor, "is_forecast": provider == mb.PROVEDOR_FOCUS,
            "retrieved_at": AGORA}


def _mensais(ultimo: date, valores: list[float]) -> dict[date, float]:
    """``valores`` do mais antigo ao mais novo, terminando em ``ultimo``."""
    out, mes = {}, ultimo
    for v in reversed(valores):
        out[mes] = v
        mes = (mes - timedelta(days=1)).replace(day=1)
    return out


# Medido em 04/10/2026 (SGS via SOAP e Olinda).
OBS_MEDIDAS = [
    _obs(mb.PROVEDOR_SGS, "432", date(2026, 10, 3), 13.75),
    _obs(mb.PROVEDOR_SGS, "432", date(2026, 10, 4), 13.75),
    _obs(mb.PROVEDOR_SGS, "433", date(2026, 7, 1), 0.26),
    _obs(mb.PROVEDOR_SGS, "433", date(2026, 8, 1), -0.32),
    _obs(mb.PROVEDOR_SGS, "13522", date(2026, 7, 1), 4.44),
    _obs(mb.PROVEDOR_SGS, "13522", date(2026, 8, 1), 4.22),
    _obs(mb.PROVEDOR_FOCUS, "ipca_12m", date(2026, 9, 18), 4.60, date(2026, 9, 18)),
    _obs(mb.PROVEDOR_FOCUS, "ipca_12m", date(2026, 9, 25), 4.6475, date(2026, 9, 25)),
    _obs(mb.PROVEDOR_FOCUS, "selic_anual", date(2026, 12, 31), 13.5, date(2026, 9, 25)),
    _obs(mb.PROVEDOR_FOCUS, "selic_anual", date(2027, 12, 31), 12.0, date(2026, 9, 25)),
    _obs(mb.PROVEDOR_FOCUS, "selic_anual", date(2027, 12, 31), 12.25, date(2026, 9, 18)),
    _obs(mb.PROVEDOR_FOCUS, "ipca_anual", date(2026, 12, 31), 4.9915, date(2026, 9, 25)),
    _obs(mb.PROVEDOR_FOCUS, "ipca_anual", date(2027, 12, 31), 4.3118, date(2026, 9, 25)),
]
CDI = {date(2026, 9, 30): 0.050788, date(2026, 10, 1): 0.050788}


def test_fisher_nao_e_subtracao():
    assert mb.fisher(13.75, 4.22) == pytest.approx(9.1441, abs=1e-3)
    assert 13.75 - 4.22 - mb.fisher(13.75, 4.22) == pytest.approx(0.386, abs=1e-2)
    assert mb.fisher(None, 4.22) is None


def test_ipca_12m_composto_exige_doze_meses_consecutivos():
    doze = _mensais(date(2026, 8, 1), [0.3] * 11 + [-0.32])
    valor, ultimo = mb.ipca_12m_de_mensais(doze)
    assert ultimo == date(2026, 8, 1)
    assert valor == pytest.approx(((1.003 ** 11) * 0.9968 - 1) * 100)
    del doze[date(2026, 3, 1)]
    assert mb.ipca_12m_de_mensais(doze) is None
    assert mb.ipca_12m_de_mensais({}) is None


def test_cdi_anualizado_base_252():
    assert mb.cdi_anualizado(0.050788) == pytest.approx(13.65, abs=0.01)


def test_resumo_pega_a_ultima_observacao_e_a_ultima_pesquisa_focus():
    res = mb.resumo(OBS_MEDIDAS)
    assert res["432"] == {"valor": 13.75, "data": date(2026, 10, 4)}
    assert res["13522"]["valor"] == 4.22
    assert res["focus_ipca_12m"]["data"] == date(2026, 9, 25)
    # A pesquisa de 18/09 (12,25% para 2027) não vaza para a de 25/09.
    assert res["focus_selic_anual"]["por_ano"] == {2026: 13.5, 2027: 12.0}


def test_resumo_sem_13522_compoe_o_433():
    obs = [_obs(mb.PROVEDOR_SGS, "433", d, v)
           for d, v in _mensais(date(2026, 8, 1), [0.4] * 12).items()]
    res = mb.resumo(obs)
    assert res["13522"]["composto_do_433"] is True
    assert res["13522"]["valor"] == pytest.approx((1.004 ** 12 - 1) * 100)


def test_linhas_trazem_selic_meta_ipca_12m_cdi_focus_e_os_dois_juros_reais():
    texto = "\n".join(mb.linhas_macro_brasil(mb.resumo(OBS_MEDIDAS), CDI,
                                             origem="armazém local", hoje=HOJE))
    assert "Selic meta (Copom, SGS 432): 13,75% a.a., vigente em 04/10/2026" in texto
    assert "IPCA acumulado em 12 meses (SGS 13522): 4,22% até 08/2026" in texto
    assert "IPCA do mês (SGS 433): -0,32% em 08/2026" in texto
    assert "CDI (SGS 12): 0,050788% ao dia útil em 01/10/2026 = 13,65% a.a." in texto
    assert "Juro real EX POST" in texto and "9,14% a.a." in texto
    assert "Juro real EX ANTE aproximado" in texto and "8,70% a.a." in texto
    assert "Focus (mediana, pesquisa de 25/09/2026)" in texto
    assert "Selic no fim de 2027 12,00%" in texto and "IPCA de 2026 4,99%" in texto
    assert "DEFASADO" not in texto
    # O juro real aritmético da auditoria não reaparece.
    assert "10,64" not in texto and "9,53" not in texto


def test_fonte_ausente_e_nomeada_e_nao_some():
    texto = "\n".join(mb.linhas_macro_brasil({}, {}, origem="x", hoje=HOJE))
    assert "Selic meta (SGS 432): ausente" in texto
    assert "IPCA acumulado em 12 meses (SGS 13522): ausente" in texto
    assert "CDI (SGS 12): arquivo cdi_diario ausente" in texto
    assert "Focus (expectativas do mercado): ausente" in texto
    assert "Juro real" not in texto  # sem os dois componentes, não há conta


def test_serie_parada_vai_marcada_como_defasada():
    res = mb.resumo(OBS_MEDIDAS)
    texto = "\n".join(mb.linhas_macro_brasil(res, CDI, origem="x",
                                             hoje=HOJE + timedelta(days=30)))
    assert "DEFASADO: 30 dias" in texto     # Selic meta
    assert "DEFASADO: 39 dias" in texto     # Focus


def test_parse_focus():
    doze = mb.parse_focus_12m([{"Data": "2026-09-25", "Mediana": 4.6475},
                               {"Data": "x", "Mediana": 1}])
    assert doze == [{"provider": "bcb_focus", "provider_code": "ipca_12m",
                     "reference_period": date(2026, 9, 25),
                     "vintage_date": date(2026, 9, 25), "value": 4.6475,
                     "is_forecast": True}]
    anuais = mb.parse_focus_anuais([
        {"Indicador": "Selic", "Data": "2026-09-25", "DataReferencia": "2027", "Mediana": 12.0},
        {"Indicador": "IPCA", "Data": "2026-09-25", "DataReferencia": "2026", "Mediana": 4.9915},
        {"Indicador": "Câmbio", "Data": "2026-09-25", "DataReferencia": "2026", "Mediana": 5.3},
    ])
    assert [(o["provider_code"], o["reference_period"], o["vintage_date"]) for o in anuais] == [
        ("selic_anual", date(2027, 12, 31), date(2026, 9, 25)),
        ("ipca_anual", date(2026, 12, 31), date(2026, 9, 25)),
    ]


def test_consulta_focus_monta_url_com_cifrao_literal(monkeypatch):
    """Com ``params=`` o requests codifica ``$`` e o Olinda devolve corpo vazio."""
    import requests

    urls = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"value": []}

    monkeypatch.setattr(requests, "get", lambda url, timeout: urls.append(url) or _Resp())
    mb._consulta_focus("ExpectativasMercadoAnuais", 5, top="3", filter="Indicador eq 'IPCA'")
    assert "?$top=3&$filter=Indicador%20eq%20%27IPCA%27" in urls[0]


def test_arquivo_ida_e_volta_e_vencimento(tmp_path):
    destino = tmp_path / "macro_brasil.json.gz"
    dados = mb.serializar(AGORA, OBS_MEDIDAS)
    assert dados == mb.serializar(AGORA, list(reversed(OBS_MEDIDAS)))  # bytes estáveis
    destino.write_bytes(dados)
    pub = mb.carregar_publicado(destino, agora=AGORA)
    assert pub.gerado_em == AGORA
    assert mb.resumo(pub.observacoes) == mb.resumo(OBS_MEDIDAS)
    # A rotina de vitrines lê ``gerado_em`` no topo do arquivo.
    assert json.loads(gzip.decompress(dados))["gerado_em"] == AGORA.isoformat()
    vencido = AGORA + timedelta(days=mb.IDADE_MAXIMA_DIAS + 1)
    assert mb.carregar_publicado(destino, agora=vencido) is None
    assert mb.carregar_publicado(tmp_path / "ausente.json.gz") is None


def test_alvo_registrado_com_ingestao_antes_da_publicacao():
    from core.publicacao_agenda import POR_CHAVE
    from scripts import atualizar_vitrines as av

    alvo = POR_CHAVE["macro_brasil"]
    assert alvo.passos == (("scripts/ingerir_macro_brasil.py",),
                           ("scripts/publish_macro_brasil.py",))
    assert alvo.artefatos == ("data/public/macro_brasil.json.gz",)
    assert av.CARIMBO["macro_brasil"] == ("arquivo", "data/public/macro_brasil.json.gz")


def test_carimbo_da_rotina_le_o_arquivo(tmp_path, monkeypatch):
    from scripts import atualizar_vitrines as av

    (tmp_path / "m.json.gz").write_bytes(mb.serializar(AGORA, OBS_MEDIDAS))
    monkeypatch.setattr(av, "ROOT", tmp_path)
    assert av._carimbo_do_arquivo("m.json.gz") == AGORA


def test_publicador_recusa_coleta_parada(monkeypatch, tmp_path, capsys):
    from core.macro_data import database as db
    from scripts import publish_macro_brasil as pub

    class _Eng:
        def dispose(self):
            pass

    velha = [_obs(mb.PROVEDOR_SGS, "432", date.today() - timedelta(days=9), 13.75)]
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Eng())
    monkeypatch.setattr(mb, "ler_do_armazem", lambda e, hoje=None: velha)
    saida = tmp_path / "m.json.gz"
    assert pub.main(["--saida", str(saida)]) == 1
    assert not saida.exists()
    assert "rode scripts/ingerir_macro_brasil.py" in capsys.readouterr().out

    fresca = [_obs(mb.PROVEDOR_SGS, "432", date.today(), 13.75)]
    monkeypatch.setattr(mb, "ler_do_armazem", lambda e, hoje=None: fresca)
    assert pub.main(["--saida", str(saida)]) == 0
    assert saida.exists()


def test_ingestao_grava_unmapped_e_nomeia_a_fonte_que_falhou(monkeypatch, capsys):
    """``unmapped`` mantém as séries fora do score das carteiras."""
    from core.macro_data import database as db
    from core.macro_data import repository as repo
    from scripts import ingerir_macro_brasil as ing

    monkeypatch.setattr(mb, "baixar_sgs", lambda c, i, f: (
        ({date(2026, 10, 4): 13.75}, None) if c == "432" else ({}, "SOAP fora")))
    monkeypatch.setattr(mb, "baixar_focus", lambda hoje: ([], "Olinda fora"))
    indicadores, observacoes = [], []
    monkeypatch.setattr(repo, "upsert_indicator", lambda c, i: indicadores.append(i) or 1)
    monkeypatch.setattr(repo, "append_observation", lambda c, o: observacoes.append(o) or 1)

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    class _Eng:
        def begin(self):
            return _Ctx()

        def dispose(self):
            pass

    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Eng())
    assert ing.main([]) == 1  # fonte falhou: a rotina precisa ver
    assert {i.category for i in indicadores} == {"unmapped"}
    assert [(o.provider, o.provider_code, o.value) for o in observacoes] == [
        ("bcb_sgs", "432", 13.75)]
    relatorio = json.loads(capsys.readouterr().out)
    assert set(relatorio["falhas"]) == {"sgs_433", "sgs_13522", "focus"}


# ── contexto_mercado ─────────────────────────────────────────────────────────

def test_secao_bcb_le_o_armazem_e_tira_o_bcb_das_series_cruas(monkeypatch):
    import core.contexto_mercado as cm
    from core import rentabilidade
    from core.macro_data import database as db

    class _Eng:
        def dispose(self):
            pass

    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Eng())
    monkeypatch.setattr(mb, "ler_do_armazem", lambda e, hoje=None: OBS_MEDIDAS)
    monkeypatch.setattr(rentabilidade, "ler_cdi_publicado", lambda *a: CDI)
    texto = "\n".join(cm._macro_brasil())
    assert "(armazém local, BCB/SGS e Focus)" in texto
    assert "Selic meta (Copom, SGS 432): 13,75%" in texto and "13,65% a.a." in texto

    fatos = [{"provider": "bcb_sgs"}, {"provider": "bcb_focus"}, {"provider": "fred"}]
    assert cm._sem_secao_propria(fatos) == [{"provider": "fred"}]


def test_secao_bcb_sem_armazem_usa_o_arquivo_e_diz_por_que(monkeypatch):
    import core.contexto_mercado as cm
    from core import rentabilidade
    from core.macro_data import database as db

    publicado = mb.desserializar(mb.serializar(AGORA, OBS_MEDIDAS))
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: None)
    monkeypatch.setattr(mb, "carregar_publicado", lambda *a, **k: publicado)
    monkeypatch.setattr(rentabilidade, "ler_cdi_publicado", lambda *a: CDI)
    primeira = cm._macro_brasil()[0]
    assert "arquivo publicado em 04/10/2026" in primeira
    assert "armazém local não configurado" in primeira


def test_secao_bcb_sem_nada_nomeia_a_falha_e_ainda_traz_o_cdi(monkeypatch):
    import core.contexto_mercado as cm
    from core import rentabilidade
    from core.macro_data import database as db

    class _Parado:
        def dispose(self):
            pass

    def _cai(e, hoje=None):
        raise OSError("connection refused")

    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Parado())
    monkeypatch.setattr(mb, "ler_do_armazem", _cai)
    monkeypatch.setattr(mb, "carregar_publicado", lambda *a, **k: None)
    monkeypatch.setattr(rentabilidade, "ler_cdi_publicado", lambda *a: CDI)
    linhas = cm._macro_brasil()
    assert "indisponível" in linhas[0] and "connection refused" in linhas[0]
    assert "sem arquivo publicado recente" in linhas[0]
    assert linhas[1].startswith("    CDI (SGS 12): 0,050788%")


# ── seed_macro_bcb ───────────────────────────────────────────────────────────

def test_seed_calcula_juro_real_por_fisher_sobre_ipca_12m():
    """2026: Selic 13,75% e IPCA 12m 4,22% -> 9,14%, não 13,75 − 3,11 (acumulado no ano)."""
    from scripts import seed_macro_bcb as seed

    mensais_2025 = [0.35] * 12
    mensais_2026 = [0.4, 0.5, 0.3, 0.4, 0.3, 0.6, 0.26, -0.32]
    idx = pd.date_range("2025-01-01", periods=20, freq="MS")
    ipca = pd.DataFrame({"ipca": mensais_2025 + mensais_2026}, index=idx)
    selic = pd.DataFrame({"selic": [15.0, 13.75]},
                         index=pd.to_datetime(["2025-12-31", "2026-10-04"]))
    vazio = {n: pd.DataFrame(columns=[n]) for n in seed.SERIES}
    df = seed._build_macro({**vazio, "ipca": ipca, "selic": selic}).set_index("ano")

    acumulado_2026 = (pd.Series(mensais_2026) / 100 + 1).prod() * 100 - 100
    doze_2026 = (pd.Series((mensais_2025 + mensais_2026)[-12:]) / 100 + 1).prod() * 100 - 100
    # A coluna ``ipca`` não muda de semântica: o cenário automático lê o acumulado.
    assert df.loc[2026, "ipca"] == pytest.approx(acumulado_2026)
    assert df.loc[2026, "juros_real_ex_ante"] == pytest.approx(
        (1.1375 / (1 + doze_2026 / 100) - 1) * 100)
    ano_2025 = (1.0035 ** 12 - 1) * 100
    assert df.loc[2025, "juros_real_ex_ante"] == pytest.approx(
        (1.15 / (1 + ano_2025 / 100) - 1) * 100)
    assert "_ipca_12m" not in df.columns
