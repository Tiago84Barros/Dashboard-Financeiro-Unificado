"""
Visão Global das Finanças — correlação entre Controle Financeiro e Investimentos.

Cruza dados das duas seções do Dashboard Financeiro Unificado para produzir
um panorama completo da jornada rumo à independência financeira:

  - Controle Financeiro: receitas, despesas, taxa de poupança, reserva
  - Investimentos: valor de mercado, aportes, renda passiva (proventos 12m)
  - Correlação: índice de independência financeira (renda passiva / despesas)

Uso (raiz do projeto):
    .venv-analise/Scripts/python.exe scripts/analista_global.py [--ano N]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from core.controle import get_controle, get_historico_anual
from core.investimentos import get_carteira, get_cashflow_mensal
from core.proventos import get_proventos

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()


def _pct(a, b):
    return round(a / b * 100, 1) if b else None


def gerar(ano: int) -> dict:
    hoje = date.today()
    mes = hoje.month

    # ── Controle Financeiro ──
    controle = get_controle(ano, mes)
    rec = controle.get("receitas") or 0
    desp = controle.get("despesas") or 0
    inv = controle.get("investimentos") or 0
    saldo = round(rec - desp - inv, 2)
    tp = _pct(rec - desp, rec)

    hist = get_historico_anual().get("por_ano", {})
    def _c(k): return next((v for ch, v in hist.items() if int(ch) == int(k)), None)
    ano_prev = _c(int(ano) - 1) or {}

    # ── Investimentos ──
    carteira = get_carteira()
    tm = carteira.get("total_mercado")
    ti = carteira.get("total_investido")
    cash = get_cashflow_mensal()
    cf = [m for m in cash if isinstance(m, dict)]
    aporte_ano = round(sum(m.get("investimentos", 0) for m in cf if m.get("ano") == ano), 2)
    aporte_6m = round(sum(m.get("investimentos", 0) for m in cf[-6:]), 2)

    # ── Renda passiva (proventos últimos 12 meses) ──
    prov = get_proventos()
    renda_passiva_12m = prov.get("total_12m")
    renda_passiva_mes = round((renda_passiva_12m or 0) / 12, 2)

    # Média de despesa (últimos 6 meses) e reserva alvo
    media_desp = round(sum(m.get("despesas", 0) for m in cf[-6:]) / len(cf[-6:]), 2) if len(cf[-6:]) else None
    reserva_alvo = round((media_desp or 0) * 6, 2) if media_desp else None

    # ── Índices de correlação / independência ──
    independencia_pct = _pct(renda_passiva_mes, media_desp) if (renda_passiva_mes and media_desp) else None

    # Poupança anual acumulada vs aportes
    poupanca_ano = _c(ano) or {}
    saldo_ano = poupanca_ano.get("saldo")

    return {
        "referencia": f"{ano}-{mes:02d}",
        "controle_financeiro": {
            "receitas_mes": rec, "despesas_mes": desp, "investimentos_mes": inv,
            "saldo_mes": saldo, "taxa_poupanca_pct": tp,
            "media_despesa_6m": media_desp, "reserva_emergencia_alvo_6m": reserva_alvo,
            "saldo_anual_acumulado": saldo_ano,
            "receitas_ano_passado": ano_prev.get("receitas"),
            "despesas_ano_passado": ano_prev.get("despesas"),
        },
        "investimentos": {
            "valor_mercado": tm, "total_investido": ti,
            "aporte_ano": aporte_ano, "aporte_6m": aporte_6m,
            "proventos_12m": renda_passiva_12m, "proventos_media_mes": renda_passiva_mes,
        },
        "correlacao_independencia": {
            "independencia_financeira_pct": independencia_pct,
            "meta_cobertura_despesas_12m": round((media_desp or 0) * 12, 2) if media_desp else None,
            "patrimonio_total_visivel": round((tm or 0) + (saldo or 0), 2),
        },
        "notas_modelo": {
            "cobertura_parcial": True,
            "mensagem": (
                "Este panorama cobre apenas a carteira de investimentos do dashboard "
                "e o fluxo de caixa do controle financeiro. Financiamentos, imóveis e "
                "demais ativos/passivos fora do dashboard NÃO estão incluídos — a visão "
                "de patrimônio líquido total é parcial e subestima a condição real."
            ),
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ano", type=int, default=date.today().year)
    args = ap.parse_args()
    print(json.dumps(gerar(args.ano), ensure_ascii=False, indent=2, default=str))
