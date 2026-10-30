"""Pregões recentes da B3 no contexto das LLMs (seção MOVIMENTO RECENTE).

Em 29/10/2026 o chat da carteira analisou duas ações a partir do fechamento
mensal de 05/10 e chamou de manchete sem confirmação uma queda que o pregão de
26/10 já tinha medido. O que se prende:

* o publicador lê o COTAHIST, recusa fita parada e grava bytes determinísticos;
* a seção traz termômetro, ativos em questão, amplitude e a síntese para citar,
  e nomeia arquivo ausente, velho ou atrasado;
* salto de preço com o mercado parado sai marcado como possível desdobramento;
* a trajetória mensal velha ganha o "SUPERADO" na mesma frase;
* as ferramentas e a regra do chat apontam para o dado diário.

Tickers, nomes e preços são sintéticos.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import core.llm_ferramentas as lf
import core.pregoes_recentes as pr
import core.trajetoria_mercado as tm
import scripts.publish_pregoes_recentes_b3 as pub

AGORA = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _datas(n: int, inicio: date = date(2026, 9, 1)) -> list[str]:
    """``n`` dias úteis a partir de ``inicio``: com 30, de 01/09 a 12/10/2026."""
    d, saida = inicio, []
    while len(saida) < n:
        if d.weekday() < 5:
            saida.append(d.isoformat())
        d += timedelta(days=1)
    return saida


def _art(series: dict, *, n: int = 30, gerado: str | None = "2026-10-12T22:00:00+00:00",
         volumes: dict | None = None) -> dict:
    pregoes = _datas(n)
    return {"gerado_em": gerado, "base_ate": pregoes[-1], "pregoes": pregoes,
            "por_ticker": {tk: {"t": t, "n": f"NOME {tk}",
                                "v": (volumes or {}).get(tk, 1000), "c": list(c)}
                           for tk, (t, c) in series.items()}}


# BOVA11: alta em 29/09, queda de 15% em 09/10 (índices 20 e 28 de 30 pregões).
BOVA = ("etf", [100.0] * 20 + [110.0] * 8 + [93.5, 94.0])
SERIES = {
    "BOVA11": BOVA,
    # −50% em 06/10 com o BOVA11 parado: cara de grupamento.
    "AAAA3": ("acao", [20.0] * 25 + [10.0] * 5),
    # −50% em 09/10 junto com o BOVA11 −15%: queda de mercado.
    "BBBB4": ("acao", [50.0] * 28 + [25.0, 25.0]),
    "DDDD3": ("acao", [10.0] * 25 + [12.0] * 5),
    # FII com pregões sem negócio: fora da amplitude, que é de ações.
    "CCCC11": ("fii", [80.0] * 27 + [None, 78.0, None]),
    # Sem fechamento 5 pregões atrás: fora da amplitude.
    "EEEE3": ("acao", [None] * 25 + [5.0] * 5),
}


@pytest.fixture
def art():
    return _art(SERIES)


# ── movimento ────────────────────────────────────────────────────────────────

def test_movimento_do_termometro(art):
    m = pr.movimento(art, "bova11.sa")
    assert m.ticker == "BOVA11" and m.data == "2026-10-12" and m.fechamento == 94.0
    assert m.var[1] == pytest.approx(94 / 93.5 - 1)
    assert m.var[5] == pytest.approx(94 / 110 - 1)
    assert m.var[21] == pytest.approx(-0.06)
    assert m.mes == ("2026-09-30", pytest.approx(94 / 110 - 1))
    # A primeira máxima vence: 29/09, não 08/10.
    assert (m.maxima, m.data_maxima) == (110.0, "2026-09-29")
    assert m.maior_dia == ("2026-10-09", pytest.approx(-0.15))
    assert not m.salto_suspeito
    assert pr.movimento(art, "ZZZZ3") is None
    assert pr.movimento({}, "BOVA11") is None


def test_salto_com_mercado_parado_e_suspeito(art):
    assert pr.movimento(art, "AAAA3").salto_suspeito
    # Mesmo tamanho, mas no dia em que o BOVA11 caiu 15%: é mercado.
    assert not pr.movimento(art, "BBBB4").salto_suspeito
    # Sem BOVA11 no arquivo não há como saber: fica a ressalva.
    sem_bova = _art({"AAAA3": SERIES["AAAA3"]})
    assert pr.movimento(sem_bova, "AAAA3").salto_suspeito


def test_linha_do_ativo_leva_a_ressalva_na_mesma_frase(art):
    texto = pr.linha_ativo(pr.movimento(art, "AAAA3"))
    assert texto.startswith("  AAAA3 (NOME AAAA3, ação): R$ 10,00 em 12/10/2026 · "
                            "1 pregão +0,0% · 5 pregões −50,0% · 21 pregões −50,0% · "
                            "no mês (desde 30/09) −50,0% · −50,0% desde a máxima da "
                            "janela (R$ 20,00 em 01/09) · maior pregão: 06/10 −50,0% "
                            "— com o BOVA11 parado no mesmo dia, pode ser desdobramento")
    assert texto.endswith("confira antes de citar como queda ou alta.")
    assert "na máxima da janela de 63 pregões" in pr.linha_ativo(pr.movimento(art, "DDDD3"))


def test_ultimos_fechamentos_e_fechamentos(art):
    assert pr.ultimos_fechamentos(art, ["BOVA11", "SMAL11", "cccc11.sa"]) == {
        "BOVA11": (date(2026, 10, 12), 94.0), "cccc11.sa": (date(2026, 10, 9), 78.0)}
    assert pr.fechamentos(art, "CCCC11", 3) == [
        ("2026-10-08", None), ("2026-10-09", 78.0), ("2026-10-12", None)]
    assert pr.fechamentos(art, "ZZZZ3", 3) == []


# ── amplitude ────────────────────────────────────────────────────────────────

def test_amplitude_conta_so_acoes_com_serie_completa(art):
    amp = pr.amplitude(art)
    assert (amp["n"], amp["caiu"], amp["subiu"]) == (3, 2, 1)
    assert amp["mediana"] == pytest.approx(-0.5) and amp["desde"] == "2026-10-05"
    assert [tk for tk, _ in amp["quedas"]] == ["AAAA3", "BBBB4"]
    assert [tk for tk, _ in amp["altas"]] == ["DDDD3"]


def test_amplitude_nao_lista_alta_entre_as_quedas():
    art = _art({"DDDD3": SERIES["DDDD3"]})
    assert pr.amplitude(art)["quedas"] == []
    texto = "\n".join(pr.linhas(art=art, agora=AGORA))
    assert "maiores quedas nenhuma; maiores altas DDDD3 +20,0%" in texto


def test_amplitude_fica_com_as_mais_negociadas(art):
    art = _art(SERIES, volumes={"DDDD3": 10**9})
    amp = pr.amplitude(art, top=1)
    assert amp["n"] == 1 and amp["altas"][0][0] == "DDDD3"
    assert pr.amplitude(_art({"BOVA11": BOVA})) is None


# ── cabeçalho ────────────────────────────────────────────────────────────────

def test_cabecalho_em_dia(art):
    assert pr.cabecalho(art, AGORA) == (
        "MOVIMENTO RECENTE DA BOLSA (pregões diários da B3, COTAHIST da B3, "
        "fechamento bruto, sem ajuste por provento; último pregão 12/10/2026):")


def test_cabecalho_nomeia_arquivo_velho_e_pregao_atrasado(art):
    velho = pr.cabecalho(art, datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
    assert "VELHO: arquivo gerado há 62 h" in velho and "DIAS ÚTEIS" not in velho
    atrasado = pr.cabecalho(art, datetime(2026, 10, 19, 12, tzinfo=timezone.utc))
    assert "ÚLTIMO PREGÃO HÁ 5 DIAS ÚTEIS: não descreve a bolsa de hoje" in atrasado


def test_cabecalho_com_geracao_ilegivel_ou_sem_base():
    sem_data = _art(SERIES, gerado=None)
    assert "data de geração ilegível" in pr.cabecalho(sem_data, AGORA)
    ingenuo = _art(SERIES, gerado="2026-10-12T22:00:00")
    assert "VELHO" not in pr.cabecalho(ingenuo, AGORA)
    sem_base = _art(SERIES)
    del sem_base["base_ate"]
    assert "último pregão 12/10/2026" in pr.cabecalho(sem_base, AGORA)


# ── seção inteira ────────────────────────────────────────────────────────────

def test_secao_sem_arquivo_nomeia_a_falha():
    for vazio in ({}, {"pregoes": []}):
        (linha,) = pr.linhas(art=vazio, agora=AGORA)
        assert "arquivo pregoes_recentes_b3 ausente ou ilegível" in linha
        assert "Não trate como mercado parado" in linha


def test_secao_com_termometro_ativos_amplitude_e_sintese(art):
    linhas = pr.linhas(["aaaa3.sa", "BBBB4", "AAAA3", "BOVA11", "ZZZZ3", ""],
                       art=art, agora=AGORA)
    texto = "\n".join(linhas)
    assert linhas[0].startswith("MOVIMENTO RECENTE DA BOLSA (")
    assert linhas[1].startswith("  BOVA11 (NOME BOVA11, ETF, proxy do Ibovespa): R$ 94,00")
    assert linhas[2] == ("    dia a dia: 05/10 R$ 110,00 (+0,0%) · 06/10 R$ 110,00 "
                         "(+0,0%) · 07/10 R$ 110,00 (+0,0%) · 08/10 R$ 110,00 (+0,0%) · "
                         "09/10 R$ 93,50 (−15,0%) · 12/10 R$ 94,00 (+0,5%).")
    assert linhas[3] == "  SMAL11 (proxy das small caps): sem pregão no arquivo."
    assert linhas[4] == "  Ativos em questão:"
    assert linhas[5].startswith("  AAAA3 (") and linhas[6].startswith("  BBBB4 (")
    # O termômetro não se repete entre os ativos e o duplicado sai uma vez.
    assert texto.count("  BOVA11 (") == 1 and texto.count("  AAAA3 (") == 1
    assert ("  Sem pregão no COTAHIST nos últimos 21 pregões: ZZZZ3 (ticker antigo, "
            "sem negócio ou fora da B3).") in linhas
    assert ("  Amplitude (as 3 ações mais negociadas, 5 pregões desde 05/10): 2 caíram, "
            "1 subiram, mediana −50,0%; maiores quedas AAAA3 −50,0%, BBBB4 −50,0%; "
            "maiores altas DDDD3 +20,0%.") in linhas
    # A síntese leva número, data e fonte na mesma frase: é o trecho que a LLM copia.
    assert linhas[-2] == (
        "  Síntese para citar: no pregão de 09/10/2026 o BOVA11 (proxy do Ibovespa) "
        "caiu 15,0% (R$ 110,00 → R$ 93,50); em 5 pregões até 12/10/2026, BOVA11 "
        "−14,5% e −14,5% desde a máxima de 29/09/2026; das 3 ações mais negociadas, "
        "2 caíram e 1 subiram em 5 pregões (mediana −50,0%) — COTAHIST da B3, "
        "fechamento bruto.")
    assert linhas[-1] == pr.NOTA_LEITURA


def test_secao_manda_o_excedente_para_a_ferramenta(art, monkeypatch):
    monkeypatch.setattr(pr, "MAX_ATIVOS", 1)
    texto = "\n".join(pr.linhas(["AAAA3", "BBBB4"], art=art, agora=AGORA))
    assert "  BBBB4 (" not in texto
    assert "E mais 1 ativos fora desta lista: use a ferramenta preco_diario_recente" in texto


def test_secao_sem_ativos_nao_abre_a_lista(art):
    assert "  Ativos em questão:" not in pr.linhas(art=art, agora=AGORA)


# ── publicador ───────────────────────────────────────────────────────────────

def _linha(tk, d, close, *, bdi="02", fator=1, vol=1000.0):
    return {"ticker": tk, "trade_date": d, "close": close, "fator_cotacao": fator,
            "financial_volume": vol, "bdi": bdi, "issuer_short_name": f"NOME {tk}"}


def test_montar_payload():
    datas = _datas(30)
    linhas = [_linha("AAAA3", d, 2000.0, fator=100) for d in datas]
    # Duplicado no mesmo pregão: o de maior volume vence, em qualquer ordem.
    linhas += [_linha("AAAA3", datas[-1], 3000.0, fator=100, vol=5000.0),
               _linha("AAAA3", datas[-1], 9000.0, fator=100, vol=10.0)]
    linhas += [_linha("BOVA11", d, 100.0, bdi="14") for d in datas]
    linhas += [_linha("CCCC11", d, 80.0, bdi="12") for d in datas[::2]]
    # Negociou só antes dos últimos 21 pregões: sai.
    linhas += [_linha("VELH3", d, 5.0) for d in datas[:6]]
    # Preço inválido não entra.
    linhas += [_linha("RUIM3", datas[-1], v) for v in (0, "nan", None, -1)]
    p = pub.montar_payload(linhas, AGORA, pregoes=25)
    assert p["pregoes"] == datas[-25:] and p["base_ate"] == datas[-1]
    assert sorted(p["por_ticker"]) == ["AAAA3", "BOVA11", "CCCC11"]
    assert p["n_tickers"] == 3 and p["gerado_em"] == "2026-10-13T12:00:00+00:00"
    a = p["por_ticker"]["AAAA3"]
    assert a["c"] == [20.0] * 24 + [30.0]
    assert a["t"] == "acao" and a["n"] == "NOME AAAA3"
    assert a["v"] == round((20 * 1000 + 5000) / 21)
    assert p["por_ticker"]["BOVA11"]["t"] == "etf"
    fii = p["por_ticker"]["CCCC11"]
    assert fii["t"] == "fii" and None in fii["c"] and 80.0 in fii["c"]


def test_payload_publicado_alimenta_a_secao(tmp_path):
    datas = _datas(30)
    linhas = [_linha("BOVA11", d, 100.0 if i < 28 else 85.0, bdi="14")
              for i, d in enumerate(datas)]
    caminho = tmp_path / "pregoes.json.gz"
    pub.serializar(pub.montar_payload(linhas, AGORA), caminho)
    from core.inteligencia_ativos.arquivo_publicado import ler

    texto = "\n".join(pr.linhas(art=ler(str(caminho), "teste"), agora=AGORA))
    assert "caiu 15,0% (R$ 100,00 → R$ 85,00)" in texto
    assert pub.CAMINHO_PADRAO == pr.ARQUIVO


def test_serie_velha():
    assert pub.serie_velha({"base_ate": None}, date(2026, 10, 13)) == (
        "nenhum pregão nos ZIPs do COTAHIST")
    p = {"base_ate": "2026-10-12"}
    assert pub.serie_velha(p, date(2026, 10, 22)) is None
    assert pub.serie_velha(p, date(2026, 10, 23)).startswith(
        "último pregão do COTAHIST é 2026-10-12 (11 dias); limite 10")


def test_serializar_e_deterministico(tmp_path):
    payload = pub.montar_payload([_linha("AAAA3", "2026-10-12", 10.0)], AGORA)
    a, b = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    pub.serializar(payload, a)
    pub.serializar(payload, b)
    assert a.read_bytes() == b.read_bytes()
    assert json.loads(gzip.decompress(a.read_bytes())) == payload


def test_main_recusa_fita_parada(tmp_path, monkeypatch, capsys):
    velho = date.today() - timedelta(days=30)
    monkeypatch.setattr(pub, "linhas_do_cotahist",
                        lambda pasta, hoje: [_linha("AAAA3", velho.isoformat(), 10.0)])
    saida = tmp_path / "x.json.gz"
    assert pub.main(["--pasta", str(tmp_path), "--saida", str(saida)]) == 1
    assert not saida.exists()
    assert "Rodar o alvo b3_pregao antes" in json.loads(capsys.readouterr().out)["erro"]


def test_main_publica_fita_em_dia(tmp_path, monkeypatch, capsys):
    dias = [(date.today() - timedelta(days=k)).isoformat() for k in (3, 2, 1)]
    monkeypatch.setattr(pub, "linhas_do_cotahist",
                        lambda pasta, hoje: [_linha("AAAA3", d, 10.0) for d in dias])
    saida = tmp_path / "x.json.gz"
    assert pub.main(["--pasta", str(tmp_path), "--saida", str(saida)]) == 0
    relatorio = json.loads(capsys.readouterr().out)
    assert relatorio["n_pregoes"] == 3 and relatorio["base_ate"] == dias[-1]
    assert json.loads(gzip.decompress(saida.read_bytes()))["base_ate"] == dias[-1]
    assert pub.main(["--pasta", str(tmp_path), "--saida", str(tmp_path / "y.json.gz"),
                     "--dry-run"]) == 0
    assert not (tmp_path / "y.json.gz").exists()


@pytest.fixture
def zips(tmp_path, monkeypatch):
    """ZIPs falsos: o conteúdo é a lista de datas que o leitor devolve."""
    import data_pipeline.market.b3_cotahist as cot

    lidos: list = []

    def _ler(conteudo, *, bdi_codes):
        lidos.append(bdi_codes)
        return [{"trade_date": d} for d in json.loads(conteudo)]

    monkeypatch.setattr(cot, "ler_linhas", _ler)

    def _gravar(ano, datas):
        (tmp_path / f"COTAHIST_A{ano}.ZIP").write_text(json.dumps(datas))
    return tmp_path, _gravar, lidos


def test_cotahist_le_o_ano_anterior_so_se_faltar_pregao(zips):
    pasta, gravar, lidos = zips
    gravar(2026, ["2026-01-02", "2026-01-05"])
    gravar(2025, ["2025-12-29", "2025-12-30"])
    datas = [r["trade_date"] for r in pub.linhas_do_cotahist(pasta, date(2026, 1, 6),
                                                             pregoes=2)]
    assert datas == ["2026-01-02", "2026-01-05"]
    datas = [r["trade_date"] for r in pub.linhas_do_cotahist(pasta, date(2026, 1, 6),
                                                             pregoes=3)]
    assert datas == ["2025-12-29", "2025-12-30", "2026-01-02", "2026-01-05"]
    assert set(lidos[0]) == {"02", "05", "06", "07", "08", "09", "12", "14"}


def test_cotahist_sem_zip_do_ano(zips):
    pasta, gravar, _ = zips
    gravar(2026, ["2026-12-30"])
    # Início de janeiro: a B3 ainda não publicou o ZIP do ano.
    assert pub.linhas_do_cotahist(pasta, date(2027, 1, 5), pregoes=1) == [
        {"trade_date": "2026-12-30"}]
    with pytest.raises(SystemExit, match="COTAHIST_A2027.ZIP não existe"):
        pub.linhas_do_cotahist(pasta, date(2027, 2, 1), pregoes=1)


def test_alvo_e_carimbo_da_rotina():
    import scripts.atualizar_vitrines as av
    from core.publicacao_agenda import ALVOS, POR_CHAVE

    alvo = POR_CHAVE["pregoes_recentes_b3"]
    assert alvo.passos == (("scripts/publish_pregoes_recentes_b3.py",),)
    assert alvo.artefatos == ("data/public/pregoes_recentes_b3.json.gz",)
    assert alvo.cadencia_dias == 1 and not alvo.precisa_armazem
    assert av.CARIMBO["pregoes_recentes_b3"] == ("arquivo", alvo.artefatos[0])
    ordem = [a.chave for a in ALVOS]
    # O COTAHIST do dia é baixado antes de virar arquivo para as LLMs.
    assert ordem.index("b3_pregao") < ordem.index("pregoes_recentes_b3")


# ── trajetória mensal ────────────────────────────────────────────────────────

def _resumo_bova():
    return tm.resumir({date(2026, 8, 28): 100.0, date(2026, 9, 30): 110.0,
                       date(2026, 10, 5): 120.0})


def test_trajetoria_marca_o_ponto_mensal_superado():
    s, r = tm.HISTORICO["BOVA11"], _resumo_bova()
    texto = tm.linha(s, r, recente=(date(2026, 10, 26), 102.0))
    assert ("R$ 120,00 em 05/10/2026 — SUPERADO: no pregão de 26/10/2026 fechou em "
            "R$ 102,00 (-15,0% desde 05/10, COTAHIST da B3); as comparações e o "
            "percentil abaixo são do ponto de 05/10, não de hoje") in texto
    for recente in (None, (date(2026, 10, 5), 102.0), (date(2026, 10, 1), 102.0)):
        assert "SUPERADO" not in tm.linha(s, r, recente=recente)


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def rollback(self):
        pass


class _Engine:
    def connect(self):
        return _Conn()


def test_trajetoria_recebe_o_fechamento_recente(monkeypatch):
    r = _resumo_bova()
    monkeypatch.setattr(tm, "resumos_sql",
                        lambda conn, fonte, series: {"BOVA11": r} if "BOVA11" in series else {})
    linhas = tm.linhas_trajetoria(
        supabase=_Engine, cdi=dict, bcb=lambda: (None, "x"), eua=lambda: (None, "x"),
        recentes=lambda: {"BOVA11": (date(2026, 10, 26), 102.0)}, hoje=date(2026, 10, 29))
    bova = next(linha for linha in linhas if linha.startswith("  BOVA11"))
    assert "SUPERADO: no pregão de 26/10/2026" in bova
    assert not any("SUPERADO" in linha for linha in linhas if linha is not bova)


def test_trajetoria_sobrevive_a_falha_dos_pregoes(monkeypatch):
    monkeypatch.setattr(tm, "resumos_sql",
                        lambda conn, fonte, series: {"BOVA11": _resumo_bova()}
                        if "BOVA11" in series else {})

    def _quebra():
        raise RuntimeError("gz ruim")

    linhas = tm.linhas_trajetoria(supabase=_Engine, cdi=dict, bcb=lambda: (None, "x"),
                                  eua=lambda: (None, "x"), recentes=_quebra,
                                  hoje=date(2026, 10, 29))
    assert any(linha.startswith("  BOVA11") and "R$ 120,00" in linha for linha in linhas)
    assert not any("SUPERADO" in linha for linha in linhas)


# ── bloco CONTEXTO DE MERCADO ────────────────────────────────────────────────

def test_bloco_traz_o_movimento_entre_trajetoria_e_cenarios(monkeypatch):
    import core.contexto_mercado as cm

    for nome in ("_macro_supabase_cache", "_macro_brasil", "_macro_local"):
        monkeypatch.setattr(cm, nome, lambda: [])
    monkeypatch.setattr(cm, "_trajetoria_cache", lambda: ["TRAJ"])
    monkeypatch.setattr(cm, "_movimento_recente", lambda ativos, tickers=(): ["MOVIMENTO"])
    monkeypatch.setattr(cm, "_cenarios_analogos", lambda: ["CENARIOS"])
    bloco = cm.bloco_contexto_mercado(noticias_gerais=False)
    assert bloco.index("TRAJ") < bloco.index("MOVIMENTO") < bloco.index("CENARIOS")
    regra = cm.REGRA_CADEIA_TRANSMISSAO
    assert "PREÇO DE AGORA" in regra and "preco_diario_recente" in regra
    assert "em vez de descartá-la como manchete sem confirmação" in regra


def test_preco_confirma_o_movimento_nao_a_causa_da_manchete():
    """O pregão mede a queda; a explicação da manchete segue atribuída à fonte.

    Promover a causa a fato medido apagaria a fronteira entre fato e inferência
    (AGENTS.md); descartar a manchete repetiria a análise de 29/10/2026.
    """
    import core.contexto_mercado as cm

    for texto in (cm.REGRA_CADEIA_TRANSMISSAO, pr.NOTA_LEITURA):
        assert "confirma é fato medido" not in texto
        assert "confirma (mesma direção, mesmos dias) é fato medido" not in texto
        assert "não a causa" in texto and "atribuída à fonte" in texto


def test_movimento_recebe_so_os_ativos_da_b3(monkeypatch):
    import core.contexto_mercado as cm

    pedidos: list = []
    monkeypatch.setattr(pr, "linhas", lambda tickers: pedidos.append(tickers) or ["OK"])
    ativos = {"b3": {"AAAA3": "x"}, "fii": {"CCCC11": "y"}, "us": {"ZZZZ": "z"}}
    assert cm._movimento_recente(ativos) == ["OK"]
    assert pedidos == [["AAAA3", "CCCC11"]]
    assert cm._movimento_recente(None) == ["OK"] and pedidos[-1] == []
    assert cm._movimento_recente({"b3": {"AAAA3": "x"}}, ["CCCC11"]) == ["OK"]
    assert pedidos[-1] == ["AAAA3", "CCCC11"]


def test_tickers_movimento_dao_pregao_sem_puxar_o_noticiario(monkeypatch):
    """O chat de um ativo já traz o noticiário dele: só o pregão entra aqui."""
    import core.conjuntura as conjuntura
    import core.contexto_mercado as cm

    for nome in ("_macro_supabase_cache", "_macro_brasil", "_macro_local"):
        monkeypatch.setattr(cm, nome, lambda: [])
    monkeypatch.setattr(cm, "_trajetoria_cache", lambda: [])
    monkeypatch.setattr(cm, "_cenarios_analogos", lambda: [])
    recebidos: list = []
    monkeypatch.setattr(cm, "_movimento_recente",
                        lambda ativos, tickers=(): recebidos.append((ativos, list(tickers)))
                        or ["MOVIMENTO"])

    def _sem_noticiario(**_):
        raise AssertionError("o noticiário do ativo não deveria ser montado aqui")

    monkeypatch.setattr(conjuntura, "bloco_para_prompt", _sem_noticiario)
    bloco = cm.bloco_contexto_mercado(noticias_gerais=False, tickers_movimento=["AAAA3"])
    assert recebidos == [(None, ["AAAA3"])]
    assert "MOVIMENTO" in bloco and "NOTICIÁRIO E CONJUNTURA" not in bloco


def test_chats_de_ativo_e_de_fii_pedem_o_pregao_dos_seus_ativos():
    """Os dois chats chamavam o bloco sem ativo e perdiam a linha do próprio ativo."""
    raiz = Path(__file__).resolve().parents[1]
    chat_ativo = (raiz / "design" / "chat_ativo.py").read_text(encoding="utf-8")
    assert 'tickers_movimento=[tk] if mercado in ("b3", "fii") else ()' in chat_ativo
    fiis = (raiz / "views" / "fiis.py").read_text(encoding="utf-8")
    assert "bloco_contexto_mercado(\n                    tickers_movimento=alvos)" in fiis


def test_movimento_com_falha_nomeia_a_falha(monkeypatch):
    import core.contexto_mercado as cm

    def _quebra(tickers):
        raise RuntimeError("gz ruim")

    monkeypatch.setattr(pr, "linhas", _quebra)
    (linha,) = cm._movimento_recente({"b3": {"AAAA3": "x"}})
    assert "falha na leitura (gz ruim)" in linha and "Não trate como mercado parado" in linha


def test_fechamentos_recentes_para_a_trajetoria(monkeypatch, art):
    import core.contexto_mercado as cm

    monkeypatch.setattr(pr, "carregar", lambda: art)
    assert cm._fechamentos_recentes() == {"BOVA11": (date(2026, 10, 12), 94.0)}


def test_ponte_sem_veredito_nao_cala_preco_nem_noticia():
    from core.conjuntura import ponte as P

    ctx = P.ContextoConjuntural(as_of=datetime(2026, 10, 29, tzinfo=timezone.utc),
                                knowledge_mode="vitrine", asset_class="b3", decisoes=())
    texto = P.para_llm(ctx)
    assert "não atribua a ele veredito favorável nem desfavorável" in texto
    assert "Isso não cala o resto" in texto and "MOVIMENTO RECENTE" in texto


# ── ferramentas do chat ──────────────────────────────────────────────────────

def test_preco_diario_recente(monkeypatch, art):
    monkeypatch.setattr(pr, "carregar", lambda: art)
    texto = lf.preco_diario_recente(["aaaa3.sa", "CCCC11", "ZZZZ3", "BOVA11", "EEEE3"],
                                    pregoes=3)
    blocos = texto.split("\n\n")
    assert blocos[0].startswith("MOVIMENTO RECENTE DA BOLSA (") and blocos[0].endswith(".")
    assert blocos[1].startswith("AAAA3 (NOME AAAA3, ação): R$ 10,00 em 12/10/2026")
    assert blocos[1].endswith("Fechamentos (3 pregões): 08/10 R$ 10,00; 09/10 R$ 10,00; "
                              "12/10 R$ 10,00.")
    assert blocos[2].endswith("08/10 sem negócio; 09/10 R$ 78,00; 12/10 sem negócio.")
    assert blocos[3].startswith("ZZZZ3: sem pregão no COTAHIST nos últimos 21 pregões")
    assert blocos[4].startswith("BOVA11 (")
    assert "Não consultados (teto de 4 por chamada): EEEE3." in blocos
    assert blocos[-1] == "Fechamento bruto: provento e desdobramento não estão descontados."


def test_preco_diario_recente_cabe_inteiro_com_4_ativos_e_63_pregoes(monkeypatch):
    """O teto do ``executar`` cortava o 4º ativo e a ponta da série do 3º.

    Cada ativo leva o resumo inteiro e a ponta mais nova da série; o que sobra
    do teto é dividido entre eles, e a série encurtada diz que foi encurtada.
    """
    longas = {tk: ("acao", [1234.56 + i for i in range(63)])
              for tk in ("AAAA3", "BBBB4", "DDDD3", "EEEE3")}
    art63 = _art(longas, n=63)
    monkeypatch.setattr(pr, "carregar", lambda: art63)
    texto = lf.executar("preco_diario_recente",
                        {"tickers": list(longas), "pregoes": 63})
    assert len(texto) <= lf.MAX_CHARS_RESULTADO
    assert "resultado cortado" not in texto
    ultimo = f"{date.fromisoformat(art63['pregoes'][-1]):%d/%m}"
    blocos = texto.split("\n\n")
    for tk, bloco in zip(longas, blocos[1:5]):
        assert bloco.startswith(f"{tk} (NOME {tk}, ação)")
        assert "Fechamentos (últimos " in bloco and " de 63 pregões" in bloco
        assert bloco.endswith(f"{ultimo} R$ 1.296,56.")
    assert blocos[-1] == "Fechamento bruto: provento e desdobramento não estão descontados."


def test_preco_diario_recente_argumentos_ruins(monkeypatch, art):
    monkeypatch.setattr(pr, "carregar", lambda: art)
    assert "Fechamentos (21 pregões)" in lf.preco_diario_recente("AAAA3; BBBB4", "x")
    assert "Fechamentos (30 pregões)" in lf.preco_diario_recente(["AAAA3"], 999)
    assert "Fechamentos (1 pregões)" in lf.preco_diario_recente(["AAAA3"], 0)
    assert lf.preco_diario_recente([]).startswith("Nenhum ticker informado")
    monkeypatch.setattr(pr, "carregar", lambda: {})
    assert "arquivo pregoes_recentes_b3 ausente" in lf.preco_diario_recente(["AAAA3"])


def test_preco_diario_recente_pelo_executor(monkeypatch, art):
    monkeypatch.setattr(pr, "carregar", lambda: art)
    texto = lf.executar("preco_diario_recente",
                        json.dumps({"tickers": ["AAAA3"], "pregoes": 2, "extra": 1}))
    assert "Fechamentos (2 pregões): 09/10 R$ 10,00; 12/10 R$ 10,00." in texto


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def all(self):
        return list(self._linhas)


class _ConnPrecos(_Conn):
    def __init__(self, pontos):
        self._pontos = pontos

    def execute(self, sql, params):
        if "market_us" in str(sql):
            return _Resultado([])
        return _Resultado([(tk, d, v) for tk in params["tickers"]
                           for d, v in self._pontos.get(tk, [])])


def _engine_precos(pontos):
    class _E:
        def connect(self):
            return _ConnPrecos(pontos)
    return _E()


def test_preco_ativo_aponta_o_pregao_mais_novo(monkeypatch, art):
    monkeypatch.setattr(pr, "carregar", lambda: art)
    mensal = [(date(2026, m, 28), 20.0) for m in range(1, 10)]
    monkeypatch.setattr(lf, "_engine", lambda: _engine_precos({"AAAA3": mensal}))
    texto = lf.preco_ativo(["AAAA3"], anos=1)
    assert ("Há pregão mais novo que o último ponto mensal acima (COTAHIST da B3, "
            "fechamento bruto): AAAA3 R$ 10,00 em 12/10/2026. Para os dias recentes, "
            "chame preco_diario_recente.") in texto
    assert texto.endswith("Retorno passado não é previsão.")
    # Ponto mensal mais novo que o arquivo diário: nada a acrescentar.
    mensal.append((date(2026, 10, 28), 9.0))
    assert "Há pregão mais novo" not in lf.preco_ativo(["AAAA3"], anos=1)


def test_ferramenta_registrada_entre_as_de_mercado():
    nomes = [f.nome for f in lf.FERRAMENTAS_MERCADO]
    assert nomes.index("preco_ativo") < nomes.index("preco_diario_recente")
    assert "preco_diario_recente" in lf.REGRA_FERRAMENTAS


# -- 🎯 Grau de Confiança ------------------------------------------------------

def test_grau_de_confianca_mede_o_pregao_que_o_chat_cita(monkeypatch, art):
    """O chat do ativo cita o pregão diário; a nota mede a idade dele.

    Mesma régua do cabeçalho que o chat lê: até ``ATRASO_UTEIS`` dias úteis é o
    atraso normal da B3; com duas semanas úteis o chat fala de outra bolsa.
    """
    import core.confianca_secao as cs

    monkeypatch.setattr(pr, "carregar", lambda: art)
    fresco = cs._pregao_do_chat(hoje=date(2026, 10, 14))
    assert (fresco.nome, fresco.pct, fresco.peso) == (cs.PREGAO_DO_CHAT, 100.0, 0.05)
    assert "COTAHIST" in fresco.evidencia and "12/10" in fresco.evidencia
    assert "fechamento bruto" in fresco.evidencia and "B3 e FII" in fresco.evidencia
    assert cs._pregao_do_chat(hoje=date(2026, 10, 30)).pct == 0.0

    monkeypatch.setattr(pr, "carregar", lambda: {})
    ausente = cs._pregao_do_chat(hoje=date(2026, 10, 14))
    assert ausente.pct == 0.0 and "ausente" in ausente.evidencia


def test_b3_e_fii_repartem_o_frescor_com_o_pregao_do_chat(monkeypatch, art):
    """O bloco de frescor (0,25) foi repartido, não ampliado; e o pregão é lido
    do arquivo publicado, então segue medido com o banco fora do ar."""
    import core.confianca_secao as cs

    class _Morta:
        def connect(self):
            raise RuntimeError("sem banco")

        begin = connect

    monkeypatch.setattr("core.database.get_engine", lambda *a, **k: _Morta())
    monkeypatch.setattr(pr, "carregar", lambda: art)
    for secao in (cs.confianca_b3, cs.confianca_fii):
        s = secao(engine=_Morta())
        pesos = {c.nome: c.peso for c in s.componentes}
        assert abs(pesos["Frescor"] + pesos[cs.PREGAO_DO_CHAT] - 0.25) < 1e-9, s.secao
        assert abs(sum(pesos.values()) - 1.0) < 1e-9, s.secao
        assert next(c for c in s.componentes if c.nome == cs.PREGAO_DO_CHAT).medido
