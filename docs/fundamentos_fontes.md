# Fundamentos por classe — fontes de dados

Camada de fundamentos da **Inteligência dos Ativos** (cartão 5 da análise).
O código está em `core/inteligencia_ativos/fundamentos.py` (catálogo, formatação e texto
para a LLM) e em `core/inteligencia_ativos/fontes_fundamentos.py` (leitores por classe).

## Regras

1. **Cada classe tem o seu catálogo.** Uma ação não mostra P/VP de FII, e um FII não
   mostra ROIC. A classe sai do rótulo da carteira (`tipo_do_ativo`): `Ações BR`/`BDR`
   são ações, ativos no exterior (moeda ≠ BRL) são ações, e ETF vem antes de tudo.
   Cripto e Outros não têm catálogo.
2. **Nada é inventado.** Uma métrica sem valor na fonte sai como `Dado não disponível.`.
   Não há média de setor, estimativa nem valor anterior preenchendo a lacuna. O
   `montar` recusa uma métrica fora do catálogo da classe: um leitor que a devolve é
   defeito.
3. **DADO e INTERPRETAÇÃO ficam separados.** O backend entrega os valores com
   procedência (fonte, data de referência e nota de cálculo). No texto para a LLM, o
   bloco `[DADO]` traz só números e o bloco `[INTERPRETAÇÃO]` traz só perguntas próprias
   da classe. A tela mostra a tabela "Dado · fornecido pelo sistema" e deixa a
   interpretação para a análise por LLM.
4. **Unidades.** Percentuais são exibidos em % (os leitores convertem fração → %).
   Valores monetários são absolutos, na moeda do ativo (R$ ou US$). Múltiplos usam `x`.
   Duration é dada em anos.

## Fontes por classe

### Ações B3 (`Ações BR`, `BDR`)

| Indicador | Fonte | Observação |
|---|---|---|
| Receita, lucro líquido, FCO, FCL, dívida bruta, dívida líquida | `market.income_statements`, `balance_sheets`, `cash_flow_statements` via `core.market_read.load_demonstracoes` | último exercício anual |
| Crescimento da receita e do lucro | idem | variação anual; só entre exercícios consecutivos e com base positiva |
| Dívida líquida/EBITDA | idem, calculado | só quando o EBITDA está gravado e é positivo; hoje ele vem vazio para boa parte da base, então o indicador fica indisponível |
| Dívida líquida/EBITDA (base sem EBITDA) | dívida líquida do balanço da base ÷ EBITDA de 12 meses da brapi | a dívida é sempre a do balanço, a mesma da Empresas B3 e do chat; a razão pronta da brapi só entra quando a base não tem dívida líquida |
| Dívida bruta/patrimônio | `Endividamento_Total` de `load_multiplos` (o número da Empresas B3); na falta dele, dívida bruta ÷ PL do exercício | sem valor com PL negativo. A avaliação o mede contra o teto do arquétipo do segmento (`core.segment_calibration`), refinado pelo p90 dos pares como na Empresas B3. Acima do teto pesa contra em qualquer ação B3; na incorporadora é a régua de dívida (acima de 2× o teto elimina) e dívida/EBITDA vira contexto |
| Dívida líquida/EBITDA (na falta da base) | brapi `financialData` via `data/public/valuation_historico` | conferida contra a dívida bruta do balanço: se a líquida implícita (razão × EBITDA de 12 meses) passar de 1,5× a bruta anual, o indicador fica retido com nota "Fontes divergentes" e vira aviso, nunca alerta eliminatório (caso DIRR3, 10/2026) |
| Margem EBIT, margem líquida, ROE, ROIC, payout, DY | `market.calculated_metrics` via `load_multiplos` | 12 meses; ROIC **antes** de impostos; DY "como gravado na base" |
| Margem bruta, cobertura de juros, proventos em 12 meses por ação, guidance | — | **Dado não disponível.** O loader não lê custo nem despesa financeira. A coluna `Dividendos` da demonstração tem unidade ambígua e não é usada. Não existe fonte de guidance. |

### Ações EUA (ativos no exterior)

| Indicador | Fonte | Observação |
|---|---|---|
| Receita, lucro, crescimento de 3 anos (receita e LPA), margens bruta/EBIT/líquida, ROE, ROIC, FCL, dívida líquida, DL/EBITDA, cobertura de juros, payout, DY | `market_us.company_snapshots.metrics` (SEC/EDGAR) via `core.us_data.company_metrics` | último exercício anual; ROIC depois de impostos (NOPAT) |
| FCO, dívida bruta | `company_snapshots.financials` via `company_financials` | último `fiscal_year` |
| Dívida bruta/patrimônio | `metrics.debt_to_equity` | informativo: o teto de Dív/PL é régua da base brasileira e não se aplica |
| Proventos em 12 meses por ação, guidance | — | **Dado não disponível.** |

### FIIs

Fonte única: o snapshot da Seleção de FIIs (`data/public/fii_selection_snapshot_v2`),
lido por `core.market_read.load_fii_methodology_inputs`. A data de referência e a
origem vêm de `metric_metadata`.

| Indicador | Campo | Observação |
|---|---|---|
| P/VP, DY 12m | `pvp`, `dy_12m` | |
| Vacância física / ocupação | `vacancia_fisica` | ocupação = 100% − vacância física (calculada, com nota) |
| Vacância financeira, inadimplência | `vacancia_financeira`, `delinquency` | |
| Concentração de locatários | `tenant_concentration` | só a participação do maior locatário |
| Concentração geográfica | `regions` | |
| Prazo dos contratos | `lease_expiry_concentration_24m`, `contract_profile_text` | vencimentos em até 24 meses e perfil típico/atípico |
| WAULT | `wault_anos` | vazio para a maioria dos fundos |
| Emissões | `shares_outstanding` | só o saldo de cotas, sem histórico de ofertas |
| Alavancagem | `leverage` | passivo total ÷ ativo total |
| Qualidade dos ativos | `property_count`, `property_diversification`, `implied_cap_rate` | são insumos, não uma nota de qualidade |
| Revisional | — | **Dado não disponível.** Nenhuma fonte publica. |

### Renda fixa (`Tesouro Direto`, `Renda Fixa`, `Fundo RF`)

| Indicador | Tesouro Direto | CDB, LCI/LCA, CRI/CRA, debêntures |
|---|---|---|
| Emissor, risco de crédito, liquidez | regra do sistema: Tesouro Nacional, risco soberano (que não é um rating), recompra diária | **Dado não disponível.** O emissor não está na carteira. |
| Cobertura do FGC | não se aplica | regra pela natureza do papel: CDB/LCI/LCA/LC/RDB/LIG cobertos; CRI/CRA/debênture/LF/FIDC não. O limite por instituição não é conferido. |
| Indexador | extrato do Tesouro, ou o nome do papel | nome do papel, quando identifica um único indexador |
| Vencimento, taxa contratada, taxa de mercado, marcação a mercado | Extrato Analítico (`tesouro_lots`) + curva do Tesouro via `core.tesouro_posicao.carregar_titulos` | **Dado não disponível.** |
| Duration | só nos prefixados e IPCA+ sem cupom (Macaulay = prazo em dias úteis ÷ 252); nos títulos com cupom, Selic, Renda+ e Educa+ fica indisponível | **Dado não disponível.** |

A carteira junta o principal e o com-cupom num ticker só. O título é casado primeiro
pelo ticker exato; se não houver, pela família e ano, mas **só quando há um único
candidato**.

### ETFs

Nenhuma fonte do projeto traz índice, composição, concentração, taxa de administração,
tracking error, liquidez ou exposição setorial ou geográfica. O país de listagem não é
usado como exposição: o IVVB11 é listado no Brasil e expõe aos EUA. **Todos os 8
indicadores saem como `Dado não disponível.`** Esta é a classe que mais ganharia com
uma nova fonte.

## Leitura em produção

Todas as leituras passam por funções já cacheadas do projeto (Supabase e
`data/public/`). O extrato do Tesouro tem cache próprio de 900 s. Nenhuma tabela nova é
criada. Se um leitor falhar, a seção vira `SEM_DADOS` e o resto da análise continua. Na
suíte de testes, a fixture `_fundamentos_sem_banco` (`tests/conftest.py`) mantém a
seção offline.

## O mesmo veredito em todo o app (B3, EUA, FII)

`core/inteligencia_ativos/veredito.py` calcula, para cada ação B3, a mesma `Avaliacao`
da Inteligência dos Ativos (mesmos leitores, mesma régua de dívida) e o limite que ela
impõe: alerta eliminatório → "avaliar troca"; qualidade frágil com preço caro ou mercado
negativo → "avaliar troca"; qualidade frágil sozinha → "não aportar". São os passos de
`resumida.com_avaliacao`, sem a decisão por peso, que depende da política do usuário.

- **Chat da análise de portfólio**: o contexto recebe o bloco `AVALIAÇÃO POR REGRAS`
  das ações da carteira e das citadas na pergunta (até 15), e o system prompt carrega
  `REGRA_VEREDITO`. Ticker que falha é nomeado no bloco.
- **Conferência pós-resposta do chat** (`llm_b3.chat_coerente`): a resposta passa por
  `veredito.conferir_resposta`, que procura, por ticker, compra/aumento/aporte com
  limite "avaliar troca" ou "não aportar", e "manter" sem ressalva (alerta, troca,
  venda, redução) com "avaliar troca". Achou → uma chamada a mais pede a resposta
  inteira reescrita. Se a reescrita falhar ou ainda contradisser, a resposta sai com um
  aviso no topo nomeando ticker, trecho e limite. É heurística (verbos, negação na
  mesma oração, janela até o próximo ticker): paráfrase como "vale ter mais" escapa e
  pode haver falso positivo; o que ela garante é que contradição detectada não sai calada.
- **Relatório por empresa**: o veredito entra no contexto e `veredito.coerente` limita
  a `perspectiva` em código ("fraca" com "avaliar troca", "moderada" com "não aportar"),
  dizendo no resumo por quê. A perspectiva limitada também reduz o multiplicador da
  redistribuição de pesos.

### Generalização (02/10/2026)

O veredito deixou de ser só da Empresas B3. `veredito.avaliar_ativo(..., mercado=)`
avalia ação da B3, ação americana e FII com os leitores da Inteligência; o bloco aceita
`mercado` por item. Duas profundidades:

- **Carteiras-modelo** (Empresas B3, Empresas Americanas, Seleção de FIIs, Portfólio
  Global, chat por ativo): o limite do ativo, como acima.
- **Carteira do usuário** (Investimentos, chat por classe, Visão Geral e dossiê): a
  DECISÃO que a Inteligência mostra para a posição — estratégia e peso, via
  `resumida.decisao` — convertida em limite por `veredito_de_decisao` ("avaliar troca",
  "reduzir", "não aportar", "livre"). Sem Estratégia liberada, ou sem decisão para o
  ativo, vale o limite do ativo. ETF, BDR e renda fixa não têm avaliação por regras.

Todo chat dessas telas passa por `veredito.responder_coerente` (a conferência acima). O
dossiê não é reescrito (é longo): a contradição detectada sai avisada no topo.
`REGRA_VEREDITO` está nos prompts de B3, FII, Global, ativo e carteira.

**Portão da seleção (Criação de Portfólio B3, Empresas Americanas, Seleção de FIIs)**:
sempre ligado. Nome com limite "avaliar troca" ou "não aportar" não entra na carteira
criada. Avaliação indisponível não veta, mas é listada (fail-open nomeado).

- **B3**: depois do piso de qualidade e antes do parecer de LLM; o próximo do ranking
  do mesmo segmento herda a vaga e o peso (`veredito.filtrar_selecao`). Vaga sem
  substituto fica vazia e é listada. O resumo salvo (`inteligencia_*`) leva o log.
- **Empresas Americanas**: em `us_portfolio_creation.select_industry_leaders`, depois
  do piso; o substituto é o próximo da mesma indústria que também passa no piso
  (`filtrar_selecao`). A rede "carteira preservada" e a revisão (`us_review`) não
  readmitem vetados.
- **FIIs**: o otimizador não tem vaga por tipo, então `veredito.reotimizar_sem_vetados`
  exclui o vetado e remonta a carteira (`montar_carteira_com_concessao`) sob as mesmas
  restrições, até 6 rodadas; vetado que resta vai como "persistente", com aviso. A
  revisão (`fii_review`) também recebe o universo sem vetados.

As três telas mostram a seção "Inteligência dos Ativos — vetos e substituições"
(`design/portao_inteligencia.py`).

Lacunas conhecidas: nos FIIs, quem entra no lugar do vetado é escolha do otimizador,
não herança de vaga; o log das seleções americana e de FIIs ainda não vai para o resumo
salvo; a conferência é heurística; menção a ticker americano só é reconhecida para
tickers que estão no bloco.
