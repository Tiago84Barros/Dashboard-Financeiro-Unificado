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


---

## 13. Estatística dimensionada à amostra (25/07/2026)

Terceira e última peça da resposta à objeção da §16 da auditoria. As duas
anteriores contornaram o problema (rota de valor; três estados de evidência).
Esta ataca a causa: **o desenho do teste estava errado para o tamanho da
amostra**.

### O diagnóstico, em um número

Simulação com as amplitudes REAIS da B3 (78 segmentos, mediana de 3 empresas,
438 no total), 10 anos, α=10%:

| Desenho | Efeito mínimo detectável (Rank-IC) |
|---|---:|
| Um teste por segmento (3 empresas) | **0,533** |
| Um teste no universo (438 empresas) | **0,027** |
| | **19× mais sensível** |

Um Rank-IC de 0,533 não existe em mercado nenhum: sinais fundamentalistas reais
vivem na faixa de 0,02 a 0,10 (Grinold-Kahn). Ou seja, **o teste por segmento
era estruturalmente cego a qualquer efeito realista** — ele nunca poderia
aprovar por mérito, só por acaso. Na mesma simulação, com sinal verdadeiro
**igual a zero**, apenas 27 dos 78 segmentos eram sequer mensuráveis e 1
"passava" por sorte.

### O que mudou

1. **Teste no nível do universo** (`pooled_yearly_ics` + `universe_evidence`):
   o Rank-IC anual passa a ser calculado sobre todas as empresas de cada ano,
   num único teste com amplitude real — e sem multiplicidade a corrigir, já que
   é um teste, não 78.
2. **Encolhimento hierárquico** (`shrink_segment_estimates`, empirical Bayes por
   método dos momentos): a estimativa de cada segmento é puxada para a média do
   universo na medida da própria incerteza. Na simulação com ruído puro, os ICs
   brutos mais extremos (−0,186; +0,151) foram encolhidos a 0,000 com peso
   próprio 0% — exatamente o que deveria acontecer com ruído.
3. **Segmento sem observação recebe a estimativa do universo**, não uma
   reprovação: sem dado próprio, a melhor inferência disponível é a do mercado.

A seção "🔬 Evidência no universo" exibe o Rank-IC agrupado, o valor-p, a
amplitude (anos × empresas/ano), o efeito mínimo detectável e a tabela de
estimativas encolhidas com o peso próprio de cada segmento.

### O que continua valendo

Sobrevivência, publication lag e o guarda-corpo anti-preditivo não dependem de
amplitude e seguem ativos. A rota econômica continua sendo o padrão para
aprovar segmentos; o teste de universo informa se o score, como um todo, tem
poder preditivo no mercado brasileiro — pergunta que agora pode ser respondida.

---

## 15. Perfis pré-configurados (29/07/2026)

A aba tem cerca de vinte parâmetros. Alguns, mal calibrados, degradam a
carteira **sem sinal visível** — o usuário atribui ao mercado o que foi da
configuração. Os perfis resolvem isso com combinações cujo efeito foi
**medido** na varredura automatizada sobre o universo real.

| Perfil | Para quê | Efeito medido |
|---|---|---|
| **Equilibrado (recomendado)** | uso normal | 10 ativos · 60% cíclico · 20% defensivo · 7 setores |
| **Conservador** | menos exposição ao ciclo | carteira menor por construção |
| **Amplo (diagnóstico)** | explorar o universo | sem proteção de concentração — não use para decidir |

O perfil recomendado é `Econômico (Brasil)` · margem 15% · teto setorial 30% ·
teto cíclico 60% · resiliência desligada · grupo mínimo 5. Cada valor tem
evidência:

* modos estatísticos aprovam **zero segmentos** (efeito mínimo detectável de
  0,533 é inalcançável com mediana de 3 empresas por segmento);
* resiliência a 5 p.p. corta de 10 para **6 ativos** e penaliza utilities
  (20% passam) mais que cíclicas (27%);
* margem de 25% cai para 7 ativos; 5% não muda o resultado;
* tetos 30%/60% foram a combinação verificada **sem conflito** entre si.

### Alertas de calibragem

Alterar um parâmetro com custo conhecido dispara um aviso **com o número
medido** — por exemplo, ligar resiliência a 5 p.p. avisa que cortaria de 10
para 6 ativos. Os alertas informam; nunca bloqueiam. A decisão continua do
usuário, agora sabendo o preço.

### O que os perfis NÃO são

Não são "carteira ótima". Isso não existe de forma verificável fora da
amostra, e persegui-lo por iteração é sobreajuste — o erro que a §16 da
auditoria corrigiu. São combinações **medidas e reprodutíveis**, com o custo
de cada desvio declarado.

## 16. Proteção ao investidor pelo histórico — e a guarda que ainda não foi exercitada (15/09/2026)

A seleção passou a olhar a **qualidade histórica** da distribuição, não o
exercício isolado: payout mediano, sustentabilidade em N anos observados e a
fração dos pares de anos em que o patrimônio cai com lucro positivo. Um ano
ruim não condena; um padrão que se repete na maioria dos anos, sim.

### A regra que não pode ser quebrada

Nenhuma dessas proteções pode deixar **zerada** a criação de um portfólio. Por
isso a quarta confirmação (payout mediano alto **e** patrimônio em queda) vem
acompanhada de uma guarda de viabilidade: se o critério novo for o único motivo
para reprovar o representante de um segmento, o representante entra **marcado**,
não excluído.

### O que a medição encontrou — e o que ela não prova

Medição de 15/09/2026, três pontos da linha do tempo rodados no mesmo instante
contra o mesmo banco (pré-plano, base e ramo), sobre 426 empresas:

| Grandeza | Antes | Depois |
|---|---|---|
| Empresas avaliadas pelo piso | 444 (248 aprovadas) | 444 (248 aprovadas) |
| Segmentos com representante | 67 de 78 | 67 de 78 |
| Carteira recomendado / conservador / amplo | — | 8 / 5 / 16 |

Nenhuma carteira ficou vazia e nenhum segmento perdeu representante: os 11
segmentos sem representante são a mesma lista antes e depois.

**A ressalva honesta**: a quarta confirmação decisiva ocorre hoje em **0 das 426
empresas**, e a guarda de viabilidade foi acionada **0 vezes** em todos os
perfis. A promessa "nunca zera" está verificada, mas se sustenta **trivialmente**
— a guarda nunca precisou salvar ninguém, logo não foi validada em produção.

Isso importa porque este projeto já tropeçou no mesmo formato: um critério que
só podia dar um resultado nunca é revisto, e no dia em que a fonte de dados
melhora ele passa a morder a base inteira de uma vez. A cobertura de payout é
justamente o que hoje mantém o número em zero. **Quando ela subir, a guarda
entra em operação sem nunca ter sido exercitada.** Remedir nessa hora, antes de
confiar no resultado.

### 16.1 A bandeira que acendia para quase todo mundo (15/09/2026)

O dossiê determinístico emite as suas observações numa lista só, `red_flags`, e
os três consumidores dela — o texto que alimenta o parecer da LLM no gate de
seleção, a tela de Empresas B3 e a de Análise de Portfólio — imprimiam todas sob
o mesmo cabeçalho, "RED FLAGS DETERMINÍSTICAS (verificadas em código, não são
opinião)". A lista, porém, carrega três coisas incomparáveis: risco confirmado,
observação medida que o próprio texto já descarta como padrão, e lacuna de
dados.

Medido no armazém local sobre as **426 empresas** com fundamentos, antes da
correção:

| o que acendia a bandeira vermelha | empresas |
|---|---|
| risco confirmado de verdade (linha em CAIXA ALTA) | **8** |
| só `MOMENTUM:` — lucro de **um** trimestre a/a abaixo de −25% | 94 |
| só `DADOS:` — inconsistência da **nossa** ingestão de proventos | 50 |
| combinações e demais casos | 38 |
| **total sob "red flag"** | **190** |

Três quartos das bandeiras vermelhas eram um período isolado ou um defeito do
nosso próprio banco. Uma bandeira que acende para 190 e descreve 8 não distingue
ninguém — e a LLM que decide `classificacao_selecao` lê o cabeçalho antes da
linha. Marcar a companhia de perigosa porque *nós* gravamos dois valores de
provento na mesma data-ex é a ponderação indevida na sua forma mais nítida.

A correção não silenciou nada: as três categorias continuam impressas e
continuam chegando ao parecer, agora apartadas em "RISCO CONFIRMADO",
"OBSERVAÇÕES DE CONTEXTO" e "LIMITAÇÕES DE COBERTURA". Silêncio no dossiê
lê-se como "nada encontrado", que seria pior que a diluição. O que mudou é o
cabeçalho. Depois: **8 de 426** sob risco confirmado.

A regra de classificação mora num único lugar (`core/dossie_b3.py`), e a
unicidade é verificada por AST — este projeto já teve três cópias da mesma
guarda com duas divergências entre elas. Cada um dos três consumidores tem um
teste que morre se a separação for desfeita.

**O limite desta correção**: a separação no texto é verificável; a obediência da
LLM a ela não é. A regra 5.4 do prompt instrui que contexto e cobertura não
reprovam, mas instrução não é portão. Medir isso exige rodar o gate com LLM
ligada (`scripts/eval_gate_selecao.py`), o que não foi feito aqui.

## 17. Vigência da safra (22/09/2026)

A safra N é pontuada com dados até N−1 (`lag = 1`) e vigora de **01/04/N a
31/03/N+1**: os balanços do exercício N−1 só são públicos até 31/03 (CVM). A
regra vive em `core/b3_vigencia.py` e é a única definição do mês de rebalance
no projeto — `tests/test_b3_vigencia.py::test_mes_de_rebalance_definido_num_lugar_so`
varre `views/` e `core/` por AST e falha se outro arquivo redefinir o mesmo
valor (por nome em inglês *ou* em português, ex.: `_MES_REBALANCE = 4`).

O relatório de safras (`core/b3_safras.py`, desenhado por
`views/portfolio_b3_safras.py`) reconstrói a carteira de cada safra — os
líderes por segmento com o orçamento que o score daquele ano definiu — e mede
o retorno da janela de vigência contra Selic e equal-weight. Três
características da medição, verificadas no código:

1. **Viés de universo — medido, não só declarado.** O conjunto de segmentos
   elegíveis vem do teste OOS sobre a amostra inteira, até hoje; uma safra
   antiga é reconstruída com segmentos que só foram aprovados por evidência
   posterior a ela. Isso não dá para tornar point-in-time (nas primeiras
   safras não há janela OOS e nenhum segmento seria aprovado). Em vez de só
   avisar, o bloco "Tamanho do viés de universo" (`render_vies_universo`,
   sob demanda via botão — é a reconstrução mais cara da tela) recalcula as
   mesmas safras sem o gate de aprovação e publica a distância entre as duas
   curvas.
2. **Pós-filtros não entram na reconstrução histórica.** `carteiras_por_safra`
   monta a carteira de cada safra direto de `lids_por_ano`/`pesos_por_ano` —
   os líderes por segmento e os pesos do score daquele ano — sem repassar o
   piso de liquidez nem a diversificação por correlação que a carteira atual
   aplica. Diferente do viés de universo, isto **não tem caption própria na
   tela**: é uma propriedade do código (`core/b3_safras.py`), não um aviso
   visível para quem lê o relatório.
3. **Peso sem preço rende zero, e não é redistribuído.** Um ticker sem preço
   válido na janela (deslistagem, incorporação, buraco de dado) fica com sua
   fatia em `peso_ausente` na estratégia e entra com `0.0` no equal-weight —
   nos dois casos, sem redistribuir o orçamento entre os sobreviventes.
   Redistribuir inflaria os dois lados da comparação com "a média de quem
   sobreviveu", que é sistematicamente mais forte que o mercado que a conta
   deveria descrever. A tela publica isso: quando `peso_ausente` de alguma
   safra medida é maior que zero, aparece um aviso dizendo que aquela fatia
   "rende zero" e que a perda "também não rende o que os sobreviventes
   renderam".

### Limitação conhecida: a chamada do relatório está provada por forma, não por alcance real

`tests/test_portfolio_b3_safras.py` tem um verificador de alcançabilidade por
AST (`_alcancavel`) que confirma que `render_safras` é chamada dentro de
`render()` e que nenhum `return`, nem um `if` com condição **constante**
conhecida (`if False:`, uma variável atribuída a `False` no próprio corpo, ou
uma constante de módulo), esconde essa chamada como código morto. Isso
substituiu um teste mais fraco que só verificava a *presença* do nó na árvore,
sem checar se o fluxo de execução realmente chegava até ele.

Mas o verificador tem um limite deliberado: quando a condição de um `if` **não
é uma constante que ele sabe avaliar** — por exemplo, uma variável calculada em
tempo de execução a partir de dados (sessão, banco, resposta de rede) —, ele
trata os dois ramos como potencialmente alcançáveis, porque não pode provar o
contrário sem executar o código de fato. Isso é uma escolha correta para não
gerar falso-positivo, mas tem um efeito colateral: se algum dia a chamada de
`render_safras` (ou de `render_expectativa`/`render_vies_universo` dentro
dela) ficar dentro de um `if` cuja condição de runtime é **sempre falsa na
prática** — uma flag de sessão que nunca é setada, uma lista que chega vazia
por um bug upstream —, o verificador continua passando (porque não consegue
provar que a condição é sempre falsa), e a tela inteira — Blocos 1, 2 e 3 —
some da produção com a suíte inteira verde. Não é uma correção pendente; é uma
lacuna estrutural do que um teste de AST consegue provar sobre condições que
dependem de dado em tempo de execução.
