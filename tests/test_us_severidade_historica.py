"""Red flag dos EUA qualificada pela persistência medida no histórico.

Cada teste aqui foi verificado por mutação: removendo a linha de produção que
ele diz cobrir, o teste falha. O que se protege é a distinção entre "cinco anos
em cinco" e "um ano em cinco", que antes de 15/09/2026 saía com o mesmo texto.
"""
from __future__ import annotations

import ast
import pathlib

import core.us_dossie as ud
import core.us_risco_historico as hist
from core.severidade_flags import (
    SEVERIDADE_COBERTURA,
    SEVERIDADE_CONTEXTO,
    SEVERIDADE_RISCO,
    agrupa_flags_por_severidade,
    severidade_flag,
)

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _series(anos, *, fcf=None, equity=None, lucro=None):
    """Séries anuais mínimas para exercitar as condições por ano."""
    income, balance, cashflow = [], [], []
    for i, ano in enumerate(anos):
        income.append({"fiscal_year": ano,
                       "net_income": (lucro[i] if lucro else 100)})
        balance.append({"fiscal_year": ano,
                        "total_equity": (equity[i] if equity else 500)})
        cashflow.append({"fiscal_year": ano,
                         "free_cash_flow": (fcf[i] if fcf else 10)})
    return income, balance, cashflow


# ----------------------------------------------------------- a medição pura

def test_persistencia_conta_apenas_anos_avaliaveis():
    """Ano sem o campo sai do denominador — não conta como ano limpo."""
    anos = (2020, 2021, 2022, 2023)
    income = [{"fiscal_year": a, "net_income": 100} for a in anos]
    balance = [{"fiscal_year": a, "total_equity": 500} for a in anos]
    cashflow = [{"fiscal_year": 2020, "free_cash_flow": -5},
                {"fiscal_year": 2021},            # ano sem o campo
                {"fiscal_year": 2022, "free_cash_flow": -5},
                {"fiscal_year": 2023, "free_cash_flow": -5}]
    p = hist.avalia(income, balance, cashflow)["fcf_negativo"]
    # 3 avaliáveis, não 4: o ano em branco não entra como exercício limpo,
    # o que inflaria a saúde de quem não reportou.
    assert (p.acesos, p.anos) == (3, 3)
    assert p.maioria and p.julgavel


def test_maioria_e_estrita():
    """2 de 4 é empate, e empate não vira risco confirmado."""
    inc, bal, cfw = _series([2020, 2021, 2022, 2023], fcf=[-1, -1, 1, 1])
    p = hist.avalia(inc, bal, cfw)["fcf_negativo"]
    assert (p.acesos, p.anos) == (2, 4)
    assert not p.maioria


def test_janela_fica_nos_ultimos_cinco_exercicios():
    anos = [2016, 2017, 2018, 2019, 2020, 2021, 2022]
    inc, bal, cfw = _series(anos, fcf=[-1, -1, -1, 1, 1, 1, 1])
    p = hist.avalia(inc, bal, cfw)["fcf_negativo"]
    assert (p.acesos, p.anos) == (1, 5)


# -------------------------------------------------- a qualificação em texto

def test_cinco_em_cinco_e_risco_confirmado():
    inc, bal, cfw = _series(range(2019, 2024), fcf=[-1] * 5)
    flags = ud.red_flags({}, inc, bal, cfw)
    linha = next(f for f in flags if "Fluxo de caixa livre negativo" in f)
    assert severidade_flag(linha) == SEVERIDADE_RISCO
    assert "em 5 dos últimos 5 exercícios" in linha


def test_um_em_cinco_e_contexto_nao_risco():
    inc, bal, cfw = _series(range(2019, 2024), fcf=[1, 1, 1, 1, -1])
    flags = ud.red_flags({}, inc, bal, cfw)
    linha = next(f for f in flags if "Fluxo de caixa livre negativo" in f)
    assert severidade_flag(linha) == SEVERIDADE_CONTEXTO
    assert "apenas no último exercício" in linha


def test_serie_curta_e_limitacao_de_cobertura():
    inc, bal, cfw = _series([2022, 2023], fcf=[-1, -1])
    flags = ud.red_flags({}, inc, bal, cfw)
    linha = next(f for f in flags if "Fluxo de caixa livre negativo" in f)
    assert severidade_flag(linha) == SEVERIDADE_COBERTURA


def test_padrao_que_cessou_continua_visivel_com_ressalva():
    """4 em 5, mas não no último: o padrão não some — ganha a ressalva."""
    inc, bal, cfw = _series(range(2019, 2024), fcf=[-1, -1, -1, -1, 1])
    flags = ud.red_flags({}, inc, bal, cfw)
    linha = next(f for f in flags if "Fluxo de caixa livre negativo" in f)
    assert severidade_flag(linha) == SEVERIDADE_RISCO
    assert "(não no último exercício)" in linha


def test_condicao_que_nunca_acendeu_nao_vira_linha():
    inc, bal, cfw = _series(range(2019, 2024), fcf=[10] * 5)
    assert not [f for f in ud.red_flags({}, inc, bal, cfw)
                if "Fluxo de caixa livre" in f]


def test_rede_de_seguranca_quando_nenhum_ano_e_avaliavel():
    """A foto acusa e a série não permite apurar: sai como cobertura, não sumiço."""
    flags = ud.red_flags({"_fcf": -5})
    linha = next(f for f in flags if "Fluxo de caixa livre negativo" in f)
    assert severidade_flag(linha) == SEVERIDADE_COBERTURA
    assert "nenhum exercício da janela" in linha


# ------------------------------------------------------ vocabulário e texto

def test_prefixo_desconhecido_cai_no_lado_seguro():
    assert severidade_flag("SEI LÁ: alguma coisa") == SEVERIDADE_RISCO


def test_agrupamento_sempre_traz_as_tres_chaves():
    grupos = agrupa_flags_por_severidade(None)
    assert set(grupos) == {SEVERIDADE_RISCO, SEVERIDADE_CONTEXTO,
                           SEVERIDADE_COBERTURA}


def test_dossie_em_texto_separa_os_tres_blocos():
    d = {"symbol": "X", "red_flags": [
        "Patrimônio líquido negativo em 5 dos últimos 5 exercícios.",
        "CONTEXTO: Fluxo de caixa livre negativo apenas no último exercício, "
        "em 1 dos últimos 5 exercícios.",
        "COBERTURA: Alavancagem alta em 2 dos últimos 2 exercícios — série "
        "curta demais para separar episódio isolado de padrão."]}
    texto = ud.dossie_to_text(d)
    assert "RISCO CONFIRMADO NO HISTÓRICO" in texto
    assert "OBSERVAÇÕES DE CONTEXTO" in texto
    assert "LIMITAÇÕES DE COBERTURA" in texto
    # A linha de contexto não pode cair sob o cabeçalho de risco.
    corte = texto.index("OBSERVAÇÕES DE CONTEXTO")
    assert "apenas no último exercício" not in texto[:corte]


def test_prompt_da_carteira_ensina_a_distincao():
    from core.portfolio_report_us import _PROMPT_COMPANY_PORTFOLIO
    assert "OBSERVAÇÕES DE CONTEXTO" in _PROMPT_COMPANY_PORTFOLIO
    assert "LIMITAÇÕES DE COBERTURA" in _PROMPT_COMPANY_PORTFOLIO


# ------------------------------------------------------------ fonte única

def test_nenhum_outro_modulo_compara_os_prefixos():
    """A regra mora em core/severidade_flags.py. Cópia é o defeito conhecido.

    Verificado por AST, não por texto: o que se procura é uma comparação de
    string com o prefixo (``startswith("CONTEXTO:")`` e afins) fora do módulo
    dono da regra.
    """
    prefixos = {"CONTEXTO:", "COBERTURA:"}
    culpados: list[str] = []
    for caminho in list((RAIZ / "core").rglob("*.py")) + \
            list((RAIZ / "views").rglob("*.py")):
        if caminho.name in {"severidade_flags.py", "dossie_b3.py"}:
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (isinstance(no, ast.Call)
                    and isinstance(no.func, ast.Attribute)
                    and no.func.attr in {"startswith", "removeprefix"}):
                for arg in no.args:
                    if isinstance(arg, ast.Constant) and arg.value in prefixos:
                        culpados.append(f"{caminho.name}:{no.lineno}")
    assert not culpados, culpados
