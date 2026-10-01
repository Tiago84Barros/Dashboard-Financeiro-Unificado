# Inteligência dos Ativos — painel

Investimentos → Inteligência dos Ativos, com a estratégia concluída. Desde
30/09/2026 a aba abre numa **página resumida**, desenhada pelo usuário porque
o painel anterior mostrava tudo de uma vez e confundia. De cima para baixo:

1. **🌎 Cenário econômico atual (lido dos dados)**, num expander no topo: as
   12 variáveis do cenário com valor, tendência, fonte e data (seção
   *Cenário lido dos dados*, abaixo).
2. **Página resumida**, um grupo por classe (seção abaixo).
3. **📊 Visão geral da carteira**, recolhida num expander. Traz o resumo da
   carteira, a premissa, os cálculos objetivos e a tabela de adequação.
4. **🔎 Análise detalhada**, atrás de um toggle (desligado ao abrir). Mostra as
   questões e o fluxo das 13 seções do ativo escolhido, o **Portfolio Fit**
   (regras e LLM sob demanda) e o **Histórico e auditoria**. É um toggle, e
   não um expander, porque o Portfolio Fit tem expander próprio e o Streamlit
   não aninha expanders.
5. **Minha estratégia**, recolhida num expander ao fim da página. Mostra a
   estratégia vigente e permite editá-la. A edição abre uma nova versão, e a
   atual continua valendo até a nova ser concluída.
*Meu cenário* saiu em 30/09/2026: o usuário não informa mais o cenário,
o programa o lê do banco.

## Página resumida

Dados em `core/inteligencia_ativos/resumida.py` (puro, só reagrupa o que
`analisar_carteira` já montou); tela em `views/inteligencia_ativos_resumida.py`.

- **🛟 Reserva de emergência**: o Tesouro Selic (nome com SELIC ou LFT) ou o
  ativo com papel de reserva. Valor e % de cada título. A reserva conta no
  alvo da renda fixa; se a estratégia informa os meses de reserva, eles
  aparecem no cabeçalho.
- **🏦 Renda fixa**: as demais, em lista com valor e %. O cabeçalho mostra o
  alvo da renda fixa, com a reserva incluída.
- **📈 Ações, 🏢 FIIs e 🌎 Internacional**: um expander por ativo, com o rótulo
  `TICKER · peso → % devida · Manter|Comprar|Vender`. Dentro, na ordem do rascunho:
  - **Porcentagem atual** e **porcentagem devida**. Desde 30/09/2026 (pedido
    do usuário) a devida é **sugerida**: o alvo da classe dividido entre os
    ativos da classe pelo inverso da volatilidade dos retornos mensais
    (`resumida.alvos_sugeridos`), respeitando o teto por ativo, com o
    excedente redistribuído (*water-filling*). Ativo sem histórico recebe a
    volatilidade mediana da classe; sem nenhum histórico, pesos iguais. A
    faixa que o usuário definir para o ativo prevalece. Reserva e renda fixa
    não recebem alvo por ativo. O método aparece ao lado do número.
  - **Manter, comprar ou vender**: decidido **ativo a ativo** desde
    01/10/2026 (`resumida.decisao(a, sugerido)`). Antes era só a tradução da
    ação de adequação, que lê a classe. Por isso a tela publicada dava
    "Comprar" a todos os FIIs e "Manter" a todas as ações, e nunca "Vender"
    sem teto estourado; o usuário perguntou se estava certo. A ordem agora:
    1. Vender da análise (*reavaliar tese*, *reduzir concentração*) prevalece.
    2. Com % devida (faixa do usuário ou sugestão), o peso decide. Acima da
       devida mais a folga (`tolerancia_pp`: 20% do alvo, mínimo 0,5 pp) é
       **vender parte**, com o lembrete de que parar de aportar também
       reduz o peso. Abaixo da devida menos a folga é **comprar**, salvo
       quando a classe já passou do alvo (*reavaliar aportes*): aí fica
       manter. Dentro da folga é manter.
    3. Sem % devida, vale a tradução da ação de adequação.
    4. Sobre isso, a **avaliação multicritério** (`avaliacao.avaliar`, desde
       01/10/2026, pedido do usuário: "fundamentos e valuation na decisão
       ... sentimento do mercado"). Um alerta eliminatório leva a **avaliar
       troca**, mesmo com tudo o mais bom e mesmo com o ativo já acima do
       peso. Qualidade frágil com preço caro ou mercado negativo também leva a
       *avaliar troca*. Qualidade frágil sozinha não deixa aportar
       (comprar → manter) nem reforçar. Num comprar, preço caro pede aportar
       aos poucos.

  - **Avaliação do ativo** (card): três dimensões, cada critério com número e
    referência (+ a favor, − contra, · contexto). Não é média solta.
    - **Régua setorial** (`avaliacao.perfil`):
      - banco/seguradora: sem dívida/EBITDA nem caixa livre; o ROE pesa;
      - setor regulado: dívida/EBITDA tolerada até 4x (crítico acima de
        5,5x); payout acima de 100% com caixa livre negativo é alerta, não
        eliminação;
      - cíclica: P/L baixo não conta como barato (pico de ciclo);
      - FII: vacância, inadimplência, alavancagem e WAULT;
      - geral: dívida/EBITDA bom até 1,5x, alto acima de 3x, crítico acima de
        4,5x.
    - **Qualidade e fundamentos**:
      - ROE e margem contra a mediana dos pares; sem pares, régua absoluta;
      - ROIC, crescimento de receita e de lucro;
      - conversão do lucro em caixa (razão acima de 5x não conta: lucro
        perto de zero);
      - fluxo de caixa livre, dívida, cobertura de juros e payout.

      Na B3, a base contábil não tem EBITDA nem despesa financeira. Quando
      falta, dívida líquida/EBITDA e cobertura de juros vêm do
      `financialData` da brapi (foto de até 200 dias, publicada em
      `data/public/valuation_historico`). Bancos não têm EBITDA e ficam sem
      a razão. A cobertura usa EBIT ÷ despesa financeira do exercício; a
      despesa inclui variação cambial, então a razão sai conservadora.

      A leitura é forte, adequada, frágil ou dado insuficiente (menos de 3
      critérios). Alerta eliminatório põe frágil direto:
      - alavancagem acima do crítico;
      - cobertura de juros abaixo de 1x;
      - payout acima de 100% com caixa livre negativo;
      - patrimônio negativo;
      - EBITDA de 12 meses negativo;
      - vacância acima de 30% ou inadimplência acima de 10%;
      - recuperação judicial ou fraude no noticiário do ativo.
    - **Valuation**: cada múltiplo contra a mediana do próprio histórico e
      contra a dos pares. As duas réguas concordando valem 1; uma só vale
      meia. P/L abaixo de 3x não conta como barato (lucro não recorrente).
      Frágil e barato ao mesmo tempo gera o aviso de *armadilha de valor*.
    - **Mercado e notícias**:
      - tom das notícias próprias. Vem do provedor (Alpha Vantage) quando
        ele mede; senão, do léxico de `core.noticias.sentimento` (1.1.0,
        com vocabulário de mercado). O critério diz quantas notícias vieram
        de cada método e quantas tiveram provedor e léxico em sinais
        opostos;
      - tom do segmento, como contexto;
      - volatilidade e queda máxima contra os pares;
      - momento: retorno de 12 meses com proventos reinvestidos contra a
        mediana dos pares. 10 p.p. ou mais de diferença conta a favor ou
        contra. Queda de 30% ou mais vira alerta não eliminatório. Preço
        vem de `market.historical_prices` do Supabase; preço com mais de
        45 dias ou salto de 3x na janela fica de fora.

      Notícia de recuperação judicial que fala da *saída* dela ("página
      virada") vira alerta não eliminatório.
    - **Limites declarados no card**: o sentimento é termômetro, não modelo,
      e momento descreve o passado recente, não o próximo ano. O bloco vai
      inteiro para a LLM em `analise.texto_para_llm`.

    *Comparar alternativas* continua sem gerador em `adequacao.acao`; só tem
    rótulo.
  - **Se vender, qual substituir?**: até dois pares do mesmo grupo fora da
    carteira, os mais próximos em perfil. Não são ordenados como melhores.
  - **Comparação com o mesmo segmento**: uma régua por métrica (P/L, P/VP,
    DY, ROE...) com o ativo e os dois pares mais próximos marcados, e a
    leitura de onde o ativo fica entre eles. Só métricas que têm dado.
  - **Papel na carteira** e **notícias** (3 mais recentes). A caixa nunca
    fica vazia: sem notícia do ativo, mostra as do segmento
    (`informacoes.noticias_com_setor`); sem elas, o noticiário geral do
    mercado (`contexto_mercado.manchetes_gerais`), cada nível rotulado.
  - **Relatórios relevantes**: o que os documentos dizem, não o link. Até 3
    documentos dos últimos 12 meses com até 3 frases literais do emissor
    que têm fato e número (resultado, caixa, dívida, proventos, guidance),
    escolhidas de forma determinística do corpus RAG
    (`destaques_relatorios.py`, sem LLM). Tabela, cabeçalho, inglês e
    documento de política/regimento ficam de fora. Documento sem texto no
    acervo aparece só com data e título.
  - **Como o macro influencia**: as variáveis que mais pesam na classe, o
    que o cenário lido dos dados diz delas, os sinais de
    divergência e, se já gerada nesta sessão, a leitura de impacto do
    Portfolio Fit.
  - O botão **Ver análise completa** liga a análise detalhada já no ativo.

Os cartões antigos da grade "Ativos" (`tela_painel.render_cards`) saíram da
tela; a função continua no módulo.

## Cenário lido dos dados

Desde 30/09/2026 o cenário não é perguntado ao usuário: havia dado
suficiente no banco para o programa dizer em que ambiente estamos.
`core/cenario/automatico.py` monta os 12 itens com `origem=DADOS`: Selic
(`public.macro`), prefixado de ~1 ano contra a Selic (curva do Tesouro),
IPCA, inflação implícita, USDBRL diário, PIB e ICC, dívida/PIB, juro real
IPCA+, Fed Funds e Treasury 10 anos, spread high yield. Commodities e risco
geopolítico não têm série: saem ausentes, nomeados, e a LLM os lê nas
notícias. Fonte que falha vira motivo no item, nunca some. `de_dados` é
puro; `carregar` usa cache de 15 minutos e nunca grava. `revisar` continua
recusando `origem=DADOS`.

## Estratégia na própria aba

Desde 27/09/2026 a Estratégia de Investimentos não fica mais em Configurações
→ Geral. Ela é configurada e alterada nesta aba.

- **Bloqueada.** O cartão de onboarding mostra o que falta. O botão
  (*Configurar*, *Continuar* ou *Revisar minha estratégia*) abre a
  configuração logo abaixo do cartão. Com a estratégia não iniciada, o
  próprio clique já abre o rascunho. Ao concluir, o app reexecuta, o portão
  reavalia e o painel aparece.
- **Liberada.** A seção *Minha estratégia* fica no fim da página.

O bloco (`views/configuracoes_estrategia.py`) é renderizado uma única vez por
execução, porque as chaves dos widgets são fixas. O `next_step` do portão
aponta para `investments / asset_intelligence`.

O Cenário de Investimentos saiu de Configurações → Geral no mesmo dia e,
em 30/09/2026, da aba também: passou a ser lido dos dados (seção acima).
O não-admin perdeu a aba Geral de Configurações, que só tinha o cenário.

## Entrevista com o perfil financeiro

Desde 28/09/2026 a IA da entrevista lê um resumo do Controle Financeiro
(`core/estrategia/perfil_financeiro.py`). O resumo cobre os últimos 12 meses
fechados; o mês corrente fica de fora, porque pela metade derrubaria as médias.
O resumo traz:

- a renda: média, mediana, mínima, máxima e estabilidade (coeficiente de
  variação);
- a despesa média do caixa e a renda comprometida;
- a sobra (receitas − despesas, antes de investir) e os meses em déficit;
- o aporte médio;
- a reserva de referência, de 6 e de 12 meses de despesa;
- as categorias do caixa, com recorrência e essencialidade;
- o cartão: fatura média, categorias, cobranças que se repetem em 3 ou mais
  faturas e parcelamentos ativos.

As regras de leitura são as mesmas do Controle Financeiro:

- aporte não é despesa;
- compras no cartão não entram nas despesas do caixa;
- a categoria de pagamento de fatura aparece à parte, para não ser somada à
  fatura;
- dado de demonstração (MOCK_MODE ou fallback mock) não entra.

A regra 8 do prompt (`core/llm_estrategia._SISTEMA`) manda a IA usar esses
números para avaliar e orientar. Ela deve:

- sugerir um aporte compatível com a sobra real;
- dimensionar a reserva de emergência em meses de despesa;
- pesar a capacidade de risco contra a estabilidade da renda.

A IA cita os números na pergunta, mas continua gravando só o que o usuário
disser ou confirmar. Na tela, o expander *📊 O que a IA vê das suas finanças*
mostra exatamente o mesmo texto. O resumo fica em cache por 10 minutos, por
dono.

## Resumo da carteira

| Campo | Origem |
|---|---|
| Patrimônio total | `ctx.total_mercado` (carteira consolidada) |
| Rentabilidade | `rentabilidade_total_pct`, só quando `rentabilidade_total_disponivel` |
| Renda gerada | proventos `total_12m` / `total_historico`; some se `data_source == "error"` |
| Alocação atual, alvo e desvio | `calculos.alocacao` (por classe da política) |
| Concentrações | maior ativo, setor e emissor (`calculos`) |
| Objetivo e estratégia | política de investimentos (versão exibida) |
| Cenário | Cenário de Investimentos; avisa quando os sinais pedem revisão |
| Alertas relevantes | alertas de severidade ALTA e MÉDIA (até 6) |
| Próximos eventos | eventos futuros das análises (até 6) |
| Ativos que merecem revisão | ação em reavaliar tese, reduzir concentração, comparar alternativas ou reavaliar aportes, por prioridade |

Nada é inventado. Um número sem base aparece como indisponível, nunca como
zero.

## Cartão do ativo

Cada cartão mostra ticker, valor, peso na carteira, target/faixa, papel,
status da tese (válida, com sinal contra ou sem veredito), valuation, principal
risco, próximo evento e ação a considerar.

- **Valuation** mostra uma métrica com comparação ao próprio histórico
  ("acima da média histórica"). Nunca diz "barato" ou "caro".
- **Principal risco** segue esta ordem: primeiro o gatilho da tese disparado,
  depois o alerta do próprio ativo, e por fim o do setor ou do emissor, do
  mais grave para o menos grave.
- **Ordem dos cartões:** os ativos que pedem revisão vêm primeiro; os demais
  seguem do maior peso para o menor.

## Histórico

`core/inteligencia_ativos/historico.py` (puro) e `historico_repo.py` (I/O).

Uma foto guarda peso, valor, status da tese, ação, papel, sinais de risco e até
seis métricas de fundamentos e de valuation. A foto da carteira guarda
patrimônio, rentabilidade, alocação por classe e número de alertas.

**Quando salvar.** O histórico não grava a cada visita. Numa foto automática,
só grava:

- quando é a primeira foto;
- quando mudou a versão da estratégia ou do cenário;
- quando mudou o status da tese, a ação ou a contagem de sinais de risco;
- quando o peso variou 1 pp ou mais;
- quando a alocação por classe variou 1 pp ou mais (na foto da carteira);
- quando a última foto tem mais de 30 dias.

Há duas outras formas de gravar:

- **leitura por LLM gerada:** grava sempre, com o modelo que respondeu;
- **botão *Salvar esta análise no histórico*:** grava sempre, como manual.

A aba tenta a gravação automática uma vez por sessão. A decisão acontece
dentro da transação, com a linha travada (`FOR UPDATE`), então duas abas
abertas não duplicam a foto.

**Comparação.** A comparação é feita com a foto anterior, e não com a gravada
agora. Ela gera frases como:

- "Na análise anterior (dd/mm/aaaa), o peso era 8,0%; agora é 12,0%."
- "Desde a última análise, a tese permaneceu válida."
- "O risco aumentou: os sinais objetivos passaram de 1 para 2."
- "Dívida líquida/EBITDA aumentou de 1,50x para 2,40x (fundamentos)."

Variações de métrica abaixo de 2% são tratadas como ruído e não viram frase.

## Auditoria

Cada foto registra os seguintes campos:

| Campo | Conteúdo |
|---|---|
| `analysis_timestamp` | momento da análise (UTC, ISO) |
| `model_used` | `provedor/modelo` que de fato respondeu (com fallback, é o do fallback). Fica vazio quando não houve LLM |
| `data_timestamp` | a data mais recente dos dados usados, nunca no futuro |
| `scenario_version` | versão do Cenário de Investimentos, ou vazio |
| `investment_policy_version` | versão da estratégia |
| `sources_used` | fontes das seções disponíveis e dos fundamentos |

`model_used` vem de `core.llm_b3.ultimo_modelo()`, que é por thread e é zerado
a cada chamada.

## Armazenamento

O histórico fica em `user_settings.extra_settings`, na chave
`asset_analysis_history` (esquema `historico_analises.v1`). O limite é de 8
fotos por ativo e 8 da carteira.

O motivo de não usar uma tabela é que o Supabase está acima do teto de 500 MB.
Algumas fotos compactas por usuário não justificam uma tabela nova nem uma
migration.

Se o histórico precisar crescer (mais fotos, consulta entre usuários, retenção
longa), a alternativa é uma tabela `asset_analysis_snapshots`, com as colunas
de auditoria promovidas a físicas. Essa mudança depende de uma migration
aprovada. O formato JSON já usa os nomes em inglês dessas colunas.

## Testes

- `tests/test_inteligencia_ativos_painel.py` cobre o resumo, os cartões, a
  ordem dos riscos, o HTML só com tokens e o AppTest completo da aba: abrir a
  análise pela caixa do ativo, a gravação automática, o botão de salvar e a
  falha do banco.
- `tests/test_inteligencia_ativos_resumida.py` cobre a página resumida: os
  grupos, manter/comprar/vender, a porcentagem devida sugerida pelo inverso
  da volatilidade (teto, redistribuição, mediana, pesos iguais), os
  substitutos fora da carteira, a régua de pares, notícias com fallback,
  relatórios sem link, macro e o HTML só com tokens.
- `tests/test_cenario_automatico.py` cobre o cenário lido dos dados (itens,
  ausências nomeadas, fallback do câmbio, cache, texto da LLM, tela).
- `tests/test_destaques_relatorios.py` cobre a escolha das frases dos
  relatórios.
- `tests/test_inteligencia_ativos_historico.py` cobre a regra de quando salvar,
  o limite, a comparação, a auditoria, o modelo que respondeu e o repositório
  com engine falso.
- `tests/conftest.py` (`_historico_em_memoria`) troca o repositório por um
  dicionário em memória, para que nenhum teste chegue ao banco.
