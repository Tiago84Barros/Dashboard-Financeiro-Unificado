# app4-architecture

> Diretrizes de arquitetura do Dashboard Financeiro Unificado (app4).

## Objetivo

Garantir que novas funcionalidades respeitem a arquitetura existente do app4,
que unifica Dashboard Financeiro (App 1), Dashboard-Investimentos (App 2) e
Controle Financeiro (App 3) em uma única aplicação Streamlit + Supabase.

## Quando usar

- Antes de criar novo módulo, view ou job.
- Antes de tocar `core/`, `data_pipeline/`, `etl/` ou `views/`.
- Antes de propor mudança de schema.

## Limites

- App4 **unifica**, não substitui. Lógica já existente em `core/` é a fonte.
- Não duplicar lógica entre módulos.
- Não criar engine SQLAlchemy próprio em outro módulo — sempre
  `core.database.get_engine()`.
- Não ler `os.environ` direto fora de `core/config.py`. Use `settings`.
- Não quebrar o pipeline automático do GitHub Actions.

## Camadas

```
app.py                      ponto de entrada Streamlit (roteamento)
views/                      cada página/aba (render() + helpers)
design/                     tema, componentes visuais reutilizáveis
core/                       camadas de serviço (financeiro, investimentos,
                            proventos, controle, alertas, metas, ...) +
                            config, database, auth
etl/                        schema_setup + importação CSV/XLSX legada
data_pipeline/              orchestrator + jobs automáticos + utils
data_pipeline/importers/    importadores acionados manualmente pela UI
                            (novo — NÃO entram no run_data_updates --all)
migration/                  scripts one-shot de migração dos apps antigos
scripts/                    scripts utilitários (manuais, ad-hoc)
supabase_unificado/         SQL versionado do schema canônico
```

## Checklist de implementação

- [ ] Usar `from core.database import get_engine` (nunca `create_engine`
      direto).
- [ ] Usar `from core.config import settings` (nunca `os.getenv` direto).
- [ ] Filtrar por `settings.OWNER_USER_ID` em queries de dados pessoais.
- [ ] Respeitar `settings.MOCK_MODE` quando houver fallback de mock.
- [ ] Logs no padrão `logging.getLogger(__name__)`, nunca `print()`.
- [ ] Novos jobs do pipeline automatizado: `data_pipeline/jobs/<job>.py` +
      entrada em `data_pipeline/update_registry.py` + `_JOB_MAP` em
      `orchestrator.py`.
- [ ] Importadores manuais: `data_pipeline/importers/<tema>/` — recebem
      bytes do upload e a engine, retornam resumo dict padronizado.
- [ ] Funções públicas de view: `def render() -> None:`.

## Critérios de aceite

- Engine única em todo o app, vinda de `core.database.get_engine()`.
- Configuração única, vinda de `core.config.settings`.
- Nenhuma view nova grava direto em tabela — sempre via camada `core/` ou
  importador dedicado.
- Pipeline automático (`python run_data_updates.py --all`) continua executando
  apenas dados públicos/de mercado.
- GitHub Actions verde após a mudança (mesma matriz de jobs ativos).

## Cuidados para não quebrar o app4

- Manter compatibilidade com o Supabase unificado existente (não renomear
  tabelas; adicionar colunas via `_apply_migrations()` em `etl/schema_setup.py`,
  sempre dentro de `DO $$ ... IF NOT EXISTS ... $$`).
- Cache do Streamlit: `@st.cache_data` para dados, `@st.cache_resource` para
  recursos (engine, sessions). Nunca trocar.
- Não chamar `st.rerun()` em funções de camada `core/` — só em views.
- Idempotência: scripts e importadores devem poder rodar duas vezes sem
  duplicar dados.

## Documentação de testes

- `core/` é testável puro-Python; quando alterar, validar com import direto.
- Views só validáveis manualmente (Streamlit). Documentar smoke test no PR:
  "rodei `streamlit run app.py`, naveguei até X, vi Y".
- Pipeline: `python run_data_updates.py --source <job> --force` deve continuar
  funcionando.
