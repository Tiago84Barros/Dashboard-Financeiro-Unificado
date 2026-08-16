# CLAUDE.md

## Objetivo do Projeto

Aplicação Streamlit unificada para:
- controle financeiro;
- investimentos;
- carteira;
- proventos;
- análise de empresas;
- indicadores econômicos;
- inteligência artificial aplicada.

## Repositórios Originais

- Tiago84Barros/Dashboard
- Tiago84Barros/Controle_Financeiro
- Tiago84Barros/Dashboard-Investimentos

## Onde estão os dados — leia ANTES de planejar qualquer coisa que envolva dado

Este projeto tem **dois** bancos, e ignorar o segundo já causou retrabalho caro.

**Supabase (nuvem, plano free 500 MB)** — é o que `core/database.py::get_engine()`
devolve e o único que a Streamlit Cloud alcança. Só a vitrine mora aqui.

**Warehouse local (Docker `dfu_warehouse`, porta 5433)** — Postgres 16 + pgvector,
vários GB. É onde moram os dados pesados: `market_us.prices_daily` e
`prices_monthly`, observações de CRI e FII, histórico de séries. A URL sai de
`scripts/publish_fii_selection_from_local.py::_warehouse_url()`, que lê a senha do
container. O app publicado **não** o alcança — ele serve para gerar e publicar
vitrines, não para consulta em produção.

Antes de propor ingerir, buscar em API externa ou criar tabela: **verifique se o
dado já está no warehouse local.** Ele frequentemente está.

```bash
docker ps --filter name=dfu_warehouse
python -c "from scripts.publish_fii_selection_from_local import _warehouse_url; print(_warehouse_url())"
```

## Base de conhecimento (Obsidian + graphify)

Vault em `../ProjetoIA/` — é um diretório de trabalho adicional, acessível.

- `04_App_Dashboard_Financeiro_Unificado/` — 16 notas sobre este app: arquitetura,
  funcionalidades e o histórico de decisões por período.
- `05_Banco_de_Dados/` — modelagem, tabelas, migrations, mapa dos dados.
- `graphify-out/GRAPH_REPORT.md` — grafo de conhecimento do código. O `CLAUDE.md`
  do vault manda lê-lo antes de sair fazendo grep.

**Consulte o vault antes de redescobrir.** Exemplo real do que custa não fazer
isso: `atualizacoes_2026-07_b3_rag_supabase_local_first.md` seção 5 documenta,
desde julho, que o Supabase estourou os 500 MB e que a saída foi a arquitetura
local-first — com runbook em `local_staging/README.md`. Uma sessão de agosto
gastou horas redescobrindo isso e chegou a propor buscar via yfinance dados que
já estavam no disco.

## Regras

- Não apagar funcionalidades existentes sem validação.
- Migrar funcionalidades por módulos.
- Não duplicar conexão de banco.
- Priorizar organização modular.
- Manter padrão Streamlit.
- Separar interface, lógica de negócio e ETL.

## Estrutura desejada

- app.py
- pages/
- core/
- etl/
- design/
- docs/

## Execução

```bash
pip install -r requirements.txt
streamlit run app.py
```
