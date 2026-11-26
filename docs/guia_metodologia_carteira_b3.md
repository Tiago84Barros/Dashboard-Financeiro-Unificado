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
| **Equilibrado (padrão)** | uso normal | 10 ativos · 60% cíclico · 20% defensivo · 7 setores |
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
o retorno da janela de vigência contra Selic e equal-weight. Cinco
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
4. **Janela de 12 meses e Selic realizada (achado D13, 17/11/2026).** Os
   preços são de fim de mês. Até 17/11/2026 a janela começava no primeiro
   preço *dentro* da vigência — o fechamento de 30/04 — e media só 11 meses,
   enquanto a Selic do benchmark rendia os 12. Agora `_janela_de_mercado`
   parte do último fechamento até 01/04 dentro de `TOLERANCIA_DIAS` (o de
   31/03) e vai ao de 31/03 seguinte: safras consecutivas se encadeiam sem
   buraco nem sobreposição. Sem esse fechamento, a janela cai no primeiro
   preço de dentro, como antes. A Selic deixou de ser a meta de fim de ano
   (`taxa_selic_aa`) e passou a ser a **realizada**, composta mês a mês de
   `info_economica_mensal."Selic_Media"` (`load_selic_mensal()`); mês sem
   dado usa a meta do ano e sai nomeado na legenda ("pela meta: 2026-02,
   2026-03"), e a tabela declara a fonte em `attrs["selic_fonte"]`. A medição
   fora da amostra (`core/b3_oos_carteira.py`, 2.5.0) usa a mesma janela —
   `tests/test_b3_safras.py::test_d13_oos_usa_a_mesma_janela_da_tabela`.
5. **Safra inviável ou sem segmento é caixa na Selic, não ano descartado
   (achado D1, 17/11/2026).** Na medição fora da amostra
   (`core/b3_oos_carteira.py::itens_da_cadeia`, desde a 2.4.0), uma safra
   cujos pesos não montam carteira (inviável) ou que não aprovou segmento
   entra na média como caixa rendendo a Selic da janela. Tirar o ano da conta
   seria escolher os anos: até a 2.3 a safra inviável entrava com os pesos
   concentrados que sobravam, e o ano sem segmento simplesmente sumia. Ficam
   fora só o **aquecimento** — as safras anteriores à primeira em que algum
   segmento foi aprovado, quando ainda não há decisão nenhuma a medir — e as
   sem preço observado. A primeira resposta inviável já é uma decisão e vira
   caixa: até a revisão de 17/11/2026 o aquecimento ia até a primeira safra
   *montável*, o que deixava fora a 2017 do Equilibrado e a 2018 do
   Conservador, ambas inviáveis
   (`tests/test_b3_oos_carteira.py::test_aquecimento_so_antes_da_primeira_resposta`).
6. **Tetos que não fecham também são caixa (unificação B3 × FII,
   18/11/2026, medição 2.6.0).** A tela desliga o salvar nos dois casos da
   rota relaxada — poucos ativos para o cap e tetos de setor e de ciclo que
   não fecham (`views/portfolio_b3.py`, `disabled=(not _portfolio_viavel) or
   _rota_relaxada`) —, mas até a 2.5.0 a medição só convertia o primeiro em
   caixa: a safra dos tetos entrava com os pesos relaxados, que a tela não
   deixa salvar. A regra agora é uma função só,
   `core/b3_oos_carteira.py::carteira_salvavel`, lida pela manchete
   (`itens_da_cadeia`) e pela banda do portão (`rendimento_contado`). Os pesos
   relaxados que a tela mostra viram sensibilidade
   (`sensibilidade_pesos_da_tela` no JSON), nunca manchete. Na medição 2.5.0
   gravada, só o Conservador tinha safras nesse caso (2023 e 2025). A Seleção
   de FIIs segue a mesma regra desde a mesma data: período sem carteira
   publicável é caixa a Selic e conta
   (`tests/test_b3_oos_carteira.py::test_banda_e_cadeia_leem_a_mesma_regra_de_salvavel`).

### O alcance da chamada do relatório: por forma (AST) e, desde 22/09/2026, por execução

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
some da produção com a suíte inteira verde. É uma lacuna estrutural do que um
teste de AST consegue provar sobre condições que dependem de dado em tempo de
execução.

**Essa lacuna foi fechada em 22/09/2026 por execução**, e não por um verificador
melhor. `tests/test_portfolio_b3_render_alcance.py` roda
`views/portfolio_b3.py::render()` de ponta a ponta sob 15 dublês (nenhuma rede,
nenhum Supabase) e falha se `render_safras` não for chamada — ou se for chamada
com a carteira vazia, sem `df_precos_all`, ou sem `resultados_todos`. O teste
custa ~4,9 s dentro de `render()` e ~10 s de arquivo.

A prova de que ele morde: envolver a chamada num `if` cuja condição é falsa
**por dado** (`st.session_state.get("pb3_safras_visivel")`, chave que nenhum
caminho do app grava) faz o teste de execução falhar e o verificador de AST
continuar passando — que é exatamente o cenário descrito acima. O verificador
de AST segue no lugar: ele é barato e pega o caso constante; o teste de
execução é o que cobre o caso dependente de dado.

---

## 18. Filtro nota + fatores na rota líder (22/11/2026, medido: fica opcional)

### Que pergunta ele responde

A carteira ampla nota + fatores é a única estratégia da aba com ganho medido
fora do zero (+2,5 pp ao ano sobre pesos iguais), mas não é a carteira que se
salva: a que se salva é a "Líder de cada segmento", concentrada, cujo intervalo
cruza o zero. O filtro pergunta se o sinal amplo, usado **só para vetar**,
melhora a carteira concentrada sem mudar o resto dela.

### A regra (`core/b3_nota_fatores.py::trocar_piores`)

- Os piores são os 30% de pior sinal nota + fatores do universo inteiro: os
  `fica=False` da mesma `carteira_nota_fatores` que a tela mostra e a medição
  da carteira ampla usa (`piores_da_tabela`).
- Roda depois do piso de qualidade. Quem está entre os piores sai e entra o
  próximo do ranking do **próprio segmento** que não seja dos piores, nem
  barrado pela guarda de entrada, nem reprovado pelo piso
  (`core/b3_quality_floor.py::reprovados`, sem o afrouxamento de
  viabilidade). O substituto herda o peso. Sem substituto, a vaga se perde.
- Os piores ficam barrados também nas trocas seguintes: Inteligência dos
  Ativos, parecer da IA, troca ON/PN do piso de liquidez (`_veto_liquidez`)
  e diversificação por correlação (`exclui_extra` em
  `views/portfolio_b3.py::_barrado`).
- Na tela é uma caixa, **desmarcada por padrão**. A carteira salva grava em
  `params` se o filtro estava ligado, a versão e as trocas.

### Como se mede (`scripts/medir_lider_filtro_nota_fatores_b3.py`)

A mesma régua da líder (`scripts/medir_oos_carteira_b3.py::_medir_perfil`),
sem mudar nada: PIT, custo do giro, IR, caixa a Selic (D1) e pesos iguais. Ela
roda duas vezes sobre a mesma rodada da aba, sem e com o filtro; o único passo
trocado é a escolha dos segmentos de cada safra. A métrica principal é a
**diferença pareada** por safra do retorno líquido da carteira sem portão (com
filtro menos sem filtro), com média e IC 95%. As duas rodadas começam na mesma
safra, o fim do aquecimento do controle (`_medir_perfil(..., comeca_em=)`):
safra que o filtro esvazia vira caixa a Selic e conta, em vez de adiar o começo
e sair da comparação. A medição é de um perfil; a caixa da tela avisa quando a
aba roda com outro.

**Regra de decisão, registrada antes de medir:** o filtro só vira padrão se a
média da diferença pareada líquida for > 0 **e** o IC 95% excluir o zero.
Senão fica opcional e desligado. A regra não é ajustada depois do resultado.

### Resultado (medido em 22/11/2026, armazém local, perfil Equilibrado, 9 safras)

Diferença pareada líquida, com filtro menos sem filtro: média −0,3 pp ao ano,
IC 95% de −2,2 a +1,1 pp; com IR, −0,3 pp (−2,1 a +1,1 pp). Negativa em 3 das
9 safras (2019, 2023 e 2024). Pela regra registrada antes de medir, a média
não passa de zero: **o filtro fica opcional e desligado**, como já era o
padrão. Não ganhou o direito de ser o padrão, e também não se provou que
piora a carteira. Nada muda na tela; a caixa continua desmarcada.
Números em `data/oos_lider_filtro_nota_fatores_b3.json`.

### O que a medição não cobre

- o portão de LLM (Inteligência e parecer da IA) não roda na medição — vale
  para a líder com ou sem filtro;
- a troca ON/PN do piso de liquidez da tela não é refeita na medição (na tela
  ela respeita o filtro);
- a rota de revisão (`b3_review`) não aplica o filtro.

Com nove safras o efeito tem de ser grande para o IC sair do zero. Um "não
provado" é o resultado mais provável e não quer dizer que o filtro piora a
carteira: quer dizer que ele não ganhou o direito de ser o padrão.

## 19. Cinco carteiras de regra fundamentalista (22/11/2026, medido: duas candidatas)

### Que pergunta elas respondem

A carteira padrão ainda não se distingue de dividir igualmente entre as
empresas. A nota do motor já mistura qualidade, ROIC, dívida, preço e
dividendos, então pôr "mais Buffett" na mesma nota tende a não mudar nada. A
pergunta aqui é outra: uma **regra simples, que o investidor repete em casa**,
bate pesos iguais e a carteira padrão?

### As regras (`core/b3_estrategias_fundamentalistas.py`)

Fixadas antes de medir. Pesos iguais entre os escolhidos, formação em abril.

1. **Rota de valor** — quem a rota da seção 11 classifica como `oportunidade`
   no retrato da safra (margem de Graham/Bazin ≥ 20% e solvência preservada).
   Nunca tinha sido medida fora da amostra.
2. **Fórmula mágica (Greenblatt)** — soma do posto de ROIC e do posto do
   rendimento operacional (1/EV_EBIT). Fica o terço melhor, no mínimo 10
   nomes. Financeiras saem e só entra quem tem EV_EBIT > 0, como no livro.
3. **Qualidade constante** — ROIC acima da Selic do próprio ano em ≥ 70% dos
   últimos 7 exercícios (mínimo 5 com ROIC), lucro de N-1 positivo e maior que
   o de três anos antes, e nenhuma falha da trava de solvência da rota de
   valor. Dado ausente não é falha.
4. **Qualidade segurada por 3 anos** — a regra 3 refeita na primeira safra e a
   cada três. No meio só sai quem quebrou a solvência ou deixou o universo.
   Carteira que zerou é refeita na safra seguinte.
5. **Líder segurado até degradar** — a premissa original do usuário: não
   vender a líder do ano anterior sem que os fundamentos piorem. A carteira da
   safra é a carteira padrão da safra (os líderes, Equilibrado sem portão)
   **mais** os nomes das safras anteriores que não degradaram. Degradar é uma
   de três coisas: deixar o universo, falhar na trava de solvência da rota de
   valor ou ter prejuízo no último exercício (lucro de N-1 ≤ 0). Dado ausente
   não é degradação. O JSON guarda, por safra, os líderes, a carteira e quem
   saiu e por quê.

### Como se mede (`scripts/medir_estrategias_fundamentalistas_b3.py`)

A régua de `scripts/medir_nota_fatores_b3.py::medir_escolha`, que passou a ser
o ponto único das carteiras "de regra": o mesmo universo, saídas e retrato PIT
de `_segmentos_por_safra`, o histórico cortado em N-1 pela mesma regra de
vintage (`_aceita_da_safra`), custo do giro numa cadeia contínua desde a
primeira safra com carteira da medição oficial e caixa a Selic na safra sem
nome. Cada braço é comparado com pesos iguais e, **pareado safra a safra**, com
a carteira padrão (Equilibrado, sem portão). Grava
`data/oos_estrategias_fundamentalistas_b3.json`.

**Regra de decisão, registrada antes de medir:** um braço só vira candidato a
carteira padrão se, líquido do giro, (a) o excesso médio sobre pesos iguais for
> 0 com IC 95% acima de zero, (b) a média sem a melhor safra continuar > 0 e
(c) a diferença pareada contra a carteira padrão tiver média > 0. Com cinco
braços, a chance de um falso positivo sobe para perto de 1 em 4; por isso (a)
sozinho não basta. Nenhum passando, nada muda na tela. O braço 5 entrou depois
dos quatro primeiros, mas antes de qualquer número medido.

### Resultado (medido em 22/11/2026, armazém local, perfil Equilibrado, 9 safras)

Excesso médio líquido sobre pesos iguais, por ano. Os três testes (a), (b) e
(c) são os da regra acima, aplicados sem mudança:

| Braço | (a) excesso | piso do IC 95% | (b) sem a melhor safra | (c) contra a padrão | Veredito |
|---|---|---|---|---|---|
| Rota de valor | +9,9 pp | +3,6 pp | +6,3 pp | +7,0 pp | candidata |
| Fórmula mágica | +5,6 pp | +1,3 pp | +4,4 pp | +2,7 pp | candidata |
| Qualidade constante | +2,7 pp | −2,2 pp | +1,2 pp | −0,2 pp | fora (a, c) |
| Qualidade por 3 anos | +1,0 pp | −4,3 pp | −0,6 pp | −1,9 pp | fora (a, b, c) |
| Líder segurado | −0,6 pp | −5,0 pp | −2,3 pp | −3,5 pp | fora (a, b, c) |

Leitura com cautela:

- "Candidata" é só o direito de ir à próxima etapa. **Nada vira padrão por
  este resultado** e nada muda na tela: estas carteiras ainda não existem no
  app, só no módulo e na medição.
- Com cinco braços, a chance de ao menos um falso positivo é de perto de 1
  em 4. Passaram dois, e o IC da fórmula mágica (+1,3 a +10,0 pp) é estreito
  de um lado só: nove safras não separam sorte de regra com folga.
- A rota de valor tem o melhor número, mas é a que mais depende de poucas
  safras; o teste (b) retira só a melhor, não as duas melhores.
- O líder segurado (a premissa original: não vender a líder sem que os
  fundamentos piorem) rendeu −0,6 pp sobre pesos iguais, com 5 de 9 safras
  negativas e giro médio de 41% ao ano. Segurar quem não degradou **não
  ajudou** nesta janela; o IC cruza o zero e, sem a melhor safra (2021), a
  média cai a −2,3 pp.
- O próximo passo, se houver, é uma rota nova na tela (opcional, com aviso de
  "candidata, nove safras") e uma segunda janela de medição, não a troca da
  carteira padrão.

Números em `data/oos_estrategias_fundamentalistas_b3.json`. A primeira
rodada leu por engano o Supabase (a `SUPABASE_DB_URL` do `.env` vence a
`DATABASE_URL`); refeita no armazém local, deu os mesmos números.

### O que a medição não cobre

- os múltiplos anuais usam o preço do fim do exercício N-1, não o de abril/N;
- o lucro da série de PL é cortado pelo ano do exercício, sem data de
  publicação;
- guarda de entrada, piso de qualidade, tetos do perfil e portão de LLM não
  filtram estas carteiras (no braço 5, os líderes novos já passaram por eles;
  os segurados, não);
- no braço 5 os pesos voltam a ser iguais em abril, então o vencedor antigo é
  aparado todo ano. A tela não faz isso: o histórico dela compra só com o
  aporte. A carteira também cresce com os anos e, com muitos nomes, fica
  parecida com pesos iguais no universo;
- nove safras não separam sorte de regra com folga.

### O desconto de liderança, ligado e desligado (`scripts/medir_decaimento_lideranca_b3.py`)

Na escolha do líder de cada segmento, o score de quem lidera há anos
consecutivos perde 7% por ano, com teto de 30%
(`core/pesos_por_score.py::apply_decay_penalty`). O próprio código diz que ele
existe para "forçar rotação". Isso contraria a premissa do braço 5, e o
desconto nunca tinha sido medido na B3 (na Seleção de FIIs já é medido, com
`--decaimento on,off`).

A medição roda a aba duas vezes no perfil padrão: como está e com o desconto
trocado pela identidade nas duas chamadas (o laço anual e a carteira atual).
Cada rodada tem o seu pickle em `--cache-dir`. A régua é a de
`_medir_perfil`, sem mudar nada, e a rodada sem desconto usa o mesmo fim de
aquecimento do controle. O desconto muda o líder e, com ele, o backtest e a
aprovação de cada segmento; a diferença mede os dois efeitos juntos, como na
tela. O JSON traz, por safra, o giro, o custo e o retorno das duas carteiras.
Grava `data/oos_decaimento_lideranca_b3.json`.

**Regra de decisão, registrada antes de medir:** o desconto sai da B3 só se a
média da diferença pareada líquida (sem desconto menos com desconto, carteira
sem portão) for > 0 e o IC 95% excluir o zero. Senão fica como está.

## 20. Rota de valor enxuta: 10, 15 e 20 ações (22/11/2026, medido: nenhum corte passa)

### Que pergunta responde

A rota de valor foi a melhor carteira da §19, mas segura em média ~39 ações
(de 20 a 57). Quem tem pouco capital não monta isso: corretagem, lote mínimo e
atenção pesam. A pergunta: **se a rota for cortada em 10, 15 ou 20 nomes, o
excesso sobre pesos iguais sobrevive?**

### A regra (`core.b3_estrategias_fundamentalistas.carteira_rota_valor_topn`)

Fixada antes de ver qualquer número, sem parâmetro para ajustar:

- Mesmo filtro da rota completa: classificação `oportunidade`.
- Fica com os **N de maior `valor_score`** da própria rota (60% desconto,
  40% folga de solvência). Desempate por margem de valor e depois ticker.
- Com menos de N oportunidades na safra, fica com todas.
- N ∈ {10, 15, 20}. Pesos iguais, formação em abril, mesma régua da §19
  (9 safras, custos de pessoa física, caixa a Selic em safra vazia).

Medição: `python scripts/medir_estrategias_fundamentalistas_b3.py --enxuta
--cache-dir <pasta>` (grava `data/oos_rota_valor_enxuta_b3.json`; a rota
completa roda junto, para comparar).

### Regra de decisão, registrada antes de medir

Um corte só é candidato se, líquido do giro, passar nos mesmos três testes da
§19: (a) excesso médio sobre pesos iguais > 0 com piso do IC 95% > 0;
(b) média sem a melhor safra > 0; (c) diferença pareada contra a carteira
padrão > 0.

Regras adicionais:

- **Três cortes elevam a chance de falso positivo** (os cortes são
  correlacionados entre si e com a rota completa, que já é uma candidata).
  Não se escolhe o N de melhor número: vale o **menor N que passa nos três
  testes**, porque o objetivo é viabilidade com pouco capital.
- A comparação com a rota completa (`vs_rota_completa`) é **só informativa**:
  mostra o custo do corte, não reprova nem aprova.
- Nenhum corte passando: a rota de valor continua sem versão viável para
  pouco capital, e isso fica registrado aqui. Nada muda na tela.
- Passar não muda a carteira padrão nem a tela. Só abre a próxima etapa
  (outra janela de safras, filtros de liquidez e tamanho do lote por capital).

### Limites já conhecidos

- Nove safras não separam sorte de regra com folga; (b) existe por isso.
- Cortar em N aumenta a concentração: o risco de um nome ruim pesa mais
  (10 ações = 10% cada).
- Liquidez e lote mínimo não entram nesta medição.

### Resultado (medido em 22/11/2026, armazém local, perfil Equilibrado, 9 safras)

A regra acima foi aplicada sem mudança. A rota completa reproduziu a §19
(+9,9 pp, piso +3,6 pp), o que confirma a mesma base de dados.

| Carteira | Ações | (a) excesso | piso do IC 95% | (b) sem a melhor safra | (c) contra a padrão | Giro/ano | Veredito |
|---|---|---|---|---|---|---|---|
| Rota completa | ~39 | +9,9 pp | +3,6 pp | +6,3 pp | +7,0 pp | 48% | candidata |
| Top 20 | 20 | +9,5 pp | −0,4 pp | +4,0 pp | +6,6 pp | 55% | fora (a) |
| Top 15 | 15 | +7,9 pp | −2,3 pp | +1,7 pp | +5,0 pp | 63% | fora (a) |
| Top 10 | 10 | +11,1 pp | −2,7 pp | +2,0 pp | +8,1 pp | 62% | fora (a) |

Leitura com cautela:

- **Nenhum corte passa em (a):** o piso do IC 95% fica abaixo de zero nos três.
  Pela regra pré-registrada, nenhum é candidato. O top 20 chega perto (−0,4 pp),
  mas perto não é passar, e a regra não muda depois de ver o número.
- Os três passam em (b) e (c): a média continua positiva e acima da carteira
  padrão. O que falta é certeza estatística, não direção.
- **O corte aumenta a incerteza.** Top 10 tem o maior excesso médio (+11,1 pp),
  puxado por uma safra de +83,6 pp; sem ela cai para +2,0 pp. Em três das nove
  safras o top 10 perdeu para pesos iguais por 5 a 8 pp.
- Contra a rota completa (informativo), os cortes ficam entre −2,0 e +1,2 pp,
  com IC cruzando zero: **não se distingue** perder ou não com o corte, com
  nove safras.
- O giro sobe de 48% para 55–63% ao ano: o corte troca mais de nomes.
- A rota de valor continua **sem versão viável medida para pouco capital**.
  Nada muda na tela nem na carteira padrão. Próximas perguntas, se o usuário
  quiser: outra janela de safras, filtro de liquidez e capital mínimo por lote.


## 21. A IA lê o que os números não mostram? (22/11/2026, desenho; etapa 1: Opus vaza; Sonnet passa só com números e vaza com texto, 23/11/2026)

### Que pergunta responde

As regras de "qualidade" (§19) tentam adivinhar o futuro com números do
passado e falharam. O que um investidor como Buffett acrescenta é julgamento
sobre a vantagem competitiva duradoura, a gestão e a repetição do lucro.
Pergunta: **uma IA lendo o que era público em março/N dá notas que preveem o
retorno dos 12 meses seguintes, além do que a Rota de valor já capta?**

### O problema: vazamento do futuro

A IA foi treinada com dados até 2026. Em 2019, ela pode "lembrar" o que
aconteceu depois, e a nota passa a ser memória, não leitura. O teste só vale
se isso for controlado.

### Três etapas

1. **Teste de vazamento (primeiro).** Dossiê sem nome, ticker nem data
   (anos relativos a N−1) de uma amostra de empresa-safra. Pergunta à IA qual
   empresa e qual ano. **Acerto > 20% invalida a etapa 2.**
   `scripts/testar_vazamento_ia_b3.py`.
2. **Retroativa, às cegas.** Para cada empresa e safra 2017–2025, a IA lê só o
   público até março/N, anonimizado, e dá três notas de 1 a 5 (vantagem
   competitiva, gestão, repetição do lucro). Prompt fixado antes de rodar,
   3 rodadas para medir a estabilidade.
   - **Medida A (~900 casos):** correlação de postos entre a nota e o retorno
     dos 12 meses seguintes.
   - **Medida B (mesma régua da §19):** Rota de valor só com nota ≥ 4 contra a
     Rota de valor inteira, pareado safra a safra.
3. **Ao vivo.** Notas congeladas em abril/2027, medidas em março/2028. É a
   única etapa sem vazamento possível.

### Regra de decisão, registrada antes de medir

A IA só vira "filtro opcional" (nunca padrão) se: (a) a Medida A for positiva
com piso do IC 95% acima de zero; (b) a Medida B tiver diferença pareada média
> 0, também sem a melhor safra; (c) o vazamento da etapa 1 for ≤ 20%. Se (c)
falhar, o resultado retroativo é descartado, por mais bonito que seja. Nada
muda na tela por esta seção.

### Limites já conhecidos

- A etapa 1 desta primeira rodada usa dossiê só de números (retrato PIT,
  histórico de ROIC e lucro). O texto dos relatórios entra antes da etapa 2.
  Dossiê só de números tende a **subestimar** o vazamento: se já vaza assim, vaza
  mais com texto.
- Anonimizar o texto pode tirar contexto que a IA usaria: pode subestimar o
  valor da IA.
- Nove safras são pouco para a Medida B; a Medida A existe por isso.

### Resultado da etapa 1 (22/11/2026): inconclusivo, o modelo foi fraco

Amostra de 60 empresa-safras (semente fixa), dossiê só de números.

- Acerto do ticker: **6,7%** (4 de 60); ticker e ano: 1,7%. Abaixo do limite de
  20%, mas **a conclusão não vale**.
- **Quem respondeu foi um modelo gratuito do OpenRouter (Nemotron)**, não o
  Claude nem o GPT: a conta da Anthropic estava sem crédito e a cadeia do app
  passou ao próximo elo. Um modelo pequeno reconhece menos empresas do que um
  grande. Se o modelo que daria as notas na etapa 2 for outro, o vazamento
  dele é outro.
- 11 das 60 respostas não vieram em JSON válido, o que também sugere um modelo
  fraco para esta tarefa.
- Os quatro acertos foram em nomes grandes e conhecidos (ROMI3, VIVT3, ITUB4,
  SANB11).

Pela regra, **a etapa 2 não pode começar** até repetir a etapa 1 com o mesmo
modelo que daria as notas, e com crédito para isso. Números em
`data/oos_vazamento_ia_b3.json`.

### Resultado da etapa 1 com Claude (23/11/2026): o Opus vaza, o Sonnet passa raspando

Mesma amostra (semente 20261122, 60 casos de 1.792), mesmo prompt, mesmo
dossiê só de números. Desta vez quem respondeu foi o Claude, pelo Claude Code,
sem gastar crédito da API.

| Modelo | Acerto do ticker | Ticker e ano | IC 95% do ticker (Wilson) | Etapa 2 |
|---|---|---|---|---|
| Claude Opus 5.5 | **50,0%** (30 de 60) | 50,0% | 37,7% a 62,3% | **invalidada** |
| Claude Sonnet 5.5 | 13,3% (8 de 60) | 5,0% | 6,9% a 24,2% | permitida pela regra |

- **O Opus lembra.** Arriscou 35 nomes e acertou 30. Em todos os 30 acertou
  também o ano. Não é palpite por setor: inclui nomes médios como RAPT4,
  MDIA3, JSLG3, EZTC3, DXCO3 e COGN3. Com o Opus, uma nota retroativa seria
  memória do que veio depois. **O Opus fica fora da etapa 2.**
- **O Sonnet fica abaixo do limite**, com 13,3% no ticker. Ele erra o ano com
  mais frequência (5% com o ano certo). Mas o teto do IC passa de 20%, e o dossiê
  só de números tende a subestimar o vazamento (ver "Limites"). O Sonnet é o
  modelo padrão do app (`core/llm_b3.py`).
- **O que isto decide:** pela regra (c), a etapa 2 só pode rodar com o Sonnet.
  Antes dela, a etapa 1 tem de ser repetida com o dossiê de texto, que é o que a
  etapa 2 vai ler. Se o Sonnet passar de 20% com texto, a etapa 2 cai também.
  A regra não muda depois de ver estes números.

**Como foi rodado.** Cada ajudante abriu um contexto limpo, sem nada desta
conversa. Recebeu um arquivo com 20 dossiês e ids opacos (`d001`…), sem
gabarito, e foi proibido de usar outras ferramentas. Cada ajudante usou uma
ferramenta só, a leitura do lote. Foram três lotes por modelo.
`--exportar` grava os dossiês e o gabarito em arquivos separados, e
`--corrigir` pontua as respostas.

Isto difere da etapa 1 pelo app, que faz uma chamada por caso, em dois pontos:

- no lote, a mesma empresa pode aparecer em duas safras, como SANB11, VIVT3 e
  CIEL3, e as séries deslocadas ajudam a achar o ano;
- o modelo é o do Claude Code, não a chamada da API com `effort` médio.

Os dois pontos tendem a aumentar o acerto, o que pesa contra o Opus e não a
favor dele.

Números em `data/oos_vazamento_ia_b3_opus.json` e
`data/oos_vazamento_ia_b3_sonnet.json`.

### Etapa 1 com texto: desenho (23/11/2026, registrado antes de medir)

**O que muda.** O dossiê ganha os textos que a etapa 2 vai ler, além dos
números. São o release de resultados mais recente (até 3.500 caracteres) e os
dois fatos relevantes mais recentes (até 1.200 cada), publicados de 01/01/N-1 a
31/03/N. A fonte é `public.docs_corporativos` com `docs_corporativos_chunks`,
no armazém local (16.731 documentos, 366 tickers).

**O texto só cobre as safras 2024 e 2025.** O documento mais antigo do armazém
é de 02/01/2023. Por isso o teste com texto, e a etapa 2 que ler texto, ficam
nessas duas safras. Cobrir safras antigas exige ingerir o histórico de
documentos da CVM (IPE). Caso sem documento no período sai da amostra.
Documento que no acervo é só a linha de cabeçalho, sem corpo, é pulado e
entra o seguinte do mesmo tipo.

**O que se esconde** (`anonimizar` no script):

- o nome da empresa (do cadastro, pela raiz do ticker, e do cabeçalho
  "Empresa: TICKER (NOME)" que o acervo põe em cada trecho), as palavras
  distintivas dele e as que começam por elas ("Randon" leva "Randoncorp"),
  a razão social do documento e a palavra colada antes do nome
  ("Metalúrgica", que separa GOAU de GGBR);
- a razão social de qualquer sociedade citada ("X Y S.A."), inclusive
  controladora e contraparte, e todo apelido entre aspas fora de uma lista de
  termos jurídicos (ex.: ("Itaú Unibanco" ou "Companhia"));
- o ticker da B3 com ou sem número (B3: GOAU), inclusive o de raiz com
  dígito (B3SA3, desde a revisão de 23/11/2026), o do exterior (OTC, ADR, NYSE),
  o CNPJ, o NIRE e o código CVM, e a sigla que vem logo antes da listagem
  ("O GPA [B3: ...]"), no texto todo;
- site (inclusive ri.empresa.com), e-mail, endereço, a cidade da linha de
  data, a assinatura e sequências de três ou mais nomes próprios;
- os anos, que viram relativos: "N" é o ano em curso, e 3T24 vira 3T(N-1).

**O que fica, e é limite assumido:** marcas e produtos citados no texto (ex.:
os nomes de negócios adquiridos), controladas e contrapartes citadas por um
nome curto (ex.: "BRF" num texto da controladora, "Eletrobras" numa troca de
usinas), nomes de cidade fora da linha de data, o
setor, o tamanho e o tom. Um analista humano também veria isso. O que se mede
é se o modelo reconhece a empresa por esse conjunto.

**A regra não muda:** acerto do ticker acima de 20% invalida a etapa 2 com o
Sonnet. A amostra é nova, porque o universo mudou: 60 casos sorteados com a
mesma semente entre as empresa-safras de 2024 e 2025 que têm texto. Mesmo
modelo (Sonnet 5.5), mesmos lotes limpos de 20 dossiês, mesmo `--corrigir`.

### Etapa 1 com texto: resultado (23/11/2026)

| Modelo | Dossiê | Acerta o ticker | IC 95% (Wilson) | Ticker e ano |
|---|---|---|---|---|
| Sonnet 5.5 | números + texto | **90,0%** (54/60) | 79,9%–95,3% | 80,0% |

- **Com texto, o Sonnet reconhece a empresa em 9 de 10 casos.** O piso do
  intervalo (79,9%) fica quase quatro vezes acima do limite de 20%. Pela
  regra registrada antes de medir, **a etapa 2 com texto cai também com o
  Sonnet.** Uma nota retroativa que lê release e fato relevante seria, na
  maior parte, memória do que a empresa fez depois.
- **Revisão de 23/11/2026.** Na primeira rodada (88,3%, 53/60), o ticker
  B3SA3 escapou no dossiê d027: a máscara não pegava ticker com dígito na
  raiz. A máscara foi corrigida, os dossiês foram refeitos com a mesma
  amostra e o lote 2 (o do d027) foi respondido de novo por um Sonnet limpo.
  Além do d027, só o d021 perdeu uma palavra junto do nome escondido; os
  outros cinco dossiês que mudaram mudaram só no espaço depois de "B3:".
  Sem o ticker, o Sonnet ainda acertou B3SA3. No lote refeito, TUPY3 passou
  de erro a acerto, e daí o 54/60.
- A contagem é conservadora. O caso MBRF3 de 2024 foi respondido como MRFG3,
  que é a mesma empresa antes da troca de ticker. Contado como acerto, o
  número sobe para 91,7% (55/60). Dos outros cinco erros, três são
  "desconhecida" (VSPT4 duas vezes e MYPK3) e dois trocam por um par do mesmo
  setor (AZUL4 por GOLL4, TRIS3 por EVEN3).
- O que entrega a empresa não é o nome, que foi escondido. Depois da revisão,
  a busca nos lotes não acha ticker no padrão da B3. É o conjunto que
  entrega: negócios adquiridos, marcas, contrapartes, a ordem de grandeza dos
  números e o setor. Esconder isso tira do texto o que a etapa 2 queria ler.
- **O que isto decide:** a hipótese "a IA lê o que os números não mostram"
  não tem teste limpo com o Sonnet 5.5 nem com o Opus 5.5 nas safras com
  texto (2024–2025). Os dois modelos viram o desfecho dessas safras no
  treino. Um teste limpo pede um modelo com corte de treino anterior ao
  período medido, ou safras posteriores ao corte do modelo (o que só existe
  daqui para frente, em tempo real).

Números em `data/oos_vazamento_ia_b3_sonnet_texto.json`.

## 22. Carteiras sorteadas: 15 ou 30 ações? (23/11/2026, medido)

### Que pergunta responde

Duas perguntas, separadas:

1. **Mais ações dão mais chance de vencer, ou só menos susto?** Sem nenhuma
   análise, uma carteira de 30 ações sorteadas bate pesos iguais, Ibovespa e
   Selic mais vezes que uma de 15?
2. **As carteiras de regra do app mostram habilidade ou sorte?** Cada uma é
   comparada com milhares de carteiras sorteadas do **mesmo tamanho, safra a
   safra**. Se ela fica no meio dos sorteios, o resultado não se separa da
   sorte.

### O desenho (`scripts/medir_carteiras_aleatorias_b3.py`)

Fixado antes de ver qualquer número:

- **Universo:** o da medição oficial de cada safra (quem tinha score mais quem
  saiu da bolsa nela). Sorteio sem reposição, pesos iguais, formação em abril.
- **Régua:** a mesma das §18–§20 (`medir_escolha`): 9 safras, perfil
  Equilibrado, custos de pessoa física, cadeia de custos contínua desde a
  primeira safra com carteira. Ibovespa pelo BOVA11 nas mesmas pontas
  (`oos.retorno_benchmark`); Selic a da vigência.
- **Pergunta 1:** N ∈ {10, 15, 20, 30, 40}. **1.000 sorteios** por N, semente
  20261123. Cada sorteio troca todos os nomes a cada safra. Se a safra tem
  menos de N nomes, entra o universo inteiro.
- **Pergunta 2:** para cada carteira de regra (carteira padrão com os mesmos
  nomes em pesos iguais, rota de valor completa, top 10/15/20, fórmula mágica,
  qualidade constante, qualidade 3 anos e líder segurado), 1.000 sorteios com
  **o mesmo número de nomes que ela teve em cada safra**. Safra em que ela
  ficou em caixa, o sorteio também fica.
- **O que sai, por N:** excesso médio anual (líquido) sobre pesos iguais,
  Ibovespa e Selic; a fração de sorteios com excesso médio > 0 contra cada um;
  os percentis 5, 50 e 95 do excesso médio sobre pesos iguais.
- **O que sai, por carteira de regra:** o percentil do excesso médio dela
  entre os sorteios do mesmo tamanho, líquido e bruto.

### Regra de decisão, registrada antes de medir

- **"30 protege mais que 15"** se a faixa p5–p95 do excesso médio sobre pesos
  iguais com N = 30 for mais estreita que com N = 15.
- **"30 dá mais chance de vencer que 15"** só se a fração de sorteios que batem
  pesos iguais (líquido) com N = 30 ficar **5 pontos ou mais** acima da de
  N = 15. O mesmo vale contra o Ibovespa e a Selic, cada um lido à parte.
- **Uma carteira de regra mostra habilidade** se ficar **acima do percentil
  95** dos sorteios do mesmo tamanho, no líquido e no bruto. Entre 50 e 95:
  não se separa da sorte. Abaixo de 50: pior que a sorte mediana.
- O sorteio troca 100% dos nomes por ano; as carteiras de regra trocam menos
  (§19, §20). Por isso o percentil sai também no bruto: um percentil alto só
  no líquido pode vir do giro menor, não da escolha.
- **Só informativo.** Nada muda na tela nem na carteira padrão. Nove safras
  dão leitura fraca, e a correlação entre as carteiras de regra não é
  corrigida.

### O que se espera, e por quê

Sem custo, a média dos sorteios de qualquer N fica perto de pesos iguais: cada
nome tem a mesma chance de entrar. O N mexe na **dispersão**, não na média.
Com custo, todo sorteio perde um pouco para pesos iguais. Se isso se
confirmar, 30 ações reduzem o tamanho do erro, mas não aumentam a chance de
vencer sem uma escolha que preste.

### Resultado (medido em 23/11/2026, armazém local, perfil Equilibrado, 9 safras)

Antes de sortear, o script conferiu que a régua com memória dá o mesmo que
sem ela em três sorteios. Números em `data/oos_carteiras_aleatorias_b3.json`.

**Pergunta 1 — por tamanho** (1.000 sorteios por N; excesso médio anual,
líquido):

| N | Média vs pesos iguais | Batem pesos iguais | p5 / p95 | Faixa p5–p95 | Batem Ibovespa | Batem Selic | Giro anual |
|---|---|---|---|---|---|---|---|
| 10 | −0,3 pp | 46% | −7,7 / +7,0 | 14,7 pp | 34% | 86% | 95% |
| 15 | −0,2 pp | 45% | −5,4 / +5,5 | 10,9 pp | 30% | 94% | 93% |
| 20 | −0,4 pp | 45% | −5,0 / +4,6 | 9,6 pp | 27% | 95% | 90% |
| 30 | −0,3 pp | 45% | −4,1 / +3,6 | 7,7 pp | 24% | 98% | 85% |
| 40 | −0,2 pp | 47% | −3,3 / +2,8 | 6,2 pp | 19% | 100% | 80% |

- A média contra o Ibovespa fica em torno de −1,7 pp em todo N, e contra a
  Selic em torno de +4,9 pp. No bruto, 48–51% dos sorteios batem pesos
  iguais em todo N: o que separa do bruto é só o custo do giro.
- **Decisão:** "30 protege mais que 15" — **sim** (faixa de 10,9 para 7,7
  pp). "30 dá mais chance que 15" — **não**, nos três: pesos iguais (45% e
  45%), Selic (+4 pontos, abaixo do limiar de 5) e Ibovespa, onde a chance
  **cai** (30% para 24%). Na janela, o Ibovespa bateu o universo em pesos
  iguais; quanto mais a carteira sorteada se parece com o universo, mais ela
  herda essa perda.

**Pergunta 2 — cada carteira de regra entre sorteios do mesmo tamanho:**

| Carteira | Excesso vs pesos iguais | Percentil líquido / bruto | p95 dos sorteios | Giro (regra / sorteio) | Leitura |
|---|---|---|---|---|---|
| Rota de valor | +9,9 pp | 100% / 100% | +3,3 pp | 48% / 82% | acima do p95 |
| Rota de valor, top 20 | +9,5 pp | 100% / 100% | +4,5 pp | 55% / 90% | acima do p95 |
| Fórmula mágica | +5,6 pp | 99,7% / 99,7% | +3,3 pp | 43% / 83% | acima do p95 |
| Rota de valor, top 10 | +11,1 pp | 98,9% / 98,8% | +7,6 pp | 62% / 95% | acima do p95 |
| Rota de valor, top 15 | +7,9 pp | 98,4% / 98,3% | +5,8 pp | 63% / 92% | acima do p95 |
| Qualidade constante | +2,7 pp | 84% / 83% | +4,5 pp | 54% / 87% | não se separa da sorte |
| Carteira padrão, pesos iguais | +2,9 pp | 76% / 76% | +7,5 pp | 81% / 94% | não se separa da sorte |
| Qualidade 3 anos | +1,0 pp | 66% / 65% | +4,9 pp | 31% / 90% | não se separa da sorte |
| Líder segurado | −0,6 pp | 49% / 48% | +5,9 pp | 41% / 87% | pior que a sorte mediana |

O percentil quase não muda do líquido para o bruto: o giro menor das
carteiras de regra não explica a posição delas.

### Leitura

- **Mais ações reduzem o tamanho do erro, não aumentam a chance de vencer.**
  Sem uma escolha que preste, 30 ações sorteadas batem pesos iguais tanto
  quanto 15 (45%), e batem o Ibovespa menos.
- **Concentração não é o problema; a regra de escolha é.** A rota de valor
  com 10 ou 15 nomes fica acima de 98% das carteiras sorteadas do mesmo
  tamanho, mesmo com a faixa de sorte mais larga que vem de poucos nomes.
- **Isto não contradiz a §20.** Lá, o piso do IC 95% dos cortes fica abaixo
  de zero: o intervalo mede a incerteza de **quais anos** vieram (sorteio das
  safras). Aqui as nove safras ficam fixas e o sorteio é dos **nomes**. Nessas
  safras a escolha bateu a sorte; se outras safras repetiriam isso, a §20
  continua dizendo que não há certeza. A régua de decisão da §20 não muda.
- **A carteira padrão do app não se separa da sorte** (percentil 76). O
  excesso de +2,9 pp está dentro do que carteiras sorteadas com o mesmo
  número de nomes alcançam com frequência.
- Os limites da §20 continuam: nove safras, braços correlacionados entre si
  e sem correção para isso, e pesos iguais e Ibovespa sem custo (viés contra
  as carteiras). Um percentil de 98–100% em nove safras não prova que a
  vantagem continua.

## 23. Pequenas baratas e dividendo de 6% (24/11/2026, medido e refeito com o leitor corrigido: nenhuma das duas passa)

### Que pergunta responde

A literatura que o usuário estuda aponta duas rotas que o app não testava:

1. **Pequenas e baratas.** O prêmio de tamanho e valor costuma morar nas
   empresas menores, e o perfil Equilibrado corta tudo abaixo de R$ 1 bi de
   valor de mercado. A pergunta: **as baratas que o piso corta batem pesos
   iguais?**
2. **Dividendo de 6% (Bazin).** Comprar quem paga pelo menos 6% ao ano de
   forma constante. A pergunta: **essa regra, sozinha e com trava de
   solvência, bate pesos iguais?**

### As regras (`core.b3_estrategias_fundamentalistas`, versão 1.3.0)

Fixadas antes de ver qualquer número, sem parâmetro para ajustar:

- **`pequenas_baratas`:** roda a aba com o perfil Equilibrado trocando só o
  piso de valor de mercado para "Sem filtro" (a liquidez mínima de R$ 500 mil
  fica). "Pequena" é quem está no universo sem piso e fora do universo com
  piso, na mesma safra. Entre elas, ficam as que a rota de valor classifica
  como `oportunidade`. Pesos iguais.
- **`dividendo_6`:** universo da carteira padrão. Fica quem tem DY médio de
  **pelo menos 6%** nos três últimos exercícios conhecidos em abril (N-3 a
  N-1), **os três presentes**, e nenhuma falha da trava de solvência da §19.
  DY fora de 0% a 100% conta como ausente (não é crível). Ano com duas
  linhas vale a mais nova. Pesos iguais.
- Mesma régua da §19: 9 safras, formação em abril, custos de pessoa física,
  safra vazia vira caixa a Selic, só o que se sabia em abril (vintage).

Medição: `python scripts/medir_estrategias_fundamentalistas_b3.py
--pequenas-dividendo --cache-dir <pasta>` (grava
`data/oos_pequenas_dividendo_b3.json`). A rodada sem piso tem cache próprio
(`Equilibrado|pb3_min_mcap=Sem filtro`), para não se misturar com a rodada
padrão nem com outro perfil personalizado.

### Regra de decisão, registrada antes de medir

Texto em `REGRA_DE_DECISAO_PEQUENAS_DIVIDENDO`. Cada carteira só é candidata
se, líquida do giro, passar nos três testes da §19:

- (a) excesso médio sobre pesos iguais **no próprio universo** > 0, com piso
  do IC 95% > 0;
- (b) média sem a melhor safra > 0;
- (c) diferença pareada contra a carteira padrão atual (Equilibrado, sem
  portão) com média > 0.

Regras adicionais:

- **Dois braços** elevam a chance de falso positivo para perto de 1 em 10.
  Isso fica dito no resultado.
- **Só informativos, não aprovam nem reprovam:** a rota de valor sem piso
  (grandes e pequenas juntas) e o excesso das pequenas sobre os pesos iguais
  do universo **com** piso.
- Passar não muda a tela nem a carteira padrão. Só abre a próxima etapa.

### Limites já conhecidos

- Nove safras não separam sorte de regra com folga; (b) existe por isso.
- "Pequena" inclui quem não tem valor de mercado no retrato (o piso corta
  quem não tem o número). E uma empresa grande pode cair nas "pequenas" se o
  segmento dela só existe na rodada sem piso.
- Pequenas têm spread e impacto de preço maiores que o custo de pessoa física
  da régua: o resultado líquido tende a ser otimista para elas.
- O DY vem do histórico anual (preço de fim do exercício). Dividendo
  extraordinário de um ano entra na média; a exigência de três anos reduz,
  mas não remove, esse efeito.
- O armazém tem viés de sobrevivência parcial (§19): quem saiu da bolsa entra
  pela lista de saídas, mas nem toda saída tem preço até o fim.

### Correção de 24/11/2026: a primeira rodada leu múltiplos velhos

A primeira versão deste resultado dizia "o dividendo passa" (+5,0 pp, IC
[+1,4; +8,4]). Ela estava errada, e o erro não era da regra:

- O histórico anual B3 (`core.market_read._annual_long`) lia, de
  `market.calculated_metric_vintages`, o cálculo gravado **primeiro**
  (`recorded_at ASC`). O reprocess de 12/11 regravou os múltiplos corrigidos
  com a mesma data de publicação, e a leitura seguiu com os velhos. Exemplo:
  WEGE3, DY de 2014 lido como 15,7%; o corrigido é 2,4%.
- Com a regra antiga, 16.924 múltiplos anuais divergiam mais de 10% do valor
  corrigido. Com a regra nova (primeira publicação, cálculo mais novo dela:
  `ORDEM_DA_SAFRA_VIGENTE`), são 2.
- A conferência dos preços pedida na revisão do #804 também falhou: a rodada
  da aba guardada em 14/11 tinha 2.418 níveis de preço que já não batiam com
  `market.historical_prices` (grupamentos retroajustados depois).

A rodada foi refeita do zero, com o leitor corrigido e os preços de hoje. A
conferência ponto a ponto agora dá diferença máxima 0,0 em 47.356 pontos. As
§23, §24 e §25 abaixo são da rodada nova. **As §1 a §22 ainda usam a leitura
antiga** e não foram refeitas.

### Resultado (refeito em 24/11/2026, armazém local, perfil Equilibrado, 9 safras)

A regra acima foi aplicada sem mudança. A carteira padrão rende +1,2 pp sobre
pesos iguais nesta rodada (IC [−6,6; +9,4]); a §22, com a leitura antiga,
dava +2,9 pp. A rota de valor completa da §19 entra na tabela só como
referência.

| Carteira | Ações | (a) excesso | IC 95% | (b) sem a melhor safra | (c) contra a padrão | Giro/ano | Veredito |
|---|---|---|---|---|---|---|---|
| Dividendo de 6% | 15–39 | +2,1 pp | [−3,6; +7,3] | +0,5 pp | +0,9 pp | 50% | fora (a) |
| Pequenas baratas | 0–9 | +6,9 pp | [−17,3; +29,5] | +1,2 pp | +6,5 pp | 53% | fora (a) |
| Rota de valor sem piso (informativa) | 9–49 | +16,2 pp | [+3,1; +36,1] | +7,3 pp | +15,8 pp | 62% | — |
| Rota de valor completa (§19, referência) | 9–44 | +16,3 pp | [+2,8; +38,5] | +5,8 pp | — | 63% | — |

Leitura com cautela:

- **O dividendo de 6% não passa em (a).** O IC cruza o zero. Perdeu para
  pesos iguais em 2018, 2020 e 2023. Sem a melhor safra (2022, quando
  Petrobras pagou muito) a média cai para +0,5 pp. Com IR, +0,6 pp.
- **O que mudou foi a carteira, não a regra.** Com os DY velhos e inflados
  a carteira tinha 34 a 55 ações; com os corrigidos, 15 a 39. Saíram nomes
  como WEGE3 e TOTS3, que não pagavam perto de 6%.
- **As pequenas baratas também não passam.** Duas safras (2019 e 2020) não
  tiveram nenhum nome e ficaram em caixa. As outras têm de 1 a 9 ações. A
  incerteza continua grande demais para separar regra de sorte.
- **A rota de valor ficou mais forte, mas mais concentrada.** +16,3 pp por
  ano, IC acima de zero. Só que 2020 (+99,5 pp com 9 ações, a volta da
  COVID com PRIO3 e ROMI3 subindo mais de 400%) pesa muito: sem 2020,
  +5,8 pp. De 2017 a 2021 a rota teve só 9 a 17 ações. Ela não está sob regra nesta seção; o
  número fica como referência para a §19 ser refeita.
- **Tirar o piso não muda a rota** (+16,2 contra +16,3 pp).
- Contra os pesos iguais **do universo com piso** (informativo), as pequenas
  ficam em +7,7 pp com IC de −13,8 a +29,1 pp: a mesma conclusão.
- **Nada muda na tela nem na carteira padrão.**

## 24. O dividendo de 6% em outra janela: safras 2013 a 2016 (24/11/2026, medido: confirma, mas a janela principal não passa)

### Que pergunta responde

A §23 mediu o dividendo de 6% (Bazin) em nove safras, de 2017 a 2025. Na
primeira rodada ele passou nos três testes com folga pequena; refeita com o
leitor corrigido, não passa (ver a correção na §23). Nove safras não separam
regra de sorte com segurança. A pergunta, fixada quando ele ainda passava:
**a mesma regra, sem mudar nada, também bate pesos iguais em safras que
ninguém olhou?**

### Por que 2013 a 2016

A §23 começa em 2017 porque a aba carrega só 10 anos de preço
(`period="10y"` na leitura dos preços mensais de `views/portfolio_b3.py`). O
score da rodada cobre 2013 a 2025, e o armazém local tem preço desde 2000.
Então as safras de abril/2013 a março/2017 têm universo, retrato da época e
preço, mas nunca foram medidas. São as únicas safras "novas" que dá para
medir sem inventar dado.

### O que muda e o que não muda

- **Não muda:** a regra (`carteira_dividendo_bazin`), o universo de cada
  safra, o retrato PIT, o histórico cortado pela vintage, o custo do giro e a
  régua (`medir_escolha`). A rodada da aba é a mesma do pickle da §23.
- **Muda:** a primeira safra (`_segmentos_por_safra(..., primeira=2013)`) e
  os preços, que perdem o corte de 10 anos (`precos_sem_corte`: a mesma
  leitura da aba, mesmas trocas de código e mesmas empresas que saíram da
  bolsa). O script confere que, no trecho comum, os preços são iguais aos da
  aba. A cadeia de custos começa em 2013.

### Regra de decisão (registrada antes de medir)

`REGRA_DE_DECISAO_DIVIDENDO_OUTRA_JANELA`, versão `fundamentalistas-1.4.0`.
Métrica: excesso por safra, **líquido do giro**, sobre pesos iguais no
universo da carteira padrão. Leituras, aplicadas por `leitura_outra_janela`:

| Leitura | Quando |
|---|---|
| **confirma** | (a) média > 0 com IC 95% acima de zero **e** (b) média sem a melhor safra > 0 |
| **não contradiz** | média > 0 e (b) > 0, mas o IC cruza o zero |
| **contradiz** | média ≤ 0 **ou** (b) ≤ 0 |
| **inconclusiva** | menos de três safras medidas |

Com quatro safras o IC quase nunca fica acima de zero; por isso "não
contradiz" foi separado de "contradiz" antes de medir. O teste (c) da §23,
contra a carteira padrão, não se aplica: a padrão não tem carteira antes de
2017.

Só informativos: a janela inteira 2013–2025 (13 safras), o excesso com IR e
a conferência das safras 2017–2025 contra a §23 (devem bater, salvo o giro de
2017, que agora vem da carteira de 2016).

Passar não muda a tela.

### Limites declarados antes

- Só quatro safras.
- O universo antigo usa o piso nominal de R$ 1 bi da aba, sem correção pela
  inflação: em 2013 ele corta proporcionalmente mais empresas.
- O DY da safra 2013 precisa dos exercícios 2010 a 2012; quem não tem os três
  fica fora.
- Safra sem nenhum nome vira caixa à Selic.

### Como rodar

```bash
python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> --dividendo-outra-janela
```

Grava `data/oos_dividendo_outra_janela_b3.json`.

### Resultado (refeito em 24/11/2026 com o leitor corrigido): confirma

`data/oos_dividendo_outra_janela_b3.json`. Excesso por safra sobre pesos
iguais no universo da padrão, líquido do giro:

| Safra | Universo | Na carteira | Carteira | Pesos iguais | Excesso |
|---|---|---|---|---|---|
| 2013 | 165 | 13 | −1,0% | −7,4% | +6,4 pp |
| 2014 | 161 | 11 | +5,5% | −4,7% | +10,2 pp |
| 2015 | 159 | 12 | −1,9% | −4,6% | +2,7 pp |
| 2016 | 142 | 15 | +30,0% | +29,6% | +0,4 pp |

- **(a)** média **+4,9 pp** ao ano, IC 95% **[+1,6; +8,3]**: acima de zero.
- **(b)** sem a melhor safra (2014): **+3,2 pp**.
- Nenhuma safra negativa. **Leitura: confirma.**

**Mas a regra não fecha a história.** A §24 foi desenhada para conferir uma
regra que passava na §23. Com o leitor corrigido ela não passa lá (+2,1 pp,
IC cruzando o zero). E a janela inteira, 2013 a 2025, também não passa:

- **Janela inteira 2013–2025 (13 safras):** +3,0 pp, IC [−1,2; +6,8], sem
  a melhor safra +2,0; com IR +1,6 pp, IC [−2,5; +5,5]. Negativas: 2018,
  2020 e 2023.

Leitura honesta: o dividendo de 6% foi bem na recessão de 2013 a 2016 (perdeu
menos que pesos iguais nas quedas de 2013 a 2015) e não se distinguiu de
pesos iguais de 2017 em diante. Somando tudo, o resultado não separa regra de
sorte.

Informativos:

- **Com IR:** +4,8 pp, IC [+1,2; +8,3]. As perdas de 2013 e 2015 ficam
  como prejuízo a compensar.
- **Bruto (sem custo de giro):** +5,1 pp. O giro custa ~0,1 pp ao ano.
- **Conferência com a §23:** 2018 a 2025 batem até a 15ª casa decimal. 2017
  difere 0,009 pp, como previsto: o giro de 2017 agora parte da carteira de
  2016, não do caixa.
- **Conferência dos preços:** 470 colunas, 47.356 pontos em comum com a aba,
  diferença máxima 0,0. Os preços sem corte começam em 2000-01-31 e vêm de
  `market.historical_prices` (armazém local), lido do rótulo que o próprio
  carregador grava (`procedencia_dos_precos`). Se o carregador cair no
  yfinance, a medição para em vez de publicar com o rótulo errado.

O que isto não diz:

- Quatro safras ainda são poucas; o IC estreito vem de quatro números
  parecidos, não de muitos.
- Três das quatro safras foram de queda (recessão de 2014 a 2016). A
  janela nova é um tipo de mercado só; não prova que a regra vai bem em
  qualquer mercado.
- Não compara com a rota de valor (+16,3 pp na §23 refeita), que não tem
  carteira antes de 2017.

Passar não muda a tela.

## 25. O dividendo de 6% sobre a rota de valor (24/11/2026, medido: não se distingue)

### Que pergunta responde

Nas safras 2017 a 2025, com a leitura antiga, a rota de valor (§19) rendia
+9,9 pp por ano sobre pesos iguais e o dividendo de 6% (§23) +5,0 pp. Com o
leitor corrigido: +16,3 e +2,1 pp (ver a correção na §23). As duas regras olham
preço baixo e trava de solvência, então podem comprar as mesmas ações. Duas
perguntas:

1. **Quanto as duas carteiras se repetem?** Por safra: quantos nomes do
   dividendo a rota já compra.
2. **Somar o dividendo à rota rende mais que a rota sozinha?**

### O que se mede

Nenhuma regra muda. Os braços saem das duas carteiras já medidas, na mesma
rodada, mesmo universo (o da carteira padrão), mesma régua (`medir_escolha`),
mesmos custos e mesmos preços da §23:

| Braço | Nomes | Papel |
|---|---|---|
| `rota_mais_dividendo` | a união dos nomes das duas carteiras, pesos iguais | **sob a regra** |
| `dividendo_fora_da_rota` | os nomes do dividendo que a rota não tem | informativo |
| meio a meio | média dos retornos líquidos das duas carteiras | informativo |

A sobreposição de cada safra sai de `sobreposicao`: tamanho de cada carteira,
nomes em comum, a fração do dividendo que a rota já tem e o índice de Jaccard
(comuns sobre a união).

### Regra de decisão (registrada antes de medir)

`REGRA_DE_DECISAO_SOBREPOSICAO_ROTA`, versão `fundamentalistas-1.5.0`.
Métrica: diferença pareada, safra a safra, **líquida do giro**, entre
`rota_mais_dividendo` e a rota de valor sozinha. Leituras, aplicadas por
`leitura_sobreposicao`:

| Leitura | Quando |
|---|---|
| **acrescenta** | (a) média > 0 com IC 95% acima de zero **e** (b) média sem a melhor safra > 0 |
| **tira** | média < 0 com IC 95% abaixo de zero |
| **não se distingue** | os outros casos |
| **inconclusiva** | menos de três safras medidas |

Um braço só sob a regra: o "fora da rota" e o meio a meio respondem a
perguntas vizinhas e ficam como informativos. A diferença com IR também é
informativa.

Passar não muda a tela.

### Limites declarados antes

- Nove safras: diferenças pequenas entre duas carteiras parecidas não se
  separam do ruído.
- A união dá peso maior a quem está nas duas regras? Não: pesos iguais entre
  os nomes da união, cada nome uma vez.
- O meio a meio é aproximado: a média dos retornos líquidos supõe que cada
  metade volta a 50% em abril sem custo extra.
- Safra em que o "fora da rota" não tem nenhum nome vira caixa à Selic.

### Como rodar

```bash
python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> --sobreposicao-rota
```

Grava `data/oos_sobreposicao_dividendo_rota_b3.json`.

### Resultado (refeito em 24/11/2026 com o leitor corrigido): não se distingue

Nove safras, 2017 a 2025 (abril/2017 a março/2026). A rota e o dividendo
repetem os números da §23 refeita, então a rodada usa a mesma régua. A
primeira versão desta seção, com a leitura antiga, também deu "não se
distingue" (−2,4 pp, IC [−6,6; +0,5]).

**Quanto se repetem.** Em média, 43% dos nomes do dividendo já estão na rota
(de 13% em 2020 a 67% em 2023). O índice de Jaccard médio é 0,30.

| Safra | Rota | Dividendo | Em comum | Fração do dividendo na rota |
|---|---:|---:|---:|---:|
| 2017 | 10 | 22 | 7 | 32% |
| 2018 | 11 | 16 | 5 | 31% |
| 2019 | 17 | 16 | 8 | 50% |
| 2020 | 9 | 15 | 2 | 13% |
| 2021 | 9 | 15 | 5 | 33% |
| 2022 | 44 | 28 | 17 | 61% |
| 2023 | 40 | 39 | 26 | 67% |
| 2024 | 27 | 37 | 18 | 49% |
| 2025 | 40 | 36 | 20 | 56% |

**Excesso sobre pesos iguais, líquido do giro:**

| Braço | Média | IC 95% | Sem a melhor safra | Giro anual |
|---|---:|---|---:|---:|
| rota de valor | +16,3 pp | [+2,8; +38,5] | +5,8 | 63% |
| dividendo de 6% | +2,1 pp | [−3,6; +7,3] | +0,5 | 50% |
| **rota + dividendo** | +7,7 pp | [+3,8; +13,2] | +5,3 | 52% |
| dividendo fora da rota | +2,3 pp | [−5,3; +8,3] | +0,9 | 58% |
| meio a meio (informativo) | +9,2 pp | [+2,9; +18,6] | +5,0 | — |

**Sob a regra — rota + dividendo menos a rota sozinha:** −8,5 pp por ano,
IC 95% [−25,4; +1,4], sem a melhor safra −10,1. Cinco das nove safras
ficaram negativas. O IC cruza o zero: **não se distingue**.

Informativos, todos contra a rota sozinha:

| Diferença | Média | IC 95% |
|---|---:|---|
| rota + dividendo, com IR | −8,3 pp | [−25,5; +1,8] |
| dividendo fora da rota | −13,9 pp | [−43,2; +4,0] |
| dividendo inteiro | −14,1 pp | [−41,1; +2,9] |
| meio a meio | −7,1 pp | [−20,6; +1,5] |

### Leitura

- **Somar o dividendo à rota não rendeu mais.** A média é −8,5 pp, mas quase
  tudo vem de 2020: a rota tinha 9 ações e subiu +99,5 pp sobre pesos
  iguais; a união diluiu essa alta (−72,7 pp naquela safra). Nas outras oito
  safras a diferença fica entre −9,6 e +3,9 pp. Nove safras não bastam para
  afirmar que tira, e a regra não diz que tira.
- **O que o dividendo traz de novo rende como pesos iguais.** Os nomes do
  dividendo que a rota não tem renderam +2,3 pp sobre pesos iguais, com IC
  cruzando o zero.
- **O que a união tem a favor é depender menos de uma safra.** O IC da
  união é bem mais estreito que o da rota ([+3,8; +13,2] contra
  [+2,8; +38,5]) e sem a melhor safra as duas ficam perto (+5,3 contra
  +5,8). Isso é dispersão entre safras, não risco medido mês a mês, e não
  foi critério da regra.
- Para quem já segue a rota de valor, o dividendo de 6% não é um acréscimo
  de retorno medido. E sozinho ele não passa mais (§23 refeita).

Nada muda na tela.

## 26. O dividendo de 6% segurado, com equilíbrio pelo aporte (24/11/2026, medido: giro cai pela metade, retorno igual; não passa)

### Que pergunta responde

O dividendo de 6% da §23 refaz a carteira todo abril: quem deixa de ter DY
médio de 6% sai, mesmo saudável. Isso gerou giro de 50% ao ano. A premissa
do usuário (24/11/2026) é outra:

- comprar boas empresas que pagam bom dividendo;
- não vender enquanto a empresa seguir saudável, por mais que o dividendo
  caia;
- a cada ano, novas empresas podem entrar pela mesma regra;
- sair só quando a saúde financeira degradar a ponto de tornar a empresa
  perigosa;
- manter o equilíbrio entre as empresas com o aporte: comprar mais das que
  ficaram abaixo, não vender as que subiram.

Pergunta: **com essa regra, a carteira rende mais que pesos iguais, líquida
dos custos, e com menos giro?**

### O que se mede

| Parte | Regra |
|---|---|
| Entrada | a do dividendo de 6% da §23, sem mudança: DY médio de N-3 a N-1 ≥ 6%, os três anos presentes, sem falha na trava de solvência |
| Saída | a do líder segurado da §19, sem mudança: deixar o universo, falhar na trava de solvência da rota de valor ou ter prejuízo no último exercício. Dado ausente não é degradação. DY abaixo de 6% não tira ninguém |
| Peso-alvo | igual (1/N) entre todos os nomes da carteira |
| Equilíbrio | em cada abril, o aporte (10% do patrimônio) mais o que sobrar das saídas compra os nomes mais abaixo do alvo, de baixo para cima, até o dinheiro acabar (`encher_por_baixo`). Quem está acima do alvo fica como está |
| Retorno da safra | valor em março de N+1 sobre (patrimônio de abril + aporte), líquido de custo de compra e de venda. É o retorno ponderado pelo tempo: o aporte não conta como ganho |

A cadeia é `simular_aportes`, em `core/b3_estrategias_fundamentalistas.py`.
Ela entra na régua `medir_escolha` pelo argumento `simular`; o resto da régua
(universo, preços, pesos iguais do universo, Selic) é o mesmo da §19 à §25.

### Regra de decisão (registrada antes de medir)

`REGRA_DE_DECISAO_DIVIDENDO_SEGURADO`, versão `fundamentalistas-1.6.0`,
commit de 24/11/2026 anterior à medição. Um braço sob a regra
(`dividendo_segurado`, aporte de 10%). Candidato só se, **líquido**, passar
nos três testes da §19:

- (a) excesso médio sobre pesos iguais no universo > 0, com IC 95% acima de
  zero;
- (b) média sem a melhor safra > 0;
- (c) diferença pareada contra a carteira padrão atual (Equilibrado, sem
  portão) com média > 0.

Só informativos:

- aporte de 5% e de 20%;
- a mesma carteira com a régua da §19 (volta a 1/N todo abril, vendendo o
  excesso);
- a variante que só vende por solvência ou saída do universo (prejuízo não
  tira);
- a diferença pareada contra o dividendo de 6% sem segurar, na régua da §23
  e na mesma cadeia com aporte;
- o giro de venda, os motivos de saída e o peso maior e menor por safra;
- o excesso com IR.

Passar não muda a tela; só abre a próxima etapa.

### Limites declarados antes

- Nove safras: diferenças pequenas não se separam do ruído.
- O dividendo pago é reinvestido no próprio nome (o preço é ajustado), não
  vai para os nomes abaixo do alvo. Na vida real ele pode ajudar o
  equilíbrio.
- "Saiu do universo" inclui cair abaixo do piso de tamanho ou de liquidez,
  não só deixar de ser negociada.
- A carteira padrão do teste (c) e os pesos iguais do universo usam a régua
  da §19: voltam a 1/N todo abril, sem aporte e, no caso dos pesos iguais,
  sem custo. Isso pesa contra a carteira.
- Muitos braços foram medidos da §19 à §26 no mesmo período. Um falso
  positivo fica mais provável do que o IC sugere.
- As §1 a §22 deste guia ainda usam a leitura antiga dos fundamentos.

### Como rodar

```bash
python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> --dividendo-segurado
```

Grava `data/oos_dividendo_segurado_b3.json`.

### Resultado (24/11/2026): giro cai pela metade, retorno igual; não passa

Nove safras, 2017 a 2025 (abril/2017 a março/2026), com o leitor corrigido
e os preços conferidos ponto a ponto (diferença máxima 0,0 em 47.356
pontos). O dividendo de 6% sem segurar repete os números da §23 refeita.

**Excesso sobre pesos iguais, líquido:**

| Braço | Média | IC 95% | Sem a melhor safra | Giro de compra | Giro de venda |
|---|---:|---|---:|---:|---:|
| **dividendo segurado, aporte 10%** | +1,7 pp | [−4,3; +6,7] | +0,5 | 27% | 19% |
| aporte 5% (informativo) | +1,6 pp | [−4,3; +6,4] | +0,4 | 23% | 19% |
| aporte 20% (informativo) | +1,9 pp | [−4,0; +7,0] | +0,6 | 33% | 19% |
| segurado, régua da §19 (informativo) | +2,1 pp | [−3,7; +7,1] | +0,8 | 32% | — |
| só solvência (informativo) | +1,7 pp | [−4,0; +6,5] | +0,5 | 26% | 19% |
| dividendo de 6% sem segurar (§23) | +2,1 pp | [−3,6; +7,3] | +0,5 | 50% | — |
| dividendo de 6% sem segurar, com aporte | +1,6 pp | [−4,0; +6,7] | +0,1 | 48% | 43% |

O giro de compra inclui o aporte, que é dinheiro novo; o de venda mostra o
que de fato saiu da carteira.

**Sob a regra:**

| Teste | Valor | Passa? |
|---|---|---|
| (a) excesso médio > 0 com IC 95% acima de zero | +1,7 pp, IC [−4,3; +6,7] | **não** |
| (b) média sem a melhor safra > 0 | +0,5 pp | sim |
| (c) contra a carteira padrão, média > 0 | +0,5 pp | sim |

O IC cruza o zero: **não é candidato.**

Informativos, diferença pareada contra o segurado:

| Comparação | Média | IC 95% |
|---|---:|---|
| segurado menos dividendo de 6% sem segurar (§23) | −0,4 pp | [−1,8; +1,3] |
| segurado menos dividendo de 6% sem segurar, mesma cadeia com aporte | +0,1 pp | [−1,4; +1,8] |
| segurado menos a mesma carteira na régua da §19 | −0,5 pp | [−1,3; +0,4] |
| só solvência menos segurado | 0,0 pp | [−0,4; +0,6] |

Com IR, o excesso fica em +1,1 pp, IC [−4,7; +6,0]. O custo médio cai de
0,13% para 0,06% do patrimônio ao ano.

**Composição por safra:**

| Safra | Nomes | Entraram | Saíram | Seguem sem DY de 6% | Peso maior | Peso menor | Excesso |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2017 | 22 | 22 | 0 | 0 | 4,5% | 4,5% | +1,1 pp |
| 2018 | 20 | 5 | 7 | 4 | 5,8% | 4,9% | +0,8 pp |
| 2019 | 26 | 8 | 2 | 10 | 5,7% | 2,3% | +4,5 pp |
| 2020 | 26 | 6 | 6 | 11 | 6,1% | 3,2% | −16,1 pp |
| 2021 | 29 | 6 | 3 | 14 | 6,4% | 2,7% | +8,1 pp |
| 2022 | 38 | 15 | 6 | 10 | 9,2% | 1,7% | +11,5 pp |
| 2023 | 44 | 14 | 8 | 5 | 7,9% | 1,5% | −9,0 pp |
| 2024 | 43 | 9 | 10 | 6 | 5,8% | 2,0% | +6,6 pp |
| 2025 | 43 | 10 | 10 | 7 | 5,2% | 2,1% | +7,7 pp |

Motivos das 52 saídas (um nome pode ter mais de um): falha na trava de
solvência 40, saída do universo 10, prejuízo 3. Treze das 52 saídas voltaram
à carteira em safra posterior.

### Leitura

- **A regra fez o que prometia no giro.** A venda caiu de 43% para 19% do
  patrimônio ao ano, e o custo caiu pela metade. Ninguém foi vendido por ter
  subido. Os pesos ficaram entre 1,5% e 9,2%, perto do alvo, só com o
  aporte.
- **O retorno não mudou.** Segurar rendeu o mesmo que refazer a carteira
  todo abril: −0,4 pp contra a §23, IC de −1,8 a +1,3. Tirar o prejuízo da
  lista de saída também não mudou nada. O tamanho do aporte mexe pouco.
- **Não passa porque o dividendo de 6% não passa.** As duas versões rendem
  cerca de +2 pp sobre pesos iguais, com IC cruzando o zero. Segurar não
  criou nem destruiu retorno; só cortou giro e custo.
- **A trava de solvência é a maior fonte de venda e oscila.** Ela responde
  por 40 das 52 saídas, e 13 saídas voltaram depois. Uma régua de "empresa
  perigosa" mais estável, por exemplo exigir falha em dois anos seguidos,
  cortaria mais giro. Isso seria regra nova, a registrar antes de medir.
- Para quem prefere este estilo de carteira, a medição diz que ele não custa
  retorno em relação ao dividendo de 6% puro. Não diz que ele bate pesos
  iguais.

Nada muda na tela.

## 27. Solvência em dois anos seguidos (24/11/2026, medido: giro cai de novo, retorno cai; não passa)

### Que pergunta responde

Na §26, a trava de solvência respondeu por 40 das 52 saídas, e 13 saídas
voltaram à carteira depois. A hipótese levantada ali: a trava oscila, e
exigir a falha em dois anos seguidos tiraria só quem está de fato perigoso.

Pergunta: **exigir a falha de solvência em dois anos seguidos corta o giro
sem perder retorno?**

### O que se mede

Tudo igual à §26 (entrada do dividendo de 6% da §23, alvo 1/N, equilíbrio só
pelo aporte de 10%, ninguém vendido por ter subido), menos uma coisa:

| Motivo de saída | §26 | §27 |
|---|---|---|
| Saiu do universo | tira na hora | tira na hora |
| Prejuízo no último exercício | tira na hora | tira na hora |
| Falha na trava de solvência | tira na hora | tira só se o nome também falhou na safra anterior (retrato de abril de N-1, universo de N-1) |

Sem retrato na safra anterior, ou fora do universo dela, não há falha
anterior: dado ausente não é degradação. O filtro é
`motivos_persistentes`, em `core/b3_estrategias_fundamentalistas.py`. As
§26 e §27 usam a mesma cadeia (`_cadeia_do_dividendo_segurado`, no script).

### Regra de decisão (registrada antes de medir)

`REGRA_DE_DECISAO_SOLVENCIA_DOIS_ANOS`, versão `fundamentalistas-1.7.0`,
commit b5eefd94, anterior à medição. Um braço sob a regra
(`dividendo_segurado_dois_anos`). Candidato só se, líquido, passar nos três
testes da §19 (a, b e c, como na §26).

A hipótese do giro se confirma se as duas coisas valerem:

- o giro de venda médio anual fica abaixo do da §26;
- a diferença pareada contra a §26 não tem o IC 95% inteiro abaixo de zero.

Só informativos: a variante que também pede prejuízo em dois exercícios
seguidos; a §26 refeita na mesma rodada; a diferença contra o dividendo de 6%
sem segurar; as voltas, os motivos de saída e o excesso com IR.

### Limites declarados antes

- A hipótese nasceu da §26, medida nas mesmas nove safras. O teste do giro
  não é fora da amostra.
- Nove safras: diferenças pequenas não se separam do ruído.
- Muitos braços foram medidos da §19 à §27 no mesmo período. Um falso
  positivo fica mais provável do que o IC sugere.
- Os demais limites da §26 valem aqui.

### Como rodar

```bash
python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> --solvencia-dois-anos
```

Grava `data/oos_solvencia_dois_anos_b3.json`. A §26 refeita na mesma rodada
repete os números da §26 (+1,7 pp, giro de venda 19%).

### Resultado (24/11/2026): giro cai de novo, retorno cai; não passa

**Excesso sobre pesos iguais, líquido:**

| Braço | Média | IC 95% | Sem a melhor safra | Giro de compra | Giro de venda | Custo ao ano |
|---|---:|---|---:|---:|---:|---:|
| **solvência em dois anos** | +0,9 pp | [−6,7; +6,3] | −0,3 | 18% | 10% | 0,04% |
| também prejuízo em dois anos (informativo) | +0,9 pp | [−6,6; +6,1] | −0,1 | 18% | 9% | 0,03% |
| §26 refeita | +1,7 pp | [−4,3; +6,7] | +0,5 | 27% | 19% | 0,06% |
| dividendo de 6% sem segurar (§23) | +2,1 pp | [−3,6; +7,3] | +0,5 | 50% | — | 0,13% |

**Sob a regra:**

| Teste | Valor | Passa? |
|---|---|---|
| (a) excesso médio > 0 com IC 95% acima de zero | +0,9 pp, IC [−6,7; +6,3] | **não** |
| (b) média sem a melhor safra > 0 | −0,3 pp | **não** |
| (c) contra a carteira padrão, média > 0 | −0,3 pp | **não** |

**Não é candidato.**

**Hipótese do giro:**

| Condição | Valor | Vale? |
|---|---|---|
| giro de venda abaixo da §26 | 10% contra 19% | sim |
| diferença contra a §26 sem IC inteiro abaixo de zero | −0,8 pp, IC [−3,6; +1,7] | sim |

A hipótese se confirma pelo critério registrado: o giro caiu, e a perda de
retorno não se separa do ruído. Mas a média contra a §26 é negativa.

Informativos, diferença pareada:

| Comparação | Média | IC 95% |
|---|---:|---|
| dois anos menos §26 | −0,8 pp | [−3,6; +1,7] |
| dois anos menos dividendo de 6% sem segurar | −1,2 pp | [−4,6; +1,7] |
| também prejuízo menos dois anos | 0,0 pp | [−0,4; +0,6] |

Com IR, o excesso fica em +0,6 pp, IC [−7,0; +6,0].

**Saídas:**

| Braço | Saídas | Por solvência | Saiu do universo | Prejuízo | Voltaram depois |
|---|---:|---:|---:|---:|---:|
| solvência em dois anos | 36 | 20 | 12 | 5 | 5 |
| §26 | 52 | 40 | 10 | 3 | 13 |

**Por safra:**

| Safra | Nomes | A mais que a §26 | Seguem sem DY de 6% | Peso maior | Peso menor | Excesso | Excesso §26 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2017 | 22 | 0 | 0 | 4,5% | 4,5% | +1,1 pp | +1,1 pp |
| 2018 | 26 | 6 | 10 | 5,8% | 2,3% | +4,5 pp | +0,8 pp |
| 2019 | 29 | 3 | 13 | 5,4% | 2,8% | +4,1 pp | +4,5 pp |
| 2020 | 32 | 6 | 17 | 9,6% | 2,2% | −25,8 pp | −16,1 pp |
| 2021 | 35 | 6 | 20 | 6,5% | 2,3% | +4,4 pp | +8,1 pp |
| 2022 | 47 | 9 | 19 | 7,0% | 1,0% | +8,8 pp | +11,5 pp |
| 2023 | 50 | 6 | 11 | 6,2% | 1,4% | −5,1 pp | −9,0 pp |
| 2024 | 54 | 11 | 17 | 4,4% | 1,4% | +10,2 pp | +6,6 pp |
| 2025 | 51 | 8 | 15 | 4,3% | 1,6% | +5,8 pp | +7,7 pp |

### Leitura

- **O giro caiu como previsto.** A venda foi de 19% para 10% ao ano, e as
  voltas de 13 para 5. As saídas por solvência caíram pela metade.
- **O retorno caiu, sem se separar do ruído.** Contra a §26, −0,8 pp; contra
  o dividendo de 6% puro, −1,2 pp. Nenhum dos três testes passa.
- **A safra de 2020 explica a perda.** A §27 entrou nela com seis nomes que a
  §26 já tinha vendido por falha de solvência. Ela perdeu 25,8 pp para os
  pesos iguais, contra 16,1 pp da §26. Só essa safra soma −9,7 pp, mais que
  a diferença total das nove (−7,0 pp). Na crise, a primeira falha da trava
  parece ter sido aviso.
- **A trava oscila, mas não é só ruído.** Esperar o segundo ano corta giro,
  e o preço disso apareceu justo na safra de crise. Com nove safras, não dá
  para dizer se isso se repete.
- Pedir também prejuízo em dois exercícios não muda nada (0,0 pp).
- Para quem prioriza giro baixo, a §27 é o extremo: vende 10% ao ano. A
  medição não mostra ganho de retorno em troca. Entre as três versões do
  dividendo de 6%, a de maior média é a que refaz a carteira todo abril, e
  nenhuma passa.

**Trava contra a volta no mesmo abril (revisão do PR #807).** Na §26 e na §27,
quem a regra de saída tira numa safra não volta pela regra de entrada da mesma
safra (`fe.segurar_lideres(..., barrar_reentrada=True)`). Antes da trava, um
nome com prejuízo que ainda pagasse 6% sairia e voltaria no mesmo abril. A
medição refeita com a trava dá os mesmos números: o caso não aconteceu em
nenhuma safra. O líder segurado da §19 fica sem a trava, porque lá a entrada é
a própria carteira padrão: quem a padrão escolhe está nela de qualquer jeito.

Nada muda na tela.

## 28. O dividendo de 6% com poucos nomes (26/11/2026, medido: nenhum tamanho passa)

### Que pergunta responde

O dividendo de 6% da §23 fica com 15 a 39 nomes por safra. Não supera os
pesos iguais no universo, mas supera o Ibovespa, o CDI, o IFIX e a inflação
no mesmo período. Pergunta do usuário: **e se ficarmos só com os 5, 10, 15
ou 20 de maior DY?**

### O que se mede

A entrada é a da §23: universo da carteira padrão, DY médio de N-3 a N-1
igual ou acima de 6% (os três exercícios presentes) e nenhuma falha da trava
de solvência. Dos que passam, ficam os N de maior DY médio, desempate por
ticker. Com menos de N que passam, ficam todos. Pesos iguais, refeita todo
abril, mesma régua de custos da §19.

O filtro mora num ponto só, `dy_de_quem_passa`, em
`core/b3_estrategias_fundamentalistas.py`. A §23 (`carteira_dividendo_bazin`)
e a §28 (`carteira_dividendo_topn`) leem dele, para que o corte nunca use um
filtro diferente do da carteira inteira.

### Regra de decisão (registrada antes de medir)

`REGRA_DE_DECISAO_DIVIDENDO_TOPN`, versão `fundamentalistas-1.8.0`, commit
2a4fc31f, anterior à medição. Quatro braços sob a regra:
`dividendo_6_top5`, `_top10`, `_top15` e `_top20`. Um braço é candidato só
se, líquido, passar nos três testes da §19 (a, b e c). Com mais de um
passando, vale o menor N.

Só informativos: o dividendo de 6% inteiro refeito na mesma rodada, a
diferença pareada contra ele, o número de nomes por safra e a comparação com
Ibovespa, CDI, IFIX, S&P 500 e inflação.

### Limites declarados antes

- O DY usa o preço de fim de cada exercício, não o de abril/N.
- Ordenar pelo maior DY põe na frente quem teve o preço derrubado. É o risco
  que o corte mede, não um defeito a corrigir depois.
- Quatro braços: a chance de um falso positivo sobe para perto de 1 em 5.
- Os braços se sobrepõem (o top 5 está dentro do top 10). Não são quatro
  testes independentes.
- Nove safras não separam sorte de regra com folga. O teste (b) existe por
  isso.
- Safra sem nenhum nome vira caixa à Selic.

### Como rodar

```bash
python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> --dividendo-topn
```

Grava `data/oos_dividendo_topn_b3.json`. A pasta do cache tem de ser nova ou
montada depois da última mudança de preços no armazém: a conferência dos
preços (`exigir_precos_conferidos`) para a medição se o pickle não bate,
ponto a ponto, com `market.historical_prices`.

### Resultado (26/11/2026): cortar piora; nenhum tamanho passa

Rodada com cache montado em 26/11, depois da troca de preços do mesmo dia. A
conferência dos preços bateu nos 47.356 pontos (diferença máxima 0,0). O
dividendo de 6% inteiro reproduz a §23: +2,1 pp, IC [−3,6; +7,3].

**Excesso sobre pesos iguais, líquido:**

| Braço | Média | IC 95% | Sem a melhor safra | Com IR | Giro ao ano | Custo ao ano |
|---|---:|---|---:|---:|---:|---:|
| top 5 | +0,4 pp | [−6,9; +7,2] | −1,8 | −1,4 | 56% | 0,16% |
| top 10 | −1,0 pp | [−8,8; +6,1] | −3,1 | −2,3 | 57% | 0,15% |
| top 15 | −1,7 pp | [−8,3; +4,4] | −3,4 | −2,9 | 50% | 0,13% |
| top 20 | −0,8 pp | [−7,3; +5,4] | −2,9 | −2,2 | 51% | 0,13% |
| dividendo de 6% inteiro (informativo) | +2,1 pp | [−3,6; +7,3] | +0,5 | +0,6 | 50% | 0,13% |

**Sob a regra:**

| Braço | (a) IC acima de zero | (b) sem a melhor > 0 | (c) contra a padrão > 0 | Candidato? |
|---|---|---|---|---|
| top 5 | não | não (−1,8) | não (−0,8 pp) | não |
| top 10 | não | não (−3,1) | não (−2,3 pp) | não |
| top 15 | não | não (−3,4) | não (−2,9 pp) | não |
| top 20 | não | não (−2,9) | não (−2,0 pp) | não |

**Nenhum é candidato.** O inteiro, informativo, passa em (b) e (c), com +0,9
pp contra a padrão, IC [−10,0; +10,8], e falha em (a).

Informativos, diferença pareada contra o dividendo de 6% inteiro:

| Braço | Média | IC 95% |
|---|---:|---|
| top 5 | −1,7 pp | [−8,3; +5,3] |
| top 10 | −3,1 pp | [−5,8; −0,7] |
| top 15 | −3,8 pp | [−6,7; −1,3] |
| top 20 | −2,9 pp | [−5,5; −0,6] |

**Por safra, excesso sobre pesos iguais (pp):**

| Safra | Nomes no inteiro | top 5 | top 10 | top 15 | top 20 | inteiro |
|---|---:|---:|---:|---:|---:|---:|
| 2017 | 22 | −1,6 | −2,8 | −8,4 | −2,5 | +1,1 |
| 2018 | 16 | +2,3 | −5,1 | −2,1 | −0,7 | −0,7 |
| 2019 | 16 | +18,6 | +7,7 | +6,6 | +6,6 | +6,6 |
| 2020 | 15 | +2,3 | −19,9 | −13,9 | −13,9 | −13,9 |
| 2021 | 15 | +5,5 | +3,7 | +2,9 | +2,9 | +2,9 |
| 2022 | 28 | +6,8 | +15,3 | +11,8 | +15,9 | +15,1 |
| 2023 | 39 | −23,3 | −19,2 | −19,8 | −18,5 | −7,9 |
| 2024 | 37 | +1,5 | +2,9 | +2,0 | +3,0 | +8,0 |
| 2025 | 36 | −8,2 | +8,2 | +5,7 | 0,0 | +7,8 |

Com 15 ou 16 nomes no inteiro (2018 a 2021), o top 15 e o top 20 são quase a
carteira inteira.

**Informativo, R$ 100 mil de abril/2017 a abril/2026, líquido:**

| Carteira | Valor final | Ao ano |
|---|---:|---:|
| S&P 500 (em reais) | R$ 529 mil | 20,3% |
| dividendo de 6% inteiro | R$ 362 mil | 15,4% |
| carteira padrão | R$ 313 mil | 13,5% |
| top 5 | R$ 308 mil | 13,3% |
| Ibovespa (BOVA11) | R$ 294 mil | 12,7% |
| top 20 | R$ 290 mil | 12,6% |
| pesos iguais | R$ 284 mil | 12,3% |
| top 10 | R$ 284 mil | 12,3% |
| top 15 | R$ 266 mil | 11,5% |
| CDI | R$ 216 mil | 8,9% |
| IFIX | R$ 190 mil | 7,4% |
| inflação (IPCA) | R$ 156 mil | 5,1% |

### Leitura

- **Cortar piora.** Os quatro tamanhos ficam abaixo do dividendo de 6%
  inteiro. No top 10, no top 15 e no top 20 a perda é de 3 a 4 pp ao ano, com
  o IC inteiro abaixo de zero: é a única diferença desta seção que se separa do
  ruído.
- **O top 5 é o mais instável.** Ganha 18,6 pp em 2019 e perde 23,3 pp em
  2023. Sem 2019, a média fica negativa.
- **A safra de 2023 mostra o risco declarado.** Os maiores DY daquele abril
  tinham o preço derrubado, e os cortes perderam de 18 a 23 pp, contra 7,9 pp
  do inteiro. Ordenar pelo DY concentra justamente nesses nomes.
- **Todos batem o CDI, o IFIX e a inflação, e nenhum bate o S&P 500.** O top 5
  fica perto do Ibovespa e da padrão; os outros, perto dos pesos iguais.
- Entre as versões do dividendo de 6% medidas (§23, §26, §27, §28), a de maior
  média segue a que refaz a carteira inteira todo abril, e nenhuma passa.

Nada muda na tela.
