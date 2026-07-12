# Relatório de Modificações — Criação de Portfólio B3

> Complemento ao "Guia da Metodologia". Aqui explico **o que mudou recentemente** e
> **por quê**, em nível intermediário. Foco na mudança conceitual mais importante:
> a régua de aprovação passou a medir **habilidade de seleção**, não **timing macro**.

---

## 1. Resumo do que mudou (visão rápida)

| # | Mudança | Tipo |
|---|---|---|
| 1 | **Aprovação passou a exigir bater os Pesos Iguais do segmento** (habilidade de seleção), não bater a Selic | Metodologia |
| 2 | **Margem vs Selic virou diagnóstico** — não reprova mais | Metodologia |
| 3 | Descoberta: **o score é de qualidade, não de "barato"** | Auditoria |
| 4 | **Termos em inglês traduzidos** para português + glossário na tela | Interface |
| 5 | Correções de erros que travavam a aba (dados antigos / segmentos sem preço) | Bug |
| 6 | Filtro na aba Tabelas **não volta mais para o Dashboard** | Bug |

---

## 2. A mudança principal: medir habilidade, não timing

### Antes: bater a Selic no holdout (o problema)

A régua antiga exigia que a estratégia **batesse o Tesouro Selic** nos ~24 meses
finais (o holdout). Parece razoável — mas cria um efeito perverso: é **pró-cíclica**.

Num período de **juros altos** (como agora, Selic ~11–15%), a Selic acumula ~30% em
dois anos. Isso é uma barra que **derruba o segmento inteiro**, boas e más empresas
juntas. Uma empresa excelente que só está barata **por causa do macro ruim** era
reprovada junto com o lixo. Como você mesmo apontou: *é justamente da dissociação
entre a qualidade da empresa e um macro que pune a todas que nascem as melhores
oportunidades.*

### Depois: bater os Pesos Iguais do próprio segmento

A régua nova pergunta outra coisa: **a escolha dos líderes superou uma carteira que
simplesmente compra todas as empresas do segmento em partes iguais?**

Essa referência — "Pesos Iguais" — é a chave, porque ela **neutraliza o macro**:
se o cenário derrubou o segmento todo, os Pesos Iguais caíram junto. O que sobra na
comparação é **só o que a seleção acrescentou** — ou seja, a **habilidade** de
escolher as melhores dentro do grupo, e a **resiliência** delas quando o mar está
bravo para todos.

### Antes × depois

| Pergunta que a régua faz | Antes | Depois |
|---|---|---|
| Base de comparação da aprovação | Tesouro Selic | **Pesos Iguais do segmento** |
| Sensível ao regime macro? | Sim (pró-cíclica) | **Não (neutra)** |
| Papel da margem vs Selic | Reprovava | **Só diagnóstico** |
| O que é medido | Ações batem a renda fixa agora? | **A seleção bate os pares?** |

### O que continua exigido (o rigor não caiu)

- **Significância estatística** do excesso vs Pesos Iguais (valor-p) com **controle
  de falsos positivos** (Benjamini-Hochberg, q ≤ 10%).
- **Poder preditivo** consistente: Rank-IC de pelo menos 2 anos, positivo — a
  pontuação de qualidade realmente antecipou o retorno, no histórico inteiro.
- **Recência de liderança.**

Ou seja: tiramos a régua **errada** (timing macro) e mantivemos as réguas **certas**
(habilidade + evidência estatística).

---

## 3. Por que isso é mais correto (a intuição)

Pense em dois zeladores de dois prédios na mesma rua, num temporal que alaga a rua
toda. Julgar cada um por "a rua do seu prédio ficou seca?" é injusto — choveu para
todos. O justo é: **o seu prédio alagou menos que os vizinhos?** Isso mede a
competência do zelador, não a sorte do tempo.

Os "Pesos Iguais" são "os vizinhos". Bater a Selic era "a rua ficou seca" —
depende do tempo (macro). Bater os pares é competência (seleção).

E o Rank-IC, medido em **todos** os anos, é a prova de longo prazo de que a
pontuação de qualidade **prevê** retorno — independentemente do regime.

---

## 4. Descoberta importante: o score é "qualidade", não "barato"

Você chamou o score de "valuation". Auditei os pesos que ele usa (`_PESOS_SETOR`) e a
verdade é mais precisa:

- O score **pesa forte**: ROE (retorno sobre patrimônio), ROIC (retorno sobre
  capital investido), margens, dividendos e **tendências** desses indicadores.
- O score **quase não usa**: múltiplos de preço (EV/EBIT aparece pouco; **não há
  P/L nem P/VP** com peso relevante).

**Tradução:** o score identifica a **melhor empresa** — esteja ela cara ou barata.
Ele garante o "qualidade", mas **não** garante o "barato".

### Por que isso importa para a sua tese

Sua estratégia é *"comprar boas empresas quando o macro as deixou baratas"*. Para
isso, faltaria uma **dimensão de preço/valuation** no score (P/L, P/VP, EV/EBITDA).
Hoje o modelo escolhe qualidade, mas não olha se está na promoção. **Não alterei
isso** — muda o que "melhor empresa" significa no app inteiro, é decisão sua.
(Posso adicionar um componente de cheapness configurável, se quiser.)

---

## 5. Como ler a tela agora (termos traduzidos)

Os termos em inglês da tela Criação de Portfólio foram traduzidos, e há um
**glossário** logo abaixo dos parâmetros. Equivalências principais:

| Antes (inglês) | Agora (português) | O que é |
|---|---|---|
| Equal-Weight | **Pesos Iguais** | Carteira que investe igual em todas as empresas do segmento |
| holdout / out-of-sample | **janela de validação (fora da amostra)** | Os ~24 meses finais, não usados para montar a estratégia |
| p-value | **valor-p** | Chance do resultado ser sorte (quanto menor, melhor) |
| FDR / Benjamini-Hochberg | **controle de falsos positivos** | Ajuste por testar dezenas de segmentos de uma vez |
| Rank-IC | **poder preditivo** | O quanto a pontuação acertou o retorno futuro |
| Status | **Situação** | Aprovado / Reprovado |

*Siglas de mercado consagradas (ROE, ROIC, DY) foram mantidas, com dica em
português.*

---

## 6. Correções de erros (rápido)

- **"KeyError: val_est_oos"**: resultados de uma execução antiga (anterior ao
  overhaul) ficavam salvos na sessão e quebravam a tela. Agora são detectados e o
  app pede para rodar de novo.
- **"cannot convert float NaN to integer"**: segmentos cujos tickers não têm preços
  (sem dados) travavam o processamento. Agora são pulados com segurança.
- **Aba Tabelas voltava para o Dashboard** ao aplicar o filtro de categoria. A
  navegação agora persiste na aba escolhida.

---

## 7. O que continua igual (o rigor que permanece)

- **Point-in-time**: a pontuação de cada ano só usa dados que já estavam
  disponíveis naquela data (sem "olhar o futuro").
- **Holdout de validação**: a aprovação é julgada em dados que a estratégia não viu
  ao ser montada.
- **Correção de falsos positivos + poder preditivo**: nada é aprovado por sorte
  entre dezenas de tentativas.

A mudança **não afrouxou** o rigor — ela **realinhou** o rigor para o alvo certo
(habilidade de seleção, neutra ao macro).

---

## 8. Próximos passos possíveis (opcionais)

1. **Adicionar cheapness ao score** (P/L, P/VP, EV/EBITDA) — para a estratégia
   capturar "qualidade barata", com peso qualidade × preço que você calibra.
2. **Fallback de granularidade**: juntar segmentos com poucas empresas (< 5) ao
   subsetor/setor, para que o poder preditivo (Rank-IC) possa ser medido.
3. **Janela de validação de 36 meses** (mais robustez estatística) como alternativa
   aos 24 meses.

---

*Documento gerado como material de apoio. Reflete as modificações até esta data.*
