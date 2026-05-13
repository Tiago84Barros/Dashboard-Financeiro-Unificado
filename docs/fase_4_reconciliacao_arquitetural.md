# Fase 4 — Relatório de Reconciliação Arquitetural

> Documento: `docs/fase_4_reconciliacao_arquitetural.md`
> Data: 2026-05-13
> Escopo: Revisão de todos os arquivos criados/alterados na Fase 4 frente à nova estratégia de banco unificado
> Status: Apenas análise e propostas — nenhum código alterado

---

## Contexto da Revisão

A Fase 4 foi implementada originalmente prevendo um banco dedicado para o App 4 (`finapp-dev`).
A estratégia foi revisada: o projeto Supabase do Dashboard Financeiro passa a ser o **banco unificado**,
o Controle Financeiro é **origem temporária de migração**, e o Dashboard-Investimentos migra via SQLite.

Esta revisão identifica o que está alinhado, o que precisa de ajuste e quais riscos existem.

---

## Arquivos Revisados

| Arquivo | Tipo | Revisado |
|---------|------|:--------:|
| `core/config.py` | Config | ✅ |
| `core/auth.py` | Segurança | ✅ |
| `core/database.py` | Infraestrutura | ✅ |
| `etl/schema_setup.py` | DDL | ✅ |
| `etl/importacao.py` | ETL | ✅ |
| `pages/configuracoes.py` | UI | ✅ |
| `app.py` | Roteamento | ✅ |
| `.env.example` | Configuração | ✅ |

---

## 1. O Que Está Totalmente Compatível

### `core/auth.py` — ✅ Sem problemas

- Gate de autenticação funciona independentemente de banco e estratégia
- `SHA-256` hash support correto
- Nenhuma dependência de banco de dados
- Nenhuma referência a credenciais Supabase
- `st.session_state` isolado por sessão, sem persistência em banco
- **Nenhum ajuste necessário**

---

### `etl/importacao.py` — ✅ Lógica central compatível

| Verificação | Resultado |
|-------------|:---------:|
| `dry_run=True` como padrão em TODOS os métodos | ✅ |
| `ON CONFLICT DO NOTHING` em todas as inserções | ✅ |
| Nenhum `DROP TABLE`, `TRUNCATE` ou `DELETE` | ✅ |
| `_TABELAS_VALIDAS` whitelist contra injeção por nome de tabela | ✅ |
| Transação com rollback automático em erro | ✅ |
| Credenciais nunca logadas ou gravadas | ✅ |
| `importar_tabela_generica()` funciona com qualquer URL SQLAlchemy | ✅ |

Os três métodos app-específicos (`importar_app1_dashboard`, `importar_app2_investimentos`,
`importar_app3_controle`) estão como `NotImplementedError` — correto para esta fase.

---

### `core/database.py` — ✅ Estrutura compatível

- Comentário explícito: `service_role_key do Supabase jamais deve ser usado aqui` ✅
- `get_db_status()` não expõe URL nem credenciais ✅
- `pool_pre_ping=True` detecta conexões mortas ✅
- `@st.cache_resource` evita reconexões desnecessárias ✅

---

### `etl/schema_setup.py` — ✅ DDL seguro

- Todo DDL usa `CREATE TABLE IF NOT EXISTS` ✅
- `verificar_schema()` é 100% somente-leitura ✅
- `criar_schema()` é idempotente ✅
- Nenhum `DROP TABLE`, `DROP COLUMN`, `TRUNCATE` ✅
- Ordem FK correta: `usuarios → contas → categorias → transacoes → ...` ✅
- Índices com `IF NOT EXISTS` ✅

---

### `app.py` — ✅ Sem problemas

- Roteamento lazy por módulo: nenhuma integração com banco diretamente
- `verificar_autenticacao()` chamado antes de qualquer renderização
- Avisos de configuração via `settings.validate()` sem expor credenciais
- **Nenhum ajuste necessário**

---

## 2. Problemas Identificados

Os problemas estão classificados por severidade: **Crítico**, **Importante**, **Menor**.

---

### CRÍTICO — C01

**Arquivo:** `core/config.py`
**Problema:** A propriedade `db_url` usa apenas `DATABASE_URL or SUPABASE_DB_URL`. A nova variável `SUPABASE_UNIFICADO_URL` (definida na estratégia) não existe no código e, portanto, nunca seria utilizada.

**Trecho atual:**
```python
@property
def db_url(self) -> str:
    """Retorna a URL de conexao ativa (DATABASE_URL tem prioridade)."""
    return self.DATABASE_URL or self.SUPABASE_DB_URL
```

**Efeito:** Ao configurar `.env` com `SUPABASE_UNIFICADO_URL`, o app ignoraria a variável silenciosamente e continuaria com `DATABASE_URL=""` → banco desativado.

**Proposta de correção:**
```python
# Adicionar ao Settings:
SUPABASE_UNIFICADO_URL: str = os.getenv("SUPABASE_UNIFICADO_URL", "")
SUPABASE_UNIFICADO_ANON_KEY: str = os.getenv("SUPABASE_UNIFICADO_ANON_KEY", "")
SUPABASE_ORIGEM_CONTROLE_URL: str = os.getenv("SUPABASE_ORIGEM_CONTROLE_URL", "")
SUPABASE_ORIGEM_CONTROLE_ANON_KEY: str = os.getenv("SUPABASE_ORIGEM_CONTROLE_ANON_KEY", "")

# Atualizar db_url:
@property
def db_url(self) -> str:
    return (
        self.SUPABASE_UNIFICADO_URL
        or self.DATABASE_URL
        or self.SUPABASE_DB_URL
        or ""
    )

# Adicionar propriedade de conveniência:
@property
def has_supabase_unificado(self) -> bool:
    return bool(self.SUPABASE_UNIFICADO_URL)

@property
def has_origem_controle(self) -> bool:
    return bool(self.SUPABASE_ORIGEM_CONTROLE_URL)
```

---

### CRÍTICO — C02

**Arquivo:** `pages/configuracoes.py` — função `_render_setup()`
**Problema:** As instruções da aba "📋 Setup" orientam a **criar um novo projeto Supabase** chamado `finapp-dev`. Isto contradiz diretamente a estratégia definida, que proíbe a criação de terceiro projeto.

**Trecho problemático:**
```python
st.markdown("""
### Passos para ativar o banco de dados (Fase 4)

**1. Criar o banco no Supabase**
```
Supabase Dashboard → New Project → finapp-dev
Region: South America (Sa Paulo)
```
""")
```

**Efeito:** Um usuário que seguisse estas instruções criaria um terceiro projeto Supabase,
violando a limitação do plano gratuito e contradizendo a arquitetura acordada.

**Proposta de correção:** Reescrever `_render_setup()` para orientar o usuário a:
1. Usar o projeto Supabase **existente** do Dashboard Financeiro
2. Executar os scripts SQL gerados nas Fases 4.3/4.5 — não criar novo projeto
3. Referenciar `docs/banco_unificado_fases.md` como guia de execução

---

### CRÍTICO — C03

**Arquivo:** `pages/configuracoes.py` — botão "Criar tabelas" → `etl/schema_setup.py:criar_schema()`
**Problema:** O botão "Criar tabelas" na aba "Banco de Dados" executa `criar_schema()` diretamente,
sem nenhuma revisão humana prévia dos scripts SQL.

A nova estratégia (Fases 4.3 → 4.4 → 4.5) exige:
- Fase 4.3: scripts DDL gerados e salvos em `supabase_unificado/schema/`
- Fase 4.4: revisão humana obrigatória de cada script
- Fase 4.5: execução **manual** no SQL Editor do Supabase pelo proprietário

O botão atual bypassa Fases 4.3, 4.4 e 4.5 completamente.

**Efeito:** Se `SUPABASE_UNIFICADO_URL` apontar para o projeto do Dashboard Financeiro e o usuário clicar em "Criar tabelas", o DDL será executado no banco real sem ter passado pela revisão formal. Como o DDL usa `IF NOT EXISTS`, o risco de perda de dados é baixo — mas cria tabelas em `public` sem garantia de compatibilidade com o schema existente no projeto.

**Proposta de correção:** Não remover o botão, mas:
1. Adicionar aviso explícito: "Este botão aplica o DDL diretamente no banco configurado. Use apenas após executar os passos da Fase 4.5."
2. Exibir o SQL que será executado **antes** do botão de confirmação
3. Exigir confirmação textual ("Digitar CONFIRMAR") antes de executar em banco real
4. Desabilitar o botão quando `SUPABASE_UNIFICADO_URL` estiver configurado (banco de produção)

---

### IMPORTANTE — I01

**Arquivo:** `etl/importacao.py` — `ImportadorPostgres._conectar()`
**Problema:** O método `_conectar()` passa `connect_args={"connect_timeout": 10}` para o `create_engine`. Este argumento é **específico de PostgreSQL** e causaria erro para conexões SQLite.

```python
engine = create_engine(
    self.source_url,
    connect_args={"connect_timeout": 10},  # ← falha com sqlite:///
    pool_pre_ping=True,
)
```

O Dashboard-Investimentos usa SQLite como banco principal
(`SOURCE_DB_APP2 = "sqlite:///./investment_dashboard.db"`).
Ao tentar conectar via `ImportadorPostgres("sqlite:///...")`, a conexão falharia.

**Proposta de correção:**
```python
def _conectar(self) -> None:
    if not self.source_url:
        self.erro_conexao = "URL da fonte não configurada."
        return
    try:
        # connect_args é específico por dialeto
        is_sqlite = self.source_url.startswith("sqlite")
        extra = {} if is_sqlite else {"connect_timeout": 10}
        engine = create_engine(
            self.source_url,
            connect_args=extra,
            pool_pre_ping=True,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        self._engine_fonte = engine
        self.conectado = True
    except Exception as exc:
        self.erro_conexao = str(exc)
        self.conectado = False
```

---

### IMPORTANTE — I02

**Arquivo:** `etl/schema_setup.py` + `core/database.py`
**Problema:** O schema das 10 tabelas é criado no schema `public` (padrão implícito do PostgreSQL).
O projeto Supabase do Dashboard Financeiro já pode ter tabelas em `public` criadas por outros apps.
Tabelas com o mesmo nome gerariam conflitos silenciosos (o `IF NOT EXISTS` as ignoraria,
mas a estrutura existente pode ser incompatível).

A estratégia menciona a possibilidade de usar `CREATE SCHEMA IF NOT EXISTS app4`
para isolar as tabelas do App 4 — mas isso não foi implementado.

**Risco real:** Depende do que existe no projeto Dashboard Financeiro. A Fase 4.1
(auditoria) vai revelar se há conflito. Mas como o código não prevê isso, há risco
de criar tabelas no `public` sem checar se já existem tabelas homônimas com schema diferente.

**Proposta de correção:** Após a Fase 4.1 (auditoria), se houver conflitos:
- Considerar prefixo `app4_` nas tabelas OU
- Usar schema separado `app4` com `search_path`
- Atualizar `schema_setup.py` para aceitar `schema_name` como parâmetro

---

### IMPORTANTE — I03

**Arquivo:** `.env.example`
**Problema:** Faltam todas as variáveis da nova estratégia.

Variáveis ausentes:
```ini
SUPABASE_UNIFICADO_URL=""
SUPABASE_UNIFICADO_ANON_KEY=""
SUPABASE_UNIFICADO_SERVICE_ROLE_KEY=""  # somente local
SUPABASE_ORIGEM_CONTROLE_URL=""
SUPABASE_ORIGEM_CONTROLE_ANON_KEY=""
```

Além disso, o comentário do `DATABASE_URL` não menciona qual projeto Supabase usar,
e o comentário de `SOURCE_DB_APP2` não menciona que aceita URL SQLite.

**Proposta de correção:** Adicionar as variáveis com comentários claros.
Exemplo para `SOURCE_DB_APP2`:
```ini
# Tiago84Barros/Dashboard-Investimentos — aceita URL SQLite local:
# sqlite:///C:/caminho/para/investment_dashboard.db
SOURCE_DB_APP2=""
```

---

### IMPORTANTE — I04

**Arquivo:** `pages/configuracoes.py` — `_render_import_postgres()`
**Problema:** A UI de importação via PostgreSQL lista as fontes como
"App 1 — Dashboard", "App 2 — Investimentos", "App 3 — Controle Financeiro"
sem comunicar:
- Que "App 3 — Controle Financeiro" **mapeia para `SUPABASE_ORIGEM_CONTROLE_URL`** na nova nomenclatura
- Que "App 2 — Investimentos" usa SQLite, não PostgreSQL (o label "🐘 Banco de Origem (PostgreSQL)" é enganoso)
- Que estas são fontes de leitura **somente para migração** — não para uso recorrente

**Proposta de correção:** Atualizar labels e adicionar `st.info` explicativo
diferenciando fontes SQLite de PostgreSQL.

---

### MENOR — M01

**Arquivo:** `etl/importacao.py` — `importar_tabela_generica()`
**Problema:** O parâmetro `filtro_sql` é concatenado diretamente na query:

```python
query_sql = f'SELECT {cols_select} FROM "{tabela_fonte}" {filtro_sql} LIMIT {limite}'
```

Este é um risco teórico de injeção SQL se `filtro_sql` viesse de entrada não-confiável.
Na prática, o risco é baixo porque:
- O app está protegido por `APP_PASSWORD` (Regra de Segurança)
- `_TABELAS_VALIDAS` já valida o destino
- A fonte é somente-leitura

**Proposta de correção (baixa prioridade):** Adicionar validação básica do `filtro_sql`:
```python
# Bloquear palavras-chave destrutivas no filtro
_PALAVRAS_PROIBIDAS_FILTRO = {"drop", "delete", "truncate", "insert", "update", "--", ";"}
if any(p in filtro_sql.lower() for p in _PALAVRAS_PROIBIDAS_FILTRO):
    res.erros.append("filtro_sql contém palavra-chave não permitida.")
    return res
```

---

### MENOR — M02

**Arquivo:** `core/config.py`
**Problema:** A variável `SOURCE_DB_APP3` é genérica, mas na nova estratégia ela é especificamente
a connection string do Supabase do Controle Financeiro (origem de migração).
O nome `SOURCE_DB_APP3` não reflete esse papel.

**Proposta de correção:** Adicionar `SUPABASE_ORIGEM_CONTROLE_URL` como variável específica
(já proposto em C01) e manter `SOURCE_DB_APP3` como alias de retrocompatibilidade:

```python
@property
def url_origem_controle(self) -> str:
    """URL do Supabase Controle Financeiro (fonte de migração)."""
    return self.SUPABASE_ORIGEM_CONTROLE_URL or self.SOURCE_DB_APP3
```

---

### MENOR — M03

**Arquivo:** `pages/configuracoes.py` — `_render_seguranca()`
**Problema:** O checklist de segurança tem um item marcado como sempre `True` sem verificação real:

```python
(True, "service_role_key nunca referenciada no codigo"),
```

Esta verificação é estática e não detectaria se alguém adicionasse a variável ao código no futuro.
Semanticamente ok para agora, mas deveria ser anotado como "por convenção de desenvolvimento".

**Proposta de correção (baixa prioridade):** Substituir por uma verificação real em tempo de execução:
```python
# Verifica se SUPABASE_SERVICE_ROLE_KEY ou variantes aparecem em settings
has_service_role_exposed = bool(
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or
    os.getenv("SUPABASE_UNIFICADO_SERVICE_ROLE_KEY", "")
)
(not has_service_role_exposed, "service_role_key ausente do ambiente de execução"),
```

---

## 3. Busca por DROP, TRUNCATE, DELETE

Resultado da análise de todos os arquivos Python da Fase 4:

| Termo | `core/config.py` | `core/auth.py` | `core/database.py` | `etl/schema_setup.py` | `etl/importacao.py` | `pages/configuracoes.py` | `app.py` |
|-------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `DROP` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `TRUNCATE` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `DELETE` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

> ✅ **Nenhuma operação destrutiva encontrada em nenhum arquivo.**

---

## 4. Verificação de dry_run

| Método | `dry_run=True` padrão? |
|--------|:----------------------:|
| `ImportadorCSV.importar_transacoes()` | ✅ |
| `ImportadorCSV.importar_operacoes()` | ✅ |
| `ImportadorCSV.importar_proventos()` | ✅ |
| `ImportadorPostgres.importar_tabela_generica()` | ✅ |
| `ImportadorPostgres.importar_app1_dashboard()` | ✅ (NotImplementedError) |
| `ImportadorPostgres.importar_app2_investimentos()` | ✅ (NotImplementedError) |
| `ImportadorPostgres.importar_app3_controle()` | ✅ (NotImplementedError) |
| Toggle na UI (CSV) | ✅ (`value=True`) |
| Toggle na UI (PostgreSQL) | ✅ (`value=True`) |

> ✅ **Todos os métodos ETL têm `dry_run=True` como padrão.**

---

## 5. Verificação de Exposição de Secrets / Credenciais

| Risco verificado | Resultado |
|-----------------|:---------:|
| `service_role_key` referenciada em algum arquivo `.py` | ✅ Não encontrada |
| `DATABASE_URL` ou `SUPABASE_DB_URL` exibida na UI | ✅ `get_db_status()` não expõe URL |
| Connection string logada em console | ✅ `ImportadorPostgres` não loga `source_url` |
| Credenciais em arquivos `.py` hardcoded | ✅ Nenhuma encontrada |
| `APP_PASSWORD` exibida na UI | ✅ Apenas o hash gerado — nunca o valor original |
| Arquivos `.env` ou `secrets.toml` no repositório | ✅ Ambos no `.gitignore` (confirmado via `CLAUDE.md`) |

> ✅ **Nenhum risco de exposição de credenciais encontrado no código.**

---

## 6. Suporte às Três Origens da Nova Estratégia

| Origem | Suporte atual | Problema |
|--------|:-------------:|---------|
| **Supabase unificado** (Dashboard Financeiro) | ⚠️ Parcial | `SUPABASE_UNIFICADO_URL` não existe em `config.py`. `db_url` não a inclui (C01) |
| **Supabase Controle Financeiro** (migração) | ⚠️ Parcial | `SOURCE_DB_APP3` existe, mas sem naming explícito. `SUPABASE_ORIGEM_CONTROLE_URL` não existe (M02, I03) |
| **SQLite Dashboard-Investimentos** (migração) | ⚠️ Falha técnica | `ImportadorPostgres._conectar()` falha com SQLite por `connect_args` PostgreSQL-específico (I01) |

---

## 7. Execução Automática de Schema Sem Revisão

O ponto de risco principal identificado:

```
pages/configuracoes.py
  └── _executar_criar_schema()
        └── etl/schema_setup.criar_schema()
              └── engine.begin() → executa DDL diretamente no banco configurado
```

**Fluxo atual (problemático):**
```
Usuário clica "Criar tabelas"
  → criar_schema() executa imediatamente
  → DDL aplicado no banco sem revisão
```

**Fluxo esperado pela nova estratégia (Fases 4.3/4.4/4.5):**
```
Fase 4.3: Claude gera scripts em supabase_unificado/schema/
Fase 4.4: Proprietário revisa cada script
Fase 4.5: Proprietário executa manualmente no SQL Editor do Supabase
```

O botão atual contorna completamente as Fases 4.4 e 4.5.
**Não é necessário remover o botão** — é uma ferramenta útil para desenvolvimento —
mas precisa de aviso claro e confirmação explícita quando o banco for o banco unificado de produção.

---

## 8. Resumo dos Problemas por Prioridade

| ID | Severidade | Arquivo | Problema | Bloqueia |
|----|:----------:|---------|---------|:--------:|
| C01 | 🔴 Crítico | `core/config.py` | `SUPABASE_UNIFICADO_URL` inexistente; `db_url` incompleto | Fase 4.9 |
| C02 | 🔴 Crítico | `pages/configuracoes.py` | Setup orienta criar novo projeto Supabase (contradiz estratégia) | Fase 4.5 |
| C03 | 🔴 Crítico | `pages/configuracoes.py` + `etl/schema_setup.py` | "Criar tabelas" executa DDL sem revisão humana | Fase 4.4/4.5 |
| I01 | 🟡 Importante | `etl/importacao.py` | `connect_args` PostgreSQL falha para SQLite | Fase 4.7 (SQLite) |
| I02 | 🟡 Importante | `etl/schema_setup.py` | Schema no `public` — possível conflito no Dashboard Financeiro | Fase 4.5 |
| I03 | 🟡 Importante | `.env.example` | Variáveis `SUPABASE_UNIFICADO_*` e `SUPABASE_ORIGEM_*` ausentes | Fase 4.9 |
| I04 | 🟡 Importante | `pages/configuracoes.py` | Label "PostgreSQL" enganoso para fonte SQLite | Fase 4.7 |
| M01 | 🟢 Menor | `etl/importacao.py` | `filtro_sql` concatenado — risco teórico de injeção | — |
| M02 | 🟢 Menor | `core/config.py` | `SOURCE_DB_APP3` ambíguo para nova nomenclatura | — |
| M03 | 🟢 Menor | `pages/configuracoes.py` | Check de `service_role_key` é estático (sempre `True`) | — |

---

## 9. Plano de Correções Proposto

> **Este plano é apenas uma proposta — nenhum arquivo foi alterado.**
> Aguarda aprovação antes de qualquer implementação.

### Pacote 1 — Crítico (necessário antes da Fase 4.9)

**P1.1 — Atualizar `core/config.py`**
- Adicionar: `SUPABASE_UNIFICADO_URL`, `SUPABASE_UNIFICADO_ANON_KEY`, `SUPABASE_ORIGEM_CONTROLE_URL`, `SUPABASE_ORIGEM_CONTROLE_ANON_KEY`
- Atualizar: `db_url` property com prioridade `SUPABASE_UNIFICADO_URL → DATABASE_URL → SUPABASE_DB_URL`
- Adicionar: `has_supabase_unificado`, `has_origem_controle`, `url_origem_controle`
- Compatibilidade retroativa: manter `DATABASE_URL` e `SOURCE_DB_APP3` funcionando

**P1.2 — Reescrever `_render_setup()` em `pages/configuracoes.py`**
- Remover instrução de criar novo projeto (`finapp-dev`)
- Substituir por guia que referencia `docs/banco_unificado_fases.md`
- Orientar uso do projeto Dashboard Financeiro existente

**P1.3 — Adicionar proteção ao botão "Criar tabelas" em `pages/configuracoes.py`**
- Exibir o SQL que será executado antes de confirmar
- Exigir confirmação textual quando o banco for o banco unificado
- Adicionar aviso da Fase 4.4/4.5

---

### Pacote 2 — Importante (necessário antes de usar fontes SQLite / migração)

**P2.1 — Corrigir `etl/importacao.py:ImportadorPostgres._conectar()`**
- Detectar `sqlite://` na URL e não passar `connect_args={"connect_timeout": 10}`

**P2.2 — Atualizar `.env.example`**
- Adicionar todas as variáveis `SUPABASE_UNIFICADO_*` e `SUPABASE_ORIGEM_*`
- Comentar que `SOURCE_DB_APP2` aceita URL SQLite

**P2.3 — Atualizar labels da UI em `pages/configuracoes.py`**
- Renomear sub-aba "🐘 Banco de Origem (PostgreSQL)" para "🔗 Banco de Origem"
- Adicionar nota explicativa sobre SQLite vs PostgreSQL
- Identificar "App 3 — Controle Financeiro" como "Supabase Origem (migração)"

---

### Pacote 3 — Menor (melhorias de qualidade — executar quando conveniente)

**P3.1 — `etl/importacao.py`**: Validar `filtro_sql` contra palavras-chave destrutivas

**P3.2 — `core/config.py`**: Adicionar `url_origem_controle` como alias explícito de `SOURCE_DB_APP3`

**P3.3 — `pages/configuracoes.py`**: Tornar verificação de `service_role_key` dinâmica

**P3.4 — `etl/schema_setup.py`**: Preparar suporte a `schema_name` como parâmetro (após Fase 4.1 confirmar necessidade de schema `app4`)

---

## 10. Conclusão

### O que está bem

A base da Fase 4 é sólida:
- Autenticação, ETL com `dry_run`, `ON CONFLICT DO NOTHING`, sem operações destrutivas, sem exposição de credenciais.
- A arquitetura de `get_engine()` → `settings.db_url` é a abstração correta — basta estender `config.py`.
- Os métodos `importar_app*()` estão como `NotImplementedError`, o que é correto nesta fase.

### O que precisa ser ajustado

Três problemas críticos e quatro importantes precisam ser resolvidos antes de avançar para a Fase 4.9:
1. **`core/config.py`** precisa das novas variáveis e da lógica de prioridade de URL
2. **`pages/configuracoes.py` aba Setup** orienta o usuário incorretamente (criar novo projeto)
3. **Botão "Criar tabelas"** precisa de proteção explícita antes de executar no banco de produção
4. **`etl/importacao.py`** precisa suportar SQLite antes de migrar o Dashboard-Investimentos

### Ordem recomendada de execução das correções

```
P1.2 (Setup corrigido)       ← segurança imediata, evita erro de usuário
P1.1 (config.py atualizado)  ← habilita as novas variáveis
P1.3 (botão protegido)       ← segurança antes de conectar banco real
P2.1 (SQLite fix)            ← antes de iniciar Fase 4.6/4.7 com investimentos
P2.2 (.env.example)          ← documentação para o usuário
P2.3 (labels UI)             ← clareza na interface
P3.x (melhorias menores)     ← quando conveniente
```

---

## Confirmação de Segurança

> ✅ **Nenhum banco foi alterado durante esta revisão.**
> ✅ **Nenhum SQL foi executado.**
> ✅ **Nenhuma credencial foi acessada ou exposta.**
> ✅ **Nenhum arquivo de código foi modificado.**
> ✅ **Nenhum dado foi migrado ou apagado.**
> ✅ **Esta revisão é 100% somente-leitura.**

---

## Referências

- `docs/estrategia_supabase_unificado_plano_gratuito.md` — decisão arquitetural
- `docs/banco_unificado_fases.md` — plano de execução
- `docs/banco_unificado_regras_de_seguranca.md` — regras de segurança
- `docs/auditoria_dados_investimentos.md` — arquitetura do SQLite de investimentos
