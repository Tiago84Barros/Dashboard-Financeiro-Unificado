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

## Rodada 2 (2026-07-04, score v2.22.0) — pendências 1-4 atacadas

1. **Point-in-time no banco — PARCIALMENTE RESOLVIDO.** Migração
   `019_point_in_time.sql`: `period_end_date`, `first_seen_at` (proxy de
   available_at — quando a linha entrou no NOSSO banco; nunca sobrescrito em
   re-ingestão) e `raw_payload_id` (proveniência) nas 3 tabelas de
   demonstrações; ETL preenche tudo e tolera banco sem a migração (colunas
   ausentes são omitidas do INSERT). AINDA PENDENTE: `published_at` real da
   CVM e versionamento de republicações (restatements) — exige fonte CVM.
2. **Rebalance com vendas — RESOLVIDO.** Modo opcional no backtest da
   Análise Avançada: `_executar_rebalance_vendas` vende excedentes (custo de
   venda + IR 15% sobre lucro realizado, isenção PF R$ 20k/mês) e recompra
   déficits; custo médio rastreado; IR/custos exibidos na UI.
3. **Benchmark IBOV real — RESOLVIDO.** Linha "IBOV (aportes)" no backtest:
   mesmo fluxo de aportes aplicado ao ^BVSP.
4. **Validação preditiva do score — RESOLVIDO.** `_rank_ic_por_ano` (rank-IC
   Spearman por ano, score lag=1 × retorno do ano, spread top−bottom tercil)
   com expander próprio e interpretação honesta (limiares Grinold & Kahn;
   aviso de que universo de sobreviventes superestima o IC).
   Extra da rodada: **rebalance anual movido de janeiro para ABRIL**
   (`_REBAL_MONTH=4`) no backtest da Avançada E no da Criação de Portfólio —
   balanços FY N−1 são publicados até 31/03; janeiro antecipava 2-4 meses de
   informação contábil (apontado pelas duas auditorias).

### Revisão adversarial da rodada 2 (achados incorporados)

A própria rodada 2 passou por revisão multi-agente; achados confirmados e
corrigidos antes do merge:
- **IR do modo com vendas**: `custo_venda` aplicava a isenção de R$ 20k
  pro-rata e por operação; a IN RFB 1.585/2015 é BINÁRIA (vendas do mês >
  20k ⇒ todo o ganho líquido tributável) com compensação de prejuízos no
  mês. Agora o IR é apurado no nível do mês em `_executar_rebalance_vendas`.
  Viés documentado na UI: a série ajustada embute dividendos (isentos) na
  base ⇒ IR simulado tende a SUPERestimar (conservador).
- **Caixa destruído**: rebalance sem nenhum destino com preço no mês agora
  aborta as vendas (antes o caixa sumia da série).
- **Dividendo reinvestido** entra na base de custo do IR (custo médio).
- **Linha IBOV** paga a mesma fricção de compra quando custos estão ativos.
- **Rank-IC**: janela pós-publicação (fim de março/N → fim de março/N+1),
  ano parcial excluído, guarda de IC NaN, metadados do cálculo exibidos.
- **ETL**: `renormalize` agora preserva/corrige `raw_payload_id` (antes o
  backfill zerava a proveniência via ON CONFLICT); cache de colunas não é
  mais envenenado por falha transitória e é resetado a cada run.

## Auditoria da Seleção de FII (2026-07-05, score FII 3.0.0 → 3.1.0)

Auditoria própria (3 agentes de mapeamento + crítica/verificação manual —
agentes de crítica caíram por limite de sessão). Veredito: seção mais
honesta e mais bem desenhada que a Empresas B3 pré-auditoria (exclusão de
dado incompleto, P/VP por proximidade de alvo, captions verdadeiras sobre
in-sample/sobrevivência), mas com defeitos de INSUMO. Corrigidos:

1. **DY ex-amortizações** — `dy_12m` somava amortização de capital como
   renda (peso 45% do score); agora exclui por label ("amort"), item sem
   label conta como rendimento.
2. **P/VP efetivo** — score/exibição usavam `priceToBook` da brapi enquanto
   a aba Busca usava VPA CVM; novo `pvp_efetivo` (preço ÷ VPA CVM quando
   disponível) aplicado na view, no Dashboard e no ETL (ingest/reprocess).
3. **Snapshot mensal point-in-time do score** — migração
   `020_fii_score_snapshot.sql` estende `fii_metrics_monthly` com
   score/inputs; ETL grava a cada run com ranking (guarda p/ banco sem a
   migração); habilita rank-IC de FIIs no futuro.
4. **Liquidez em janela de 6 meses** — era mediana de 5 ANOS (fundo com
   liquidez seca passava o gate de R$ 200k/dia por anos).
5. **RLS no DDL de runtime** de `fii_portfolio_models`/`_items` (espelha a
   018) + **defasagem visível** (informe CVM mm/aaaa e data de coleta da
   vacância na aba Carteira) + **fallback do Dashboard avisa** quando exibe
   carteira recalculada em vez da salva.

Registrado (não corrigido nesta rodada): método "qualidade retrospectiva"
segue selecionando por CAGR/drawdown de sobreviventes (declarado na UI);
vacância segue via scraping fora do cron; `vacancia_ref_date` segue sendo
data de coleta; backtest FII segue buy-and-hold in-sample (declarado).

## Pendências remanescentes

5. **Split do `views/empresas_b3.py`** (~5.300 linhas) em view / scoring /
   backtest / providers, conforme CLAUDE.md (recomendado fazer com modelo
   padrão — refatoração mecânica coberta por testes).
6. **Taxonomia setorial** ausente em parte do universo market e vínculo
   asset→company incompleto (backfill de `company_id`).
7. **published_at real (CVM) + vintage/restatements** (complemento do item 1).
