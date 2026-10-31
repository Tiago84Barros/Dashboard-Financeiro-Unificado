"""Lotes do Tesouro remontados da Movimentação, para quem não importou o Extrato Analítico."""

from datetime import date

import pytest

from core.tesouro_mtm import parse_taxa_contratada
from core.tesouro_posicao import _texto_taxa, lotes_da_movimentacao, taxa_da_compra
from data_pipeline.jobs.update_tesouro_curva import chaves_detidas
from design.tesouro_painel import estado_html


def test_prefixado_sem_curva_usa_o_pu_de_face():
    taxa = taxa_da_compra(pu_compra=568.74, data_compra=date(2023, 6, 30),
                          vencimento=date(2029, 1, 1), indexador="PRE", cotacao=None)
    assert taxa is not None and taxa.indexador == "PRE"
    assert taxa.valor == pytest.approx(0.1086, abs=5e-4)


def test_indexado_sem_curva_fica_sem_taxa():
    assert taxa_da_compra(pu_compra=2297.36, data_compra=date(2024, 1, 15),
                          vencimento=date(2035, 5, 15), indexador="IPCA",
                          cotacao=None) is None


def test_compra_ao_pu_do_dia_tem_a_taxa_do_dia():
    taxa = taxa_da_compra(pu_compra=3200.0, data_compra=date(2024, 7, 10),
                          vencimento=date(2029, 5, 15), indexador="IPCA",
                          cotacao={"buy_rate": 6.35, "buy_pu": 3200.0})
    assert taxa.valor == pytest.approx(0.0635)
    assert taxa.texto == "IPCA + 6,35%"


def test_pagar_mais_que_o_pu_do_dia_baixa_a_taxa():
    taxa = taxa_da_compra(pu_compra=3300.0, data_compra=date(2024, 7, 10),
                          vencimento=date(2029, 5, 15), indexador="IPCA",
                          cotacao={"buy_rate": 6.35, "buy_pu": 3200.0})
    assert taxa.valor < 0.0635


@pytest.mark.parametrize("indexador,valor", [("IPCA", 0.0774), ("SELIC", 0.0005),
                                             ("PRE", 0.1484)])
def test_texto_da_taxa_volta_pelo_parser_do_extrato(indexador, valor):
    lida = parse_taxa_contratada(_texto_taxa(indexador, valor))
    assert lida.indexador == indexador
    assert lida.valor == pytest.approx(valor, abs=1e-5)


def _mov(dia, tipo, qtd, pu, chave="TIPCA2029"):
    return {"event_date": dia, "movement": tipo, "chave": chave,
            "quantity": qtd, "unit_price": pu, "total_value": qtd * pu}


def test_venda_baixa_do_lote_mais_antigo_e_transferencia_nao_conta():
    movs = [
        _mov(date(2024, 1, 10), "Compra", 2.0, 3000.0),
        _mov(date(2024, 6, 10), "Compra", 1.0, 3100.0),
        _mov(date(2024, 8, 1), "Transferência", 3.0, 3150.0),
        _mov(date(2025, 1, 10), "Venda", 1.5, 3300.0),
    ]
    curva = {("TIPCA2029", date(2024, 6, 7)): {"buy_rate": 6.0, "buy_pu": 3100.0}}
    lotes = lotes_da_movimentacao(
        movs, vencimentos={"TIPCA2029": date(2029, 5, 15)},
        indexadores={"TIPCA2029": "IPCA"}, curva=curva)["TIPCA2029"]

    assert [lote.quantidade for lote in lotes] == pytest.approx([0.5, 1.0])
    assert lotes[0].valor_investido == pytest.approx(1500.0)
    # Primeiro lote sem curva até 4 dias antes: sem taxa. Segundo achou a de 3 dias antes.
    assert lotes[0].taxa_contratada is None
    assert lotes[1].taxa_contratada.valor == pytest.approx(0.06)


def test_titulo_sem_vencimento_conhecido_fica_de_fora():
    lotes = lotes_da_movimentacao([_mov(date(2024, 1, 10), "Compra", 1.0, 900.0, "TX")],
                                  vencimentos={}, indexadores={}, curva={})
    assert lotes == {}


class _Conexao:
    def __init__(self, respostas):
        self._respostas = respostas

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        texto = str(sql)
        for trecho, linhas in self._respostas.items():
            if trecho in texto:
                if isinstance(linhas, Exception):
                    raise linhas
                return type("R", (), {"fetchall": lambda _s, v=linhas: v})()
        raise AssertionError(texto)


class _Engine:
    def __init__(self, respostas):
        self._respostas = respostas

    def connect(self):
        return _Conexao(self._respostas)


def test_chaves_detidas_junta_extrato_e_movimentacao():
    engine = _Engine({"tesouro_lots": [("TIPCA2029",)],
                      "investment_movement_events": [("Tesouro IPCA+ 2035",)]})
    assert chaves_detidas(engine) == frozenset({"TIPCA2029", "TIPCA2035"})


def test_chaves_detidas_sem_extrato_ainda_le_a_movimentacao():
    engine = _Engine({"tesouro_lots": RuntimeError("relation does not exist"),
                      "investment_movement_events": [("Tesouro Selic 2029",)]})
    assert chaves_detidas(engine) == frozenset({"TSELIC2029"})


def test_estado_mostra_a_movimentacao_quando_nao_ha_extrato():
    html = estado_html(data_curva=date(2026, 10, 9), data_extrato=None,
                       hoje=date(2026, 10, 12), data_movimentacao=date(2026, 10, 5))
    assert "Compras da movimentação até <b>05/10/2026</b>" in html
    com_extrato = estado_html(data_curva=date(2026, 10, 9), data_extrato=date(2026, 10, 1),
                              hoje=date(2026, 10, 12), data_movimentacao=date(2026, 10, 5))
    assert "movimentação" not in com_extrato
