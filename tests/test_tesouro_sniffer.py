"""Testes do detector dos extratos do Tesouro Direto e do lote unificado.

O Consolidado é posição e o Analítico é lote: entregar um ao parser do outro
não levanta exceção nenhuma em alguns casos — grava dado sob a fonte errada, em
silêncio. Por isso o caso que mais importa aqui é o do Analítico cuja aba se
chama "Extrato", que é exatamente onde a detecção por nome de aba erraria.
"""
from __future__ import annotations

import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from data_pipeline.importers.investments.tesouro_sniffer import (  # noqa: E402
    detect,
    sheet_names,
)

# Cabeçalho mínimo que o parser do Analítico exige: título e vencimento.
_TITULO = "Extrato Analítico - Tesouro IPCA+ 2029"
_VENCIMENTO = "Vencimento: 15/05/2029"


def _xlsx(abas: dict[str, list[list]]) -> bytes:
    """Workbook em memória: {nome da aba: linhas}, na ordem pedida."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nome, linhas in abas.items():
        ws = wb.create_sheet(title=nome)
        for linha in linhas:
            ws.append(linha)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _analitico(aba: str = "Extrato Analítico") -> bytes:
    return _xlsx({aba: [[_TITULO], [_VENCIMENTO], ["Data Aplicação", "Qtde"]]})


def _consolidado() -> bytes:
    return _xlsx({"Extrato": [["Extrato Consolidado"], ["Período: 08/2026"]]})


# ─────────────────────────────────────────────────────────────────────────────
# detect
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_reconhece_o_analitico_pela_aba_nomeada():
    assert detect(_analitico()) == "tesouro_analitico"


def test_detect_reconhece_o_analitico_sem_acento_na_aba():
    assert detect(_analitico("Extrato Analitico")) == "tesouro_analitico"


def test_detect_reconhece_o_consolidado():
    assert detect(_consolidado()) == "tesouro_direto"


def test_o_conteudo_vence_o_nome_da_aba():
    """Analítico numa aba chamada "Extrato" continua sendo Analítico.

    O parser do Analítico, não achando aba com "ANALITICO" no nome, cai na
    PRIMEIRA aba do arquivo — então um arquivo assim é lido normalmente por
    ele. Se a detecção olhasse só o nome da aba, o mesmo arquivo iria para o
    parser do Consolidado, que leria linhas de aplicação como posição.
    """
    bytes_ = _xlsx({"Extrato": [[_TITULO], [_VENCIMENTO], ["Data Aplicação"]]})
    assert detect(bytes_) == "tesouro_analitico"


def test_o_analitico_e_reconhecido_em_aba_nao_primeira():
    bytes_ = _xlsx({
        "Capa": [["qualquer coisa"]],
        "Extrato Analítico": [[_TITULO], [_VENCIMENTO]],
    })
    assert detect(bytes_) == "tesouro_analitico"


def test_cabecalho_incompleto_nao_e_analitico():
    """Título sem vencimento é exatamente o que o parser recusa lá dentro.

    Aceitar aqui o que o parser rejeita adiante devolveria ao usuário um erro
    de parser em vez da recusa explícita do arquivo.
    """
    bytes_ = _xlsx({"Plan1": [[_TITULO], ["sem vencimento aqui"]]})
    assert detect(bytes_) is None


@pytest.mark.parametrize("bytes_", [
    b"",
    b"isto nao e um xlsx",
])
def test_detect_recusa_arquivo_ilegivel(bytes_):
    assert detect(bytes_) is None


def test_detect_recusa_xlsx_de_outra_origem():
    assert detect(_xlsx({"Negociação": [["Data do Negócio"]]})) is None


def test_a_aba_do_consolidado_e_comparada_pelo_nome_exato():
    """O parser exige `"Extrato" in sheetnames`, sem normalizar.

    Aceitar "extrato" aqui deixaria passar na porta um arquivo que o parser
    recusaria com "aba 'Extrato' nao encontrada".
    """
    assert detect(_xlsx({"extrato": [["Extrato Consolidado"]]})) is None


def test_sheet_names_alimenta_a_mensagem_de_recusa():
    assert sheet_names(_xlsx({"Plan1": [], "Plan2": []})) == ["Plan1", "Plan2"]
    assert sheet_names(b"nao e xlsx") == []


# ─────────────────────────────────────────────────────────────────────────────
# Planejamento do lote
# ─────────────────────────────────────────────────────────────────────────────

def test_o_lote_roda_em_ordem_fixa_independente_do_upload():
    from views.configuracoes import _TESOURO_ORDEM, planejar_lote_tesouro

    planejados, recusados = planejar_lote_tesouro([
        ("analitico-ipca.xlsx", _analitico()),
        ("consolidado.xlsx", _consolidado()),
    ])

    assert not recusados
    assert [chave for chave, _n, _b in planejados] == list(_TESOURO_ORDEM)


def test_varios_analiticos_preservam_a_ordem_de_upload():
    from views.configuracoes import planejar_lote_tesouro

    planejados, _ = planejar_lote_tesouro([
        ("b.xlsx", _analitico()),
        ("a.xlsx", _analitico()),
    ])
    assert [nome for _c, nome, _b in planejados] == ["b.xlsx", "a.xlsx"]


def test_arquivo_desconhecido_e_recusado_com_as_abas_na_mensagem():
    from views.configuracoes import planejar_lote_tesouro

    planejados, recusados = planejar_lote_tesouro([
        ("estranho.xlsx", _xlsx({"Plan1": [], "Plan2": []})),
    ])
    assert planejados == []
    assert recusados == [("estranho.xlsx", ["Plan1", "Plan2"])]


def test_o_analitico_chega_ao_parser_com_o_nome_do_arquivo(monkeypatch):
    """O nome é o rótulo de toda mensagem de erro do Analítico.

    Sem ele, um lote de dez títulos reportaria dez falhas chamadas
    "extrato.xlsx" e o usuário não saberia qual reenviar.
    """
    import views.configuracoes as cfgmod

    recebidos: list[tuple[str, object]] = []

    def _fake(cfg, payload):
        recebidos.append((cfg["key"], payload))
        return {"status": "success"}

    monkeypatch.setattr(cfgmod, "_executar_importacao_investimento", _fake)
    monkeypatch.setattr(cfgmod.settings, "OWNER_USER_ID", None, raising=False)

    anl = _analitico()
    csl = _consolidado()
    cfgmod._executar_lote_tesouro([
        ("analitico-ipca-2029.xlsx", anl),
        ("consolidado.xlsx", csl),
    ])

    por_chave = dict(recebidos)
    assert por_chave["tesouro_analitico"] == ("analitico-ipca-2029.xlsx", anl)
    # O Consolidado não usa o nome do arquivo; segue recebendo bytes puros.
    assert por_chave["tesouro_direto"] == csl


def test_o_tesouro_nao_tem_mais_caixas_proprias():
    """Duas portas para o mesmo arquivo é uma a mais.

    Se os blocos antigos sobrevivessem ao lado do uploader único, o usuário
    teria dois caminhos para o mesmo extrato — e o de baixo não passaria pela
    detecção que separa Analítico de Consolidado.
    """
    from views.configuracoes import _INVESTIMENTO_UPLOADS, _TESOURO_JOBS

    chaves_soltas = {c["key"] for c in _INVESTIMENTO_UPLOADS}
    assert not chaves_soltas & set(_TESOURO_JOBS)


def test_o_rotulo_da_nomad_mudou_mas_a_fonte_registrada_nao():
    """`source_name` é chave de histórico, não texto de tela.

    `data_update_logs` e `data_freshness_status` já carregam essa string.
    Renomeá-la junto com o rótulo abriria uma segunda linha de histórico e a
    primeira ficaria parada, com cara de fonte abandonada.
    """
    from views.configuracoes import _INVESTIMENTO_UPLOADS

    nomad = next(c for c in _INVESTIMENTO_UPLOADS if c["key"] == "nomad")
    assert "Dados Históricos Internacionais" in nomad["label"]
    assert nomad["source_name"] == "Nomad — Notas PDF (manual)"
