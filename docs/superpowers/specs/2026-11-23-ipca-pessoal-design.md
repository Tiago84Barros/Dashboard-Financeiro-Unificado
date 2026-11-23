# IPCA Pessoal — design

Data: 2026-11-23 · Branch: `feat/ipca-pessoal-design`

## Objetivo

O IPCA mede a inflação da família média brasileira: uma média ponderada da
variação de preço de nove grupos, com pesos da Pesquisa de Orçamentos
Familiares (POF). Quem gasta numa proporção diferente da família média sente
uma inflação diferente da oficial.

O IPCA Pessoal aplica o mesmo método à cesta do usuário. Os pesos vêm dos gastos
lançados no Controle Financeiro. A variação de preço vem dos próprios
lançamentos, quando eles permitem medir preço. Quando não permitem, vem do IPCA
oficial do grupo equivalente. O resultado aparece numa aba nova, ao lado do
IPCA oficial, com a decomposição por categoria.

## Decisões tomadas

| Pergunta | Decisão |
|---|---|
| Unidade de peso | Categoria de despesa do Controle Financeiro |
| Medida de preço | Itens recorrentes da própria categoria; senão, IPCA oficial do grupo equivalente |
| Pesos no tempo | Fixos na janela de 12 meses (como o IBGE), sem pesos móveis |
| Onde aparece | Nova aba em Controle Financeiro, depois de "Cartão de Crédito" |

Descartadas:

- **Variação do gasto por categoria como se fosse preço.** O gasto mistura
  preço com quantidade consumida. Medir isso e chamar de inflação engana.
- **Só itens recorrentes, sem IPCA por grupo.** Deixaria de fora mercado,
  restaurante e combustível, justamente os gastos que mais pesam.
- **Pesos móveis e chave para alternar os modos.** Ficam para depois (YAGNI).
- **Registro de quantidade/litros nos lançamentos.** Melhoraria a medida de
  preço, mas muda o formulário e o hábito de lançamento. Fica para depois.

## 1. A conta

### Janela

A janela tem os 12 meses que terminam no mês escolhido na barra lateral do
Controle Financeiro. Para 12 variações mensais são precisos 13 meses de
lançamentos: o mês anterior à janela entra só como base de preço.

### Lançamentos que entram

Só despesa real de consumo:

- `type = 'expense'` e `status = 'settled'`;
- fora: receitas, transferências, aporte e resgate de investimento,
  pagamento de fatura (a mesma regra de `core.controle.is_investment_category`
  e da classificação de pagamento de fatura já usadas no módulo);
- fora: a categoria **Impostos e Taxas**, que não é consumo (o IPCA também não
  mede imposto de renda nem taxa).

Compra parcelada entra pelo valor da parcela do mês, como o Controle Financeiro
já mostra. Parcelas ficam fora da medida de **preço** (ver abaixo), mas
contam no **peso**.

### Peso

```
w_c = gasto da categoria c na janela / gasto total da janela
```

O peso fica parado durante a janela inteira. Soma 1.

### Variação de preço da categoria no mês

Para cada categoria `c` e cada mês `t` da janela, `r_{c,t}` vem de uma de duas
origens, nesta ordem.

**a) Origem "seus itens"** — itens recorrentes da categoria.

Item recorrente é a descrição normalizada que:

- aparece em pelo menos 3 meses dos 13;
- não é parcela (`installment_total` nulo ou 1).

A normalização da descrição tira acentos, caixa, dígitos soltos e o sufixo
de categoria entre colchetes que a importação antiga deixou
(ex.: `GIZELE [Despesas Domésticas]` → `gizele`).

A variação do item `i` no mês `t` é `v_{i,t} = valor_{i,t} / valor_{i,t-1} − 1`.
Ela só existe se o item aparece em `t` e em `t-1`. Se `|v_{i,t}| > 50%`, o par
é tratado como troca de produto ou erro de lançamento: sai da conta e vai para
a lista "ignorados", com o motivo.

Se o item aparece mais de uma vez no mesmo mês, usa a soma do mês.

```
r_{c,t} = Σ_i (valor_{i,t-1} · v_{i,t}) / Σ_i valor_{i,t-1}
```

A categoria usa esta origem se os itens recorrentes cobrem **pelo menos 30%**
do gasto dela na janela. Abaixo disso, a amostra não representa a categoria.
Nesse caso a categoria inteira usa a origem (b), sem misturar origens no mês.

**b) Origem "estimativa oficial"** — variação mensal do grupo do IPCA
equivalente, pelo mapeamento abaixo.

Se nenhuma origem tem dado no mês, a categoria fica de fora daquele mês. Os
pesos das outras categorias são renormalizados só naquele mês, e a tela avisa.

### Índice

```
I_t        = Σ_c w_c · r_{c,t}                 (variação mensal pessoal)
Acum_12m   = Π_t (1 + I_t) − 1
Contrib_c  = Acum_12m · (Σ_t w_c·r_{c,t}) / (Σ_t I_t)
```

A contribuição reparte o acumulado na proporção da soma simples das
contribuições mensais. Assim, a soma das contribuições é igual ao acumulado.
Se `Σ_t I_t = 0`, a contribuição de cada categoria é a própria soma
`Σ_t w_c·r_{c,t}`, e a tela avisa que a repartição é aproximada.

O IPCA oficial do comparativo é o **Índice geral** da mesma tabela do IBGE,
encadeado da mesma forma. Assim os dois números usam a mesma fonte e a mesma
unidade (variação mensal em %), sem depender do IPCA "acumulado no ano" que
`core/contexto_mercado.py` documenta.

### Mapeamento categoria → grupo do IPCA

Dicionário no código, com fallback explícito:

| Categoria do app | Grupo do IPCA |
|---|---|
| Moradia | Habitação |
| Alimentação | Alimentação e bebidas |
| Transporte | Transportes |
| Saúde | Saúde e cuidados pessoais |
| Educação | Educação |
| Lazer | Despesas pessoais |
| Vestuário | Vestuário |
| Assinaturas | Comunicação |
| Telefone / Internet | Comunicação |
| Pets | Despesas pessoais |
| Seguros | Despesas pessoais |
| Presentes e Doações | Índice geral |
| Outras Despesas | Índice geral |
| Impostos e Taxas | fora da conta |
| categoria criada pelo usuário ou pela importação de fatura | Índice geral, marcada "sem grupo definido" |

"Artigos de residência" fica sem categoria própria no app. Isso aparece na
comparação de pesos como 0% do usuário.

## 2. Dados

### Gastos

A mesma leitura de lançamentos do Controle Financeiro (`core/controle.py`), sem
nova conexão de banco. A camada de cálculo recebe uma lista de lançamentos já
filtrada e não sabe de onde ela veio. Isso permite testar a conta sem banco.

No modo de teste do app (`core/mock_data.py`, `views/app_test_mode.py`) a aba
usa lançamentos fictícios com 13+ meses, como as outras telas fazem.

### IPCA por grupo e pesos do IBGE

Fonte: IBGE, SIDRA, tabela 7060 (IPCA: variação mensal e peso mensal, por
grupo, de jan/2020 em diante). Variáveis: variação mensal e peso mensal. O
número da tabela e das variáveis será conferido na primeira tarefa do plano.

**Antes de criar qualquer coisa**, a primeira tarefa roda a skill
`inventario-de-dados`: confere se essa série já está no Supabase
(`public.macro`, `market.macro_indicators`) ou no armazém local
(`macro_staging`). Se já estiver, usa a existente e a seção abaixo encolhe.

Se não estiver:

- **Tabela nova no Supabase** (é pequena: 10 séries × ~80 meses), para a
  Streamlit Cloud alcançar:

  ```sql
  CREATE TABLE IF NOT EXISTS ipca_grupos_mensal (
      mes           DATE         NOT NULL,  -- primeiro dia do mês
      grupo         VARCHAR(60)  NOT NULL,  -- 'Índice geral' ou um dos 9 grupos
      variacao_pct  NUMERIC(8,4) NOT NULL,
      peso_pct      NUMERIC(8,4),           -- nulo no Índice geral
      fonte         VARCHAR(40)  NOT NULL DEFAULT 'ibge_sidra_7060',
      atualizado_em TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
      PRIMARY KEY (mes, grupo)
  );
  ```

  Script SQL novo e não destrutivo em `supabase_unificado/schema/`, no padrão
  dos existentes. Leitura pública (dado oficial, sem usuário), escrita só
  pelo serviço.

- **Coleta em ETL separado** (`etl/`), com upsert por `(mes, grupo)`,
  chamado pela rotina de atualização macro que já existe
  (`run_macro_updates.py`). A tela só lê; nunca chama o IBGE.

## 3. Módulos

Cada unidade com um papel, testável sozinha:

| Unidade | Papel | Depende de |
|---|---|---|
| `core/ipca_pessoal.py` | Conta pura: filtro, normalização, recorrentes, pesos, variação por categoria, índice, contribuição. Recebe listas/DataFrames, devolve um resultado estruturado. | nada de banco |
| `core/ipca_grupos.py` | Mapeamento categoria → grupo e leitura de `ipca_grupos_mensal` | `core/database.py` |
| `etl/ipca_grupos_sidra.py` | Busca no SIDRA e grava | `core/database.py` |
| `views/controle_ipca_pessoal.py` | Só desenha a tela | os dois módulos `core` |

`views/controle_financeiro.py` já tem 3.900 linhas. A aba nova entra lá apenas
como uma linha em `_SECOES` e uma chamada `render_ipca_pessoal(...)`, como
`render_previstos` já faz.

O resultado da conta é um objeto com: `acumulado_12m`, `oficial_12m`,
`mensal` (lista de mês, pessoal, oficial), `categorias` (peso, variação,
origem, contribuição, cobertura dos recorrentes), `pesos_por_grupo` (usuário ×
IBGE), `ignorados` (item, mês, motivo) e `avisos`.

## 4. Tela

Aba **"🧮  IPCA Pessoal"**, sexta de Controle Financeiro, depois de
"💳  Cartão de Crédito".

1. **Dois números no topo:** IPCA Pessoal 12 meses e IPCA oficial 12 meses, com
   a diferença em pontos percentuais.
2. **Gráfico de linhas** (Plotly, como o resto do módulo): os dois acumulados mês
   a mês.
3. **Tabela por categoria:** peso, variação de preço em 12 meses, origem
   ("seus itens" / "estimativa oficial" / "sem grupo definido"), contribuição.
   Ordenada pela contribuição.
4. **Comparação de pesos por grupo do IPCA:** seu peso × peso do IBGE no último
   mês da janela.
5. **Expansor "Como esta conta é feita"**, com a explicação em linguagem simples
   e a lista de itens ignorados.

## 5. Falhas e avisos

Seguem os componentes de aviso que o app já usa (`design/lacunas.py`).

| Situação | O que a tela faz |
|---|---|
| Menos de 13 meses de lançamentos | Não mostra número. Diz quantos meses existem e quantos faltam. |
| `ipca_grupos_mensal` vazia ou sem o mês | Não mostra índice. Nomeia a fonte que falhou. |
| Série do IBGE com mais de 60 dias de atraso em relação ao mês pedido | Mostra o índice até o último mês disponível e diz isso. |
| Categoria sem dado em algum mês | Calcula sem ela naquele mês e lista o mês no aviso. |
| Categoria sem grupo definido | Usa o Índice geral e marca a linha. |

Nunca mostra um índice com dado faltando como se estivesse completo.

## 6. Chat financeiro

Pela premissa do projeto (toda LLM recebe o dado disponível), o resumo do
IPCA Pessoal (acumulado, oficial, as 3 maiores contribuições, a origem de cada
uma) entra no contexto de `core/llm_context_financeiro.build_financas_chat_context`.
Quando a conta não é possível, o contexto diz por quê, em vez de omitir.

## 7. Informes de Configurações (mesmo PR)

Aba nova é mudança de estrutura (regra de 29/10/2026). No mesmo PR:

- **Mapa do App:** `docs/mapa_app/areas/controle_financeiro.json` e
  `python scripts/montar_mapa_app.py`;
- **Documentação:** `views/documentacao.py`, nó e nota da tela nova;
- **Grau de Confiança:** `core/confianca_secao.py`, seção nova em `_SECOES`
  medindo `transactions` e `ipca_grupos_mensal`;
- recontar textos que dizem "cinco abas" do Controle Financeiro, se houver.

`tests/test_configuracoes_sincronizadas.py` tem de passar.

## 8. Testes

Em `tests/test_ipca_pessoal.py`, com números pequenos feitos à mão:

- pesos somam 1 e excluem receita, transferência, investimento, fatura e
  Impostos e Taxas;
- normalização de descrição (acentos, dígitos, sufixo entre colchetes);
- item recorrente: 3 meses entra, 2 não; parcela não entra;
- variação acima de 50% vai para "ignorados";
- cobertura abaixo de 30% cai para a estimativa oficial;
- categoria sem grupo usa o Índice geral;
- encadeamento: duas categorias, pesos e variações conhecidos, acumulado
  conferido à mão;
- soma das contribuições = acumulado;
- histórico de 12 meses devolve aviso e nenhum número;
- mês faltando renormaliza só aquele mês.

Em `tests/test_ipca_grupos_sidra.py`: o parser do SIDRA com uma resposta
gravada em fixture (sem rede no teste).

Mais o teste de configurações sincronizadas, que já existe.

## Fora do escopo

- Pesos móveis e alternância de modos.
- Quantidade ou litros nos lançamentos.
- Edição do mapeamento categoria → grupo pela tela (fica no código).
- Inflação por item com nome de produto (exige nota fiscal detalhada).
