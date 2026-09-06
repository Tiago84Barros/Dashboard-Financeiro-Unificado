# Conjuntura: a porta de entrada dos motores de contexto

## O que este documento responde

Por que macro e noticiario passaram a aparecer na Analise Avancada, na Criacao
de Portfolio (B3 e EUA) e na Selecao de FIIs -- e por que, no dia em que este
texto foi escrito, eles **nao mexem em peso nenhum** e isso esta certo.

## O achado

Nenhum motor precisou ser escrito. Os quatro ja existiam e ja tinham teste:

| motor | o que entrega |
|---|---|
| `core.macro_data.portfolio_context` | impacto macro por ativo, point-in-time |
| `core.noticias` | relevancia, sentimento e direcao do item |
| `core.memoria_mercado.scores` | Score Conjuntural e as acoes que ele autoriza |
| `core.aporte` | o plano que consome bloqueio e prioridade |

O que faltava era o fio. `scores.para_aporte` devolve exatamente
`(bloqueios, prioridades)`; `aporte.plano_de_aporte` recebe exatamente
`bloqueios_conjunturais=` e `prioridades=`; a docstring de um cita o outro pelo
nome. As duas pontas estavam construidas, testadas e desligadas --
`para_aporte` so aparecia em testes. Motor que ninguem consulta na decisao e
decoracao.

`core/conjuntura/ponte.py` e esse fio.

## As duas medicoes que decidiram o desenho

### Macro: historico existe, captura historica nao

68.647 observacoes cobrindo 1947-2026. Mas `retrieved_at` tem **5 dias
distintos**, com 68.644 carimbadas no backfill de 03-04/09/2026, e `released_at`
existe em **3 linhas**. Rodando o carregador de verdade:

| corte | modo | cobertura | limitacao |
|---|---|---|---|
| 2015, 2020, 2024, 2026-06-30 | `strict` | 0,0% | nenhuma observacao era conhecida na data de corte |
| os mesmos | `reconstructed` | 100% (13 fontes) | historico reconstruido ex post |

**Consequencia:** ponderar peso historicamente e possivel so em modo
`reconstructed`, e o selo ex post tem que viajar junto ate a tela e ate o
prompt -- e viaja. Aplicar leitura macro de hoje a um rebalanceamento de 2020 e
vies de look-ahead, e o modo `strict` recusa fazer isso em vez de fingir.

### Noticias: o acervo nao existe

`noticias_itens` e `noticias_avaliacoes` sao criadas sob demanda por
`garantir_esquema()` e nunca foram criadas no Supabase. Zero itens, zero
historico. Por isso o componente de noticias entra hoje como **ausente**, nao
como neutro.

## Tres recusas

**1. Ausencia de noticia nao e noticia neutra.** Componente sem fonte sai do
denominador (`None`), nunca entra como `0.0`. Zero e uma leitura -- significa
"o noticiario esta equilibrado". Vazio nao significa nada.

**2. Falha de leitura tem que parecer falha.** Tabela ausente levanta
`AcervoIndisponivel` e vira limitacao declarada. Um `except` que devolvesse
acervo vazio publicaria "nada relevante aconteceu" toda vez que o banco caisse.
O bloco da LLM tem tres ramos mutuamente exclusivos -- itens citaveis, leitura
falhou, acervo genuinamente vazio -- e o texto do segundo manda explicitamente
nao escrever que nao houve noticias.

**3. Cobertura rala nao move peso.** `scores.conjuntural` exige
`COBERTURA_MINIMA = 0,50`. Com so o macro ligado (peso 0,20), `avaliar` devolve
`MANTER` com fator 1,00 e nada anda. Isso nao e efeito colateral a corrigir: e
o comportamento correto, e hoje e o comportamento real. O sistema fica ligado
dizendo o que lhe falta. No dia em que o coletor rodar, a cobertura passa a
0,55 e o mesmo codigo volta a decidir -- sem alterar uma linha.

## O teto

Bloquear dinheiro **novo** e reordenar prioridade entre quem continua elegivel.
Nada aqui vende: `scores.ACOES_QUE_REDUZEM_POSICAO` e um conjunto vazio com
teste que falha se alguem acrescentar algo. A proposta de ajuste sai como
proposta -- nenhuma operacao significativa e executada automaticamente.

## O grao, e por que o plano de aporte nao foi ligado

O unico `plano_de_aporte` em producao (`views/portfolio_global.py:1198`)
trabalha por **classe** (`renda variavel BR`, `FIIs`), e a ponte decide por
**ticker**. Passar um ao outro nao levantaria erro: as chaves nunca se
encontrariam, o bloqueio viraria no-op e a tela mostraria um plano "com
conjuntura" que ignora a conjuntura inteira.

Em vez de cometer isso, `para_plano_de_aporte` ganhou o parametro `universo`:
quando o chamador informa as chaves do plano e nenhuma delas encontra as da
conjuntura, sai `GraoIncompativel` na hora.

## O que falta, e nao e codigo

1. **Criar o esquema de noticias e rodar o coletor.** Sem isso o componente de
   maior peso (0,35) fica escuro e nada se move. Isso e escrita remota no
   Supabase e depende de autorizacao.
2. ~~Backtestar a camada macro.~~ **Medido em 05/09/2026** — ver a secao
   abaixo. O que falta agora nao e a medicao: e decidir o que fazer com ela.

## O tilt macro, medido

`scripts/backtest_macro_tilt.py` reusa `load_portfolio_macro_snapshot` (nunca uma
segunda implementacao de point-in-time) e roda 188 cortes mensais de 2011-01 a
2026-08 nas tres classes. Ele responde tres perguntas separadas, e as duas
ultimas continuam valendo depois de a primeira dar "nao".

**1. O impacto macro ordena retorno futuro? Nao, em nenhuma classe.**

| classe | ativos | vetores distintos | Rank-IC 1m | t | t Newey-West | acerto |
|---|---|---|---|---|---|---|
| B3  |  9 | 185 de 188 | +0,0065 | +0,25 | +0,25 | 49,7% |
| FII | 12 |  40 de 188 | +0,0487 | +1,21 | +1,07 | 51,5% |
| US  | 11 |  17 de 188 | −0,0048 | −0,21 | −0,21 | 48,7% |

A coluna que importa e a terceira. **N efetivo e o numero de vetores de impacto
distintos, nao o numero de cortes**: insumo anual lido todo mes repete o mesmo
vetor doze vezes. O arm dos EUA tem 17 opinioes diferentes em 188 meses.

O unico t que passou de 2 em qualquer horizonte foi o de 12 meses nos EUA
(−3,58) — e ele **e artefato de janela sobreposta**: Newey-West o leva a −1,61, e
as doze subamostras nao-sobrepostas vao de −2,30 a +0,54. Nao ha achado ali, nem
positivo nem negativo.

**2. `max_relative_weight_tilt = 0,15` nao e um limite — e decoracao.**

O multiplicador e `1 + impacto/100 × 0,15`, e o teto so seria alcancado com
`|impacto| = 100`. O maximo **observado em 188 cortes** foi 31,3 (US), 23,0 (FII)
e 20,7 (B3). O teto nunca mordeu, em nenhum mes, em nenhuma classe. Na pratica
ele move no maximo 3,1% a 4,7% de peso relativo, e o efeito na carteira e nulo:

```
B3   k=0,15  188 meses  +0,011%/ano  t_NW=+0,47
FII  k=0,15  111 meses  +0,011%/ano  t_NW=+1,04
US   k=0,15  188 meses  -0,005%/ano  t_NW=-0,61
```

Subir ou baixar 0,15 nao muda nada mensuravel. O que limita a magnitude nao e o
teto: e a escala do proprio impacto.

**3. `max_score_adjustment = 10` NAO e inerte, e essa e a assimetria.**

O mesmo par de priors que e inofensivo no peso e forte na ordenacao. Medido
contra a distribuicao real de nota da safra corrente da vitrine dos EUA (2.443
empresas, gap mediano entre posicoes adjacentes = 0,00):

| entrada | ajuste | posicoes deslocadas |
|---|---|---|
| p95 do impacto B3, modo `moderate`  | 1,76 pt | 216 (8,8% da tabela) |
| p95 do impacto FII, modo `scenario` | 2,93 pt | 337 (13,8%) |
| no teto (`10 × 1,5`)                | 15,00 pt | 1.161 (**47,5%**) |

A tabela e densa: um ponto de nota vale centenas de posicoes. Um ajuste no teto
atravessa metade do ranking.

**A conclusao nao e simetrica.** Nao ha evidencia de que o impacto ordene retorno
(item 1), e o ajuste que age sobre a ordenacao e o que tem tamanho (item 3). O
limite que deveria ser revisto para baixo e o **da nota**, nao o do peso.

**O que este backtest nao pode dizer.** `max_score_adjustment` nao e validavel
contra desfecho: nao existe serie historica de nota fundamentalista para comparar
"nota com macro" contra "nota sem macro" ao longo do tempo. O que esta medido
acima e **tamanho do efeito**, nao acerto. Dizer que o valor 10 foi validado
seria inventar a conclusao.

**Limitacoes declaradas da medicao**, todas no docstring do script:

- **Nao existe vintage de verdade.** `retrieved_at` so cobre 2026 — toda a
  historia entrou de uma vez. Cortes historicos leem valores **ja revisados**.
  Series nao revisadas (DGS10, FEDFUNDS, selic, cambio) nao sofrem; CPIAUCSL,
  UNRATE, GDPC1 e pib sofrem. `knowledge_mode="reconstructed"` e o mais honesto
  disponivel, nao o correto — em `strict` todo corte historico voltaria vazio.
- **Universo 100% sobrevivente.** Os ativos saem de `macro_portfolio_assets`, que
  e a carteira de hoje. Isso **inflaria** um resultado positivo e nao salva um
  resultado nulo, que e o que se encontrou.
- **A cadeia de priors e maior que os dois tetos.** As 28 linhas de
  `macro_sector_exposures` foram aprovadas num unico instante (2026-09-04
  00:14), a `sensitivity` sao 19 valores redondos em passo de 0,05, e a
  `confidence` e **constante 0,55 nas 28 linhas** — ou seja, nao discrimina nada.
- **A camada domestica e anual, com 17 pontos** (2010-12-31 a 2026-12-31, passo
  365 dias). E dela que vem o N efetivo baixo do FII.
- **Ha nove linhas com `reference_period = 2026-12-31` e `is_forecast = false`**
  em `macro_observations` (selic 14,0, cambio 5,157, ipca 3,44...). Hoje o filtro
  `reference_period <= as_of` as contem, mas e projecao gravada como observacao.
- **Preco da B3 so a partir de 2011.** Antes disso a serie tem meses inteiros com
  retorno exatamente zero (LEVE3: 85% dos meses em 2000) — serie parada, nao
  ativo sem volatilidade.

## O custo de ligar: um engine por montagem

`core.macro_data.database.get_local_macro_engine()` **não** é cacheado — ao
contrário de `core.database.get_engine()`, que carrega `@st.cache_resource`. Cada
chamada constrói um `Engine` novo com `pool_size=1, max_overflow=1`. Isso já era
verdade nas cinco telas que o chamam; a diferença é que esta porta de entrada
monta o bloco em **seis** pontos, e a versão por ativo roda uma vez por ativo do
contexto. Ligar sem fechar transformaria um detalhe tolerável em vazamento.

Medido contra o Postgres local, contando `pg_stat_activity`:

| cenário | conexões abertas |
|---|---|
| baseline | 1 |
| 12 engines criados e não descartados | **13** |
| 12 montagens de `bloco_para_prompt` | **1** |

Daí o `finally: macro.dispose()`. A assimetria é deliberada e está comentada no
código: o engine macro **nasce** dentro de `bloco_para_prompt`, então morre lá; o
do Supabase vem do cache do Streamlit e é compartilhado pelo app inteiro —
descartá-lo derrubaria as outras telas.

A alternativa seria cachear `get_local_macro_engine`. Não foi feita aqui de
propósito: mudaria o comportamento das cinco telas que já o chamam, em um PR cujo
assunto é outro, e o ganho sobre o `dispose` é de conexões por turno, não de
correção.
