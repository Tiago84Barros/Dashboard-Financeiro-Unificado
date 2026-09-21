"""Testes do detector de extratos da B3 e do planejamento do lote.

O detector decide qual dos dois extratos um .xlsx contém olhando só os nomes
das abas. Errar essa decisão é gravar dado sob a fonte errada em silêncio, e
por isso os casos de recusa importam tanto quanto os de acerto.
"""
from __future__ import annotations

import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from data_pipeline.importers.investments.b3_sniffer import (  # noqa: E402
    detect,
    sheet_names,
)


def _xlsx(*abas: str) -> bytes:
    """Workbook em memória com as abas pedidas, na ordem pedida."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nome in abas:
        wb.create_sheet(title=nome)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# detect
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "abas, esperado",
    [
        (("Negociação",), "b3_neg"),
        (("Negociacao",), "b3_neg"),          # export sem acento
        (("NEGOCIAÇÃO",), "b3_neg"),
        (("Movimentação",), "b3_mov"),
        (("Movimentacao",), "b3_mov"),
        (("Plan1", "Negociação"), "b3_neg"),  # assinatura em aba não-primeira
    ],
)
def test_detect_reconhece_assinaturas(abas, esperado):
    assert detect(_xlsx(*abas)) == esperado


@pytest.mark.parametrize(
    "abas",
    [
        ("Plan1",),
        ("Extrato",),                      # Tesouro Direto Consolidado
        ("Posição - Ações", "Posição - ETF"),  # XP Consolidado
    ],
)
def test_detect_recusa_arquivo_de_outra_origem(abas):
    """Arquivo válido de outra fonte tem que ser recusado, não chutado."""
    assert detect(_xlsx(*abas)) is None


def test_detect_nao_levanta_em_arquivo_invalido():
    """Bytes que não são xlsx devolvem None — a tela recusa, não quebra."""
    assert detect(b"isto nao e um xlsx") is None
    assert detect(b"") is None


def test_detect_desempata_a_favor_de_negociacao():
    """Arquivo com as duas abas vira Negociação: ela é a fonte canônica das
    compras e vendas, e a Movimentação ignora compra/venda de qualquer forma."""
    assert detect(_xlsx("Movimentação", "Negociação")) == "b3_neg"


# ─────────────────────────────────────────────────────────────────────────────
# sheet_names — alimenta a mensagem de recusa
# ─────────────────────────────────────────────────────────────────────────────

def test_sheet_names_lista_abas_do_arquivo():
    assert sheet_names(_xlsx("Plan1", "Plan2")) == ["Plan1", "Plan2"]


def test_sheet_names_vazio_em_arquivo_invalido():
    assert sheet_names(b"nao e xlsx") == []


# ─────────────────────────────────────────────────────────────────────────────
# Ordem de execução do lote
# ─────────────────────────────────────────────────────────────────────────────

def test_lote_executa_negociacao_antes_de_movimentacao():
    """A ordem de upload não pode influenciar o resultado do lote."""
    from views.configuracoes import planejar_lote_b3

    arquivos = [
        ("mov_2025.xlsx", _xlsx("Movimentação")),
        ("neg_2025.xlsx", _xlsx("Negociação")),
        ("mov_2024.xlsx", _xlsx("Movimentação")),
    ]
    planejados, recusados = planejar_lote_b3(arquivos)

    assert recusados == []
    assert [chave for chave, _nome, _b in planejados] == [
        "b3_neg", "b3_mov", "b3_mov",
    ]
    # Dentro de cada tipo, a ordem de upload é preservada.
    assert [nome for _c, nome, _b in planejados] == [
        "neg_2025.xlsx", "mov_2025.xlsx", "mov_2024.xlsx",
    ]


def test_lote_separa_recusados_sem_derrubar_os_demais():
    from views.configuracoes import planejar_lote_b3

    arquivos = [
        ("estranho.xlsx", _xlsx("Plan1")),
        ("neg.xlsx", _xlsx("Negociação")),
    ]
    planejados, recusados = planejar_lote_b3(arquivos)

    assert [nome for _c, nome, _b in planejados] == ["neg.xlsx"]
    assert [nome for nome, _abas in recusados] == ["estranho.xlsx"]
    assert recusados[0][1] == ["Plan1"]  # abas vão para a mensagem de recusa


def test_consolidacao_soma_contadores_e_marca_parcial():
    from views.configuracoes import _consolidar_resultados_b3

    resultados = [
        ("b3_neg", "neg.xlsx", {
            "status": "success", "records_imported": 10,
            "transactions_imported": 10, "duplicates_skipped": 2,
        }),
        ("b3_mov", "mov.xlsx", {
            "status": "failed", "records_imported": 0,
            "errors": ["Aba nao encontrada"],
        }),
    ]
    total = _consolidar_resultados_b3(resultados)

    assert total["status"] == "partial_success"
    assert total["records_imported"] == 10
    assert total["transactions_imported"] == 10
    assert total["duplicates_skipped"] == 2
    assert total["errors"] == ["mov.xlsx: Aba nao encontrada"]
