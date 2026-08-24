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
