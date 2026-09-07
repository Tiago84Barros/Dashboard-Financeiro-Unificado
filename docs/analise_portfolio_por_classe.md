# Análise do Portfólio por classe — médias, banco e chat

Cada sub-aba de **Investimentos → Análise do Portfólio** (Ações, FIIs, Tesouro,
Exterior) ganhou três blocos abaixo dos cards que já existiam:

1. médias da classe (DY, P/L, P/VP e o que mais for agregável naquela classe);
2. confronto dos ativos com o universo do banco, no mesmo espírito de Empresas
   B3, Seleção de FIIs e Empresas Americanas;
3. um chat sobre aquela classe.

O que já existia — cards fundamentalistas, KPIs de Tesouro, painel de exterior,
Alertas e Stress — não foi alterado.

## Onde mora cada coisa

| Camada | Arquivo | Responsabilidade |
| --- | --- | --- |
| Regra | `core/portfolio_valuations.py` | validade por indicador, médias por classe, agregação de Tesouro |
| Regra | `core/portfolio_db_analysis.py` | consulta os motores de score e devolve linhas prontas |
| Regra | `core/b3_slopes.py` | inclinação log-linear das trilhas de crescimento |
| Contexto | `core/llm_context_carteira.py` | texto auditável enviado à LLM |
| LLM | `core/llm_carteira.py` | system prompt e transporte |
| Interface | `design/portfolio_valuations.py` | cards das médias |
| Interface | `design/portfolio_db_analysis.py` | cards do confronto + carregamento cacheado |
| Interface | `design/chat_carteira.py` | barra de conversa por classe |
| Interface | `views/investimentos.py` | `_bloco_analise_classe` chama os três em cada sub-aba |

## 1. Médias da classe

`aggregate_valuations(positions, fundamentals, metrics)` calcula, por indicador,
`Σ(valor de mercado × indicador) / Σ(valor de mercado com dado válido)`.

- **É média descritiva, não múltiplo contábil consolidado.** O P/L da classe não
  é `Σpreço / Σlucro`; é a média das empresas ponderada pelo quanto o usuário
  tem de cada uma. A diferença aparece no rodapé de cada painel.
- **Ausência não é imputada.** Ativo sem o indicador fica fora da média e
  encolhe a cobertura; ele nunca entra como zero ou como valor mediano.
- **Cobertura é medida dentro da classe.** Cada sub-aba passa apenas as suas
  posições, então "70% do valor da classe" significa a classe, não a carteira.
- **Lotes do mesmo ticker somam peso e contam como um ativo.**

### Validade por indicador (`SPECS`)

A regra antiga era única para todos: rejeitava qualquer valor negativo. Isso
descartava ROE, margem e crescimento negativos, que são observação legítima, e
puxava a média para cima exatamente onde a empresa vai mal. Agora cada
indicador declara seu piso:

| Grupo | Piso | Motivo |
| --- | --- | --- |
| Múltiplos (P/L, P/VP, PSR, EV/EBITDA, EV/EBIT, P/FCF, P/S) | `> 0` | múltiplo nulo ou negativo é indefinido, não barato; incluí-lo inverte a leitura |
| DY, vacância | `>= 0` | negativo não existe na definição |
| ROE, ROIC, margens, crescimento, yields | sem piso | negativo é observação válida |

### Indicadores por classe (`METRICS_BY_CLASS`)

| Classe | Indicadores |
| --- | --- |
| Ações | DY, P/L, P/VP, PSR, EV/EBITDA, EV/EBIT, ROE, ROIC, margem líquida, cresc. receita 5a, dív. bruta/patrimônio |
| FIIs | DY, P/VP, vacância física, vacância financeira |
| Exterior | P/L, P/S, EV/EBITDA, EV/EBIT, P/FCF, ROE, margem líquida, FCF yield, retorno ao acionista |

Vacância é de FII e ROE é de companhia — a mesma média não comporta os dois, e
não há tradução implícita entre os nomes de campo de cada fonte.

**Exterior não tem DY nem P/VP.** O conjunto de métricas do módulo americano
não entrega dividend yield nem price-to-book. Anunciar os cards e deixá-los
vazios seria pior que não exibi-los; a limitação está no rodapé do painel.

O universo americano entrega ROE, margens e yields como **fração decimal**
(0,12 = 12%). `para_percentual` converte só as chaves dessa lista; múltiplos
passam intactos, porque multiplicar P/L 12 por 100 publicaria 1200.

## 2. Tesouro Direto: outra agregação, não a mesma

Título público não tem lucro nem patrimônio; não existe P/L de Tesouro, e
inventar um seria pior que declarar a ausência. `aggregate_tesouro` consolida o
que de fato é somável:

- títulos distintos e valor de mercado;
- **retorno mercado sobre custo** — acumulado desde o aporte, não taxa ao ano;
- **prazo médio até o vencimento**, ponderado pelo valor de mercado, com a
  cobertura ao lado: título sem ano legível (Educa+, Renda+) fica fora do prazo
  médio em vez de entrar como zero e encurtar a média;
- **composição por indexador** (Selic, IPCA+, Prefixado) em fração do total.

No lugar do confronto com um universo, a sub-aba mostra a **conjuntura** de
`public.macro` (Selic, IPCA, juro real ex-ante, câmbio). Não há nota de Tesouro
porque o emissor é único e não existe corte transversal para ranquear — e o
painel diz isso, em vez de exibir um score vazio.

Limitação preservada: o valor de mercado vem do saldo informado pela corretora,
não de marcação a mercado independente título a título.

## 3. Confronto com o universo do banco

| Classe | Motor | Referência |
| --- | --- | --- |
| Ações | `core.b3_company_score.score_cross_section` sobre `public.multiplos` + `public.setores` | universo B3 inteiro |
| FIIs | `core.fii_methodology.score_fiis_by_type` | pares do **mesmo tipo** |
| Exterior | `core.us_data.scored_universe()` | universo de ações dos EUA |

Decisões que o painel declara na tela:

- **Ações**: é a mesma decomposição em seis trilhas do painel individual de
  Empresas B3, **não** o ranking da Análise Avançada, que pondera por setor. O
  percentil é contra o universo inteiro, não contra o setor. Quando o histórico
  não vem na sessão, a trilha de crescimento fica sem cobertura e a nota encolhe
  para o neutro — isso é perda de convicção, não penalidade, e o rodapé diz.
- **FIIs**: o percentil é medido **dentro do tipo** (tijolo, papel, FoF,
  híbrido), com o número de pares ao lado. Sem validação point-in-time aprovada
  na sessão, o painel avisa que as notas servem para diligência e não como
  recomendação publicada.
- **Exterior**: ETF, BDR e fundo de índice voltam em "sem nota apurada". Não é
  falha de ingestão — o módulo americano analisa **ações**, e fundo de índice
  não tem demonstração de companhia. A tela explica em vez de exibir cobertura
  baixa sem motivo.
- **Ativo sem nota nunca recebe nota mediana.** "Não apurado" e "mediano" são
  coisas diferentes; confundi-las já produziu aprovação confiante sobre dado
  ausente neste projeto.
- **Banco fora do ar não derruba a aba.** As funções de `core` não propagam
  exceção: devolvem um texto de indisponibilidade sem qualquer detalhe de
  conexão, e os cards fundamentalistas acima seguem válidos.

Cada consulta é cacheada por uma hora, chaveada pela tupla **ordenada** de
tickers: o resultado não depende da ordem em que a carteira lista os ativos.

## 4. Chat por classe

`build_carteira_classe_context` monta um texto auditável com a composição em
percentual, as médias com a respectiva cobertura, o confronto com o banco e os
indicadores por ativo. Duas decisões:

- **Peso, não saldo.** Sai daqui a participação percentual de cada ativo dentro
  da classe, nunca o valor em reais nem a quantidade. O percentual responde às
  perguntas de concentração e exposição; o saldo é dado pessoal, e mandá-lo é
  publicá-lo num serviço externo sem melhorar a resposta.
- **Ausência declarada.** Cobertura, ativos sem nota e universo indisponível
  entram no texto. Média sobre metade da classe e média sobre a classe inteira
  não podem chegar à LLM com a mesma cara.

O system prompt (`core/llm_carteira.py`) obriga: usar só fatos do contexto;
nunca estimar o patrimônio do usuário; separar fato, comparação e inferência;
declarar cobertura antes de concluir a partir de uma média; tratar ausência como
ausência, não como zero ou risco baixo; e não emitir recomendação personalizada
de compra, venda ou alocação, nem preço-alvo.

O histórico é preso à classe e à assinatura dos tickers: mudou a carteira, a
conversa reinicia, porque o contexto que a LLM recebeu deixou de valer.

## Duplicação removida no caminho

A inclinação log-linear das trilhas de crescimento vivia só dentro de
`views/empresas_b3.py`. Copiá-la para cá daria duas definições do mesmo
crescimento, livres para divergir. Ela foi extraída para `core/b3_slopes.py`, e
a view de Empresas B3 passou a delegar — o comportamento dela não mudou.

## Verificação — 07/09/2026

- `python -m pytest -q`: **4493 passaram, 4 pulados** (`tests/test_portfolio_classe_analise.py`,
  36 testes novos, entre eles).
- `python -m ruff check .`: aprovado.
- `python scripts/run_quality_checks.py`: aprovado (skills, fórmulas, varredura
  de segredos e os três testes de ambiente).
- Smoke ao vivo contra o Supabase dos quatro carregadores de
  `core/portfolio_db_analysis.py`: notas, coberturas e percentis coerentes.
- Os testes do contexto da LLM são funções puras, não AppTest: neste projeto
  AppTest já vazou atribuição de módulo entre testes — passa isolado e falha só
  no CI, dentro da suíte.
- **Não verificado no navegador**: a aparência dos cards no deploy. O que garante
  a moldura é o padrão de card em bloco único de `st.markdown`, já adotado.
