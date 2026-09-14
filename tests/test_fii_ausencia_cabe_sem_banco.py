"""A linha de ausencia tem de caber na tabela — provado SEM armazem.

`tests/test_fii_ausencia_gravavel.py` prova isto contra o Postgres local, que
e a prova forte. So que ele **pula** quando o armazem nao responde, e o
`.github/workflows/tests.yml` roda sem banco e sem docker: com a marca de
`value_json` revertida o CI via 0 falhas (3 skipped) enquanto localmente 3
falhavam. A garantia que impede a ingestao inteira de cair ficava invisivel
onde o merge e decidido — "portao que pergunta por uma representante".

Este arquivo reproduz o predicado da constraint em Python **a partir do DDL
versionado** (nao de uma copia escrita a mao) e o aplica as linhas que o
codigo de producao monta. Nao precisa de banco, nao pula, e reprova a
reversao. O teste contra o armazem continua existindo e continua sendo o mais
forte: este cobre o invariante, aquele cobre o dialeto real.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from data_pipeline.market import fii_v2

_RAIZ = Path(__file__).resolve().parents[1]
_DDL = _RAIZ / "supabase_unificado/schema/023_fii_methodology_v4.sql"


def _colunas_do_check() -> list[str]:
    """As colunas que o DDL versionado exige que somem exatamente uma.

    Ler do DDL e deliberado: uma copia escrita a mao envelheceria em silencio
    se a constraint mudasse, e o teste passaria a garantir outra coisa.
    """
    texto = _DDL.read_text(encoding="utf-8")
    achado = re.search(r"CHECK\s*\(\s*num_nonnulls\(([^)]*)\)\s*=\s*1\s*\)", texto)
    assert achado, (
        f"{_DDL.name} nao declara mais o CHECK num_nonnulls(...) = 1; "
        "se a invariante mudou, este teste precisa mudar junto — nao pode "
        "passar por nao encontrar nada")
    return [c.strip() for c in achado.group(1).split(",") if c.strip()]


def _num_nonnulls(linha: dict, colunas: list[str]) -> int:
    return sum(1 for coluna in colunas if linha.get(coluna) is not None)


def test_o_ddl_declara_a_invariante_que_este_arquivo_reproduz():
    assert _colunas_do_check() == ["value_numeric", "value_text", "value_json"]


@pytest.mark.parametrize("serie, rotulo", [
    ({}, "sem nenhum provento observado"),
    ({date(2026, 8, 1): 1.0}, "vida observada menor que o minimo"),
    ({date(2026, 1 + i, 1): 0.0 for i in range(12)}, "media de renda nao positiva"),
])
def test_toda_linha_de_ausencia_satisfaz_a_constraint(serie, rotulo):
    """O modo de falha que isto reprova: linha com os tres campos nulos.

    Ela violava `num_nonnulls(...) = 1`, o `IntegrityError` subia pelo fallback
    linha-a-linha do repositorio, que re-levanta, e o `engine.begin()` unico da
    derivacao revertia a rodada inteira — nem os valores validos sobreviviam.
    """
    colunas = _colunas_do_check()
    linhas = fii_v2.income_metrics_from_monthly({"ZZAUS11": serie},
                                                as_of=date(2026, 9, 20))
    assert linhas, f"a producao nao emitiu observacao nenhuma para: {rotulo}"
    for linha in linhas:
        assert _num_nonnulls(linha, colunas) == 1, (
            f"{linha['metric_name']} ({rotulo}) tem "
            f"{_num_nonnulls(linha, colunas)} campos de valor preenchidos; "
            "a tabela exige exatamente 1 e recusaria a linha, derrubando a rodada")


def test_a_ausencia_nao_cabe_virando_numero():
    """A saida facil que este teste tambem precisa barrar: satisfazer a
    constraint gravando `0.0`. Em media renormalizada `0.0` e punitivo e `None`
    e neutro, e o projeto ja publicou nota errada por confundir os dois."""
    linhas = fii_v2.income_metrics_from_monthly({"ZZAUS11": {}}, as_of=date(2026, 9, 20))
    recorrencia = next(linha for linha in linhas
                       if linha["metric_name"] == "income_recurrence")
    assert recorrencia["value_numeric"] is None, "ausencia nao pode virar numero"
    assert recorrencia["value_text"] is None, (
        "texto entraria como *valor* nos leitores, que resolvem "
        "value_numeric -> value_text -> value_json")
    assert recorrencia["value_json"] is not None


def test_a_marca_gravada_e_reconhecida_pelo_dono_unico():
    """Fecha o circuito: o que a producao grava, o leitor unico le como ausencia."""
    from core.observacao_ausente import motivo_da_ausencia, valor_observado

    linhas = fii_v2.income_metrics_from_monthly({"ZZAUS11": {}}, as_of=date(2026, 9, 20))
    recorrencia = next(linha for linha in linhas
                       if linha["metric_name"] == "income_recurrence")
    assert valor_observado(recorrencia) is None
    assert motivo_da_ausencia(recorrencia["value_json"]) == "sem_provento_observado"


def test_o_portao_do_leitor_nao_usa_o_relogio_da_transacao():
    """Achado C, na forma que o CI enxerga sem banco.

    ``_latest_metric_rows`` roda dentro do mesmo ``engine.begin()`` que grava
    as observacoes. Em Postgres, ``now()`` devolve o instante em que a
    transacao COMECOU, entao esse portao esconde do consumidor tudo o que a
    propria rodada acabou de gravar -- inclusive as ausencias. O relogio certo
    para um leitor que corre junto do escritor e ``statement_timestamp()``, que
    ainda barra conhecimento do futuro sem apagar o presente.
    """
    import inspect

    from data_pipeline.market.fii_ingest import _latest_metric_rows

    fonte = inspect.getsource(_latest_metric_rows)
    assert "knowledge_at <= now()" not in fonte, (
        "o portao voltou para now(): dentro da transacao da derivacao ele "
        "esconde as observacoes que a propria rodada gravou")
    assert "statement_timestamp()" in fonte, (
        "o leitor precisa de um portao de conhecimento; sem nenhum, "
        "observacao com knowledge_at no futuro entraria na decisao")
