# Tesouro Direto — Extrato Analítico e marcação a mercado

## O problema que isto resolve

A aba Tesouro exibia *retorno mercado/custo* e recusava chamar aquilo de
marcação a mercado. A recusa estava certa: valor de mercado menos custo
**mistura carrego com marcação**. Um Tesouro Selic que subiu 10% em 277 dias
não valorizou 10% — ele rendeu a Selic. Derivar "hora de vender" daquele número
seria inventar o sinal.

O que faltava era um dado, não uma fórmula: **a taxa contratada por lote** e a
**data de cada aplicação**. O Extrato Consolidado não traz; o **Extrato
Analítico** traz.

## A identidade

```
PU  = VNA / (1 + i) ** (du/252)
MtM = PU_mercado / PU_pela_taxa_contratada − 1
    = ((1 + i_contratada) / (1 + i_mercado)) ** (du_restante/252) − 1
```

O VNA cancela na razão, então a mesma conta serve para prefixado, IPCA+ (taxa
real dos dois lados) e Selic (ágio dos dois lados), sem precisar do VNA nem do
IPCA projetado. Em título **com juros semestrais** o cupom não cancela: o
resultado sai marcado como aproximação, e a tela diz isso.

### Dias úteis: convenção calibrada, não presumida

`core.tesouro_mtm.dias_uteis` conta `[inicio, fim)` — data-base **incluída**,
vencimento excluído. Isso não veio de manual: veio de inverter
`PU = 1000/(1+i)**(du/252)` nos prefixados (únicos com VNA conhecido) contra
**3.637 PUs oficiais desde 2024-01-01**. Excluindo a data-base, todo vencimento
errava por exatamente −1 du; incluindo-a, o resíduo cai para o arredondamento
da taxa publicada com duas casas (|Δ| mediano 0,0144 du, máximo 0,049).

Estendendo a 2022 aparecem resíduos de 1, 2 e 4 du — são o 20/11, que só virou
feriado nacional com a Lei 14.759/2023. Está no calendário e no teste
`test_consciencia_negra_so_a_partir_de_2024`.

## Peças

| Arquivo | Papel |
|---|---|
| `data_pipeline/importers/investments/tesouro_analitico.py` | Importa o Extrato Analítico (`tesouro_lots`), um arquivo por título, vários de uma vez |
| `data_pipeline/jobs/update_tesouro_curva.py` | Ingere a curva oficial diária (`tesouro_market_rates`) do Tesouro Transparente |
| `core/tesouro_mtm.py` | Calendário, taxa contratada, MtM, IR/IOF regressivos, veredito |
| `core/tesouro_curva.py` | Leitura da curva; unidade, ponta e **índice implícito** |
| `core/tesouro_posicao.py` | Junta lotes e curva; consolida por título |
| `design/tesouro_mtm_cards.py` | Cards (só HTML, nenhuma decisão) |
| `views/investimentos.py` | Seção "Marcação a Mercado — Extrato Analítico" |

## Decisões que não são óbvias

**Só o extrato mais recente de cada título conta.** `tesouro_lots` guarda um
conjunto de lotes por (título, data do extrato). Importar outubro sem descartar
setembro somaria a mesma posição duas vezes, e o dobro pareceria aporte. A
seleção por `MAX(report_date)` está em `core/tesouro_posicao.py`, não na view.

**A procedência do preço viaja até a tela.** Quando a curva não cobre o título,
a avaliação cai para o valor que o próprio extrato imprimiu — verdadeiro, mas
da data do arquivo. O campo `fonte_preco` aparece em todo card, inclusive
quando está tudo certo: valor velho com cara de preço de hoje é exatamente o
defeito que essa barra existe para impedir.

**Duas janelas de retenção.** Guardar a série completa dos ~60 títulos
ofertados multiplicaria a tabela por sessenta num Supabase que opera perto do
teto. A série longa (3 anos, configurável) fica só para os títulos que o
usuário tem; do resto basta a cotação recente (30 dias).

**Título indexado publica spread, não taxa cheia.** Um Tesouro Selic 2031 sai a
0,08% ao ano — o ágio. Capitalizar 0,08% contra uma alternativa faria carregar
parecer sempre pior, sem erro visível. Por isso `comparar_carregar_vs_vender`
recebe `taxa_indice`, aplicado **às duas pernas**: Selic projetada pelo
prefixado de vencimento vizinho, inflação implícita pelo breakeven
`(1+pré)/(1+real)−1`. Sem par no cardápio a resposta é `None`, e o veredito sai
`SEM BASE` — melhor que somar grandezas diferentes com confiança.

**O veredito só existe contra alternativa nomeada.** Vender e recomprar o mesmo
papel é estritamente pior que carregar: antecipa IR e cruza o spread. Comparar
o investimento contra ele mesmo devolveria um "MANTER" que não foi medido. O
seletor só oferece títulos do **mesmo indexador** e com vencimento igual ou
posterior, porque pernas que terminam em datas diferentes não se comparam.

**Conjuntura é datada e feita de preço.** `public.macro` é **anual** — o valor
mais recente pode ter meses e não pode ser apresentado como "hoje". A leitura
de conjuntura vem da curva (inclinação entre vértices prefixados e inflação
implícita), com a data-base impressa ao lado; o dado anual aparece rotulado
pelo ano a que se refere.

## Como rodar

```bash
python -m data_pipeline.orchestrator --job update_tesouro_curva
```

Depois, em **Configurações → Importar dados de investimentos → Tesouro Direto —
Extrato Analítico**, envie um `.xlsx` por título (aceita vários de uma vez).

## Testes

`tests/test_tesouro_mtm.py` e `tests/test_tesouro_analitico.py` — 89 testes
puros, sem banco e sem rede, incluindo a calibração de dias úteis contra PUs
oficiais, a coincidência da chave entre extrato e curva (divergência aqui não
levanta erro: silencia o join) e a inversão de veredito quando o índice
implícito é omitido.
