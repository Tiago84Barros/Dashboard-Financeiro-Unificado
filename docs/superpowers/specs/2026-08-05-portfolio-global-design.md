# Persistência de Carteiras + Portfólio Global — Design

**Data:** 2026-08-05
**Estado:** aprovado para planejamento
**Escopo:** persistência analítica das carteiras existentes e nova seção Portfólio Global

---

## 1. Problema

O Dashboard Financeiro Unificado tem três seções que produzem carteiras-modelo — Empresas B3,
Empresas Americanas e Seleção de FIIs. Elas sofrem de dois problemas independentes.

**Persistência rasa.** As tabelas `b3_portfolio_models`, `us_portfolio_models` e
`fii_portfolio_models` (mais suas `_items`) guardam peso, score e alguns rótulos. Fundamentos,
histórico, evidências e premissas não são persistidos: são recalculados contra `market.*` a cada
abertura. `load_active_b3_portfolio_model` sequer lê o `meta_json` que grava. O resultado é que
reabrir o sistema exige reconstruir a análise.

**Ausência de visão patrimonial.** As três carteiras são analisadas isoladamente. Não existe
nenhum lugar onde o conjunto seja tratado como um único patrimônio — concentração, correlação,
exposição a fatores e métricas agregadas simplesmente não existem no nível global.

Há ainda um problema estrutural de manutenção: `core/b3_portfolio_model.py`,
`core/us_portfolio_model.py` e `core/fii_portfolio_model.py` são aproximadamente 90% código
idêntico. Cada classe de ativo nova custa um quarto arquivo quase igual.

## 2. Decisões tomadas

| Decisão | Escolha | Consequência |
|---|---|---|
| Base do Portfólio Global | Carteiras-modelo das três seções | Não depende de importação de posições reais |
| Profundidade do snapshot | Analítico + séries agregadas | ~18 KB/ativo; sem série diária de preço |
| Retenção | Ativa + 5 arquivadas com payload | Versões mais antigas mantêm só o cabeçalho |
| Motor de movimentação | Híbrido: motor determinístico propõe, LLM contesta | Auditável, com segunda camada crítica |
| Fonte macro | BCB/SGS + Fed reais, cenário editável | Substitui o hardcode atual |
| Alocação entre classes | Alvo definido pelo usuário | Explícito, versionado, extensível |

Local de armazenamento: **Supabase**. O app publica pela `main` no Streamlit Cloud e o armazém
local não acompanha o deploy. O Supabase Free já está apertado (metadados acima de 0,5 GB), o que
torna o tamanho do payload uma restrição de projeto, não um detalhe.

## 3. Abordagem escolhida

**Camada canônica aditiva com adaptadores por classe.**

Uma tabela nova guarda o payload rico, referenciando `(asset_class, model_id, symbol)`. As tabelas
existentes permanecem intactas como fonte de identidade e pesos. Um registro de classes de ativo
descreve tabelas, coluna-chave, moeda, país e adaptador, e um repositório genérico faz a leitura
uniforme das três classes para o Portfólio Global.

**Restrição de aditividade.** A Fase 1 não reescreve `core/b3_portfolio_model.py`,
`core/us_portfolio_model.py` nem `core/fii_portfolio_model.py`. A lógica de cada um permanece
intacta; cada `save_*` ganha somente uma chamada extra que grava o snapshot, protegida por
`try/except`, de modo que uma falha ali deixa o salvamento com o comportamento idêntico ao de hoje.
A deduplicação dos três módulos é desejável mas fica como fase posterior e opcional, executada
apenas depois da nova camada estar em produção e validada. Reescrever código validado sem
necessidade contraria a regra do projeto.

Alternativas descartadas:

- **Estender cada tabela com `snapshot_json`.** Menos migração, mas perpetua três formatos
  divergentes e acopla o Global a cada um. Cada classe nova custaria três lugares para alterar.
- **Schema unificado substituindo os três.** Destino mais limpo, porém migra dados vivos e
  contraria a regra do projeto de não mexer em funcionalidade validada sem necessidade.

## 4. Arquitetura de módulos

```
core/portfolio/                 camada canônica
  registry.py       AssetClassSpec: tabelas, chave, moeda, país, adaptador
  models.py         dataclasses: AssetSnapshot, PortfolioModel, AllocationTarget
  repository.py     save/load/list/restore genéricos, retenção, poda de órfãos
  snapshots.py      montagem e leitura do payload versionado
  adapters/
    b3.py           extrai o snapshot rico da seleção B3
    us.py           idem para o mercado americano
    fii.py          idem para FIIs

core/global_portfolio/          análise patrimonial
  aggregate.py      alvo x peso do modelo, câmbio para BRL, look-through de FII
  taxonomy.py       mapa canônico de setor/classe entre B3, EUA e FII
  concentration.py  HHI e top-N por ativo, setor, país, moeda e classe
  correlation.py    matriz EWMA, clusters, redundância, cobertura
  factors.py        betas macro por regressão; fallback de mapa setorial
  metrics.py        valuation, DY, qualidade, crescimento, volatilidade, risco
  roles.py          papel estratégico por ativo
  advisor.py        motor determinístico de movimentação

core/llm_client.py              cadeia OpenAI -> Gemini extraída dos llm_*.py
core/llm_context_global.py      montagem de contexto do Portfólio Global
core/llm_global.py              chat e crítica do motor
etl/bcb_sgs.py                  ingestão SELIC, IPCA, câmbio, atividade
views/portfolio_global.py       nova rota registrada em app.py
```

Regra de fronteira: `core/portfolio/` não conhece análise; `core/global_portfolio/` não conhece SQL
nem Streamlit; `views/` não calcula.

## 5. Persistência

### 5.1 Schema

Arquivo `supabase_unificado/schema/049_portfolio_asset_snapshots.sql`, idempotente, sem `DROP`,
`TRUNCATE` ou `DELETE`, seguindo o padrão do 047 (índices + RLS por dono).

```sql
CREATE TABLE IF NOT EXISTS portfolio_asset_snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    asset_class    VARCHAR(16) NOT NULL,
    model_id       UUID NOT NULL,
    symbol         VARCHAR(16) NOT NULL,
    schema_version INTEGER NOT NULL,
    as_of_date     DATE NOT NULL,
    payload        JSONB NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asset_class, model_id, symbol)
);

CREATE TABLE IF NOT EXISTS portfolio_allocation_targets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    status        VARCHAR(20) NOT NULL DEFAULT 'active',
    total_brl     NUMERIC(18,2),
    targets_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'archived'))
);
```

`targets_json` mapeia `asset_class -> peso alvo` e os pesos são normalizados na escrita.
`total_brl` é opcional: sem ele o Global trabalha só em percentuais.

### 5.2 Custo aceito conscientemente

`model_id` é polimórfico — aponta para três tabelas distintas, portanto não há chave estrangeira
nem `ON DELETE CASCADE` quando um modelo é apagado. A compensação é `repository.prune_orphans()`,
chamado a cada gravação, com teste que cria e apaga um modelo e verifica que nenhum snapshot
sobrou.

A alternativa — três colunas FK anuláveis com `CHECK` de exclusividade — daria integridade
declarativa real, mas exigiria migração de schema a cada classe de ativo nova. A troca é
integridade declarativa por extensibilidade, e é deliberada.

### 5.3 Payload (`schema_version = 1`)

| Bloco | Conteúdo | Tamanho aproximado |
|---|---|---|
| `identity` | símbolo, nome, classe, país, moeda, setor/subsetor/segmento, CNPJ ou CIK | 0,5 KB |
| `fundamentals` | múltiplos e demonstrativos do momento da seleção, com `reference_date` por campo | 4 KB |
| `metrics` | score, cobertura, alpha, DY, volatilidade, beta, margens, crescimento | 2 KB |
| `classification` | piso de qualidade, ciclo, `data_confidence`, status, `critical_missing` | 1 KB |
| `history` | 6 anos anuais de DRE e 5 anos de proventos, agregados | 5 KB |
| `assumptions` | parâmetros do modelo, restrições, taxas, cenário vigente | 1 KB |
| `evidence` | proveniência por campo, divergências banco/web, fontes | 4 KB |
| `notes` | observações livres, editáveis na interface | — |
| `provenance` | fonte, `captured_at`, `ingestion_run`, `backfilled` | 0,3 KB |

Cerca de 18 KB por ativo. Uma carteira de 30 ativos ocupa aproximadamente 0,5 MB. Com retenção de
cinco versões arquivadas por classe, o teto fica em torno de 10 MB.

Séries diárias de preço ficam fora do payload por decisão explícita: elas explodiriam o Supabase
Free e continuam disponíveis online em `market.*` para os cálculos de correlação e volatilidade.

### 5.4 Retenção

Ao salvar uma carteira nova, a anterior é arquivada. A partir da sexta versão arquivada de uma
classe, o payload das mais antigas é removido e apenas o cabeçalho (pesos e métricas) permanece. A
constante `RETENTION_ARCHIVED = 5` vive em `core/portfolio/repository.py`.

### 5.5 Backfill

`scripts/backfill_portfolio_snapshots.py` reconstrói o snapshot das carteiras já salvas. O script
tenta `market.calculated_metric_vintages` para recuperar o valor como era na data da seleção, mas o
próprio código do projeto registra que hoje praticamente todas as vintages são baseline. Na
prática o backfill grava o valor atual, marca `provenance.backfilled = true` e usa `as_of_date` de
hoje. Não há retroatividade fingida: a captura verdadeira começa nas gravações seguintes.

O script segue o padrão do projeto com execução em modo de simulação por padrão e `--apply` para
gravar.

### 5.6 Compatibilidade

`save_b3_portfolio_model`, `save_us_portfolio_model` e `save_fii_portfolio_model` mantêm assinatura,
lógica interna e comportamento observável. A única alteração é uma chamada adicional ao final,
dentro de `try/except`, que grava o snapshot. Falha na gravação do snapshot deixa o salvamento com
resultado idêntico ao atual e apenas registra aviso na interface — degrada, não quebra.

Custos reais assumidos, e são apenas dois: até cerca de 10 MB de espaço no Supabase sob a política
de retenção, e alguns segundos adicionais ao salvar uma carteira, porque montar o snapshot lê
fundamentos e histórico. Nenhum efeito sobre leitura ou sobre as telas existentes.

## 6. Análise do Portfólio Global

### 6.1 Agregação

Peso global de um ativo = peso alvo da sua classe multiplicado pelo peso dentro do modelo.
Consolidação em BRL por `currency_returns.converter_para_brl`. Sem taxa de câmbio disponível, o
ativo entra marcado como indisponível em vez de receber proxy, respeitando a regra já imposta pelo
módulo. FIIs passam por `fii_lookthrough` para que a exposição imobiliária apareça por tipo de
imóvel em vez de um bloco opaco.

### 6.2 Taxonomia comum

B3 usa setor, subsetor e segmento próprios; o mercado americano usa sector e industry no estilo
GICS; FIIs usam segmento (tijolo, papel, híbrido). Sem um mapa canônico, "concentração por setor"
mistura escalas incompatíveis e produz um número enganoso.

`taxonomy.py` mantém o de-para em código, versionado e testado, com categoria `outros` explícita
para o que não mapear. O teste exige que nenhum setor presente nas carteiras caia em `outros` por
acidente.

### 6.3 Concentração

HHI por ativo, setor, país, moeda e classe, publicado como **número efetivo de posições**
(`1/HHI`), que é mais legível que o índice cru. Complementado por participação do top-1, top-3,
top-5 e top-10 e pela curva de Lorenz. Toda classificação vem acompanhada do número que a produziu.

### 6.4 Correlação

Retornos diários calculados em BRL — o americano convertido, porque correlacionar em moeda de
origem mede câmbio disfarçado de ativo. Matriz EWMA por `correlations.ewma_correlation_matrix`;
pares redundantes por `b3_correlation_diversification.high_correlation_pairs`; agrupamento
hierárquico para identificar excesso de ativos semelhantes.

Diversificação real medida por *diversification ratio* `(soma de wi*sigma_i) / sigma_p` e por
número efetivo de apostas via PCA. `correlation_coverage` reporta quantos pares tiveram observações
suficientes; com cobertura baixa o painel declara isso em vez de exibir uma matriz vazia com
aparência de completa.

### 6.5 Exposição a fatores

Duas camadas, sempre rotuladas na saída:

1. **Betas estimados** — regressão dos retornos contra juros, inflação implícita, USDBRL,
   crescimento (Ibovespa e S&P 500) e commodities, mais fatores de estilo via `ff_risk_model`.
   Publica R², erro-padrão e número de observações.
2. **Mapa setorial qualitativo** — usado quando a série é curta demais para regressão. Marcado como
   qualitativo, nunca apresentado como estimativa.

Exposição do portfólio = soma de `wi * beta_i,f`.

Tecnologia, consumo, bancos, utilities e small/large cap entram como exposições setoriais e de
tamanho, não como regressores. Misturá-los com os fatores macro inflaria o R² sem ganho de
significado.

### 6.6 Métricas globais

Dois pontos onde o caminho óbvio está errado:

- **Valuation agregado** calculado por *earnings yield* ponderado e invertido ao final. A média
  aritmética ponderada de P/L é matematicamente incorreta e distorce para cima quando há empresa de
  lucro pequeno.
- **Qualidade média** só após normalizar cada score ao seu percentil **dentro da classe**. Score
  B3, score americano e score FII vêm de metodologias diferentes; somá-los crus produz um número
  sem significado.

Demais métricas: DY consolidado, crescimento por CAGR ponderado, volatilidade
`sigma_p = raiz(w' * Sigma * w)` com `Sigma` EWMA, VaR e CVaR paramétricos e históricos, drawdown
máximo do portfólio sintético, e distribuição dos pesos.

Toda métrica publica sua cobertura, isto é, o percentual do patrimônio que possui o dado. Abaixo de
60% a métrica aparece com aviso explícito em vez de ser omitida silenciosamente.

## 7. Papel estratégico

`roles.py` é um classificador determinístico por regras sobre o snapshot:

| Papel | Critério |
|---|---|
| Geração de renda | DY acima da mediana da classe com payout estável |
| Crescimento | CAGR de receita e lucro alto com DY baixo |
| Redução de volatilidade | Beta e volatilidade abaixo dos limiares |
| Diversificação | Correlação média com o restante abaixo do limiar |
| Hedge cambial | Moeda diferente de BRL |
| Proteção inflacionária | Indexação contratual: FII de papel IPCA, shoppings, utilities reguladas |
| Reserva de valor | Ativo real (FII de tijolo ou setor de infraestrutura) com beta de crescimento abaixo do limiar |

Cada ativo recebe papel primário e secundários, cada um acompanhado do número que o justificou,
mais uma justificativa de permanência. Ativo que não conquista papel algum e está caro é
sinalizado.

Ressalva registrada no próprio painel: este é um classificador heurístico com limiares escolhidos,
não um fato sobre o ativo. Os limiares ficam visíveis e ajustáveis na interface.

## 8. Motor de movimentação

`advisor.py` combina sinais normalizados:

- valuation, por percentil na classe e contra o próprio histórico do ativo;
- qualidade e `data_confidence`;
- contribuição marginal ao risco comparada ao peso;
- redundância por correlação alta com par melhor classificado;
- estouro de limite de concentração por ativo, setor ou país;
- ausência de papel estratégico;
- desvio em relação ao alvo da classe.

O score resultante passa por `portfolio_constraints`, é resolvido por `rebalancing.py` e descontado
por `transaction_costs.py` — os três já existem no projeto. A saída é uma lista de ações
(`aumentar`, `reduzir`, `vender`, `manter`) com peso atual, peso sugerido e decomposição numérica
do motivo.

**Requisito que impede o motor de virar decoração:** cada recomendação registra qual analisador a
disparou. Se `concentration`, `correlation` e `factors` não aparecem como gatilho de nenhuma
sugestão, eles não estão no caminho da decisão e são apenas exibição. A interface mostra essa
origem e um teste verifica que os analisadores são efetivamente consultados pelo motor.

## 9. Integração com LLM

Dois papéis distintos, nunca misturados na mesma área da tela.

**Chat contextual.** `llm_context_global.py` monta o contexto no padrão já estabelecido por
`llm_context_b3`: detecção de intenção, montagem por blocos e teto de tokens. O contexto contém
exatamente o que está na tela — snapshots, saídas dos analisadores, alocação-alvo e cenário macro.
Ancoragem por `llm_grounding`.

**Crítico do motor.** Recebe a saída do advisor e o contexto e responde de forma estruturada: onde
concorda, onde discorda e por quê, e o que o motor não enxerga. A interface exibe duas colunas,
motor à esquerda e crítica à direita. A LLM nunca reescreve os números do motor.

`core/llm_client.py` promove a cadeia de provedores OpenAI para Gemini a módulo próprio. Hoje ela
vive em `llm_b3._chat_complete`, uma função privada de um módulo de seção que `llm_fii` e
`llm_financeiro` já importam diretamente — acoplamento invertido que pioraria com mais um
consumidor. `llm_b3` passa a reexportar `_chat_complete` para não quebrar os importadores atuais.

## 10. Cenário macro de 12 meses

`etl/bcb_sgs.py` ingere SELIC, IPCA, câmbio e indicadores de atividade da API BCB/SGS para a tabela
criada em `supabase_unificado/schema/050_macro_indicators.sql`, substituindo o dicionário
`_MACRO_REF` hardcoded em `views/macro.py`. O lado americano reaproveita `market_us` macro.

O cenário de 12 meses fica num painel editável com premissas de variação de juros, inflação,
USDBRL, crescimento e commodities, pré-preenchido com o último valor observado. A tentativa de
buscar expectativas do Focus pela API Olinda é feita, e quando indisponível o campo permanece em
branco para preenchimento manual — sem valor inventado.

A propagação do cenário para o portfólio é determinística: cenário multiplicado pelos betas de
fator produz impacto estimado por ativo e agregado. A LLM explica quem se beneficia e quem sofre a
partir desse cálculo, não a partir do próprio conhecimento prévio.

## 11. Tratamento de erros

Cada painel do Portfólio Global falha isoladamente, seguindo o padrão de isolamento por módulo já
usado no roteamento de `app.py`. Com o banco indisponível, o Global exibe o que houver em cache.
Ativo sem snapshot aparece rotulado como "sem snapshot", com ação para regravar, e nunca desaparece
silenciosamente da soma dos pesos.

## 12. Testes

Módulos puros, determinísticos, executados no CI já existente:

- `taxonomy`: mapa exaustivo; nenhum setor presente nas carteiras cai em `outros` por acidente.
- `concentration`: HHI conferido contra valores conhecidos.
- `correlation`: matriz sintética com correlação plantada.
- `factors`: regressão sobre série com beta conhecido.
- `metrics`: `earnings yield` ponderado confrontado com a média aritmética incorreta.
- `advisor`: cenários fixos produzindo as ações esperadas; verificação de que os analisadores são
  consultados.
- `repository`: round-trip de gravação e leitura, poda de órfãos e política de retenção.

Validação com `PYTHONHASHSEED` variado, pelo histórico de não-determinismo na ordenação da carteira
B3.

## 13. Interface

Nova rota "🌐 Portfólio Global" registrada em `app.py`, no grupo de investimentos. Todas as
métricas em cards CSS pelo helper `_kpi_html` do padrão do projeto; nenhuma informação solta.

## 14. Faseamento

Cada fase recebe spec de implementação, plano e PR próprios.

| Fase | Entrega | Seções deste documento |
|---|---|---|
| 1 | Persistência canônica, snapshots ricos, adaptadores, backfill | 4, 5 |
| 2 | Portfólio Global: agregação, alocação-alvo, diversificação, correlação, fatores, métricas | 6 |
| 3 | Papel estratégico e motor determinístico de movimentação | 7, 8 |
| 4 | Chat LLM, crítica do motor, macro de 12 meses com ingestão BCB/SGS | 9, 10 |
| 5 (opcional) | Deduplicação dos três `*_portfolio_model.py` sobre o repositório genérico | 3 |

A Fase 1 é o próximo passo e será planejada em detalhe. A Fase 5 só é considerada depois das
anteriores estarem em produção e validadas, e pode simplesmente não ser feita: ela melhora a
manutenção sem entregar funcionalidade.

Todas as fases são aditivas em relação ao que já existe. Nenhuma remove ou reescreve comportamento
das seções Empresas B3, Empresas Americanas e Seleção de FIIs. A única alteração em arquivo
existente na Fase 1 é a chamada extra de gravação de snapshot descrita em 5.6; na Fase 2, o
registro da nova rota em `app.py`; na Fase 4, a substituição do `_MACRO_REF` hardcoded por leitura
de tabela em `views/macro.py`.

## 15. Fora de escopo

- Consolidação a partir de posições reais (`portfolio_position_snapshots`). O Global opera sobre
  carteiras-modelo. A comparação alvo contra realizado é candidata a fase futura.
- Persistência de séries diárias de preço.
- Classes de ativo além de B3, ações americanas e FIIs. A arquitetura acomoda novas classes pelo
  registro, mas nenhuma outra será implementada agora.
- Execução de ordens. O motor sugere movimentações; não há integração com corretora.
