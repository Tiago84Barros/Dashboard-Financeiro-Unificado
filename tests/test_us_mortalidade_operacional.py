"""Contrato da coorte SEC operacional/doméstica, todo com dados sintéticos."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.us_survivorship import (
    coorte_operacional_verificada,
    ciks_com_relatorio_anual,
    ciks_com_relatorio_anual_operacional,
    frase_mortalidade,
)
from scripts import medir_mortalidade_operacional_us as operacional

IDX = """10-K        DOMESTIC INC  1 2010-03-01  edgar/data/1/a.txt
20-F        FOREIGN SA    2 2010-03-01  edgar/data/2/b.txt
40-F        UNKNOWN FORM  3 2010-03-01  edgar/data/3/c.txt
"""


def test_coorte_ampla_mantem_20f_mas_operacional_o_exclui() -> None:
    assert ciks_com_relatorio_anual(IDX) == {1, 2}
    assert ciks_com_relatorio_anual_operacional(IDX) == {1}


def test_10k_positivo_inclui_cik_operacional_mesmo_com_20f_adicional() -> None:
    """20-F puro fica fora; 10-K doméstico presente satisfaz o contrato."""
    indice_misto = """10-K        DOMESTIC INC  1 2010-03-01  edgar/data/1/a.txt
20-F        DOMESTIC INC  1 2010-03-01  edgar/data/1/b.txt
20-F        FOREIGN SA    2 2010-03-01  edgar/data/2/c.txt
"""
    assert ciks_com_relatorio_anual_operacional(indice_misto) == {1}


def test_aplicar_com_identidade_incompleta_nao_grava_nem_retorna_sucesso(
    monkeypatch, capsys,
) -> None:
    """CIK 2 não pode sumir do denominador só porque a SEC não o identificou."""
    monkeypatch.setattr(operacional, "_ciks_por_ano", lambda *_, **__: {
        2010: {1, 2}, 2025: {1},
    })
    monkeypatch.setattr(operacional, "_entidades", lambda: [
        {"cik": 1, "nome": "Domestic Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    monkeypatch.setattr(operacional, "painel_por_ano", lambda *_: {})
    monkeypatch.setattr(operacional, "carregar_medicao", lambda: {"saidas": 0})
    gravacoes: list[dict] = []
    monkeypatch.setattr(operacional, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))

    assert operacional.main(["--anos", "2010", "2025", "--aplicar"]) != 0
    assert gravacoes == []
    assert "NÃO VERIFICADO" in capsys.readouterr().out


def test_aplicar_com_cik_nao_inteiro_nao_grava(monkeypatch) -> None:
    monkeypatch.setattr(operacional, "_ciks_por_ano", lambda *_, **__: {
        2010: {1}, 2025: {1},
    })
    monkeypatch.setattr(operacional, "_entidades", lambda: [
        {"cik": 1.5, "nome": "Domestic Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    monkeypatch.setattr(operacional, "painel_por_ano", lambda *_: {})
    gravacoes: list[dict] = []
    monkeypatch.setattr(operacional, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))

    assert operacional.main(["--anos", "2010", "2025", "--aplicar"]) != 0
    assert gravacoes == []


def test_frase_operacional_incompleta_falha_fechada_com_contagens() -> None:
    frase = frase_mortalidade({"coorte_operacional": {
        "ano_base": 2010,
        "ano_final": 2025,
        "universo_base": 1,
        "sobreviventes": 1,
        "mortalidade_pct": 0.0,
        "sem_identidade_apurada": 1,
        "nao_classificados": 0,
    }})
    assert "NÃO VERIFICADO" in frase
    assert "numerador 1" in frase
    assert "denominador 1" in frase
    assert "desconhecidos 1" in frase
    assert "0% não publicam" not in frase


def test_frase_operacional_completa_e_verificada_eh_selecionada() -> None:
    frase = frase_mortalidade({
        "coorte": {"ano_base": 2010, "ano_final": 2025,
                   "universo_base": 99, "mortalidade_pct": 90.0},
        "coorte_operacional": {
            "ano_base": 2010,
            "ano_final": 2025,
            "medido_em": "2026-08-28",
            "universo_base": 2,
            "sobreviventes": 1,
            "mortalidade_pct": 50.0,
            "populacao": "operacional",
            "cobertura_identidade_pct": 100.0,
            "sem_identidade_apurada": 0,
            "nao_classificados": 0,
            "curva": {
                "2010": {"vivas": 2, "universo_do_ano": 2,
                         "sobrevivencia_pct": 100.0},
                "2025": {"vivas": 1, "universo_do_ano": 1,
                         "sobrevivencia_pct": 50.0},
            },
        },
    })
    assert "companhias operacionais" in frase
    assert "50% não publicam" in frase
    assert "90% não publicam" not in frase


@pytest.mark.parametrize("medido_em,espera_verificada", [
    (datetime.now(timezone.utc).date().isoformat(), True),
    ((datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat(), True),
    ((datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat(), False),
])
def test_coorte_operacional_rejeita_somente_data_futura_utc(
    medido_em: str, espera_verificada: bool,
) -> None:
    coorte = _coorte_operacional_valida()
    coorte["medido_em"] = medido_em
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert ("NÃO VERIFICADO" not in frase) is espera_verificada


def test_coorte_operacional_rejeita_ano_final_utc_corrente() -> None:
    hoje_utc = datetime.now(timezone.utc).date()
    coorte = _coorte_operacional_valida()
    coorte.update({
        "ano_base": hoje_utc.year - 1,
        "ano_final": hoje_utc.year,
        "medido_em": hoje_utc.isoformat(),
    })
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "NÃO VERIFICADO" in frase
    assert "50% não publicam" not in frase


def test_coorte_operacional_aceita_ano_final_utc_anterior_medido_hoje() -> None:
    hoje_utc = datetime.now(timezone.utc).date()
    coorte = _coorte_operacional_valida()
    coorte.update({
        "ano_base": hoje_utc.year - 2,
        "ano_final": hoje_utc.year - 1,
        "medido_em": hoje_utc.isoformat(),
        "curva": {
            str(hoje_utc.year - 2): {"vivas": 2, "universo_do_ano": 2,
                                      "sobrevivencia_pct": 100.0},
            str(hoje_utc.year - 1): {"vivas": 1, "universo_do_ano": 1,
                                      "sobrevivencia_pct": 50.0},
        },
    })
    assert "NÃO VERIFICADO" not in frase_mortalidade({"coorte_operacional": coorte})


@pytest.mark.parametrize("mortalidade", [49.995, 50.0, 50.005])
def test_mortalidade_operacional_aceita_somente_arredondamento_publicado(
    mortalidade: float,
) -> None:
    coorte = _coorte_operacional_valida()
    coorte["mortalidade_pct"] = mortalidade
    assert "NÃO VERIFICADO" not in frase_mortalidade({"coorte_operacional": coorte})


def _coorte_operacional_valida() -> dict:
    return {
        "ano_base": 2010,
        "ano_final": 2025,
        "medido_em": "2026-08-28",
        "universo_base": 2,
        "sobreviventes": 1,
        "mortalidade_pct": 50.0,
        "populacao": "operacional",
        "cobertura_identidade_pct": 100.0,
        "sem_identidade_apurada": 0,
        "nao_classificados": 0,
        "curva": {
            "2010": {"vivas": 2, "universo_do_ano": 2,
                     "sobrevivencia_pct": 100.0},
            "2025": {"vivas": 1, "universo_do_ano": 1,
                     "sobrevivencia_pct": 50.0},
        },
    }


def _coorte_ampla_valida() -> dict:
    return {
        "medido_em": "2026-01-01",
        "ano_base": 2010,
        "ano_final": 2025,
        "universo_base": 2,
        "sobreviventes": 1,
        "mortalidade_pct": 50.0,
        "curva": {
            "2010": {"vivas": 2, "universo_do_ano": 2,
                     "sobrevivencia_pct": 100.0},
            "2025": {"vivas": 1, "universo_do_ano": 1,
                     "sobrevivencia_pct": 50.0},
        },
    }


@pytest.mark.parametrize("campo,valor", [
    ("mortalidade_pct", float("nan")),
    ("mortalidade_pct", float("inf")),
    ("mortalidade_pct", float("-inf")),
    ("mortalidade_pct", 49.0),
    ("sobreviventes", 3),
    ("curva", {}),
])
def test_coorte_ampla_invalida_falha_fechada_sem_percentual(campo, valor) -> None:
    coorte = _coorte_ampla_valida()
    coorte[campo] = valor
    frase = frase_mortalidade({"coorte": coorte})
    assert "NÃO VERIFICADO" in frase
    assert "50% não publicam" not in frase
    assert "numerador" in frase
    assert "denominador" in frase


def test_coorte_ampla_valida_publica_percentual() -> None:
    frase = frase_mortalidade({"coorte": _coorte_ampla_valida()})
    assert "NÃO VERIFICADO" not in frase
    assert "50% não publicam" in frase


@pytest.mark.parametrize("curva", [
    {"02010": {"vivas": 2, "universo_do_ano": 2, "sobrevivencia_pct": 100.0},
     "2025": {"vivas": 1, "universo_do_ano": 1, "sobrevivencia_pct": 50.0}},
    {"2025": {"vivas": 1, "universo_do_ano": 1, "sobrevivencia_pct": 50.0},
     "2010": {"vivas": 2, "universo_do_ano": 2, "sobrevivencia_pct": 100.0}},
    {2010: {"vivas": 2, "universo_do_ano": 2, "sobrevivencia_pct": 100.0},
     "2010": {"vivas": 2, "universo_do_ano": 2, "sobrevivencia_pct": 100.0},
     "2025": {"vivas": 1, "universo_do_ano": 1, "sobrevivencia_pct": 50.0}},
    {"2010": {"vivas": 2, "universo_do_ano": 2, "sobrevivencia_pct": float("nan")},
     "2025": {"vivas": 1, "universo_do_ano": 1, "sobrevivencia_pct": 50.0}},
])
def test_curva_ampla_rejeita_chaves_ou_pontos_nao_auditaveis(curva) -> None:
    coorte = _coorte_ampla_valida()
    coorte["curva"] = curva
    assert "NÃO VERIFICADO" in frase_mortalidade({"coorte": coorte})


def test_curva_ampla_rejeita_vivas_acima_do_universo_do_ano() -> None:
    coorte = _coorte_ampla_valida()
    coorte["curva"] = {
        "2010": {"vivas": 2, "universo_do_ano": 2, "sobrevivencia_pct": 100.0},
        "2015": {"vivas": 2, "universo_do_ano": 1, "sobrevivencia_pct": 100.0},
        "2025": {"vivas": 1, "universo_do_ano": 1, "sobrevivencia_pct": 50.0},
    }
    assert "NÃO VERIFICADO" in frase_mortalidade({"coorte": coorte})


@pytest.mark.parametrize("medido_em,espera_verificada", [
    ("2025-01-01", False),
    ("2026-01-01", True),
])
def test_coorte_ampla_exige_ano_final_encerrado(
    medido_em: str, espera_verificada: bool,
) -> None:
    coorte = _coorte_ampla_valida()
    coorte["medido_em"] = medido_em
    frase = frase_mortalidade({"coorte": coorte})
    assert ("NÃO VERIFICADO" not in frase) is espera_verificada


@pytest.mark.parametrize("medido_em,espera_verificada", [
    ("2025-01-01", False),
    ("2026-01-01", True),
])
def test_coorte_operacional_exige_ano_final_encerrado(
    medido_em: str, espera_verificada: bool,
) -> None:
    coorte = _coorte_operacional_valida()
    coorte["medido_em"] = medido_em
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert ("NÃO VERIFICADO" not in frase) is espera_verificada


@pytest.mark.parametrize("operacional_invalida", [
    None,
    {},
    "payload malformado",
])
def test_chave_operacional_presente_malformada_nunca_cai_para_ampla(
    operacional_invalida,
) -> None:
    frase = frase_mortalidade({
        "coorte": {"ano_base": 2010, "ano_final": 2025,
                   "universo_base": 99, "mortalidade_pct": 90.0},
        "coorte_operacional": operacional_invalida,
    })
    assert "NÃO VERIFICADO" in frase
    assert "90% não publicam" not in frase
    assert "numerador" in frase
    assert "denominador" in frase
    assert "desconhecidos" in frase


@pytest.mark.parametrize("campo,valor", [
    ("medido_em", "data impossível"),
    ("ano_base", "2010"),
    ("ano_base", 2010.0),
    ("ano_base", Decimal("2010")),
    ("ano_final", 2010),
    ("ano_final", 2025.0),
    ("ano_final", Decimal("2025")),
    ("universo_base", True),
    ("universo_base", 2.0),
    ("universo_base", Decimal("2")),
    ("universo_base", 0),
    ("sobreviventes", -1),
    ("sobreviventes", 1.0),
    ("sobreviventes", Decimal("1")),
    ("sobreviventes", 3),
    ("mortalidade_pct", -0.01),
    ("mortalidade_pct", 100.01),
    ("mortalidade_pct", float("nan")),
    ("mortalidade_pct", float("inf")),
    ("mortalidade_pct", float("-inf")),
    ("mortalidade_pct", 49.0),
    ("mortalidade_pct", 50.006),
    ("cobertura_identidade_pct", 99.99),
    ("cobertura_identidade_pct", float("nan")),
    ("cobertura_identidade_pct", float("inf")),
    ("sem_identidade_apurada", True),
    ("sem_identidade_apurada", 0.0),
    ("sem_identidade_apurada", Decimal("0")),
    ("nao_classificados", 0.0),
    ("nao_classificados", Decimal("0")),
])
def test_schema_operacional_incoerente_nao_publica_percentual(campo, valor) -> None:
    coorte = copy.deepcopy(_coorte_operacional_valida())
    coorte[campo] = valor
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "NÃO VERIFICADO" in frase
    assert "50% não publicam" not in frase
    assert "numerador" in frase
    assert "denominador" in frase
    assert "desconhecidos" in frase


@pytest.mark.parametrize("campo,valor,mostra_painel,mostra_veiculos", [
    ("cobertura_pct", "x", False, True),
    ("cobertura_pct", float("nan"), False, True),
    ("cobertura_pct", float("inf"), False, True),
    ("painel_no_ano_base", True, False, True),
    ("veiculos_excluidos", "x", True, False),
])
def test_opcionais_invalidos_omitem_contexto_sem_contaminar_frase(
    campo, valor, mostra_painel, mostra_veiculos,
) -> None:
    coorte = _coorte_operacional_valida()
    coorte.update({
        "cobertura_pct": 50.0,
        "painel_no_ano_base": 1,
        "veiculos_excluidos": 1,
    })
    coorte[campo] = valor
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "50% não publicam" in frase
    assert ("painel cobre" in frase) is mostra_painel
    assert ("Ficaram fora" in frase) is mostra_veiculos
    assert "nan%" not in frase.lower()
    assert "inf%" not in frase.lower()


@pytest.mark.parametrize("campo,valor", [
    ("mortalidade_pct", 10**400),
    ("cobertura_identidade_pct", 10**400),
])
def test_inteiro_gigante_nao_lanca_nem_publica_coorte_operacional(campo, valor) -> None:
    coorte = _coorte_operacional_valida()
    coorte[campo] = valor
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "NÃO VERIFICADO" in frase
    assert "50% não publicam" not in frase


def test_inteiro_gigante_na_curva_ampla_nao_lanca_nem_publica_fato() -> None:
    coorte = _coorte_ampla_valida()
    coorte["curva"]["2025"]["sobrevivencia_pct"] = 10**400
    frase = frase_mortalidade({"coorte": coorte})
    assert "NÃO VERIFICADO" in frase
    assert "50% não publicam" not in frase


def test_curva_operacional_invalida_nao_publica_percentual() -> None:
    coorte = _coorte_operacional_valida()
    coorte["curva"]["2025"]["vivas"] = 2
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "NÃO VERIFICADO" in frase
    assert "50% não publicam" not in frase


def test_veiculos_opcional_parcial_nao_lanca_nem_renderiza_bloco() -> None:
    coorte = _coorte_ampla_valida()
    coorte["veiculos_excluidos"] = 1
    frase = frase_mortalidade({"coorte": coorte})
    assert "50% não publicam" in frase
    assert "Ficaram fora" not in frase


def test_painel_opcional_maior_que_universo_omite_contexto() -> None:
    coorte = _coorte_operacional_valida()
    coorte.update({"cobertura_pct": 100.0, "painel_no_ano_base": 3})
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "50% não publicam" in frase
    assert "painel cobre" not in frase


@pytest.mark.parametrize("sic_invalido", [
    "2834.0",
    "0000",
    "foo",
    float("nan"),
    float("inf"),
    2834.0,
])
def test_aplicar_com_sic_invalido_nao_grava(monkeypatch, sic_invalido) -> None:
    monkeypatch.setattr(operacional, "_ciks_por_ano", lambda *_, **__: {
        2010: {1}, 2025: {1},
    })
    monkeypatch.setattr(operacional, "_entidades", lambda: [
        {"cik": 1, "nome": "Domestic Inc", "sic": sic_invalido,
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    monkeypatch.setattr(operacional, "painel_por_ano", lambda *_: {})
    gravacoes: list[dict] = []
    monkeypatch.setattr(operacional, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))

    assert operacional.main(["--anos", "2010", "2025", "--aplicar"]) != 0
    assert gravacoes == []


def test_aplicar_com_cik_conflitante_nao_grava(monkeypatch) -> None:
    monkeypatch.setattr(operacional, "_ciks_por_ano", lambda *_, **__: {
        2010: {1}, 2025: {1},
    })
    monkeypatch.setattr(operacional, "_entidades", lambda: [
        {"cik": 1, "nome": "A Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 1, "nome": "A Trust", "sic": "6189", "sic_descricao": None},
    ])
    monkeypatch.setattr(operacional, "painel_por_ano", lambda *_: {})
    gravacoes: list[dict] = []
    monkeypatch.setattr(operacional, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))

    assert operacional.main(["--anos", "2010", "2025", "--aplicar"]) != 0
    assert gravacoes == []


def test_aplicar_revalida_coorte_gerada_antes_de_gravar(monkeypatch) -> None:
    monkeypatch.setattr(operacional, "_ciks_por_ano", lambda *_, **__: {
        2010: {1}, 2025: {1},
    })
    monkeypatch.setattr(operacional, "_entidades", lambda: [
        {"cik": 1, "nome": "Domestic Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    monkeypatch.setattr(operacional, "painel_por_ano", lambda *_: {})
    monkeypatch.setattr(operacional, "carregar_medicao", lambda: {"saidas": 0})
    monkeypatch.setattr(operacional, "medir_mortalidade", lambda *_: {
        "medido_em": "2025-01-01", "ano_base": 2010, "ano_final": 2025,
        "universo_base": 1, "sobreviventes": 1, "mortalidade_pct": 0.0,
        "curva": {"2010": {"vivas": 1, "sobrevivencia_pct": 100.0},
                  "2025": {"vivas": 1, "sobrevivencia_pct": 100.0}},
    })
    gravacoes: list[dict] = []
    monkeypatch.setattr(operacional, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))

    assert operacional.main(["--anos", "2010", "2025", "--aplicar"]) == 2
    assert gravacoes == []


def test_aplicar_bloqueia_curva_operacional_invalida(monkeypatch) -> None:
    monkeypatch.setattr(operacional, "_ciks_por_ano", lambda *_, **__: {
        2010: {1}, 2025: {1},
    })
    monkeypatch.setattr(operacional, "_entidades", lambda: [
        {"cik": 1, "nome": "Domestic Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    monkeypatch.setattr(operacional, "painel_por_ano", lambda *_: {})
    monkeypatch.setattr(operacional, "carregar_medicao", lambda: {"saidas": 0})
    monkeypatch.setattr(operacional, "medir_mortalidade", lambda *_: {
        "medido_em": "2026-01-01", "ano_base": 2010, "ano_final": 2025,
        "universo_base": 1, "sobreviventes": 1, "mortalidade_pct": 0.0,
        "curva": {"2010": {"vivas": 1, "universo_do_ano": 1,
                              "sobrevivencia_pct": 100.0},
                  "2025": {"vivas": 1, "universo_do_ano": 0,
                              "sobrevivencia_pct": 100.0}},
    })
    gravacoes: list[dict] = []
    monkeypatch.setattr(operacional, "gravar_medicao", lambda medicao, *_: gravacoes.append(medicao))

    assert operacional.main(["--anos", "2010", "2025", "--aplicar"]) == 2
    assert gravacoes == []


# ── A banda que substituiu o portão impossível (A-158) ──────────────────────
#
# Exigir cobertura de identidade 100% era um critério que nunca poderia passar:
# 111 CIKs da coorte de 2010 são BDC e companhia fechada, que a SEC cadastra sem
# SIC e nenhuma execução futura vai preencher. Enquanto o portão ficava fechado,
# o número exibido continuava sendo o amplo, medido na população errada.
#
# O que autoriza afrouxá-lo é a banda: a dúvida sobre o não classificado é só de
# PERTENCIMENTO -- o desfecho dele é observado como o de qualquer outro CIK --
# então o efeito máximo é calculável e pequeno. Os testes abaixo guardam as duas
# metades disso: a banda tem de existir, e tem de ser estreita.


def _coorte_com_banda(minimo: float, maximo: float, nao_classificados: int = 111,
                      cobertura: float = 98.76) -> dict:
    coorte = _coorte_operacional_valida()
    coorte.update({
        "cobertura_identidade_pct": cobertura,
        "nao_classificados": nao_classificados,
        "mortalidade_pct_min": minimo,
        "mortalidade_pct_max": maximo,
    })
    return coorte


def test_cobertura_parcial_com_banda_estreita_publica() -> None:
    """O caso real: 98,76% de cobertura e 0,16 pp de banda."""
    assert coorte_operacional_verificada(_coorte_com_banda(49.9, 50.1)) is True


def test_nao_classificado_sem_banda_declarada_nao_publica() -> None:
    """Ponto sem banda apresentaria como exato o que não é."""
    coorte = _coorte_com_banda(49.9, 50.1)
    del coorte["mortalidade_pct_max"]
    assert coorte_operacional_verificada(coorte) is False


def test_banda_larga_nao_publica() -> None:
    """Banda que muda a leitura volta a ser motivo de recusa, não de nota de rodapé."""
    assert coorte_operacional_verificada(_coorte_com_banda(45.0, 55.0)) is False


def test_ponto_fora_da_propria_banda_nao_publica() -> None:
    """Incoerência interna: o valor publicado tem de estar entre os extremos."""
    assert coorte_operacional_verificada(_coorte_com_banda(51.0, 52.0)) is False


def test_cobertura_abaixo_do_piso_nao_publica() -> None:
    assert coorte_operacional_verificada(
        _coorte_com_banda(49.9, 50.1, cobertura=97.99)) is False


def test_cobertura_parcial_declarando_zero_nao_classificado_nao_publica() -> None:
    """Sem ninguém por classificar, cobertura só pode ser 100%.

    Afirmar as duas coisas descreve uma medição que não aconteceu; o limiar mais
    frouxo não pode virar porta de entrada para esse payload.
    """
    coorte = _coorte_operacional_valida()
    coorte["cobertura_identidade_pct"] = 98.76
    assert coorte_operacional_verificada(coorte) is False


def test_frase_publica_a_banda_quando_ha_desconhecido() -> None:
    """A dúvida só é auditável pelo usuário se ela aparecer na tela."""
    coorte = _coorte_com_banda(49.9, 50.1)
    coorte.update({"veiculos_excluidos": 1030, "estrangeiros_20f_excluidos": 731})
    frase = frase_mortalidade({"coorte_operacional": coorte})
    assert "49,9%" in frase and "50,1%" in frase
    assert "731" in frase
