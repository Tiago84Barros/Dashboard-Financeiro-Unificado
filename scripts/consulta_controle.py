"""
Extração de dados do módulo Controle Financeiro (Dashboard Financeiro Unificado).

Usa as funções REAIS do dashboard (core/controle) para produzir um JSON
fiel com receitas, despesas, histórico anual, gastos por categoria e fluxo
de caixa — fonte de verdade para o analista financeiro pessoal.

Uso (a partir da raiz do projeto):
    .venv-analise/Scripts/python.exe scripts/consulta_controle.py
"""
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from core.controle import get_controle, get_gastos_categoria_anual, get_historico_anual
from core.investimentos import get_cashflow_mensal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()


def _ano_mes_atual() -> tuple[int, int]:
    hoje = date.today()
    return hoje.year, hoje.month


def extrair() -> dict:
    ano, mes = _ano_mes_atual()
    try:
        controle = get_controle(ano, mes)
    except Exception as exc:
        controle = {"error": str(exc)}

    try:
        historico = get_historico_anual()
    except Exception as exc:
        historico = {"error": str(exc), "anos": []}

    try:
        cashflow = get_cashflow_mensal()
    except Exception as exc:
        cashflow = {"error": str(exc)}

    # Gastos por categoria no mês (do controle) e no ano
    cats_mes = (controle or {}).get("categorias", [])

    try:
        cats_ano = get_gastos_categoria_anual(ano)
    except Exception as exc:
        cats_ano = {"error": str(exc)}

    return {
        "referencia": f"{ano}-{mes:02d}",
        "ano": ano,
        "mes": mes,
        "controle_mes": {
            "receitas": (controle or {}).get("receitas"),
            "despesas": (controle or {}).get("despesas"),
            "investimentos": (controle or {}).get("investimentos"),
            "categorias": cats_mes,
            "saldo": (
                round(
                    (controle or {}).get("receitas", 0)
                    - (controle or {}).get("despesas", 0)
                    - (controle or {}).get("investimentos", 0),
                    2,
                )
                if isinstance(controle, dict)
                else None
            ),
        },
        "historico_anual": historico,
        "gastos_categoria_ano": cats_ano,
        "cashflow_mensal": cashflow,
        "data_source": (controle or {}).get("data_source", "desconhecida"),
    }


if __name__ == "__main__":
    print(json.dumps(extrair(), ensure_ascii=False, indent=2, default=str))
