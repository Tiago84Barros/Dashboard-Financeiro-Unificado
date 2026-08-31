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
US_FUNDAMENTAL_SCORE_VERSION = "0.7.1"

# Score de assimetria da aba "Empresas Fora da Curva".
US_ASYMMETRY_SCORE_VERSION = "0.1.0"
