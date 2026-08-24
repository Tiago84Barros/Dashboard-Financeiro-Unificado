# Objetivo: App 4 como analista profissional de portfólios

> Aberto em 2026-08-23. Livro-razão vivo — atualizado a cada item fechado.
> Critério herdado de `.claude/skills/profissionalizar-app4/SKILL.md` §6.

## O que conta como "fechado"

Não é "o código está correto". É **a fórmula responde à pergunta que o analista
está fazendo, e quem usa o app vê o resultado disso.** Um achado só fecha quando:

1. existe teste que falha sem a correção;
2. a correção foi verificada **executando** contra dado real, não só em teste
   sintético (o A-105 só apareceu assim);
3. o efeito chega a **produção** — código na `main` não basta quando a tela lê
   valor pré-computado de vitrine;
4. a limitação que sobra está escrita onde o usuário do app a lê, não só no vault.

## Estado dos itens

| ID | Item | Estado | Prova |
|---|---|---|---|
| A-101 | Razão com denominador de sinal variável (B3 + EUA) | Código ✅ / Produção: B3 ✅, EUA ❌ | `tests/test_score_sinal_de_denominador.py`; medido: 32 das 100 "mais baratas" tinham EV/EBIT negativo |
| A-102 | ROE em duas trilhas, premiando alavancagem | ✅ | `test_roe_nao_conta_duas_vezes_na_eficiencia_de_capital` |
| A-103 | Cobertura parcial com convicção cheia; badge "Neutra" sem pares | ✅ | `test_trilha_com_meia_cobertura_...`, `test_sem_pares_apurados_...` |
| A-105 | Prejuízo apagado pela fonte ranqueando como barato | ✅ | `test_prejuizo_apagado_pela_fonte_nao_vira_ausencia_neutra`; 53,4→42,5 |
| SCORE-01 | Vitrine EUA republicada | 🔴 **aberto** | Vitrine viva é de 03/08, `score_version` 0.5.0, ranqueador antigo, 1.111 com `decision_grade` |
| SCORE-02 | App não diz que os três motores não são comparáveis | ✅ código / 🟡 produção | `tests/test_aviso_escala_do_score.py`; o painel Global já dizia, as três abas individuais não |
| SCORE-03 | A-104: correlação com janelas de 32 e 556 meses | 🟡 decisão do usuário | — |
| SCORE-04 | `P/VP` e `P_FCO` sem proxy de sinal na B3 | 🟡 limitação aceita | documentada em `core/b3_company_score.py` |
| A-106 | FII: cobertura parcial encolhia a nota para ZERO, não para o neutro | ✅ | `tests/test_fii_encolhimento_por_cobertura.py`; inversões entre os `ready` caíram de 3,8% para 0,54% |
| A-107 | FII: ingestão e validador PIT calculavam o crescimento de renda por fórmulas diferentes | ✅ | `tests/test_fii_crescimento_de_renda_definicao_unica.py`; Spearman entre as duas era 0,765 |
| A-108 | FII: P/VP simétrico pune desconto como ágio | ⚪ **não procede** | Os extremos (0,019 e 18,34) já são barrados como `insufficient`; os 11 que passam ficam todos abaixo da mediana |
| PIT-6.8 | Walk-forward revalidado para a metodologia 6.8.0 | ✅ local / 🔴 produção | Run 52 `passed`: 44 períodos, excesso médio +0,167% a.m., drawdown máx. −12,7%, zero bloqueadores. Gravado no armazém LOCAL; o Supabase ainda tem só o certificado 6.7.0 |
| FII-Q | Passar a pergunta "a fórmula responde ao que se pergunta?" no motor de FIIs | 🟢 rodada feita | 2 defeitos achados e fechados (A-106, A-107), 1 descartado (A-108) |
| A-109 | Global: covariância par a par pode não ser positiva semidefinida | ⚪ **não procede** (invariante travado) | `tests/test_global_covariancia_psd.py`; medido: 10% das carteiras com séries desalinhadas, pior caso −26,2% vs +3,5% de contribuição ao risco |
| GLOB-Q | Idem no Portfólio Global | 🟢 rodada feita | 0 defeitos vivos, 1 descartado (A-109) com guarda de regressão |
| A-110 | Markowitz: intensidade de shrinkage somava a diagonal no numerador e não no denominador, cravando α = 1 (correlações zeradas) | ✅ | `tests/test_markowitz_shrinkage_suporte.py`; medido em 178 cestas reais da B3: α cravado em 1,000 em 121 → 6; peso muda até 14,93 pp |
| CONSTR-Q | Idem no módulo de construção e rebalanceamento de carteira | 🟢 rodada feita | 1 defeito achado e fechado (A-110); cadeia de solver e exibição de não-convergência já íntegras |
| A-111 | Global: VaR e CVaR de 95% exibidos lado a lado repousando sobre 1 observação, com promessa de "1 a cada 20 meses" numa série de 18 | ✅ (declaração) | `tests/test_global_var_cauda_declarada.py`; no piso de 18 meses o CVaR é idêntico ao pior mês em 100% de 200 carteiras |
| A-112 | Sortino usava a dispersão das perdas em torno da média delas, não o desvio contra o alvo | ✅ | `tests/test_sortino_downside_deviation.py`; 1,20× o padrão na mediana (até 1,75×), exagerado em 222 de 300 carteiras |
| A-113 | "Sharpe" exibido com taxa livre de risco zero | ✅ (declaração) | cartão passa a dizer `Sharpe (rf = 0)`; correção real exige série de Treasury no pipeline |
| A-116 | Backtest EUA apagava do painel a ação que parou de negociar — viés de sobrevivência puro | ✅ | `tests/test_us_panel_sobrevivencia.py`; cesta de duas ações (+30% e −80% com deslistagem) saía como **+30,0%** em vez de −25,0% |
| A-117 | Horizonte elástico: o primeiro preço após o alvo, ainda que 7 anos depois, virava "retorno de 12 meses" | ✅ | idem; +300% de 84 meses rotulados como 12m, e +100% de 11 meses rotulados como retorno mensal |
| BACKTEST-Q | Idem na evidência histórica — o retorno exibido era alcançável naquela data? | 🟢 rodada feita | 2 achados, **os primeiros que enviesavam para CIMA** |
| A-118 | FII: fundo escolhido que liquidou tinha o peso redistribuído entre os sobreviventes — o dinheiro do que sumiu rendia o que os OUTROS renderam | ✅ | `tests/test_fii_pit_saida_de_campo.py`; cesta de 3 fundos: **−6,46% virava +10,49%** quando o perdedor liquidava. Inversão de sinal |
| A-119 | B3: o Rank-IC lia o preço de UMA data fixa em cada ponta; quem não negociou naquele pregão — e quem deslistou no meio do ano — saía do teste | ✅ | `tests/test_b3_ic_sobrevivencia.py`; **444 empresas-ano (7,5%)** descartadas no painel real, e as recuperadas rendem menos que as incluídas em 9 dos 11 anos |
| A-120 | B3: `tail(12)` pega as 12 últimas *observações*, não os 12 últimos *meses* | ✅ | `tests/test_b3_retorno_12m_janela.py`; FSTU11 exibia **76 meses** rotulados "retorno 12m" |
| DADO-Q | Idem na camada que alimenta B3 e FII | 🟢 rodada feita | 3 achados (A-118 FII, A-119/A-120 B3); **todos enviesavam para cima** |
| A-114 | Rebalanceamento por banda e híbrido não enxergavam a saída de posição: o laço varria só o alvo, e o ticker que saiu não tem chave lá | ✅ | `tests/test_rebalancing_saida_de_posicao.py`; sair inteiro de 30% media desvio 0,0 e devolvia "não precisa mexer" |
| A-115 | Advisor compara custo contra o **tamanho** da ordem, não contra o benefício dela | ⚠️ decisão sua | `core/global_portfolio/advisor.py`; aprova movimento grande de pouco valor e barra movimento pequeno de muito valor |
| REBAL-Q | Idem na ação que o app recomenda — rebalanceamento e custos | 🟢 rodada feita | 2 achados (A-114 latente e corrigido, A-115 metodológico) |
| RISCO-Q | Idem nas métricas de risco e retorno apresentadas como conclusão | 🟢 rodada feita | 3 achados (A-111, A-112, A-113); 1 erro de fórmula, 2 de declaração |

## Por que a lista não é o critério

Doze rodadas de auditoria com G1–G7 em "A" não pegaram A-101/102/103/105. A
matriz pergunta se o cálculo está certo; `-9` está aritmeticamente certo. Quatro
achados na primeira vez que alguém fez a outra pergunta é evidência de que
existem mais — fechar esta lista não encerra o objetivo, só a rodada.

Regra derivada: **revisor que executa acha o que revisor que lê não acha.**
Nenhum item fecha sem rodar contra o armazém.

A rodada de FIIs confirmou a regra duas vezes. O motor de FIIs era o mais
rigoroso dos três e mesmo assim tinha dois defeitos que só apareceram medindo:
o A-106 exigiu comparar `raw_score` com `type_score` par a par nos 258 fundos
declarados `ready`, e o A-107 exigiu recalcular as duas fórmulas de crescimento
sobre os dividendos reais de 296 fundos. Nenhum dos dois é visível lendo o
arquivo — as duas expressões estão aritmeticamente corretas.

E confirmou também o contrário, que importa igual: o A-108 parecia o defeito
mais grave dos três ao ler o código (um FII a P/VP 0,019 ranqueado como pior
avaliação que um a 18,34) e **não procede**, porque o gate de prontidão já
barra esses casos. Auditoria que só lê produz achado falso nas duas direções.

## A rodada do Portfólio Global (GLOB-Q)

Nenhum defeito vivo. O módulo já trata bem tudo o que quebrou os outros três:
`valuation_agregado` descarta múltiplo não positivo e separa presença de
usabilidade; `fields.py` marca não aplicável em vez de substituir por proxy;
`qualidade_por_classe` recusa agregar entre classes; `_percentil_por_classe`
ranqueia dentro da classe com correção de posição de plotagem; `returns.py` faz
câmbio point-in-time com limite de defasagem e recusa explícita.

O A-109 é o achado que **não procede, mas quase**. `_covariancia_confiavel`
monta a matriz com `cov(min_periods=...)`, que é par a par: cada entrada pode
repousar sobre uma amostra diferente, e matriz assim não é garantidamente
positiva semidefinida. Alimentando o cálculo com séries desalinhadas do
armazém, 10% das carteiras davam matriz não-PSD e, no pior caso, um ativo
aparecia **protegendo** a carteira (−26,2% do risco) onde a matriz corrigida
dizia que ele **adicionava** risco (+3,5%). A identidade de Euler continua
fechando nesse caso — ou seja, o teste central de `risk.py` não enxerga nada.

Em produção não acontece, e o motivo estava escondido em outro módulo: o passo
2 de `retornos_mensais` só mantém os meses em que todos os ativos têm retorno,
então o quadro publicado é de casos completos. A garantia era efeito colateral
de uma decisão tomada por outro motivo (não renormalizar peso mês a mês), sem
nada declarando a dependência — e o docstring de lá lamenta o truncamento de
séries longas, que é exatamente o argumento que levaria alguém a afrouxar a
janela. Fechado como invariante testado nos dois lados, não como correção de
fórmula.

## A rodada da construção de carteira (CONSTR-Q)

O módulo `core/markowitz.py` existe por um motivo declarado no próprio
cabeçalho: impedir que o engine escolha BBAS3 + ITUB4 + SANB3 — todos bancos,
ρ ≈ 0,85 — com pesos proporcionais ao score, tratando-os como se fossem
independentes. O A-110 é o defeito que fazia exatamente isso, por dentro.

A intensidade α do shrinkage é a razão b²/d². Com alvo `diag(S)`, o alvo
preserva as variâncias: o único efeito do α é **encolher as correlações**, e
α = 1 devolve uma matriz diagonal. O denominador d² = ‖S − F‖² já é zero na
diagonal por construção — mede só a massa de fora. Mas o numerador b², o erro
de amostragem de S, era somado sobre a matriz inteira. Numerador e denominador
mediam suportes diferentes, e a variância das variâncias — que este alvo nem
encolhe — decidia quanto encolher as correlações. Schäfer & Strimmer (2005),
citado no docstring da própria função, restringe as duas somas às entradas
fora da diagonal.

O mecanismo isolado: seis ativos com ρ = 0,5 dão α = 0,055. Acrescentar **um**
sétimo ativo independente com volatilidade 10× — que não adiciona correlação
nenhuma — leva o α a 1,000. Cestas da B3 misturam blue chip com small cap, e é
por isso que o efeito era generalizado: em 178 cestas de mesmo subsetor, o α
cravava em 1,000 em 121 delas. Em 2 de cada 3 carteiras o otimizador recebia
um mundo diagonal.

O que a medição também disse, e que a honestidade exige registrar: **o risco
realizado não piorava**. Vinte e quatro meses fora da amostra, os pesos do
código defeituoso rendiam 5,596% de volatilidade mensal contra 5,618% da
receita correta — empate. O dano se concentra onde a correlação é fraca, que é
onde apagá-la custa pouco; nas cestas com ρ ≥ 0,45 o α nem cravava (0,242).
O A-110 é defeito de estimador, não de resultado. Fica registrado como
corrigido porque a UI exibe o número como "Ledoit-Wolf" e porque um estimador
que contradiz a referência que cita não é auditável — não porque a carteira
estivesse perdendo dinheiro.

O resto do módulo passou: a cadeia de solver (cvxpy → SLSQP → projeção
heurística) marca `converged=False` na degradação e `views/portfolio_b3.py`
exibe isso ao usuário, então o padrão "degrada em silêncio" não ocorre aqui.

## A rodada das métricas de risco (RISCO-Q)

Três achados, e a diferença entre eles é o ponto: **um é erro de fórmula, dois
são de declaração** — e os dois de declaração não viram cálculo novo porque
inventar o número que falta seria pior que dizer que ele falta.

**A-112, erro de fórmula.** `core/us_backtest.performance_stats` calculava o
denominador do Sortino como `r[r < 0].std(ddof=1)` — a dispersão dos retornos
negativos em torno da média *deles*. Downside deviation é outra coisa:
`sqrt( (1/N) · Σ min(r − MAR, 0)² )`, sobre todos os períodos. Descentrar apaga
o tamanho da perda: uma série que perde exatamente −5% em todo mês ruim tinha
dispersão zero e o cartão exibia "—", isto é, *não mensurável*, para uma série
cujo Sortino padrão é 0,400. Em 300 carteiras reais de 60 meses, o número
exibido era 1,20× o padrão na mediana, até 1,75×, exagerado em 222 delas.

**A-111, declaração.** VaR e CVaR de 95% são históricos — e isso está certo,
pelo motivo que o docstring de `risk.py` já defende. O problema é o que
sustenta a cauda. Com o piso de `MIN_OBS = 18`, o percentil 5 cai entre o pior
e o segundo pior mês, a cauda fica com **um** elemento, e o CVaR passa a ser,
por definição, o próprio pior mês — verificado em 100% de 200 carteiras. Dois
cartões lado a lado exibiam a mesma observação única como se fossem medidas
independentes, e o texto prometia "1 a cada 20 meses" sobre uma série de 18.
Retirar um único mês move o CVaR em ~20% do próprio valor em qualquer tamanho
de janela. A correção expõe `n_cauda` e faz a tela dizer sobre quantos meses o
número repousa.

**A-113, declaração.** `performance_stats` é sempre chamada sem `rf`, então a
taxa livre de risco é zero: o número é retorno sobre volatilidade, não excesso
sobre volatilidade. O cartão dizia "Sharpe". Agora diz `Sharpe (rf = 0)` e
explica que descontar uma taxa que não está no pipeline seria inventá-la. A
correção de verdade — série de Treasury para o módulo EUA — fica registrada
como pendência de dado, não de código.

## A rodada do rebalanceamento (REBAL-Q)

A pergunta desta rodada era se a **ação** que o app recomenda sobrevive aos
custos. O `advisor` de Portfólio Global saiu limpo no essencial: quando o custo
não está calibrado ele devolve `manter` com `custo_calibrado=False` em vez de
adivinhar, não inventa Information Ratio que não tem como medir, e ordena de
forma determinística. Dois pontos reais.

**A-114, defeito latente, corrigido.** `ThresholdRebalance.deve_rebalancear` e
`HybridRebalance` mediam o maior desvio iterando `pesos_meta.items()`. Um ticker
que **entrou** era visto (a chave existe no alvo, e o peso atual sai de um
`.get(tk, 0.0)`); um ticker que **saiu** era invisível, porque a chave dele
sumiu justamente do dicionário que o laço varre. Sair inteiro de uma posição de
30% — o maior desvio que existe — media 0,0 p.p. e devolvia "não precisa mexer".
As duas classes passam a varrer a **união** dos dois conjuntos, via o helper
`_maior_desvio`.

Não houve decisão errada em produção: a tela usa `CalendarRebalance`
(`views/portfolio_global.py:1031`), e `advisor._projetar` devolve todos os
símbolos com peso 0,0 em vez de omitir a chave. Mas as duas classes são API
pública e documentada, e o defeito estava exatamente na regra que promete
"rebalanceia quando desviar da banda".

**A-115, metodológico, sua decisão.** A guarda `if custo_fracao >= abs(delta):
-> manter` compara o custo contra o **tamanho** da ordem. São grandezas
diferentes: o que justifica pagar um custo é o benefício de voltar ao alvo, não
o quanto se mexe. Do jeito atual, um movimento grande que agrega pouco passa, e
um movimento pequeno que agrega muito é barrado. O teste correto exige um
modelo de benefício que esta camada — deliberadamente pura — não possui. Fica
registrado ao lado de SCORE-03/A-104 como decisão de metodologia sua, não como
correção a aplicar em silêncio.

## A rodada do backtest (BACKTEST-Q)

A pergunta era se o retorno passado que a tela mostra teria sido alcançável por
alguém em pé naquela data. `core/us_backtest.py` passou bem no que costuma
falhar: o custo de transação é **de fato** deduzido (`portfolio` líquido convive
com `portfolio_gross`), a concentração acima da política é sinalizada e marcada
`eligible_for_conclusion: False`, a carteira é remedida na janela do benchmark
antes de subtrair, e erro de benchmark **omite a chave** em vez de devolver
`None` que a UI formataria como "0,00%". O `fwd_return` é do mês seguinte, não
sobreposto — o t-stat do Rank-IC não está inflado por sobreposição.

O defeito não estava no backtest. Estava em quem monta o painel que ele lê.

**A-116, sobrevivência.** `build_annual_panel` fazia `if fut.empty: continue`.
A ação que parou de negociar sumia do painel. É o viés de sobrevivência na forma
mais pura: o perdedor que quebrou não conta. O comportamento estava até **fixado
por teste** (`test_build_annual_panel_sem_preco_futuro`, "sem preço futuro → sem
linha") — foi decisão, não descuido, e é a decisão errada para um backtest.
Medido: uma cesta de duas ações, uma +30% e outra que caiu 80% e deslistou,
aparecia como **+30,0%** contra os −25,0% que aconteceram.

A correção separa duas ausências que o código confundia. Se o **dado** acaba
(as_of perto da borda do dataset), o retorno é genuinamente inobservável e a
linha sai — contada em `n_inobservavel`. Se a **ação** acaba mas o dataset
continua, ela deslistou: sai pela última cotação, marcada `censored`. Continua
otimista, porque deslistagem real costuma liquidar perto de zero sem cotação —
mas errar alguns pontos percentuais é outra ordem de grandeza do que apagar a
perda inteira.

**A-117, horizonte elástico.** `fut.iloc[0]` não tinha teto. Se o próximo preço
disponível estava 7 anos além do alvo, aqueles +300% eram rotulados "retorno de
12 meses" (medido). O mesmo em `forward_returns_from_monthly`, onde `shift(-1)`
devolve a próxima **linha**, não o próximo **mês**: um buraco na série punha
+100% de 11 meses entrando como retorno mensal, contaminando a volatilidade. O
preço de saída agora precisa cair dentro de 3 meses após o alvo.

**Estes são os dois primeiros achados da auditoria inteira que enviesavam o
número exibido para CIMA.** A-101…A-115 erravam para o lado conservador ou eram
neutros quanto ao resultado. Por isso a UI passou a declarar a censura: quantas
observações saíram por cotação forçada, e quantas ficaram de fora por retorno
inobservável — que não é retorno zero.

**Magnitude em dado real: pendente.** `prices_monthly` mora no armazém local e o
Docker está parado. O mecanismo está provado de forma exata e determinística; o
*quanto* isso movia o backtest publicado do módulo EUA só pode ser medido com o
container no ar.

## A rodada da camada de dado (DADO-Q)

Depois de A-116/A-117 no painel EUA, a pergunta natural era se o mesmo descarte
silencioso existia no B3 e no FII.

**A-118, FII.** `core/fii_validation.point_in_time_backtest` é, no geral, o
código mais cuidadoso dos três módulos: mantém fundos encerrados no universo
histórico por decisão explícita, cobra custo de transação e slippage, tem banda
de permanência no ranking e reporta cobertura. Ainda assim tinha a porta aberta.

`weights.loc[valid] / weights.loc[valid].sum()` renormalizava os pesos sobre os
fundos com retorno no período. Um fundo escolhido que liquidou — sem nenhuma
linha de retorno — tinha a fatia dele **redistribuída entre os sobreviventes**.
O dinheiro do fundo que sumiu rendia o que os outros renderam.

Medido em 24/08/2026 com uma cesta de três fundos, um caindo 2% ao dia:

| | retorno médio | cobertura |
|---|---|---|
| o fundo que caía está presente | **−6,46%** | 100% |
| o mesmo fundo liquida e some | **+10,49%** | 67% |

Inversão de sinal. `coverage` já caía para 67% e era reportado — mas como média
agregada, que ninguém traduz para "o retorno exclui um terço da carteira".

A fatia ausente passa a render **zero** no período: não inventamos a perda, que
pode ser buraco de dado, e o ganho dos sobreviventes deixa de ocupar aquele
peso. O mesmo cenário agora devolve +6,99% com `peso_ausente_medio` de 33%
declarado na aba de Backtest — um número sobre o qual dá para decidir.

**Este é o terceiro achado que enviesava para cima, e o mais grave: inverte o
sinal do resultado.** E estava no módulo que eu havia descrito como o mais
rigoroso dos três.

### B3 — A-119 e A-120

O mesmo padrão, no teste com que o app afirma que o score prevê.

**A-119.** Os pares `(ano, score, retorno)` do Rank-IC vinham de
`end_rows.iloc[-1] / start_rows.iloc[0]`: o preço de **uma data fixa** em cada
ponta. Quem não negociou naquele pregão exato saía por `NaN` no `dropna()` — e
junto saía quem **deslistou** no meio do ano, que é o caso que importa.

Medido sobre o painel real da B3 (1.089 tickers, 2015–2026):

| | empresas-ano no Rank-IC |
|---|---|
| antes | 5.958 |
| depois | 6.402 (**+7,5%**) |

444 empresas-ano voltaram ao teste. E as recuperadas rendem **menos** que as que
já entravam em 9 dos 11 anos — o que sumia era predominantemente o perdedor.
A direção do viés sobre o IC em si depende de onde esses nomes estavam no
ranking do score, o que exigiria o histórico de scores para medir; o que está
medido é a exclusão e o perfil de retorno dela.

A correção lê a primeira e a última cotação **efetivamente negociada** dentro da
janela. Para quem deslistou, a última cotação é a saída — o mesmo tratamento de
A-116. A lógica saiu da view para `core.b3_pooled_evidence.retornos_da_janela`,
como manda o `CLAUDE.md`, e por isso passou a ser testável.

**A-120.** `tail(12)` pega as 12 últimas **observações**, não os 12 últimos
**meses**. Com buraco na série a janela estica e o rótulo não muda: FSTU11
exibia **76 meses** como "retorno 12m" (−56%) e PSVM11, 33 meses. O recorte
passou a ser por data. E o lado oposto também fecha: uma série com 2 pontos
cobrindo 1 mês daria um retorno mensal rotulado "12m", então a janela precisa
cobrir ao menos 10 meses — abaixo disso o número não existe, e "não há 12 meses
de histórico" é resposta melhor do que um retorno curto com o rótulo errado.

## O que trava a chegada em produção

Três itens estão corretos no código e **não chegam à tela publicada** sem uma
gravação remota, que exige autorização sua:

1. **PIT-6.8** — a validação 6.8.0 passou (run 52) no armazém local. Enquanto
   ela não for publicada, a aba de FIIs em produção consulta a 6.8.0 no
   Supabase, não acha, e reporta `unvalidated` — ou seja, a tela fica MAIS
   conservadora que a realidade, o que é o lado certo de errar.
2. **SCORE-01** — a vitrine EUA viva é de 03/08, `score_version` 0.5.0, gerada
   pelo ranqueador antigo, com 1.111 linhas marcadas `decision_grade`. As
   correções A-101 e A-105 estão na `main` e não alcançam essas linhas. Este é
   o único item em que a tela publicada está MENOS conservadora que a
   realidade. O passo de ingestão local (`run_us_ingest.py snapshot
   --warehouse`) segue barrado pelo classificador de permissões.
3. **SCORE-02** — o aviso de escala está no código; chega à tela no próximo
   deploy da `main`, sem gravação remota.
