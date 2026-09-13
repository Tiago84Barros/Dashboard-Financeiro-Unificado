"""R1 parou na função pura: a ausência nunca chegava a quem decide.

``income_recurrence`` passou a devolver ``None`` para 19 fundos, e
``fii_v2._observation`` simplesmente **não grava linha** quando o valor é
``None``. Os leitores pegam a observação mais recente por ``knowledge_at``
(``core/market_read.py`` e ``data_pipeline/market/fii_ingest.py``) — e ausência
não derruba presença. Resultado medido em 13/09/2026 no armazém local: nenhum
dos 19 ficava ausente. Doze passavam a publicar o número do endpoint
``reports`` (RBFM11 0,077 → 0,797; ZAVI11 0,091 → 0,963) e sete mantinham o
valor velho de julho — agora com cara de fresco.

Silêncio, nessa tabela, significa "use o número anterior". Trocar um número
inventado por outro, com aparência de recém-medido, é pior que o defeito
original. A ausência precisa ser uma OBSERVAÇÃO: linha gravada, valor nulo,
motivo declarado.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
from datetime import date

from core.fii_methodology import (
    INCOME_RECURRENCE_MIN_MONTHS,
    income_recurrence_com_motivo,
)
from data_pipeline.market import fii_v2

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
_PADRAO_DISTINCT = "SELECT DISTINCT ON.*?ORDER BY[^\"]*?" + chr(10)


def _mensal(fim: date, meses: int, valor: float = 1.0) -> dict[date, float]:
    saida: dict[date, float] = {}
    ano, mes = fim.year, fim.month
    for _ in range(meses):
        saida[date(ano, mes, 1)] = valor
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return saida


def _linhas(monthly: dict, as_of: date) -> dict[str, dict]:
    return {linha["metric_name"]: linha
            for linha in fii_v2.income_metrics_from_monthly(monthly, as_of=as_of)}


def test_vida_curta_grava_observacao_de_ausencia_com_motivo():
    """O caso RBFM11: oito meses de vida. A métrica não existe — e essa não
    existência tem de ser publicada, não omitida."""
    linhas = _linhas({"RBFM11": _mensal(date(2026, 9, 1), 8)}, date(2026, 9, 20))
    linha = linhas["income_recurrence"]
    assert linha["value_numeric"] is None
    assert linha["value_text"] is None
    metadados = json.loads(linha["metadata_json"])
    assert metadados["absence_reason"] == "vida_observada_menor_que_minimo"
    assert metadados["months_observed"] == 8


def test_fundo_sem_provento_nenhum_tambem_vira_observacao():
    """Fundo sem uma linha sequer em ``market.dividends`` era o pior caso: nem
    a série chegava aqui, então nada era gravado e o valor velho (ou o do
    endpoint ``reports``) continuava decidindo para sempre."""
    linhas = _linhas({"NOVO11": {}}, date(2026, 9, 20))
    metadados = json.loads(linhas["income_recurrence"]["metadata_json"])
    assert linhas["income_recurrence"]["value_numeric"] is None
    assert metadados["absence_reason"] == "sem_provento_observado"


def test_ausencia_e_presenca_saem_do_mesmo_lugar():
    """O motivo não pode ser recontado por fora: se ele discordar do valor, a
    linha publica ausência com número ou número sem motivo."""
    for meses in (0, 1, INCOME_RECURRENCE_MIN_MONTHS - 1, INCOME_RECURRENCE_MIN_MONTHS, 36):
        serie = _mensal(date(2026, 9, 1), meses)
        valor, motivo = income_recurrence_com_motivo(serie, date(2026, 9, 1))
        assert (valor is None) == (motivo is not None), (
            f"{meses} meses: valor={valor} motivo={motivo}")


def test_a_serie_de_renda_e_semeada_com_todos_os_fiis():
    """A ausência de um fundo sem provento só existe se alguém perguntar por
    ele. A derivação parte de ``market.fiis``, não dos tickers que por acaso
    têm linha em ``market.dividends``."""
    from data_pipeline.market.fii_ingest import _serie_mensal_por_ticker

    serie = _serie_mensal_por_ticker(
        ["COMPROV11", "MUDO11"],
        [("COMPROV11", date(2026, 8, 1), 1.0)])
    assert serie["MUDO11"] == {}, "fundo sem provento não chega ao derivador"
    linhas = _linhas(serie, date(2026, 9, 20))
    assert linhas["income_recurrence"]["value_numeric"] is None


def test_o_leitor_a_jusante_enxerga_ausencia_e_nao_o_valor_antigo():
    """A prova pedida: com a linha de ausência gravada DEPOIS do valor velho, o
    leitor que alimenta a decisão não devolve mais o valor velho.

    O ``DISTINCT ON`` é do Postgres, então a consulta real não roda em SQLite.
    O que se verifica aqui é o ponto exato em que ela errava: o descarte do
    nulo dentro do ``WHERE``, que removia a ausência ANTES da escolha por
    ``knowledge_at`` e ressuscitava a observação anterior. Fora do
    ``DISTINCT ON``, a ausência vence a escolha e some depois — o leitor
    devolve nada, que é o que "métrica crítica ausente" significa.
    """
    fonte = (_RAIZ / "data_pipeline/market/fii_ingest.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    funcao = next(no for no in ast.walk(arvore) if isinstance(no, ast.FunctionDef)
                  and no.name == "_latest_metric_rows")
    sql = chr(10).join(no.value for no in ast.walk(funcao)
                     if isinstance(no, ast.Constant) and isinstance(no.value, str))
    escolha = sql.index("DISTINCT ON")
    fim_escolha = sql.index("ORDER BY", escolha)
    assert "IS NOT NULL" not in sql[escolha:fim_escolha], (
        "o nulo é descartado antes do DISTINCT ON; a ausência perde para a "
        "observação anterior e o valor velho volta parecendo fresco")
    assert "IS NOT NULL" in sql[fim_escolha:], (
        "sem o descarte depois da escolha, a ausência chega aos consumidores "
        "como float(None)")


def test_o_outro_leitor_nao_filtra_o_nulo_na_escolha():
    """``core/market_read.py`` é o caminho que a tela lê. Mesma regra."""
    texto = (_RAIZ / "core/market_read.py").read_text(encoding="utf-8")
    for bloco in re.findall(_PADRAO_DISTINCT, texto, re.S):
        if "fii_metric_observations" not in bloco:
            continue
        assert "value_numeric IS NOT NULL" not in bloco, (
            "market_read descarta o nulo antes do DISTINCT ON")
