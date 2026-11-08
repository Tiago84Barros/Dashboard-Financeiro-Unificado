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
- protocolo de validação: `fii-pit-robust-optimizer-3.5.0` (os números da
  validação acima foram medidos sob a `3.4.0`).

## Protocolo 3.5.0 — fundo que sai de campo rende zero (A-118)

Até a `3.4.0`, o walk-forward do otimizador robusto renormalizava os pesos
sobre os fundos com retorno no período. Um fundo da carteira que liquidou,
foi incorporado ou perdeu o dado tinha a fatia redistribuída entre os
sobreviventes, e o dinheiro dele rendia o que os outros renderam. A correção
do A-118 existia só no backtest simples (`point_in_time_backtest`), que não
roda em produção.

Na `3.5.0` a fatia ausente rende zero no período, pela mesma regra, e o
resultado grava `saida_de_campo` (peso ausente médio, máximo e períodos
afetados), que a tela já sabia exibir.

**A validação de 14/09/2026 precisa ser refeita** no warehouse local
(`scripts/run_fii_optimizer_pit_local.py`). Retorno médio, excesso, IC e
regimes acima só mudam nos períodos em que algum fundo da carteira ficou
sem retorno; enquanto a nova safra não for gravada, eles podem estar
inflados nesses períodos.

## Setor de inquilino só se cobra de quem tem inquilino (parser CVM 1.7.0)

A validação PIT de 30/09/2026 (run 105) foi recusada por um único motivo:
`optimizer_feasible_fraction` 0,861 (99 de 115), contra piso de 0,95. As 16
carteiras recusadas foram montadas pelo MILP e barradas no portão de
publicação por `exposicoes obrigatorias sem cobertura suficiente: sector`.

A exposição `sector` tem uma única fonte estruturada: a tabela de inquilinos
de **imóvel para renda acabado** do informe trimestral
(`inf_trimestral_fii_imovel_renda_acabado_inquilino`, coluna
`Setor_Atuacao`). Fundo de obra, de terreno, de estoque à venda ou de cotas
nunca aparece nela. Mesmo assim ele ficava na máscara de aplicáveis, porque
o informe mensal somava `Imoveis_Renda_Acabados`, `Imoveis_Renda_Construcao`,
`Imoveis_Venda_*` e `Terrenos` numa classe só (`real_estate`), e o híbrido
sem `pct_imoveis` no PIT era aplicável por cautela. Nas carteiras gravadas
no run 105, quem ficou sem setor foram fundos desse perfil: TGAR11 (todas as
recusas de 2021-08 a 2022-01), MFII11 (2018-04 a 2018-06) e, pelo mesmo
padrão, VGHF11 (2024-01). Essa leitura foi inferida das carteiras gravadas e
do cadastro atual. O `scripts/diagnosticar_cobertura_setor_fii.py` a
confirma ou refuta fundo a fundo no warehouse local.

A partir do parser `1.7.0`:

- o informe mensal grava `pct_imoveis_renda`, a fração da carteira em
  imóvel para renda acabado mais direitos reais (que entram por cautela,
  porque a CVM não diz se o direito é sobre imóvel locado). Se o layout do
  ano não traz a coluna `Imoveis_Renda_Acabados`, a métrica não é gravada:
  coluna ausente não vira fração zero;
- `core.fii_lookthrough.dimension_is_applicable` tira `sector` e `tenant` da
  máscara quando essa fração está observada abaixo de 20% e nenhum setor foi
  observado. Setor observado mantém o fundo na máscara. Região não muda,
  porque a tabela de imóveis lista obra, estoque e terreno.

Não mudaram `min_optimizer_feasible_fraction` (0,95) nem
`min_dimension_coverage` (0,80). A regra está numa função só, que a carteira
(`_dimension_observation_masks`), o diagnóstico de look-through e a revisão
de carteira chamam.

**Aprovada não quer dizer que bate o IFIX.** O excesso medido no run 105
sobre o IFIX oficial foi de −0,116 p.p./mês, com IC 95% de [−0,32; +0,10].
Destravar o portão de viabilidade não muda essa conclusão.
