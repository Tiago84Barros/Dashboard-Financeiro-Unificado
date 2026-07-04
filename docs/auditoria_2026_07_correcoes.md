# Auditoria cruzada da seção Empresas B3 — correções (2026-07-04)

Origem: parecer externo (Codex/GPT-5.5) + verificação adversarial independente
(Claude, 14 agentes de leitura/verificação sobre o código real). Cada achado foi
CONFIRMADO/REFUTADO contra o código antes de corrigir. SCORE_VERSION 2.20.0 → 2.21.0.

## Vereditos sobre o parecer externo

| # | Achado do parecer | Veredito | Correção |
|---|---|---|---|
| 1 | TTM de hoje rotulado como "ano-base" (look-ahead no score atual) | CONFIRMADO | `load_multiplos_todos(ano_ref_max)` no market agora serve métricas do exercício ANUAL fechado; histórico anual limitado ao último ano com DRE publicada; ETL não cria mais linha anual só com dividendos parciais |
| 2a | Backtest não vende no rebalance (só redireciona aportes) | CONFIRMADO (design) | Rotulagem honesta na UI (checkbox/captions); venda real + IR fica como evolução futura |
| 2b | Criação de Portfólio não corta preços por `ano_inicio` | CONFIRMADO | Corte `index.year >= ano_inicio` antes de `_simular_seg_backtest` — Selic/EW não recebem mais aportes antes de a estratégia existir |
| 3 | Gate de migração mede quantidade, não qualidade; ausente vira 0,5 sem limite | CONFIRMADO | Gate exige completude crítica do market ≥ 95% da do legado (`MARKET_READ_MIN_QUALITY_RATIO`); ranking com gate de completude ponderada ≥ 60% e lista visível de excluídas |
| 4 | Pesos setoriais errados ("Consumo não Cíclico" → cíclico; "Saúde"/"Comunicações" → genérico) | CONFIRMADO | `_get_pesos_setor` com normalização Unicode + matching determinístico chave-completa, mais-longa-primeiro + testes |
| 5 | Política de fontes contraditória (web sobrescreve market; overlay contamina backtest) | CONFIRMADO (contaminação é condicional) | Com `market_active()`: `fund_data={}` (reconciliação vira só saneamento por ranges); overlay do snapshot atual restrito ao entry guard — backtest usa histórico puro |
| 6 | `alpha_selic` ×100 duplicado no contexto da LLM (1.250% em vez de 12,5%) | CONFIRMADO | Removido o ×100 nos dois pontos; teste de contrato de escala |
| 7 | Dividendos (R$/ação) no gráfico de DRE absoluta | CONFIRMADO (o legado tinha a MESMA unidade; não é regressão do market) | Fora do gráfico absoluto; gráfico próprio "R$/ação (soma anual)" |
| — | `asset_type='stock'` fixo e `is_active=True` p/ todo payload | CONFIRMADO | Inferência por perfil (FII) + sufixo do ticker (BDR/unit/ETF); `is_active` documentado como sem fonte confiável no payload |
| — | Tabelas de portfólio sem RLS | CONFIRMADO p/ 010/011 (REFUTADO p/ portfolios clássicas, que têm RLS) | `018_rls_portfolio_models.sql` + DDL de runtime: RLS nas 3 tabelas + índice único de 1 modelo ativo/usuário |
| — | Fallback silencioso ao legado | CONFIRMADO (+ inconsistência extra: `market_active()` seguia True no fallback por exceção) | Flag de degradação: fallback por erro reativa os reparos defensivos |
| — | Números de banco (700/430 tickers, 34,1% completos, 319 MB etc.) | NÃO VERIFICÁVEL no repositório (estado de runtime) | Critério de qualidade no gate torna o cenário descrito impossível de liberar cutover |

## Correções da auditoria interna (Claude, itens 1–2 da conversa)

- Backtest sem fallback para scores de HOJE (era look-ahead): série começa no
  1º ano com score histórico; anos pulados exibidos na UI (`anos_sem_score`).
- Calibração γ/cap/soft: UI passou a usar `_calibrate_walk_forward` (purged
  k-fold, López de Prado) COM custos — a versão in-sample saiu da UI.
- Captions honestas: custos descontados são SÓ de compra; IR de venda não é
  modelado (não há vendas). A caption antiga afirmava descontar IR 15%.
- Aviso quantificado de viés de sobrevivência no resultado do backtest
  (cobertura + bps estimados, Brown et al. 1992) — bug do timedelta zero em
  `flag_survivorship_universe` corrigido (reportava viés 0 sempre).
- `core/markowitz.py`: Ledoit-Wolf real (intensidade fechada LW 2004, decresce
  com T) + solver exato SLSQP (scipy adicionado ao requirements.txt);
  projeção heurística agora se declara `converged=False`.
- Black-Litterman: `periods_per_year=12` (retornos mensais; vol exibida estava
  ~4,6× inflada) e prior rotulado como uniforme (reverse optimization CAPM
  segue pendente e agora declarada como tal na UI).

## Pendências conhecidas (não resolvidas nesta rodada)

1. **Point-in-time completo no banco**: `period_end_date`, `published_at`/
   `available_at`, versão de publicação (restatements) e `raw_payload_id` nos
   dados normalizados — exige mudança de schema + re-ingestão.
2. **Rebalance com vendas reais** (+ IR 15% e custo de venda) como modo
   opcional do backtest.
3. **Benchmark IBOV real** no backtest (hoje o benchmark é equal-weight do
   próprio universo filtrado — comparação interna, não "bater o mercado").
4. **Validação preditiva do score** (rank-IC score × retorno futuro) — não
   existe teste de poder preditivo no repositório.
5. **Split do `views/empresas_b3.py`** (~5.100 linhas) em view / scoring /
   backtest / providers, conforme CLAUDE.md.
6. **Taxonomia setorial** ausente em parte do universo market e vínculo
   asset→company incompleto (backfill de `company_id`).
