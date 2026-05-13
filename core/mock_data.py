"""
core/mock_data.py
Dados mockados com o mesmo schema que as queries reais usarão na Fase 4.

Estrutura espelha as tabelas do banco:
  patrimonio        → saldos e totais (tabela: contas + operacoes)
  fluxo_mes         → receitas/despesas do mês (tabela: transacoes)
  historico_mensal  → últimos 6 meses (query agrupada por mês)
  categorias_despesa→ gastos por categoria (tabela: transacoes + categorias + orcamentos)
  portfolio         → resumo da carteira (tabela: operacoes + cotacoes)
  classes_ativo     → alocação por classe (tabela: ativos + operacoes)
  alertas_dashboard → alertas calculados (regras de negócio)
  proximos_passos   → ações recomendadas (regras de negócio)

Substituição na Fase 4:
  Cada bloco abaixo vira uma query em core/financeiro.py
"""

# ── Período de referência ─────────────────────────────────────────────────────
MES_REFERENCIA = "Maio 2026"
MES_ANTERIOR = "Abril 2026"

# ── Patrimônio consolidado ────────────────────────────────────────────────────
# Schema: { total, investido, saldo_bancario, delta_mes_pct, saude_score }
PATRIMONIO = {
    "total":           87_450.00,
    "investido":       75_150.00,
    "saldo_bancario":  12_300.00,
    "delta_mes_pct":    5.2,       # variação vs mês anterior (%)
    "delta_mes_valor":  4_320.00,  # variação absoluta vs mês anterior
    "saude_score":     78,         # 0-100: composite score financeiro
}

# ── Fluxo de caixa do mês ─────────────────────────────────────────────────────
# Schema: { receitas, despesas, economia, taxa_poupanca_pct, ... }
FLUXO_MES = {
    "receitas":                8_500.00,
    "despesas":                4_200.00,
    "economia":                4_300.00,
    "taxa_poupanca_pct":       50.6,
    "receitas_mes_anterior":   8_200.00,
    "despesas_mes_anterior":   4_000.00,
    "economia_mes_anterior":   4_200.00,
    "taxa_poupanca_anterior":  51.2,
    "meses_reserva":           2.9,
    "meta_meses_reserva":      6.0,
    "maior_categoria":         "Moradia",
    "maior_categoria_valor":   1_250.00,
    "categoria_alerta":        "Lazer",
    "categoria_alerta_pct":    93.3,
}

# ── Histórico mensal (últimos 6 meses) ────────────────────────────────────────
# Schema: lista de { mes, receitas, despesas, economia, patrimonio }
HISTORICO_MENSAL = [
    {"mes": "Dez", "receitas": 7_800, "despesas": 4_500, "economia": 3_300, "patrimonio": 62_000},
    {"mes": "Jan", "receitas": 8_100, "despesas": 3_900, "economia": 4_200, "patrimonio": 65_400},
    {"mes": "Fev", "receitas": 7_500, "despesas": 4_100, "economia": 3_400, "patrimonio": 67_200},
    {"mes": "Mar", "receitas": 8_900, "despesas": 4_800, "economia": 4_100, "patrimonio": 70_100},
    {"mes": "Abr", "receitas": 8_200, "despesas": 4_000, "economia": 4_200, "patrimonio": 73_500},
    {"mes": "Mai", "receitas": 8_500, "despesas": 4_200, "economia": 4_300, "patrimonio": 87_450},
]

# ── Categorias de despesa com orçamento ───────────────────────────────────────
# Schema: lista de { nome, gasto, orcamento, pct_usado }
CATEGORIAS_DESPESA = [
    {"nome": "Moradia",     "gasto": 1_250.00, "orcamento": 1_500.00, "pct_usado": 83.3},
    {"nome": "Alimentação", "gasto":   890.00, "orcamento": 1_000.00, "pct_usado": 89.0},
    {"nome": "Transporte",  "gasto":   410.00, "orcamento":   500.00, "pct_usado": 82.0},
    {"nome": "Saúde",       "gasto":   230.00, "orcamento":   400.00, "pct_usado": 57.5},
    {"nome": "Lazer",       "gasto":   280.00, "orcamento":   300.00, "pct_usado": 93.3},
    {"nome": "Assinaturas", "gasto":   140.00, "orcamento":   150.00, "pct_usado": 93.3},
    {"nome": "Outros",      "gasto":   220.00, "orcamento":   300.00, "pct_usado": 73.3},
]

# ── Portfólio — resumo ────────────────────────────────────────────────────────
# Schema: { rentabilidade_mes_pct, rentabilidade_ano_pct, dividendos_mes, ... }
PORTFOLIO = {
    "rentabilidade_mes_pct":  3.2,
    "rentabilidade_ano_pct": 12.4,
    "rentabilidade_mes_anterior_pct": 1.8,
    "dividendos_mes":          420.00,
    "dividendos_ano":        2_840.00,
    "maior_concentracao_nome": "ETF (IVVB11)",
    "maior_concentracao_pct":  29.4,
    "meta_concentracao_pct":   25.0,
    "num_ativos":              12,
}

# ── Classes de ativos ─────────────────────────────────────────────────────────
# Schema: lista de { nome, valor, pct_carteira, rentab_mes_pct, cor }
CLASSES_ATIVO = [
    {"nome": "ETF",         "valor": 22_100.00, "pct_carteira": 29.4, "rentab_mes_pct":  5.2, "cor": "#F6C90E"},
    {"nome": "Ações BR",    "valor": 18_500.00, "pct_carteira": 24.6, "rentab_mes_pct":  2.1, "cor": "#00C896"},
    {"nome": "Renda Fixa",  "valor": 14_200.00, "pct_carteira": 18.9, "rentab_mes_pct":  1.1, "cor": "#4A9EFF"},
    {"nome": "FII",         "valor": 12_300.00, "pct_carteira": 16.4, "rentab_mes_pct":  1.8, "cor": "#9B59B6"},
    {"nome": "Cripto",      "valor":  8_050.00, "pct_carteira": 10.7, "rentab_mes_pct": -4.2, "cor": "#FC5C7D"},
]

# ── Alertas para o dashboard ──────────────────────────────────────────────────
# Schema: lista de { tipo, icone, titulo, descricao, acao, modulo }
ALERTAS_DASHBOARD = [
    {
        "tipo":      "alerta",
        "icone":     "⚠️",
        "titulo":    "Lazer próximo do limite",
        "descricao": "93% do orçamento utilizado. Restam R$ 20,00 para o mês.",
        "acao":      "Controle Financeiro",
        "modulo":    "💰 Controle Financeiro",
    },
    {
        "tipo":      "alerta",
        "icone":     "🛡️",
        "titulo":    "Reserva de emergência em 65%",
        "descricao": "Faltam R$ 10.500 para a meta de 6× as despesas mensais.",
        "acao":      "Metas",
        "modulo":    "🎯 Metas",
    },
    {
        "tipo":      "info",
        "icone":     "📊",
        "titulo":    "Concentração elevada em ETF",
        "descricao": "IVVB11 representa 29,4% da carteira. Meta: abaixo de 25%.",
        "acao":      "Carteira",
        "modulo":    "💼 Carteira",
    },
    {
        "tipo":      "sucesso",
        "icone":     "🎉",
        "titulo":    "Taxa de poupança acima da meta",
        "descricao": "Você poupou 50,6% da renda. Meta mensal: 30%. Excelente!",
        "acao":      "Metas",
        "modulo":    "🎯 Metas",
    },
]

# ── Próximos passos financeiros ───────────────────────────────────────────────
# Schema: lista de { numero, urgencia, titulo, descricao, modulo }
PROXIMOS_PASSOS = [
    {
        "numero":    1,
        "urgencia":  "alta",
        "titulo":    "Reforçar reserva de emergência",
        "descricao": "Aportar R$ 1.750/mês até atingir 6× as despesas (meta: R$ 30.000).",
        "modulo":    "🎯 Metas",
    },
    {
        "numero":    2,
        "urgencia":  "alta",
        "titulo":    "Revisar orçamento de Lazer",
        "descricao": "Categoria em 93% do limite. Controlar gastos ou ajustar teto.",
        "modulo":    "💰 Controle Financeiro",
    },
    {
        "numero":    3,
        "urgencia":  "media",
        "titulo":    "Rebalancear concentração em ETF",
        "descricao": "IVVB11 em 29,4% — acima do alvo de 25%. Diversificar próximo aporte.",
        "modulo":    "💼 Carteira",
    },
    {
        "numero":    4,
        "urgencia":  "baixa",
        "titulo":    "Confirmar proventos de Maio",
        "descricao": "R$ 420 em dividendos previstos. Verificar recebimento na corretora.",
        "modulo":    "💵 Proventos",
    },
]
