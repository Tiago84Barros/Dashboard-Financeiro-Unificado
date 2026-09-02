# Memória de Mercado — fórmulas, premissas e limitações

> Quando um fato relevante aparece, o que os preços fizeram nas outras vezes em
> que um fato desse tipo aconteceu — e quanto essa referência ainda vale hoje.

O Motor Conjuntural (`core/noticias/`) responde *"o que aconteceu e quão
relevante é"*. Este módulo responde a pergunta seguinte, que é outra. Ele
**não** decide compra nem venda: o teto do que ele pode fazer é suspender
aporte **novo**, e isso é invariante testada, não promessa em comentário.

Este documento é a referência de metodologia. Cada número aqui é rastreável até
uma constante nomeada no código, e cada limitação foi medida, não estimada.

---

## 1. Mapa dos módulos

| Módulo | Responsabilidade |
|---|---|
| `core/memoria_mercado/serie.py` | calendário de pregões, janelas, **portão de densidade** |
| `core/memoria_mercado/benchmark.py` | índice de referência por ativo, retorno anormal |
| `core/memoria_mercado/retornos.py` | métricas de **um** evento (reação observada) |
| `core/memoria_mercado/amostra.py` | estatística sobre **vários** eventos comparáveis |
| `core/memoria_mercado/similaridade.py` | Fator de Similaridade do Cenário (0–100) |
| `core/memoria_mercado/estimativa.py` | amostra + similaridade → **faixa**, nunca ponto |
| `core/memoria_mercado/scores.py` | Score Estrutural × Score Conjuntural e ações permitidas |
| `core/memoria_mercado/calibracao.py` | backtest ponto-no-tempo e calibração dos pesos |
| `core/memoria_mercado/repositorio.py` | persistência **no armazém local**, nunca no Supabase |
| `core/memoria_mercado/ponte_noticias.py` | conversão para `core.noticias.impacto.BaseHistorica` |
| `scripts/construir_memoria_mercado.py` | único ponto que abre o armazém `dfu_warehouse` |

`core/` não abre o armazém local por conta própria: a engine chega por
parâmetro. O repositório apenas sabe **recusar** um destino errado; escolher o
certo é do script.

---

## 2. Convenção de tempo

`t = 0` é o primeiro pregão **em ou após** a data do evento. O retorno de `h`
pregões vai do fechamento de `t=0` ao fechamento de `t=h`.

Consequência deliberada: o retorno de 1 pregão **exclui** a sessão em que o fato
foi divulgado durante o pregão. Incluí-la exigiria a hora exata da divulgação,
que boa parte das fontes não fornece — e errar essa hora para trás é vazamento
de informação para dentro da medição.

---

## 3. Portão de densidade — por que existe

Medição feita no armazém local **antes** de qualquer linha de código:

| tabela | linhas | datas distintas | leitura |
|---|---:|---:|---|
| `market_us.prices_daily` | 13.342.783 | 16.267 | diária de fato |
| `market.fii_b3_security_history` | 606.552 | 4.099 | diária de fato |
| `market.b3_security_history` (ações B3) | 1.627.752 | 4.134 | diária de fato |

A terceira linha era `market.historical_prices`: 137.735 linhas em **1.542**
datas cobrindo 2000–2026, ou ~24 pregões por ano até 2013 — série mensal. Somar
um índice nessa série e chamar o resultado de "retorno em 1 pregão" devolve, na
prática, o retorno de duas semanas — sem erro, sem aviso, com o rótulo errado.

Em **02/09/2026** a série diária de ações da B3 passou a existir, ingerida do
COTAHIST oficial da bolsa (`data_pipeline/market/b3_precos.py`), e a Memória de
Mercado foi repontada para ela. O portão continua com os mesmos limiares: quem
mudou foi a série, não o critério. Medido em PETR4, VALE3, ITUB4, WEGE3 e MGLU3
num evento de 15/03/2024, a série antiga reprovava até 63 pregões; a nova aprova
1, 5, 21 e 63. O portão segue reprovando símbolo de cobertura rala — papel
recém-listado ou com suspensão longa —, que é para o que ele serve.

```
densidade(i, h) = pregões observados na janela
                  ---------------------------------------------------------
                  dias_corridos * PREGOES_POR_DIA_CORRIDO - TOLERANCIA_PREGOES
```

* `PREGOES_POR_DIA_CORRIDO = 252 / 365,25` — mesma convenção de
  `core.us_backtest.performance_stats`.
* `DENSIDADE_MINIMA = 0,60` — abaixo disso a métrica sai `None`, não sai errada.
* `TOLERANCIA_PREGOES = 1,0` — uma sessão de folga no denominador. Existe por
  medição: numa série diária perfeita, uma janela de 1 pregão iniciada numa
  **sexta-feira** ocupa 3 dias corridos, "esperaria" 2,07 pregões e sairia com
  densidade 0,48 — reprovada. Sem a folga, perderíamos o horizonte de 1 pregão
  de todo evento de sexta, ~20% da amostra, sem nenhum erro aparecer. A folga é
  constante e some nas janelas longas (1,2% em 60 pregões), então não afrouxa o
  que o portão existe para pegar: a série da B3 fica abaixo de 0,15 em qualquer
  horizonte.

---

## 4. Métricas por evento

Horizontes exigidos: **1, 5, 20 e 60 pregões** (`HORIZONTES`).

| Requisito | Campo | Fórmula |
|---|---|---|
| retorno em 1/5/20/60 pregões | `janelas[h].retorno_ativo` | `P(t=h)/P(t=0) − 1` |
| retorno do índice de referência | `janelas[h].retorno_benchmark` | idem sobre o índice |
| retorno do índice setorial | `janelas[h].retorno_setorial` | idem sobre o setorial |
| retorno anormal | `janelas[h].retorno_anormal` | ver §5 |
| volatilidade | `volatilidade_pre`, `volatilidade_pos`, `razao_volatilidade` | desvio dos retornos diários, 60 pregões antes × depois |
| volume | `volume_medio_pre/pos`, `razao_volume` | média de volume, mesma janela |
| drawdown | `drawdown` | menor `P(t)/P(t=0) − 1` em até 120 pregões |
| tempo até o pior ponto | `pregoes_ate_o_pior` | argmin do acima |
| tempo de recuperação | `pregoes_ate_recuperar` | 1º pregão com `P(t) ≥ P(t=0)` |
| persistência ou reversão | `persistencia` | ver abaixo |

**Persistência.** Compara o movimento âncora (5 pregões) com o horizonte longo
(60): `PERSISTENTE` quando sobrevive ao menos `FRACAO_PERSISTENCIA = 0,50` do
movimento inicial; `REVERSAO` quando o sinal se inverte; `REVERSAO_PARCIAL` no
meio. Movimento âncora abaixo de `LIMIAR_MOVIMENTO = 0,01` sai como
`SEM_MOVIMENTO` — classificar ruído produziria uma taxa de reversão de 50% que
não mede nada.

**`None` nunca é zero.** Todo campo pode ser `None`, e `None` significa *não
medido*. Colapsar isso em `0.0` é o defeito de
`memoria: medicao-que-pune-a-evidencia`: numa média renormalizada, `None` é
neutro e `0.0` é punitivo. Os motivos de ausência são separados de propósito —
`MOTIVO_FORA_DA_SERIE` (histórico curto, resolve esperando) e `MOTIVO_ESPARSA`
(série sem densidade diária, não resolve nunca).

---

## 5. Retorno anormal

Separar o movimento do ativo do movimento do mercado. Dois modelos:

```
MODELO_DIFERENCA   AR = r_ativo − r_indice                    (beta 1, alfa 0)
MODELO_MERCADO     AR = r_ativo − (alfa + beta * r_indice)    (MQO pré-evento)
```

* Padrão: `MODELO_DIFERENCA`. Não precisa de janela de estimação, logo continua
  funcionando para ativo recém-listado — exatamente o caso em que o outro
  falharia calado.
* `MODELO_MERCADO`: janela de `JANELA_ESTIMACAO = 120` pregões terminando
  `INTERVALO_ANTECEDENCIA = 5` pregões **antes** do evento (para que vazamento
  pré-divulgação não contamine o contrafactual); mínimo de
  `PREGOES_MINIMOS_ESTIMACAO = 60` pontos; beta aceito apenas em `[−1,0; 4,0]`.
* **Degradação declarada**: pedido `MODELO_MERCADO` sem janela suficiente, a
  função *não* devolve `None` — cai para a diferença simples e escreve a troca
  em `limitacoes`. Devolver `None` jogaria fora um número utilizável; trocar em
  silêncio publicaria um número com o rótulo do outro.
* Evolução: `retorno_anormal()` recebe o modelo como parâmetro e não há nenhum
  `if` de modelo fora de `benchmark.py`. O degrau natural é multifator —
  `core/ff_risk_model.py` já existe no repositório.

### 5.1 Benchmark ausente é o caminho quente, não a exceção

Medido no armazém: SPY e QQQ têm **9 linhas cada**; BOVA11, 220; IFIX, 133.
Não existe série de índice utilizável. O default é o **índice equiponderado
sintético** construído do próprio painel (mínimo de 20 ativos por pregão),
marcado com `FONTE_SINTETICA` e propagado até o evento em
`benchmark_sintetico`. Sem painel largo o bastante, não há índice: o retorno
anormal sai `None` e a amostra cai para **retorno bruto**, dizendo que caiu.

---

## 6. Amostra histórica

> *"Não use apenas o evento passado mais conhecido."* O caso mais lembrado é o
> mais enviesado — é lembrado justamente por ter sido extremo.

Três patamares, os três visíveis na saída:

| `n` | comportamento |
|---|---|
| `n < 8` | **nenhuma faixa publicada**. Com 5 observações a mediana troca de sinal se uma sair, e p10–p90 é literalmente mínimo e máximo |
| `8 ≤ n < 30` | faixa publicada e marcada **experimental**, confiança reduzida. O exemplo do próprio enunciado (8 eventos) cai aqui |
| `n ≥ 30` | faixa publicada sem a marca (`N_MINIMO_ROBUSTO`, mesmo piso de `core.noticias.impacto.N_MINIMO_BASE`) |

O motor de notícias exige 30 porque publica probabilidade ao lado de uma
manchete; a Memória de Mercado aceita 8 porque publica uma faixa **marcada como
experimental**. A diferença é sobre o que cada um faz com o número — colapsar os
dois patamares perderia informação nas duas direções. `ponte_noticias.descrever`
explica essa diferença em texto quando ela morde.

**Estatística de posição, não de significância.** Mediana e percentis mandam na
estimativa; média e desvio ficam publicados ao lado porque o requisito pede.
Reação a evento tem cauda pesada: uma aquisição com prêmio de 60% desloca a
média de uma amostra de 12 e não desloca a mediana.

**Cobertura de retorno anormal.** Abaixo de `COBERTURA_MINIMA_ANORMAL = 0,60` a
amostra passa a ser descrita como amostra de retorno **BRUTO** — que é uma
amostra pior e precisa aparecer assim (`memoria: procedencia-segue-a-decisao`).

**Exclusão declarada.** Evento sem o horizonte medido sai da amostra e a
exclusão é escrita ("1 de 11 eventos sem o horizonte de 60 pregões"). Um evento
recente sem 60 pregões de futuro **não** é uma reação nula em 60 pregões —
`memoria: foto-truncada-vira-evidencia`.

---

## 7. Fator de Similaridade do Cenário (0–100)

15 dimensões, pesos-prior somando 1,00:

| Dimensão | Peso | Comparador | Escala |
|---|---:|---|---:|
| `tipo_evento` | 0,20 | rótulo | — |
| `intensidade_evento` | 0,10 | distância | 1,00 |
| `juros_br` | 0,07 | distância | 8,00 p.p. |
| `juros_us` | 0,05 | distância | 4,00 p.p. |
| `inflacao` | 0,05 | distância | 6,00 p.p. |
| `cambio` | 0,05 | razão | 0,50 |
| `commodity` | 0,04 | razão | 0,60 |
| `valuation` | 0,08 | razão | 0,60 |
| `endividamento` | 0,07 | distância | 1,50 |
| `expectativa_lucro` | 0,06 | razão | 0,50 |
| `liquidez` | 0,05 | razão | 1,00 |
| `volatilidade` | 0,06 | razão | 0,60 |
| `politico_regulatorio` | 0,04 | rótulo | — |
| `situacao_setorial` | 0,04 | rótulo | — |
| `parcela_ja_precificada` | 0,04 | distância | 1,00 |

```
por distância:  v = max(0, 1 − |hoje − histórico| / escala)
por razão:      v = max(0, 1 − |hoje/histórico − 1| / escala)     (histórico ≠ 0)
por rótulo:     v = 1,0 se igual, 0,0 se diferente

fator = 100 * Σ(peso_i * v_i) / Σ(peso_i)      — só sobre dimensões MEDIDAS
```

* **Dimensão ausente sai do denominador.** Não é creditada nem debitada. Câmbio
  com referência histórica zero devolve razão indefinida e sai medido como
  ausente, com motivo escrito.
* `COBERTURA_MINIMA = 0,40`: abaixo disso o fator é **publicado mas não
  utilizável** — ele aparece na tela com a cobertura ao lado e não ajusta nada.
* `SIMILARIDADE_INVALIDANTE = 25,0`: abaixo disso a comparação inteira é
  declarada inválida.
* **Tipo de evento diferente invalida separadamente do fator.** 80/100 de
  similaridade macro entre uma fusão e um resultado trimestral não torna os dois
  comparáveis — por isso o invalidante existe fora do número.
* `cenario_medio()` usa mediana (numéricas) e moda com desempate **alfabético**
  (categóricas): sem o desempate, a ordem de chegada mudaria o resultado
  (`memoria: determinismo-carteira-b3`).

---

## 8. A estimativa — faixa, nunca ponto

Sejam `m` a mediana histórica do retorno anormal no horizonte, `s` a
similaridade em fração, `p` a parcela já precificada e `n` o tamanho da amostra:

```
atenuacao      = ATENUACAO_PISO + (1 − ATENUACAO_PISO) * s        (piso 0,50)
central        = m * atenuacao * (1 − p)
semiamplitude  = ((p75 − p25) / 2) * atenuacao * (1 − p)
                 * (1 + K_ALARGAMENTO / sqrt(n))                  (K = 1,0)
faixa          = centro ± semiamplitude
```

* Similaridade 0 **reduz à metade**, não a zero. Zerar afirmaria que cenários
  pouco parecidos garantem reação nula — afirmação mais forte que a evidência.
  Similaridade baixa demais não vira número pequeno: vira recusa (§7).
* `p = 1` produz central zero e direção **neutra**: é a leitura correta de "o
  mercado já sabia disso". Com `p ≥ 0,70` a confiança é rebaixada, porque o que
  resta para reagir é pequeno e a estimativa fica frágil.
* O alargamento é o preço da amostra pequena: `n = 8` abre a faixa 35%,
  `n = 100` abre 10%. Uma faixa larga a ponto de ser pouco acionável é
  exatamente o que uma amostra rala produz.
* Sem similaridade informada, assume o meio (`atenuacao = 0,75`) e **declara a
  omissão**.

**Conferência contra o exemplo do enunciado**: mediana −6,4%, similaridade 74%
→ `atenuacao = 0,87`, central ≈ −5,6%, dentro da faixa −3% a −7% do exemplo.

**Horizonte** sai de p25–p75 de `pregoes_ate_o_pior` observado — é uma medida,
não uma convenção. Sem esse dado, cai para metade-a-total do horizonte da
amostra e registra a limitação.

**Confiança** = quantas das cinco condições são atendidas (5 → alta, 3–4 →
média, ≤2 → baixa): amostra robusta; retorno **anormal** e não bruto;
similaridade ≥ 60; cobertura das dimensões ≥ 0,60; consistência de sinal
(≥70% dos eventos no mesmo lado).

**Condições que invalidam a comparação** (publicam a faixa mas desautorizam a
ação): tipo de evento diferente; amostra de um único ativo (histórico não-padrão
de mercado); amostra concentrada em menos de 12 meses (é um regime só);
similaridade abaixo do invalidante.

**Os onze campos exigidos** saem todos: faixa, valor central, horizonte,
direção, `n`, similaridade, confiança, fatores que ampliam, fatores que reduzem,
condições que invalidam, intervalo histórico. `Estimativa.acionavel` só é `True`
com faixa publicada, sem condição invalidante e com direção definida — e
`acionavel` **não** quer dizer confiável: uma estimativa experimental pode ser
acionável, e por isso `experimental` e `confianca` viajam ao lado.

---

## 9. Os dois scores

| | Score Estrutural | Score Conjuntural |
|---|---|---|
| escala | **0 a 100** | **−100 a +100** |
| componentes | fundamentos 0,30 · valuation 0,25 · qualidade 0,20 · vantagem competitiva 0,15 · risco de longo prazo 0,10 | notícias 0,35 · memória de mercado 0,30 · macro 0,20 · técnico 0,15 |
| papel | **forma a carteira** | ajusta prioridade de aporte |

As escalas são diferentes **de propósito**: para que somar os dois seja
obviamente errado ao olhar. Score conjuntural é *desvio*, não nota. Se as duas
fossem 0–100, alguém acabaria escrevendo `0,7*estrutural + 0,3*conjuntural` e a
carteira passaria a ser formada por manchete.

Componente ausente sai do denominador nos dois scores; abaixo de
`COBERTURA_MINIMA = 0,50` o score é publicado e **não sustenta decisão**.

### 9.1 As sete ações — e as que não existem

`manter`, `priorizar_aporte`, `reduzir_prioridade_aporte`, `observar`,
`suspender_aporte`, `reavaliar_fundamentos`, `oportunidade_gradual`.

`ACOES_QUE_REDUZEM_POSICAO` é um **frozenset vazio**, e é documentação
executável: qualquer ação futura que reduza posição teria de entrar ali, e o
teste que lê esse conjunto falharia. A ação mais severa, `suspender_aporte`,
impede **dinheiro novo** e deixa o que já está comprado onde está.

| Score conjuntural | Ações |
|---|---|
| `< −60` | suspender aporte + observar + reavaliar fundamentos |
| `−60 … −25` | reduzir prioridade + observar |
| `−25 … +25` | manter (ruído) |
| `> +25` | priorizar aporte |
| `> +40` **e** estrutural ≥ 60 **e** queda ≤ −10% | oportunidade gradual |

O piso estrutural na última linha impede que "caiu muito" vire motivo de compra
em empresa ruim — que é como se compra armadilha de valor.

Prioridade de aporte é multiplicador em `[0,50; 1,50]`. **Nunca zero**: o
bloqueio é expresso por `bloqueia_aporte`, não por prioridade zero, para que as
duas coisas continuem distinguíveis na tela e no log.

`reavaliar_fundamentos` é a saída deliberada para o humano. O módulo não altera
o score estrutural por conta própria — mudar fundamento por notícia seria deixar
a manchete formar carteira pela porta dos fundos.

### 9.2 Integração com `core/aporte.py`

`plano_de_aporte(..., bloqueios_conjunturais=None, prioridades=None)`. Sem os
dois parâmetros novos, o plano é idêntico ao de antes (teste dedicado). Com
bloqueio, o dinheiro é **redistribuído** entre os demais e nada é vendido.

Limite real, medido: a cascata é limitada pelo **déficit** de cada ticker (peso
alvo × patrimônio depois − valor atual). Bloquear um ticker só redistribui se o
déficit somado dos outros exceder o aporte; caso contrário o dinheiro vira
`sobra`. Pelo mesmo motivo, `prioridades` só reordenam quando o aporte é menor
que a soma dos déficits elegíveis.

---

## 10. Backtest e calibração

> *"Não fixe pesos arbitrários como definitivos."*

Todos os pesos deste módulo são **priores declarados** — escritos com o motivo
ao lado, e nem por isso corretos. `ScoreEstrutural.calibrado`,
`ScoreConjuntural.calibrado` e `Similaridade.pesos_calibrados` dizem qual dos
dois está em uso, e o `False` viaja até a tela.

**`walk_forward` é ponto-no-tempo.** O evento de índice `i` é estimado usando
**apenas** os anteriores a ele. Montar a amostra com todos e depois "testar"
nela mede o quanto a mediana descreve os dados que a produziram — que é sempre
excelente e não significa nada. Empates de data desempatam por chave.

Quatro medidas:

| Medida | O que diz |
|---|---|
| `cobertura_faixa` | fração de realizados dentro da faixa. Alvo `COBERTURA_ALVO = 0,60`. **> 90%** → faixa larga demais para decidir; **< 35%** → precisão falsa |
| `acerto_direcional` | acerto de sinal, contado só onde os dois têm direção |
| `mae` | erro absoluto médio do central |
| `mae_referencia` | o mesmo erro para a mediana histórica **sem** ajuste de similaridade |

`ganho_sobre_referencia = (mae_referencia − mae) / mae_referencia`. Sem essa
comparação o `mae` é um número sozinho: um mecanismo de ajuste que não é medido
contra a alternativa de não ajustar nada é decoração
(`memoria: diagnostico-precisa-porta-de-entrada`). O `vies` sai ao lado do
`mae` porque erros que se cancelam e erros sistemáticos dão o mesmo `mae`.

**Calibração dos pesos de similaridade.** Hipótese falsificável por dimensão:
*se esta dimensão mede algo, mais similaridade nela deveria significar menos
erro* — isto é, correlação entre a similaridade da dimensão e `−|erro|`. Três
recusas deliberadas:

1. menos de `N_MINIMO_BACKTEST = 20` casos devolve o prior com
   `calibrado=False` (é o caminho normal **hoje**);
2. dimensão com menos de 3 pares mensuráveis não entra;
3. nenhuma correlação positiva devolve o prior — e isso é registrado como
   evidência **contra** o próprio Fator de Similaridade, não a favor dele.

Encolhimento: `lam = n / (n + N_ENCOLHIMENTO)`, com `N_ENCOLHIMENTO = 30`. Com
20 casos o calibrado vale 40%; com 200, 87%. Trinta observações não reescrevem
pesos inteiros.

---

## 11. Persistência — armazém local, nunca Supabase

Instrução literal desta entrega: *"salve-as no banco de dados local e nunca no
Supabase, ele já está quase no limite."* O Supabase estava em **425 MB de 500
MB** em 01/09/2026, e um evento medido gera dezenas de campos por horizonte por
versão de metodologia.

A regra é código, não documentação: `exigir_local()` levanta
`DestinoRemotoRecusado` **antes** de qualquer `INSERT` quando o host não está em
`HOSTS_LOCAIS` ou a URL contém fragmento de provedor gerenciado
(`supabase.co`, `pooler.supabase`, `neon.tech`, `amazonaws.com`, …). A engine é
parâmetro **obrigatório**: não existe `get_engine()` de reserva aqui, porque a
engine padrão do repositório aponta para o Supabase e um default transformaria
esquecer um argumento em gravar no lugar proibido.

Tabelas em `memoria_mercado.eventos_medidos` e `memoria_mercado.cenarios`, ambas
com **`PRIMARY KEY (versao_metodologia, chave)`** — safras de versões diferentes
coexistem (`memoria: versao-de-metodologia-sem-safra`). `limpar_tipo()` apaga
por tipo **sem filtrar versão**: um `DELETE` escopado pela versão corrente
deixaria a safra antiga fora de alcance para sempre
(`memoria: remocao-escopada-pelo-filtro-da-leitura`).

Credencial nunca aparece: a URL é renderizada com `hide_password=True` antes de
qualquer log ou retorno, e a senha do armazém sai do `docker inspect
dfu_warehouse`, nunca do código.

### Como construir a base

```bash
python scripts/construir_memoria_mercado.py --mercado us --eventos eventos.json
```

```bash
python scripts/construir_memoria_mercado.py --mercado fii --do-banco-de-noticias
```

```bash
python scripts/construir_memoria_mercado.py --mercado b3 --dry-run
```

Ler do banco de notícias é legítimo; **gravar** lá não é. O relatório de saída
carrega `fonte_precos`, `serie_diaria`, `indice_sintetico`,
`sem_serie_de_precos` e `sem_pregao_na_data` — falta de dado sai contada, não
vira evento medido a partir de nada.

---

## 12. Limitações conhecidas — o que este módulo **não** entrega hoje

Esta seção existe para envelhecer bem. Todo item aqui foi medido; nenhum é
precaução genérica. `memoria: aviso-que-envelhece-invertido` registra o custo de
um aviso de limitação que virou falso e continuou soando como rigor.

1. ~~**Ações da B3 não têm horizonte curto.**~~ **Resolvido em 02/09/2026.** Era
   a limitação mais cara da lista: com 1.542 datas em 26 anos, o portão de
   densidade reprovava 1, 5 e frequentemente 20 pregões, e para a B3 o módulo
   respondia `None` quase sempre. Foi resolvida como a própria nota previa —
   ingerindo preço diário no armazém, **sem tocar no portão**: 1.627.752 linhas
   e 4.134 pregões (2010-01-04 a 2026-09-01) vindas do COTAHIST oficial. Fica
   registrada em vez de apagada, porque a série começa em **2010**: evento
   anterior a isso continua sem horizonte curto, e aí o `None` é a resposta
   certa. Cobertura de anos anteriores exige baixar os arquivos de 1986-2009.
2. **O índice de referência é sintético.** Não há série de índice utilizável no
   armazém (SPY/QQQ: 9 linhas; BOVA11: 220; IFIX: 133). O equiponderado do
   próprio painel é uma aproximação: ele não é o índice que o mercado olha, e
   herda o viés de composição do painel disponível. Vai marcado em cada evento.
3. **`market.macro_indicators` está vazia.** As dimensões macro do Fator de
   Similaridade (juros BR/US, inflação, câmbio, commodity) dependem de cenário
   fornecido por quem chama. Sem ele, saem **ausentes** — fora do denominador,
   com a cobertura publicada — e nunca preenchidas com um valor plausível.
4. **Nenhum peso está calibrado.** Não há base de backtest com 20 casos ainda.
   Todo `calibrado` é `False` hoje, e a tela precisa continuar mostrando isso.
5. **O modelo de retorno anormal é de mercado, não multifator.** Beta 1 no
   default. Para ativo de beta muito distante de 1, parte do "anormal" é
   exposição a mercado. `MODELO_MERCADO` corrige parcialmente; multifator é o
   próximo degrau e a porta está aberta.
6. **`p` (parcela já precificada) é entrada, não medição.** O módulo desconta
   linearmente o que lhe informarem. Ele não estima quanto o mercado já
   precificou — isso exigiria janela pré-evento de retorno anormal e uma
   hipótese sobre vazamento.
7. **A estimativa não é recomendação.** Faixa com procedência, direção e
   confiança. `acionavel` autoriza no máximo ajustar prioridade de aporte.

---

## 13. Testes

Cinco arquivos, cobrindo os nove cenários exigidos:

| Arquivo | Cenários do requisito |
|---|---|
| `tests/test_memoria_mercado_serie.py` | benchmark ausente · retorno anormal · dados incompletos |
| `tests/test_memoria_mercado_amostra.py` | amostra suficiente e insuficiente · evento sem equivalente histórico · reversão · impacto persistente |
| `tests/test_memoria_mercado_similaridade.py` | regimes macroeconômicos diferentes |
| `tests/test_memoria_mercado_estimativa.py` | notícia já precificada |
| `tests/test_memoria_mercado_scores.py` | invariante de não-liquidação · integração com o aporte |
| `tests/test_memoria_mercado_calibracao.py` | backtest ponto-no-tempo · calibração |
| `tests/test_memoria_mercado_repositorio.py` | destino local × Supabase · script construtor |

Séries **sintéticas e determinísticas** (`tests/apoio_memoria.py`): o ruído vem
de `sin` sobre o índice do pregão, nunca de RNG. Um teste apoiado no dado real
mediria a cobertura do armazém, não o código, e mudaria de resultado a cada
ingestão. Nenhum teste abre conexão — `tests/conftest.py` recusa socket fora do
loopback.

```bash
python -m pytest tests/test_memoria_mercado_*.py -q
```
