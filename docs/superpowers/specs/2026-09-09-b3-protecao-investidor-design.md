# Proteção ao investidor na criação de portfólio de Empresas B3

Data: 2026-09-09
Status: aguardando revisão do usuário

## Problema

A carteira B3 pode indicar empresas cujo dividendo não se sustenta, porque o
motor de nota não lê nada sobre sustentabilidade da distribuição e o motor que
lê não tem porta de entrada na decisão.

`core/b3_company_score.py:38` define a trilha `shareholder` como
`[("DY", True), ("Payout", True)]` — ambas monotônicas, maior é melhor. Um
payout de 318% recebe rank 1,0: a métrica que deveria medir prudência mede
generosidade. É o mesmo defeito que o spec de FIIs corrigiu ao trocar `dy_12m`
por `dy_recorrente`.

`core/dossie_b3.py:369` (`_checks`) já computa a red flag "PATRIMÔNIO EM QUEDA
COM LUCRO POSITIVO … dividendo atual pode não ser recorrente", mas ela vai
apenas para o prompt da LLM e para a UI. Não existe caminho dela para a
seleção.

`core/b3_holdings_health.py:219` exige confirmação (FCO negativo,
endividamento acima do teto, margem operacional negativa) para transformar
payout ≥ 150% em CRÍTICO. Sem confirmação, ele emite o alerta
"verifique se é evento extraordinário (venda de ativo, privatização)" — o
código já sabe que o payout de um período pode ser um ano fora da curva e
devolve a pergunta ao usuário porque não tem como respondê-la.

### O erro que este spec corrige duas vezes

Todo diagnóstico existente lê a **foto**: o payout do TTM, o último par de anos
da série. Medido sobre a janela histórica, isso é majoritariamente ruído:

| Leitura | Empresas atingidas | Das quais são episódio isolado |
|---|---|---|
| Payout TTM > 100% com DY > 8% | 42 (com ≥3 anos) | **33 (79%)** têm payout mediano ≤ 100% |
| PL caindo com lucro positivo no último par | 88 | **80 (91%)** têm o padrão em < 50% dos anos |

UNIP6 marca payout de 318% no TTM e mediana de 63,5% em 8 anos. FESA4 marca
193% e mediana de 57,5%. Condenar por essas leituras é condenar a Unipar e a
Ferbasa por um exercício.

## Princípio

**Vale a qualidade histórica, não o período isolado.** Toda leitura de proteção
neste spec incide sobre a janela de exercícios anuais, nunca sobre um TTM ou um
par de anos. Um ano ruim em oito custa um oitavo.

**O peso está na nota; o portão é reservado ao conclusivo.** Corrigir a direção
do score não exclui ninguém — só muda quem lidera. O veto exige convergência de
duas evidências independentes.

**A criação de portfólio nunca é zerada.** Nenhuma regra deste spec pode
esvaziar a carteira nem um segmento dela. Onde a evidência falta, a ausência é
declarada e leva ao neutro — nunca a zero punitivo nem a aprovação silenciosa.

## Desenho

### 1. `core/b3_renda_sustentavel.py` (novo, puro)

Fonte única da sustentabilidade histórica da distribuição. Puro (pandas/numpy),
sem Streamlit e sem banco, no molde de `core/b3_slopes.py` — que existe pela
mesma razão declarada no seu docstring: "a tela de Empresas B3 e a Análise do
Portfólio precisam do MESMO crescimento, senão a mesma empresa recebe duas
notas e nenhuma das duas é auditável".

Assinatura espelhando `enrich_com_slopes`:

```
JANELA_ANOS = 8
MIN_ANOS = 3
PISO, OTIMO_LO, OTIMO_HI, TETO = 0.05, 0.25, 0.80, 1.30

sustentabilidade_do_ano(payout) -> float          # faixa de dois lados, [0,1]
enrich_com_renda_sustentavel(df_mult, hist_batch) -> pd.DataFrame
```

`sustentabilidade_do_ano` é uma faixa de **dois lados**: zero em payout ≤ 5%,
sobe linearmente até 1,0 em 25%, crédito cheio até 80%, desce linearmente até
zero em 130%. Distribuir quase nada e distribuir muito acima do lucro são as
duas formas de o dividendo não ser um dividendo sustentável.

`enrich_com_renda_sustentavel` acrescenta três colunas ao cross-section:

- `payout_sustentabilidade` — média das notas anuais na janela; `NaN` com menos
  de `MIN_ANOS` anos observados.
- `dy_sustentavel` — `DY` corrente × `payout_sustentabilidade`.
- `payout_mediano_hist` — mediana do payout na janela, consumida pela seção 3.

O nível é o de hoje e a qualidade é a do histórico. É a mesma forma que
`dy_recorrente = dy_12m * income_recurrence` (36 meses) no spec de FIIs.

**Regra de incoerência da fonte.** Um exercício com `DY > 0,5%` e
`Payout ≤ 1%` é contradição interna da fonte — a empresa não pode distribuir e
não distribuir no mesmo ano. Esse ano é tratado como **ausência**: sai do
cálculo da média, não entra como nota zero. Atinge 61 de 2.143 exercícios
(2,8%) em 53 tickers. Punir por dado incoerente é o mesmo erro de medir a
fonte errada.

Nenhuma chamada nova ao banco: `views/empresas_b3.py:3352` e
`core/portfolio_db_analysis.py:89` já carregam `load_multiplos_historico_batch`
para alimentar `enrich_com_slopes`. O novo enriquecimento consome o mesmo
`hist_batch`.

### 2. A trilha `shareholder`

`core/b3_company_score.py` passa de

```python
"shareholder": [("DY", True), ("Payout", True)],
```

para

```python
"shareholder": [("dy_sustentavel", True),
                ("payout_sustentabilidade", True),
                ("DY", True)],
```

Peso da trilha inalterado em 0,12.

`Payout` **sai** da trilha: monotônico e crescente, ele é a definição do
defeito. `DY` **permanece**, e a permanência é deliberada — três formulações
sem ele foram medidas e rejeitadas porque todas premiavam quem quase não
distribui. Com a trilha reduzida a métricas de sustentabilidade, uma empresa
com payout de 1,7% tirava rank alto por não ter de onde cair.

**Dupla contagem, assumida.** `payout_sustentabilidade` influi duas vezes:
dentro de `dy_sustentavel` e como métrica própria. Deliberado e assimétrico,
pelo mesmo argumento do spec de FIIs: o produto mede o **nível** da renda
sustentável e a métrica isolada mede a **qualidade** da política de
distribuição. Duas empresas com o mesmo `dy_sustentavel` de 8% não são
equivalentes se uma chega lá com sustentabilidade 0,95 sobre DY de 8,4% e a
outra com 0,45 sobre 17,8%.

**Ausência não pune.** 114 das 426 empresas (26,8%) não têm 3 anos de payout
observado. Elas ficam no `_NEUTRAL` da trilha, e o mecanismo de encolhimento
por cobertura já existente (`b3_company_score.py:160`,
`score = _NEUTRAL + (score - _NEUTRAL) * coverage.pow(0.5)`) trata o resto. O
delta mediano desse grupo é 0,0.

### 3. O portão

Um único veto, por **convergência de duas evidências históricas
independentes**:

- **A** — `payout_mediano_hist > 1,0` com pelo menos 5 anos observados.
- **B** — patrimônio líquido em queda com lucro positivo em ≥ 50% dos pares de
  anos consecutivos, com pelo menos 5 pares.

O veto exige **A e B**. Isoladas, A atinge 18 empresas e B atinge 9; juntas,
3 tickers (AFLT3, WHRL3, WHRL4 — duas empresas). A variante "A ou B" foi
medida e rejeitada: 24 empresas vetadas, com Utilidades Domésticas perdendo
40% e Construção e Engenharia 38%, sem ganho de convicção.

**Onde entra.** A persistência histórica entra como uma quarta **confirmação**
em `core/b3_holdings_health.py:210-218`, ao lado de FCO negativo, endividamento
acima do teto e margem operacional negativa. Não é motor paralelo: o docstring
de `core/b3_quality_floor.py` já estabelece que o piso "não define limiares
próprios — reaproveita `check_holdings`", porque "definir critérios paralelos
aqui recriaria exatamente o defeito que esta sessão inteira corrigiu: dois
motores julgando a mesma empresa com réguas diferentes, divergindo em
silêncio".

O critério B também corrige a red flag existente. `core/dossie_b3.py:388`
compara `serie[-2]` com `serie[-1]` e dispara em 88 de 426 empresas, das quais
80 têm o padrão em menos da metade dos anos. Ela passa a reportar a **fração
histórica**, com o texto nomeando quantos dos N anos apresentaram o padrão. A
flag continua indo para a LLM e para a UI; o que muda é que ela deixa de
condenar por um par de anos.

O `PAYOUT_CRITICO = 1.50` sobre o payout do TTM permanece como está: ali ele
testa a magnitude corrente, e a confirmação histórica é o que decide entre
ATENÇÃO e CRÍTICO.

### 4. A guarda de viabilidade

Nenhum setor e nenhum subsetor fica zerado pelo veto na medição de hoje. A
guarda existe porque a regra não pode depender dessa medição.

`core/b3_quality_floor.py::apply_with_substitution` já preserva a vaga setorial
chamando o próximo do mesmo segmento, e deixa a vaga vazia com o motivo em
`log["sem_substituto"]` quando ninguém passa. Para o critério novo, e **apenas
para ele**, o comportamento no esgotamento muda: quando nenhum candidato do
segmento sobrevive à confirmação histórica, o critério é rebaixado de CRÍTICO
para ATENÇÃO naquele segmento e o líder entra **marcado**, com o motivo no log.

A carteira nunca encolhe por causa desta regra. No pior caso ela entrega o
líder do segmento com a etiqueta, que é o comportamento que
`core/us_quality_floor.py` já adotou pela mesma razão declarada — reprovar
Observação "esvaziaria a carteira inteira, não a tornaria mais seletiva".

O afrouxamento não se estende aos critérios pré-existentes de `check_holdings`:
eles mantêm o comportamento atual de vaga vazia.

### 5. Tela e contexto da LLM

- A tabela de Criação de Portfólio B3 passa a exibir "DY sustentável" ao lado
  do "DY divulgado", com a sustentabilidade histórica e o número de anos
  observados no tooltip. A coluna que decide e a coluna que aparece são a
  mesma.
- Empresa marcada pela guarda da seção 4 carrega a etiqueta e o motivo na
  linha, não apenas no log.
- O contexto da LLM entrega `payout_sustentabilidade`, `payout_mediano_hist` e
  o número de anos observados. Quando faltam anos, declara a ausência
  explicitamente em vez de omitir a linha.
- A red flag de patrimônio passa a citar a fração histórica, e o prompt ganha
  a regra de não tratar um exercício isolado como padrão da empresa.

### 6. Versão

`SCORE_VERSION` sobe de 2.25.0 para 2.26.0 em `core/b3_methodology.py`. A
composição da trilha `shareholder` mudou; manter a versão faria duas
metodologias diferentes responderem pelo mesmo número.

`MODEL_SCHEMA_VERSION` permanece em 3: o schema de saída não muda, só o
conteúdo das trilhas.

## Efeito medido

426 empresas, janela 2018–2025, delta em pontos de uma nota de 0 a 100.

| Grupo | n | Δ mediano |
|---|---|---|
| Payout TTM > 100% e DY > 8%, **padrão persistente** | 9 | **−3,3** |
| Payout TTM > 100% e DY > 8%, **episódio isolado** | 32 | −1,4 |
| Mesmo grupo, sem histórico suficiente | 4 | −4,2 |
| Demais empresas | 381 | +0,4 |
| Pagadoras saudáveis (DY > 5%, payout mediano 25–80%) | 72 | +0,2 |
| Quase-não-pagadoras (DY < 1%) | 17 | −1,3 |

Casos individuais:

| Ticker | Payout TTM | Payout mediano 8a | Anos | Δ nota | Portão |
|---|---|---|---|---|---|
| UNIP6 | 318% | 63,5% | 8 | −1,2 | passa |
| FESA4 | 193% | 57,5% | 8 | +0,1 | passa |
| WHRL4 | 178% | 161% | 6 | −3,5 | **vetado** (A e B) |
| TAEE11 | 218% | 195% | 7 | −6,7 | passa (só A) |
| BRBI11 | 298% | 282% | 6 | −7,0 | passa (só A) |
| CEBR3 | 140% | incoerente | — | −4,0 | passa |

A UNIP6 é o caso nomeado no docstring de `core/b3_quality_floor.py` como
motivação do módulo inteiro. O histórico a absolve, e isso é consequência
direta do princípio: julgar pelo histórico contradiz julgamentos feitos sobre o
período isolado.

CEBR3 cai −4,0 por ficar no neutro da trilha enquanto as demais sobem — perda
de convicção, não punição, que é o comportamento que o módulo já dá a dado
faltante.

TAEE11 e BRBI11 são penalizadas fortemente pela nota e passam pelo portão:
satisfazem A mas não B. É o desenho funcionando — o peso está na nota.

O efeito não é setorial: dentro de Energia Elétrica (42 tickers), apenas 14%
têm payout mediano acima de 100%.

## Testes

1. `sustentabilidade_do_ano` devolve zero em payout de 5% e em 130%, e 1,0 em
   25% e em 80% — as quatro fronteiras da faixa, com os valores exatos.
2. Empresa com um único ano de payout de 300% em oito, e os outros sete dentro
   da faixa, tem `payout_sustentabilidade` ≥ 0,85 — o episódio isolado não
   condena.
3. Empresa com payout acima de 130% em todos os anos tem
   `payout_sustentabilidade` igual a 0,0.
4. Exercício com `DY > 0,5%` e `Payout ≤ 1%` sai do cálculo como ausência: uma
   série de 4 anos com 1 incoerente produz `n_anos = 3`, não uma nota zero na
   média.
5. Menos de `MIN_ANOS` anos observados produz `NaN`, e o score coloca a empresa
   no `_NEUTRAL` da trilha — nunca em zero.
6. `dy_sustentavel` é `NaN` quando `payout_sustentabilidade` é `NaN`; nunca cai
   no `DY` bruto.
7. A tela de Empresas B3 e a Análise do Portfólio derivam o mesmo
   `payout_sustentabilidade` para as mesmas linhas.
8. O veto exige A **e** B: uma empresa que satisfaz apenas A (payout mediano
   alto, patrimônio estável) não é reprovada.
9. Quando todos os candidatos de um segmento satisfazem A e B, o líder entra
   marcado com o motivo no log — a vaga não fica vazia.
10. A guarda da seção 4 não afrouxa os critérios pré-existentes de
    `check_holdings`: um segmento cujos candidatos reprovam por FCO negativo
    continua deixando a vaga vazia.
11. A red flag de `dossie_b3` reporta a fração histórica; uma empresa com o
    padrão em 1 de 8 anos não recebe o mesmo texto de uma com 5 de 8.
12. `SCORE_VERSION` mudou, e nenhuma safra da 2.25.0 é aceita como validação da
    2.26.0.

## Fora de escopo

- **Empresas Americanas.** Ciclo próprio, spec próprio: diluição por emissão de
  ações (ausente de `build_entry_scores`) e as 702 saídas classificadas que
  `core/us_portfolio_creation.py` nunca consulta.
- `core/risk_logit.py` como portão ou como métrica de nota. Ele declara
  `risk_model_calibrated: False` no próprio retorno, e 46 empresas estão no
  quartil de risco mais baixo com metade ou mais das entradas ausentes, porque
  `distress_features` faz `.fillna(0.0)`. Além disso é função de seis métricas
  já presentes nas trilhas. Permanece como diagnóstico de tela.
- Calibrar `risk_logit` contra uma base rotulada de distress.
- Ampliar a cobertura histórica de `Payout` além dos 73,2% atuais.
- As demais red flags de `_checks`: quatro medem cobertura e não risco, e a de
  momentum mede um trimestre isolado.
- O descasamento entre `core/portfolio_db_analysis.py:100`, que roda o score
  sobre o universo inteiro, e `views/empresas_b3.py:3358`, que roda por
  segmento. É pré-existente, foi medido (efeito setorial fraco) e não é
  agravado por este spec.
