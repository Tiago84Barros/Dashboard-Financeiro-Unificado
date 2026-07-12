# Relatório — Rigor Estatístico × Análise por Fundamentos

> Como conciliar a robustez estatística da seleção com a lógica econômica da
> análise por *valuation*, num mercado (o brasileiro em especial) sujeito a
> choques macro, políticos, regulatórios e tributários constantes.

---

## 1. O problema

A régua atual de aprovação (aba Criação de Portfólio) exige **significância
estatística** do desempenho recente: `valor-p < 0,10` sobre o excesso da
estratégia, com correção de falsos positivos (FDR). A questão levantada é
legítima:

> Um rigor estatístico muito elevado pode descartar justamente as empresas de
> maior potencial de longo prazo, apenas porque atravessaram um período de
> adversidade conjuntural — confundindo a **volatilidade do ambiente** com a
> **qualidade intrínseca do negócio**.

Exemplo real observado: o segmento de Petróleo (Exploração/Refino/Distribuição)
bateu os pares em +13,7% no holdout e teve bom poder preditivo (Rank-IC 0,117),
mas foi **reprovado** porque o `valor-p` (0,227) não cruzou o limite de 10%.

---

## 2. Por que a crítica está correta

Testar significância sobre **retornos realizados** é notoriamente de **baixo
poder estatístico**:

- Com ~24 retornos mensais e alta variância, o teste raramente atinge p < 0,10 —
  não por a estratégia ser ruim, mas por a amostra ser pequena e ruidosa.
- Isso é o clássico **erro Tipo II** (descartar algo bom para evitar aceitar algo
  ruim). Uma régua binária de p-value incorre nesse erro sistematicamente.
- O retorno de 24 meses carrega o **beta ao macro** (juros, câmbio, política
  fiscal). Logo, o teste **confunde ambiente com empresa**.

A literatura tem nome para isso: **McCloskey & Ziliak, "The Cult of Statistical
Significance"** — significância *estatística* ≠ significância *econômica*; tratar
p < 0,10 como portão binário é exatamente o erro que os autores denunciam.

---

## 3. O que Damodaran sugere

Damodaran **não** avalia estratégias por significância de retorno — avalia por
**valor intrínseco**. Três pilares respondem diretamente à questão:

- **Normalização (mid-cycle):** não julgar a empresa pelos números deprimidos do
  momento ruim; usar lucros/margens/ROIC **normalizados ao longo do ciclo**. Uma
  boa empresa num macro adverso tem lucro corrente baixo mas valor intrínseco
  alto — é aí que nasce a oportunidade.
- **Separar macro de empresa:** ele valora com premissas macro *explícitas*
  (taxa livre de risco, prêmio de risco) e premissas *da empresa* (crescimento,
  margem, ROIC). A **resiliência** mora na capacidade de sustentar
  **ROIC > custo de capital** através dos ciclos — não no preço.
- **Ceticismo com backtest de retorno** ("Narrative and Numbers"): o que importa
  é se a *história econômica* (gerar caixa acima do custo de capital e reinvestir)
  se sustenta, não se o preço bateu um benchmark numa janela.

---

## 4. O que a literatura quantitativa sugere

- **Lei Fundamental da Gestão Ativa (Grinold & Kahn):** `IR = IC × √amplitude`.
  Aumenta-se o poder estatístico **não baixando a barra**, mas **aumentando a
  amplitude** (mais anos, mais nomes, mais ciclos). O **Rank-IC** já é o IC — e é
  medido cross-sectional em todos os anos, portanto **macro-neutro e de alta
  amplitude**. É um teste muito melhor que o p-value de 24 meses.
- **Literatura de Qualidade** (Novy-Marx 2013, *gross profitability*; Asness,
  Frazzini & Pedersen — AQR, *Quality Minus Junk*): qualidade = rentabilidade,
  crescimento, segurança e **sua persistência**. Testa-se a *persistência do
  fundamento*, não a significância do retorno recente.
- **Abordagem Bayesiana / priors econômicos** (espírito do **Black-Litterman**):
  usar a qualidade fundamentalista como **prior** e deixar os dados de retorno
  *atualizarem* a crença. Empresa de fundamentos sólidos parte de prior favorável
  e precisa de **menos evidência de retorno** para passar.

**Contraponto honesto — Harvey, Liu & Zhu ("…and the Cross-Section of Expected
Returns"):** defendem barras *mais altas* (t > 3) — **mas** para *declarar um novo
fator ao mundo*. Para um investidor com prior fundamentalista, o balanço entre
erro Tipo I e Tipo II é outro. Ou seja: rigor alto para **descobrir anomalias**;
rigor calibrado ao prior para **alocar capital**.

---

## 5. A reconciliação: trocar O QUE se testa, não o QUANTO

A síntese Damodaran + Grinold-Kahn + qualidade é: **parar de gatear no p-value do
retorno de 24 meses** (frágil, macro-contaminado) e passar a gatear em três
coisas robustas.

| Em vez de… | Usar… | Fonte |
|---|---|---|
| p-value do retorno em 24m | **Significância do Rank-IC** (sinal fundamental prevê retorno, muitos anos × nomes) | Grinold & Kahn |
| Um único holdout recente | **Consistência através de ciclos** (bater os pares em K de N anos) | McCloskey & Ziliak |
| Retorno bruto | **Persistência de qualidade**: ROIC/margem estáveis e ROIC > custo de capital ao longo do ciclo | Damodaran; Novy-Marx; AQR |

Isso separa explicitamente o **transitório (macro)** do **estrutural (empresa)**:

- O **macro** já é neutralizado pelo benchmark de **Pesos Iguais** (se o setor
  caiu, os pares caíram junto).
- O **estrutural** entra medindo a **resiliência dos fundamentos no ciclo ruim**:
  a empresa manteve ROIC > custo de capital e margem estável quando o macro
  apertou? Isso distingue "ação que caiu por juros" de "empresa que se
  deteriorou".

---

## 6. Proposta de implementação — aprovação em duas trilhas

Sem afrouxar o rigor: apenas **realocá-lo** para o alvo certo.

1. **Trilha estatística de amplitude** — significância do **Rank-IC** (não do
   retorno): muitos anos × nomes → alto poder, macro-neutro.
2. **Trilha de resiliência estrutural** (métricas Damodaran) — estabilidade de
   ROIC/margem através dos ciclos, spread ROIC − custo de capital positivo, e
   drawdown-e-recuperação dos *fundamentos* (não do preço) nas recessões
   (2015-16, 2020, 2022+).
3. **Gate por consistência econômica** — bater os pares em K de N anos, no lugar
   do p-value binário de janela única; opcionalmente, um **prior bayesiano** em
   que a qualidade dá "crédito" de evidência.

Efeito esperado: casos como o Petróleo (Rank-IC 0,117, +13,7% vs pares) tendem a
**passar** — porque a régua premia justamente o que se busca: fundamento sólido
que atravessou adversidade conjuntural, sem abrir mão da robustez estatística.

---

## 7. Referências

- **Damodaran, A.** — *Investment Valuation*; *Narrative and Numbers*; *The Little
  Book of Valuation*. (Valor intrínseco, normalização mid-cycle, separação
  macro × empresa.)
- **Grinold, R. & Kahn, R.** — *Active Portfolio Management*. (Lei Fundamental:
  IR = IC × √amplitude.)
- **Novy-Marx, R. (2013)** — "The Other Side of Value: The Gross Profitability
  Premium".
- **Asness, C., Frazzini, A. & Pedersen, L. (AQR)** — "Quality Minus Junk".
- **McCloskey, D. & Ziliak, S.** — *The Cult of Statistical Significance*.
- **Harvey, C., Liu, Y. & Zhu, H. (2016)** — "…and the Cross-Section of Expected
  Returns". (Contraponto: barras altas para *descoberta* de fatores.)
- **Black, F. & Litterman, R.** — modelo de combinação de priors com o mercado.

---

*Documento gerado como material de apoio à decisão metodológica. As referências
sintetizam as abordagens; a implementação sugerida na seção 6 permanece opcional
e sujeita à sua calibração.*
