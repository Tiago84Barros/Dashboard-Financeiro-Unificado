# Coleta oficial de relatórios de FIIs

## Objetivo

Complementar CVM/Fundos.NET com documentos publicados nos canais oficiais de
Relações com Investidores (RI), preservando a separação entre:

- dados regulatórios;
- fatos reportados pelo gestor;
- estimativas do gestor;
- inferências e achados qualitativos;
- dados aceitos após revisão.

Nenhuma extração documental alimenta automaticamente o score ou libera uma
carteira. A evidência passa por revisão humana e mantém documento, SHA-256,
página, parser, data de referência e instante de conhecimento.

## Fluxo

1. `market.fii_document_sources` mantém a allowlist de fontes oficiais.
2. O coletor aceita somente HTTPS, valida host e DNS públicos, limita tamanho e
   bloqueia redirecionamentos para fora da allowlist.
3. HTML e APIs WordPress oficiais são varridos por PDFs do FII correto.
4. `market.fii_documents` e `market.fii_document_versions` preservam catálogo,
   hash e revisões.
5. O parser determinístico gera métricas, projetos e achados de risco.
6. Evidências entram como `pending`; estimativas ficam rotuladas como
   `manager_estimate`.
7. Divergências materiais entre documentos da mesma data entram em
   `market.fii_reconciliation_issues`.
8. A aba **Revisão de dados** permite aceitar ou rejeitar métricas,
   empreendimentos e alegações qualitativas, com hash de auditoria.

## Estruturas novas

- `market.fii_document_sources`
- `market.fii_projects`
- `market.fii_project_observations`
- `market.fii_document_findings`
- `value_nature` e `review_priority` em
  `market.fii_extraction_evidence`

A migration é
`supabase_unificado/schema/046_fii_official_documents_and_projects.sql`.
O rollback é destrutivo e exige confirmação humana:
`supabase_unificado/rollback/046_fii_official_documents_and_projects_rollback.sql`.

## Operação

```powershell
python scripts/collect_fii_official_documents.py --limit 25
python scripts/collect_fii_official_documents.py --source-id 1
python scripts/collect_fii_official_documents.py --source-id 1 --process-limit 5 --ticker MFII11
python scripts/collect_fii_official_documents.py --process-limit 10 --source-hash-only --min-free-gb 2
```

O estágio `official_documents` também faz parte de
`data_pipeline.market.fii_enrichment.run_enrichment`.

`--source-hash-only` é o modo indicado quando há pouco espaço local: preserva
URL, hash, tamanho, MIME e evidência extraída sem manter outra cópia do PDF.
Desde o parser 1.6.3, URLs não HTTPS, artefatos `manual-pilot` e promoções
provisórias sem revisão humana ficam bloqueados na rotina recorrente.

## Piloto MFII11 — 31/07/2026

Fonte analisada:
`16915968000188-REL15072026V01-001249631.pdf`, referência junho de
2026, publicado em 15/07/2026.

Resultados locais:

- 29 páginas e 57.486 caracteres;
- 14 evidências quantitativas pendentes;
- 10 métricas novas de desenvolvimento;
- 34 empreendimentos extraídos;
- 7 achados de risco atribuídos ao gestor;
- 0 evidências aceitas automaticamente;
- 0 promoções automáticas ao score.

O RI oficial da Mérito expõe 94 documentos MFII11 por sua API WordPress, de
agosto de 2016 a fevereiro de 2025. Para documentos recentes, Fundos.NET/CVM
permanece a fonte oficial mais atual; o relatório de junho de 2026 possui o
identificador Fundos.NET `1249631`.

## Medição antes/depois

Snapshot público v2 usado como linha de base:

- 394 FIIs no snapshot;
- 382 FIIs classificáveis;
- 245 com dados suficientes;
- 137 com dados insuficientes;
- confiança mediana de 80,05%;
- cobertura média dos campos essenciais de 90,71%.

Após o piloto, esses números permanecem iguais. Isso é esperado e desejável:
o novo conteúdo está pendente de revisão e as métricas de desenvolvimento ainda
não fazem parte do score validado. O arcabouço informacional melhorou, mas a
quantidade em diligência não deve cair antes de revisão, reconciliação e
validação point-in-time da eventual mudança metodológica.

## Estado de implantação

Em 31/07/2026, a migration 046 foi validada em banco descartável e aplicada
somente ao armazém local, após backup verificável. A alteração ainda não foi
aplicada ao Supabase remoto e a branch ainda não foi publicada.

A regressão completa passou com 1.099 testes aprovados e 3 ignorados; os quality
checks, a compilação e a inicialização headless do Streamlit também passaram.
A validação visual automatizada permaneceu pendente porque a conexão do
navegador do ambiente falhou antes de abrir a aplicação.

## Início do backfill — 31/07/2026

O backfill local foi iniciado em lotes limitados, usando `source-hash-only`
porque o disco possuía aproximadamente 4 GB livres. No preflight havia:

- 26.171 documentos recentes elegíveis, distribuídos por 1.065 tickers;
- 47.349 documentos com status `pending`;
- 94 documentos RI do MFII11 ainda pendentes.

As rodadas iniciais processaram 12 documentos distintos de 12 FIIs. A inspeção
detectou e corrigiu três classes de problema antes da expansão:

- textos educacionais de glossário classificados incorretamente como riscos;
- artefato `manual-pilot` com URL local entrando na seleção recorrente;
- rota legada promovendo três métricas documentais provisórias.

O parser 1.6.3 bloqueia URLs não HTTPS, ignora artefatos `manual-pilot`, exige
contexto material para achados de risco e deixa a promoção provisória
desabilitada por padrão. As três observações criadas antes desse bloqueio foram
marcadas como `rejected`, sem exclusão física e com evento de auditoria.

O lote de controle do parser 1.6.3 gerou 10 métricas pendentes, nenhuma promoção
provisória e nenhum achado genérico persistido. Respostas `520`, timeout e
download incompleto do Fundos.NET permaneceram como falhas recuperáveis.

## Resiliência do coletor 1.6.4 — 01/08/2026

O coletor/parser 1.6.4 mantém a extração determinística da versão anterior e
endurece o transporte e a retomada:

- exige HTTPS também no downloader de baixo nível;
- aplica retry somente a falhas transitórias, inclusive `IncompleteRead` e
  interrupções de stream;
- respeita `Retry-After`, usa backoff exponencial com jitter e impõe orçamento
  total de tempo por documento;
- solicita conteúdo sem compressão e fecha a conexão após cada documento para
  reduzir falhas em servidores legados;
- limita documentos por host no mesmo lote;
- abre circuit breaker auditável por host e exclui o host durante o cooldown;
- respeita `next_retry_at` em todos os estados elegíveis;
- restaura o estado anterior de claims adiados ou liberados;
- executa cada lote do worker em uma única chamada, de forma que todos os
  documentos compartilhem o mesmo circuito;
- continua descartando conteúdo parcial, sem retenção binária e sem promoção
  automática ao score.

Três controles locais identificaram indisponibilidade simultânea de
`fnet.bmfbovespa.com.br`, `merito.inc` e `web.cvm.gov.br`. Os circuitos foram
abertos após o limite configurado e nenhum documento ficou em `processing`.
Dois documentos de um host responsivo foram extraídos, gerando quatro métricas
e oito achados pendentes; zero observações provisórias foram promovidas.

A regressão completa terminou com 1.104 testes aprovados e 3 ignorados. Os
quality checks, a compilação, a varredura de segredos e o health check do
Streamlit também passaram. A recorrência permanece pausada porque o controle
externo ainda apresentou taxa de falha superior a 50%; reativação depende de
uma janela em que ao menos uma origem oficial conclua o lote dentro do gate.

## Fallback estruturado CVM — 01/08/2026

O worker resiliente passou a acionar o Informe Mensal Estruturado do Portal de
Dados Abertos da CVM quando um host documental já está em circuito ou abre o
circuito durante o lote. A execução usa apenas o ano corrente, cache
condicional, checkpoint por hash/parser e `run_postprocess=False`.

O piloto do arquivo 2026 (`SHA-256 871458bc5eb46cfd…`) processou 5.958 contextos,
47.092 observações e 12.846 exposições. Na competência mais recente, a cobertura
passou de 92 para 1.009 dos 1.066 FIIs cadastrados (94,65%), com 100% de linhagem,
zero duplicidades naturais, zero valores vazios e zero violações temporais.

A repetição foi idempotente: usou cache, reconheceu o checkpoint e inseriu zero
linhas. Dos 137 FIIs em diligência, 135 já possuem evidência mensal recente e 2
continuam sem cobertura, mas a diligência não foi recalculada nem publicada.
Detalhes: `docs/fii_cvm_fallback_quality_2026-08-01.md`.

Após 1.110 testes aprovados, 3 ignorados, quality checks e health check HTTP 200,
o agendamento foi reativado. Falha documental totalmente transitória e coberta
por fallback íntegro vira `warning`; falha estrutural, queda material de
cobertura, duplicidade, violação temporal, promoção provisória ou claim preso
continua pausando a recorrência.

## Descoberta auxiliar pelo Fundamentus

O comando `scripts/discover_fii_reports_fundamentus.py` consulta, no máximo,
20 páginas de FIIs por execução. O Fundamentus atua apenas como índice: só
são aceitos links HTTPS para o endpoint exato `downloadDocumento` do
Fundos.NET. A URL canônica, o identificador do documento e a chave natural são
do Fundos.NET; links externos, linhas sem período e parâmetros inesperados são
rejeitados.

O modo padrão é somente leitura:

```powershell
python scripts/discover_fii_reports_fundamentus.py --tickers BRCR11 MFII11
```

Para cadastrar apenas lacunas na fila local:

```powershell
python scripts/discover_fii_reports_fundamentus.py --tickers BRCR11 MFII11 --write
```

A inserção usa `ON CONFLICT DO NOTHING`: registros existentes não são
sobrescritos. Cada novo documento recebe auditoria com o hash da página de
descoberta e `score_eligible=false`. O coletor não baixa binários, não executa
o parser, não promove evidências e não publica snapshots.

Antes da escrita, a identidade do fundo precisa ser ancorada pela sobreposição
de ao menos um ID Fundos.NET já conhecido para o mesmo ticker. Qualquer colisão
da URL canônica com outro ticker bloqueia a página inteira.

### Piloto Fundamentus/Fundos.NET — 02/08/2026

O dry-run limitado a 20 FIIs encontrou 2.071 links Fundos.NET: 1.087 já
existiam no warehouse e 984 eram lacunas históricas. As 984 lacunas foram
registradas somente no warehouse local, todas como `pending`, com auditoria
individual e referências entre abril de 2016 e junho de 2026. Nenhuma página
falhou e nenhum link precisou ser rejeitado.

A validação posterior confirmou 984 documentos para 984 eventos de auditoria,
zero URLs fora do endpoint canônico, zero datas vazias, zero versões baixadas,
zero registros elegíveis ao score e zero chaves naturais duplicadas. Uma nova
execução do MFII11 reconheceu os 133 links como existentes e inseriu zero
linhas, comprovando a idempotência.

O catálogo bruto local passou de 53.629 para 54.613 documentos e a fila
`pending`, de 47.343 para 48.327. Como não houve download, extração, revisão
ou recálculo, o snapshot metodológico 6.4.0 permanece com 245 FIIs `ready` e
137 `insufficient`. A automação recorrente continua pausada até a correção do
timeout do worker documental.
