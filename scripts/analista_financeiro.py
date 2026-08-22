"""
Analista Financeiro Pessoal — fundamentado nos livros da biblioteca ProjetoIA.

Extrai os dados REAIS do Controle Financeiro (Dashboard Financeiro Unificado)
e produz: indicadores, avaliação, sugestões e metas com citações dos livros.

Uso (raiz do projeto):
    .venv-analise/Scripts/python.exe scripts/analista_financeiro.py [--ano N] [--mes N]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from core.controle import get_controle, get_gastos_categoria_anual, get_historico_anual
from core.investimentos import get_cashflow_mensal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

# ── Referências dos livros (biblioteca do usuário) ────────────────────────────
REF = {
    "housel": {
        "livro": "Housel — A Psicologia Financeira",
        "citas": [
            "O controle sobre o seu tempo é o dividendo mais alto que o dinheiro paga.",
            "Planejar esperando que tudo dê certo é frágil; a margem de erro permite sobreviver ao imprevisível.",
            "Ficar rico exige otimismo e risco; permanecer rico exige humildade e aversão à perda.",
        ],
    },
    "kiyosaki": {
        "livro": "Kiyosaki — Pai Rico, Pai Pobre",
        "citas": [
            "O ativo coloca dinheiro no bolso; o passivo tira. Pague-se primeiro.",
            "A independência financeira é a renda passiva cobrindo as despesas mensais.",
        ],
    },
    "cerbasi": {
        "livro": "Cerbasi — Investimentos Inteligentes",
        "citas": [
            "Sem o diagnóstico de quanto entra, sai e qual é a dívida, qualquer estratégia é construída sobre areia.",
            "Pagar dívida de cartão rotativo equivale a um investimento de ~400% a.a.",
            "Construa a reserva de emergência (6 meses de gastos) antes da renda variável.",
        ],
    },
    "kahneman": {
        "livro": "Kahneman — Rápido e Devagar",
        "citas": [
            "A maioria dos erros financeiros ocorre quando o Sistema 1 (rápido e emocional) assume o controle.",
            "A aversão à perda dói cerca de 2× mais do que o ganho equivalente traz prazer.",
        ],
    },
    "graham": {"livro": "Graham — O Investidor Inteligente",
               "citas": ["A margem de segurança é o princípio central de todo investimento."]},
    "dalio": {"livro": "Dalio — Princípios",
              "citas": ["Dor + Reflexão = Progresso.",
                        "A diversificação é o único almoço grátis nos investimentos."]},
    "gunther": {"livro": "Gunther — Axiomas de Zurique",
                "citas": ["Só aposte no que entende; não confunda sorte com habilidade.",
                          "A primeira regra é não perder dinheiro."]},
}


def _pct(a, b):
    return round(a / b * 100, 1) if b else None


def gerar(ano: int, mes: int | None = None) -> dict:
    hoje = date.today()
    mes = mes or hoje.month

    controle = get_controle(ano, mes)
    historico = get_historico_anual()
    cashflow = get_cashflow_mensal()
    cats_ano = get_gastos_categoria_anual(ano)

    rec = controle.get("receitas") or 0
    desp = controle.get("despesas") or 0
    inv = controle.get("investimentos") or 0
    saldo = round(rec - desp - inv, 2)
    tp = _pct(rec - desp, rec)

    por_ano = historico.get("por_ano", {})

    def _chave_ano(k):
        return next((v for ch, v in por_ano.items() if int(ch) == int(k)), None)

    ano_ant = int(ano) - 1
    rec_ant = (_chave_ano(ano_ant) or {}).get("receitas")
    desp_ant = (_chave_ano(ano_ant) or {}).get("despesas")

    ult = [m for m in cashflow if isinstance(m, dict)]
    media_desp = round(sum(m.get("despesas", 0) for m in ult[-6:]) / len(ult[-6:]), 2) if len(ult[-6:]) else None
    reserva_alvo = round(media_desp * 6, 2) if media_desp else None

    cats_top = sorted(cats_ano, key=lambda c: c.get("gasto", 0), reverse=True)[:6]

    rec_atual_ano = (_chave_ano(ano) or {}).get("receitas")
    rec_variacao = _pct(rec_atual_ano - rec_ant, rec_ant) if (rec_atual_ano and rec_ant) else None

    return {
        "referencia": f"{ano}-{mes:02d}",
        "mes": {
            "receitas": rec, "despesas": desp, "investimentos": inv, "saldo": saldo,
            "taxa_poupanca_pct": tp,
        },
        "comparativo_ano": {
            "ano_atual": int(ano), "ano_anterior": ano_ant,
            "receitas_ano_anterior": rec_ant, "despesas_ano_anterior": desp_ant,
            "receitas_variacao_pct": rec_variacao,
        },
        "indicadores": {
            "media_despesa_6m": media_desp,
            "reserva_emergencia_alvo_6m": reserva_alvo,
            "maiores_categorias_ano": cats_top,
        },
        "historico_anos": por_ano,
        "cashflow_ultimos_12": ult[-12:],
        "referencias": {"livros": [r["livro"] for r in REF.values()]},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ano", type=int, default=date.today().year)
    ap.add_argument("--mes", type=int, default=None)
    args = ap.parse_args()
    print(json.dumps(gerar(args.ano, args.mes), ensure_ascii=False, indent=2, default=str))
