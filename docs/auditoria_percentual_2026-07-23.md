# Auditoria Percentual do Sistema — 23/07/2026

Auditoria executada sobre o worktree `peaceful-meitner-a62577` (branch `claude/adoring-kowalevski-8504ec`, base `3cabacf`), com acesso de leitura ao **armazém local Postgres** (`dfu_warehouse`, Postgres 17 + pgvector, porta 5433) e execução completa da suíte de testes.

**Escopo efetivamente auditado**: código-fonte (≈80.197 linhas Python em `core/`, `views/`, `data_pipeline/`, `etl/`, `scripts/`), banco de dados local (schemas `market`, `market_us`, `public`), metodologias de seleção B3/FIIs/EUA, modelos de carteira, camada LLM e arquitetura.

**Não auditado nesta rodada** (evidência insuficiente / sem acesso):
- Banco Supabase de produção (finanças pessoais, carteiras salvas, `macro_indicators` — vazio no espelho local);
- Respostas reais da LLM em lote (não há harness de avaliação automática; exigiria chaves de API e execução ao vivo);
- Execução real dos workflows GitHub Actions (5 workflows de dados; nenhum roda `pytest`);
- Ingestão EUA ao vivo (SEC EDGAR/yfinance) — auditada apenas pelo resultado armazenado.

---

## 0. Evidências primárias (dados brutos preservados)

### 0.1 Suíte de testes
- **776 testes coletados; 772 aprovados (99,5%); 4 falhas** em 79,6s.
- As 4 falhas foram re-executadas isoladamente e classificadas:
  - `test_market_read.py::test_financeiro_sempre_market_para_qualquer_flag` e `::test_financeiro_market_erro_retorna_vazio_nao_legado` — **passam isolados**: poluição de cache Streamlit entre testes (flake de isolamento), não é regressão de produção. O código da fachada (`core/b3_data.py::_financeiro`) está correto: erro no `market.*` devolve vazio, nunca o legado.
  - `test_backup_remote_snapshots.py::test_snapshot_backup_is_ignored_by_git` — o diretório `migration/backup` não existe neste worktree (é gitignored); artefato de ambiente.
  - `test_market_companies.py::test_view_americana_card_analisar_seleciona_ticker_e_muda_aba` — timeout de 20s do AppTest nesta máquina (caminho OneDrive, I/O lento); artefato de ambiente.

### 0.2 Banco de dados (consultas executadas em 23/07/2026)

| Tabela | Registros | Achados |
|---|---:|---|
| `market.calculated_metrics` | 65.213 (818 tickers) | 0 nulos em `metric_value`; 0 duplicatas na chave (ticker, métrica, ano, tri, período); atualizado 23/07/2026 |
| `market.historical_prices` | 138.241 (1.087 tickers, 2000→22/07/2026) | **1.526 `close` nulos + 1 zero (1,1%)**; 729 desde 2024 |
| `market.dividends` | 39.132 (798 tickers, ex-date até 03/11/2026) | **4 grupos duplicados exatos** (TRNT11, CBOP11…, ~8 linhas, 0,02%); **18 registros com `amount` ≤ 0** |
| `market.income/balance/cash_flow` (BR) | 27.616 / 27.840 / 27.003 | 2010–2026, 423 tickers |
| `market.companies` | 390 | 27 sem setor (93,1% com setor) |
| `market.fiis` (cadastro) | 1.066 (430 com preço) | Entre os 430 com preço: 92 sem segmento (21,4%), **294 sem vacância (68,4%)** — parte compensada por métricas extraídas de informes CVM no pipeline PIT |
| `market.fii_score_snapshots` (últ. corte 23/07) | 1.146 | **756 `validated` (66%) / 390 `diligence_only`** — gate de publicação PIT operante |
| `market.fii_universe_history` | 4.808 (1.030 tickers, até 16/07) | Controle de sobrevivência ativo |
| `market.ticker_alias` | 15 remapeamentos | Mitigação do defeito conhecido da brapi |
| `market_us.company_snapshots` | 2.989 (2.830 ativos) | **100% com score, `score_confidence` e `score_status`** (drift da vitrine resolvido); gerado 23/07/2026; 9 ativos com último exercício < 2024 |
| `market_us.prices_monthly` | 609.347 (2.817 símbolos, até 17/07/2026) | ok |
| `market_us.score_vintages` | 56.064 (2.800 símbolos) | PIT operante |
| `market_us.income/balance/cash_flow` | 133.367 / 135.694 / 107.328 | **26 linhas com `fiscal_year` inválido em formato serial-Excel (ex.: 43465 = 2018)**, símbolos PRTH e TNET — bug de parser SEC |
| `market_us.dividends` | 0 | Dividendos por evento não armazenados; análise usa linhas do fluxo de caixa |
| `market.macro_indicators` | 0 (local) | Macro vive só no Supabase — **não auditado** |

Infra de rastreabilidade confirmada: `brapi_raw_payloads` (22.843), `calculated_metric_vintages` (4.250), `fii_pit_score_snapshots` (20.155), `fii_parser_versions`, `*_archive_loads` com hash de arquivo/parser, `data_quality_logs`, `fii_quality_runs`.

---

## 1. Regras de cálculo usadas nesta auditoria

- Cada nota composta apresenta pesos que somam 100%.
- Qualidade, confiança da avaliação e cobertura da auditoria são reportadas separadamente.
- Itens sem evidência recebem **Não auditado / Evidência insuficiente**, nunca 0%.
- Erro crítico limita a classificação do componente (nenhum erro classificado como crítico-bloqueante foi encontrado nos módulos de seleção; os defeitos de dados achados são pontuais e quantificados).
- Qualidade projetada é **estimativa**, com premissas explícitas (§12.13).

---

## 2. Módulo Empresas Brasileiras

### 2.1 Dados (BR) — **88,4%**

| Critério | Peso | Nota | Contribuição | Evidência |
|---|---:|---:|---:|---|
| Completude | 25% | 92% | 23,0% | 0 nulos em métricas; 818 tickers; 423 tickers c/ demonstrações; 27/390 empresas sem setor |
| Atualização | 15% | 98% | 14,7% | métricas e preços atualizados em 22–23/07/2026 |
| Consistência | 20% | 88% | 17,6% | aliases brapi mitigados (15); ecos de dividendos por classe tratados (dedup + scrub); limitação cross-classe documentada |
| Precisão | 20% | 84% | 16,8% | 1.527 closes nulos/zero (1,1%); 18 dividendos amount≤0; 4 grupos duplicados |
| Rastreabilidade | 20% | 96% | 19,2% | raw payloads, vintages PIT, coluna `source`, logs de qualidade |
| **Total** | **100%** | | **88,4%** | |

Confiança da avaliação: 85% · Cobertura: 90% (Supabase produção não consultado). **Muito bom — aprovado com ressalvas.**

### 2.2 Metodologia (BR) — **87,2%**

Evidência: `core/b3_company_score.py` (6 trilhas, winsorização p5–p95, percentil intra-grupo, ausência = 50 neutro + cobertura explícita, múltiplo negativo excluído do valuation); catálogo auditável de 34 técnicas com bibliografia (`core/metodologia.py`); validação estatística no engine de portfólio (`views/portfolio_b3.py`): holdout OOS ≥18 meses, rank-IC anual com t-stat e p-valor, walk-forward multi-regime, publication lag=1, controle de sobrevivência, FDR.

| Sub-item (§12.3) | Nota | Sub-item | Nota |
|---|---:|---|---:|
| Fundamentação financeira | 92% | Tratamento de outliers | 90% |
| Robustez metodológica | 88% | Controle de vieses (look-ahead/survivorship) | 91% |
| Adequação à classe/mercado BR | 87% | Estabilidade do ranking | 78% (bootstrap disponível, não contínuo) |
| Diferenciação setorial | 80% (intra-setor sim; sem override de trilhas p/ bancos como no módulo EUA) | Explicabilidade | 90% (Shapley/XAI) |
| Qualidade das fórmulas | 88% | Coerência dos pesos | **86%** (0,22/0,18/0,15… fixos por convenção — ver correção em §15: peso fixo é robustez sob quebra de regime, não defeito) |
| Filtros eliminatórios | 88% | Utilidade p/ longo prazo | 88% |

Sub-avaliações exigidas: fundamentalista 90% · setores B3 84% · financeiras 74% (EV/EBIT e P/FCO pouco adequados a bancos; mitigado pelo percentil intra-setor, sem exclusão explícita das métricas) · cíclicas 82% (reversão à média + CV) · commodities 80% · endividamento 88% · rentabilidade 92% · crescimento 84% (slopes log) · geração de caixa 86% (P/FCO, cobertura FCO) · dividendos 90% (Bazin + sustentabilidade/payout) · governança 62% (sem métricas explícitas de governança no score BR — diferente dos FIIs) · valuation 88% · aderência macro 85% (ajuste Selic/IPCA) · confiabilidade da seleção final 86%.

Confiança: 88% · Cobertura: 85%. **Muito bom — aprovado com ressalvas.**

### 2.3 Indicadores (BR) — **84,8%**

| Indicador | Fórmula | Dados | Adequação | Relevância | Não redundância | Qualidade geral |
|---|---:|---:|---:|---:|---:|---:|
| ROE | 95% | 92% | 90% | 90% | 70% (usado em 2 trilhas — dupla contagem leve) | 87% |
| ROIC | 95% | 90% | 92% | 92% | 85% | 91% |
| Margens (líq./oper.) | 95% | 92% | 90% | 88% | 80% | 89% |
| P/L, P/VP, EV/EBIT, P/FCO | 92% | 90% | 82% (bancos) | 90% | 80% | 87% |
| DY / Payout | 92% | 88% | 92% | 92% | 85% | 90% |
| Endividamento / Liq. Corrente | 92% | 90% | 84% | 86% | 88% | 88% |
| Slopes log (crescimento) | 88% | 84% | 86% | 82% | 90% | 86% |

Indicadores >80%: todos os listados. Recomendações: reduzir a dupla contagem de ROE (presente em `quality` e `capital_efficiency`); restringir EV/EBIT e P/FCO em Financials (como o módulo EUA já faz via overrides). Nenhum indicador precisa ser removido.

### 2.4 Ranking (BR) — **86,1%** · 2.5 Carteira Modelo B3 — **85,3%** (metodologia; instância salva no Supabase **não auditada**) · 2.6 LLM B3 — **78,9%** · 2.7 Código BR — **80,7%** (núcleo puro e testado; `views/empresas_b3.py` com 5.958 linhas concentra lógica demais na camada de interface)

### 2.8 Consolidado Empresas Brasileiras — **85,8%**

.25·88,4 + .25·87,2 + .15·84,8 + .10·86,1 + .10·85,3 + .05·78,9 + .10·80,7 = **85,8%** — **Muito bom**, aprovado com ressalvas. Confiança 84% · Cobertura 85%.

---

## 3. Módulo FIIs Brasileiros

### 3.1 Dados (FII) — **79,8%**

| Critério | Peso | Nota | Contribuição | Evidência |
|---|---:|---:|---:|---|
| Completude | 30% | 63% | 18,9% | cadastro `market.fiis`: 68,4% sem vacância e 21,4% sem segmento entre os 430 com preço; parcialmente suprido pelas observações PIT de informes CVM (418 MB) — incerteza sobre a fração real coberta |
| Atualização | 15% | 96% | 14,4% | scores e métricas de 23/07/2026 |
| Consistência | 15% | 85% | 12,8% | resolução de entidades (10.867 aliases, 8.530 canônicas); 97 issues abertas em `fii_reconciliation_issues` |
| Precisão | 20% | 82% | 16,4% | duplicatas de dividendos FII no achado global; calibração de parser embutida na confiança |
| Rastreabilidade | 20% | 97% | 19,4% | releases imutáveis, archive_loads com hash, parser_versions, PIT snapshots (20.155) |
| **Total** | **100%** | | **81,9% → ajustado a 79,8%** | ajuste −2,1 p.p. pela incerteza sobre quanto da lacuna cadastral é realmente suprida pelo pipeline PIT (não foi possível cruzar campo a campo) |

Confiança: 70% · Cobertura: 80%. **Bom — dependente de correções (cadastro).**

### 3.2 Metodologia (FII) — **89,1%**

Evidência: `core/fii_methodology.py` v6.0.0 — separação formal entre `score`, `confidence` (média geométrica ponderada: cobertura 40%, frescor 12%, qualidade de fonte 12%, consistência 12%, histórico 9%, calibração de parser 15%) e `publication_status` (diligência até validação PIT aprovada); ausência **nunca** vira zero/neutro; penalização de cobertura no score final (`raw · (0,55 + 0,45·cov)`); gate de publicação com 4 condições.

Sub-avaliações exigidas (§12.3 FIIs): adequação ao mercado BR de FIIs 92% · separação tijolo/papel (+FoF/híbrido) 94% · vacância 88% (física e financeira, críticas) · contratos 84% (WAULT, vencimentos 24m) · locatários 86% (concentração crítica) · imóveis 82% (`fii_imoveis`, qualidade de ativo) · concentração 90% · gestão 82% (eficiência, taxas) · emissões 86% (disciplina de emissão e de preço) · diluição 84% · alavancagem 88% · liquidez 90% (crítica, R$ 1M/dia na carteira) · risco de crédito 88% (rating, inadimplência) · garantias/subordinação 84% · LTV 88% · duration 86% (alvo 3 anos) · indexadores 86% · recorrência de rendimentos 90% (crítica) · rendimentos extraordinários 78% (tratado via recorrência, sem decomposição explícita de não-recorrentes) · sensibilidade à Selic 88% (regimes macro + bandas táticas) · sensibilidade à inflação 84% · aderência CVM 90% (informes mensal/trimestral/eventuais como fontes obrigatórias) · confiabilidade da seleção final 85%.

Confiança: 88% · Cobertura: 85%. **Muito bom.**

### 3.3 Indicadores (FII) — **84,3%** · 3.4 Ranking (FII) — **85,7%** · 3.5 Carteira FII — **84,9%** (MILP com tetos por gestor/setor/inquilino/devedor/emissor/indexador/região; dimensões sem cobertura ≥80% viram bloqueio reportado, nunca concentração presumida zero; ressalva: `max_weighted_uncertainty` relaxado 0,30→0,35, documentado) · 3.6 LLM FII — **77,5%** · 3.7 Código FII — **86,2%** (o subsistema mais testado do repositório: ~30 arquivos `test_fii_*`)

### 3.8 Consolidado FIIs — **84,2%**

.25·79,8 + .25·89,1 + .15·84,3 + .10·85,7 + .10·84,9 + .05·77,5 + .10·86,2 = **84,2%** — **Muito bom**, aprovado com ressalvas (publicação segue corretamente gateada: 66% validated no último corte). Confiança 80% · Cobertura 82%.

---

## 4. Módulo Ações Americanas

### 4.1 Dados (EUA) — **89,4%**

| Critério | Peso | Nota | Contribuição | Evidência |
|---|---:|---:|---:|---|
| Completude | 25% | 88% | 22,0% | 2.830 ativos 100% com score/confiança/status; dividendos por evento ausentes (tabela vazia; derivados do CF) |
| Atualização | 15% | 92% | 13,8% | snapshots 23/07; preços 17/07; 9 ativos com exercício <2024 |
| Consistência | 20% | 90% | 18,0% | schema dedicado `market_us`, vintages PIT |
| Precisão | 20% | 88% | 17,6% | 26 linhas com `fiscal_year` serial-Excel (PRTH/TNET) — 0,02%, mas indica bug de parser |
| Rastreabilidade | 20% | 90% | 18,0% | `score_vintages`, `generated_at/published_at`, `ingestion_errors` |
| **Total** | **100%** | | **89,4%** | |

**Muito bom.** Confiança: 85% · Cobertura: 88%.

### 4.2 Metodologia (EUA) — **84,6%**

Evidência: `core/us_score.py` — percentil intra-indústria com fallback a setor/universo sinalizado; overrides setoriais para Real Estate e Financial Services; encolhimento da nota ao neutro pela raiz da cobertura; `score_confidence` + status decision/research/screen-grade; penalidade de confiança 0,85 para setores atendidos por proxies.

Sub-avaliações exigidas (§12.3 EUA): adequação ao mercado norte-americano 86% · crescimento 86% (CAGRs 3/5 anos de receita, EBIT, EPS, FCF) · rentabilidade 90% · fluxo de caixa livre 90% (fcf_margin, cash_conversion, fcf_yield) · endividamento 88% · valuation 86% · **stock-based compensation 55%** (sem métrica explícita de SBC; capturada só indiretamente via cash conversion e emissões) · diluição 72% (emissões líquidas no shareholder yield; sem série de share count como fator) · recompras 88% · dados GAAP 90% (SEC EDGAR) · dados non-GAAP 60% (não tratados — decisão defensável, mas sem reconciliação) · diferenciação setorial 88% · tecnologia 82% · bancos/seguradoras 78% (override + penalidade de confiança; proxies ainda genéricas, autodeclarado no código) · cíclicas 78% · qualidade dos lucros 80% (cash conversion) · alocação de capital 82% · vantagens competitivas 68% (sem métrica explícita de moat; margens como proxy) · confiabilidade da seleção final 84%.

Confiança: 85% · Cobertura: 82%. **Muito bom com ressalvas (SBC).**

### 4.3 Indicadores (EUA) — **82,9%** (trilhas `capital_efficiency` e `shareholder` com 1 métrica cada — fragilidade de robustez) · 4.4 Ranking — **84,1%** (vintages PIT, backtest de outliers) · 4.5 Carteira EUA — **79,6%** (gates de histórico mínimo; menos maduro que o B3; instância real não auditada) · 4.6 LLM/dossiê — **76,8%** · 4.7 Código — **84,3%**

### 4.8 Consolidado Ações Americanas — **84,5%**

.25·89,4 + .25·84,6 + .15·82,9 + .10·84,1 + .10·79,6 + .05·76,8 + .10·84,3 = **84,5%** — **Muito bom**, aprovado com ressalvas. Confiança 82% · Cobertura 80%.

---

## 5. Banco de Dados (transversal) — **86,3%**

| Base | Completude | Atualização | Consistência | Precisão | Rastreabilidade | Qualidade geral |
|---|---:|---:|---:|---:|---:|---:|
| Preços BR (`historical_prices`) | 95% | 98% | 92% | 84% | 92% | 91,4% |
| Demonstrações BR | 90% | 95% | 90% | 92% | 92% | 91,3% |
| Dividendos BR/FII | 92% | 96% | 86% | 90% | 90% | 90,6% |
| Indicadores calculados BR | 97% | 98% | 95% | 94% | 96% | 95,9% |
| Cadastro/setores BR | 90% | 92% | 90% | 92% | 88% | 90,3% |
| Cadastro FII (`market.fiis`) | 63% | 92% | 85% | 84% | 90% | 79,3% |
| PIT FII (observações/snapshots) | 90% | 96% | 90% | 88% | 98% | 91,8% |
| Preços EUA (`prices_monthly`) | 94% | 92% | 92% | 94% | 90% | 92,7% |
| Demonstrações EUA | 90% | 90% | 90% | 88% | 90% | 89,6% |
| Vitrine EUA (`company_snapshots`) | 96% | 96% | 94% | 92% | 94% | 94,5% |
| Macro (`macro_indicators`) | — | — | — | — | — | **Não auditado** (0 linhas no espelho local) |
| Finanças pessoais (`public.*`) | — | — | — | — | — | **Não auditado** (vivem só no Supabase) |

Números absolutos globais: **≈1,47 milhão de registros consultados nas tabelas centrais; ≈1.585 problemáticos identificados (1.527 closes + 26 fiscal_year + 18 dividendos inválidos + 8 duplicados + 27 setores ausentes ≈ 0,11%)** — fora a lacuna cadastral FII, que é o maior passivo de completude.

Confiança: 85% · Cobertura: 75% (produção Supabase fora do alcance). **Muito bom — aprovado com ressalvas.**

---

## 6. Sistemas de Pontuação e Rankings (§12.6) — **85,4%**

Fundamentação dos pesos 80% · coerência 86% · estabilidade 78% · sensibilidade do ranking 80% · outliers 92% (winsorização universal) · normalização 92% (percentil) · eliminatórios 88% · desempates 84% (rank médio em empates) · dupla contagem 76% (ROE 2×; DY no score e na utilidade da carteira FII) · sobreposição de fatores 80% · robustez estatística 88% (OOS, rank-IC, walk-forward, FDR) · reprodutibilidade 92% (versões de fórmula/metodologia persistidas, PIT) · explicabilidade 90% (Shapley, componentes por trilha) · resistência a perturbações 76% (bootstrap existe, não é gate contínuo).

Por sistema: B3 86,1% (confiança 85%) · FII 85,7% (confiança 82%) · EUA 84,1% (confiança 80%). Principal fragilidade: dupla contagem leve (ROE em duas trilhas). Alteração recomendada: teste de estabilidade bootstrap como gate — mede a fragilidade do ranking a perturbações nos dados e rejeita ranking frágil. Qualidade estimada após correção: ~88% (**estimativa**).

> **Recomendação RETIRADA (25/07/2026)** — a versão original desta seção também
> recomendava "tornar rotina a calibração empírica periódica dos pesos
> (Fama-MacBeth)". Isso foi retirado: ver §15. Recalibrar pesos a cada período
> importa uma premissa de estacionariedade que o mercado brasileiro não
> satisfaz, e faria o ranking perseguir o regime macro.

---

## 7. Carteiras Modelo (§12.7) — consolidado **83,3%**

Metodologia de construção auditada em código; **as instâncias salvas (Supabase) não foram auditadas** — cobertura desta seção: ~60%.

| Dimensão | B3 | FII | EUA |
|---|---:|---:|---:|
| Qualidade média dos ativos (mecanismo) | 88% | 86% | 84% |
| Diversificação real/setorial | 86% | 90% | 78% |
| Controle de concentração | 84% (teto suave por ativo) | 92% (7 dimensões com teto) | 76% |
| Liquidez | 84% | 90% (piso R$ 1M/dia) | 82% |
| Renda / crescimento / valuation | 86% / 82% / 86% | 90% / 78% / 84% | 78% / 86% / 84% |
| Risco × retorno / resiliência | 86% (Markowitz, LW, DCC-GARCH, stress) | 86% (cenários + CVaR) | 78% |
| Exposição a juros / inflação / câmbio | 84% / 82% / n.a. | 90% / 84% / n.a. | 70% (câmbio não modelado na visão consolidada) |
| Correlação entre ativos | 88% | 78% (mín. 12 meses) | 74% |
| Drawdown | 84% | 84% (max_drawdown crítico) | 76% |
| Coerência dos pesos | 86% | 88% | 78% |
| Custos de rebalanceamento | 84% (custos PF-BR) | 74% | 68% |
| Estabilidade / longo prazo | 80% / 88% | 80% / 90% | 74% / 84% |
| **Confiabilidade geral** | **85,3%** | **84,9%** | **79,6%** |

FII adicional: segmento 90% · gestor 90% (teto 25%) · imóvel 76% · locatário 88% · devedor 90% · indexador 88% · geográfica 86% · tijolo×papel 92% (bandas por regime) · duration 86% · crédito 88% · sustentabilidade dos rendimentos 88%.

Aptidão prática: **B3 e FII aprovados com ressalvas** (FII permanece "lista de diligência" até o gate liberar — comportamento correto); **EUA dependente de correções** antes de uso pleno. Correções obrigatórias antes do uso: nenhuma bloqueante para B3; FII: completar cobertura cadastral; EUA: SBC/diluição explícitos e custos.

---

## 8. Análises por LLM (§12.8) — **78,1%** (confiança da avaliação: 55%)

Design auditado em código; **saídas reais não testadas em lote** (sem harness/golden set — os percentuais de respostas corretas/erradas/inventadas são **Não auditados — evidência insuficiente**).

Fidelidade aos dados (por design) 88% (regras "use apenas o CONTEXTO", aviso de mock/fallback, separação FATO/ESTIMATIVA/PROJEÇÃO) · atualização do contexto 85% · consistência numérica 80% (exige mostrar cálculos; sem verificação programática pós-resposta) · interpretação de indicadores 82% · diferenciação classe/setor 84% (contextos dedicados B3/FII/financeiro/cartão) · identificação de riscos 82% · limitações explícitas 88% · rastreabilidade 76% (RAG com embeddings pgvector para docs CVM; citações não obrigatórias) · clareza 86% · prevenção de alucinação 80% (estrutural: gráficos só por diretivas JSON validadas e desenhadas com dados reais — a LLM nunca executa código) · prevenção de recomendação categórica 88% (disclaimers e papel de "apoio à decisão") · qualidade dos prompts 88% · qualidade do contexto 86% (montado só com dados do OWNER_USER_ID) · validação da saída 55% (parse JSON com fallback; sem verificação semântica) · confiabilidade geral 78%.

Testes existentes: `test_llm_provider_fallback`, `test_llm_report_context`, `test_llm_fii`, `test_apb3_*` — estruturais (contexto/protocolo), não avaliam qualidade da resposta.

> **Correção aplicada (24/07/2026)** — a lacuna de validação de saída foi fechada:
> - `core/llm_grounding.py`: verificador determinístico e offline de **ancoragem numérica** — todo número citado pela LLM precisa existir no contexto enviado ou ser derivável dele (soma, diferença, variação). Conservador por construção: percentuais só casam com percentuais (um defeito encontrado durante a implementação — a variação de 780,95% ancorava indevidamente "R$ 780,00" — está travado por teste de regressão), derivações exigem tolerância apertada (0,3%), e anos/contagens não contam como afirmação factual.
> - **Na interface**: os chats de Finanças e de Cartão passam a exibir aviso quando a resposta cita valor sem lastro nos dados enviados. A verificação nunca derruba o chat (falha silenciosa por design).
> - `scripts/eval_llm.py`: harness de **golden set** com contexto sintético (nenhum dado real do usuário) que mede exatamente os percentuais que faltavam — corretas, parcialmente corretas, com dados inventados, fora do formato — mais aderência ao protocolo de gráficos, honestidade sobre dado ausente e presença de ressalva. Requer chave de API; executado pelo usuário.
> - 23 testes offline cobrem o verificador e o avaliador.
>
> Reavaliação: validação da saída **55% → 82%**; prevenção de alucinação 80% → 88%; consistência numérica 80% → 88%. **LLM consolidado: 78,1% → 83,4%** (confiança da avaliação sobe para 72%; o percentual de respostas corretas segue *não medido* até o usuário rodar o harness com chave).

---

## 9. Arquitetura e Qualidade do Código (§12.9) — **83,7%**

Organização 88% (core/views/data_pipeline/etl/scripts/docs conforme CLAUDE.md) · modularização 82% (núcleos puros exemplares; **`views/empresas_b3.py` 5.958 linhas e `views/portfolio_b3.py` 3.071 linhas concentram lógica de negócio na camada de interface**, incluindo a validação estatística do portfólio B3) · legibilidade 88% · documentação 92% (docs/ com 40+ documentos, metodologias versionadas, dicionário de dados) · tratamento de erros 78% (209 `except Exception` amplos em core) · segurança 84% (RLS nas tabelas de carteira, sem credenciais no repo, `check_secrets.py`, isolamento OWNER_USER_ID) · validação de entradas 82% · testes automatizados 86% (117 arquivos, 756 funções, 99,5% verdes) · **cobertura de testes: não medida; estimativa ~55–65% do core, ~45% do repositório** (funções críticas de metodologia: ~85% testadas; fórmulas com testes unitários: ~80%; pipelines validados: ~70%; exceções tratadas: ~75%; regras de negócio documentadas: ~85%) · rastreabilidade dos cálculos 92% · separação UI/negócio 74% · separação entre metodologias de classes 90% (B3/FII/US totalmente apartados) · manutenção 82% · escalabilidade 80% · observabilidade 74% (logging presente; sem métricas/alertas de execução) · **controle de versões 90%, porém nenhum workflow de CI executa `pytest`** — os 5 workflows são só de dados.

> **Correção aplicada (24/07/2026)** — `.github/workflows/tests.yml` executa a suíte
> em cada PR e push na main (Python 3.11 e 3.12, sem banco e sem chaves). Antes de
> declarar o CI verde, a suíte foi validada em **ambiente limpo** (venv só com
> `requirements.txt` + pytest), o que revelou dois problemas que teriam deixado o
> CI vermelho e foram corrigidos na origem:
> 1. `tests/test_b3_company_score.py` substituía atributos de `core.b3_data` em
>    escopo de módulo via AppTest e **vazava para `tests/test_market_read.py`** —
>    era a causa real dos 2 "flakes de cache" relatados na §0.1; agora há fixture
>    de restauração;
> 2. `test_snapshot_backup_is_ignored_by_git` exigia que um diretório
>    **gitignorado existisse** (fato da máquina local); passou a verificar o
>    invariante real, a regra no `.gitignore`;
> 3. testes de AppTest com timeout de 20–40s reprovavam em runner lento —
>    elevados a 60s (o assert é o comportamento renderizado, não o tempo).
>
> Resultado: **821 testes, 100% verdes em ambiente limpo**. Reavaliação: testes
> automatizados 86% → 92%; controle de versões 90% → 96%; observabilidade
> mantida em 74%. **Código consolidado: 83,7% → 86,1%.**

---

## 10. Percentual geral por módulo (§12.10)

Pesos de referência do protocolo (dados 25 / metodologia 25 / indicadores 15 / ranking 10 / carteira 10 / LLM 5 / código 10):

| Módulo | Dados | Metodologia | Indicadores | Ranking | Carteira | LLM | Código | **Qualidade geral** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Empresas brasileiras | 88,4% | 87,2% | 84,8% | 86,1% | 85,3% | 78,9% | 80,7% | **85,8%** |
| FIIs brasileiros | 79,8% | 89,1% | 84,3% | 85,7% | 84,9% | 77,5% | 86,2% | **84,2%** |
| Ações americanas | 89,4% | 84,6% | 82,9% | 84,1% | 79,6% | 76,8% | 84,3% | **84,5%** |

## 11. Percentual global do sistema (§12.11)

Pesos por módulo transversal (justificativa: BR é o módulo mais usado e maduro; FII tem o maior volume de infra própria; banco é fundação de tudo): BR 25% · FII 20% · EUA 15% · Banco de dados 15% · Carteiras 8% · Pontuação 7% · LLM 3% · Código 7% = 100%.

**Global = .25·85,8 + .20·84,2 + .15·84,5 + .15·86,3 + .08·83,3 + .07·85,4 + .03·78,1 + .07·83,7 = 84,8%**

1. **Percentual global atual: 84,8% — Muito bom (robusto com pequenas ressalvas)**
2. Confiança da avaliação: **78%**
3. Cobertura da auditoria: **~72%** (código e banco local a fundo; produção, LLM ao vivo e workflows não)
4. Redutores principais: cadastro FII incompleto; SBC/diluição EUA; ausência de CI de testes; ausência de harness LLM; views gigantes; closes nulos
5. Melhores elementos: indicadores calculados BR (95,9%); vitrine EUA (94,5%); metodologia FII v6 (89,1%); rastreabilidade PIT (92%+)
6. Correções indispensáveis: ver §13
7. **Global projetado pós-correções prioritárias: ~88,6% (estimativa técnica)** — premissas: itens 1–5 do §13 implementados e re-testados; sem regressões; lacuna cadastral FII reduzida à metade

## 12. Painel final (ordenado por criticidade → menor nota → impacto na seleção)

| Elemento auditado | Qualidade | Confiança | Cobertura | Classificação | Situação |
|---|---:|---:|---:|---|---|
| ⚠ Validação de saída LLM | 55% | 55% | 40% | Fraco | dependente de correções |
| ⚠ SBC (EUA) | 55% | 80% | 80% | Fraco | dependente de correções |
| ⚠ Non-GAAP (EUA) | 60% | 75% | 70% | Regular | aprovado c/ ressalvas (decisão de escopo) |
| ⚠ Governança no score BR | 62% | 80% | 80% | Regular | dependente de correções |
| ⚠ Cadastro FII (completude) | 63% | 70% | 80% | Regular | dependente de correções |
| ⚠ Moat explícito (EUA) | 68% | 75% | 75% | Regular | aprovado c/ ressalvas |
| Financeiras no score BR | 74% | 80% | 80% | Bom | aprovado c/ ressalvas |
| Separação UI/negócio | 74% | 90% | 95% | Bom | dependente de correções |
| Observabilidade | 74% | 80% | 85% | Bom | aprovado c/ ressalvas |
| Diluição (EUA) | 72% | 80% | 80% | Bom | aprovado c/ ressalvas |
| Estabilidade do ranking (gate) | 78% | 80% | 75% | Bom | aprovado c/ ressalvas |
| LLM (consolidado) | 78,1% | 55% | 50% | Bom | aprovado c/ ressalvas |
| Dados FII | 79,8% | 70% | 80% | Bom | aprovado c/ ressalvas |
| Carteira EUA | 79,6% | 75% | 60% | Bom | dependente de correções |
| Código BR (views) | 80,7% | 90% | 90% | Muito bom* | aprovado c/ ressalvas |
| Carteiras Modelo (consol.) | 83,3% | 75% | 60% | Muito bom | aprovado c/ ressalvas |
| Código/arquitetura | 83,7% | 88% | 90% | Muito bom | aprovado c/ ressalvas |
| FIIs (módulo) | 84,2% | 80% | 82% | Muito bom | aprovado c/ ressalvas |
| Ações americanas (módulo) | 84,5% | 82% | 80% | Muito bom | aprovado c/ ressalvas |
| Pontuação/rankings | 85,4% | 82% | 80% | Muito bom | aprovado c/ ressalvas |
| Empresas BR (módulo) | 85,8% | 84% | 85% | Muito bom | aprovado c/ ressalvas |
| Banco de dados | 86,3% | 85% | 75% | Muito bom | aprovado c/ ressalvas |
| Metodologia FII v6 | 89,1% | 88% | 85% | Muito bom | aprovado |
| Dados EUA | 89,4% | 85% | 88% | Muito bom | aprovado |
| Vitrine EUA | 94,5% | 90% | 95% | Excelente | aprovado |
| Indicadores calculados BR | 95,9% | 90% | 95% | Excelente | aprovado |
| Macro / finanças pessoais / LLM ao vivo / workflows CI | — | — | 0% | **Não auditado** | evidência insuficiente |

\* nota individual acima de 80 refere-se ao conjunto; o problema é concentração de lógica nas views, não defeito funcional.

## 13. Qualidade atual × projetada (§12.13)

| Elemento | Atual | Correção recomendada | Projetada | Ganho estimado |
|---|---:|---|---:|---:|
| Parser SEC (fiscal_year serial) | 88% precisão EUA | converter serial-Excel→ano em PRTH/TNET e revalidar ingestão | 95% | +7 p.p. |
| Preços BR (closes nulos) | 84% precisão | investigar/limpar 1.527 linhas com `close` nulo (729 pós-2024) | 93% | +9 p.p. |
| Dividendos | 90% | remover 4 duplicatas exatas e 18 amount≤0 (constraint + limpeza) | 96% | +6 p.p. |
| CI de testes | 0% (inexistente) | workflow GitHub rodando `pytest` em PR | 90% | +90 p.p. |
| Harness LLM | 55% validação | golden set de perguntas/respostas com verificação numérica programática | 80% | +25 p.p. |
| Cadastro FII | 63% completude | preencher segmento/vacância ou documentar a fonte PIT que os supre | 82% | +19 p.p. |
| SBC/diluição EUA | 55%/72% | métrica explícita de SBC/receita e crescimento de share count como fator | 82% | +27/+10 p.p. |
| Views gigantes | 74% separação | extrair validação estatística de `views/portfolio_b3.py` para `core/` | 85% | +11 p.p. |
| Flakes de teste | 99,5% verde | isolar cache Streamlit entre testes (fixture de limpeza) | 100% | +0,5 p.p. |

Todos os valores "projetada" são **estimativas técnicas**, condicionadas a implementação + re-teste.

---

## 14. Fechamento do ciclo — reavaliação de 24/07/2026

Todas as correções da §13 foram implementadas e verificadas, exceto a extração
das views gigantes (mantida como próximo passo). Esta seção reavalia os
percentuais **com base em evidência de execução**, não em projeção.

### 14.1 O que foi executado

| Correção | Evidência | Situação |
|---|---|---|
| Parser SEC (`fiscal_year` serial-Excel) | `_sane_fiscal_year` + 2 testes; 26 linhas corrigidas/removidas; CHECK criado | ✅ verificado nos dois bancos (0 fora de faixa) |
| Preços BR (candles vazios) | guarda em `price_rows` + `fii_pit` + CHECK; 1.527 linhas locais e 1.509 no Supabase removidas com backup | ✅ 0 restantes |
| Dividendos inválidos/duplicados | limpeza + CHECK `amount > 0`; sync seletivo removeu 17,5 mil ecos de classe no Supabase | ✅ 0 restantes; bases alinhadas |
| Cadastro FII | `enrich_cadastro_gaps()` + comando `fiis-cadastro-gaps`: 727 segmentos e 312 vacâncias | ✅ 0 sem segmento; lacuna real de vacância = 62 fundos (o resto é papel/FoF, onde não se aplica) |
| CI de testes | `.github/workflows/tests.yml` (3.11 e 3.12); suíte validada em venv limpo | ✅ **821 testes, 100% verdes** |
| SBC e diluição (EUA) | `sbc_to_revenue`, `fcf_ex_sbc_margin`, `share_count_cagr_3y`; score v0.5.0; 9 testes; exposto na UI | ✅ dado já existia (SBC em 89% das linhas anuais) |
| Validação de saída da LLM | `core/llm_grounding.py` + aviso nos chats + `scripts/eval_llm.py`; 23 testes | ✅ percentuais de acerto ainda dependem de execução com chave |
| Flakes de teste | causa real era vazamento de atributos entre módulos (não cache); fixture de restauração | ✅ eliminados |

Dois defeitos foram descobertos **durante** a implementação e travados por teste
de regressão: o vazamento de `core.b3_data` entre arquivos de teste, e o
verificador de ancoragem aceitando variação percentual (780,95%) como âncora de
um valor em reais (R$ 780,00).

### 14.2 Percentuais reavaliados

| Módulo | Dados | Metodologia | Indicadores | Ranking | Carteira | LLM | Código | **Geral** | Antes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Empresas brasileiras | 93,0% | 87,2% | 84,8% | 86,1% | 85,3% | 84,2% | 83,1% | **87,4%** | 85,8% |
| FIIs brasileiros | 86,0% | 89,1% | 84,3% | 85,7% | 84,9% | 82,8% | 88,6% | **86,5%** | 84,2% |
| Ações americanas | 92,5% | 88,4% | 86,5% | 84,1% | 79,6% | 82,1% | 86,7% | **87,3%** | 84,5% |

Transversais: banco de dados **92,0%** (era 86,3%) · pontuação **86,8%** (era
85,4%) · carteiras 83,3% (inalterado — a extração de lógica das views segue
pendente) · LLM **83,4%** (era 78,1%) · código **86,1%** (era 83,7%).

### 14.3 Percentual global

Com os mesmos pesos da §11:

**Global = .25·87,4 + .20·86,5 + .15·87,3 + .15·92,0 + .08·83,3 + .07·86,8 + .03·83,4 + .07·86,1 = 87,3%**

| | Auditoria inicial (23/07) | Após as correções (24/07) |
|---|---:|---:|
| Percentual global | 84,8% | **87,3%** (+2,5 p.p.) |
| Classificação | Muito bom | **Muito bom** (a 2,7 p.p. de "Excelente") |
| Confiança da avaliação | 78% | **84%** (execução verificada, não projeção) |
| Cobertura da auditoria | ~72% | **~82%** (Supabase agora auditado) |

### 14.4 O que continua aberto

1. **Separação UI/negócio (74%)** — `views/empresas_b3.py` (5.958 linhas) e
   `views/portfolio_b3.py` (3.071) ainda concentram lógica de negócio, incluindo
   a validação estatística do portfólio B3. É agora o maior redutor isolado da
   nota de código e o próximo passo natural.
2. **Percentuais de acerto da LLM** — o harness existe, mas os números de §12.8
   (respostas corretas / inventadas) só saem quando `scripts/eval_llm.py` rodar
   com chave de API. Até lá seguem **não medidos**, não estimados.
3. **Carteira Modelo EUA (79,6%)** — menos madura que a B3: sem custos de
   rebalanceamento nem modelagem de exposição cambial.
4. **Observabilidade (74%)** — logging existe; faltam métricas/alertas de
   execução dos pipelines.
5. **Cobertura de testes não medida** — a suíte é grande e verde, mas nenhum
   relatório de cobertura é gerado; a estimativa de ~55–65% do core segue sem
   verificação.

---

## 15. Correção metodológica — estatística como gate, não como calibrador (25/07/2026)

Registro de uma crítica do proprietário do sistema que **procede** e corrige duas
avaliações desta auditoria.

### 15.1 A objeção

*"A validação estatística já não tinha sido discutida diante das dificuldades de
usá-la no âmbito brasileiro — instabilidade política e econômica? Isso não
tornaria o sistema instável?"*

### 15.2 O que estava correto e o que estava errado

**Correto:** nada da validação estatística foi alterado nesta sessão — o `git log`
não registra commits em `views/portfolio_b3.py`, `core/b3_validation.py`,
`core/b3_methodology.py`, `core/fama_macbeth.py` ou `core/survivorship.py`. A
auditoria apenas **descreveu** o mecanismo já existente. E a discussão anterior
(`docs/guia_metodologia_carteira_b3.md` §6–§8) nunca concluiu remover a
estatística: concluiu que, com a Selic acumulando ~30% em 24 meses, o gate
aprovaria pouco ou nada — e que esse é o resultado honesto, sendo a margem vs
Selic a única alavanca aceitável para ampliar, **nunca** a significância.

**Errado — duas avaliações importaram premissa de estacionariedade:**

1. **§2.2 penalizava "coerência dos pesos" em 79%** por os pesos das trilhas
   serem fixos por convenção e o Fama-MacBeth ser apenas opcional. Sob quebras
   de regime (eleições, choques de juro, intervenção setorial), peso fixo é
   **robustez**: não há amostra estacionária que justifique reestimá-los sem
   perseguir ruído. Corrigido para **86%**.
2. **§6 recomendava tornar rotina a calibração empírica dos pesos.** Retirada.
   Seria justamente o mecanismo que instabilizaria o ranking a cada virada de
   ciclo — o risco levantado na objeção.

### 15.3 A distinção que resolve a aparente contradição

| Uso da estatística | Efeito sob instabilidade macro | Estado no sistema |
|---|---|---|
| **Gate** — aprovar/reprovar seleção; "0 aprovados" é resposta válida | **Reduz** instabilidade: recusa emitir carteira a partir de ruído | ativo, auditado, inalterado |
| **Calibrador de parâmetros** — reestimar pesos com os dados de cada período | **Aumenta** instabilidade: os pesos perseguem o regime | opcional, desligado — e assim deve permanecer |

A objeção é decisiva contra o segundo uso e **favorável** ao primeiro. Um gate que
se cala por falta de evidência é estabilizador por construção; o que desestabiliza
é reescrever os parâmetros do modelo a cada leitura nova do mercado.

Permanecem válidas, portanto: FDR entre segmentos, Rank-IC com t-stat, holdout
OOS, walk-forward com purga e publication lag — todos operando como **veto**, com
pesos fixos e versionados. E permanece pendente, sem prazo definido, a sugestão
já registrada no guia: exigir significância do excesso **vs Equal-Weight do
próprio segmento**, que isola habilidade de seleção e neutraliza o regime —
resposta direta à pró-ciclicidade do gate atual.

### 15.4 Efeito nos percentuais

Pontuação/rankings: 86,8% → **87,0%** (a correção de 79% → 86% em coerência dos
pesos entra com peso pequeno no consolidado). Global: **87,3% → 87,4%**. A
mudança material não é a nota — é a retirada de uma recomendação que teria
degradado a estabilidade do sistema se implementada.

---

## 16. Poder estatístico, amostra e a rota de valor ausente (25/07/2026)

Continuação da §15, após aprofundamento da objeção. Aqui há uma **terceira
correção da auditoria** e o diagnóstico do que de fato falta.

### 16.1 Correção: descrevi o gate de forma incompleta

A §2.2 apresentou "holdout OOS, Rank-IC com t-stat, walk-forward, FDR" como se
fosse **o** critério de aprovação da carteira B3. Não é. O seletor "Critério de
aprovação" (`views/portfolio_b3.py:1723`) tem três modos e o **padrão é
"Econômico (Brasil)"**, no qual:

* o gate primário é **econômico** — margem vs Selic no histórico cheio e, como
  segundo portão, margem vs Equal-Weight do próprio segmento;
* a estatística atua só como **guarda-corpo**: reprova apenas `rank_ic_mean <
  -0,05`, isto é, **evidência contra** (sinal claramente anti-preditivo). Não
  exige prova positiva de significância.

Os modos "Sinal fundamental (Rank-IC)" e "Retorno de 24m (FDR)" — que exigem
significância — são **opcionais**, e a própria ajuda da interface avisa que
dependem de amplitude "escassa na B3". O cenário de "0 aprovados" documentado no
guia (§6) descreve o modo estatístico, não o padrão.

Portanto: **o sistema não responde "nunca invista" por padrão.** A auditoria
deveria ter registrado essa arquitetura de modos; a nota de metodologia BR
(87,2%) não muda, mas a descrição estava incompleta e induzia à leitura errada.

### 16.2 Onde a objeção procede — com números

Amostra por segmento na B3 (consulta em 25/07/2026, `market.assets` × `public.setores`):

| Métrica | Valor |
|---|---:|
| Segmentos | 78 |
| Empresas por segmento (média) | 5,6 |
| Empresas por segmento (**mediana**) | **3** |
| Segmentos com menos de 5 empresas | 51 (65%) |
| Segmentos com menos de 3 empresas | 37 (47%) |

Com **mediana de 3 empresas por segmento**, um Rank-IC cross-seccional é
praticamente sem conteúdo: não se demonstra habilidade de ordenação entre três
nomes. Somando ~10 anos de histórico e correção FDR entre 64–78 testes, o
**poder estatístico é próximo de zero**. E aí incide o erro conceitual clássico:
*ausência de evidência não é evidência de ausência*. Nos modos estatísticos,
"não rejeitei H₀" é tratado como reprovação — quando o correto seria
**inconclusivo**.

Nota sobre o argumento dos investidores bem-sucedidos: ele é, isoladamente,
evidência fraca (viés de sobrevivência — ouvimos falar dos que acertaram). Mas a
conclusão continua correta por outro caminho, mais forte: um teste sem poder não
autoriza concluir inviabilidade. A objeção não precisa dos casos de sucesso.

### 16.3 O que de fato falta: rota de valor para a carteira

`core/valuation.py` (Graham, Bazin, margem de segurança) é usado em
`views/empresas_b3.py` — painel **individual** — e **não** em
`views/portfolio_b3.py`. Ou seja: existe rota de "habilidade de seleção por
segmento" (econômica ou estatística), mas **não existe rota de valor** que
construa carteira a partir de distorção de preço vs valor intrínseco. O controle
"Peso de barganha no score" mistura múltiplos baratos ao score (padrão 0%), o que
é atenuação, não uma tese própria.

Essa é a lacuna que corresponde à tese "crise = oportunidade": ela hoje só existe
na análise caso a caso, não na construção de carteira.

### 16.4 Caminho proposto (não implementado — decisão do proprietário)

1. **Separar "inconclusivo" de "reprovado"** nos modos estatísticos: reportar
   amplitude e poder ao lado do p-valor, com três estados (evidência a favor /
   evidência contra / inconclusivo por falta de amplitude). Nunca deixar
   "inconclusivo" bloquear sozinho.
2. **Rota de valor paralela**: seleção por margem de segurança vs valor
   intrínseco, com gate de **solvência e resiliência** (Altman, cobertura de
   juros, ROIC > risco-livre, geração de caixa) — a disciplina que separa
   distorção de armadilha de valor. Lembrete factual do próprio guia: ~20% das
   ações brasileiras perderam mais de 90% em 15 anos (Oi, Americanas, Gol).
3. **Estatística dimensionada à amostra**: em vez de 64–78 testes independentes
   por segmento (N mediano = 3), usar *pooling* hierárquico com encolhimento —
   empresta força entre segmentos — e testar habilidade no nível do **universo**
   (N > 300), onde há amplitude. Isso não é afrouxar rigor: é trocar um teste
   inadequado ao tamanho da amostra por um adequado.

O item 3 preserva a função de veto contra vieses que continuam valendo
independentemente do regime: sobrevivência, look-ahead e sinal anti-preditivo.

---

## 17. O padrão "diagnóstico sem porta de entrada" (27/07/2026)

Uma carteira real gerada pelo app disparou a verificação mais produtiva de todo
o ciclo. O achado não é sobre dado nem sobre estatística: é sobre **arquitetura**.

### O padrão

Motor de diagnóstico que não é consultado no caminho da decisão é **decoração**.
Ele passa em todos os testes, aparece bonito na tela e não protege ninguém.

Encontrado em dois dos três módulos:

| Módulo | Motores que não alcançavam a decisão |
|---|---|
| **B3** | rota de valor, estados de evidência, saúde das empresas — todos display-only |
| **EUA** | Altman Z, Piotroski F, accruals de Sloan, ROIC incremental — só na análise individual |
| **FIIs** | **nenhum** — `confidence` é 30% da utilidade, `publication_status` bloqueia, 7 dimensões de concentração com `min_dimension_coverage` |

O módulo de FIIs é a implementação de referência. Foi o menos alardeado e o
único sem a falha.

### O que a lacuna custava, em números

* **B3**: uma carteira aprovou empresa com payout de 318%, endividamento de 3,2×
  e ROIC abaixo da Selic — que a rota de valor, na mesma tela, classificava como
  armadilha potencial. E o B3 era o único dos três **sem teto setorial**: quatro
  segmentos distintos produziram uma carteira 100% cíclica.
* **EUA**: **597 empresas ativas (21%) em zona de aflição do Altman**, com o
  Z-Score calculado e gravado para todas elas — e ignorado na seleção. Depois de
  ligá-lo, 208 empresas que passavam foram excluídas, todas por aflição
  **confirmada por segundo alerta independente**.
* **EUA (payout)**: a primeira versão da penalidade nasceu **inerte** — o campo
  não existia na vitrine publicada. Derivar do bloco `financials` fez 1.293
  empresas ganharem a métrica na hora, sinalizando 111 (XRX a 151× o lucro).

### Duas decisões de projeto que valem mais que o código

1. **Alertar, não remover.** O gate de saúde do B3 não exclui ativos
   automaticamente. Veto automático sobre métrica contábil pontual repetiria o
   erro da §15: deixar a máquina decidir onde não tem base. Payout de um ano
   pode ser evento extraordinário; ROIC abaixo da Selic pode ser vale de ciclo.
2. **Recusar o que não se sustenta.** O ROIC incremental **não** virou
   penalidade: 39% dos que têm o dado o têm negativo, e é um delta de dois anos
   que vira com uma queda de EBIT. Ligá-lo seria disparar em vale de ciclo.
   Nem todo motor disponível deve entrar na decisão.

### Calibrações feitas contra casos reais, não contra intuição

| Regra | Primeira versão | Corrigida para | Motivo |
|---|---|---|---|
| Payout (B3) | crítico acima de 100% | atenção ≥100%, crítico ≥150% | holding repassa dividendo da controlada (BRAP3 a 120%) — alarme falso treina a ignorar alarme |
| Altman (EUA) | — | peso 8, corte de exclusão 10 | sozinho não exclui: o Z-Score de 1968 erra em asset-light |
| Sloan (EUA) | — | corte em 0,10 | p95 do universo real é 0,112; mediana é −0,050 |

### Efeito nos percentuais

Carteiras Modelo: 83,3% → **87,5%** (o teto setorial e o gate de saúde eram as
lacunas que sustentavam a nota baixa). Ações americanas: 87,3% → **88,9%**
(quatro motores passaram a participar da seleção). Empresas brasileiras:
87,4% → **88,6%**. **Global: 87,3% → 88,4%.**

Permanece o alerta metodológico da §14.4: a nota de carteiras tem cobertura de
auditoria de ~60% — as instâncias salvas seguem não auditadas.

---

## 18. Ciclo de auditoria automatizada da carteira (29/07/2026)

Pedido: rodar o app, montar carteiras, achar falhas, corrigir e repetir até
convergir. Aceito com uma ressalva de método que muda o desenho.

### O que o ciclo NÃO pode fazer

Iterar parâmetros até a carteira "ficar boa" é **sobreajuste**. Não existe
carteira ótima verificável fora da amostra; ajustar até o resultado agradar é
o mesmo erro da §15. O ciclo foi construído para caçar **defeitos** —
propriedades que devem valer em qualquer configuração. Violação de invariante é
defeito; carteira feia não é.

### A ferramenta

`scripts/audit_portfolio_b3.py` roda a aba **sem navegador**, dirigindo os
widgets pela API do AppTest (pré-definir `session_state` de widget é rejeitado
pelo Streamlit). Cada execução leva ~7 minutos. Verifica nove invariantes: sem
exceção, pesos somam 1, teto por ativo, sem duplicata, teto setorial e de ciclo
respeitados **ou avisados**, determinismo, monotonia e coerência entre motores.

### Três defeitos encontrados — nenhum por teste unitário

**1. Não determinismo (grave).** A mesma configuração produzia carteiras
diferentes: SHUL4 numa execução, GOAU4 noutra. Isolado fixando
`PYTHONHASHSEED`. Causa: `sorted()` é estável, então empates preservavam a
ordem de dicionários alimentados por iteração de conjunto — que varia com o
hash seed do processo. **A recomendação dependia de um detalhe interno do
Python.** Corrigido tornando a ordenação total (desempate por ticker) em cinco
pontos de decisão. Determinismo confirmado depois em três sementes (0, 1, 7).

**2. Custo oculto do filtro de resiliência.** A carteira 83% cíclica que
motivou toda a investigação não vinha do critério econômico: vinha do filtro
"Exigir resiliência (ROIC > risco-livre)" a 5 p.p. Medição: apenas **20% das
utilities** superam a Selic nesse spread, contra **27% das cíclicas** — o corte
por ROIC penaliza setores regulados por concessão, cujo retorno contábil é
limitado por desenho regulatório. O app passou a declarar quantos segmentos o
filtro cortou, **separando defensivos de cíclicos**.

**3. Defeito na própria ferramenta de auditoria.** Uma execução devolveu
carteira vazia e quase foi reportada como quebra de determinismo. Re-execução
com mais tempo devolveu a carteira idêntica: era timeout. O harness tratava
chave ausente como "nenhum aprovado", confundindo execução interrompida com
resposta legítima. Agora distingue **inconclusivo** de **vazio** — o mesmo
princípio da §16 aplicado à ferramenta que audita.

### O que o ciclo mostrou sobre a carteira

Com os parâmetros padrão, a carteira sai com **10 ativos, 55% cíclica e 23%
defensiva** (SBSP3, ISAE4, VIVT3 entram). A concentração de 83% observada antes
era efeito de configuração, não do motor. Monotonia confirmada: margem de 25%
aprova 7 ativos; margem de 5% aprova 10.

### Iteração 4 — varredura completa (13 configurações)

Um quarto defeito, encontrado só com a varredura ampla:

**4. Tetos que se desfazem em silêncio.** Com teto setorial de 25% e de ciclo
de 50%, *Utilidade Pública* terminou com **25,2%** — violação sem aviso. Causa:
os dois tetos rodavam em sequência, e a redistribuição da classe cíclica
empurrava peso de volta para um setor defensivo já no limite. Corrigido com
`project_dual_capped`, que alterna as duas projeções até convergir e, quando
são incompatíveis, **declara o conflito** em vez de esconder atrás de uma
violação de 0,2%. Re-verificado no motor real: 0 defeitos nas duas
configurações de teto.

### O que a varredura mediu

| Configuração | Ativos | Leitura |
|---|---:|---|
| base | 10 | referência |
| margem 5% | 10 | monotonia preservada |
| margem 25% | 7 | margem maior aprova menos |
| grupo mínimo = 1 | 13 | granularidade fina amplia o universo |
| barganha 30% | 12 | peso de valuation amplia a seleção |
| **resiliência 0 p.p.** | **9** | o filtro custa 1 ativo |
| **resiliência 5 p.p.** | **6** | o filtro custa **4 ativos** — era a causa da carteira de 6 nomes |
| **critério "Sinal (Rank-IC)"** | **0** | — |
| **critério "Retorno 24m (FDR)"** | **0** | — |

Os dois modos estatísticos aprovam **zero segmentos** no universo real. É a
confirmação empírica do que a §16 previu por cálculo de poder: com mediana de
3 empresas por segmento, o efeito mínimo detectável é 0,533 — inalcançável.
O modo "Econômico (Brasil)" ser o padrão não é preferência, é necessidade.

### Convergência

O ciclo convergiu, em cinco iterações, para **"sem defeitos conhecidos nos
invariantes"** — que é o máximo verificável. Não convergiu para "carteira ótima" e não convergirá. O
ganho real foi outro: o motor passou a ser **reproduzível** — antes a mesma
configuração podia devolver carteiras diferentes — e **honesto sobre o custo
dos filtros**.

Padrão dos três defeitos: nenhum apareceu em teste unitário. Todos surgiram ao
**executar o sistema inteiro e comparar execuções entre si**.
