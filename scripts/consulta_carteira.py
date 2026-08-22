"""
Consulta da carteira real do Dashboard Financeiro Unificado (Tiago Barros).

Usa o código-fonte do próprio dashboard (core/investimentos.get_carteira) para
produzir EXATAMENTE os mesmos números que o app online mostra — incluindo as
regras de agregação por base_ticker, câmbio USD/BRL e extras do Nomad.

Uso (a partir da raiz do projeto Dashboard-Financeiro-Unificado):
    .venv-analise/Scripts/python.exe scripts/consulta_carteira.py

O .env local (com SUPABASE_DB_URL) já aponta para o projeto Supabase correto
(jdvijvfrjfpbnlyfxltr), o mesmo que o Streamlit Secrets usa via DATABASE_URL.
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from core.investimentos import get_carteira

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()


def _resumo(porta_json: bool = True) -> dict:
    carteira = get_carteira()
    total_mercado = carteira.get("total_mercado")
    total_investido = carteira.get("total_investido")
    posicoes = carteira.get("posicoes", [])
    por_classe = carteira.get("por_classe", [])

    resumo = {
        "total_mercado": total_mercado,
        "total_investido": total_investido,
        "rendimento_nao_realizado": (
            round(total_mercado - total_investido, 2)
            if total_mercado and total_investido else None
        ),
        "num_ativos": len(posicoes),
        "por_classe": [
            {
                "nome": c.get("nome"),
                "valor_mercado": round(c.get("valor_mercado") or 0, 2),
                "total_investido": round(c.get("total_investido") or 0, 2),
                "num_ativos": c.get("num_ativos"),
                "pct_carteira": round(c.get("pct_carteira") or 0, 2),
                "rentab_pct": c.get("rentab_pct"),
            }
            for c in por_classe
        ],
        "posicoes": [
            {
                "ticker": p.get("ticker"),
                "nome": (p.get("nome") or "")[:60],
                "classe": p.get("classe"),
                "quantidade": p.get("quantidade"),
                "preco_medio": p.get("preco_medio"),
                "total_investido": round(p.get("total_investido") or 0, 2),
                "valor_mercado": round(p.get("valor_mercado") or 0, 2),
                "pct_carteira": round(p.get("pct_carteira") or 0, 2),
                "rentab_pct": p.get("rentab_pct"),
                "moeda": p.get("moeda"),
            }
            for p in sorted(
                posicoes, key=lambda x: x.get("valor_mercado") or 0, reverse=True
            )
        ],
    }
    return resumo


if __name__ == "__main__":
    r = _resumo()
    print(json.dumps(r, ensure_ascii=False, indent=2))
