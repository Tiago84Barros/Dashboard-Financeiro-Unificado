# Estratégia Supabase Unificado — Plano Gratuito

> Data: 2026-05-13
> Contexto: Conta Supabase gratuita com limite de 2 projetos.
> Projetos existentes: "Dashboard Financeiro" e "Controle Financeiro".
> Objetivo: Usar um dos dois como banco unificado para o App 4 (Dashboard-Financeiro-Unificado).

---

## REGRA DE SEGURANÇA ABSOLUTA

> **Nenhum script, comando ou instrução neste documento pode conter ou executar:**
>
> - `DROP TABLE`
> - `DROP SCHEMA`
> - `TRUNCATE`
> - `DELETE` (sem cláusula `WHERE` explícita e autorização manual)
> - `ALTER TABLE ... DROP COLUMN`
> - Qualquer operação destrutiva irreversível
>
> **Toda operação destrutiva exige:**
> 1. Backup confirmado e verificado
> 2. Aprovação manual explícita do proprietário
> 3. Execução em ambiente de teste primeiro
> 4. Registro no log de mudanças

---

## Contexto do Ecossistema

Conforme documentado em `MAPA_SUPABASE.md` e `MAPA_GERAL_DOS_APPS.md`:

| Projeto Supabase | App Principal | Domínio de Dados |
|-----------------|---------------|-----------------|
| **Dashboard Financeiro** | App 1 (agregador) | Visão geral: saldo, fluxo, patrimônio, alertas. Consome App 2 e App 3 |
| **Controle Financeiro** | App 3 (produtor) | Transações, categorias, orçamentos, metas |

O App 4 (Dashboard-Financeiro-Unificado) precisa de todas as 10 tabelas do schema unificado:
`usuarios · contas · categorias · transacoes · orcamentos · metas · ativos · operacoes · proventos · cotacoes`

---

## Comparação das Opções

### Opção A — Dashboard Financeiro como banco unificado

O projeto "Dashboard Financeiro" passa a ser o banco central do App 4.
O "Controle Financeiro" vira fonte de migração e depois backup/teste.

#### Vantagens

| # | Vantagem |
|---|---------|
| A1 | App 1 (Dashboard Financeiro) foi **projetado como agregador** — exatamente o papel do App 4 |
| A2 | Schema planejado já inclui todos os 10 domínios (transações, investimentos, controle) |
| A3 | Alinhamento estratégico: App 4 e App 1 têm o mesmo propósito de visão unificada |
| A4 | Tabelas de investimentos (`ativos`, `operacoes`, `proventos`, `cotacoes`) já fazem parte do design original deste projeto |
| A5 | Se App 1 (Next.js) for construído no futuro, pode reutilizar o mesmo banco sem migração |
| A6 | A conexão futura `App 4 → finapp-prod` já está prevista em `MAPA_SUPABASE.md` |

#### Riscos

| # | Risco | Probabilidade | Severidade |
|---|-------|:---:|:---:|
| RA1 | Schema atual pode ter tabelas incompletas ou divergentes da modelagem planejada | Média | Médio |
| RA2 | Dados existentes no projeto podem colidir com o novo schema unificado | Baixa | Alto |
| RA3 | Qualquer erro de configuração afeta também o App 1 futuro | Baixa | Médio |

#### Impacto nos apps existentes

- **App 1 (Dashboard Financeiro — Next.js):** Em planejamento — sem código em produção.
  Impacto praticamente nulo: schema unificado beneficia o App 1 quando for construído.
- **App 3 (Controle Financeiro — Next.js):** Não conectado a este projeto. Sem impacto.
- **App 4 (Streamlit):** Receptor — esse é o objetivo da mudança. Impacto positivo.

#### Complexidade de migração

**Baixa.** O projeto Dashboard Financeiro já tem o schema correto por design. A migração consiste em:
1. Adicionar as tabelas faltantes (se alguma não existir ainda)
2. Migrar dados do projeto Controle Financeiro para cá
3. Conectar o App 4 via `DATABASE_URL`

---

### Opção B — Controle Financeiro como banco unificado

O projeto "Controle Financeiro" passa a ser o banco central do App 4.
O "Dashboard Financeiro" vira fonte de migração e depois backup/teste.

#### Vantagens

| # | Vantagem |
|---|---------|
| B1 | Dados do Controle Financeiro (transações, orçamentos) já estão neste projeto — sem migração para a parte financeira |
| B2 | App 3 (Controle Financeiro — Next.js) continuaria conectado ao mesmo banco sem precisar migrar |

#### Riscos

| # | Risco | Probabilidade | Severidade |
|---|---------|:---:|:---:|
| RB1 | Schema do Controle Financeiro **não foi projetado para investimentos** — faltam `ativos`, `operacoes`, `proventos`, `cotacoes` | Alta | Alto |
| RB2 | Adicionar tabelas de investimentos em um schema de controle cria mistura de domínios e dificulta manutenção | Alta | Médio |
| RB3 | Conflito futuro: App 3 (Next.js) e App 4 (Streamlit) usando o mesmo banco com lógicas diferentes | Média | Alto |
| RB4 | App 1 ficaria sem banco, pois o projeto mais alinhado (Dashboard Financeiro) passaria a ser apenas origem de migração | Alta | Alto |
| RB5 | Desvio arquitetural: o banco do App 3 não deve ser o banco central — ele é um **produtor**, não um **agregador** | Alta | Alto |

#### Impacto nos apps existentes

- **App 3 (Controle Financeiro — Next.js):** Compartilhamento de banco com o App 4 cria acoplamento indesejado.
  Mudanças de schema do App 4 podem afetar o App 3 e vice-versa.
- **App 1 (Dashboard Financeiro):** Perde seu banco natural — precisaria de uma terceira decisão futura.
- **App 4 (Streamlit):** Funciona, mas carrega dívida técnica por estar em banco errado por design.

#### Complexidade de migração

**Alta.** Requer:
1. Adicionar tabelas de investimentos em schema de controle (desvio arquitetural)
2. Migrar dados do Dashboard Financeiro para o Controle Financeiro (sentido contrário ao natural)
3. Resolver conflitos de naming e FK entre os dois domínios
4. Aceitar que App 3 e App 4 dividem banco — risco de RLS conflitante

---

## Comparação Resumida

| Critério | Opção A (Dashboard Financeiro) | Opção B (Controle Financeiro) |
|----------|:---:|:---:|
| Alinhamento arquitetural | ✅ Alto — banco do agregador | ⚠️ Baixo — banco do produtor |
| Schema completo (10 tabelas) | ✅ Já planejado | ❌ Faltam 4 tabelas de investimento |
| Risco de perda de dados | 🟡 Baixo | 🔴 Médio (migração no sentido errado) |
| Impacto no App 3 | ✅ Nenhum | 🔴 Alto — App 3 divide banco |
| Impacto no App 1 futuro | ✅ Positivo — banco pronto para App 1 | 🔴 Negativo — App 1 fica sem banco |
| Complexidade de migração | 🟡 Baixa | 🔴 Alta |
| Facilidade de manutenção futura | ✅ Alta | 🔴 Baixa — mistura de domínios |
| Dívida técnica | Nenhuma | Alta |

---

## Recomendação: Opção A

**Usar o projeto "Dashboard Financeiro" como banco unificado.**

**Justificativa principal:**

1. **Alinhamento de propósito:** App 4 e App 1 são ambos agregadores de visão financeira.
   O banco do App 1 foi projetado exatamente para essa função.

2. **Schema por design:** A modelagem em `modelagem_inicial.md` já inclui todas as 10 tabelas
   necessárias. Não é necessário "forçar" tabelas de investimento em um banco de controle.

3. **Caminho de upgrade natural:** Quando o App 1 (Next.js) for construído, ele usa o mesmo banco —
   sem custo adicional de migração, sem terceiro projeto Supabase.

4. **Isolamento do App 3:** O projeto Controle Financeiro pode continuar existindo como banco
   do App 3 sem interferência, ou ser reaproveitado como ambiente de teste/backup.

5. **Conformidade com CLAUDE_INSTRUCTIONS.md:** A decisão respeita a regra
   "unificação deve acontecer por módulos, não de uma vez só" e
   "nenhuma alteração sem identificar qual app será impactado".

---

## Plano de Execução em Fases

> **Princípio:** nenhuma fase avança sem a anterior estar concluída e validada.
> **Critério de parada:** qualquer erro inesperado interrompe o plano.

---

### Fase P0 — Backup (pré-requisito absoluto)

**Objetivo:** garantir que nenhum dado seja perdido antes de qualquer alteração.

| Ação | Como | Onde guardar |
|------|------|-------------|
| Exportar dump completo do projeto Dashboard Financeiro | Supabase Dashboard → Settings → Database → Backups → Download | `backups/dump_dashboard_financeiro_YYYYMMDD.sql` |
| Exportar dump completo do projeto Controle Financeiro | Idem | `backups/dump_controle_financeiro_YYYYMMDD.sql` |
| Verificar integridade dos dumps | Abrir arquivo, confirmar que não está vazio ou corrompido | Local |
| Registrar versão atual do schema de cada projeto | Listar tabelas e colunas antes de qualquer mudança | `docs/schema_dashboard_financeiro_antes.txt` |

**Critério de conclusão:** ambos os dumps existem, têm tamanho > 0 e foram abertos com sucesso.

**🚫 Não avançar sem este passo concluído.**

---

### Fase P1 — Auditoria dos Schemas Existentes

**Objetivo:** entender exatamente o que existe em cada projeto antes de qualquer mudança.

SQL para executar no SQL Editor de cada projeto (somente leitura):

```sql
-- Lista todas as tabelas e colunas do projeto
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
```

```sql
-- Lista todas as tabelas com contagem de registros
SELECT
    relname AS tabela,
    n_live_tup AS registros_estimados
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
```

```sql
-- Lista todas as policies RLS
SELECT
    tablename,
    policyname,
    cmd,
    qual
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename;
```

**Documentar:**
- Quais tabelas existem em cada projeto
- Quais colunas cada tabela tem
- Quais tabelas têm dados reais
- Quais RLS policies estão ativas
- Divergências em relação à `modelagem_inicial.md`

---

### Fase P2 — Criação do Schema Unificado (sem apagar nada)

**Objetivo:** criar as tabelas do schema unificado no projeto Dashboard Financeiro,
**sem remover nenhuma tabela existente**.

Regra estrita:
- Usar `CREATE TABLE IF NOT EXISTS` — nunca `DROP TABLE` antes
- Usar `ADD COLUMN IF NOT EXISTS` para colunas novas em tabelas existentes
- Tabelas antigas permanecem intactas até validação completa da Fase P4

Sequência de criação (respeita dependências FK):
```
1. usuarios          ← base de tudo
2. contas            ← depende de usuarios
3. categorias        ← depende de usuarios (self-referencing pai_id)
4. transacoes        ← depende de usuarios, contas, categorias
5. orcamentos        ← depende de usuarios, categorias
6. metas             ← depende de usuarios
7. ativos            ← independente (dados de mercado)
8. operacoes         ← depende de usuarios, ativos
9. proventos         ← depende de usuarios, ativos
10. cotacoes         ← depende de ativos
```

O DDL já está em `etl/schema_setup.py` do App 4 — pode ser executado via
"Criar tabelas" na aba Configurações do App 4 após configurar `DATABASE_URL`.

**Verificação pós-criação:**
```sql
-- Confirmar que todas as 10 tabelas existem
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'usuarios','contas','categorias','transacoes',
    'orcamentos','metas','ativos','operacoes','proventos','cotacoes'
  )
ORDER BY table_name;
-- Resultado esperado: 10 linhas
```

---

### Fase P3 — Migração de Dados do Controle Financeiro

**Objetivo:** copiar os dados existentes no projeto Controle Financeiro para o
banco unificado (Dashboard Financeiro), sem apagar a origem.

Estratégia: **INSERT ... ON CONFLICT DO NOTHING** — segura e idempotente.

Ferramentas disponíveis:
- `etl/importacao.py` → `ImportadorPostgres` com `importar_tabela_generica()`
- `SOURCE_DB_CONTROLE` no `.env` aponta para o Controle Financeiro como origem

**Ordem de migração:**
1. `usuarios` (ou criar usuário manualmente no banco destino)
2. `contas`
3. `categorias`
4. `transacoes`
5. `orcamentos`
6. `metas`

**Regras de migração:**
- Sempre com `dry_run=True` na primeira execução
- Confirmar contagens antes e depois de cada tabela
- Registrar resultado de cada etapa em `docs/log_migracao_YYYYMMDD.md`
- Nunca executar na origem — apenas no destino

**Verificação pós-migração:**
```sql
-- Comparar contagens origem × destino por tabela
SELECT 'transacoes' AS tabela, COUNT(*) AS total FROM transacoes
UNION ALL
SELECT 'orcamentos', COUNT(*) FROM orcamentos
UNION ALL
SELECT 'metas', COUNT(*) FROM metas;
-- Comparar com os mesmos números da fase P1
```

---

### Fase P4 — Migração de Dados de Investimentos (Dashboard-Investimentos SQLite)

**Objetivo:** importar operações, proventos e ativos do banco SQLite do
Dashboard-Investimentos (`investment_dashboard.db`).

Ferramenta: `ImportadorPostgres` com `SOURCE_DB_APP2="sqlite:///..."`.

Detalhes documentados em `docs/auditoria_dados_investimentos.md` (seção 9).

Mapeamento:
- `transactions` (SQLite) → `operacoes` (PostgreSQL)
- `incomes` (SQLite) → `proventos` (PostgreSQL)
- `assets` (SQLite) → `ativos` (PostgreSQL)

---

### Fase P5 — Validação

**Objetivo:** garantir que os dados migrados estão corretos antes de apontar o App 4.

Checklist de validação:

```
[ ] Contagem de registros bate entre origem e destino
[ ] Todas as 10 tabelas existem no banco unificado
[ ] Pelo menos 1 usuário existe em `usuarios`
[ ] OWNER_USER_ID configurado no .env do App 4
[ ] App 4 com MOCK_MODE=false consegue carregar dados
[ ] Dashboard Geral exibe valores reais (não zeros)
[ ] Nenhuma query retorna erro de FK violada
[ ] RLS não bloqueia as queries do app4_reader
[ ] Configurações → Banco de Dados mostra todos os badges verdes
```

---

### Fase P6 — Troca Gradual do App 4 para o Banco Unificado

**Objetivo:** ativar o banco real no App 4 em etapas, com rollback disponível.

| Etapa | Ação | Rollback se falhar |
|-------|------|-------------------|
| P6.1 | Configurar `DATABASE_URL` no `.env` apontando para Dashboard Financeiro | Reverter para `MOCK_MODE=true` |
| P6.2 | Setar `MOCK_MODE=false` | Voltar para `MOCK_MODE=true` |
| P6.3 | Testar Dashboard Geral com dados reais | `MOCK_MODE=true` |
| P6.4 | Implementar `_visao_geral_real()` em `core/financeiro.py` | Manter mock |
| P6.5 | Habilitar demais módulos com dados reais (Fase 5, 6...) | Por módulo |

---

### Fase P7 — Desativação ou Reaproveitamento do Controle Financeiro

**Objetivo:** decidir o destino do projeto Controle Financeiro após migração validada.

**Opção P7-A: Reaproveitamento como ambiente de teste/staging**
- Renomear para `finapp-staging` ou `finapp-dev`
- Usar para testar schemas antes de aplicar em produção
- Manter dados reais para comparação com destino
- **Recomendada:** menor risco, sem perda de dados

**Opção P7-B: Uso como banco do App 3 (Controle Financeiro — Next.js)**
- Quando o App 3 for construído, este projeto fica como banco dedicado
- Schema será expandido para o domínio do App 3
- **Recomendada se App 3 for construído antes do App 1**

**Opção P7-C: Manter como backup ativo**
- Manter dados do Controle Financeiro intactos como ponto de recuperação
- Fazer sync periódico manual
- **Recomendada como complemento à P7-A ou P7-B**

**🚫 Não executar DROP DATABASE ou DELETE geral — apenas rename ou arquivamento.**

---

## Variáveis de Ambiente Propostas

> Regra: nenhum valor de credencial neste documento. Apenas nomes de variáveis.

### Arquivo `.env` do Dashboard-Financeiro-Unificado

```ini
# ── Banco unificado (Dashboard Financeiro → banco central do App 4) ───────────
# Connection string do pooler Supabase (Transaction Mode, porta 6543).
# Formato: postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres
SUPABASE_UNIFICADO_URL=""

# Chave anon (pública) — necessária para algumas integrações Supabase futuras.
# Não usada nas queries SQLAlchemy diretas.
SUPABASE_UNIFICADO_ANON_KEY=""

# Chave service_role — SOMENTE para uso local (nunca no Streamlit Cloud).
# Necessária para operações de admin (criar tabelas, seed inicial).
# Nunca commitar. Nunca expor no frontend.
SUPABASE_UNIFICADO_SERVICE_ROLE_KEY=""  # apenas local, nunca em produção

# ── Banco de origem (Controle Financeiro → fonte de migração) ─────────────────
# Connection string do pooler do projeto Controle Financeiro.
# Usado apenas para leitura durante a migração de dados históricos.
# Nunca usado para gravação.
SUPABASE_ORIGEM_CONTROLE_URL=""

# Chave anon do projeto Controle Financeiro.
# Usada opcionalmente para listar tabelas via API REST do Supabase.
SUPABASE_ORIGEM_CONTROLE_ANON_KEY=""

# ── Compatibilidade com variáveis existentes ──────────────────────────────────
# DATABASE_URL e SUPABASE_DB_URL continuam funcionando para retrocompatibilidade.
# Preferir SUPABASE_UNIFICADO_URL quando possível.
DATABASE_URL=""           # alias para SUPABASE_UNIFICADO_URL (pooler port 6543)
SUPABASE_DB_URL=""        # alias alternativo (mesma string)
```

### Prioridade de leitura em `core/config.py`

```python
# Ordem de preferência: SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL
@property
def db_url(self) -> str:
    return (
        self.SUPABASE_UNIFICADO_URL
        or self.DATABASE_URL
        or self.SUPABASE_DB_URL
        or ""
    )
```

---

## Impacto no Schema Existente do App 4

As tabelas criadas por `etl/schema_setup.py` são compatíveis com o schema
definido em `modelagem_inicial.md`. Diferenças menores (nomes de colunas,
tipos de dados) serão resolvidas nas Fases P1/P2 sem quebrar nada.

Tabela de compatibilidade:

| Tabela App 4 (schema_setup) | Tabela Modelagem (modelagem_inicial) | Compatível |
|-----------------------------|--------------------------------------|:---------:|
| `usuarios` | `usuarios` | ✅ |
| `contas` | `contas` | ✅ |
| `categorias` | `categorias` | ✅ |
| `transacoes` | `transacoes` | ✅ |
| `orcamentos` | `orcamentos` | ✅ |
| `metas` | `metas` | ✅ |
| `ativos` | `ativos` | ✅ |
| `operacoes` | `operacoes` | ✅ |
| `proventos` | `proventos` | ✅ |
| `cotacoes` | `cotacoes` | ✅ |

---

## Checklist Geral de Segurança

```
[ ] SERVICE_ROLE_KEY nunca no Streamlit Cloud (só local)
[ ] ANON_KEY nunca em código Python (só no .env)
[ ] app4_reader role com SELECT apenas (sem BYPASSRLS)
[ ] Todas as queries incluem WHERE usuario_id = :owner_id
[ ] Backup verificado antes de qualquer operação no banco
[ ] Nenhum DROP/TRUNCATE/DELETE sem autorização manual
[ ] .env está no .gitignore
[ ] Nenhuma credencial em docs, commits ou logs
```

---

## Próximos Passos Imediatos

1. Executar Fase P0 (backup) — **não pular**
2. Executar auditoria P1 em ambos os projetos (SQL de leitura)
3. Configurar `.env` com `SUPABASE_UNIFICADO_URL` e `OWNER_USER_ID`
4. Executar "Criar tabelas" nas Configurações do App 4 (Fase P2)
5. Testar conexão com `MOCK_MODE=false`
6. Implementar `_visao_geral_real()` em `core/financeiro.py`

---

## Links Internos (Obsidian)

- [[MAPA_SUPABASE]] — mapa dos projetos Supabase
- [[modelagem_inicial]] — DDL completo das 10 tabelas
- [[STATUS_DOS_APPS]] — status atual de cada app
- [[04_App_Dashboard_Financeiro_Unificado/pendencias_e_melhorias]] — riscos abertos
- [[04_App_Dashboard_Financeiro_Unificado/proximos_passos]] — plano de ação

## Links no Repositório

- `docs/status_fase_4.md` — status atualizado da Fase 4
- `docs/auditoria_dados_investimentos.md` — mapeamento do SQLite de investimentos
- `docs/fase_4_supabase_auditoria.md` — auditoria de integração Supabase
- `etl/schema_setup.py` — DDL das 10 tabelas
- `etl/importacao.py` — ImportadorPostgres para migração
- `pages/configuracoes.py` — interface de setup e importação
