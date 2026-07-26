# Guia da Metodologia de Criação de Portfólio B3

> Documento explicativo para uso pessoal. Nível: intermediário.
> Objetivo: entender **o que** a aba "Criação de Portfólio" faz, **por que** ela é
> rigorosa, **como ler** o resultado e **o que fazer** com ele como investidor.

---

## 1. Em uma frase

A aba **Criação de Portfólio** não tenta adivinhar o futuro. Ela faz uma pergunta
honesta e difícil:

> "Existe **prova estatística** de que a minha forma de escolher as melhores
> empresas de cada segmento realmente funcionou — e não foi só sorte?"

Quando a resposta é "não tenho prova suficiente", ela **se recusa a recomendar**.
Isso é uma qualidade, não um defeito. Um filtro que sempre aprova alguma coisa é
um filtro inútil.

---

## 2. O problema central: separar HABILIDADE de SORTE

Imagine 64 pessoas jogando uma moeda 12 vezes cada. Por puro acaso, **alguém** vai
tirar cara 10 ou 11 vezes. Se você olhar só para essa pessoa e disser "que talento!",
está confundindo **sorte** com **habilidade**.

A bolsa é parecida. Com 64 segmentos e 12 anos de histórico, **sempre** vai existir
um segmento que, por acaso, teve um desempenho espetacular no passado. O perigo é
olhar para esse número bonito e comprar — porque no futuro a sorte não se repete.

Toda a metodologia existe para resolver **exatamente esse problema**: não deixar a
sorte se disfarçar de habilidade.

---

## 3. Os conceitos-chave (glossário com analogias)

### 3.1. Backtest (reconstrução histórica)
"Rebobinar" o tempo e simular: *"se eu tivesse seguido essa estratégia desde 2013,
aportando R$ 1.000 por mês, quanto teria hoje?"*. É um teste da ideia contra o
passado real.

### 3.2. In-sample vs Out-of-sample (o conceito mais importante)
- **In-sample ("dentro da amostra")** = medir a estratégia no **mesmo** período que
  você usou para construí-la. É como fazer uma prova **com o gabarito ao lado**:
  você acerta muito, mas isso não prova que aprendeu.
- **Out-of-sample / OOS ("fora da amostra")** = testar em dados que a estratégia
  **nunca viu** ao ser montada. É a **prova nova**, sem gabarito. Só isso mede se o
  desempenho **generaliza** para o futuro.

> **Regra de ouro**: performance in-sample quase sempre **superestima**. A única
> estimativa honesta do futuro é a out-of-sample.

### 3.3. Holdout (~24 meses)
O **holdout** é o pedaço de tempo que você "esconde" para servir de prova nova.
Neste app, são os **últimos ~24 meses**. A estratégia é construída com o histórico
até ali, mas **julgada só nesse trecho final que ela não viu**.

Analogia: você estuda com as provas de 2013–2023, mas seu diploma depende de ir
bem na prova de 2024–2025, que você nunca tinha visto.

### 3.4. Point-in-time / look-ahead bias
**Look-ahead bias** ("viés de olhar para frente") é o erro de usar, numa decisão de
2018, uma informação que só ficou disponível em 2019. É trapaça sem querer.

O app evita isso com **point-in-time**: ao pontuar as empresas para o ano N, só usa
dados que **realmente já estavam publicados** naquela data (campo `AvailableAt`).
Isso é rigor de verdade — muita ferramenta amadora erra aqui.

### 3.5. Rank-IC (Information Coefficient)
Mede se o **score** que o modelo dá às empresas realmente **prevê** o retorno do ano
seguinte. Tecnicamente: a correlação entre "nota que dei" e "quanto rendeu depois".

- Rank-IC **positivo e consistente** = o score tem poder preditivo real.
- Rank-IC **zero ou negativo** = o score não prevê nada; qualquer acerto foi sorte.

É calculado sobre **todos os anos** do histórico — por isso é **independente do
regime** (não depende de a Selic estar alta ou baixa agora).

### 3.6. p-value (valor-p)
Responde: *"qual a probabilidade de esse resultado bom ter sido só sorte?"*
- p-value **baixo** (ex.: < 0,10) = improvável ser sorte → evidência real.
- p-value **alto** (ex.: 0,90) = provavelmente sorte → sem evidência.

### 3.7. FDR — correção de Benjamini-Hochberg
Se você testa **64 segmentos**, alguns vão parecer "significativos" só por acaso
(como as moedas da seção 2). A correção de **FDR** ajusta os números levando em
conta que *"você tentou 64 vezes"*. O resultado ajustado é o **q-value** (quer ≤ 0,10).

Sem FDR, você aprovaria falsos positivos. Com FDR, só sobra o que é robusto de verdade.

### 3.8. Survivorship bias (viés de sobrevivência)
Se a base de dados só tem as empresas que **sobreviveram** e esquece as que
faliram/saíram (Oi, Americanas, etc.), o backtest fica **otimista demais** — você
só vê os vencedores. É um risco de **dados**, não de método.

---

## 4. Como funcionam os "portões" de aprovação

Para um segmento ser **Aprovado**, ele precisa passar em **todos** estes portões
(medidos no holdout OOS, exceto o Rank-IC que usa todo o histórico):

| Portão | O que exige | Por quê |
|---|---|---|
| **Rank-IC** | ≥ 2 anos, média positiva | O score precisa prever retorno (habilidade) |
| **p-value OOS** | baixo (significativo) | O desempenho recente não pode ser sorte |
| **FDR (q-value)** | ≤ 10% | Corrige o "tentei 64 vezes" |
| **Margem vs Selic** | ≥ 10% (default) | Piso econômico leve (bater o risco-livre) |
| **Recência de liderança** | líder recente | Evita apostar em glória antiga |

**Quem manda é a estatística.** A margem vs Selic é só um piso leve — não é ela que
decide.

---

## 5. Como ler a tabela de auditoria

Ordem das colunas:

`Setor` · `Subsetor` · `Segmento` · `Status` · **`vs Selic OOS`** · **`vs EW OOS`** ·
**`p-value OOS`** · **`q-value BH`** · `Meses OOS` · **`Rank-IC médio`** ·
`Anos Rank-IC` · `Patrimônio` · `Últ. liderança`

Leitura rápida de uma linha (exemplo real — Incorporações):
- `vs Selic OOS = +39%` → nos 24 meses, bateu a Selic em 39%.
- `vs EW OOS = +58%` → bateu a média do próprio segmento em 58% (bom sinal de seleção).
- `Rank-IC = 0,20` → score previu bem o retorno (o melhor da tabela).
- `p-value = 0,42` → **mas** ainda há 42% de chance de ser sorte → não significativo.
- `q-value = 1,00` → depois do FDR, totalmente insignificante.
- **Veredito: Reprovado.** Promissor, mas sem prova estatística suficiente.

Linhas com `Patrimônio = R$ 0,00` e `Meses = 0` = segmentos **sem dados de preço**
(não são reprovações reais, são ausência de dado).

---

## 6. Por que a rodada recente deu 0 aprovados

Três causas somadas — **nenhuma delas é bug**:

1. **Janela recente = Selic altíssima.** Nos últimos ~24 meses a Selic acumulou
   ~30%. Ação teve que fazer isso **e mais um pouco** só para empatar. Poucas
   conseguiram — reflexo real do mercado, não erro.
2. **FDR entre 64 testes.** A barra de significância sobe quando você testa muitos
   segmentos. Isso mata os "bonitos por acaso".
3. **Granularidade fina demais.** Metade dos segmentos tem menos de 5 empresas/ano,
   então nem dá para medir Rank-IC (`Anos Rank-IC = 0`). Sem dados, sem aprovação.

O caso mais ilustrativo: **Equipamentos/Saúde** teve **+67% vs Selic** e mesmo assim
foi reprovado — porque `p-value = 0,996`, ou seja, aquele +67% **não é distinguível
de sorte**.

---

## 7. Como interpretar isso como investidor

**0 aprovados = "o modelo não tem convicção agora".** Não é "ações são ruins" nem
"venda tudo". É o filtro sendo honesto.

O que fazer:
1. **Não force uma carteira a partir de ruído.** Zero aprovados é uma resposta
   válida. Com a Selic alta, ficar no risco-livre é uma decisão racional que o
   próprio modelo está sinalizando.
2. **Use os "quase" como watchlist, não como lista de compra.** Segmentos com
   Rank-IC positivo real (Incorporações 0,20; Bancos 0,14) têm **algum** sinal —
   valem acompanhar, não comprar às cegas.
3. **Cuidado com sorte disfarçada.** Energia Elétrica teve +15% vs Selic mas
   **Rank-IC negativo** — margem sem poder preditivo. É "número bonito" que a régua
   está certa em ignorar.
4. **A alavanca que importa não é a margem.** Baixar a margem não destrava a
   carteira, porque o gargalo é a **significância** (p-value/FDR). Para ter nomes
   você teria que afrouxar a estatística — exatamente o que não se deve fazer.

---

## 8. A tensão com "comprar aos sons dos canhões" (Buffett / contrarianismo)

Você levantou um ponto legítimo: *um filtro rigoroso não pune empresas excelentes
que só estão passando por um momento macro ruim — momento que seria justamente a
hora de acumular barato?*

**Sim, em parte.** O gate atual é **pró-cíclico**: ele aprova o que foi bem
recentemente e reprova o que apanhou recentemente. Se uma empresa ótima acumulou
posições baratas na janela ruim, o payoff disso vem **depois** do fim do holdout — e
o holdout não enxerga o futuro. Sua intuição tem base técnica real.

**Mas cuidado com o contrarianismo ingênuo.** Lembre: ~20% das ações brasileiras
perderam **mais de 90%** em 15 anos (Oi, Americanas, Gol...). Às vezes o "som dos
canhões" é o navio realmente afundando. O Buffett de verdade não compra "o que caiu"
— ele compra **valor intrínseco com margem de segurança**, após análise
fundamentalista. É uma disciplina, não a simples inversão do momentum.

O ponto-chave são **dois eixos de decisão diferentes**:

| Pergunta | Ferramenta certa |
|---|---|
| "Meu processo de seleção tem habilidade comprovada?" | A régua estatística (esta aba) |
| "Esta empresa está barata vs valor intrínseco? Vale acumular no ciclo ruim?" | Análise fundamentalista de valuation |

A aba de Criação de Portfólio responde a **primeira**. A decisão contrária de
timing é a **segunda** — e essa é sua, como investidor, não da estatística.

> **Ajuste possível (sem afrouxar o rigor):** exigir significância do excesso
> **vs Equal-Weight do próprio segmento** (isola habilidade de escolha e neutraliza
> o regime — se o mercado todo caiu, o EW caiu junto), e rebaixar a margem vs Selic
> de "critério de reprovação" para "informação de alocação exibida". Assim o modelo
> continua rigoroso sobre o que **pode** provar (habilidade de seleção) e devolve a
> você a decisão de timing/contrarianismo.

---

## 9. O ponto cego que o rigor NÃO cobre: qualidade dos dados

Todo esse rigor blinda a **seleção**, não os **dados**. Se o histórico tiver
survivorship ou ajuste errado de eventos (splits, dividendos), **lixo entra, viés
sai** — e a estatística não percebe.

Sobre as fontes do app:
- **Preços de ações**: Brapi (banco `market.*`) ou yfinance — agregadores gratuitos.
  Bons de cobertura, **não** grau institucional. Pontos frágeis: survivorship,
  ajuste de eventos, buracos em ilíquidos/antigos.
- **Fundamentos de ações**: cruzados entre **Fundamentus + Status Invest + Brapi**
  (só grava quando concordam). Boa salvaguarda.
- **FIIs**: cotação via Brapi, mas **VPA/P-VP ancorados na CVM** (o regulador
  oficial) — aqui você está bem servido.

Recomendação: confie mais nos dados de **FII** (CVM) do que nos fundamentos de ação
(agregador), e vale um spot-check de survivorship (o universo inclui deslistados?).

---

## 10. Resumo em 6 frases

1. A aba mede **habilidade comprovada**, não previsão — e se abstém quando não tem prova.
2. O **holdout** é a "prova nova" que impede confundir sorte com talento.
3. **Rank-IC** = poder preditivo; **p-value/FDR** = não é sorte; **margem** = piso leve.
4. **0 aprovados** hoje = "sem convicção no regime de Selic alta" — resposta honesta, não bug.
5. O **contrarianismo à la Buffett** é um eixo diferente (valuation), que fica com você.
6. O rigor protege a seleção, **não os dados** — atenção a survivorship e ajustes.

---

*Documento gerado como material de apoio. A metodologia continua evoluindo; se algo
mudar no código, este guia deve ser atualizado junto.*

---

## 11. Rota de valor — distorção com solvência (implementada em 25/07/2026)

Resposta à objeção registrada em `docs/auditoria_percentual_2026-07-23.md` §16:
a carteira só tinha o caminho "habilidade de seleção por segmento", que exige
amplitude cross-seccional — e a B3 tem **mediana de 3 empresas por segmento**,
onde nenhum teste de ordenação tem poder. Faltava o caminho que a tese de
crise-como-oportunidade exige.

### Que pergunta esta rota responde

| Rota | Pergunta | Depende de amplitude? |
|---|---|---|
| Segmentos (existente) | "Meu processo de escolha tem habilidade comprovada?" | Sim |
| **Valor (nova)** | **"Está barata vs valor intrínseco E sobrevive para realizar esse valor?"** | **Não** |

São perguntas diferentes; nenhuma substitui a outra. A rota de valor continua
útil quando a de segmentos fica muda.

### Como funciona (`core/b3_value_route.py`, puro e testado)

1. **Margem de segurança** — média das fontes disponíveis (Graham via
   P/L·P/VP; Bazin via DY vs yield-alvo). É a média, não o máximo: escolher a
   fonte mais generosa seria torcer o resultado.
2. **Gate de solvência** — o que separa distorção de armadilha. Reprova FCO
   negativo, margem operacional negativa, endividamento acima do teto, liquidez
   corrente abaixo do piso e ROIC negativo.
3. **Classificação** em quatro estados, sempre com o motivo explícito:
   `oportunidade` · `armadilha_potencial` · `sem_margem` · `sem_evidencia`.

Regra preservada de todo o projeto: **ausência de dado nunca vira aprovação**.
Sem insumo crítico, a empresa fica em `sem_evidencia` e não entra.

ROIC abaixo da Selic é **ressalva, não reprovação** — pode ser vale de ciclo,
que é exatamente a hipótese que a rota existe para capturar.

### Resultado no universo real (25/07/2026, 426 empresas)

| Classe | Empresas |
|---|---:|
| Oportunidade | 96 |
| Armadilha potencial (barrada) | 28 |
| Sem desconto | 148 |
| Sem evidência | 154 |

As armadilhas barradas são o ponto: HBRE3 aparecia com "desconto" de 1025% e
liquidez corrente < 1; JALL3 com 99% e margem operacional negativa, dívida acima
do teto e ROIC negativo. Qualquer filtro de múltiplos as mostraria como
barganhas.

Das 154 sem evidência, **57 tinham desconto ≥ 20%** e ficaram mudas por falta de
insumo (P_FCO ausente em 107 casos, endividamento em 79). A interface lista essas
teses num painel próprio — não como recomendação, mas como medida do custo da
cobertura de fundamentos e fila de prioridade para a ingestão.

### O que a rota NÃO faz

Não diz **quando** comprar. Mostra o que está barato e sobrevive; o timing
continua sendo decisão do investidor — a mesma divisão de eixos da §8.

---

## 12. Três estados de evidência — "inconclusivo" ≠ "reprovado" (25/07/2026)

Segunda correção derivada da auditoria §16. A tabela de auditoria rotulava como
**Reprovado** tanto o segmento cujo score ordenou ao contrário do retorno quanto
aquele que **nunca pôde ser medido**. São coisas opostas.

Com mediana de 3 empresas por segmento, o Rank-IC anual exige ao menos 5 empresas
alinhadas — muitos segmentos simplesmente não geram nenhuma observação. Chamar
isso de reprovação é confundir *ausência de evidência* com *evidência de ausência*.

### O que a tabela mostra agora

| Situação | Significado |
|---|---|
| ✅ Aprovado | passou nos critérios do modo escolhido |
| ❌ Reprovado (evidência contra) | Rank-IC claramente negativo — reprovação de mérito |
| ❌ Reprovado (critério econômico) | não bateu Selic/Pesos Iguais pela margem |
| 🟡 Inconclusivo (sem amplitude) | não houve dados para calcular o Rank-IC |
| 🟡 Inconclusivo (sem significância) | mediu, mas não distingue do acaso |

Duas colunas novas: **Estado da evidência** e **Efeito mínimo detectável
(Rank-IC)** — o menor poder preditivo que o teste enxergaria com os dados
disponíveis, a 80% de poder. Valor alto = teste cego para efeitos moderados.
É o número que faltava para julgar se um "não passou" significa alguma coisa.

`core/b3_evidence.py` é puro e testado; só `evidencia_contra` é bloqueante.

