"""Partes puras do publicador brapi B3 armazém → vitrine (caminho A)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts import publish_b3_brapi_from_local as pub

AGORA = datetime(2026, 10, 5, 22, tzinfo=timezone.utc)


def test_corte_sem_marca_usa_a_janela_inicial():
    assert pub.corte({}, "dividends", AGORA) == AGORA - pub.JANELA_INICIAL


def test_corte_com_marca_volta_a_sobreposicao():
    marca = datetime(2026, 10, 4, 3, tzinfo=timezone.utc)
    estado = {"dividends": marca.isoformat()}
    assert pub.corte(estado, "dividends", AGORA) == marca - pub.SOBREPOSICAO


def test_corte_com_marca_sem_fuso_assume_utc():
    estado = {"dividends": "2026-10-04T03:00:00"}
    esperado = datetime(2026, 10, 4, 3, tzinfo=timezone.utc) - pub.SOBREPOSICAO
    assert pub.corte(estado, "dividends", AGORA) == esperado


def test_corte_com_marca_ilegivel_cai_na_janela_inicial():
    assert pub.corte({"dividends": "ontem"}, "dividends", AGORA) == AGORA - pub.JANELA_INICIAL


def test_avancar_marca_nunca_recua():
    estado = {"balance_sheets": AGORA.isoformat()}
    assert pub.avancar_marca(estado, "balance_sheets", AGORA - timedelta(days=2)) == estado
    novo = pub.avancar_marca(estado, "balance_sheets", AGORA + timedelta(hours=1))
    assert novo["balance_sheets"] == (AGORA + timedelta(hours=1)).isoformat()


def test_avancar_marca_sem_maximo_nao_muda_e_nao_altera_o_original():
    estado = {"x": AGORA.isoformat()}
    novo = pub.avancar_marca(estado, "y", None)
    assert novo == estado and novo is not estado
    pub.avancar_marca(estado, "y", AGORA)
    assert "y" not in estado


def test_avancar_marca_sem_fuso_vira_utc():
    novo = pub.avancar_marca({}, "t", datetime(2026, 10, 5, 1))
    assert novo["t"] == "2026-10-05T01:00:00+00:00"


def test_colunas_publicaveis_tira_controle_e_o_que_a_vitrine_nao_tem():
    origem = ["id", "ticker", "value", "raw_payload_id", "created_at",
              "updated_at", "so_no_armazem"]
    destino = {"id", "ticker", "value", "raw_payload_id", "created_at", "updated_at"}
    assert pub.colunas_publicaveis(origem, destino) == ["ticker", "value"]


def test_reter_ausentes_separa_ticker_que_a_vitrine_nao_conhece():
    linhas = [{"ticker": "PETR4", "v": 1}, {"ticker": "BRML3", "v": 2},
              {"ticker": "VALE3", "v": 3}, {"ticker": "BRML3", "v": 4}]
    publicaveis, retidos = pub.reter_ausentes(linhas, {"PETR4", "VALE3"})
    assert [r["v"] for r in publicaveis] == [1, 3]
    assert retidos == {"BRML3"}


def test_reter_ausentes_por_outra_chave():
    linhas = [{"codigo_cvm": 9512}, {"codigo_cvm": 1}]
    publicaveis, retidos = pub.reter_ausentes(linhas, {9512}, chave="codigo_cvm")
    assert publicaveis == [{"codigo_cvm": 9512}] and retidos == {"1"}


def test_estado_ida_e_volta(tmp_path):
    caminho = tmp_path / "sub" / "marca.json"
    estado = {"dividends": AGORA.isoformat()}
    pub.gravar_estado(caminho, estado)
    assert pub.ler_estado(caminho) == estado
    assert not caminho.with_suffix(".tmp").exists()


def test_estado_ausente_ou_corrompido_vira_vazio(tmp_path):
    assert pub.ler_estado(tmp_path / "nao_existe.json") == {}
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{nao é json", encoding="utf-8")
    assert pub.ler_estado(ruim) == {}
    lista = tmp_path / "lista.json"
    lista.write_text("[1, 2]", encoding="utf-8")
    assert pub.ler_estado(lista) == {}


def test_cabe_no_disco_soma_o_pior_caso_ao_usado():
    # 463,9 MB usados, 28 mil linhas a 400 bytes = +11,2 MB.
    assert pub.cabe_no_disco(463_900_000, 28_000, 400.0, 485)
    # A mesma tabela a 1 KB por linha passaria de 485 MB.
    assert not pub.cabe_no_disco(463_900_000, 28_000, 1000.0, 485)


def test_cabe_no_disco_teto_em_megabytes_decimais():
    assert pub.cabe_no_disco(484_000_000, 1, 1_000_000.0, 485)
    assert not pub.cabe_no_disco(484_000_001, 1, 1_000_000.0, 485)


def test_cabe_no_disco_sem_linhas_ou_com_guarda_desligada():
    assert pub.cabe_no_disco(600_000_000, 0, 1000.0, 485)
    assert pub.cabe_no_disco(600_000_000, 10**6, 1000.0, 0)


def test_main_sai_com_erro_quando_o_disco_parou_a_publicacao(monkeypatch, tmp_path):
    parado = {"modo": "APLICADO", "tabelas": {},
              "parado_por_disco": {"tabela": "balance_sheets"}}
    monkeypatch.setattr(pub, "publish", lambda **kw: parado)
    assert pub.main(["--apply", "--estado", str(tmp_path / "m.json")]) == 1

    monkeypatch.setattr(pub, "publish", lambda **kw: {"modo": "APLICADO", "tabelas": {}})
    assert pub.main(["--apply", "--estado", str(tmp_path / "m.json")]) == 0


def test_main_repassa_o_teto(monkeypatch, tmp_path):
    visto = {}

    def falso(**kw):
        visto.update(kw)
        return {"tabelas": {}}

    monkeypatch.setattr(pub, "publish", falso)
    pub.main(["--teto-disco-mb", "490", "--estado", str(tmp_path / "m.json")])
    assert visto["teto_disco_mb"] == 490
