"""Série do IPCA por grupo (IBGE, SIDRA 7060): parser, arquivo e publicador.

Sem rede: a resposta do SIDRA é uma fixture em linha, no formato real da API
(primeira linha = cabeçalho, ``D2C`` = variável, ``D3C`` = mês, ``D4C`` = grupo).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from core import ipca_grupos as ig
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from scripts import publicar_ipca_grupos as pub

CABECALHO = {"V": "Valor", "D1C": "Brasil (Código)", "D2C": "Variável (Código)",
             "D3C": "Mês (Código)",
             "D4C": "Geral, grupo, subgrupo, item e subitem (Código)"}


def _l(var, mes, grupo, valor):
    return {"D1C": "1", "D2C": var, "D3C": mes, "D4C": grupo, "V": valor}


LINHAS = [
    CABECALHO,
    _l("63", "202609", "7169", "0.48"),
    _l("63", "202610", "7169", "0.09"),
    _l("63", "202610", "7170", "-0.34"),
    _l("66", "202610", "7170", "21.7123"),
    _l("66", "202610", "7169", "100.0000"),
    _l("63", "202610", "7445", "..."),     # IBGE sem valor: ignora
    _l("63", "202610", "9999", "1.0"),     # grupo fora da lista: ignora
]
SET, OUT = date(2026, 9, 1), date(2026, 10, 1)
ALIM = "Alimentação e bebidas"


def test_parse_sidra_separa_variacao_e_peso():
    serie = ig.parse_sidra(LINHAS)
    assert serie.variacao == {(SET, INDICE_GERAL): 0.48, (OUT, INDICE_GERAL): 0.09,
                              (OUT, ALIM): -0.34}
    assert serie.peso == {(OUT, ALIM): 21.7123, (OUT, INDICE_GERAL): 100.0}
    assert serie.meses() == [SET, OUT]
    assert serie.ultimo_mes() == OUT


def test_serie_vazia_nao_tem_mes():
    assert SerieIpcaGrupos().meses() == []
    assert SerieIpcaGrupos().ultimo_mes() is None


def test_arquivo_ida_e_volta_com_bytes_deterministicos():
    serie = ig.parse_sidra(LINHAS)
    quando = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    dados = ig.serializar_ipca_grupos(serie, quando)
    assert dados[:2] == b"\x1f\x8b"
    assert dados == ig.serializar_ipca_grupos(serie, quando)
    volta = ig.desserializar_ipca_grupos(dados)
    assert volta.variacao == serie.variacao
    assert volta.peso == serie.peso
    assert volta.gerado_em == quando


def test_schema_desconhecido_e_recusado():
    import gzip
    dados = gzip.compress(json.dumps({"schema": 99, "linhas": []}).encode())
    with pytest.raises(ValueError):
        ig.desserializar_ipca_grupos(dados)


def test_ler_arquivo_ausente_ou_ilegivel_devolve_serie_vazia(tmp_path):
    assert ig.ler_ipca_grupos_publicado(tmp_path / "nao_existe.json.gz").meses() == []
    lixo = tmp_path / "lixo.json.gz"
    lixo.write_bytes(b"isto nao e gzip")
    assert ig.ler_ipca_grupos_publicado(lixo).meses() == []


def test_ler_arquivo_valido(tmp_path):
    alvo = tmp_path / "ipca.json.gz"
    alvo.write_bytes(ig.serializar_ipca_grupos(ig.parse_sidra(LINHAS),
                                               datetime(2026, 11, 23, tzinfo=timezone.utc)))
    assert ig.ler_ipca_grupos_publicado(alvo).ultimo_mes() == OUT


def test_url_pede_as_duas_variaveis_e_os_dez_grupos():
    assert ig.URL_SIDRA == (
        "https://apisidra.ibge.gov.br/values/t/7060/n1/all/v/63,66/p/all/c315/"
        "7169,7170,7445,7486,7558,7625,7660,7712,7766,7786")


def test_baixar_sidra_ok():
    chamadas = []

    def obter(url, timeout):
        chamadas.append((url, timeout))
        return LINHAS

    serie, erro = ig.baixar_sidra(obter=obter, timeout=10.0)
    assert erro is None
    assert serie.ultimo_mes() == OUT
    assert chamadas == [(ig.URL_SIDRA, 10.0)]


def test_baixar_sidra_nomeia_a_falha():
    def sem_rede(url, timeout):
        raise ConnectionError("sem rede")

    serie, erro = ig.baixar_sidra(obter=sem_rede)
    assert erro == "ConnectionError: sem rede"
    assert serie.meses() == []

    _, erro = ig.baixar_sidra(obter=lambda u, t: {"erro": "x"})
    assert "não é uma lista" in erro

    _, erro = ig.baixar_sidra(obter=lambda u, t: [CABECALHO])
    assert "sem nenhuma linha válida" in erro


@pytest.mark.parametrize("categoria, grupo, definido", [
    ("Moradia", "Habitação", True),
    ("Conta de Luz", "Habitação", True),
    ("Gás", "Habitação", True),
    ("Gastos gerais", INDICE_GERAL, False),   # "gas" não casa dentro de "gastos"
    ("Bem-Estar", "Saúde e cuidados pessoais", True),
    ("Tarifas & Anuidade", "Despesas pessoais", True),
    ("Supermercado", "Alimentação e bebidas", True),
    ("Alimentação", "Alimentação e bebidas", True),
    ("Transporte", "Transportes", True),
    ("Educação", "Educação", True),
    ("Vestuário", "Vestuário", True),
    ("Assinaturas", "Comunicação", True),
    ("Outros", INDICE_GERAL, False),
    ("A Revisar", INDICE_GERAL, False),
])
def test_grupo_da_categoria(categoria, grupo, definido):
    assert ig.grupo_da_categoria(categoria) == (grupo, definido)


def test_publicador_grava_o_arquivo(tmp_path, capsys):
    saida = tmp_path / "ipca_grupos.json.gz"
    codigo = pub.main(["--saida", str(saida)],
                      baixar=lambda: (ig.parse_sidra(LINHAS), None))
    assert codigo == 0
    assert ig.ler_ipca_grupos_publicado(saida).ultimo_mes() == OUT
    relatorio = json.loads(capsys.readouterr().out)
    assert relatorio["status"] == "ok"
    assert relatorio["gravado"] is True
    assert relatorio["meses"] == 2
    assert relatorio["ultimo"] == "2026-10"


def test_publicador_com_erro_nao_grava(tmp_path, capsys):
    saida = tmp_path / "ipca_grupos.json.gz"
    codigo = pub.main(["--saida", str(saida)],
                      baixar=lambda: (SerieIpcaGrupos(), "ConnectionError: sem rede"))
    assert codigo == 1
    assert not saida.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "erro"


def test_publicador_dry_run_nao_grava(tmp_path, capsys):
    saida = tmp_path / "ipca_grupos.json.gz"
    codigo = pub.main(["--saida", str(saida), "--dry-run"],
                      baixar=lambda: (ig.parse_sidra(LINHAS), None))
    assert codigo == 0
    assert not saida.exists()
    assert json.loads(capsys.readouterr().out)["gravado"] is False
