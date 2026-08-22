"""
Diagnostico rapido da carteira (T8 Fase 5.5).
Chama _carteira_real diretamente, sem Streamlit.
"""
import os
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.investimentos import _carteira_real

dados = _carteira_real()
print("=== T8 - Carteira apos cotacoes ===")
print("data_source        : real (direto)")
print("num_ativos         :", dados["num_ativos"])
print("total_investido    : R$", "{:,.2f}".format(dados["total_investido"]))
print("total_mercado      : R$", "{:,.2f}".format(dados["total_mercado"]))
print("rentab_total_pct   :", dados["rentabilidade_total_pct"], "%")
print("cotacoes_disp      :", dados["cotacoes_disponiveis"])

com_cotacao = sum(1 for p in dados["posicoes"] if p["preco_atual"] != p["preco_medio"])
sem_cotacao = dados["num_ativos"] - com_cotacao
print("ativos c/ cotacao  :", com_cotacao)
print("ativos s/ cotacao  :", sem_cotacao)

print()
print("Top 5 posicoes por total investido:")
for p in dados["posicoes"][:5]:
    ticker = p["ticker"]
    ti = p["total_investido"]
    vm = p["valor_mercado"]
    rentab = p["rentab_pct"]
    print("  {:10s}  inv=R${:>10,.2f}  merc=R${:>10,.2f}  rentab={:>6.2f}%".format(
        ticker, ti, vm, rentab))
