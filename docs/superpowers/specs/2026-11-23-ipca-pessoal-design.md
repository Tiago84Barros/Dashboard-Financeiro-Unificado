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

Se o mês escolhido é posterior ao último mês publicado pelo IBGE, a janela
termina no último mês publicado e a tela diz isso (com aviso extra quando o
atraso passa de 60 dias). O mês do lançamento é o de `due_date`, como no resto
do Controle Financeiro.

### Lançamentos que entram

Só despesa real de consumo:

- saída realizada (`amount < 0` e `core.previstos.sql_realizado`), com
  `core.controle.canonical_transaction_type(...) == "expense"` — isso já tira
  receitas, transferências e aporte/resgate de investimento;
- fora: **Financiamento** (dívida, não consumo) e **Impostos e Taxas** (o IPCA
  também não mede imposto de renda nem taxa);
- **pagamento de fatura** (categorias "Pagamento de Cartão" / "Pagamento de
  Fatura", fora da conta do cartão): sai no mês em que a fatura foi importada
  em CSV, porque as compras dela já estão lá; no mês sem fatura importada,
  entra como a categoria **"Cartão (sem detalhe)"**, para o gasto não sumir.
  Essa categoria nunca usa a origem "seus itens" e não tem grupo do IPCA.

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
do gasto dela na janela. Abaixo disso, a amostra não representa a categoria, e
a categoria inteira usa a origem (b).

Mesmo numa categoria que usa "seus itens", um mês sem nenhum par válido (item
que não apareceu no mês anterior, ou só salto acima de 50%) usa a origem (b)
naquele mês. A tela mostra quantos meses foram medidos pelos itens:
"seus itens (n de 12 meses)".

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

Palavras-chave no código (`core/ipca_grupos.py::grupo_da_categoria`), porque
as categorias reais do usuário vêm da importação e variam de nome. O nome da
categoria é normalizado (sem acento e caixa) e casa por palavra inteira — "gas"
não casa dentro de "gastos". A primeira regra que casa vence:

| Palavras-chave | Grupo do IPCA |
|---|---|
| moradia, condominio, luz, agua, gas, aluguel, casa & construcao | Habitação |
| alimentacao, mercado, supermercado | Alimentação e bebidas |
| transporte, combustivel, automotivo | Transportes |
| saude, bem estar, cuidados pessoais | Saúde e cuidados pessoais |
| educacao | Educação |
| lazer, pets, seguros, despesas domesticas, tarifas & anuidade | Despesas pessoais |
| vestuario | Vestuário |
| assinaturas, internet, telefone | Comunicação |
| nenhuma (ex.: Outros, A Revisar, Gastos gerais, Cartão (sem detalhe)) | Índice geral, marcada "sem grupo definido" |

Nos dados reais de 23/11/2026, cerca de 29% do gasto cai em "sem grupo
definido" (sobretudo pagamento de fatura sem detalhe e "Outros"). A tela
mostra esse percentual num aviso, com o nome das categorias.

"Artigos de residência" fica sem categoria própria no app. Isso aparece na
comparação de pesos como 0% do usuário.

## 2. Dados

### Gastos

Leitura própria e enxuta em `core/ipca_pessoal_dados.py`, pela conexão única do
app (`core.database.get_engine()`), no padrão de `core/controle.py`
(`transactions` + `accounts` + `categories`, filtro `sql_realizado`). Lê 24
meses até o mês pedido, para a janela poder recuar até o último mês do IBGE.
A camada de cálculo recebe uma lista de lançamentos e não sabe de onde ela
veio. Isso permite testar a conta sem banco.

No modo de teste do app (`MOCK_MODE`) a aba usa uma cesta fictícia e
determinística de 24 meses.

### IPCA por grupo e pesos do IBGE

Fonte: IBGE, SIDRA, tabela 7060, variáveis 63 (variação mensal, %) e 66 (peso
mensal, %), classificação 315 com o Índice geral (7169) e os 9 grupos. Conferido
em 23/11/2026: série de 01/2020 a 10/2026.

O inventário (skill `inventario-de-dados`) foi feito: a série por grupo não
está no Supabase nem no armazém local; só existe o IPCA cheio.

**Sem tabela no Supabase**: o plano free está em 497/500 MB. A série é pequena
(10 séries × ~80 meses) e mora num **arquivo versionado**,
`data/public/ipca_grupos.json.gz`, como os outros arquivos de `data/public/`
que a Streamlit Cloud lê do repositório.

- `scripts/publicar_ipca_grupos.py` baixa do SIDRA e grava o arquivo (gzip
  determinístico). Sem rede ou com resposta vazia, não grava e sai com código 1:
  o arquivo anterior continua valendo.
- Alvo `ipca_grupos` em `core/publicacao_agenda.py`, cadência de 7 dias,
  módulo macro, sem armazém.
- A tela só lê o arquivo; nunca chama o IBGE.

## 3. Módulos

Cada unidade com um papel, testável sozinha:

| Unidade | Papel | Depende de |
|---|---|---|
| `core/ipca_grupos.py` | Série do IBGE: parser do SIDRA, arquivo publicado (gravar/ler), download, mapeamento categoria → grupo | nada de banco |
| `core/ipca_pessoal.py` | Conta pura: filtro, normalização, recorrentes, pesos, variação por categoria, índice, contribuição, linhas do contexto do chat. Recebe lista de lançamentos, devolve um resultado estruturado. | `core/ipca_grupos.py` |
| `core/ipca_pessoal_dados.py` | Leitura dos lançamentos (ou da cesta fictícia) e a conta do mês | `core/database.py` |
| `scripts/publicar_ipca_grupos.py` | Baixa do SIDRA e grava o arquivo | `core/ipca_grupos.py` |
| `views/controle_ipca_pessoal.py` | Só desenha a tela | os módulos `core` |

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
| Arquivo `ipca_grupos.json.gz` ausente/ilegível, ou sem o Índice geral de um mês da janela | Não mostra índice. Nomeia a fonte que falhou. |
| Leitura dos lançamentos falhou | Não mostra índice. Diz que a leitura falhou. |
| Mês pedido depois do último mês do IBGE | Janela termina no último mês publicado, e a tela diz isso; aviso extra com mais de 60 dias de atraso. |
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
- **Grau de Confiança:** `core/confianca_secao.py`, componente novo "Série do
  IPCA por grupo" **dentro** da seção Controle Financeiro (a aba é parte dela,
  e os lançamentos já são medidos lá). Mede o frescor do último mês do arquivo:
  100% até 75 dias, 0% com 150 dias ou mais; arquivo ausente vale 0% e vira
  nota. Não é seção nova em `_SECOES`;
- recontar textos que dizem "cinco abas" do Controle Financeiro (o Mapa do App
  diz; passa a "seis abas").

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
- mês faltando renormaliza só aquele mês;
- pagamento de fatura: entra como "Cartão (sem detalhe)" só no mês sem fatura
  importada; Financiamento fica fora;
- mês pedido depois do último mês do IBGE: a janela recua e avisa;
- item com 2 meses cai na estimativa oficial; com 3, usa os itens só nos meses
  com par válido.

Em `tests/test_ipca_grupos_sidra.py`: o parser do SIDRA com uma resposta
em fixture (sem rede no teste), o arquivo (ida e volta) e o publicador.

Mais: leitura (`tests/test_ipca_pessoal_dados.py`), partes puras da tela,
componente do Grau de Confiança, contexto do chat e o teste de configurações
sincronizadas, que já existe.

Plano de implementação: `docs/superpowers/plans/2026-11-23-ipca-pessoal.md`.

## Fora do escopo

- Pesos móveis e alternância de modos.
- Quantidade ou litros nos lançamentos.
- Edição do mapeamento categoria → grupo pela tela (fica no código).
- Inflação por item com nome de produto (exige nota fiscal detalhada).
