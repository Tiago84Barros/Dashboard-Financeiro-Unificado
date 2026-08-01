# Metodologia Integrada de FIIs v6.4

## Objetivo

A v6.4 corrige o conflito observado na v6.3 entre pré-seleção e gate de
publicação. A carteira continua sendo uma lista de diligência até que o
walk-forward point-in-time do mesmo motor passe em todos os critérios.
Desempenho histórico não é promessa de retorno.

## Mudanças determinísticas

- a pré-seleção MILP exige cobertura mínima de 80% para as dimensões
  aplicáveis: setor de ocupação em Tijolo/Híbrido e emissor em Papel/Híbrido;
- cada posição selecionada precisa ter peso de pelo menos 2%, impedindo que
  posições residuais sejam usadas apenas para elevar a contagem de cobertura;
- o vínculo binário do MILP passa a impor simultaneamente peso mínimo e máximo;
- o otimizador contínuo e a revalidação final aplicam o mesmo piso de 2%;
- os diagnósticos PIT registram cobertura por dimensão, universo elegível e
  estágio de cada falha;
- a região do imóvel pode ser derivada apenas quando a UF estiver explicitamente
  declarada no informe trimestral estruturado da CVM ou no final do endereço;
- identidade de locatário continua ausente quando não divulgada pela fonte.

## Identificadores

- metodologia: `6.4.0`;
- fórmula: `br-fii-integrated-income-resilience-6.4.0`;
- estratégia: `fii_integrated_robust_optimizer.v6.4`;
- protocolo PIT: `fii-pit-robust-optimizer-3.3.0`;
- parser CVM estruturado: `1.5.0` (normalização determinística de UF explícita
  em sigla ou nome completo; nenhuma geocodificação inferida).

## Regras de publicação

O resultado só pode ser publicado como carteira quando:

- a própria saída do otimizador tiver `can_publish=true`;
- houver ao menos 36 períodos PIT;
- cobertura histórica, estabilidade, turnover, observabilidade de retornos,
  factibilidade do solver e correlação atenderem aos limites registrados;
- o benchmark for o IFIX oficial;
- o security master histórico estiver disponível;
- a validação remota corresponder exatamente à estratégia v6.4.

## Resultado executado em 29/07/2026

O parser 1.4 reprocessou, sem erro, 11 arquivos trimestrais de 2016 a 2026:

- 18.459 contextos de fundo/data;
- 98.699 observações;
- 141.538 exposições processadas;
- 11.019 observações de UF, cobrindo 331 tickers e 26 unidades federativas.

O walk-forward reconstruiu 20.155 snapshots e produziu 65 períodos publicáveis:

- retorno líquido médio: 0,8786% ao mês;
- IFIX oficial: 0,7652% ao mês;
- excesso médio: +0,1134 ponto percentual ao mês;
- intervalo bootstrap de 95% do excesso: -0,2497% a +0,4816% ao mês;
- drawdown máximo: -12,17%;
- turnover anual: 2,39x;
- estabilidade média de ranking: 0,875;
- snapshots verificados, cobertura de retornos e correlação: 100%;
- factibilidade condicional do solver: 100%;
- zero períodos com violação de restrição.

A validação local 43 ficou `passed`. O intervalo do excesso cruza zero: a
execução valida o funcionamento point-in-time e os controles de risco, mas não
comprova alfa estatisticamente diferente de zero nem garante retorno futuro.

Dos 120 meses avaliados, 55 não possuíam pré-requisitos suficientes e foram
excluídos antes do cálculo de desempenho. A fração de entrada pronta foi
54,17%; esse recorte continua sendo limitação material e deve ser monitorado
como risco de representatividade histórica.
