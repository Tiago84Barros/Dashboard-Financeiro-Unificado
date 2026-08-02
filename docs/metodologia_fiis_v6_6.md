# Metodologia Integrada de FIIs v6.6

## Objetivo

A v6.6 corrige uma dupla penalização de vacância e endurece a classificação de
tipo dos FIIs. Nenhum gate de confiança foi reduzido, nenhum dado ausente foi
convertido em zero e evidências documentais pendentes continuam fora do score.

## Alterações da fórmula

1. Fundos de tijolo deixam de receber pesos independentes para vacância física
   (4%) e financeira (6%). A fórmula usa uma única `vacancia_operacional` com
   peso de 10%: prefere a financeira e usa a física apenas quando a primeira
   estiver ausente.
2. Fundos híbridos aplicam a mesma precedência na métrica de vacância, mantendo
   o peso anterior de 5%.
3. O score registra em `score_input_sources` se o valor veio de
   `vacancia_financeira` ou `vacancia_fisica`. Frescor e qualidade usam os
   metadados da observação efetivamente selecionada.
4. A compactação do Snapshot V2 preserva os metadados das métricas alternativas,
   evitando perda de linhagem na publicação.

## Taxonomia conservadora

- Tipo explícito da fonte estruturada tem precedência.
- Na ausência dele, são aceitos somente sinais fortes do perfil oficial: mandato
  `Híbrido`, `Títulos e Valores Mobiliários`, `Fundo de Fundos`, nome com token
  isolado `FOF` ou inventário exclusivamente imobiliário observado.
- A classe genérica `fund_share` não implica FoF. Esse rótulo também aparece em
  fundos imobiliários diretos e produzia falsos positivos.
- Inferências não sobrescrevem tipos válidos já persistidos; apenas um tipo
  explícito pode corrigi-los.

No universo público de 394 FIIs, 9 dos 12 fundos sem tipo foram classificados.
BBFI11, HGPO11 e LSOP11 permanecem sem classificação por ambiguidade real.

## Resultado de qualidade em 2 de agosto de 2026

| Indicador | V6.5 antes | V6.6 depois |
|---|---:|---:|
| Universo | 394 | 394 |
| FIIs pontuáveis | 382 | 391 |
| Prontos | 244 | 252 |
| Insuficientes entre pontuáveis | 138 | 139 |
| Sem tipo | 12 | 3 |
| Diligência ou sem tipo | 150 | 142 |
| Cobertura do universo | 96,95% | 99,24% |
| Confiança mediana | 79,95% | 80,58% |

O total pendente caiu em 8 fundos. O número de `insufficient` isoladamente subiu
em 1 porque oito fundos antes excluídos por falta de tipo agora são avaliados e
continuam corretamente em diligência. A lista bruta ainda não pode ser publicada
como recomendação: 64,45% dos pontuáveis estão prontos, abaixo do gate de 80%.

## Validação point-in-time local

O walk-forward do otimizador V6.6 foi executado no warehouse local com dados
observáveis em cada corte:

- status: aprovado;
- períodos com retorno utilizável: 58;
- viabilidade do otimizador: 100%;
- períodos com violação de restrição: 0;
- cobertura média de correlação: 100%;
- retorno médio mensal líquido: 0,61964%;
- retorno médio mensal do IFIX: 0,61978%;
- excesso médio mensal: -0,00015 ponto percentual;
- drawdown máximo: -11,13%;
- turnover anualizado: 2,47 vezes.

O resultado relativo ficou essencialmente neutro e abaixo da V6.5 no mesmo
histórico. A aprovação atesta integridade temporal, viabilidade e respeito às
restrições; não demonstra superioridade de retorno. Resultados históricos não
constituem promessa de desempenho futuro.

## Identificação

- metodologia e modelo integrado: `6.6.0`;
- estratégia: `fii_integrated_robust_optimizer.v6.6`;
- fórmula: `br-fii-integrated-income-resilience-6.6.0`.
