# Lote único do Tesouro Direto e rótulo "Dados Históricos Internacionais"

**Data:** 21/09/2026
**Pedido:** *"Agora em configurações, junte tudo que estiver relacionado a
tesouro direto e na nomenclatura de NOMAD, você põe 'Dados Históricos
Internacionais'"*

## O que muda na tela

Em **Configurações → Importar investimentos**, os dois blocos do Tesouro
Direto ("Extrato Consolidado" e "Extrato Analítico") viram **um só** —
🏦 Dados Históricos Tesouro Direto (.xlsx) —, com um único uploader que aceita
os dois documentos misturados e identifica cada arquivo pelo conteúdo. É a
mesma mecânica que os três extratos da B3 já usam desde 20/09/2026.

O bloco da Nomad passa a se chamar **🌎 Dados Históricos Internacionais
(.pdf)**. Só o rótulo muda.

## Por que o conteúdo decide, e não o nome da aba

O detector do Tesouro (`tesouro_sniffer.detect`) checa **cabeçalho antes de
nome de aba**, e essa ordem é o ponto central do desenho.

`tesouro_analitico.parse_arquivo` procura uma aba cujo nome contenha
"ANALITICO" e, **não achando, cai na primeira aba do arquivo**. Ou seja: um
Extrato Analítico cuja aba se chame "Extrato" é lido normalmente pelo parser do
Analítico. Se a detecção olhasse só o nome da aba, esse mesmo arquivo seria
entregue ao parser do Consolidado — que leria linhas de aplicação como posição
e gravaria snapshot a partir de um documento de lotes. Nenhuma exceção, nenhum
erro na tela: dado errado sob a fonte errada.

Por isso:

1. Se o cabeçalho do Analítico está lá (título **e** vencimento) → Analítico.
2. Só então, se existe aba chamada exatamente `Extrato` → Consolidado.
3. Caso contrário → recusado, com as abas encontradas na mensagem.

O reconhecimento do passo 1 chama `tesouro_analitico.parse_cabecalho`, e não
uma cópia das expressões regulares. A pergunta que o detector faz é exatamente
"este arquivo passa no cabeçalho que o parser exige?" — uma segunda cópia dos
padrões divergiria na primeira correção feita de um lado só, e o sintoma seria
um arquivo aceito na porta e recusado lá dentro. O passo 2 compara o nome da
aba de forma exata (`"Extrato" in sheetnames`) pelo mesmo motivo: é a checagem
literal que `tesouro_direto.parse` faz.

## Estrutura

- `data_pipeline/importers/investments/xlsx_probe.py` (novo) — abrir, fechar,
  listar abas, normalizar texto, ler o topo de uma aba. Base comum dos dois
  sniffers; o `b3_sniffer` passou a importar daqui em vez de manter cópia.
- `data_pipeline/importers/investments/tesouro_sniffer.py` (novo) — `detect`
  devolvendo `"tesouro_analitico"`, `"tesouro_direto"` ou `None`.
- `views/configuracoes.py` — o que era específico da B3 virou genérico:
  `planejar_lote(arquivos, ordem, detector, listar_abas)`,
  `_consolidar_resultados_lote(resultados, fonte)`, `_executar_lote(arquivos,
  lote)`, `_render_import_lote(lote)` e `_render_resultado_lote`. Cada lote é
  um dicionário (`_LOTE_B3`, `_LOTE_TESOURO`) com título, legenda, jobs e
  planejador. Os nomes antigos da B3 continuam existindo como casca fina.

Ordem de execução do lote do Tesouro: `("tesouro_direto",
"tesouro_analitico")`. Os dois gravam em tabelas diferentes
(`portfolio_position_snapshots` e `tesouro_lots`) e não disputam linha nenhuma,
então a ordem não protege contra duplicidade — ela garante que o mesmo conjunto
de arquivos produza sempre o mesmo resultado e o mesmo relatório na tela.

## O que deliberadamente NÃO muda

- **Nenhum parser e nenhum schema.** O Consolidado segue recusando títulos já
  cobertos por operações da B3; o Analítico segue gravando lote a lote.
- **`source_name` da Nomad.** O rótulo visível virou "Dados Históricos
  Internacionais", mas a fonte registrada continua `"Nomad — Notas PDF
  (manual)"`. Ela é a chave que `data_update_logs` e `data_freshness_status` já
  carregam: renomeá-la abriria uma segunda linha de histórico e a primeira
  ficaria congelada na data da última importação, com cara de fonte
  abandonada.
- **Os `source_name` e `job_name` do Tesouro**, pelo mesmo motivo. Os dois
  documentos seguem sendo duas fontes distintas no painel de atualização — o
  que foi unificado é a porta de entrada, não a contabilidade.

O Analítico passou a receber `(nome, bytes)` em vez de bytes puros. O parser não
infere nada do nome, mas o usa como rótulo em toda mensagem de erro: sem ele,
um lote de dez títulos reportaria dez falhas todas chamadas "extrato.xlsx".

## Testes

`tests/test_tesouro_sniffer.py` — o caso que mais importa é
`test_o_conteudo_vence_o_nome_da_aba`: Analítico numa aba chamada "Extrato"
tem de continuar sendo Analítico. Também cobrem cabeçalho incompleto (o que o
parser recusa lá dentro tem de ser recusado aqui), arquivo ilegível, xlsx de
outra origem, a comparação exata da aba do Consolidado, a ordem fixa do lote, a
preservação da ordem de upload dentro de cada tipo, o nome do arquivo chegando
ao Analítico e a permanência do `source_name` da Nomad.

## Nota sobre o spec anterior

`2026-09-20-uploader-unico-b3-design.md` diz, em "Fora de escopo", que o
Tesouro e a Nomad "seguem com bloco próprio". Isso valia naquele dia e deixou
de valer aqui: o Tesouro passou a ter lote próprio com a mesma mecânica, e a
Nomad mudou de rótulo.
