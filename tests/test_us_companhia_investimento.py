# -*- coding: utf-8 -*-
"""A-147: BDC e fundo fechado disputavam ranking com companhia operacional.

A regra de universo ja sabia recusar "closed-end management investment
offices" -- so que esse texto vem do SIC, e a SEC devolve `sic` VAZIO para
toda companhia de investimento registrada. A regra existia e era cega: 40
fundos de credito (FS KKR, Hercules, Goldman Sachs BDC, Oaktree, Sixth
Street...) estavam na vitrine, sem receita e com "lucro" recorrente que na
verdade e investment income distribuido por obrigacao legal de RIC.

O sinal que a SEC de fato fornece e o FORMULARIO. Medido em 15 suspeitos
contra 10 controles operacionais: 14x0 de separacao, e o unico suspeito sem
marca era o Central Bancompany -- banco de verdade, que o sinal deixou passar.
"""
from __future__ import annotations

from core.us_instrumento import MOTIVO_COMPANHIA_INVESTIMENTO, motivo_exclusao_ativo
from data_pipeline.us.edgar import _e_companhia_de_investimento
from data_pipeline.us.normalize import map_profile


def _sub(*forms):
    return {"filings": {"recent": {"form": list(forms)}}}


# ── o sinal ──────────────────────────────────────────────────────────────────
def test_n54a_identifica_bdc():
    """N-54A e a eleicao formal de virar BDC sob o Investment Company Act."""
    assert _e_companhia_de_investimento(_sub("8-K", "N-54A", "10-K")) is True


def test_n2_identifica_fundo_fechado():
    assert _e_companhia_de_investimento(_sub("N-2", "N-2/A")) is True
    assert _e_companhia_de_investimento(_sub("N-2ASR")) is True


def test_relatorio_periodico_de_companhia_de_investimento_conta():
    assert _e_companhia_de_investimento(_sub("N-CSR")) is True
    assert _e_companhia_de_investimento(_sub("NPORT-P")) is True


def test_companhia_operacional_nao_arquiva_nenhum_deles():
    """Exxon, Morgan Stanley, HCA e Unisys: zero marcas nos 10 controles."""
    assert _e_companhia_de_investimento(
        _sub("10-K", "10-Q", "8-K", "DEF 14A", "S-3", "4")) is False


def test_banco_operacional_sem_marca_continua_dentro():
    """Central Bancompany caiu no mesmo balde por `sector` nulo; e banco."""
    assert _e_companhia_de_investimento(_sub("10-K", "S-1", "8-K")) is False


def test_submissions_vazio_ou_malformado_nao_quebra():
    for entrada in ({}, None, {"filings": {}}, {"filings": {"recent": {}}},
                    {"filings": {"recent": {"form": [None, ""]}}}):
        assert _e_companhia_de_investimento(entrada) is False


def test_nao_confunde_formulario_de_nome_parecido():
    """`4` e `S-1` comecam com letra; a checagem e por prefixo de formulario."""
    assert _e_companhia_de_investimento(_sub("N-CEN")) is False


# ── a travessia ate a regra ──────────────────────────────────────────────────
def test_map_profile_carrega_a_marca():
    perfil = map_profile({"symbol": "FSK", "companyName": "FS KKR Capital Corp",
                          "_investment_company": True})
    assert perfil["is_investment_company"] is True


def test_map_profile_sem_marca_e_falso_e_nao_none():
    perfil = map_profile({"symbol": "XOM", "companyName": "EXXON MOBIL CORP"})
    assert perfil["is_investment_company"] is False


def test_a_regra_de_universo_recusa_a_companhia_de_investimento():
    motivo = motivo_exclusao_ativo(
        "FSK", "common", None, (), name="FS KKR Capital Corp",
        is_investment_company=True)
    assert motivo == MOTIVO_COMPANHIA_INVESTIMENTO


def test_sem_a_marca_o_mesmo_ativo_passava():
    """Mede exatamente o buraco: `sector` nulo, nome de corporacao comum."""
    assert motivo_exclusao_ativo(
        "FSK", "common", None, (), name="FS KKR Capital Corp") is None


def test_companhia_operacional_nao_e_afetada():
    assert motivo_exclusao_ativo(
        "XOM", "common", "Petroleum Refining", (), name="EXXON MOBIL CORP",
        is_investment_company=False) is None


def test_gestora_de_recursos_continua_dentro():
    """Blackstone administra o veiculo, nao e o veiculo (regra do A-144)."""
    assert motivo_exclusao_ativo(
        "BX", "common", "Investment Advice", (), name="Blackstone Inc.",
        is_investment_company=False) is None


# ── A-147b: a eleicao de BDC pode ser retirada (N-54C) ──────────────────────
#
# `filings.recent` guarda anos. A presenca de um N-54A antigo diz o que a
# empresa FOI. Tres das 50 marcadas no backfill de 27/08/2026 eram isso, e
# exclui-las do universo seria o espelho do defeito que o A-147 consertou.

def _sub_datado(pares):
    """pares = [(form, filingDate), ...]"""
    return {"filings": {"recent": {
        "form": [f for f, _ in pares],
        "filingDate": [d for _, d in pares],
    }}}


def test_retirada_posterior_a_eleicao_devolve_a_empresa_ao_universo():
    """NewtekOne: N-54A em 2022-03-31, N-54C em 2023-01-06, hoje SIC 6021."""
    sub = _sub_datado([("N-54A", "2022-03-31"), ("10-K", "2024-02-28"),
                       ("N-54C", "2023-01-06")])
    assert _e_companhia_de_investimento(sub) is False


def test_bdc_ativa_sem_retirada_continua_marcada():
    """FS KKR: N-2 em 2025-02-14, nenhum N-54C."""
    sub = _sub_datado([("N-2", "2025-02-14"), ("NPORT-P", "2025-05-30")])
    assert _e_companhia_de_investimento(sub) is True


def test_reeleicao_depois_da_saida_volta_a_valer():
    """Compara datas; nao assume que retirada e sempre a ultima palavra."""
    sub = _sub_datado([("N-54C", "2019-04-02"), ("N-54A", "2023-08-15")])
    assert _e_companhia_de_investimento(sub) is True


def test_retirada_sem_nenhuma_eleicao_nao_marca():
    assert _e_companhia_de_investimento(
        _sub_datado([("N-54C", "2020-12-31"), ("10-K", "2024-03-01")])) is False


def test_sem_datas_a_retirada_e_a_evidencia_mais_especifica():
    """Marcar como veiculo quem arquivou saida seria afirmar o contrario do
    unico documento que fala do assunto."""
    sub = {"filings": {"recent": {"form": ["N-54A", "N-54C"]}}}
    assert _e_companhia_de_investimento(sub) is False


def test_sem_retirada_a_ausencia_de_datas_nao_desmarca():
    """Regressao: o caminho antigo (so presenca) continua valendo quando nao
    ha N-54C nenhum -- foi assim que as 47 legitimas foram marcadas."""
    sub = {"filings": {"recent": {"form": ["N-2", "NPORT-P"]}}}
    assert _e_companhia_de_investimento(sub) is True


# ── registro nao e status (NPK) ──────────────────────────────────────────────
# A regra original marcava qualquer um que tivesse arquivado N-2 alguma vez.
# N-2 e REGISTRO de fundo fechado; quem opera como fundo arquiva relatorio
# PERIODICO (N-CSR, NPORT). A National Presto tem um unico N-2 de 2006, do
# litigio que ela venceu, e SIC 3480 (Ordnance) -- e fabricante, nao fundo.

def _sub_sic(forms_e_datas, sic=""):
    return {"sic": sic,
            "filings": {"recent": {"form": [f for f, _ in forms_e_datas],
                                   "filingDate": [d for _, d in forms_e_datas]}}}


def test_registro_antigo_com_sic_operacional_nao_e_companhia_de_investimento():
    """O caso National Presto: um N-2 de 2006 e vinte anos fabricando municao."""
    from data_pipeline.us.edgar import _e_companhia_de_investimento
    assert _e_companhia_de_investimento(
        _sub_sic([("N-2", "2006-03-27"), ("10-K", "2025-06-20")], sic="3480")) is False


def test_registro_sem_sic_continua_marcado():
    """Equus Total Return: BDC de verdade, so registro visivel e SIC vazio.

    A duvida nao libera ninguem -- sem a segunda evidencia a marca permanece.
    """
    from data_pipeline.us.edgar import _e_companhia_de_investimento
    assert _e_companhia_de_investimento(
        _sub_sic([("N-2/A", "2010-12-16")], sic="")) is True


def test_relatorio_periodico_marca_mesmo_com_sic_operacional():
    """SIC nao revoga periodica: quem entrega N-CSR esta operando como fundo."""
    from data_pipeline.us.edgar import _e_companhia_de_investimento
    assert _e_companhia_de_investimento(
        _sub_sic([("N-CSR", "2025-03-01")], sic="6021")) is True


def test_sic_de_veiculo_nao_libera_o_registro():
    """SIC 6726 e de fundo: nao serve de evidencia contraria."""
    from data_pipeline.us.edgar import _e_companhia_de_investimento
    assert _e_companhia_de_investimento(
        _sub_sic([("N-2", "2019-01-02")], sic="6726")) is True
