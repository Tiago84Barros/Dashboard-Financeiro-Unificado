import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.b3_vigencia import (
    REBAL_MONTH,
    ano_base_do_score,
    janela_de_vigencia,
    safra_completa,
    safra_vigente_em,
)

RAIZ = Path(__file__).parents[1]


def test_rebal_month_e_abril():
    assert REBAL_MONTH == 4


@pytest.mark.parametrize(
    "data, esperado",
    [
        (date(2026, 1, 1), 2025),
        (date(2026, 3, 31), 2025),   # vespera: safra ainda e a anterior
        (date(2026, 4, 1), 2026),    # vira em 1o de abril
        (date(2026, 9, 21), 2026),
        (date(2026, 12, 31), 2026),
    ],
)
def test_safra_vigente_vira_em_abril(data, esperado):
    assert safra_vigente_em(data) == esperado


def test_aceita_timestamp_e_date():
    assert safra_vigente_em(pd.Timestamp("2026-04-01")) == 2026
    assert safra_vigente_em(date(2026, 4, 1)) == 2026


def test_janela_de_vigencia_e_abril_a_marco():
    inicio, fim = janela_de_vigencia(2026)
    assert inicio == pd.Timestamp("2026-04-01")
    assert fim == pd.Timestamp("2027-03-31")


def test_ano_base_e_o_exercicio_anterior():
    assert ano_base_do_score(2026) == 2025


def test_safra_vigente_nunca_usa_balanco_do_futuro():
    """Propriedade: em toda data de 2010 a 2030, o exercicio-base da safra
    vigente e estritamente anterior ao ano da propria data. Se esta
    propriedade cair, a tela esta publicando look-ahead."""
    for d in pd.date_range("2010-01-01", "2030-12-31", freq="D"):
        safra = safra_vigente_em(d)
        assert ano_base_do_score(safra) < d.year, d


def test_safra_completa_so_apos_o_fim_da_janela():
    assert safra_completa(2025, hoje=pd.Timestamp("2026-04-01")) is True
    assert safra_completa(2026, hoje=pd.Timestamp("2026-09-21")) is False
    assert safra_completa(2026, hoje=pd.Timestamp("2027-03-30")) is False
    assert safra_completa(2026, hoje=pd.Timestamp("2027-03-31")) is True


def _nomes_atribuidos(caminho: Path) -> set[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    nomes.add(alvo.id)
    return nomes


def _valores_inteiros_atribuidos(caminho: Path) -> dict[str, int]:
    """nome -> valor, só para atribuições simples de literal inteiro
    (`NOME = 4`). Usado para achar duplicatas por VALOR, não só por nome
    (achado N-2): um nome em português como `_MES_REBALANCE` não contém
    a palavra inglesa "MONTH", e por isso escapava do critério anterior,
    que só olhava o nome."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    valores: dict[str, int] = {}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign) and isinstance(no.value, ast.Constant) \
                and isinstance(no.value.value, int) \
                and not isinstance(no.value.value, bool):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    valores[alvo.id] = no.value.value
    return valores


def test_mes_de_rebalance_definido_num_lugar_so():
    """Guarda duplicada nao fica igual: tres copias da regra de abril ja
    existiram e uma delas (o grafico) ficou para tras, e uma lista branca
    de arquivos ja perdeu a chave nao prevista noutro guarda desta base
    (nota de memoria `lista-branca-perde-a-chave-nao-prevista`). Por isso a
    lista aqui e derivada da estrutura -- todo .py de views/ e core/ --
    em vez de mais um nome de arquivo escrito a mao. O caminho e resolvido
    a partir da raiz do pacote, nao por `parts` de caminho absoluto --
    filtro por caminho absoluto nao visita nada dentro de worktree.

    O criterio DENTRO de cada arquivo (achado N-2, rodada de correcao 2)
    nao e mais so o nome em ingles ("REBAL"+"MONTH") -- essa lista branca
    de palavras perdia um duplicado em portugues como `_MES_REBALANCE = 4`
    (a re-revisao provou isso: 1 passed onde devia falhar). Agora o
    criterio decisivo e o VALOR: qualquer atribuicao de literal inteiro
    igual a `REBAL_MONTH` cujo nome contenha "REBAL" (raiz comum a
    "rebalance"/"rebalanceamento" nas duas linguas) e suspeita. Valor +
    nome, nao so nome -- ve `test_guarda_pega_duplicata_com_nome_em_portugues`
    para a prova isolada."""
    permitido = RAIZ / "core" / "b3_vigencia.py"
    pastas = [RAIZ / "views", RAIZ / "core"]
    caminhos = [c for pasta in pastas for c in sorted(pasta.rglob("*.py"))
                if c != permitido]
    assert caminhos, "a varredura de views/ e core/ nao encontrou nada"
    for caminho in caminhos:
        valores = _valores_inteiros_atribuidos(caminho)
        suspeitos = {n: v for n, v in valores.items()
                     if v == REBAL_MONTH and "REBAL" in n.upper()}
        assert not suspeitos, (
            f"{caminho.relative_to(RAIZ)} define {sorted(suspeitos)} com o "
            f"mesmo valor de REBAL_MONTH ({REBAL_MONTH}); o mes de "
            "rebalance mora so em core/b3_vigencia.py"
        )


def test_guarda_pega_duplicata_com_nome_em_portugues(tmp_path):
    """Prova isolada do N-2: um duplicado em portugues, `_MES_REBALANCE = 4`,
    tem que ser pego pelo novo criterio por valor -- o antigo (nome em
    ingles "REBAL"+"MONTH") deixava passar (a re-revisao reproduziu com
    1 passed). Escreve um modulo sintetico -- não altera nada em views/
    nem core/ -- e roda a mesma função de detecção usada pelo teste
    acima."""
    modulo = tmp_path / "modulo_fake_pt.py"
    modulo.write_text(
        '"""Modulo sintetico so para este teste."""\n'
        "_MES_REBALANCE = 4  # duplica REBAL_MONTH em portugues\n"
        "JANELA_INICIO = \"04-01\"\n",
        encoding="utf-8",
    )
    valores = _valores_inteiros_atribuidos(modulo)
    suspeitos = {n: v for n, v in valores.items()
                 if v == REBAL_MONTH and "REBAL" in n.upper()}
    assert suspeitos == {"_MES_REBALANCE": 4}
