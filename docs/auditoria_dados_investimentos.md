# Auditoria de Dados — Dashboard-Investimentos

> Data: 2026-05-13
> Repositório auditado: `Tiago84Barros/Dashboard-Investimentos`
> Caminho local: `Projetos/Dashboard-Investimentos/Dashboard-Investimentos-main/investment-dashboard/`
> Escopo: somente leitura — nenhum código foi alterado.

---

## Visão Geral da Arquitetura

O projeto tem arquitetura **backend + frontend separados**:

| Camada | Stack | Função |
|--------|-------|--------|
| **Backend** | FastAPI + SQLAlchemy + SQLite/PostgreSQL | Persiste dados, processa importações, serve API REST |
| **Frontend** | Streamlit + yfinance + requests | Consome API REST, busca cotações ao vivo, renderiza dashboards |

O frontend **nunca acessa o banco diretamente** — toda leitura de dados do banco passa pela
API REST (`api_client.py`). Cotações em tempo real são buscadas no frontend via yfinance e Yahoo Finance.

---

## 1. Banco de Dados

**Sim — usa banco de dados.**

| Item | Detalhe |
|------|---------|
| Motor padrão | **SQLite** — `backend/investment_dashboard.db` |
| Motor alternativo | PostgreSQL (configurado via `DATABASE_URL`) |
| ORM | SQLAlchemy 2.0 com `mapped_column` (typed) |
| Arquivos SQLite presentes | `backend/investment.db` (legado) e `backend/investment_dashboard.db` (atual) |
| Conexão | `backend/app/db/session.py` — singleton `SessionLocal` com `check_same_thread=False` para SQLite |

Configuração em `backend/app/core/config.py`:
```python
DATABASE_URL: str = "sqlite:///./investment_dashboard.db"  # default
# Produção: postgresql+psycopg2://usuario:senha@host:5432/investment_dashboard
```

---

## 2. Formatos de Arquivo Suportados

| Formato | Fonte | Processado por |
|---------|-------|---------------|
| **Excel (.xlsx)** | B3 — aba "Movimentação" | `b3_import_service.parse_b3_movimentacao()` |
| **Excel (.xlsx)** | B3 — aba "Negociação" | `b3_negociacao_service.parse_b3_negociacao()` |
| **Excel (.xlsx)** | XP Investimentos — Relatório Consolidado Anual/Mensal | `xp_import_service.parse_xp_consolidado()` |
| **PDF** | Nomad (Apex Clearing ou DriveWealth) | `nomad_import_service_v2.parse_nomad_pdf()` |
| **JSON** | `backend/excluded_tickers.json` | `api/v1/endpoints/exclusions._load()` |
| **JSON** | `backend/` (position_overrides) | `api/v1/endpoints/position_overrides._load()` |

Não usa CSV. Não usa dados mockados — todos os dados vêm de importações reais ou do banco.

---

## 3. Arquivos que Contêm os Dados

### Banco de Dados (principal)
| Arquivo | Papel |
|---------|-------|
| `backend/investment_dashboard.db` | Banco SQLite ativo com todas as tabelas |
| `backend/investment.db` | Banco SQLite legado (provavelmente versão anterior) |

### Configuração / Dados Estáticos
| Arquivo | Conteúdo |
|---------|---------|
| `backend/excluded_tickers.json` | Lista de tickers excluídos da carteira exibida |
| `backend/app/services/sector_data.py` | Dicionário estático `SECTOR_MAP` com ~130 tickers B3 → setor |

### Arquivos Importados pelo Usuário (não versionados)
| Tipo | Origem | Quando |
|------|--------|--------|
| `relatorio-consolidado-anual-YYYY.xlsx` | XP Investimentos | Anualmente |
| `relatorio-consolidado-mensal-YYYY-MES.xlsx` | XP Investimentos | Mensalmente |
| `movimentacao-*.xlsx` | Portal da B3 | Sob demanda |
| `negociacao-*.xlsx` | Portal da B3 | Sob demanda |
| `*.pdf` (Apex/DriveWealth) | Nomad | A cada operação |

---

## 4. Funções de Leitura dos Dados

### Backend — Importação de arquivos externos

| Função | Arquivo | Lê de | Salva em |
|--------|---------|-------|----------|
| `parse_b3_movimentacao(file_bytes, db)` | `services/b3_import_service.py` | Excel B3 Movimentação | `transactions`, `incomes`, `assets`, `accounts` |
| `parse_b3_negociacao(file_bytes, db)` | `services/b3_negociacao_service.py` | Excel B3 Negociação | `transactions`, `assets` |
| `parse_xp_consolidado(content, filename, db)` | `services/xp_import_service.py` | Excel XP (6 abas) | `xp_positions`, `incomes` |
| `parse_nomad_pdf(file_bytes, filename, db)` | `services/nomad_import_service_v2.py` | PDF Nomad (Apex/DriveWealth) | `transactions`, `assets` |

### Backend — Leitura do banco para servir a API

| Função | Arquivo | Lê de | Retorna |
|--------|---------|-------|---------|
| `build_portfolio_positions(db)` | `services/portfolio_service.py` | `xp_positions`, `transactions`, `assets` | Lista de posições ativas com custo médio |
| `_build_from_xp_snapshot(db, report_date)` | `services/portfolio_service.py` | `xp_positions` (snapshot mais recente) | Posições nacionais com custo calculado |
| `_build_from_transactions(db)` | `services/portfolio_service.py` | `transactions` + `assets` | Posições calculadas por transações (fallback) |
| `_build_nomad_positions(db)` | `services/portfolio_service.py` | `transactions` (external_id LIKE "nomad-%") | Posições internacionais em USD convertidas para BRL |
| `_costs_from_transactions(db)` | `services/portfolio_service.py` | `transactions` | `{ticker: gross_cost}` com reset em venda total |
| `position_inception_dates(db)` | `services/portfolio_service.py` | `transactions` | Data de início da posição atual por ativo |
| `list_nomad_transactions(db)` | `services/nomad_import_service_v2.py` | `transactions` WHERE `external_id LIKE "nomad-%"` | Lista de operações Nomad |

### Frontend — Leitura de preços ao vivo

| Função | Arquivo | Lê de |
|--------|---------|-------|
| `_load_prices(tickers_br, tickers_us)` | `frontend/pages/1_Dashboard.py` | yfinance (B3 com .SA, US sem sufixo, USDBRL=X) |
| `_load_index_series(dates_iso)` | `frontend/pages/1_Dashboard.py` | yfinance (^BVSP, IFIX.SA, ^GSPC, ^IXIC) + BCB API (CDI 4391, IPCA 433, SELIC 4189) |
| `_fetch_macro()` | `frontend/pages/1_Dashboard.py` | BCB API (SELIC 432, IPCA 13522, CDI 4391) + AwesomeAPI (USD/BRL) + Yahoo Finance (^BVSP, ^GSPC, IFIX.SA) |
| `fetch_b3_prices(tickers)` | `services/price_service.py` | Yahoo Finance API v7 direta (sem yfinance) |
| `_current_usd_brl()` | `services/portfolio_service.py` | yfinance "USDBRL=X" |
| `_current_us_prices(tickers)` | `services/portfolio_service.py` | yfinance (ETFs internacionais) |
| `_fetch_ptax(trade_date)` | `services/nomad_import_service_v2.py` | BCB PTAX API `olinda.bcb.gov.br` |

---

## 5. APIs Externas Utilizadas

### Yahoo Finance
| Endpoint | Dados | Usado por |
|----------|-------|-----------|
| `query1.finance.yahoo.com/v7/finance/quote` | Cotações em lote para B3 (`.SA`) | `price_service.fetch_b3_prices()` |
| `query1.finance.yahoo.com/v8/finance/chart/USDBRL=X` | USD/BRL atual | `_fetch_macro()` |
| `query1.finance.yahoo.com/v8/finance/chart/%5EBVSP` | Ibovespa | `_fetch_macro()` |
| `query1.finance.yahoo.com/v8/finance/chart/%5EGSPC` | S&P 500 | `_fetch_macro()` |
| `query1.finance.yahoo.com/v8/finance/chart/IFIX.SA` | IFIX | `_fetch_macro()` |
| yfinance (biblioteca) | Histórico de ativos B3 e US, USDBRL=X | `_load_prices()`, `_load_index_series()`, `portfolio_service` |

### Banco Central do Brasil (BCB)
| Endpoint | Série | Dados |
|----------|-------|-------|
| `api.bcb.gov.br/dados/serie/bcdata.sgs.432` | 432 | SELIC (taxa básica) |
| `api.bcb.gov.br/dados/serie/bcdata.sgs.4391` | 4391 | CDI acumulado |
| `api.bcb.gov.br/dados/serie/bcdata.sgs.433` | 433 | IPCA mensal |
| `api.bcb.gov.br/dados/serie/bcdata.sgs.13522` | 13522 | IPCA acumulado 12m |
| `api.bcb.gov.br/dados/serie/bcdata.sgs.4189` | 4189 | SELIC acumulada ao mês |
| `olinda.bcb.gov.br/olinda/servico/PTAX` | — | PTAX USD/BRL histórico por data |

### AwesomeAPI
| Endpoint | Dados |
|----------|-------|
| `economia.awesomeapi.com.br/json/last/USD-BRL` | USD/BRL em tempo real (bid + % mudança) |

### Railway (infraestrutura)
O frontend aguarda até 90s pelo backend via `/health` — trata cold-start do Railway.

---

## 6. Bibliotecas para Dados Financeiros

| Biblioteca | Versão | Uso |
|------------|--------|-----|
| `yfinance` | ≥ 0.2.50 | Cotações históricas e em tempo real (B3, US, índices, câmbio) |
| `pdfplumber` | 0.11.4 | Extração de texto de PDFs Nomad (Apex + DriveWealth) |
| `openpyxl` | 3.1.5 | Leitura de arquivos Excel (.xlsx) da B3 e XP |
| `pandas` | 2.2.x | Processamento e alinhamento de séries temporais |
| `requests` | 2.32.x | Chamadas HTTP para BCB, AwesomeAPI, Yahoo Finance |
| `httpx` | 0.27.2 | HTTP alternativo no backend |
| `beautifulsoup4` | ≥ 4.12 | HTML parsing (frontend, uso não identificado nos arquivos lidos) |
| `lxml` | ≥ 5.0 | Parser XML/HTML para beautifulsoup4 |
| `sqlalchemy` | 2.0.35 | ORM + queries typed para SQLite/PostgreSQL |

---

## 7. Indicadores Calculados

### Patrimônio e Resultado
| Indicador | Fórmula |
|-----------|---------|
| **Patrimônio Total** | `sum(market_value)` — ou `sum(gross_cost)` se sem cotação |
| **Resultado Acumulado (R$)** | `total_market - total_cost` |
| **Resultado Acumulado (%)** | `resultado_abs / total_cost × 100` |
| **Split BR/Exterior** | Separa por `currency == "USD"` / `country == "US"` / `source == "nomad"` |

### Renda / Proventos
| Indicador | Fórmula |
|-----------|---------|
| **Renda Total Recebida** | `sum(incomes.amount)` |
| **Renda 12m** | `sum(incomes.amount)` WHERE `date >= now - 12 meses` |
| **Dividend Yield (12m)** | `renda_12m / patrimônio × 100` |

### Custo Médio (portfolio_service)
| Indicador | Fórmula |
|-----------|---------|
| **Custo Médio por Ativo** | `gross_cost / quantity` — recalculado a cada compra/venda |
| **Reinício de Ciclo** | Zera custo quando `quantity ≤ 0.001` após venda total |
| **Override de Data** | `position_overrides.json` — descarta transações anteriores à data configurada |
| **Conversão Nomad USD→BRL** | `gross_cost_usd × PTAX(data_operacao)` |
| **Valor de Mercado Nomad** | `price_usd × quantity × USDBRL_atual` |

### Risco e Diversificação
| Indicador | Fórmula |
|-----------|---------|
| **HHI** | `sum((peso_ativo)²)` onde `peso = market_value / total` |
| **N Efetivo** | `1 / HHI` — número de ativos equivalentes |
| **Peso por Ativo** | `market_value / total_market` |
| **Concentração Setorial** | `sum(market_value) por setor / total` |
| **% por Classe** | Ações, FII, ETF BR, ETF Intl, Tesouro, Renda Fixa, Fundo RF |
| **Radar de Risco (6 eixos)** | Concentração ativo, setorial, FIIs, baixa diversificação, dependência dividendos, renda fixa |

### Dependências Macroeconômicas
| Pilar | Critério de Inclusão |
|-------|---------------------|
| Brasil / Risco fiscal | Todos os ativos nacionais |
| Selic / CDI / Juros | FIIs, Tesouro, Renda Fixa, Fundo RF |
| Inflação / IPCA | Tesouro IPCA, Renda Fixa IPCA, FIIs papel |
| Crédito privado | Renda Fixa, Fundo RF, FIIs papel |
| Renda imobiliária | FIIs |
| Bolsa Brasil | Ações, ETF BR |
| Commodities / câmbio / China | PETR3/4, VALE3, CSNA3, GGBR4, etc. |
| Dólar / Exterior | ETF Internacional (Nomad) |
| Fed / Juros EUA | ETF Internacional |
| Bolsa EUA / Tecnologia | SPY, VOO, IVV, VTI, QQQ |
| Renda fixa em dólar | SGOV, TFLO, BND, AGG |

### Benchmarks (histórico)
| Benchmark | Fonte |
|-----------|-------|
| IBOV | yfinance `^BVSP` |
| IFIX | yfinance `IFIX.SA` |
| S&P 500 | yfinance `^GSPC` |
| Nasdaq | yfinance `^IXIC` |
| CDI | BCB série 4391 |
| IPCA | BCB série 433 |
| SELIC | BCB série 4189 |

---

## 8. Dados Exportáveis para o Dashboard-Financeiro-Unificado

### Mapeamento direto de tabelas

| Tabela Origem (investimentos) | Tabela Destino (unificado) | Campos equivalentes |
|-------------------------------|---------------------------|---------------------|
| `transactions` | `operacoes` | `date`→`data`, `type`→`tipo` (buy/sell), `quantity`→`quantidade`, `price`→`preco_unitario`, `total`→`total`, `currency`→`moeda`, `asset.ticker`→`ticker` |
| `incomes` | `proventos` | `date`→`data_pagamento`, `type`→`tipo_provento` (dividend/jcp/rendimento), `amount`→`valor_liquido`, `asset.ticker`→`ticker` |
| `assets` | `ativos` | `ticker`→`ticker`, `name`→`nome`, `type`→`tipo` (stock/fii/etf/tesouro/renda_fixa), `currency`→`moeda`, `country`→`pais` |
| `xp_positions` | `cotacoes` | `report_date`→`data`, `ticker`→`ticker`, `market_price`→`preco_fechamento`, `market_value`→`valor_mercado` |
| `accounts` | `contas` | `name`→`nome`, `type`→`tipo`, `currency`→`moeda` |

### Volume estimado (dados reais no SQLite)
- `transactions`: operações históricas B3 + Nomad (compras, vendas, bonificações, desdobros)
- `incomes`: dividendos, JCP, rendimentos de FIIs e ações (B3 + XP)
- `xp_positions`: snapshots mensais/anuais da XP com patrimônio por ativo e data
- `assets`: catálogo de ativos com ticker, nome e tipo

### O que NÃO pode ser aproveitado diretamente
| Item | Motivo |
|------|--------|
| `positions_snapshots` (tabela vazia/legado) | Substituída por `xp_positions` |
| `sector_data.py` (mapa setor) | Pode ser reusado como código Python — não é dado |
| Cotações ao vivo (Yahoo/BCB) | São temporárias — buscadas em runtime, não persistidas |
| PDFs Nomad | Já processados e em `transactions` — não reexportar |

---

## 9. Melhor Forma de Integração sem Dependência de Supabase Terceiro

### Estratégia recomendada: Leitura Direta do SQLite

O `ImportadorPostgres` existente no App 4 (`etl/importacao.py`) usa SQLAlchemy,
que suporta SQLite nativamente. A integração pode ser feita **sem nenhuma
dependência de Supabase** dos apps originais — apenas copiar o arquivo SQLite.

```
Dashboard-Investimentos/
  backend/investment_dashboard.db   ← copiar para pasta acessível
         ↓
Dashboard-Financeiro-Unificado/
  etl/importacao.py                 ← ImportadorPostgres adaptado para SQLite
  etl/importacao_investimentos.py   ← módulo específico (a criar na Fase 5)
```

### Passo a passo técnico

**Passo 1 — Ler o SQLite como fonte**
```python
# SOURCE_DB_APP2 no .env:
# SOURCE_DB_APP2="sqlite:////caminho/para/investment_dashboard.db"
from etl.importacao import ImportadorPostgres
imp = ImportadorPostgres(settings.SOURCE_DB_APP2)
```

**Passo 2 — Implementar `importar_app2_investimentos()`**

Na classe `ImportadorPostgres.importar_app2_investimentos()` (atualmente `NotImplementedError`),
implementar os 3 mapeamentos:

```python
# Mapeamento 1: transactions → operacoes
mapeamento_transacoes = {
    "ticker":           "ticker",        # via JOIN assets
    "date":             "data",
    "type":             "tipo",          # buy→compra, sell→venda, split→desdobro
    "quantity":         "quantidade",
    "price":            "preco_unitario",
    "total":            "total",
    "currency":         "moeda",
    "external_id":      "id_externo",    # deduplicação
}

# Mapeamento 2: incomes → proventos
mapeamento_proventos = {
    "ticker":           "ticker",        # via JOIN assets
    "date":             "data_pagamento",
    "type":             "tipo_provento", # dividend→dividendo, jcp→jcp, rendimento→rendimento
    "amount":           "valor_liquido",
    "currency":         "moeda",
}

# Mapeamento 3: assets → ativos
mapeamento_ativos = {
    "ticker":           "ticker",
    "name":             "nome",
    "type":             "tipo",
    "currency":         "moeda",
    "country":          "pais",
}
```

**Passo 3 — Query com JOIN (necessário para obter ticker)**

A tabela `transactions` não tem `ticker` — requer JOIN com `assets`:

```sql
SELECT
    a.ticker,
    t.date,
    t.type,
    t.quantity,
    t.price,
    t.total,
    t.currency,
    t.external_id
FROM transactions t
JOIN assets a ON a.id = t.asset_id
WHERE t.currency = 'BRL'  -- apenas ativos nacionais (Nomad usa USD)
  AND t.external_id NOT LIKE 'nomad-%'
ORDER BY t.date ASC;
```

**Passo 4 — Nomad (operações em USD)**

Para ativos Nomad, converter `total` (em USD) para BRL na importação:

```sql
SELECT
    a.ticker,
    t.date,
    t.type,
    t.quantity,
    t.price  AS preco_usd,
    t.total  AS total_usd,
    'USD'    AS moeda_original
FROM transactions t
JOIN assets a ON a.id = t.asset_id
WHERE t.external_id LIKE 'nomad-%';
-- Converter para BRL via PTAX histórico ou taxa atual
```

### Resumo das vantagens desta abordagem

| Aspecto | Detalhe |
|---------|---------|
| **Zero dependência externa** | SQLite é um arquivo local — sem servidor, sem credenciais de rede |
| **Compatibilidade total** | `ImportadorPostgres` já usa SQLAlchemy — funciona com `sqlite:///` |
| **Deduplicação gratuita** | Campo `external_id` único em `transactions` — `ON CONFLICT DO NOTHING` funciona |
| **dry_run seguro** | Flag `dry_run=True` padrão evita gravações acidentais |
| **Sem quebra do App 4** | `MOCK_MODE=true` continua funcionando enquanto a importação não é feita |
| **Dados completos** | Histórico total de operações, proventos e snapshots XP disponíveis |

### Variável de ambiente a configurar

```ini
# .env — Dashboard-Financeiro-Unificado
SOURCE_DB_APP2="sqlite:////C:/Users/Tiago Barros/OneDrive/Área de Trabalho/Meus Arquivos/Projetos/Dashboard-Investimentos/Dashboard-Investimentos-main/investment-dashboard/backend/investment_dashboard.db"
```

---

## Resumo Executivo (9 perguntas)

| # | Pergunta | Resposta |
|---|---------|---------|
| 1 | Usa banco de dados? | ✅ Sim — **SQLite** (`investment_dashboard.db`), com suporte a PostgreSQL |
| 2 | Usa CSV/Excel/JSON/PDF/mock? | ✅ Excel (.xlsx) da B3 e XP; PDF (Nomad); JSON (configuração); **sem mock** |
| 3 | Arquivos com dados | `investment_dashboard.db`, `excluded_tickers.json`, Excel/PDF importados pelo usuário |
| 4 | Funções de leitura | `parse_b3_*`, `parse_xp_consolidado`, `parse_nomad_pdf`, `build_portfolio_positions`, `_load_prices` |
| 5 | APIs externas | Yahoo Finance (yfinance + REST), BCB (SELIC/CDI/IPCA/PTAX), AwesomeAPI (USD/BRL) |
| 6 | Bibliotecas financeiras | yfinance, pdfplumber, openpyxl, pandas, requests |
| 7 | Indicadores calculados | Patrimônio, Resultado, DY, HHI, N Efetivo, Custo Médio, Benchmarks (IBOV/CDI/SELIC/S&P500), Radar de Risco, Dependências Macro |
| 8 | Dados exportáveis | `transactions`→`operacoes`, `incomes`→`proventos`, `assets`→`ativos`, `xp_positions`→`cotacoes` |
| 9 | Melhor integração | Leitura direta do SQLite via `ImportadorPostgres` (SQLAlchemy) — `SOURCE_DB_APP2="sqlite:///..."` — zero Supabase, zero servidor |
