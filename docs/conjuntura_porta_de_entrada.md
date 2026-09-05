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
2. **Backtestar a camada macro.** `_simular_seg_backtest` valida o motor
   fundamentalista; o tilt macro e aplicado **depois** dele. Os limites
   (`max_score_adjustment=10`, `max_relative_weight_tilt=0,15`) sao priors sem
   procedencia medida.

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
