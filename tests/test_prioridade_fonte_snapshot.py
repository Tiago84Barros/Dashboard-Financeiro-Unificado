"""
tests/test_prioridade_fonte_snapshot.py
=======================================
Prende o desempate entre origens de `portfolio_position_snapshots`.

Quando o mesmo ativo aparece na mesma data em duas origens, quem decide qual
linha sobrevive e o `CASE` de `_SQL_POSICOES_SNAPSHOT` -- o `DENSE_RANK` corta
em `asset_source_rank = 1`. O `CASE` e uma lista branca: nomeia algumas
origens e joga o resto no `ELSE`.

O problema que este arquivo existe para impedir ja aconteceu. O importador
`b3_posicao_detalhada` foi escrito, mergeado e publicado caindo no `ELSE` --
ultimo lugar em todo empate --, nao por decisao, mas por ninguem ter voltado
no `CASE`. Nada quebra quando isso acontece: a consulta roda, devolve uma
linha por ativo e o numero exibido simplesmente vem da fonte errada.

Por isso a checagem NAO e uma lista mantida a mao. Ela deriva de quem GRAVA:
varre os importadores atras dos literais de `source_table` e exige que cada um
esteja nomeado no `CASE`. Um importador novo quebra este teste no dia em que
for escrito, e nao meses depois numa auditoria.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
_IMPORTADORES = _RAIZ / "data_pipeline" / "importers" / "investments"

# Casa `SOURCE_TABLE = "x"`, `source_table="x"` e `source_table: str = "x"`.
# Passagem por variavel (`source_table=SOURCE_TABLE`) nao casa de proposito:
# o literal ja foi capturado na definicao da constante.
_RE_SOURCE_TABLE = re.compile(
    r'(?:SOURCE_TABLE|source_table)\s*(?::\s*str\s*)?=\s*"([^"]+)"'
)


def _source_tables_gravadas() -> set[str]:
    """Todo valor de source_table que algum importador escreve."""
    achados: set[str] = set()
    for arquivo in _IMPORTADORES.glob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        achados.update(_RE_SOURCE_TABLE.findall(texto))
    return achados


def _case_da_consulta() -> str:
    from core.investimentos import _SQL_POSICOES_SNAPSHOT
    return _SQL_POSICOES_SNAPSHOT


def _posicao_no_desempate(origem: str) -> int:
    """Onde `origem` aparece no CASE, com falha legivel se nao aparecer.

    `str.index` sozinho estoura ValueError cru quando a origem sumiu do
    CASE -- e o traceback fala de string, nao de prioridade de fonte. Quem
    ler a falha precisa saber que o problema e uma origem caida no ELSE.
    """
    sql = _case_da_consulta()
    marcador = f"'{origem}'"
    assert marcador in sql, (
        f"'{origem}' nao aparece no CASE de _SQL_POSICOES_SNAPSHOT: caiu no "
        f"ELSE e perde todo empate de data. Sem ela nomeada nao ha ordem de "
        f"autoridade para comparar."
    )
    return sql.index(marcador)


def test_varredura_encontra_os_importadores_conhecidos():
    """Guarda do proprio teste.

    Se a regex parar de casar -- alguem troca aspas duplas por simples, por
    exemplo --, `_source_tables_gravadas` devolve vazio e o teste principal
    passa sem exercer nada. Um teste que so pode dar verde nao e teste.
    """
    gravadas = _source_tables_gravadas()
    assert "b3_posicao_detalhada" in gravadas
    assert "xp_consolidado" in gravadas
    assert "tesouro_direto" in gravadas


@pytest.mark.parametrize("origem", sorted(_source_tables_gravadas()))
def test_toda_origem_gravada_esta_nomeada_no_desempate(origem: str):
    """Nenhum importador pode cair no ELSE por esquecimento."""
    assert f"'{origem}'" in _case_da_consulta(), (
        f"O importador grava source_table='{origem}', mas o CASE de "
        f"_SQL_POSICOES_SNAPSHOT nao nomeia essa origem. Ela cai no ELSE e "
        f"perde todo empate de data em silencio. Decida a posicao dela na "
        f"ordem de autoridade e acrescente o WHEN."
    )


def test_b3_tem_prioridade_sobre_a_xp():
    """A custodia central manda sobre o extrato de uma corretora.

    Decisao de 2026-09-22. O extrato "Posicao Detalhada" vem da Area do
    Investidor da B3 e cobre TODAS as corretoras; o Consolidado cobre uma.
    Empatados na data, vale o primeiro.
    """
    pos_b3 = _posicao_no_desempate("b3_posicao_detalhada")
    pos_xp = _posicao_no_desempate("xp_consolidado")
    assert pos_b3 < pos_xp, (
        "A ordem do CASE inverteu: a XP voltou a ganhar da B3 no empate."
    )


def test_tesouro_continua_na_frente():
    """O Tesouro e a unica fonte que traz o titulo, entao segue em primeiro."""
    assert (_posicao_no_desempate("tesouro_direto")
            < _posicao_no_desempate("b3_posicao_detalhada"))


def test_o_corte_por_rank_continua_existindo():
    """Sem `asset_source_rank = 1` o desempate nao filtra nada.

    O CASE poderia estar perfeito e a consulta ainda devolver as duas linhas
    do mesmo ativo -- dobrando o patrimonio dele -- se o corte sumisse numa
    edicao. A ordenacao so vale acompanhada do filtro.
    """
    assert "asset_source_rank = 1" in _case_da_consulta()
