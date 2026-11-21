"""Identidade da metodologia Empresas Americanas (compartilhada UI ↔ persistência).

Espelha core/b3_methodology.py. Versões separadas por trilha para que os scores
sejam versionados de forma point-in-time em market_us.score_vintages.
"""

# Schema físico das tabelas market_us.* (migration 040).
US_SCHEMA_VERSION = 1

# Score fundamentalista (Qualidade/Crescimento/Solidez/Eficiência/Valuation/Retorno).
# 0.5.0 (auditoria 2026-07): stock-based compensation e diluição passam a ser
# fatores explícitos — sbc_to_revenue e fcf_ex_sbc_margin em Qualidade,
# share_count_cagr_3y em Retorno ao acionista. Scores anteriores continuam
# consultáveis em market_us.score_vintages pela versão antiga.
#
# 0.6.0 (2026-08-29): a trilha de Crescimento deixa de medir lucro operacional,
# LPA e fluxo de caixa por CAGR. A taxa composta não é definida com base ou
# ponta <= 0, e devolvia ausência para 1.159 das 1.976 empresas com par de anos
# de lucro operacional -- a maioria. Ausência não é nota baixa: ela derruba a
# COBERTURA da trilha, e por ela a confiança, de modo que a empresa no prejuízo
# era tratada como empresa sem demonstração. Entram no lugar taxas SIMÉTRICAS
# (Davis-Haltiwanger-Schuh), definidas através do zero e limitadas, sob nomes
# novos -- `op_income_growth_3y`, `eps_growth_3y`, `fcf_growth_3y`. Receita
# continua em CAGR: não fica negativa, e a taxa composta é a leitura familiar.
# 0.7.0 (2026-08-30): duas correcoes na leitura, nao no criterio.
# (a) NaN deixa de ser lido como valor. O quadro que produz a nota vem de
#     pandas, onde NULL vira `float('nan')`; o dossie vem de `_mapping`, onde
#     vira `None`. Como `nan is None` e falso, TODA derivacao guardada por
#     `is None` -- EBITDA, FCL, divida liquida, capital investido -- ficava
#     desligada no caminho que decide e ligada no que exibe. O sintoma: 21
#     empresas gravadas `decision_grade` com `impairment_flags` na propria
#     linha, porque o portao de balanco quebrado (A-101) lia NaN e nao via
#     marca nenhuma. O portao volta a valer: 1.024 empresas passam a ser
#     travadas por balanco quebrado, como sempre deveriam ter sido.
# (b) Lucro bruto passa a ser derivado de receita menos custo quando a empresa
#     tagueia os dois extremos e nao o subtotal. Nao e estimativa: e a
#     definicao. Ausencia derrubava a cobertura da trilha de Qualidade por um
#     numero que os demonstrativos ja continham.
# Efeito medido sobre as mesmas 2.626 empresas: decision_grade 974 -> 1.061,
# screen_grade 673 -> 388, cobertura media 68,2 -> 77,7.
# 0.7.1 (2026-08-31): razao que NAO EXISTE deixa de contar como dado que falta.
# ROE, ROIC, conversao de caixa, divida/EBITDA e divida/PL sao anuladas quando o
# denominador foi MEDIDO e veio <= 0 -- e isso continua certo, porque razao com
# denominador que troca de sinal nao e ordenavel. O erro era o passo seguinte: a
# razao anulada chegava ao score como ausencia, e ausencia derruba COBERTURA,
# que e o numero que barra decision_grade. A empresa deficitaria apanhava duas
# vezes pelo mesmo prejuizo -- uma no rank, puxada ao neutro, e outra na
# cobertura, por um dado que ela entregou. Agora a indefinicao sai do numerador
# E do denominador da cobertura.
# So ficou seguro fazer isto depois de 0.7.0: o portao A-101 voltou a disparar,
# e ele trava exatamente a empresa que produz estas indefinicoes. A medicao
# confirma que o buraco nao abriu -- sobre as mesmas 2.626 empresas,
# decision_grade 1.061 -> 1.062. O ganho e outro e e honesto: screen_grade
# 388 -> 373, cobertura media 77,7 -> 80,4, confianca media 80,3 -> 82,2.
# 1.218 empresas carregavam pelo menos uma razao indefinida (2.163 no total).
# 0.7.2 (2026-08-31): o painel PIT deixa de pontuar empresa que ainda nao
# existia na data. O portao de estreia sempre esteve escrito em
# scoring_history, mas lia `market_us.assets.first_trade_date` -- NULL nas
# 7.654 linhas, porque nenhum passo do projeto escrevia nessa coluna. Portao
# sem escritor e codigo morto com cara de protecao. A demonstracao anual de um
# ano pre-IPO chega ao EDGAR junto do S-1; sem `filed_at` ela cai na regra
# antiga (fim do periodo mais folga) e entra na safra. Medido antes da
# correcao, sobre as 16 safras da 0.7.1: 2.695 das 23.522 linhas -- 11,5% --
# eram empresa que ainda nao havia negociado na data (20 de 163 em 2010, 205
# de 2.354 em 2025). Como o score e por RANK, cada
# intrusa deslocava a posicao de todas as outras. `first_trade_date` passa a
# ser derivada da primeira barra observada em prices_monthly (7.577 de 7.654
# ativos); onde nao ha serie, continua NULL e NAO exclui ninguem -- nao saber
# quando o papel estreou nao e evidencia de que ele nao existia.
# 0.8.0 (2026-08-31): a marca de balanco quebrado deixa de ser eliminatoria, e
# entra no lugar um piso de RESPONDIBILIDADE. A marca nasceu em A-101 para
# impedir que razao anulada chegasse ao score como ausencia -- caminho que 0.7.1
# fechou na raiz, tirando a indefinicao do numerador E do denominador da
# cobertura. O portao sobreviveu ao proprio motivo e virou outra coisa: travava
# 1.023 das 2.618 empresas da vitrine, entre elas Lowe's, Altria, Cardinal
# Health e Bath & Body Works com cobertura 100% e confianca 100%. Patrimonio
# negativo ali e estrutura de capital escolhida (recompra acumulada), nao
# defeito de dado; negar opiniao sobre a Lowe's era o erro de A-101 com o sinal
# trocado. A marca continua saindo na tela e no prompt como DIVULGACAO --
# multiplo sobre base negativa nao significa nada --, e nunca mais como motivo
# para nao ter opiniao.
# O que entra no lugar fecha um buraco real de 0.7.1: `coverage` responde "das
# perguntas RESPONDIVEIS, quantas foram respondidas", entao a trilha em que
# sobrou uma unica pergunta respondivel, respondida, marca 100%. E "quem
# pergunta menos tira nota maior" uma camada abaixo. `answerability` mede a
# fracao das metricas que a METODOLOGIA define e a empresa consegue ter, e o
# piso e a maioria estrita: trilha julgada pela minoria das proprias perguntas
# nao foi julgada. O piso morde -- 83 empresas tem alguma trilha critica em 50%,
# 68 delas passariam por todos os outros criterios.
# Efeito medido sobre as mesmas 2.618 empresas: decision_grade 1.062 -> 1.727,
# abrangencia dos EUA 41% -> 66%.
# 0.9.0 (2026-09-08): a trilha de crescimento passa a perguntar pela INCLINACAO
# DA REGRESSAO log-linear da serie, e nao mais pelo CAGR de ponta a ponta. O
# CAGR le dois numeros -- primeiro e ultimo -- e chama de taxa o que sobra;
# tudo que aconteceu no meio e invisivel para ele, inclusive o fato de a serie
# nao ter tendencia nenhuma. Medido sobre as mesmas 3.701 empresas: as duas
# aritmeticas concordam (correlacao 0,9365, divergencia mediana 1,47 pp), ou
# seja, a regressao REFINA em vez de inverter -- mas 1.109 das 3.145 taxas
# (35%) tem R2 abaixo de 0,5, e essa e exatamente a informacao que o CAGR
# escondia. O R2 passa a ser publicado ao lado de cada taxa, na tela, no
# dossie e no contexto da LLM. Series que cruzam zero (lucro operacional, LPA,
# FCL) usam crescimento simetrico Davis-Haltiwanger-Schuh, onde o CAGR e
# indefinido.
# O piso de respondibilidade cobra o preco: a regressao exige tres pontos onde
# o CAGR se contentava com duas pontas, entao quem tem serie curta deixa de
# responder a trilha. decision_grade 1.742 -> 1.668 (-74), screen_grade
# 657 -> 740. Nenhuma empresa passa a ter `growth` como trilha irrespondivel
# (a unica que aparece continua sendo `solidity`, 206): a perda esta na
# cobertura dentro da trilha, nao na trilha inteira. Uma taxa ajustada em dois
# pontos e o CAGR com outro nome e sem R2 -- perder 74 opinioes de grau de
# decisao e o custo de nao dar opiniao sobre serie que nao suporta a pergunta.
# Entra junto o DIVIDEND YIELD derivado do EDGAR (dividends_paid sobre valor de
# mercado, 23,7% do universo). Ele NAO entra na trilha `shareholder`: ja esta
# dentro de `shareholder_yield` e seria contado duas vezes. Ausencia e `None`,
# nunca 0.0 -- lacuna de dado nao e nao-pagamento de dividendo.
# Tres guardas de dado nascem da mesma medicao. `shares_outstanding` publica a
# contagem em escala errada: FLS com 176.793 em 2025 contra 176.793.000 nos
# quatro anos anteriores, GBL com 26,70 em TODA a serie (milhoes, coerente
# consigo mesma), HY sem contagem nenhuma e um divisor de 100 chegando por
# fora. O dividendo por acao chegava a 303.854 dolares; o maximo agora e 19,34.
# Em `share_count_cagr_3y` o mesmo defeito PREMIAVA -- a queda de escala virava
# recompra liquida de -99,89%, o melhor valor do universo. Ia de -0,9989 a
# +2.339.999; agora vai de -0,85 a +3,22, ao custo de 190 leituras (6%).
# 0.9.1 (2026-11-17): valor ISOLADO no grupo passa a valer o neutro, e nao a
# nota extrema. Gemeo do achado D2 da B3 (SCORE_VERSION 2.33.0). O percentil
# por industria de uma metrica que so UMA empresa do grupo tem saia 1,0 -- ou
# 0,0 depois do `1 - x` das metricas em que menor e melhor --, uma nota de
# ponta tirada de uma observacao sem ninguem com quem comparar. O grupo e
# grande (>= min_group empresas), entao o fallback para o universo nao
# disparava: o que faltava era o VALOR, nao a industria. Um valor so nao tem
# ordem; agora ele vale 0,5, como a ausencia.
# Medido no armazem local sobre as 4.349 empresas do quadro corrente: 258
# celulas (44 em interest_coverage, 32 em ev_ebitda, 26 em ev_ebit, 26 em
# shareholder_yield, 22 em net_debt_ebitda...), 126 empresas mudam de nota,
# variacao media -3,1 pontos e mediana -1,9 -- o extremo era mais vezes o
# topo que o fundo. Maiores quedas: RADI -18,1, AXTA -12,3, ZD -11,5. No
# top-20 saem CVS e IDXX e entram CF e HRMY; no top-50 trocam seis nomes.
# Os gemeos da escala 0-100 (`core.us_advanced_lab._percentile`, qualidade
# de caixa, e `core.us_estilos._percentil`, momento e P&D) recebem a mesma
# regra; eles rankeiam no universo inteiro, onde valor isolado so aparece com
# o universo quase vazio.
# 0.9.2 (18/11/2026) -- completa a 0.9.1 no lado do grupo. A 0.9.1 tirou a
# nota extrema do valor ISOLADO (um so valor no grupo -> neutro), mas o corte
# "grupo pequeno usa o universo" seguia contando EMPRESAS da industria, nao
# VALORES VALIDOS da metrica. Numa industria de cinco empresas em que so duas
# ou tres publicam ROIC, o percentil ainda saia de dois ou tres pontos: o pior
# ROIC do universo podia ser o melhor do grupo. Medido no armazem: 2.710 de
# 73.558 pares empresa x metrica, em 686 das 4.349 empresas, eram ranqueados
# em industria com menos de quatro valores. As tres empresas sem industria
# saiam 50 em todas as metricas (o groupby descarta a chave nula). Agora a
# metrica com menos de `min_group` valores na industria, ou sem industria, vai
# ao universo -- numa funcao so (`percentil_winsorizado`), que o laboratorio
# de Analise Avancada tambem usa (la o numero nao muda: ele ja ranqueia no
# universo). Sobre a foto atual, contra a 0.9.1: 673 notas mudam (3 sem
# industria), |delta| mediano 1,3 ponto, 14 caem mais de 10 e nenhuma sobe
# tanto; Spearman 0,991; 15 do top-20 ficam; o grau (decision/research/
# screen) nao muda em nenhuma empresa.
# Vantagem OOS (walk-forward anual 2010-2025, top-20 por score, mesma regua):
# ver docs/empresas_americanas.md, secao v0.9.2.
US_FUNDAMENTAL_SCORE_VERSION = "0.9.2"

# Score de assimetria da aba "Empresas Fora da Curva".
US_ASYMMETRY_SCORE_VERSION = "0.1.0"
