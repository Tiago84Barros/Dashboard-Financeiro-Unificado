# Safras da carteira B3: vigência abril→abril e relatório de desempenho histórico

Data: 2026-09-21
Escopo: módulo B3 (Criação de Portfólio B3 / Empresas B3). FII e EUA ficam fora
desta fatia — as cadências deles são diferentes e a regra de abril não se aplica.

## Problema

**1. O gráfico de desempenho nasce em janeiro.** `views/portfolio_b3.py:4358`
ancora a simulação de aportes em `pd.Timestamp(ano_atual, 1, 1)` e aplica a
carteira `proximos_uniq` — calculada hoje, com os balanços de hoje —
retroativamente a janeiro. É look-ahead: a curva mostra o desempenho de uma
carteira que ninguém poderia ter montado em janeiro.

O resto do mesmo motor já aplica a regra correta. `_REBAL_MONTH` é abril,
`_simular_seg_backtest` só troca líderes em `mes >= _REBAL_MONTH`, e
`_rank_ic_por_ano` (`views/empresas_b3.py:3110`) mede explicitamente fim de
março/N a fim de março/N+1, com o comentário registrando que medir jan–dez seria
look-ahead porque o balanço FY N−1 só é público até 31/03. O gráfico é o único
lugar que ficou fora dessa regra.

**2. Não há como testar a hipótese de qualidade dos portfólios.** Os scores
existem safra a safra, mas a carteira de cada safra não é exposta em lugar
nenhum: `lids_por_ano` e `pesos_por_ano` são construídos dentro de
`_processar_segmento` e morrem ali — o dict de retorno (`views/portfolio_b3.py:1204`)
não os carrega. Não existe tela que mostre, ano a ano, quem entrou na carteira e
como aquela carteira se saiu.

**3. O conceito de "abril" está duplicado.** Três implementações independentes da
mesma regra, e um quarto consumidor que a esqueceu.

## Não-objetivos

- Replay PIT do pipeline completo (piso de liquidez, troca de classes irmãs,
  diversificação por correlação). `_db.load_giro_diario()` devolve um giro
  **atual** por ticker, não uma série; replayar a safra de 2015 com o giro de
  2026 reintroduz look-ahead. O replay fiel exigiria derivar giro histórico do
  COTAHIST no armazém local e refazer a correlação em janela da safra. Fica para
  depois, decidido pelo que este relatório mostrar.
- Estender à FII e aos EUA. A FII roda mensal (informe da CVM, 121 períodos);
  os EUA rodam por `as_of_date` em `market_us.score_vintages` (10-Q/10-K). Abril
  seria errado nos dois.
- Criar tabela de persistência de carteira-modelo. Já existe — ver §4.

## 1. Vigência: uma regra, um módulo

Novo `core/b3_vigencia.py`, puro (sem banco, sem Streamlit):

```
REBAL_MONTH = 4

safra_vigente_em(data) -> int
    # ano N cuja safra está em vigor na data.
    # data.month >= 4 → data.year ; senão → data.year - 1

janela_de_vigencia(safra) -> (Timestamp, Timestamp)
    # (1º de abril da safra, 31 de março do ano seguinte)

ano_base_do_score(safra) -> int
    # safra - 1: o exercício cujos balanços alimentaram o score
```

`_REBAL_MONTH` passa a ser reexportado daqui. `views/portfolio_b3.py`,
`views/empresas_b3.py` e `core/b3_safras.py` importam deste módulo; nenhum deles
mantém literal de mês de rebalance.

## 2. Correção do gráfico da tela

Em `views/portfolio_b3.py`, a seção "Desempenho parcial":

- `data_ini_ano` passa a ser `janela_de_vigencia(safra_vigente_em(hoje))[0]`.
- O cabeçalho e a legenda passam a nomear a safra e sua janela:
  *"Safra 2026 (balanços de 2025), vigente desde abril/2026"*. O rótulo "ano
  atual" some: em janeiro–março de 2027 a mesma tela mostrará corretamente a
  safra 2026 ainda vigente, e "ano atual" seria falso.
- A legenda declara, em uma linha, o look-ahead que **permanece**: a carteira
  usa piso de liquidez e diversificação por correlação com dados de hoje. A
  correção encurta a distorção de nove meses para o intervalo abril→hoje; não a
  elimina.

## 3. Relatório de safras

Lógica em `core/b3_safras.py` (puro: recebe `resultados` e o quadro de preços,
devolve DataFrames). Renderização em `views/portfolio_b3_safras.py`, chamada como
seção de `views/portfolio_b3.py`. Nenhuma lógica nova entra no arquivo de 4.466
linhas.

Única alteração no motor: `_processar_segmento` passa a incluir `lids_por_ano` e
`pesos_por_ano` no dict de retorno. Ambos já são point-in-time por construção
(score com `lag=1`, dados até N−1).

A carteira da safra N é a agregação entre segmentos que a tela já usa: orçamento
igual por segmento aprovado (`1/len(_seg_groups)`), pesos internos do score, e
soma dos orçamentos quando o mesmo ticker aparece em mais de um segmento.

### Bloco 1 — Tabela de safras

Uma linha por safra: safra, ano-base dos balanços, nº de segmentos, nº de ativos,
maiores posições, e o retorno da janela de vigência (abril/N → abril/N+1) da
estratégia contra Selic e contra equal-weight. O equal-weight é o de **todos os
tickers dos segmentos aprovados naquela safra**, não o dos ativos selecionados —
o contraste que interessa é "escolher os líderes" contra "comprar o segmento
inteiro", e essa é a mesma definição que `_simular_seg_backtest` já usa.

A safra vigente **não** entra nas médias: sua janela está incompleta. Ela aparece
na tabela com a janela marcada como em curso e o retorno marcado como parcial.

### Bloco 2 — Tamanho do viés de universo (sob demanda)

O orçamento entre segmentos é `1/len(_seg_groups)` sobre os segmentos
**aprovados**, e a aprovação sai do teste OOS com FDR calculado sobre a amostra
inteira, até 2026. O conjunto de segmentos que compõe a carteira de hoje foi,
portanto, escolhido com informação posterior às safras antigas. O score é PIT; o
universo não é.

Tornar a aprovação também PIT é inviável: nas primeiras safras não há janela OOS,
então nenhum segmento seria aprovado e o relatório começaria vazio.

A saída é medir. Duas curvas:

- **(a)** safras com o universo de segmentos de hoje (o que a tela entrega);
- **(b)** safras com **todos** os segmentos que tinham score naquele ano, sem
  gate de aprovação.

A distância entre (a) e (b), em pontos percentuais, é o tamanho do viés de
seleção de universo. Os scores de todos os segmentos já são calculados, então o
custo é o do backtest, não o do scoring.

Roda **sob demanda**, por botão: a tabela de safras e o bloco de expectativa
carregam com a tela; esta curva só quando pedida. O último valor medido fica
visível enquanto não for recalculado, carimbado com a data da medição.

### Bloco 3 — Expectativa para a safra vigente

Não publica número central de expectativa. Publica três coisas, separando
deliberadamente **ordenar** de **superar**:

- **Ordena?** Rank-IC médio por safra, t-stat, e o efeito mínimo detectável —
  `core/b3_evidence.py` já calcula.
- **Supera?** Intervalo bootstrap de 95% do excesso sobre a Selic por safra. Se
  atravessar o zero, o card afirma que atravessa e que a vantagem não é
  distinguível de acaso nesta amostra — mesmo padrão que a aba de FII já aplica
  ao IFIX.
- **Quão frágil?** Sensibilidade leave-one-out: recalcula o veredito removendo
  uma safra por vez e reporta quantas safras precisam sair para ele virar.

O terceiro item existe porque o veredito da B3 já passou a APROVADO por margem de
0,004, e retirar 1 de 7 dos 10 anos o reprova de novo. Essa fragilidade precisa
estar na tela, não no histórico de uma auditoria.

## 4. Persistência da carteira-modelo

Nenhuma tabela nova. `b3_portfolio_models` e `b3_portfolio_model_items` já
existem, e `save_b3_portfolio_model` **arquiva** a versão anterior
(`core/b3_portfolio_model.py:270`) em vez de apagá-la — o histórico já se acumula.

Duas correções:

1. `ano_compra` usa `date.today().year` (`core/b3_portfolio_model.py:263`). Em
   janeiro–março de 2027 isso carimbaria 2027 numa carteira da safra 2026. Passa
   a usar `safra_vigente_em(hoje)`.
2. `params_json` ganha `safra` e a janela de vigência explícitas, ao lado de
   `score_version`. Uma safra nunca deve precisar ser inferida de `created_at`.

Efeito futuro: com o tempo, o relatório poderá comparar a safra **reconstruída**
com a safra **efetivamente salva**. A diferença entre as duas é exatamente o
efeito dos pós-filtros (liquidez, correlação) que o replay completo mediria — é o
gancho para aquele trabalho sem pagar por ele agora.

## 5. Testes

Um teste por afirmação que a tela faz:

1. **Vigência não usa o futuro.** Propriedade sobre todas as datas de 2010 a
   2030: `ano_base_do_score(safra_vigente_em(d)) < d.year`. Em 31/03 a safra
   ainda é a anterior; em 01/04 vira.
2. **Unicidade da regra.** Varredura da AST de `views/portfolio_b3.py`,
   `views/empresas_b3.py` e `core/b3_safras.py` procurando literal de mês de
   rebalance fora de `core/b3_vigencia.py`. Falha se encontrar. A varredura
   resolve os caminhos relativos à raiz do pacote, não por `parts` de caminho
   absoluto — filtro por caminho absoluto não visita nada dentro de worktree.
3. **Janela do retorno.** O retorno da safra N usa preço de abril/N a abril/N+1 e
   nenhum preço fora disso — verificado por fixture com preços marcados.
4. **Safra incompleta fica de fora.** Uma safra sem janela completa não entra nas
   médias nem no bootstrap; aparece na tabela marcada como parcial.
5. **Leave-one-out recalcula.** Caso construído em que remover uma safra vira o
   veredito; o teste falha se a função devolver o veredito original.
6. **Carimbo de safra em fevereiro.** `save_b3_portfolio_model` chamado com data
   de fevereiro/2027 grava `safra = 2026`.
7. **Bloco 2 sem gate.** A curva (b) inclui segmento que o gate de aprovação
   reprova — se as duas curvas forem idênticas num cenário com segmento
   reprovado, a medição do viés não está medindo nada.

## Ordem de implementação

1. `core/b3_vigencia.py` + testes 1 e 2.
2. Correção do gráfico (§2) — a menor fatia com valor visível.
3. `lids_por_ano` / `pesos_por_ano` no retorno de `_processar_segmento`.
4. `core/b3_safras.py` + Bloco 1 + testes 3 e 4.
5. Bloco 3 + teste 5.
6. Bloco 2 (sob demanda) + teste 7.
7. Persistência (§4) + teste 6.
