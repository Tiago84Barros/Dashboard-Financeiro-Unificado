# Status — Fase 4: Segurança e Camada de Importação ETL

> Data: 2026-05-13
> Versão: v0.4.0
> ruff: All checks passed!
> Startup: HTTP 200 ✅

---

## Objetivo da Fase

Implementar integração segura com banco de dados PostgreSQL/Supabase sem quebrar o funcionamento
existente (MOCK_MODE preservado como padrão), adicionando:

1. Gate de autenticação para proteção no Streamlit Cloud
2. Gerenciamento de schema do banco (CREATE TABLE IF NOT EXISTS)
3. Camada de importação ETL (CSV/Excel e PostgreSQL-to-PostgreSQL)
4. Página de Configurações funcional com 4 abas

---

## Arquivos Criados / Modificados

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/auth.py` | **CRIADO** | Gate de autenticação — senha em texto simples ou hash SHA-256 |
| `etl/schema_setup.py` | **CRIADO** | DDL das 10 tabelas em ordem de dependência FK; `criar_schema()` / `verificar_schema()` |
| `etl/importacao.py` | **CRIADO** | `ImportadorCSV` (transações, operações, proventos) + `ImportadorPostgres` (genérico + app1/2/3) |
| `core/config.py` | **MODIFICADO** | Adicionados `APP_PASSWORD`, `OWNER_USER_ID`, `SOURCE_DB_APP1/2/3`, `has_owner`, `has_source_*` |
| `app.py` | **MODIFICADO** | `verificar_autenticacao()` adicionado; versão bumpeada para v0.4.0 |
| `.env.example` | **MODIFICADO** | Documentadas todas as novas variáveis com exemplos e instruções |
| `pages/configuracoes.py` | **MODIFICADO** | Reescrito com 4 abas funcionais |

---

## Segurança Implementada

### S01 — Bypass de RLS (mitigado por design)

A conexão direta PostgreSQL/SQLAlchemy não aciona o RLS do Supabase.
Mitigação:
- Criar role `app4_reader` com `GRANT SELECT` apenas nas 10 tabelas necessárias
- Nunca conceder `BYPASSRLS` a este role
- Toda query deve incluir `WHERE usuario_id = :owner_id`
- Instruções SQL para setup do role documentadas na aba "Setup" das Configurações

### S02 — Sem autenticação (resolvido)

Dois mecanismos independentes:
1. `core/auth.py` — password gate antes de qualquer renderização (SHA-256 ou texto simples)
2. `OWNER_USER_ID` — UUID do proprietário nos dados; filtro universal nas queries

### S03 — OPENAI_API_KEY (documentado)

Variável isolada em `.env`, nunca exposta na UI. Aviso exibido na sidebar se ausente.

---

## Camada ETL

### ImportadorCSV

Suporta upload de arquivos CSV/Excel diretamente na interface Streamlit.
Mapeia para as tabelas: `transacoes`, `operacoes`, `proventos`.
Todas as operações são `dry_run=True` por padrão — o usuário precisa desmarcar
explicitamente para gravar no banco.

Campos obrigatórios por tipo:

| Tipo | Colunas mínimas |
|------|----------------|
| Transações | `data`, `descricao`, `valor` |
| Operações | `data`, `ticker`, `tipo`, `quantidade`, `preco_unitario` |
| Proventos | `data_pagamento`, `ticker`, `tipo_provento`, `valor_liquido` |

### ImportadorPostgres

Conexão somente-leitura nos bancos dos apps originais (SOURCE_DB_APP1/2/3).
Métodos:
- `listar_tabelas()` — introspecção das tabelas disponíveis
- `listar_colunas(tabela)` — introspecção das colunas
- `importar_tabela_generica(...)` — mapeamento de colunas configurável via UI
- `importar_app1/2/3_*` — placeholders para importação específica por app
  (requer auditoria dos schemas originais antes de implementar)

---

## Schema Setup

10 tabelas em ordem de dependência FK:

```
usuarios → contas → categorias → transacoes → orcamentos → metas
ativos → operacoes → proventos → cotacoes
```

Funções disponíveis:
- `verificar_schema()` → `dict[str, bool]` — presença de cada tabela
- `criar_schema()` → `dict` — executa DDL, retorna `{ok, criadas, ja_existiam, erros}`

Seguro para executar múltiplas vezes (todas as DDLs usam `IF NOT EXISTS`).

---

## Página de Configurações — 4 Abas

### 🗄️ Banco de Dados
- 4 badges de status: DATABASE_URL, conexão ativa, MOCK_MODE, OWNER_USER_ID
- Tabela de presença das 10 tabelas no schema
- Botão "Criar tabelas" (chama `criar_schema()`)

### 📥 Importação de Dados
- Sub-aba CSV/Excel: upload de arquivo, tipo (transações/operações/proventos),
  toggle dry_run, preview das primeiras linhas, botão de importação
- Sub-aba Banco de Origem: campo de URL, teste de conexão, listagem de tabelas,
  mapeamento de colunas, importação genérica com dry_run

### 🔒 Segurança
- Checklist de 7 pontos de segurança com status visual
- Botão de logout (encerra sessão autenticada)
- Gerador de hash SHA-256 para senha do APP_PASSWORD

### 📋 Setup
- Instruções SQL para criar o role `app4_reader` no Supabase
- Passo a passo para configurar OWNER_USER_ID
- Guia de configuração do arquivo `.env`

---

## Variáveis de Ambiente (adicionadas na Fase 4)

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `APP_PASSWORD` | Não | Protege o app no Streamlit Cloud — texto ou hash SHA-256 |
| `OWNER_USER_ID` | Sim (banco real) | UUID do proprietário; filtro universal nas queries |
| `SOURCE_DB_APP1` | Não | Connection string do banco do App 1 (somente leitura) |
| `SOURCE_DB_APP2` | Não | Connection string do banco do App 2 (somente leitura) |
| `SOURCE_DB_APP3` | Não | Connection string do banco do App 3 (somente leitura) |

---

## MOCK_MODE Preservado

O `MOCK_MODE=true` continua sendo o padrão no `.env.example`.
Nenhuma página existente foi alterada.
O app inicializa e exibe todos os dados mockados normalmente enquanto o banco
não estiver configurado — zero regressão.

---

## Resultado dos Testes

| Teste | Resultado |
|-------|-----------|
| `python -m ruff check . --output-format=concise` | ✅ All checks passed! |
| `curl http://localhost:8502` | ✅ HTTP 200 |
| Inicialização com MOCK_MODE=true | ✅ Todos os módulos carregados |
| Import de `core.auth` | ✅ Sem erros |
| Import de `etl.schema_setup` | ✅ Sem erros |
| Import de `etl.importacao` | ✅ Sem erros |

---

## Próximo Passo: Configurar Banco

Para ativar dados reais:

1. No Supabase (projeto `finapp-dev`), executar o SQL da aba Setup das Configurações
   para criar o role `app4_reader`
2. Copiar `.env.example` para `.env`
3. Preencher `DATABASE_URL` com a connection string do pooler (Transaction Mode)
4. Preencher `OWNER_USER_ID` com o UUID do usuário: `SELECT id FROM usuarios LIMIT 1;`
5. Alterar `MOCK_MODE=false` no `.env`
6. Executar "Criar tabelas" na aba Banco de Dados das Configurações
7. Implementar `_visao_geral_real()` em `core/financeiro.py`

---

## Fase 5 — Módulo de Investimentos (planejada)

| Item | Arquivo | Dependência |
|------|---------|-------------|
| Wrapper yfinance | `core/cotacoes.py` | Decisão D02 (yfinance vs. API paga) |
| Custo médio + TWRR | `core/investimentos.py` | Fase 4 banco ativo |
| Carteira completa | `pages/carteira.py` | `core/investimentos.py` |
| Evolução + benchmark | `pages/investimentos.py` | `core/cotacoes.py` |
| Histórico de dividendos | `pages/proventos.py` | `core/investimentos.py` |
