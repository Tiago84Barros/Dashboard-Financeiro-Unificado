"""Duas mensagens que descreviam mal o que tinham observado (06/09/2026).

1. A Análise de Empresa aceitava qualquer texto como ticker, montava o
   cabeçalho da empresa a partir dele ("BBSA3 — BBSA3") e depois reportava a
   ausência de dados como falha de configuração de banco, citando uma variável
   (``SUPABASE_DB_URL_B3``) que desde o cutover de 2026-07 não está mais no
   caminho de resolução dos dados financeiros.

2. A Avaliação de Portfólio bloqueava a aba inteira dizendo "versão antiga",
   sem informar qual versão estava salva nem qual é a atual.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pytest

from core.portfolio_staleness import marcar_defasagem, texto_defasagem

RAIZ = Path(__file__).resolve().parents[1]


# ── 1. defasagem de metodologia ───────────────────────────────────────────────

def _modelo(score_salvo, schema_salvo, *, atual="2.25.0", schema=3, quando=None):
    modelo = {"created_at": quando} if quando else {}
    marcar_defasagem(
        modelo,
        {"score_version": score_salvo, "model_schema_version": schema_salvo},
        score_version=atual,
        schema_version=schema,
    )
    return modelo


def test_carteira_em_dia_nao_bloqueia():
    assert _modelo("2.25.0", 3)["is_stale"] is False


@pytest.mark.parametrize(
    "score_salvo, schema_salvo",
    [("2.24.0", 3), ("2.25.0", 2), ("2.24.0", 2), (None, None)],
)
def test_qualquer_divergencia_bloqueia(score_salvo, schema_salvo):
    assert _modelo(score_salvo, schema_salvo)["is_stale"] is True


def test_mensagem_carrega_as_duas_versoes_e_a_data():
    modelo = _modelo("2.24.0", 3, quando=dt.datetime(2026, 8, 3, 15, 50))
    texto = texto_defasagem(modelo)
    assert "2.24.0" in texto and "2.25.0" in texto, "as versões têm que aparecer"
    assert "03/08/2026" in texto, "a data do salvamento têm que aparecer"
    assert "Criação de Portfólio" in texto, "a saída têm que ser indicada"


def test_mensagem_sobrevive_a_created_at_ausente():
    texto = texto_defasagem(_modelo("2.24.0", 3))
    assert "2.24.0" in texto and " em " not in texto.split("ficou")[0]


def test_schema_ilegivel_nao_derruba_a_leitura():
    modelo: dict = {}
    marcar_defasagem(
        modelo,
        {"score_version": "2.25.0", "model_schema_version": "n/a"},
        score_version="2.25.0",
        schema_version=3,
    )
    assert modelo["is_stale"] is True
    assert modelo["saved_model_schema_version"] == 0


def test_regra_de_defasagem_tem_uma_unica_implementacao():
    """A comparação não pode voltar a existir em cópias que divergem.

    Ver memória "Guarda duplicada não fica igual": três cópias, duas
    divergências. A checagem é pela AST, não pelo comportamento.
    """
    copias = []
    for caminho in (RAIZ / "core").rglob("*.py"):
        if caminho.name == "portfolio_staleness.py":
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Subscript)
                and isinstance(no.slice, ast.Constant)
                and no.slice.value == "is_stale"
                and isinstance(getattr(no, "ctx", None), ast.Store)
            ):
                copias.append(caminho.name)
    assert not copias, f"is_stale gravado fora do helper: {sorted(set(copias))}"


# ── 2. ticker desconhecido ────────────────────────────────────────────────────

def test_analise_valida_o_ticker_antes_de_montar_o_cabecalho():
    fonte = (RAIZ / "views" / "empresas_b3.py").read_text(encoding="utf-8")
    corpo = fonte.split("def _tab_analise(")[1]
    validacao = corpo.index("_universo_b3_tickers()")
    cabecalho = corpo.index("# ── Header ──")
    assert validacao < cabecalho, (
        "a validação do ticker precisa vir antes do cabeçalho da empresa, "
        "senão um texto qualquer vira 'XXXX — XXXX'"
    )


def test_nenhuma_tela_manda_configurar_a_variavel_aposentada():
    """``SUPABASE_DB_URL_B3`` não resolve mais dado financeiro desde 2026-07.

    Ver memória "aviso que envelhece invertido": a mensagem sobreviveu à
    mudança que removeu a própria causa.
    """
    ofensores = []
    for caminho in (RAIZ / "views").rglob("*.py"):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            # Só o texto que chega à tela: comentário e docstring podem — e
            # devem — explicar por que a variável saiu do caminho.
            if (
                isinstance(no, ast.Constant)
                and isinstance(no.value, str)
                and "SUPABASE_DB_URL_B3" in no.value
            ):
                ofensores.append(f"{caminho.name}:{no.lineno}")
    assert not ofensores, f"texto de tela citando variável aposentada: {ofensores}"


def test_universo_une_as_duas_fontes():
    """Fundamentos publicados fora do cadastro de setores não podem sumir.

    Em 06/09/2026 eram 3: ENAT3, NATU3 e PETZ3.
    """
    import pandas as pd

    from views.empresas_b3 import _universo_b3_de

    universo = _universo_b3_de(
        lambda: pd.DataFrame({"ticker": ["PETR4"]}),
        lambda: pd.DataFrame({"Ticker": ["ENAT3", "petr4 "]}),
    )
    assert universo == ("ENAT3", "PETR4")


def test_falha_de_banco_nao_vira_ticker_inexistente():
    """Universo vazio desliga a guarda em vez de acusar o ticker."""
    from views.empresas_b3 import _universo_b3_de

    def explode():
        raise RuntimeError("conexão caiu")

    universo = _universo_b3_de(explode, explode)
    assert universo == ()
    # a guarda na view é `if universo and tk not in universo`
    assert not (universo and "BBAS3" not in universo)


def test_uma_fonte_de_pe_ainda_serve_de_universo():
    import pandas as pd

    from views.empresas_b3 import _universo_b3_de

    def explode():
        raise RuntimeError("conexão caiu")

    assert _universo_b3_de(
        explode, lambda: pd.DataFrame({"Ticker": ["VALE3"]})
    ) == ("VALE3",)


def test_sugestao_encontra_o_ticker_certo_para_erro_de_digitacao():
    import difflib

    universo = ("BBAS3", "BBSE3", "B3SA3", "PETR4", "WEGE3", "VALE3")
    assert "BBAS3" in difflib.get_close_matches("BBSA3", universo, n=4, cutoff=0.6)
    assert "PETR4" in difflib.get_close_matches("PETRO4", universo, n=4, cutoff=0.6)
