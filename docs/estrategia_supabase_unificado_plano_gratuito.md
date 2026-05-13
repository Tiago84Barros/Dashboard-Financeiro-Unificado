# Estratégia Supabase Unificado — Plano Gratuito

> Documento: `docs/estrategia_supabase_unificado_plano_gratuito.md`
> Criado em: 2026-05-13 | Atualizado em: 2026-05-13 (Fase 4.0 — decisão finalizada)
> Status: **Decisão tomada e documentada**
> Fases detalhadas: `docs/banco_unificado_fases.md`
> Regras de segurança: `docs/banco_unificado_regras_de_seguranca.md`

---

## REGRA DE SEGURANÇA ABSOLUTA

> **Nenhum script, comando ou instrução neste documento pode conter ou executar:**
> - `DROP TABLE` · `DROP SCHEMA` · `TRUNCATE` · `DELETE` sem WHERE explícito e autorização manual
> - `ALTER TABLE ... DROP COLUMN`
> - Qualquer operação destrutiva ou irreversível
>
> **Toda operação destrutiva exige:**
> 1. Backup confirmado e verificado
> 2. Aprovação manual explícita e escrita do proprietário
> 3. Execução em ambiente de teste primeiro
> 4. Registro no log de mudanças

---

## Decisão Arquitetural

**O projeto Supabase do Dashboard Financeiro é o banco unificado do App 4.**

Esta decisão foi tomada em 2026-05-13 e é definitiva para o ciclo atual de desenvolvimento.
Não haverá criação de terceiro projeto Supabase (plano gratuito = máximo 2 projetos).

| Aspecto | Decisão |
|---------|---------|
| Banco unificado | Projeto Supabase **Dashboard Financeiro** |
| Fonte de migração | Projeto Supabase **Controle Financeiro** (temporário) |
| Dados de investimentos | SQLite local do Dashboard-Investimentos (migração futura) |
| Terceiro projeto Supabase | ❌ Não criar |
| Plano pago | ❌ Não necessário |

---

## Por Que o Projeto Dashboard Financeiro

### 1. Alinhamento de propósito

O App 4 (Dashboard-Financeiro-Unificado) é, por definição, um **agregador de visão financeira**.
O projeto Supabase "Dashboard Financeiro" foi arquitetado exatamente para este papel —
consolidar dados de múltiplas fontes em uma visão única.

Usar o banco do agregador como banco unificado é a escolha arquiteturalmente correta.

### 2. Schema por design

A modelagem em `modelagem_inicial.md` (Obsidian) já inclui as 10 tabelas necessárias:

```
usuarios → contas → categorias → transacoes → orcamentos → metas
ativos → operacoes → proventos → cotacoes
```

Todas as 10 tabelas fazem sentido neste projeto. Nenhuma é um "remendo".

### 3. Caminho de upgrade natural

Quando o App 1 (Dashboard Financeiro — Next.js) for construído no futuro,
ele usará este mesmo banco sem custo de migração adicional.
O investimento feito agora beneficia dois apps.

### 4. Isolamento do App 3

O projeto Controle Financeiro continua independente.
O App 3 (Controle Financeiro — Next.js), quando construído, poderá usar
seu próprio projeto Supabase sem interferência do App 4.

### 5. Conformidade com CLAUDE_INSTRUCTIONS.md

> "A unificação deve acontecer por módulos, não de uma vez só."
> "Nenhuma alteração técnica sem identificar qual app será impactado."

Esta decisão respeita ambas as regras: o banco unificado cresce por fases,
e o impacto de cada fase é documentado antes de qualquer execução.

---

## Papel do Projeto Controle Financeiro

### Fase atual: origem temporária de migração

O projeto Supabase "Controle Financeiro" contém dados reais de:
- Transações financeiras
- Categorias de gastos
- Orçamentos mensais
- Metas financeiras

Esses dados serão **copiados** (não movidos) para o banco unificado durante a Fase 4.7.
A origem permanece intacta durante todo o processo.

### Papel futuro: banco dedicado do App 3

Após a migração, o projeto Controle Financeiro terá três opções:

| Opção | Descrição | Recomendação |
|-------|-----------|:------------:|
| **A** | Banco dedicado do App 3 (Controle Financeiro — Next.js) | ✅ Preferida |
| **B** | Ambiente de staging/teste do ecossistema | ✅ Complementar |
| **C** | Backup ativo com dados históricos preservados | ✅ Complementar |

**O projeto Controle Financeiro não será apagado, renomeado ou destruído.**
Qualquer decisão sobre seu uso futuro ocorre na Fase 4.9 ou posterior.

---

## Papel Futuro dos Dados de Investimentos

O Dashboard-Investimentos usa atualmente:
- **SQLite local** (`investment_dashboard.db`) como banco principal
- **Yahoo Finance, BCB, AwesomeAPI** como fontes de cotações
- **Excel (B3, XP) e PDF (Nomad)** como fontes de importação

### Estratégia de integração

Os dados do SQLite serão migrados para as tabelas de investimentos do banco unificado:

| Tabela SQLite (origem) | Tabela PostgreSQL (destino) | Fase |
|------------------------|----------------------------|------|
| `transactions` | `operacoes` | 4.7 |
| `incomes` | `proventos` | 4.7 |
| `assets` | `ativos` | 4.7 |
| `xp_positions` | `cotacoes` (snapshot) | 4.7 |

A lógica de importação (parsers Excel/PDF) permanece no Dashboard-Investimentos
e será reaproveitada no App 4 via `etl/importacao.py`.

Detalhes completos em `docs/auditoria_dados_investimentos.md`.

---

## Benefícios desta Arquitetura

| Benefício | Impacto |
|-----------|---------|
| Zero custo adicional | Plano gratuito preservado |
| Schema unificado correto por design | Sem dívida técnica |
| App 1 futuro já tem banco pronto | Sem migração adicional no futuro |
| App 3 mantém seu projeto independente | Sem acoplamento entre apps |
| SQLite de investimentos migra naturalmente | SQLAlchemy suporta sqlite nativo |
| MOCK_MODE preservado durante toda a migração | Zero regressão durante a transição |
| Rollback disponível em toda fase | Segurança de execução |

---

## Riscos e Mitigações

| ID | Risco | Probabilidade | Severidade | Mitigação |
|----|-------|:---:|:---:|-----------|
| R1 | Schema atual do Dashboard Financeiro diverge da modelagem | Média | Médio | Auditoria 4.1 mapeia divergências antes de qualquer DDL |
| R2 | Dados do Controle Financeiro têm FK não mapeadas | Baixa | Alto | Dry-run na Fase 4.7 detecta antes de gravar |
| R3 | SQLite de investimentos tem colunas sem equivalente | Média | Baixo | Mapeamento documentado em `auditoria_dados_investimentos.md` |
| R4 | Posições Nomad em USD precisam de conversão PTAX | Alta | Médio | `nomad_import_service_v2.py` já faz a conversão — reutilizar |
| R5 | Erro de configuração afeta o App 1 futuro | Baixa | Médio | Schema em schema separado (`app4`) ou prefixo de tabela |
| R6 | Backup não feito antes de DDL | Baixa | Alto | Regra 4 em `banco_unificado_regras_de_seguranca.md` — obrigatório |

---

## Fases de Execução (resumo)

> Detalhamento completo em `docs/banco_unificado_fases.md`

| Fase | Nome | Executor |
|------|------|:--------:|
| **4.0** | Estratégia e documentação | ✅ Concluída |
| **4.1** | Auditoria dos bancos atuais | Humano (SQL read-only) |
| **4.2** | Modelo canônico | Claude + aprovação humana |
| **4.3** | Scripts SQL não destrutivos | Claude |
| **4.4** | Revisão humana dos scripts | **Humano (obrigatório)** |
| **4.5** | Aplicação manual no Supabase | **Humano (SQL Editor)** |
| **4.6** | Scripts de migração ETL | Claude |
| **4.7** | Migração controlada | Humano + app |
| **4.8** | Validação dos dados | Humano + app |
| **4.9** | Conexão do app ao banco | Claude + humano |

**Princípio:** nenhuma fase avança sem a anterior estar concluída e validada.
**Critério de parada:** qualquer erro inesperado interrompe o plano.

---

## Variáveis de Ambiente (nomes propostos)

> Regra: este documento contém apenas nomes de variáveis — nunca valores.

```ini
# ── Banco unificado (Dashboard Financeiro → banco central do App 4) ──────────
# Connection string do pooler Supabase (Transaction Mode, porta 6543)
# Formato: postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres
SUPABASE_UNIFICADO_URL=""

# Chave anon (pública) — para integrações REST futuras com o Supabase
SUPABASE_UNIFICADO_ANON_KEY=""

# Chave service_role — SOMENTE local, nunca no Streamlit Cloud, nunca commitada
SUPABASE_UNIFICADO_SERVICE_ROLE_KEY=""

# ── Banco de origem (Controle Financeiro → leitura durante migração) ─────────
# Connection string somente-leitura do projeto Controle Financeiro
SUPABASE_ORIGEM_CONTROLE_URL=""

# Chave anon do projeto Controle Financeiro (opcional, para REST)
SUPABASE_ORIGEM_CONTROLE_ANON_KEY=""

# ── Retrocompatibilidade ──────────────────────────────────────────────────────
# DATABASE_URL continua funcionando como alias de SUPABASE_UNIFICADO_URL
DATABASE_URL=""
```

### Prioridade de leitura em `core/config.py` (a implementar na Fase 4.9)

```python
@property
def db_url(self) -> str:
    """Retorna URL do banco unificado com fallback para variáveis legadas."""
    return (
        self.SUPABASE_UNIFICADO_URL
        or self.DATABASE_URL
        or self.SUPABASE_DB_URL
        or ""
    )
```

---

## Estrutura Operacional

A pasta `supabase_unificado/` centraliza todos os artefatos de execução:

```
supabase_unificado/
├── README.md          ← guia de uso e avisos
├── schema/            ← scripts DDL versionados (001_*.sql ... 012_*.sql)
├── migrations/        ← scripts ETL de migração (001_*.py ... 004_*.py)
├── backups/           ← dumps de backup (não commitados — .gitignore ativo)
└── validation/        ← logs e relatórios de validação
```

---

## Checklist de Segurança

```
[ ] SERVICE_ROLE_KEY nunca commitada no repositório
[ ] SERVICE_ROLE_KEY nunca em código que roda no Streamlit Cloud
[ ] ANON_KEY nunca commitada com valor real
[ ] .env está no .gitignore
[ ] app4_reader tem apenas SELECT (sem BYPASSRLS)
[ ] Toda query inclui WHERE usuario_id = :owner_id
[ ] RLS habilitada nas 8 tabelas de dados pessoais
[ ] Backup verificado antes de qualquer DDL ou DML
[ ] Nenhum DROP/TRUNCATE/DELETE sem autorização escrita
[ ] Scripts SQL numerados e imutáveis após aplicação
[ ] Log de migração salvo em supabase_unificado/validation/
[ ] Somatórios financeiros comparados origem × destino
```

---

## Próximo Passo

**Fase 4.1 — Auditoria dos bancos atuais**

Acessar o SQL Editor dos dois projetos Supabase e executar as 5 queries
de auditoria documentadas em `docs/banco_unificado_fases.md` (seção Fase 4.1).

Salvar os resultados em:
- `supabase_unificado/validation/auditoria_banco_dashboard_financeiro.md`
- `supabase_unificado/validation/auditoria_banco_controle_financeiro.md`

Sem este passo concluído, não é possível avançar para a Fase 4.2.

---

## Links de Referência

### No repositório
- `docs/banco_unificado_fases.md` — plano completo das 10 fases
- `docs/banco_unificado_regras_de_seguranca.md` — regras de segurança
- `docs/auditoria_dados_investimentos.md` — mapeamento do SQLite de investimentos
- `etl/schema_setup.py` — DDL atual das 10 tabelas
- `supabase_unificado/` — pasta operacional

### No Obsidian (ProjetoIA)
- `MAPA_SUPABASE.md` — mapa dos projetos Supabase
- `modelagem_inicial.md` — DDL completo planejado
- `STATUS_DOS_APPS.md` — status de todos os apps
- `MAPA_GERAL_DOS_APPS.md` — papel de cada app no ecossistema
