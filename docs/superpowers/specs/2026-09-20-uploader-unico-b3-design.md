# Uploader único para os arquivos históricos da B3

Data: 2026-09-20
Branch: `b3-upload-unificado`

## Problema

Em Configurações → Atualização de dados → Investimentos, os arquivos da B3
chegam por dois blocos de upload separados: "B3 — Negociação (.xlsx)" e
"B3 — Movimentação (.xlsx)". Os dois vêm do mesmo lugar
(investidor.b3.com.br → Extratos e Informativos), têm a mesma extensão e são
indistinguíveis para o usuário no momento do upload — a única coisa que os
separa é em qual caixa ele solta o arquivo.

Isso tem dois custos. O usuário precisa lembrar qual extrato é qual, e errar a
caixa gera uma falha que não diz "você trocou os arquivos", diz
`Aba 'Negociacao' nao encontrada`. E cada bloco aceita um arquivo por vez,
enquanto a B3 exporta por intervalo de datas — quem tem vários anos de
histórico faz um upload por período, por tipo.

Os dois parsers já sabem identificar o próprio arquivo: ambos procuram a aba
com o nome esperado antes de qualquer outra coisa
(`b3_negociacao.py:79`, `b3_movimentacao.py:82`). A informação que
resolveria o problema já está no arquivo; ela só é consultada tarde demais,
depois de o usuário já ter escolhido por ele.

## Escopo

Unifica **apenas os dois blocos da B3**. Os outros quatro importadores de
investimento — XP Consolidado, Tesouro Direto Consolidado, Tesouro Direto
Analítico e Nomad — ficam exatamente como estão, sem nenhuma alteração.

Fora de escopo, explicitamente:

- Mudar qualquer um dos seis parsers em `data_pipeline/importers/investments/`.
- Alterar schema, tabelas ou idempotência.
- Unificar as outras fontes sob a mesma porta de entrada.

## Arquitetura

### Detector

Módulo novo, puro: `data_pipeline/importers/investments/b3_sniffer.py`

```python
def detect(file_bytes: bytes) -> str | None:
    """Devolve "b3_neg", "b3_mov" ou None a partir dos bytes do .xlsx."""
```

Abre o arquivo com `openpyxl.load_workbook(..., read_only=True)`, normaliza
cada nome de aba (remove acentos, caixa alta) e decide:

| Aba contém   | Retorno   |
| ------------ | --------- |
| `NEGOCIACAO` | `b3_neg`  |
| `MOVIMENTACAO` | `b3_mov` |
| nada disso   | `None`    |

Arquivo que o `openpyxl` não consegue abrir também devolve `None` — o detector
não levanta exceção. Só lê `sheetnames`; não percorre linhas, não toca o banco,
não importa Streamlit.

As duas assinaturas são mutuamente exclusivas: nenhum dos dois extratos da B3
contém a aba do outro. Se um arquivo futuro trouxer as duas, `NEGOCIACAO` é
testada primeiro e vence — e a Movimentação ignora compra/venda de qualquer
forma, então o desempate não perde dado.

### Interface

Os dois primeiros itens de `_INVESTIMENTO_UPLOADS` em `views/configuracoes.py`
saem da lista genérica e passam para uma constante própria, `_B3_JOBS`, no
mesmo módulo — mesmos campos (`job_name`, `source_name`, `table_name`,
`parser_attr`), indexados por `"b3_neg"` e `"b3_mov"`, que são as chaves que o
detector devolve. Eles deixam de ser desenhados pelo laço genérico e passam a
ser renderizados por uma função própria, `_render_import_b3()`, chamada antes
do laço que desenha os quatro blocos restantes.

Conteúdo do bloco:

> **📊 Dados Históricos B3 (.xlsx)**
> investidor.b3.com.br → Extratos e Informativos → Negociação **ou**
> Movimentação. Envie quantos arquivos quiser — o app identifica cada um.
> `[ Upload — vários arquivos ]`  `[ Importar N arquivos ]`

`st.file_uploader(..., type=["xlsx"], accept_multiple_files=True)`. O rótulo do
botão acompanha a contagem, como já acontece nos blocos multi-arquivo atuais.

Os textos de ajuda dos dois blocos antigos não se perdem: o caminho de
exportação continua no `caption` do bloco novo, agora citando os dois extratos.

### Execução do lote

Ao clicar em Importar:

1. Cada arquivo passa pelo `detect`. Os não reconhecidos são separados e não
   chegam a nenhum parser.
2. Os reconhecidos são agrupados por tipo e executados em **ordem fixa**:
   todos os de Negociação, depois todos os de Movimentação. A ordem de upload
   não influencia. A Negociação é a fonte canônica das compras e vendas, e
   ordem determinística garante que o mesmo conjunto de arquivos produza o
   mesmo resultado em qualquer tentativa.
3. Cada arquivo é importado por uma chamada a
   `_executar_importacao_investimento(cfg, payload)` com o `cfg` do seu tipo,
   sem mudança nessa função. Isso preserva a granularidade dos registros:
   `data_update_logs` e `data_freshness_status` continuam recebendo
   "B3 — Negociação (manual)" e "B3 — Movimentação (manual)" como entradas
   separadas, exatamente como hoje.
4. `recompute_for_user` passa a rodar **uma vez ao final do lote**, em vez de
   uma vez por arquivo. Para isso, os dois `cfg` da B3 ganham a chave
   `"skip_recompute": True`, e `_executar_importacao_investimento` deixa de
   recalcular quando ela está presente. O bloco chama o recálculo por conta
   própria depois do último arquivo, se algum deles gravou operação.

### Erros e casos de borda

- **Arquivo não reconhecido:** recusado individualmente, com a razão explícita
  na tela — `"extrato.xlsx: não reconhecido. Abas encontradas: Plan1, Plan2"`.
  Os demais arquivos do lote importam normalmente. Nada é gravado por chute:
  um arquivo sem assinatura conhecida nunca entra no banco sob um rótulo.
- **Arquivo repetido no lote:** absorvido pela idempotência já existente via
  `investment_transactions.external_id` / `dividends.external_id`. Aparece no
  resumo como duplicatas ignoradas, não como erro.
- **Nenhum arquivo reconhecido:** o lote inteiro é recusado, o recálculo da
  carteira não roda, e a mensagem lista os arquivos com suas abas.
- **Falha de um parser no meio do lote:** os arquivos seguintes continuam. O
  resumo marca aquele arquivo como falho, e o status do lote é parcial.

### Resultado na tela

Um card CSS por arquivo — nome, tipo detectado, registros novos, duplicatas,
linhas ignoradas — seguido de um card de total do lote e do resumo do recálculo
da carteira. Segue o helper de card já usado no app; todo o conteúdo do card sai
num único `st.markdown`.

## Testes

`tests/test_b3_sniffer.py`, sobre workbooks sintéticos gerados com `openpyxl`
em memória:

| Caso | Esperado |
| ---- | -------- |
| Workbook com aba `Negociação` | `"b3_neg"` |
| Workbook com aba `Movimentação` | `"b3_mov"` |
| Nomes sem acento (`Negociacao`) | `"b3_neg"` |
| Workbook com aba `Plan1` | `None` |
| Bytes que não são xlsx | `None`, sem exceção |

Mais um teste da ordem de execução: dado um lote cuja ordem de upload é
`[mov, neg, mov]`, a sequência de chamadas ao importador começa pelo arquivo de
Negociação. Verificado com dublê no lugar de
`_executar_importacao_investimento`, sem banco.

## O que muda para o usuário

Antes: duas caixas, um arquivo cada, e errar a caixa dá um erro sobre nome de
aba.

Depois: uma caixa, quantos arquivos quiser, de qualquer um dos dois tipos
misturados, e o app diz o que reconheceu em cada um antes de gravar.

Nenhum dado existente é afetado. Nenhum importador muda. Nada que já era
possível deixa de ser.

---

## Emenda — 2026-09-21: o Consolidado da XP entra também

A seção **Escopo** acima deixou o XP Consolidado de fora por uma razão que não
se sustentou: a marca no nome do arquivo. O relatório carrega o nome da XP,
mas é emitido de dentro do investidor.b3.com.br e chega ao usuário no mesmo
download que os outros dois. Separar o lote por marca é pedir que ele saiba
algo que o arquivo já diz.

O que mudou, e o que **não** mudou:

- `b3_sniffer.py` ganha duas assinaturas para o mesmo tipo, `"POSICAO - "` e
  `"PROVENTOS RECEBIDOS"`. São duas porque as abas são independentes: um
  consolidado de mês sem posição aberta ainda traz a de proventos. O hífen em
  `"POSICAO - "` é o que impede o marcador de casar com qualquer aba que
  mencione posição.
- `_B3_ORDEM` passa a `("b3_neg", "b3_mov", "xp_csl")`. O Consolidado vem por
  último porque a aba "Proventos Recebidos" deduplica contra os proventos da
  Movimentação — antes dela, um mês coberto pelos dois arquivos gravaria o
  provento duas vezes, e a tela não mostraria erro nenhum.
- O lote passa `(nome, bytes)` para quem declara `needs_filename`.
  `_parse_report_date` infere a data do snapshot do nome do arquivo; só com os
  bytes todo relatório cairia em `date.today()`, e como `report_date` compõe a
  chave única de `portfolio_position_snapshots`, um histórico inteiro viraria
  um único snapshot sobrescrito — sem erro, sem linha a menos, só a data
  errada.
- Nenhum parser mudou. Nenhum schema mudou. O Tesouro Direto (Consolidado e
  Analítico) e a Nomad seguem com bloco próprio: os arquivos deles não saem da
  B3, e o Tesouro tem parser distinto do Consolidado.
