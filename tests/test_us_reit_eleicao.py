# -*- coding: utf-8 -*-
"""A eleicao fiscal separa REIT de operadora imobiliaria; o SIC nao (A-156).

Medido em 27/08/2026 contra o `submissions` da SEC: os 22 ativos rotulados
"Real Estate" generico tem TODOS o SIC 6500 -- REIT (IIPR, GTY, TRNO) e
operadora (FOR, MLP, FPH) com o mesmo codigo. A separacao vem do 10-K.

Os textos abaixo sao as redacoes reais colhidas nos documentos, incluindo as
tres que quebraram a primeira versao do padrao: a hipotese da Belpointe, a
revogacao da Seritage e a mencao a REIT de terceiro.
"""
from __future__ import annotations

from core.us_instrumento import (ELEICAO_REIT_AUSENTE, ELEICAO_REIT_DECLARADA,
                                 MOTIVO_REIT, MOTIVO_TIPO_NAO_CONFIRMADO,
                                 motivo_exclusao_ativo)
from data_pipeline.us.reit_eleicao import (apurar_eleicao, eleicao_no_texto,
                                           url_relatorio_anual)


def _sub(forma="10-K", doc="form10-k.htm", cik="1677576"):
    return {"cik": cik, "filings": {"recent": {
        "form": ["8-K", forma], "accessionNumber": ["0000-00-000001", "0001-23-456789"],
        "primaryDocument": ["ev.htm", doc]}}}


# ── leitura do texto ─────────────────────────────────────────────────────────

def test_eleicao_afirmada_e_declarada() -> None:
    assert eleicao_no_texto("REIT Qualification. The Company elected to be taxed "
                            "as a REIT under the Code.") is True
    assert eleicao_no_texto("We have elected to be taxed as a real estate "
                            "investment trust.") is True


def test_hipotese_nao_e_eleicao() -> None:
    """Belpointe PREP: um padrao ingenuo tirava do universo uma sociedade operacional."""
    assert eleicao_no_texto(
        "If we elect to be taxable as a corporation for U.S. federal income tax "
        "purposes, we may also elect to qualify and be taxed as a REIT.") is False


def test_revogacao_vence_a_eleicao_passada() -> None:
    """Seritage: o documento descreve a eleicao que teve E a revogacao que fez."""
    texto = ("The Company had previously elected to be taxed as a REIT from "
             "formation through December 31, 2021. On March 31, 2022, Seritage "
             "revoked its REIT election and became a taxable C Corporation.")
    assert eleicao_no_texto(texto) is False


def test_risco_de_perder_o_status_nao_e_revogacao() -> None:
    """`REIT status would terminate` e fator de risco, nao fato consumado."""
    texto = ("We have elected to be taxed as a REIT. If we fail to qualify, we "
             "would terminate our REIT status and be taxed as a corporation.")
    assert eleicao_no_texto(texto) is True


def test_mencao_a_reit_de_terceiro_nao_conta() -> None:
    assert eleicao_no_texto("Our tenants include several REITs and we compete "
                            "with real estate investment trusts for assets.") is False


def test_tags_html_nao_escondem_a_frase() -> None:
    assert eleicao_no_texto("<p>We <b>elected</b> to be taxed as a "
                            "<i>REIT</i>.</p>") is True


# ── localizacao do documento ─────────────────────────────────────────────────

def test_url_aponta_para_o_relatorio_anual_mais_recente() -> None:
    url = url_relatorio_anual(_sub())
    assert url == ("https://www.sec.gov/Archives/edgar/data/1677576/"
                   "000123456789/form10-k.htm")


def test_sem_relatorio_anual_nao_ha_url() -> None:
    assert url_relatorio_anual(_sub(forma="S-1")) is None
    assert url_relatorio_anual(None) is None


# ── veredito de tres estados ─────────────────────────────────────────────────

def test_documento_ilegivel_nao_vira_ausencia_de_eleicao() -> None:
    """Falha de rede nao pode promover: seria o defeito do fallback silencioso."""
    def explode(_url):
        raise TimeoutError("sec fora")

    assert apurar_eleicao(_sub(), explode) is None
    assert apurar_eleicao(_sub(), lambda _u: None) is None
    assert apurar_eleicao(_sub(forma="S-1"), lambda _u: "qualquer") is None


def test_veredito_positivo_e_negativo() -> None:
    assert apurar_eleicao(_sub(), lambda _u: "We elected to be taxed as a REIT."
                          ) == ELEICAO_REIT_DECLARADA
    assert apurar_eleicao(_sub(), lambda _u: "We build and sell homes."
                          ) == ELEICAO_REIT_AUSENTE


# ── efeito na regra de universo ──────────────────────────────────────────────

def _motivo(**kw):
    base = {"symbol": "XYZ", "security_type": "common", "sector": "Real Estate"}
    base.update(kw)
    return motivo_exclusao_ativo(base.pop("symbol"), base.pop("security_type"),
                                 base.pop("sector"), **base)


def test_sem_apuracao_o_rotulo_generico_continua_excluindo() -> None:
    assert _motivo(name="Forestar Group Inc.") == MOTIVO_TIPO_NAO_CONFIRMADO


def test_eleicao_declarada_exclui_como_reit_e_nao_como_duvida() -> None:
    """O motivo passa a dizer o que a empresa e, nao que ninguem sabe."""
    assert _motivo(name="Terreno Realty Corp",
                   reit_election=ELEICAO_REIT_DECLARADA) == MOTIVO_REIT


def test_eleicao_ausente_apurada_libera_a_operadora() -> None:
    assert _motivo(name="Forestar Group Inc.",
                   reit_election=ELEICAO_REIT_AUSENTE) is None


def test_eleicao_ausente_nao_resgata_quem_sai_por_outra_regra() -> None:
    """A liberacao vale so para a duvida do rotulo; nao apaga as demais evidencias."""
    assert _motivo(symbol="EFC-PB", name="Ellington Financial Inc.",
                   reit_election=ELEICAO_REIT_AUSENTE) == "preferencial, warrant ou unit"
    assert _motivo(name="Some Capital Corp", is_investment_company=True,
                   reit_election=ELEICAO_REIT_AUSENTE) is not None
    assert _motivo(name="Angel Oak Mortgage REIT, Inc.",
                   reit_election=ELEICAO_REIT_AUSENTE) == MOTIVO_REIT
