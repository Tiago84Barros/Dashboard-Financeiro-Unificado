"""Testes do detector de extratos da B3 e do planejamento do lote.

O detector decide qual extrato um .xlsx contém olhando só os nomes das abas.
Errar essa decisão é gravar dado sob a fonte errada em silêncio, e por isso os
casos de recusa importam tanto quanto os de acerto.
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
        (("Posição - Ações", "Posição - ETF"), "xp_csl"),
        (("Posicao - Acoes",), "xp_csl"),     # export sem acento
        # Consolidado de mês sem posição aberta: só a aba de proventos.
        (("Proventos Recebidos",), "xp_csl"),
    ],
)
def test_detect_reconhece_assinaturas(abas, esperado):
    assert detect(_xlsx(*abas)) == esperado


@pytest.mark.parametrize(
    "abas",
    [
        ("Plan1",),
        ("Extrato",),          # Tesouro Direto — Extrato Consolidado
        ("Posição Atual",),    # menciona posição e NÃO é o consolidado da XP
    ],
)
def test_detect_recusa_arquivo_de_outra_origem(abas):
    """Arquivo válido de outra fonte tem que ser recusado, não chutado.

    "Posição Atual" está aqui porque o marcador da XP é "POSICAO - ", com o
    hífen: sem ele qualquer aba que mencionasse posição entraria no parser do
    consolidado, e o Tesouro Direto tem uploader próprio justamente porque o
    parser é outro.
    """
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


def test_lote_roda_o_consolidado_depois_da_movimentacao():
    """A aba "Proventos Recebidos" deduplica contra a Movimentação.

    Rodando antes dela, o lote gravaria o mesmo provento duas vezes quando os
    dois arquivos cobrissem o mesmo mês — e o usuário veria renda inflada sem
    nenhum erro na tela. A ordem do upload não pode decidir isso.
    """
    from views.configuracoes import planejar_lote_b3

    arquivos = [
        ("consolidado-mensal-2026-janeiro.xlsx", _xlsx("Posição - Ações")),
        ("mov.xlsx", _xlsx("Movimentação")),
        ("neg.xlsx", _xlsx("Negociação")),
    ]
    planejados, recusados = planejar_lote_b3(arquivos)

    assert recusados == []
    assert [chave for chave, _n, _b in planejados] == [
        "b3_neg", "b3_mov", "xp_csl",
    ]


def test_o_consolidado_chega_ao_parser_com_o_nome_do_arquivo(monkeypatch):
    """`_parse_report_date` lê a data do NOME; só os bytes viram hoje.

    A chave única de `portfolio_position_snapshots` inclui `report_date`, e
    todo relatório caindo em `date.today()` colapsaria o histórico inteiro num
    snapshot sobrescrito — sem erro, sem linha a menos, só a data errada.
    """
    from views import configuracoes as cfgmod

    recebidos: list[tuple[str, object]] = []

    def _fake(cfg, payload):
        recebidos.append((cfg["key"], payload))
        return {"status": "success"}

    monkeypatch.setattr(cfgmod, "_executar_importacao_investimento", _fake)
    monkeypatch.setattr(cfgmod.settings, "OWNER_USER_ID", None, raising=False)

    xlsx_xp = _xlsx("Posição - Ações")
    xlsx_neg = _xlsx("Negociação")
    cfgmod._executar_lote_b3([
        ("consolidado-mensal-2026-janeiro.xlsx", xlsx_xp),
        ("neg.xlsx", xlsx_neg),
    ])

    por_chave = dict(recebidos)
    assert por_chave["xp_csl"] == ("consolidado-mensal-2026-janeiro.xlsx",
                                   xlsx_xp)
    # Os extratos da B3 seguem recebendo bytes puros: seus parsers não usam o
    # nome, e mudar o contrato deles sem motivo seria outro defeito.
    assert por_chave["b3_neg"] == xlsx_neg


def test_o_consolidado_nao_tem_mais_caixa_propria():
    """Duas portas para o mesmo arquivo é uma a mais.

    Se o bloco antigo sobrevivesse ao lado do uploader único, o usuário teria
    dois caminhos para o mesmo relatório — e o de baixo não passaria pela
    ordenação que mantém o Consolidado depois da Movimentação.
    """
    from views.configuracoes import _B3_JOBS, _INVESTIMENTO_UPLOADS

    assert "xp_csl" in _B3_JOBS
    assert "xp_csl" not in {c["key"] for c in _INVESTIMENTO_UPLOADS}
