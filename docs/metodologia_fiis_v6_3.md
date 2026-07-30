# Metodologia Integrada de FIIs v6.3

## Escopo

A v6.3 corrige a factibilidade, o rebalanceamento e os gates de look-through da Carteira Modelo sem promover desempenho histórico a
garantia. A saída continua sendo uma Lista de Diligência até existir validação
point-in-time do mesmo motor usado para produzir os pesos exibidos.

## Mudanças determinísticas

- dados ausentes de vacância, crédito, LTV, duration, alavancagem ou
  concentração recebem penalidade de incerteza; não são interpretados como zero;
- consistência e calibração ausentes reduzem confiança e ficam registradas em
  `confidence_assumptions`;
- snapshot acima da idade máxima bloqueia publicação;
- correlação só libera publicação quando sua cobertura alcança o mínimo da
  política;
- pesos são revalidados depois do tratamento numérico do solver;
- `expected_yield` permanece apenas como compatibilidade, enquanto a interface
  identifica a métrica corretamente como DY histórico ponderado de 12 meses;
- a validação precisa declarar o identificador
  `fii_integrated_robust_optimizer.v6.3` para liberar o motor atual.

## Protocolo PIT do otimizador

O protocolo `fii-pit-robust-optimizer-3.2.0` reconstrói mensalmente:

- elegibilidade e score usando somente informação com `knowledge_at` anterior
  à decisão;
- cenário Selic/IPCA do último mês integralmente encerrado;
- correlação de retornos totais anteriores à decisão, com janela de 36 meses e
  amostra mínima de 12;
- pesos do `fii_integrated_robust_optimizer.v6.3`, incluindo bandas condicionais ao universo elegível,
  incerteza, concentração, liquidez e estresses;
- separação explícita entre ausência de pré-requisitos de dados e falha do
  solver apesar de entradas matematicamente factíveis;
- penalização de turnover contra a última carteira factível, sem consultar
  retornos posteriores;
- exigência de que a própria saída histórica seja publicável segundo as
  dimensões obrigatórias com série point-in-time verificável de setor e emissor;
- dimensões adicionais de locatário, devedor, indexador e região são limitadas
  quando alcançam cobertura suficiente e permanecem declaradas quando não
  observáveis; seus riscos intrafundo continuam no score e na confiança.
- gestor também é limitado na carteira atual quando observado, mas a base só
  possui primeira observação em 2026; por isso ele não é tratado como histórico
  anterior nem usado retroativamente no backtest.
- retorno posterior com 0,15% de custo e 0,10% de slippage por turnover.

## Resultado executado em 28/07/2026

O walk-forward de dez anos reconstruiu 20.155 snapshots. A V6.2 intermediária
produziu 70 períodos, turnover anual de 2,31x e zero violações matemáticas, mas
o teste funcional revelou que ela ainda aceitava carteiras de diligência com
look-through insuficiente. Esse resultado não é usado para liberar a V6.3.

O protocolo 3.2 exige que a saída histórica passe o mesmo `can_publish` da
interface. Com setor e emissor como dimensões PIT obrigatórias, nenhum período
alcançou simultaneamente todos os requisitos. A execução V6.3 ficou `blocked`,
com zero períodos publicáveis.

A base possui setor e emissor verificados desde 2016/2017, mas a cobertura dos
fundos que também passam liquidez, DY, P/VP, histórico, drawdown, bandas,
confiança e correlação não alcança 80% em uma carteira completa. Gestor só
possui primeira observação em 2026; identidade de locatário não é divulgada no
arquivo trimestral estruturado da CVM.

Consequentemente:

- a V6.3 não publica recomendação nem habilita o salvamento da carteira-modelo;
- dados ausentes não viram zero, proxy inventada ou identidade retroativa;
- o próximo desbloqueio depende de backfill point-in-time verificável de setor,
  emissor e demais exposições, seguido de novo walk-forward da mesma estratégia;
- o snapshot `fii_selection_inputs.v2` pode ser publicado para diligência, mas
  sua validação remota deve permanecer `blocked`.
