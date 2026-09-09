# Proteção ao investidor na seleção de FIIs

Data: 2026-09-09
Status: aprovado para planejamento

## Problema

O relatório da LLM recomendou fundos cujo dividend yield é sustentado por
receita não recorrente, e apresentou riscos de concentração como observação em
vez de critério. A causa não é a LLM: é a política de seleção.

`IntegratedEligibilityPolicy` (`core/fii_integrated_model.py`) impõe piso de
liquidez, piso de DY 12m, piso de histórico, teto de drawdown e faixa de P/VP.
Não impõe nada sobre recorrência da renda nem sobre concentração. O piso de DY
incide sobre a renda **divulgada**, então ele seleciona ativamente o yield
inflado: KORE11 entra com 18,17% de DY sustentado por 58,25% de recorrência.

No score (`core/fii_methodology.py`), `income_recurrence` pesa 0,08 e
`tenant_concentration` pesa 0,04 contra 0,12 do `dy_12m`. A média ponderada
deixa o yield alto compensar a renda não recorrente. O `critical=True` que
essas métricas carregam significa "se faltar, bloqueia publicação", não "se
estiver ruim, desqualifica".

### Medição sobre os 394 FIIs da vitrine (09/09/2026)

| Medida | Valor |
|---|---|
| Recorrência mediana do universo | 57,4% |
| Passam no piso de DY hoje (8%-20%) | 220 |
| Destes, recorrência abaixo de 70% | 88 (40%) |
| Cobertura de `income_recurrence` | 88,8% |
| Cobertura de `tenant_concentration` | 48,2% |
| Cobertura de `lease_expiry_concentration_24m` | 54,3% |

## Princípio

Proteção ao investidor é critério de entrada, não observação no relatório. Um
fundo cuja renda não se sustenta não é um fundo com nota menor: é um fundo
fora do universo. Onde a evidência de proteção não existe, a ausência é
declarada como ausência e cobra um preço — nunca é convertida em aprovação
silenciosa nem em zero punitivo.

## Desenho

### 1. `core/fii_renda_recorrente.py` (novo, puro)

Fonte única da renda recorrente e das leituras de concentração.

```
DY_RECORRENTE_FORMULA = "dy_12m * income_recurrence"

dy_recorrente(row) -> float | None
```

Devolve `None` quando `dy_12m` ou `income_recurrence` falta. Nunca cai no DY
bruto: um fallback que só preenche lacuna nunca contradiz, e contradizer o DY
divulgado é o objetivo da métrica.

O módulo também expõe a leitura das duas concentrações, distinguindo três
estados — abaixo do teto, acima do teto, não divulgada — para que nenhum
consumidor precise reimplementar a distinção.

`core/fii_integrated_model.py` e `core/fii_methodology.py` importam deste
módulo. Um teste compara os dois caminhos sobre as mesmas linhas: regra certa
em um consumidor só já publicou yield errado na vitrine antes.

### 2. Portão de entrada

Em `IntegratedEligibilityPolicy`:

- `min_dy_12m` é **renomeado** para `min_recurrent_dy_12m`, default `.08`. O
  rename é deliberado: impede que um chamador passe a semântica antiga em
  silêncio.
- Novo `max_tenant_concentration: float = .40`.
- Novo `max_lease_expiry_24m: float = .25`.

Novas razões de exclusão, no mesmo formato das existentes:

- `renda recorrente ausente`
- `renda recorrente abaixo do mínimo`
- `concentração de locatário acima do teto`
- `vencimentos em 24m acima do teto`

O teto de plausibilidade de 20% continua incidindo sobre o DY **bruto**: ali
ele testa a sanidade da fonte, não a qualidade da renda.

Os dois tetos de concentração só se aplicam a `tijolo` e `hibrido`, e só quando
a métrica é conhecida. Concentração desconhecida não exclui — ver seção 4.

### 3. Nota

`MetricDefinition("dy_12m", "income", .12, "higher", critical=True,
max_age_days=15)` passa a `dy_recorrente`, com peso, direção, criticidade e
idade máxima inalterados. `income_recurrence` permanece com seu peso de 0,08:
ela deixa de ser o único freio, mas continua medindo estabilidade da renda
além do nível.

Sem esta peça, entre os aprovados o ranking continuaria premiando o yield
inflado — mudaríamos quem entra, não quem é recomendado.

**Dupla contagem, assumida deliberadamente.** Com `dy_recorrente` na nota, a
recorrência passa a influir duas vezes: dentro do produto e como métrica
própria. A metodologia evita isso em outro ponto (vacância operacional e
física medem o mesmo risco e por isso compartilham uma definição com
fallback). Aqui a repetição é intencional e assimétrica: o produto mede o
**nível** da renda sustentável, e `income_recurrence` isolada mede a
**estabilidade** dela — dois fundos com o mesmo DY recorrente de 10% não são
equivalentes se um chega lá com 90% de recorrência sobre um yield de 11% e o
outro com 55% sobre 18%. Se a validação PIT mostrar que o par ficou redundante,
a correção é reduzir o peso de `income_recurrence`, não desfazer o produto.

### 4. O custo da opacidade

Tijolo e híbrido sem `tenant_concentration` ou sem
`lease_expiry_concentration_24m` divulgadas não são excluídos. Ficam marcados
como não divulgados e recebem teto de peso reduzido à metade de
`PortfolioPolicy.max_asset`.

Vetar apenas quem divulga premiaria quem cala — o mesmo defeito de uma média
renormalizada em que `None` é neutro e `0.0` é punitivo.

Implementação em `core/fii_portfolio_v4.py`: o limite superior por ativo deixa
de ser o escalar `policy.max_asset` e passa a ser um vetor por ativo, tanto nas
restrições do MILP quanto no caminho guloso de contingência. A verificação de
teste incide sobre os **pesos finais da carteira**, não sobre a proposta do
otimizador: já houve teto de 15% respeitado em toda chamada e violado em 27,8%
no acumulado.

`PortfolioPolicy.max_tenant` (0,15) é outra coisa — exposição da carteira a um
locatário via look-through — e não muda.

### 5. Tela e contexto da LLM

- A tabela passa a exibir "DY recorrente" como coluna principal e "DY
  divulgado" ao lado, para o número bater com o que o investidor vê no mercado.
  A coluna que decide e a coluna que aparece são a mesma.
- Os controles de preferência rotulam o piso como "DY recorrente 12m mín. (%)".
- `core/llm_context_fii.py` passa a entregar `dy_recorrente` e a declarar
  `tenant_concentration = não divulgado` e `lease_expiry_concentration_24m =
  não divulgado` como ausência explícita, em vez de omitir a linha.
- `core/llm_fii.py` ganha regra: não recomendar fundo cuja evidência de
  proteção seja desconhecida sem dizer que é desconhecida, nomeando a métrica.
- O relatório de exclusões da tela mostra as quatro razões novas com contagem,
  como já faz com as existentes.

### 6. Versão e safra

`METHODOLOGY_VERSION` sobe de 6.8.0 para 6.9.0 e `FORMULA_VERSION` acompanha.
`INTEGRATED_MODEL_VERSION` sobe de 6.7.0 para 6.8.0.

Consequência obrigatória: a validação point-in-time da 6.8.0 deixa de valer.
`load_fii_validation_status(METHODOLOGY_VERSION)` não encontrará safra
correspondente e a página volta a se declarar Lista de Diligência. O plano de
implementação inclui rodar o walk-forward PIT e republicar a vitrine como
etapa, não como pendência: subir versão sem reconstruir safra já desligou
backtest em silêncio neste projeto.

## Efeito medido

Universo elegível pelo piso de DY: 220 → **98** fundos.

| Etapa | Elegíveis |
|---|---|
| Hoje (DY bruto entre 8% e 20%) | 220 |
| Piso de 8% sobre DY recorrente | 122 |
| Veto de locatário acima de 40% | 106 |
| Veto de vencimentos 24m acima de 25% | 98 |

Composição em 106, antes do veto de vencimentos: 50 papel, 31 tijolo, 22 FoF,
2 híbrido — folga suficiente para carteiras de 12 ativos com o mínimo de dois
tipos distintos. Os 8 fundos que o veto de vencimentos remove são todos tijolo
ou híbrido.

Os cinco fundos que originaram a reclamação:

| Ticker | Tipo | DY divulgado | Recorrência | DY recorrente | Locatário | Venc. 24m | Situação |
|---|---|---|---|---|---|---|---|
| KORE11 | tijolo | 18,17% | 58,25% | 10,58% | 22,25% | 29,00% | **excluído** (vencimentos) |
| RZAK11 | papel | 15,68% | 80,46% | 12,62% | — | — | elegível |
| RZTR11 | tijolo | 13,79% | 88,86% | 12,25% | 0,24% | não divulgado | elegível, peso limitado |
| KNSC11 | papel | 12,61% | 64,83% | 8,17% | — | — | elegível, no limite |
| KNHF11 | híbrido | 12,20% | 82,36% | 10,05% | 28,50% | 6,76% | elegível |

## Testes

1. `dy_recorrente` devolve `None` — não o DY bruto — quando a recorrência falta.
2. Fundo com DY divulgado alto e recorrência baixa é excluído com a razão
   `renda recorrente abaixo do mínimo`, não apenas rebaixado.
3. Concentração conhecida acima do teto exclui; concentração ausente **não**
   exclui, e produz a marca de não divulgado.
4. Elegibilidade e score derivam o mesmo `dy_recorrente` para as mesmas linhas.
5. Fundo não divulgado nunca recebe peso acima de metade de `max_asset` nos
   **pesos finais**, tanto pelo MILP quanto pelo caminho guloso.
6. O contexto da LLM traz `dy_recorrente` e declara a concentração não
   divulgada em vez de omiti-la.
7. `METHODOLOGY_VERSION` mudou, logo nenhuma safra da 6.8.0 é aceita como
   validação da 6.9.0.

## Fora de escopo

- Recalcular `income_recurrence` (a janela de 36 meses e a fórmula
  `positive_share/(1+cv)` ficam como estão).
- Ampliar a cobertura de `tenant_concentration` além dos 48,2% atuais.
- Regras de proteção específicas para papel e FoF além do piso de renda
  recorrente.
