# Macro internacional — arquitetura e operação

## Auditoria (2026-09-02)

O APP4 usa Python/Streamlit, SQLAlchemy e PostgreSQL/Supabase. A entrada é
`app.py`; as views ficam em `views/`, regras em `core/` e jobs em
`data_pipeline/jobs/`. Já existiam BCB/SGS, uma importação macro legada e uma
ingestão FRED restrita ao snapshot de empresas americanas. Notícias usam um
motor próprio, com persistência e LLM ancorada. Não havia modelo global de
indicadores, observações imutáveis ou vintages. Não há fila externa: o
orquestrador e `data_update_registry` agendam jobs por frequência.

Alterações locais pré-existentes em `core/memoria_mercado`, `core/calibracao`,
scripts e testes foram preservadas.

## Componentes

- `core/macro_data/models.py`: contratos canônicos e UTC.
- `core/macro_data/providers.py`: FRED e World Bank; adaptador SDMX isolado.
- `core/macro_data/repository.py`: escrita append-only e leitura point-in-time.
- `core/macro_data/runtime.py`: lock não bloqueante, execuções, checkpoints e
  saúde por provedor, todos no Docker local.
- `core/macro_data/signals.py`: score determinístico (magnitude, surpresa e
  relevância), separado de confiança e qualidade.
- `data_pipeline/jobs/update_macro_international.py`: ingestão incremental das
  séries explicitamente configuradas.
- `supabase_unificado/schema/065_macro_international_foundation.sql`: tabelas
  novas sem DDL destrutivo.
- `supabase_unificado/schema/066_macro_international_operations.sql`: estado
  operacional e checkpoints sem DDL destrutivo.

O frontend é somente leitura e nunca acessa fontes externas. A tela mostra
fonte, código, unidade, frequência, período e data UTC de coleta.

`core.macro_data.context.build_macro_context` limita o contexto da LLM a 20
fatos normalizados, retém fonte/data/unidade/vintage e rotula dado preliminar,
projeção e defasagem. Metadados externos são higienizados e truncados antes de
entrar no prompt; o módulo não emite causalidade ou recomendação.

Quando as tabelas estiverem disponíveis, a inteligência existente lê somente a
versão mais recente de cada série e acrescenta as linhas sanitizadas ao mesmo
contexto que ancora a resposta do modelo. Se o banco ou a migração não estiver
disponível, registra a limitação e preserva a análise existente.

## Fontes e estado

| Fonte | Estado | Credencial |
|---|---|---|
| FRED/ALFRED | metadados, observações e vintages | `FRED_API_KEY` |
| World Bank | metadados, paginação e observações anuais | não |
| IMF/OECD/BIS/ECB | adaptador SDMX e feature flag; exige dataflow/dimensões configurados | não na base atual |
| Eurostat | Statistics API JSON-stat isolada; catálogo de dimensão pendente | não |
| Trading Economics | calendário opcional, append-only (anterior, consenso, previsão e resultado) | `TRADING_ECONOMICS_API_KEY` |

Não foram assumidos códigos ou dimensões SDMX. Antes de ativar uma fonte SDMX,
descubra oficialmente o dataflow e registre o código da série em ambiente de
configuração. O World Bank pode funcionar imediatamente; FRED funciona após
chave válida e séries configuradas.

`MACRO_INDICATOR_MAPPINGS` recebe JSON com a chave `provedor.codigo` e os
campos `canonical_code` e `category`. Somente categorias da taxonomia do APP4
são aceitas; um mapeamento ausente ou inválido continua como `unmapped`, sem
adivinhação textual.

## Segurança e migração

1. Configure `MACRO_LOCAL_DB_URL` para o PostgreSQL Docker local; esta camada
   não faz fallback para Supabase.
2. Faça backup verificável do banco local e teste a restauração em base descartável.
3. Aplique `065_macro_international_foundation.sql` e
   `066_macro_international_operations.sql` apenas nesse ambiente.
4. Valide a migração e então repita somente no banco local autorizado.
5. O rollback requer remover tabelas novas e é destrutivo; não está
   automatizado e exige confirmação humana explícita.

Todas as chaves ficam em `.env`/Streamlit Secrets; não entram no banco, logs,
frontend nem URLs. As consultas SQL usam parâmetros. Respostas HTTP têm
timeout, três tentativas com backoff+jitter, tratamento de 429/5xx, limite de
tamanho, cache temporário por consulta, intervalo mínimo configurável e circuit
breaker após falhas consecutivas. Falha de uma série não interrompe os demais
provedores.

## Atualização e revisões

`run_macro_updates.py` é o executor isolado para ser agendado por hora no host
que executa o Docker; ele não usa o registry nem os logs do banco principal do
APP4. Cada fonte é opt-in. Uma política local limita as consultas: intraday (1 h), diária (6 h), semanal
(12 h), mensal (24 h), trimestral (48 h) e anual (7 dias). Metadados e
histórico devem ser executados manualmente/backfill; a rotina só consulta
séries habilitadas e vencidas. Se Trading Economics estiver
habilitado, `MACRO_TRADING_ECONOMICS_COUNTRIES` limita explicitamente os países
e a coleta cobre hoje mais os próximos 14 dias. O calendário não é consultado
sem país e credencial configurados. `macro_observations` e `macro_releases`
guardam cada vintage/release sem `UPDATE`; `observations_known_at` limita
`retrieved_at` e `released_at` para impedir look-ahead em backtests.

Antes de coletar, o job toma um advisory lock não bloqueante no banco Docker:
uma segunda instância é marcada como `skipped`, sem esperar nem duplicar rede.
Cada fonte recebe checkpoint e health check persistidos; uma falha parcial não
desfaz as observações já válidas das outras fontes.

## Comandos

```powershell
python -m pip install -r requirements.txt
# após backup e validação em base descartável:
Get-Content supabase_unificado/schema/065_macro_international_foundation.sql
Get-Content supabase_unificado/schema/066_macro_international_operations.sql
python -m pytest tests/test_macro_data.py tests/test_macro_runtime.py -q
python -m ruff check core/macro_data data_pipeline/jobs/update_macro_international.py tests/test_macro_data.py tests/test_macro_runtime.py
python run_macro_updates.py
python run_macro_backfill.py --from 2010-01-01
# opção: limitar a uma fonte configurada
python run_macro_backfill.py --from 2010-01-01 --provider world_bank
# validar fontes/séries sem chamar APIs nem banco
python run_macro_backfill.py --from 2010-01-01 --dry-run
python -m streamlit run app.py
```

## Integração com carteiras e LLM

`core/macro_data/portfolio_context.py` cruza sinais com sensibilidades
explícitas por setor/tipo. B3, Empresas Americanas e FIIs oferecem três modos:
fundamental (sem alterar pesos), contextual moderado e cenário ampliado. O
ajuste é determinístico, limitado e reprojetado nas restrições da carteira;
a LLM recebe fatos, data de corte, cobertura e limitações, mas não calcula pesos.

A trajetória desde 2010 é identificada na interface como reconstrução ex post
da composição atual. O modo `strict` exige que a observação já tivesse sido
recuperada/divulgada na data e permanece separado para backtests point-in-time.
Países não são misturados: B3/FIIs usam Brasil (mais séries cambiais contendo
BRL) e a carteira americana usa EUA (mais séries cambiais contendo USD).

`run_macro_domestic_sync.py` lê `public.macro` no banco principal e escreve
somente no Docker. A tabela de origem não preserva URL primária nem data exata
de divulgação; por isso as linhas são marcadas com procedência histórica
incompleta e só entram retrospectivamente no modo reconstruído.

Limitações atuais: o calendário só existe quando Trading Economics estiver
contratado/configurado; dataflows SDMX adicionais continuam dependentes de
mapeamento oficial. Nenhuma ordem, compra, venda ou rebalanceamento automático
é produzido: salvar/restaurar carteiras continua exigindo ação humana.

O backfill começa em 2010 por padrão e aceita intervalo explícito. Ele não usa
Trading Economics como histórico de calendário: esse conector permanece apenas
para agenda futura. A rotina é append-only e pode ser repetida com segurança;
observações já gravadas são ignoradas pela chave idempotente.

```powershell
python run_macro_domestic_sync.py
python run_macro_portfolio_sync.py
python run_macro_portfolio_impacts.py
```
