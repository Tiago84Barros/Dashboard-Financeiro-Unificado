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
| Qualidade das fórmulas | 88% | Coerência dos pesos | 79% (0,22/0,18/0,15… fixos por convenção; Fama-MacBeth só opcional) |
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

Por sistema: B3 86,1% (confiança 85%) · FII 85,7% (confiança 82%) · EUA 84,1% (confiança 80%). Principais fragilidades: pesos de trilha por convenção; dupla contagem leve. Alteração recomendada: calibração empírica periódica (Fama-MacBeth já implementado, tornar rotina) + teste de estabilidade bootstrap como gate. Qualidade estimada após correção: ~89% (**estimativa**).

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

---

## 9. Arquitetura e Qualidade do Código (§12.9) — **83,7%**

Organização 88% (core/views/data_pipeline/etl/scripts/docs conforme CLAUDE.md) · modularização 82% (núcleos puros exemplares; **`views/empresas_b3.py` 5.958 linhas e `views/portfolio_b3.py` 3.071 linhas concentram lógica de negócio na camada de interface**, incluindo a validação estatística do portfólio B3) · legibilidade 88% · documentação 92% (docs/ com 40+ documentos, metodologias versionadas, dicionário de dados) · tratamento de erros 78% (209 `except Exception` amplos em core) · segurança 84% (RLS nas tabelas de carteira, sem credenciais no repo, `check_secrets.py`, isolamento OWNER_USER_ID) · validação de entradas 82% · testes automatizados 86% (117 arquivos, 756 funções, 99,5% verdes) · **cobertura de testes: não medida; estimativa ~55–65% do core, ~45% do repositório** (funções críticas de metodologia: ~85% testadas; fórmulas com testes unitários: ~80%; pipelines validados: ~70%; exceções tratadas: ~75%; regras de negócio documentadas: ~85%) · rastreabilidade dos cálculos 92% · separação UI/negócio 74% · separação entre metodologias de classes 90% (B3/FII/US totalmente apartados) · manutenção 82% · escalabilidade 80% · observabilidade 74% (logging presente; sem métricas/alertas de execução) · **controle de versões 90%, porém nenhum workflow de CI executa `pytest`** — os 5 workflows são só de dados.

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
