# -*- coding: utf-8 -*-
"""A regra que separa quem foi comprada de quem quebrou.

Estes testes existem porque a regra e a unica coisa entre "702 saidas" e uma
conclusao sobre risco de perda permanente de capital. Errar a direcao aqui nao
levanta excecao nenhuma: produz um numero plausivel e invertido.
"""
from __future__ import annotations

import pytest

from core.us_saida_causa import (
    ADQUIRIDA,
    INDEFINIDO,
    SUMIU,
    classificar,
    classificar_pacote,
    itens_de_8k,
)


def test_falencia_vence_aquisicao_no_mesmo_periodo() -> None:
    # Venda de ativos DENTRO da recuperacao judicial tambem arquiva 2.01. Se a
    # ordem se inverter, o caso que o investidor mais precisa ver vira
    # "adquirida" e some da conta de mortalidade.
    assert classificar([], ["1.03", "2.01"], ["1.03", "2.01"]) == SUMIU


def test_falencia_antiga_ainda_conta() -> None:
    # O 1.03 nao tem janela: quem pediu concordata em 2015 pediu concordata.
    assert classificar([], [], ["1.03"]) == SUMIU


def test_aquisicao_no_fim_da_historia() -> None:
    assert classificar([], ["2.01"], ["2.01"]) == ADQUIRIDA


def test_aquisicao_no_meio_da_vida_nao_e_saida() -> None:
    # 2.01 longe do fim e a empresa COMPRANDO algo no curso normal. Sem a
    # janela, toda empresa que ja fez uma aquisicao viraria "adquirida".
    assert classificar([], [], ["2.01"]) == INDEFINIDO


@pytest.mark.parametrize("forma", ["DEFM14A", "PREM14A", "SC 13E3", "SC 14D9"])
def test_proxy_de_fusao_arquivado_pelo_alvo(forma: str) -> None:
    # Estas formas so aparecem no lado comprado -- e independem de janela.
    assert classificar([forma], [], []) == ADQUIRIDA


def test_sem_evidencia_e_indefinido_e_nao_morte() -> None:
    # O conservadorismo de empurrar o desconhecido para "morreu" ja inverteu
    # uma medicao deste projeto. Ausencia de prova sai da comparacao.
    assert classificar([], [], []) == INDEFINIDO
    assert classificar(None, None, None) == INDEFINIDO
    assert classificar(["10-K", "8-K", "4"], ["5.02"], ["5.02", "2.02"]) == INDEFINIDO


def test_itens_separa_fim_do_curso_normal() -> None:
    recentes = {
        "filingDate": ["2018-03-01", "2023-11-20", "2023-12-31"],
        "items": ["2.01", "5.02", "2.01,3.01"],
    }
    r = itens_de_8k(recentes)
    assert r["ultimo_arquivamento"] == "2023-12-31"
    assert r["itens_todos"] == ["2.01", "3.01", "5.02"]
    # o 2.01 de 2018 fica de fora da janela; o de 2023 entra
    assert set(r["itens_finais"]) == {"2.01", "3.01", "5.02"}
    assert classificar([], r["itens_finais"], r["itens_todos"]) == ADQUIRIDA

    so_antigo = {"filingDate": ["2018-03-01", "2023-12-31"], "items": ["2.01", "5.02"]}
    r2 = itens_de_8k(so_antigo)
    assert r2["itens_finais"] == ["5.02"]
    assert classificar([], r2["itens_finais"], r2["itens_todos"]) == INDEFINIDO


def test_itens_alinha_listas_de_tamanhos_diferentes() -> None:
    # A SEC devolve `items` mais curta quando o arquivamento nao tem item. Um
    # zip cru descartaria as datas finais e a janela sairia errada em silencio.
    recentes = {"filingDate": ["2020-01-01", "2024-06-01"], "items": ["2.01"]}
    r = itens_de_8k(recentes)
    assert r["ultimo_arquivamento"] == "2024-06-01"
    assert r["itens_finais"] == []


def test_itens_sem_arquivamento_nao_quebra() -> None:
    r = itens_de_8k({})
    assert r == {"itens_finais": [], "itens_todos": [], "ultimo_arquivamento": None}
    assert classificar_pacote(r | {"formas": []}) == INDEFINIDO


def test_pacote_e_a_mesma_decisao() -> None:
    pacote = {"formas": ["DEFM14A"], "itens_finais": [], "itens_todos": ["1.03"]}
    assert classificar_pacote(pacote) == SUMIU
    assert classificar_pacote(pacote) == classificar(
        pacote["formas"], pacote["itens_finais"], pacote["itens_todos"])
