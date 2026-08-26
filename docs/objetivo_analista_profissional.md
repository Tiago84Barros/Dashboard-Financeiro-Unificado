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
| A-121 | Uma única cotação zerada derrubava a seção "Correlação entre ativos" inteira (`TypeError`) | ✅ | `tests/test_preco_nao_positivo.py`; MMAQ4 tem 65 meses zerados no painel real |
| A-122 | Preço **negativo** entrava nos cálculos: 5 tickers, **463 observações** no painel real | ✅ | idem; MMAQ4 exibia queda máxima de **−2.638%**, RSUL3 **−104,2%**, NEMO3 volatilidade de **361%** |
| A-123 | Card "Maior positiva"/"Inversa mais forte" mostrava a estimativa pontual sem o IC | ✅ | idem; **82% dos 3.610 pares medidos** têm IC 95% cruzando zero |
| INTEG-Q | Rodada de integridade do preço e da correlação | 🟢 rodada feita | 3 achados (A-121 quebra em voz alta; A-122 e A-123 em silêncio) |
| A-124 | Pilar de "Integridade" da confiança de dados era cego a preço inválido | ✅ | `tests/test_confianca_preco_invalido.py`; **11 tickers, 1.406 observações** no Supabase e **zero** flags registradas; MMAQ4 tinha nota **100,0 "Alta"** |
| A-125 | `core.data_confidence` sem NENHUM consumidor desde a remoção da página (a7bbe35) | ✅ | idem; o índice honesto existia, correto, e não chegava a tela alguma |
| A-126 | Quatro módulos do parecer da banca (2026-05-23) escritos, **nenhum ligado e nenhum testado**: `correlations.py` (M2, EWMA), `copulas.py` (M2c), `survivorship_ingestion.py` (C3c), `survivorship_prices.py` (C3cc+) | ✅ medido e testado / ⚠️ ligar é decisão sua | `tests/test_modulos_banca_orfaos.py`; os quatro **rodam**; EWMA difere de Pearson em **0,184 na média e 0,557 no máximo** nos pares da carteira |
| A-127 | `validation_readiness` lia `strict_available: False` **literal** para survivorship — ingerir deslistada jamais mudaria o veredito | ✅ | idem; o bloco `pit` do mesmo arquivo já era medido, o de survivorship não; gate segue não-estrito, mas agora diz **22 curados, 0 externos** |
| BANCA-Q | Rodada dos órfãos: quem implementou o parecer e nunca foi consultado | 🟢 rodada feita | 2 achados; capacidade existe, porta de entrada não |
| A-128 | **Devolução de capital contada como renda**: `AMORTIZAÇÃO` e `REST CAP DIN` entravam no provento anual e no DY | ✅ | `tests/test_proventos_renda_vs_capital.py`; **415 pares ticker-ano inflados em 234 tickers, média +139%**; RBRI11/2026 exibia 252,20 de "provento" com renda real **zero** |
| A-129 | Eco de classe da brapi somado nas agregações anuais (`SUM(amount)` cru em duas rotas) | ✅ | idem; `MIN` dentro de (data, tipo) mata o eco sem apagar evento legítimo |
| A-130 | `min` sobre a data **inteira** descartava dividendo+JCP legítimos — e a coluna `type`, selecionada, nunca era lida | ✅ | idem; **1.120 ocorrências** de DIVIDENDO+JCP na mesma data ex; BAZA3 12m passou de 3,77 para 11,27 |
| A-133 | Liquidez declarada pela brapi contradiz a fita oficial da B3 (SHPP11: 2,79 mi/dia declarados contra **721/dia** negociados) | ✅ resolvido 26/08 | `core/liquidez.py` arbitra acima de 10x; o estimador já existia mas só preenchia lacuna, nunca contradizia; 6 de 306 aptos trocam de fonte, 2 deles para **mais** liquidez |
| A-131 | `market.dividends` guarda **duas safras do mesmo pagamento** (origem: retrato degradado da brapi em 23–25/07 + chave natural insert-only): uma com o calendário real da B3, outra colapsada (`payment_date = ex_date`) | ✅ resolvido 26/08 | Filtro de leitura em `core/dividend_types.py`; **187 FIIs saíam com renda inflada, mediana +35,8%, máx +90,9%**; escopo real ~5.000 linhas, não 18.873 |
| A-132 | Eventos de renda de magnitude implausível para pagamento periódico | ⚠️ decisão sua | **625 eventos em 45 FIIs** com rendimento único acima de 30% do preço; PATL11 tem `RENDIMENTO` de 66,33 num fundo de R$ 64,15 que paga 0,57/mês |
| PROV-Q | Rodada dos proventos: o número de manchete do FII responde a "quanto isso rende?" | 🟢 rodada feita | 5 achados; 3 fechados, **os primeiros a enviesar para cima na métrica de decisão do FII** |
| A-133 | Classe sem preço **por natureza** (caixa, renda fixa) recebia o mesmo motivo `sem_preco` de um ativo cuja série deveria existir e faltou — e o aviso da tela nem exibia motivo de preço | ✅ | `tests/test_cobertura_motivo_estrutural.py`; o comentário acima da linha 405 prometia "motivo próprio" que o código não dava; agora `classe_sem_preco` e a quebra por motivo aparecem no aviso |
| EUA-Q | Rodada do módulo americano, o único dos quatro que DADO-Q nunca varreu | 🟢 rodada feita | Scores e subscores todos em [0,100]; `decision_grade` com filing mais antigo em FY2024; zero preço não-positivo; **3.040 das 3.052 empresas sem série de preço** (só 12 símbolos publicados) — a máquina de `Cobertura` declara essa ausência, não a esconde |
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

## A rodada da integridade do preço (INTEG-Q)

Depois de A-119 eu fui medir a heterogeneidade de janela da correlação — e o
script **estourou**. O achado não estava onde eu procurava.

**A-121 — quebra em voz alta.** `retornos_mensais` fazia
`.replace([inf, -inf], pd.NA)`. `pd.NA` num quadro float o converte para
`object`, e `DataFrame.corr()` levanta `TypeError`. Bastava **uma** cotação
zerada em qualquer ativo da carteira para a seção "Correlação entre ativos"
inteira cair. MMAQ4 tem 65 meses zerados no painel real, então isso não era
hipótese.

**A-122 — o mesmo dado, em silêncio.** Puxando o fio: o painel mensal da B3
(1.089 tickers, 24/08/2026) tem **463 observações de preço NEGATIVO** em 5
tickers — NEMO3 (132), PPAR3 (119), RSUL3 (108), FIGE4 (90), MMAQ4 (14). O
ajuste por proventos empurra o `adjusted_close` abaixo de zero e nada barrava.
O que chegava à tela:

| ticker | exibido | correto |
|---|---|---|
| MMAQ4 · queda máxima 5a | **−2.638%** | −14,3% |
| RSUL3 · queda máxima 5a | **−104,2%** | −100,0% |
| NEMO3 · volatilidade 12m | **361%** | não existe (sem preço válido) |
| RSUL3 × NEMO3 · correlação | **0,368** | não existe |

Uma perda não passa de 100% e uma correlação sobre preços negativos não
significa nada. Isto não é viés para cima nem para baixo: é **valor impossível
apresentado como medição**, que é pior — um viés a gente desconta, um número
impossível a gente não sabe em que direção corrigir.

A correção barra na origem (`market.historical_prices` e
`market_us.prices_monthly` passam a exigir preço > 0), e repete a guarda no
caminho de fallback do yfinance. Preço inválido vira **mês ausente**, que todo
consumidor já trata. Efeito colateral revelador: NEMO3, PPAR3 e RSUL3 saem da
matriz de correlação por não alcançarem 24 meses válidos — quase todo o
"histórico" deles era lixo.

**A-123 — o que o card afirmava.** Com o dado limpo, medi 3.610 pares em 60
carteiras aleatórias: **82% têm IC 95% cruzando zero**, ou seja, não são
distinguíveis de independência. E janela curta infla |correlação| (média 0,188
com n≤34 contra 0,150 com n>60), então os pares de menos histórico vencem
**90% dos cards de destaque** sendo 75% dos pares. O card "Inversa mais forte"
mostrava `−0,34` sozinho; o IC daquele par é `[−0,63; +0,07]`. Ele afirmava uma
proteção que o dado não sustenta.

O card passa a trazer o par, o número de meses e o IC 95%, e a dizer
literalmente "não distinguível de independência" quando é o caso — perdendo a
cor de destaque. Abaixo, uma linha conta quantos pares da carteira estão nessa
situação. A regra de ordenação **não** mudou: qual estatística deve rankear os
pares é decisão de metodologia sua, não minha. O que mudou é que a incerteza
deixou de ser invisível.

### A confiança que ninguém via — A-124 e A-125

Consultando o Supabase para dimensionar A-122, o número de produção veio pior
que o do painel em cache: **11 tickers, 1.406 observações de preço <= 0**.

| ticker | inválidas / total | |
|---|---|---|
| PPAR3 | 266 / 287 | 93% |
| NEMO3 | 224 / 226 | 99% |
| RSUL3 | 200 / 226 | 88% |
| FIGE4 | 185 / 229 | 81% |
| MMAQ4 | 174 / 242 | 72% |
| SANB3 / SANB4 | 112 cada | 34% |

SANB3 e SANB4 são bancos líquidos, não cascas deslistadas — isto alcança
análise de verdade. E `market.data_quality_logs` tinha **zero** flags para
qualquer um deles.

**A-124.** O pilar "Integridade" de `core.data_confidence` só olhava flags
abertas. Sem flag, integridade = 100%. Resultado antes da correção:

| ticker | antes | depois |
|---|---|---|
| MMAQ4 | **100,0 · Alta** | 82,0 · Baixa |
| PPAR3 | 81,2 · Alta | 58,0 · Baixa |
| NEMO3 | 72,5 · Média | 47,7 · Baixa |
| RSUL3 | 67,0 · Média | 44,9 · Baixa |
| PETR4 / VALE3 / WEGE3 | 75,2 · Alta | 75,2 · Alta (inalterado) |

O painel dava sua **nota máxima** ao ticker mais corrompido que ele tinha.

A penalidade é proporcional à fração corrompida — 3% de lixo custa pouco, 99%
não pode aparecer como confiável. E o **rótulo** passou a não poder contradizer
o pilar: MMAQ4 ainda soma 82,0 pela fórmula (cobertura e frescor perfeitos, e
integridade pesa só 25%), mas "Alta" não é leitura honesta com a integridade em
28%. O score em si **não muda** — quem o consome como número vê o mesmo. O cap
é só sobre integridade, de propósito: frescor está em 40,0 para todo ticker
saudável do painel, o que é defasagem conhecida da série mensal, e capar por
ele rotularia o painel inteiro como "Baixa".

**A-125 — o defeito que estava por trás.** Ao procurar onde declarar isso,
descobri que `core.data_confidence` **não tinha consumidor nenhum**. A página
"Saúde dos Dados" foi removida em `a7bbe35` (20/07/2026) e o módulo virou
código morto: nem `views/`, nem `pages/`, nem `app.py` o importam. O índice
honesto — que nasceu justamente do achado de que `confidence_score` é constante
por método — existia, estava correto, e não chegava a tela alguma. Motor de
análise que ninguém consulta na decisão é decoração.

Não ressuscitei a página: removê-la foi decisão sua. Liguei o sinal onde ele
muda decisão — a seção "Qualidade das Empresas" da carteira B3, sobre os
**finalistas**, os tickers que você está prestes a comprar. `alerta_confianca`
é silencioso quando os dados estão bons e nomeia ticker e fração quando não
estão. Falha de banco nunca derruba a seção.

### As recomendações que foram escritas e nunca ligadas — A-126 e A-127

A-125 mostrou um motor correto sem porta de entrada. Isso levantou uma pergunta
maior: **quantos outros existem?** Varri os 143 módulos de `core/` atrás de
código que nada importa. Quatro são órfãos de verdade — e os quatro
implementam recomendações do parecer da banca examinadora de 2026-05-23:

| módulo | linhas | recomendação | consumidores |
|---|---|---|---|
| `core/survivorship_ingestion.py` | 478 | C3c — ingestão de deslistadas | 0 |
| `core/copulas.py` | 251 | M2c — dependência de cauda | 0 |
| `core/survivorship_prices.py` | 223 | C3cc+ — preço pós-delisting | 0 |
| `core/correlations.py` | 158 | M2 — correlação EWMA | 0 |

Nenhum tinha teste. Antes de afirmar que a capacidade existe, executei os
quatro. **Os quatro rodam.** Duas falhas que encontrei na primeira tentativa
eram minhas, não deles: passei `DataFrame` onde a assinatura pede `np.ndarray`,
e retorno mensal com o default `periods_per_year=252`. Corrigido o contrato,
o comportamento é o esperado. `tests/test_modulos_banca_orfaos.py` trava isso.

**O que a medição diz que importa.** EWMA não é redundante com o Pearson
estático que a tela mostra — nos seis papéis da carteira, a diferença média
por par é **0,184** e a máxima **0,557**. Ou seja: SCORE-03/A-104 (janelas de
correlação heterogêneas) tem resposta pronta no repositório, escrita há três
meses, desligada. Ligá-la muda que estatística ordena os pares, e isso é
decisão de metodologia sua — a mesma linha que segurei em A-123.

`survivorship_prices.py` trata com princípio exatamente o que A-116/A-118/A-119
corrigiram na mão: falência leva o resíduo a zero e a variação a −100%, nunca
abaixo. O teste trava esse invariante.

**A-127 — a porta pregada.** `core/b3_validation.py` montava o manifesto com

```python
"survivorship": {"strict_available": False, "reason": "... ainda nao integrado"}
```

literal no código. `validation_readiness` lê essa chave para decidir se um
resultado pode ser promovido a validação estrita. Consequência: **por mais
deslistadas que você ingerisse, o veredito nunca mudaria** — e os 478 linhas
de `survivorship_ingestion.py` existem exatamente para essa ingestão. O
contraste estava no mesmo arquivo: o bloco `pit` também começa `False`, mas é
**sobrescrito por uma medição real** de `market.calculated_metric_vintages`.
Assimetria, não decisão.

Agora o bloco é medido. `strict_available` **continua `False`** de propósito:
22 tickers curados não são universo histórico completo, e qual contagem promove
o gate é decisão sua. O que mudou é que o motivo diz o que foi medido —
`22 tickers deslistados (22 curados, 0 de fontes externas)` — e que ingerir
passa a aparecer no manifesto. Medir nunca afrouxou o gate; há teste travando
isso.

### O provento que não era renda — PROV-Q

A camada de proventos nunca tinha sido varrida, e ela produz o número de
manchete do FII: o DY. A pergunta da rodada foi a de sempre — *a fórmula
responde ao que se pergunta?* Aqui a pergunta é "quanto isso rende?", e a
resposta somava coisas que não rendem.

**A-128 — devolução de capital contada como renda.** `market.dividends` tem
cinco tipos. Três são renda (`RENDIMENTO`, `JCP`, `DIVIDENDO`); dois devolvem
o principal do próprio cotista (`AMORTIZAÇÃO`, `REST CAP DIN`). Duas rotas de
agregação — `core/market_read.py::load_dividendos_anuais`, fonte **primária**
da aba "Análise de Empresa", e `core/portfolio/adapters/fii.py` — faziam
`SUM(amount)` sem olhar o tipo.

| medição (Supabase, 2026-08-24) | valor |
|---|---|
| pares ticker-ano inflados (≥2023) | **415** |
| tickers afetados | **234** |
| inflação média | **+139%** |
| pior caso | RBRI11/2026: 252,20 de "provento", renda real **0,00** |

Amortização tem 588 linhas mas soma 11.481 — média de 19,5 por evento contra
2,1 do `RENDIMENTO`. É pouca linha com muito peso, exatamente o formato que
passa despercebido numa conferência por amostragem.

**A-129 — o eco de classe.** A mesma agregação somava o eco documentado da
brapi (o mesmo evento sob CEBR5 e CEBR6). Dentro de um mesmo (data, tipo) o
valor honesto é o **mínimo**.

**A-130 — e o erro na direção contrária.** `core/dossie_b3.py::_dividendos`
já era cuidadoso: mantinha um ramo conservador e um bruto, e sinalizava
suspeita de duplicação. Mas agrupava só por **data** e tomava `min` sobre o
dia inteiro. No Brasil dividendo e JCP saem na mesma data ex — **1.120
ocorrências na base** — e o `min` descartava um evento verdadeiro. A coluna
`type` era selecionada no SQL e **nunca lida**. BAZA3 nos últimos 12 meses
passou de 3,77 para 11,27 por ação. Agrupar por (data, tipo) resolve os dois
lados: mínimo dentro do tipo mata o eco, soma entre tipos preserva o evento.

`core/dividend_types.py` centraliza a classificação, e tipo desconhecido conta
como renda **de propósito** — um tipo novo aparece no yield em vez de sumir em
silêncio. A-124 mostrou o que custa um sinal que ninguém vê.

**O que o conserto NÃO alcança.** O `dy_12m` que pontua o score de FII (peso
0,12, métrica crítica) não vem de `market.dividends`: é coluna armazenada em
`market.fiis`, vinda da brapi. Meu conserto não a toca, e eu não tenho como
afirmar daqui qual metodologia a brapi usa.

**A-131 e A-132 — o que achei olhando e não vou consertar sozinho.** Ao
conferir a divergência entre as duas fontes, o dado cru mostrou duas coisas:

- `market.dividends` guarda **duas safras do mesmo evento** — uma com data-ex
  sintética no dia 1º do mês, outra com o calendário real da B3. **379 tickers
  carregam as duas.** PATL11 grava cada mensal de 0,57 duas vezes (29/08 e
  01/09). Meu `min` por (data, tipo) não pega: as datas diferem. Qual safra
  vence e com que janela é decisão de metodologia sua, e mexe em 18.873 linhas.
- **625 eventos em 45 FIIs** têm rendimento único acima de 30% do preço.
  PATL11 tem um `RENDIMENTO` de **66,33** num fundo de R$ 64,15 que paga
  0,57/mês. É a mesma classe de A-122: valor impossível apresentado como
  medição. Não filtrei porque, diferente de preço negativo, um provento grande
  **pode** ser real (extraordinário, liquidação) — cortar por régua exigiria
  escolher a régua.

### O módulo que nunca tinha sido varrido — EUA-Q

DADO-Q cobriu B3 e FII. Os EUA — um dos quatro módulos mandatados — nunca
tinham passado por uma rodada de dados. Abri EUA-Q por isso, não por suspeita.

O que medi no Supabase:

| Verificação | Resultado |
|---|---|
| Tabelas de `market_us` publicadas | só `company_snapshots` (3.052) e `prices_monthly` (4.720) |
| Score e subscores fora de [0,100] | nenhum |
| `decision_grade` com filing antigo | mais antigo é **FY2024** (5 linhas) — limpo |
| Preço não-positivo (a falha de A-121/A-122) | **zero** |
| Empresas **sem nenhuma** série de preço | **3.040 de 3.052**; publicados só 12 símbolos |

O último número parece alarmante e não é o que aparenta: a vitrine americana
publica fundamentos para 3.052 empresas e preços para 12. A pergunta que
importa não é "faltam preços?", é **"o risco do Portfólio Global é calculado
sobre um pedaço e apresentado como o todo?"** — que é exatamente o defeito de
A-118 e A-119 noutra roupa. Fui atrás e a resposta é não: `Cobertura`
(`core/global_portfolio/returns.py`) carrega `peso_coberto`, `simbolos_sem_serie`
e `motivos` por símbolo, e a tela avisa que os painéis cobrem N% do patrimônio.
A máquina declara a ausência em vez de escondê-la.

**A-133 — a promessa no comentário que o código não cumpria.** Lendo esse
contrato achei o defeito real. A linha que marca os símbolos de classe sem
série trazia acima de si o comentário *"ausência estrutural, não defeito de
ingestão — por isso motivo próprio desde o começo"*, e logo abaixo gravava
`MOTIVO_SEM_PRECO` — a mesma constante das duas linhas seguintes, que tratam
de preço que **deveria existir e faltou**. Um CDB, que nunca terá cotação
mensal, saía com o mesmo rótulo de uma NVDA perdida na ingestão. Pior: o aviso
da tela nem chegava a exibir motivo de preço — nomeava os símbolos numa lista
só e detalhava apenas os motivos cambiais. Separei em `classe_sem_preco` e
levei a quebra por motivo até o aviso, ordenada para o que **não** pede ação
vir primeiro. Não muda número nenhum: muda o usuário saber onde agir.

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

## A virada: de auditoria infinita para universo de decisão (24-25/08/2026)

O critério que eu vinha usando estava errado, e o usuário nomeou isso: eu media
o app contra um ideal **sem defeito**, então toda rodada terminava com "falta
isso". Um analista sênior não trava porque 45 dos 363 FIIs têm dado sujo — ele
descarta os 45, opera com os 318 e diz com que confiança opera.

A instrução que destravou o impasse: *se a fatia de ativos de qualidade superior
passar de uma margem aceitável, descarte os ruins ou torne-os irrelevantes.*
Isso resolve de uma vez várias "decisões suas" que eu vinha devolvendo — a régua
não é reparar o dado ruim, é **expulsá-lo do universo de decisão** quando o
universo bom for grande o bastante.

### `core/universo_decisao.py`

Três populações, e a diferença entre elas é o ponto:

| | nominal | investível | apto | modo |
|---|---|---|---|---|
| Empresas B3 | 447 | 443 | 412 (93%) | descartar |
| Seleção de FIIs | 1.065 | 428 | 305 (71%) | descartar |
| Empresas Americanas | 3.052 | 3.052 | 1.111 (36%) | ressalva |

Descartar **casca de cadastro** (nominal → investível) não custa nada: nunca
houve ativo ali. Os 637 FIIs sem preço são fundos encerrados e registros CVM sem
negociação, não oportunidades perdidas. Descartar por **dado faltando**
(investível → apto) custa, e só esse custo entra na conta de confiança.

O gate primário é o **piso absoluto**, não o percentual: 1.111 ações americanas
sustentam carteira mesmo sendo 36% do cadastro; 12 nomes não sustentam carteira
por mais limpos que estejam. O percentual é reportado como o preço que se pagou,
não como o critério. Medido: filtrar preferenciais e warrants do universo EUA
move o share de 36,4% para só 38,2% — ele é mesmo ~38% `decision_grade`, e a
resposta certa é reconhecer a abundância absoluta, não inflar o número.

O gate da B3 é `core.data_confidence` com limiar de confiança média. Isso
implementa a política **e** finalmente dá um consumidor ao motor órfão de A-125.

### `core/confianca_secao.py` e a tela Grau de Confiança

Dois eixos, separados porque o usuário age diferente em cada um:
*confiabilidade* (integridade, frescor, metodologia validada) leva o peso;
*abrangência* entra com 0,15 de propósito — punir cobertura baixa empurraria o
app a inflar o universo com ativo ruim, o contrário do que se quer.

Regra central, com teste que a trava: **o que não foi medido não vira 100%.**
Componente sem medição sai da média ponderada e declara que saiu, e a seção
informa que fração do peso sustenta o número. Assumir perfeição no que ninguém
olhou é o mesmo defeito de A-124 com outra roupa.

A tela `views/confianca.py` entrou no roteamento. Motor de confiança sem porta
de entrada é decoração — foi exatamente o que aconteceu com A-125.

### O descarte passou a valer onde a decisão acontece

O filtro foi ligado no carregamento do universo da Criação de Portfólio da B3,
com duas travas: só age quando o remanescente passa do piso (`Universo.descarta`),
e sempre declara quantas empresas saíram. Filtro silencioso é pior que filtro
nenhum, porque some com a empresa sem o usuário saber.

### Achados que a medição produziu

- **Ingestão de cotações da B3 parada há ~34 dias** e cadastro de FIIs há ~32.
  A régua 3→30 dias é a do próprio projeto (`price_freshness_factor`), então o
  zero não é calibração apertada: é o feed parado. É o item de maior efeito
  sobre o número e depende de rodar a ingestão, não de código.
- **Extrato bancário sem reimportação há 316 dias** (891 movimentos, todos
  confirmados). Não é defeito do app — é ação do usuário — e por isso entra com
  peso menor, mas medir só os lançamentos manuais dava 100% falso à seção.

## Publicação autorizada: o que foi feito e o que ficou pronto (25/08/2026)

O usuário autorizou as pendências de gravação remota. O armazém local estava
parado; subi o Docker (autorização permanente concedida na mesma ocasião) e o
container `dfu_warehouse` voltou saudável.

### Feito — recálculo local da vitrine EUA

Descoberta que mudou o plano: o snapshot que estava no armazém era de **21/08**,
anterior às correções A-101/A-102/A-103/A-105 de 23/08. Como A-105 altera o
score gravado (53,4 → 42,5 no caso medido), publicar aquele snapshot teria
levado dado mais fresco **sem** a correção — melhorando a data e mantendo o
erro. Regerei com o código atual via `snapshot --warehouse --offline`
(recálculo sobre fundamentos já ingeridos, sem rede): 2.831 linhas, 0 erros.

O efeito das correções é exatamente o esperado, e é conservador:

| | vitrine viva (03/08) | recalculada (25/08) |
|---|---|---|
| `decision_grade` | 1.111 | **903** |
| média do score em `decision_grade` | 53,1 | **57,5** |
| máximo | 75,2 | 81,2 |

Duzentas e oito empresas eram classificadas como aptas a sustentar recomendação
sem dado que as sustentasse; foram despromovidas a `research_grade`. A média
sobe porque o que saiu estava embaixo. Isto fecha o diagnóstico de SCORE-01: a
tela publicada era mesmo **menos conservadora que a realidade**, e o número de
903 continua muito acima do piso de 40 — o universo segue abundante.

Integridade do que ficou no armazém: 2.831 linhas, nenhum `score_confidence`
nulo, nenhum score fora de [0,100].

### Bloqueado — as duas gravações remotas

Ambas passaram no ensaio seco e foram barradas na gravação pelo classificador
de permissões do harness, que a autorização do usuário no chat não alcança:

- **EUA** — `publish_us_snapshot_from_local.py`: seco confirmou 2.831 linhas
  prontas para upsert.
- **FII/PIT-6.8** — `publish_fii_selection_from_local.py`: seco retornou
  `publication_ready: true`, `validation_status: "passed"`, 394 linhas,
  `preflight_blockers: []`, cobertura média 92,9%. É esta publicação que leva o
  certificado 6.8 e faz a produção parar de reportar `unvalidated`.

Não contornei o bloqueio. As duas ficam prontas para execução do usuário.

### Não resolvido — frescor de preços da B3

O armazém tem preços até **31/07**; a Supabase até ~22/07. Publicar levaria o
frescor de 34 para 25 dias — ganho pequeno, ainda dentro da faixa de decaimento.
A correção de verdade é rodar a ingestão diária antes de publicar. O ensaio seco
de `run_market_ingest.py daily --warehouse` ficou 20 minutos sem emitir saída e
foi encerrado; é execução longa contra a BRAPI, não travamento diagnosticado.
Fica como o item de maior efeito isolado sobre o número de confiança.

### Publicado (25/08/2026)

O usuário reafirmou a autorização e as duas gravações passaram.

- **EUA** — 2.831 linhas publicadas, geração de 25/08 com as correções
  A-101/A-105 embutidas. `decision_grade` em produção passou de 1.111 para 903.
- **FII/PIT-6.8** — 394 linhas, `validation_published: true`. Produção agora
  responde `status: passed, as_of 2026-07-31` para a metodologia 6.8.0, em vez
  de `unvalidated`.

**Dois defeitos que a própria publicação criou no módulo de confiança.** Ambos
eram números cravados que envelheceram em silêncio no instante da publicação:

1. `Metodologia validada` era a constante `50.0` com o texto *"produção lê
   unvalidated"* escrito à mão, para FII e EUA. Publicada a validação, a
   constante passou a mentir na direção oposta — conservadora, mas errada. Os
   dois componentes agora medem: o FII lê `load_fii_validation_status()`, o
   mesmo leitor da tela; o EUA compara a `score_version` publicada com a do
   código e decai pela idade da geração.
2. `universo_us` contava as 221 linhas de julho nas versões 0.1.0/0.2.0.
   Republicar não as apaga, e elas inflavam o denominador — a abrangência
   parecia pior do que é. O universo agora é escopado à versão corrente.

O primeiro é o mesmo erro que o módulo declara evitar, em outra roupa: assumir
um valor em vez de medi-lo. Ele sobreviveu ao commit inicial porque, no momento
em que foi escrito, a constante estava certa.

**Efeito no relatório:** EUA 76,8% → **88,6%**; FIIs 52,8% → **65,3%**;
Portfólio Global 76,5% → **80,3%**; geral 77,3% → **80,3%**. A pior seção
deixou de ser FIIs e passou a ser Empresas B3, por frescor de preço.

## Por que o preço da B3 estava parado: drift de schema, não falta de ingestão

O componente `Frescor` de Empresas B3 marcava 0% com mediana de 34 dias. A
leitura óbvia — "ninguém rodou a ingestão" — estava errada, e o dado mostrava
isso: `market.historical_prices` no Supabase tem preço de ontem para 117
tickers e nada há semanas para os outros ~970.

Rodando a ingestão diária com log de progresso, **todo** ticker abortava na
mesma exceção:

```
NotNullViolation: null value in column "recorded_at"
of relation "calculated_metric_vintages"
```

Comparando as 437 colunas de `market.*` entre armazém local e Supabase,
existem exatamente **duas** divergências, e são as duas desta exceção:

| coluna | armazém local | Supabase |
|---|---|---|
| `calculated_metric_vintages.recorded_at` | `NOT NULL DEFAULT now()` | `NOT NULL`, sem default |
| `calculated_metric_vintages.availability_quality` | `NOT NULL DEFAULT 'first_seen_proxy'` | `NOT NULL`, sem default |

A migration `021_market_metric_vintages.sql` declara os dois defaults, mas usa
`CREATE TABLE IF NOT EXISTS`: a tabela remota já existia de uma versão anterior
e não foi alterada. O pipeline não escreve essas colunas — conta com o default,
como funciona localmente. Local passa, remoto falha em 100% dos tickers.

`supabase_unificado/schema/050_metric_vintages_defaults.sql` corrige. É
idempotente e não destrutivo: só define default para linhas futuras.

A lição vale além deste caso: **teste que roda só contra o banco local não vê
drift de schema**, e o sintoma chega disfarçado de "dado desatualizado" — uma
categoria que convida a redescoberta em vez de diagnóstico.

## O preço parado tinha três causas empilhadas, não uma

O sintoma era um só — "o preço da B3 está velho" — e cada correção só revelava a
próxima camada. Nenhuma das três apareceu lendo o código; todas apareceram
rodando o pipeline contra o banco remoto.

**1. Drift de schema (remoto ≠ local).** A migration 021 usava
`CREATE TABLE IF NOT EXISTS`, então a tabela que já existia no Supabase nunca
recebeu os defaults declarados. Resultado: `NotNullViolation` em
`calculated_metric_vintages.recorded_at` para *todo* ticker. Como `ingest_ticker`
grava tudo em uma transação só, a violação numa tabela periférica derrubava
também o preço. Diagnosticado comparando `information_schema.columns` nos dois
bancos: exatamente 2 de 437 colunas divergiam. Corrigido pela migration
`050_metric_vintages_defaults.sql`, aplicada em 25/08/2026.

**2. Piso de dividendo na precisão errada.** A brapi devolve resíduos como
`rate=1e-10`. Em Python `1e-10 > 0` é verdadeiro; em `numeric(18,6)` o valor
arredonda para `0.000000` e viola `chk_dividends_amount_positive` — e de novo
derrubava o preço junto, pela mesma transação única. PETR4 não atualizava por
causa de um provento de 2006 valendo um décimo de bilionésimo de real. A
constraint está certa; o normalizador é que validava na precisão da linguagem
em vez da precisão da coluna. O piso ficou em uma unidade cheia da última casa
(`0.000001`), não meia, para não depender da regra de arredondamento — Python
arredonda para o par, Postgres arredonda afastando do zero.

**3. Granularidade errada, em silêncio.** `daily()` pedia `range=1mo` sem passar
`interval`, e o default da brapi é `"1mo"`: voltavam as duas bordas do mês, não
os ~22 pregões dele. Sem erro, sem ticker perdido, sem linha de log — só a idade
mediana do preço parada perto de 30 dias com a ingestão aparentemente saudável.
Esse é o modo de falha mais perigoso dos três, porque não produz nada para
investigar. Pior: a limitação chegou a ser *documentada como aceita* em um
comentário de `core/data_confidence.py` ("frescor fica em 40,0 porque a série
mensal não é atualizada diariamente"), e o comentário protegeu o defeito de ser
investigado por meses.

**Resultado medido** (25/08/2026, 1.109 tickers, ~67 min, 19.473 preços):
idade mediana do preço caiu de **34 dias para 0**; 927 tickers fecharam com
preço do dia, 111 com o de ontem. 19 tickers falharam.

## A-134: média ponderada deixa defeito eliminatório ser compensado

Com o preço corrigido, um problema mais grave ficou visível. `data_confidence`
combina cobertura (45%), frescor (30%) e integridade (25%) por média ponderada.
O frescor *de preço* é 60% do pilar de frescor, ou seja 18% do score total — de
modo que 82% da nota vem de pilares que não sabem se o papel ainda negocia.

Medido: **LUXM3 marcava 75,2 com rótulo "Alta" e último pregão em 13/05/2015**
(4.122 dias). Outros 17 tickers passavam no gate de aptidão (>= 55) com preço
parado, incluindo NEOE3 (113 dias) e uma família inteira de 205 dias. Todos
elegíveis a entrar numa recomendação com preço fóssil.

Sem preço vivo não existe decisão: não dá para comprar, vender nem marcar a
posição a mercado. Isso não é "menos confiável", é fora do mercado — e defeito
eliminatório não se desconta, se elimina. O score passou a ser **tetado** abaixo
do limiar de aptidão quando o fator de frescor de preço zera, reusando o
`_PRECO_VELHO_DIAS = 30` que o próprio módulo já declarava, sem inventar limiar
novo. O mesmo critério saiu também do **denominador** de abrangência em
`universo_b3`: papel que não negocia não conta nem como acerto nem como falha
de cobertura.

Custo: 16 dos 415 aptos saíram (3,9%). A confiança de Empresas B3 **subiu**,
de 86,2% para 86,8%, porque o que restou é medida sobre ativos que se pode
realmente negociar.

## O corpus RAG saiu do banco: 162 MB viraram 25 MB de arquivo

O Supabase está em 505,1 MB com teto de 500 MB — já em `EXCEEDING USAGE
LIMITS`. Antes de discutir provedor, medi o que ali dentro é banco de verdade:
**de 505 MB, ~3,3 MB são escritos pelo app em tempo de execução.** Os outros
~502 MB são vitrine somente-leitura pagando preço de banco transacional (MVCC,
WAL, índices, backup contínuo) para servir consulta que é filtro + ordenação.

Trocar de provedor compraria seis meses. Separar por **mutabilidade** resolve a
classe do problema: o que muda fica em Postgres, o que só é lido vira arquivo.

O primeiro corte é o corpus CVM: `docs_corporativos_chunks`, 93.498 chunks,
162 MB — 32% do banco. A coluna `embedding` está 100% nula, então `rag_b3`
sempre usou a busca temporal; nenhuma capacidade relacional estava em uso.
Em Parquet+zstd o mesmo corpus ocupa **24,9 MB em 24 partições** (6,5×), e o
DuckDB roda o mesmo SQL por cima dos arquivos.

Três coisas que a migração exigiu e que valem além dela:

**Verificar por assinatura, não por contagem.** Contagem igual não é corpus
igual. `md5(string_agg(chunk_hash, ORDER BY chunk_hash))` provou que armazém
local e Supabase eram bit-idênticos (`3134197f…`), e o publicador recalcula a
mesma assinatura sobre o Parquet. O manifesto grava as duas; se divergirem, o
leitor cai para o Postgres em vez de responder com corpus parcial em silêncio.

**Tirar o dialeto do caminho de leitura.** O regex `~` e
`(:n || ' months')::interval` eram dívida de portabilidade. O regex classifica o
DOCUMENTO, não a consulta: virou a coluna booleana `eh_ancora`, calculada no
publish. O corte de data virou parâmetro `date` calculado em Python. O que
sobrou é SQL comum aos dois motores — uma consulta, não duas versões dela.

**Falhar alto no que falharia em silêncio.** O publicador não carrega vetores.
Hoje isso é inofensivo, mas se alguém gerar embeddings e republicar, a busca
semântica seria desligada sem erro e sem log — só respostas piores. O publicador
agora recusa publicar se existir qualquer `embedding` não nulo.

### O teste de paridade achou um defeito que nenhum motor sozinho acharia

`tests/test_rag_store_paridade.py` roda a mesma consulta nos dois backends com
dado real. Sete dos dezessete testes falharam de cara — e a causa não era a
migração. Os conjuntos eram **idênticos**; a ordem, não.

`ORDER BY data_doc DESC, chunk_index ASC` é ordenação **parcial**: dois
documentos do mesmo dia empatam no mesmo `chunk_index`, e o desempate fica a
cargo do motor. Como existe `LIMIT`, um empate na fronteira do corte não troca
só a ordem — troca **quais chunks chegam ao contexto do LLM**. Mesmo ticker,
mesmo dia, evidência diferente. O defeito era anterior a esta migração e estava
lá desde que o RAG existe. Corrigido com `doc_id ASC` no fim do ORDER BY.

Um teste que compara dois motores é mais barato que um revisor: ele torna
observável a não-determinação que um único motor esconde ao ser consistente
consigo mesmo.

## A matriz de correlação media todos os pares na mesma janela (A-135)

A legenda da matriz sempre disse "janela solicitada: 5y". Os dois caminhos que
leem do banco — `asset_quotes` e `portfolio_position_snapshots` — nunca
aplicaram essa janela: entregavam a série inteira a `DataFrame.corr()`, que é
**pairwise**. Cada par usava toda a sobreposição que tivesse. Medido em
produção, a mesma matriz misturava pares com 32 meses e pares com 556.

Isso não é imprecisão de arredondamento; é comparar coisas que não são
comparáveis. A matriz existe para o investidor ler a linha de um ativo e
escolher o que diversifica. Uma correlação de 46 anos descreve um regime de
mercado que não existe mais e, ao lado de uma de 2,7 anos, ainda aparenta ser
"mais confiável" por ter mais observações.

`JANELA_CORR_MESES = 60` trunca os retornos mensais **antes** do corte por
cobertura mínima — um ativo só entra se tiver os 24 meses exigidos *dentro* da
janela, e não emprestando história antiga. São os mesmos 5 anos que o caminho
do yfinance já pedia, de modo que as três fontes passem a medir a mesma coisa.
`janela_meses=None` preserva o comportamento antigo para quem precisa de
história inteira.

A legenda deixou de prometer: agora declara a janela comum e o período
efetivamente medido (`periodo_medido`), lido do índice dos retornos que
entraram no cálculo. Declaração e execução voltaram a coincidir.

### Segunda frente: a mesma matriz decide substituição na Criação de Portfólio B3

O caso do Portfólio Global já estava fechado por outro caminho:
`core/global_portfolio/returns.py` publica apenas os meses em que **todos** os
sobreviventes têm retorno, então o quadro não tem buraco e todo par é medido
nos mesmos meses. Foi consequência da correção de covariância PSD de 23/08.

O que restava era `core/b3_correlation_diversification.monthly_returns_for`,
que entrega o quadro inteiro de `_batch_yf_precos_mensais(period="10y")` a
`correlation_matrix`. Ali a heterogeneidade não é estética: a substituição por
correlação compara `baseline_avg` com `trial_avg` trocando um ativo por outro.
Com o candidato medido noutra janela, o "ganho" de diversificação pode vir da
mudança de amostra, não da mudança de ativo — a decisão de trocar passava a
depender de quando o candidato abriu capital.

O teto de 60 meses reduz a faixa de 18–120 para 18–60 e iguala a janela às duas
telas. **Não elimina a heterogeneidade**: quem tem 24 meses continua medido em
24. Homogeneidade completa exigiria interseção, que descartaria candidatos — e
é justamente deles que a tela precisa para poder substituir. O limite fica
declarado aqui em vez de virar promessa silenciosa.

## O Supabase voltou para dentro do plano free (26/08/2026)

Medido por `pg_database_size`: **532,8 MB → 247,2 MB**, de um limite de 500 MB.
Estava acima do teto; agora sobra mais da metade.

| ação | liberado |
|---|---|
| `market.brapi_raw_payloads` arquivado no armazém local e compactado | 99,0 MB |
| `public.docs_corporativos_chunks` aposentada (corpus serve do Parquet) | 162,0 MB |

`public.docs_corporativos` (4.368 documentos, 4,4 MB) foi preservada: é barata e
serve a ingestão. Depois do drop, `rag_b3.retrieve_chunks` devolve 45–60 chunks
para PETR4, WEGE3, ITUB4 e VALE3 e zero para ticker inexistente — sem abrir
nenhuma conexão de banco.

O que isso muda não é o número: é que a pressão de espaço vinha enquadrando
decisões de arquitetura ("trocar de provedor", "pagar o Pro"). Restam ~181 MB de
vitrine só-leitura que migram pelo mesmo padrão de Parquet — mas agora isso é
escolha de engenharia, não urgência de fatura.

## O check de "provento implausível" acusava 588 amortizações e comparava preços de épocas diferentes (A-132)

A Integridade da Seção de FIIs media a fração de fundos investíveis **sem
provento de magnitude implausível**. Ela marcava 49 fundos. Dois defeitos
empilhados, e nenhum deles era o dado:

**1. A regra foi reescrita à mão em vez de importada.** `core/dividend_types.py`
é a fonte canônica e escreve `"AMORTIZAÇÃO"` com acento. `core/confianca_secao.py`
tinha uma segunda cópia da lista, sem acento — e `upper()` no Postgres não tira
acento. A exclusão nunca disparou: as 588 amortizações do banco entravam
inteiras como se fossem rendimento. Devolver capital é, por construção, uma
fração grande do preço; o check estava desenhado para acusá-las.

**2. O valor histórico era comparado ao preço de hoje.** O check confrontava
`d.amount` (de 2018, digamos) com `f.price` (de agora). Um fundo que amortizou
a maior parte do capital negocia hoje a uma fração do preço antigo, então um
pagamento normal de 2018 aparecia como 900% do preço de 2026. RBDS11 era o caso
exemplar. Agora o preço vem de `market.historical_prices` numa janela de ±10
dias em torno do ex-date — o preço **da época do evento**.

Efeito medido contra o Supabase: 49 → 14 fundos sinalizados; Integridade
84,6 → 96,7; Seleção de FIIs 65,3% → 69,5%; global 84,1% → 85,0%.

**O que não foi inflado.** 10.018 dos 38.416 eventos de renda não têm preço na
época e não podem ser julgados. Eles não viram "limpos": a evidência do
componente declara a própria cobertura ("check cobre 74% dos 38.416 eventos").
O que não foi medido não vira 100 — é a mesma regra que o módulo aplica em
Frescor e Abrangência.

Os 14 restantes são achados reais de dado, não ruído do check: HGAG11 pagou
R$ 2.290 sobre um preço de R$ 12,85 (178x) e AROA11 paga ~R$ 6,60 sobre R$ 0,98
(6,8x) mês após mês — valores que só fecham se a unidade ou o ticker estiverem
errados na origem. FLRP11 e HGBS11 aparecem por outro motivo: são cotas de
preço alto com pagamento anual/semestral concentrado, onde 0,30 × preço é um
limiar apertado. Ficam registrados como pendência de investigação.

## As duas safras do mesmo pagamento inflavam a renda de 187 FIIs (A-131)

O item estava no backlog como "decisão sua" desde a rodada de dados, com a
estimativa de 18.873 linhas afetadas e a hipótese de que as safras se
distinguiam pela data-ex sintética no dia 1º. Fui medir antes de escolher a
régua, e **as duas coisas estavam erradas**.

**O escopo real é ~5.000 linhas, não 18.873.** Das 19.555 linhas com data-ex no
dia 1º, só 4.496 têm gêmea próxima. As outras 15 mil são histórico antigo em
que a brapi nunca forneceu as duas datas — apagá-las perderia evento de
verdade.

**E "dia 1º" não é o discriminador.** O que separa as safras é
`payment_date = ex_date`. Nenhum evento real da B3 paga no dia em que fica ex:
a mediana da defasagem na tabela é de **14 dias**. O dia colapsado não é uma
data, é a ausência de uma. RELG11 mostra o par cru:

```
ex=2025-09-05  pay=2025-09-12  0,80  criada 27/06   <- calendário real
ex=2025-09-01  pay=2025-09-01  0,80  criada 25/07   <- safra colapsada
```

A safra colapsada entrou em bloco entre 23 e 25/07/2026. RELG11 paga 0,80 por
mês e carrega **11 linhas reais mais 10 cópias**.

**Custo medido.** 187 FIIs investíveis com renda de 12 meses inflada; mediana
**+35,8%**; 71 acima de +50%; máximo +90,9%. HGLG11 — um dos maiores fundos do
país — saía **64,7% acima**.

**O bug que se escondia atrás de um conserto.** `core/portfolio/adapters/fii.py`
já fazia `MIN(amount) GROUP BY ticker, ano, event_date, type`, escrito
justamente para matar eco de duplicata (A-129). Não pegava nada aqui, e o
motivo é preciso: as duas safras **divergem no `event_date`** (01/09 contra
12/09). Deduplicar pela coluna em que as cópias diferem não deduplica.

**A régua: mês do pagamento, não valor.** Casar por valor parece natural e
falha — HGLG11 tem cópias de 0,9574 e 1,0734 ao lado de reais de 1,1000. Uma
tolerância apertada deixa as duas passarem; uma larga começa a apagar evento
legítimo. Também não serve o mês do *ex*: o par de HGLG11 fica ex em 31/10 e
paga em 14/11, e a cópia cai em 01/11 — pelo mês do ex, os dois nunca se
encontrariam.

**O que a regra não faz.** Não apaga linha: é filtro de leitura, em
`core/dividend_types.py`, ao lado da classificação de tipo. A safra colapsada
continua na tabela como evidência de que a ingestão a produziu. E uma cópia
**sem** gêmea de calendário real sobrevive — a regra só age quando há o que
preferir. Nenhuma gravação remota foi necessária.

Ligado em quatro consumidores: o adapter de FII do Portfólio Global, o backtest
(`load_fii_series`), o preenchimento de lacunas de DY em `core/market_read.py`
e a validação PIT (`data_pipeline/market/fii_pit.py`).

**Correção do que escrevi antes.** Eu registrei aqui que "a validação PIT rodava
sobre a renda dobrada" e que o run 52 estaria comprometido. Fui medir os dois
bancos antes de mandar refazer, e não estava:

| | linhas colapsadas com gêmea real | tickers |
|---|---|---|
| armazém local (onde o run 52 rodou) | 213 | 69 |
| Supabase (vitrine publicada) | 5.818 | 307 |

A safra colapsada é **quase só da vitrine**. O run 52 leu um banco praticamente
limpo, e refazê-lo mexeria em 213 linhas de 39.170. Continua valendo refazer por
higiene, mas é ajuste fino, não invalidação — a pendência que eu tinha declarado
como bloqueante não é bloqueante.

**De onde veio a safra colapsada.** Fui atrás do escritor, com pressa, porque eu
tinha acabado de passar ao usuário um comando de ingestão e precisava saber se
ele reabasteceria o defeito. O rastro:

- as linhas colapsadas do Supabase têm `source='brapi.dev'` e nasceram em bloco
  entre 23 e 25/07/2026 — 13.732 só no dia 25;
- `source='brapi.dev'` é o caminho `normalize.dividend_rows`, alimentado pelo
  endpoint `quote_fii_full`. Não é o v2 de FII, que grava `brapi_fii_v2`;
- a chave natural de `market.dividends` é `(ticker, event_date, type, amount)`.
  As duas safras divergem justamente no `event_date`, então a segunda **não
  substitui** a primeira: entra ao lado. Insert-only não tem como superseder.

Ou seja: a brapi serviu, naquela semana de julho, um retrato degradado em que
`paymentDate` vinha igual a `lastDatePrior` no dia 1 do mês; o nosso código
copiou fielmente; e o banco guardou as duas versões porque a chave não sabe que
são o mesmo pagamento. O armazém local escapou porque sua carga de FII é de
27/06, anterior ao episódio.

**A ingestão de hoje é segura — verificado no dado, não no código.** A mesma
rotina rodou hoje (644 payloads `quote_fii_full`) e o payload de RELG11 voltou
com 58 proventos e **zero** colapsados, defasagem real de 7 dias em todos. Das
24 linhas gravadas hoje em tickers terminados em 11, as 22 colapsadas são
IGTI11 — que é unit de ação, não FII, em evento antigo no qual a brapi publica
uma data só. Sem gêmea de calendário real, o filtro as mantém, que é o
comportamento correto.

**O que fica como risco residual.** A fonte é variável e a chave natural não
consegue superseder vintage. Se a brapi repetir um retrato degradado, o banco
volta a acumular as duas safras — e a defesa é o filtro de leitura, não a
ingestão. Por isso ele mora em `core/dividend_types.py` e não num script de
limpeza pontual.

## O piso de liquidez funcionava; o número que entrava nele é que não (A-133)

**O que achei.** `market.fiis.liquidez_diaria` vem da brapi. A fita oficial da
B3 — volume financeiro diário em `market.fii_b3_security_history` — conta outra
história para alguns fundos, e a diferença não é de calibragem:

| ticker | declarado | fita oficial da B3 | razão |
|---|---|---|---|
| SHPP11 | 2.794.163/dia | **721/dia** | 3.874x |
| VVRI11 | 68.561/dia | 541/dia | 127x |
| ZIFI11 | 3.861/dia | 86/dia | 45x |
| BICE11 | 1.852/dia | 42/dia | 44x |

**Por que passou despercebido.** O app já tinha o estimador certo, e ele é
rigoroso: `liquidez_diaria_b3` usa mediana de seis meses fechados, exclui o mês
incompleto e conta mês sem negócio como zero. O problema era onde ele era
chamado — `liquidity_candidates` era o conjunto dos `Liquidez_Diaria` **nulos**.
A fita preenchia lacuna e nunca contradizia. Com número preenchido, por mais
absurdo que fosse, ninguém conferia.

Isso importa porque o piso de liquidez existe e funciona: `min_daily_liquidity`
é aplicado em quatro pontos de `core/fii_portfolio_v4.py`. O que o derrotava não
era a regra, era a entrada. SHPP11 declarando 2,79 milhões passa por qualquer
piso razoável — e o investidor lê que pode sair de uma posição que, na fita da
bolsa, negocia setecentos reais por dia. Prometer saída inexistente é o pior
erro que um número de liquidez pode cometer.

**A regra.** `core/liquidez.py::liquidez_para_decisao` arbitra entre as duas
fontes: acima de 10x de divergência, a fita ganha. Não por desconfiança do
agregador, mas porque uma das fontes registra o negócio e a outra o interpreta.
Exige lastro — sem três meses de observação, fita curta é lacuna de carga, não
desmentido. E é **simétrica**: declarar liquidez de menos exclui do universo um
fundo que negocia, erro mais barato mas ainda erro.

**Efeito medido (306 aptos, 26/08/2026).** Seis fundos trocam de fonte. Dois
deles — LASC11 (245 mil declarados contra 3,36 milhões na fita) e RSPD11 (1,1 mil
contra 70 mil) — **ganham** liquidez: estavam subdeclarados e saíam penalizados
sem motivo. O saldo em cada piso testado é de −1 fundo aprovado. A regra é
bisturi, não machado.

**Limitação que herdo e não escondo.** `market.fii_b3_security_history` **não
existe no Supabase** — é tabela do armazém local. Na Streamlit Cloud a
arbitragem não roda, e o app usa o declarado. A defesa real está na publicação
da vitrine, que é feita do armazém local, onde a fita existe. Enquanto a vitrine
não for republicada, os seis fundos acima seguem com o número antigo em produção.
