# Fallback estruturado CVM — qualidade e piloto local

Data da validação: 01/08/2026
Fonte: Portal de Dados Abertos da CVM, Informe Mensal Estruturado de FII
Ambiente: warehouse local `dfu_warehouse`
Uso pretendido: complementar a coleta documental quando hosts oficiais entram
em circuit breaker, sem promover documentos, recalcular score ou publicar
snapshot.

## Dataset e grão

O arquivo anual oficial contém vários meses e versões de entrega. A observação
normalizada usa o grão:

`ticker + métrica + referência + available_at + vintage + fonte`.

`reference_date` representa a competência; `knowledge_at`/`available_at`
representam quando a entrega podia ser conhecida. Revisões do ZIP recebem novo
hash e nova `source_release`, preservando a versão anterior.

## Artefato validado

| Campo | Valor |
|---|---:|
| Ano | 2026 |
| Tamanho | 870.443 bytes |
| SHA-256 | `871458bc5eb46cfd6aa90d1fecad46f36f79e488856feae94e972d83c14672b1` |
| Parser | `cvm_fii_structured` 1.5.0 |
| Contextos mensais | 5.958 |
| Observações normalizadas | 47.092 |
| Exposições normalizadas | 12.846 |
| Release nova | 1 |

## Qualidade da competência mais recente

Competência mais recente: 01/06/2026. A revisão observada em 01/08/2026 ampliou
a cobertura da partição mais recente:

| Check | Antes | Depois |
|---|---:|---:|
| FIIs cobertos | 92 | 1.009 |
| CNPJs elegíveis | 1.066 | 1.066 |
| Cobertura de tickers | 8,63% | 94,65% |
| Observações na competência | 726 | 7.966 |
| Linhagem por `source_release_id` | 100% | 100% |
| Chaves naturais duplicadas | 0 | 0 |
| Valores integralmente vazios | 0 | 0 |
| Referência posterior a `knowledge_at` | 0 | 0 |

Restam 57 FIIs sem registro na competência mais recente; 29 possuem preço e
score local e exigem investigação de atraso de entrega, mudança cadastral ou
ausência legítima no arquivo. A ausência permanece ausente, sem imputação.

## Relação com a diligência

Dos 137 FIIs em diligência no snapshot local 6.4.0, 135 agora possuem ao menos
uma observação CVM na competência mais recente e 2 continuam sem cobertura. A
quantidade em diligência não foi recalculada: o piloto usou
`run_postprocess=False` e não executou score, snapshot ou publicação.

## Idempotência e resiliência

A segunda execução recebeu o mesmo hash por cache condicional, marcou o arquivo
como `cached`, encontrou o checkpoint já concluído e gravou zero observações,
zero exposições, zero revisões e zero linhagens adicionais.

O download agora:

- usa somente HTTPS e cache condicional por ETag/Last-Modified;
- limita o arquivo a 128 MB;
- processa o corpo em streaming e rejeita tamanho declarado ou observado acima
  do limite;
- usa a última cópia íntegra em cache quando o stream oficial é interrompido;
- valida assinatura ZIP, hash, cobertura mínima e coerência temporal antes do
  commit.

## Riscos e próximos gates

- **Médio:** 57 FIIs não cobertos na competência mais recente; segmentar os 29
  negociáveis e conciliar CNPJ/classe antes de qualquer conclusão.
- **Médio:** a competência mais recente ainda é junho de 2026; monitorar o lag
  de publicação sem interpretar atraso regulatório como valor zero.
- **Baixo:** revisões anuais alteram o ZIP; a cadeia de releases e o hash já
  preservam as versões anteriores.

Antes de medir eventual queda da diligência: reprocessar localmente a
metodologia candidata, comparar o conjunto de inputs e executar validação
point-in-time. Publicação continua dependendo de aprovação separada.

Validação de implementação: 1.110 testes aprovados e 3 ignorados, quality
checks e varredura de segredos aprovados e health check do Streamlit com HTTP
200. O agendamento foi reativado a cada duas horas com cooldown documental de
seis horas e fallback CVM compensatório.
