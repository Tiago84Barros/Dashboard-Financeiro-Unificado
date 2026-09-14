# Metodologia Integrada de FIIs v6.10

## Objetivo

A v6.10 cobra na decisão os riscos que a metodologia já rastreava e não
pesava: renda não recorrente contada como se fosse recorrente, ausência de
evidência de recorrência valendo zero no ponderado, e cardinalidade tratada
só como teto. Nenhuma restrição nova pode zerar a criação do portfólio:
todas cedem em degraus quando o regime não permite satisfazê-las, e a
cessão fica declarada ticker a ticker na tela e no canal de IA.

## Alterações da fórmula

1. A ordenação deixa de usar o dividend yield divulgado (`dy_12m`), que soma
   amortização e resultado não recorrente, e passa a usar renda recorrente
   (`dy_12m * income_recurrence`), com piso e veto de concentração.
2. Ausência de evidência de recorrência deixa de sair do ponderado valendo
   zero e vira observação explícita. Valer zero punia quem tinha evidência
   e premiava quem não tinha.
3. A recorrência conta a vida observada do fundo em vez de 36 meses fixos,
   e a safra PIT passa a ler a mesma série de renda que a produção — antes
   eram dois caminhos que podiam divergir sem erro visível.
4. O MILP ganha **piso de cardinalidade** (`min_assets = 12`), cedente. Até
   aqui a cardinalidade era só teto, e a função objetivo desempatava por
   MENOS ativos: o solver entregava o menor número que maximizava utilidade
   e nada o obrigava a diversificar.

O valor 12 foi medido, não escolhido. Piso 10 é inerte — devolve exatamente
o que a ausência de piso devolvia nos dois regimes extremos —, portanto
seria um portão calibrado no número que o próprio defeito produzia. Piso 12
fecha os dois regimes sem ceder, ao custo de 0,18% de utilidade no easing.

## Validação point-in-time local

Walk-forward do otimizador v6.8 executado no warehouse local com dados
observáveis em cada corte, em 14 de setembro de 2026:

- status: aprovado, sem blockers;
- snapshots reconstruídos e persistidos: 23.903;
- períodos com retorno utilizável: 70;
- viabilidade do otimizador: 100%;
- períodos com violação de restrição: 0;
- cobertura média de correlação: 100%;
- retorno médio mensal líquido: 0,56190%;
- retorno médio mensal do IFIX: 0,41328%;
- excesso médio mensal: +0,14861 ponto percentual;
- information ratio: 0,4516;
- estabilidade de ranking: 0,8947;
- drawdown máximo: -13,82%;
- turnover anualizado: 2,36 vezes;
- ativos elegíveis por corte, em média: 13,17.

**O excesso não é estatisticamente distinguível de zero.** O bootstrap do
excesso sobre 70 períodos devolve intervalo de -0,11161 a +0,38644 ponto
percentual ao mês, que contém o zero. A aprovação atesta integridade
temporal, viabilidade e respeito às restrições; não demonstra superioridade
de retorno. Resultados históricos não constituem promessa de desempenho
futuro.

Por regime macro, nenhum intervalo exclui o zero:

| Regime | Períodos | Excesso médio mensal | IC bootstrap | Pior período |
|---|---:|---:|---|---:|
| Juro real alto | 36 | +0,10700 pp | -0,19402 a +0,40894 pp | -3,63% |
| Easing | 17 | +0,44489 pp | -0,03688 a +0,97159 pp | -4,54% |
| Estresse inflacionário | 14 | -0,01269 pp | -0,78781 a +0,54672 pp | -2,90% |
| Neutro | 3 | -0,27824 pp | -1,59828 a +1,59813 pp | -2,40% |

## O que a validação declara sobre si mesma

Duas limitações estão gravadas na safra e não devem ser lidas como ruído:

- **Cessão de proteção em 38 dos 70 períodos (54,3%).** É o preço direto de
  nunca zerar a criação do portfólio: quando o regime não comporta as
  restrições cheias, elas afrouxam em degraus em vez de devolver carteira
  vazia. O número é publicado justamente para que a cessão não passe por
  rigor.
- **50 das 120 datas de decisão não chegaram a rodar o otimizador**
  (`optimizer_input_ready_fraction` 0,5833), por pré-requisito de dado — 37
  delas por menos de duas categorias elegíveis, 7 por pré-seleção inviável
  sob bandas, cardinalidade, liquidez, concentração e incerteza, 3 por
  leitura incompleta do universo e 3 por universo elegível vazio sem
  candidatos à concessão. Essas datas ficam fora da amostra em vez de
  entrarem como resultado neutro.

O backtest roda com `top_n = 12`, igual ao piso de cardinalidade, enquanto a
tela entrega até 14 ativos. A curva medida é, portanto, a da carteira no
piso, não a da carteira cheia.

## Identificação

- metodologia e modelo integrado: `6.10.0`;
- elegibilidade: `6.10.0`;
- estratégia: `fii_integrated_robust_optimizer.v6.8`;
- fórmula: `br-fii-integrated-income-resilience-6.10.0`;
- protocolo de validação: `fii-pit-robust-optimizer-3.4.0`.
