# Fase 4 — Auditoria de Integração Supabase

> Gerado em: 2026-05-13
> Fase: 4 — Pré-implementação (auditoria apenas — nenhum código alterado)
> Referência do vault: `MAPA_SUPABASE.md`, `modelagem_inicial.md`, `regras_principais.md`, `kpis_principais.md`

---

## 1. Estado Atual da Integração Supabase

### Diagnóstico rápido

| Item verificado                         | Status         | Detalhe                                                      |
|-----------------------------------------|:--------------:|--------------------------------------------------------------|
| `supabase-py` instalado                 | ❌ Ausente     | Não consta em `requirements.txt` nem no ambiente Python      |
| Supabase JS Client                      | ❌ N/A         | Projeto Python — não se aplica                               |
| `.env` configurado                      | ❌ Ausente     | Apenas `.env.example` existe; `.env` não foi criado          |
| `.streamlit/secrets.toml`               | ❌ Ausente     | Não criado (correto — não deve ir para o repo)               |
| `SUPABASE_DB_URL` no código             | ⚠️ Placeholder | Variável declarada em `core/config.py`, sem valor atribuído  |
| `DATABASE_URL` no código                | ⚠️ Placeholder | Idem — declarada mas sem valor                               |
| Queries SQL reais implementadas         | ❌ Nenhuma     | `_visao_geral_real()` lança `NotImplementedError`            |
| RLS configurado para App 4              | ❌ Pendente    | Nenhuma policy criada para acesso via SQLAlchemy             |
| Autenticação de usuário                 | ❌ Ausente     | App não tem login — acessa dados sem `usuario_id` do JWT     |
| `service_role_key` no código            | ✅ Correto     | Nunca referenciada; comentário proibitivo em `database.py`   |
| Dados sensíveis no frontend             | ✅ Correto     | `get_db_status()` não expõe URL nem credenciais              |
| `.env` no `.gitignore`                  | ✅ Correto     | Linha `.env` e `.streamlit/secrets.toml` no `.gitignore`     |

### Conclusão do diagnóstico

O projeto **não usa Supabase ativamente**. A integração está preparada em nível de
infraestrutura (SQLAlchemy engine em `core/database.py`, variáveis em `core/config.py`)
mas **nenhuma query real foi implementada**. Todo dado exibido vem de `core/mock_data.py`.

O app opera 100% em `MOCK_MODE=true` e é seguro neste estado.

---

## 2. Arquivos com Referências ao Supabase

| Arquivo               | Tipo de referência                                              | Risco |
|-----------------------|-----------------------------------------------------------------|:-----:|
| `.env.example`        | Nomes de variáveis `DATABASE_URL` e `SUPABASE_DB_URL` (sem valor) | ✅ Zero |
| `core/config.py`      | `os.getenv("SUPABASE_DB_URL", "")` — lê a URL como string vazia | ✅ Zero |
| `core/database.py`    | Comentário proibitivo contra `service_role_key`; engine usa `db_url` | ✅ Zero |
| Arquivos `docs/*.md`  | Menções documentais — nenhuma credencial                        | ✅ Zero |

**Nenhum arquivo contém credenciais, chaves ou URLs reais.**

---

## 3. Tabelas Necessárias (mapeadas do mock para o banco real)

Com base em `core/mock_data.py` (Fase 3) cruzado com `modelagem_inicial.md` do vault:

### 3.1 — Módulo Dashboard Geral (Fase 4 — próxima)

| Bloco mock               | Tabelas reais necessárias                        | Dependência de dados externos |
|--------------------------|--------------------------------------------------|-------------------------------|
| `PATRIMONIO`             | `contas`, `operacoes`, `cotacoes`               | Cotações: yfinance / API       |
| `FLUXO_MES`              | `transacoes`, `categorias`                      | —                              |
| `HISTORICO_MENSAL`       | `transacoes` (GROUP BY mês)                     | —                              |
| `CATEGORIAS_DESPESA`     | `transacoes`, `orcamentos`, `categorias`        | —                              |
| `PORTFOLIO`              | `operacoes`, `proventos`, `cotacoes`, `ativos`  | Cotações: yfinance / API       |
| `CLASSES_ATIVO`          | `ativos`, `operacoes`, `cotacoes`               | Cotações: yfinance / API       |
| `ALERTAS_DASHBOARD`      | Calculado sobre todas as tabelas acima          | —                              |
| `PROXIMOS_PASSOS`        | Regras de negócio aplicadas aos dados acima     | —                              |

### 3.2 — Módulos futuros (Fases 5–7)

| Módulo                    | Tabelas                                             | Fase |
|---------------------------|-----------------------------------------------------|:----:|
| Investimentos / Carteira  | `ativos`, `operacoes`, `cotacoes`, `proventos`     | 5    |
| Proventos                 | `proventos`, `ativos`                               | 5    |
| Controle Financeiro       | `transacoes`, `categorias`, `contas`               | 6    |
| Metas                     | `metas`                                             | 7    |
| Alertas inteligentes      | Todas + `orcamentos`                               | 8    |

---

## 4. Mapa Completo: Indicador → Tabela → Campos → Cálculo → Fallback Mock

### 4.1 Patrimônio e Saldos

| Indicador na tela         | Tabela(s)               | Campos necessários                                                                 | Regra de cálculo (ref. vault)                                    | Fallback mock              |
|---------------------------|-------------------------|------------------------------------------------------------------------------------|------------------------------------------------------------------|----------------------------|
| Patrimônio Total          | `contas`, `operacoes`, `cotacoes` | `contas.saldo_inicial`, `transacoes.valor`, `operacoes.quantidade`, `cotacoes.fechamento` | `saldo_bancario + Σ(qtd × preco_atual)` — RD-002              | `PATRIMONIO["total"]` = R$ 87.450 |
| Patrimônio Investido      | `operacoes`, `cotacoes`, `ativos` | `operacoes.quantidade`, `cotacoes.fechamento`, `ativos.classe`                    | `Σ(quantidade × fechamento)` por ativo                          | `PATRIMONIO["investido"]` = R$ 75.150 |
| Saldo Disponível          | `contas`, `transacoes`  | `contas.saldo_inicial`, `transacoes.valor`, `transacoes.status`                    | `saldo_inicial + Σ(transações liquidadas)` — RC-001             | `PATRIMONIO["saldo_bancario"]` = R$ 12.300 |
| Delta Patrimônio (%)      | `contas`, `operacoes`   | snapshot do mês atual vs. mês anterior                                             | `(total_atual - total_anterior) / total_anterior × 100`         | `PATRIMONIO["delta_mes_pct"]` = 5,2% |
| Score de Saúde (0–100)    | Calculado               | taxa_poupança, meses_reserva, categorias_no_limite, rentabilidade_positiva         | `calcular_saude_score()` em `core/financeiro.py`                | `PATRIMONIO["saude_score"]` = 78 |

### 4.2 Fluxo de Caixa do Mês

| Indicador na tela         | Tabela(s)               | Campos necessários                                                                 | Regra de cálculo (ref. vault)                                    | Fallback mock              |
|---------------------------|-------------------------|------------------------------------------------------------------------------------|------------------------------------------------------------------|----------------------------|
| Receitas do Mês           | `transacoes`            | `valor`, `tipo = 'receita'`, `status = 'liquidado'`, `data_competencia`           | `Σ(valor) WHERE tipo='receita' AND mes=corrente` — RD-001        | `FLUXO_MES["receitas"]` = R$ 8.500 |
| Despesas do Mês           | `transacoes`            | `valor`, `tipo = 'despesa'`, `status = 'liquidado'`, `data_competencia`           | `Σ(ABS(valor)) WHERE tipo='despesa' AND mes=corrente`           | `FLUXO_MES["despesas"]` = R$ 4.200 |
| Economia do Mês           | Calculado               | receitas - despesas                                                                | `receitas - despesas` — RD-001                                  | `FLUXO_MES["economia"]` = R$ 4.300 |
| Taxa de Poupança (%)      | Calculado               | receitas, despesas                                                                 | `(receitas - despesas) / receitas × 100` — KPI-F02 / RD-004    | `FLUXO_MES["taxa_poupanca_pct"]` = 50,6% |
| Meses de Reserva          | `contas`, `transacoes`  | saldo_bancario, média de despesas últimos 6 meses                                  | `saldo_bancario / media_despesas_6m` — RC-005                   | `FLUXO_MES["meses_reserva"]` = 2,9× |
| Maior Categoria de Gasto  | `transacoes`, `categorias` | `categoria_id`, `valor`, `data_competencia`                                     | `GROUP BY categoria ORDER BY Σ(despesa) DESC LIMIT 1`           | `FLUXO_MES["maior_categoria"]` = "Moradia" |
| Categoria em Alerta       | `transacoes`, `orcamentos`, `categorias` | `valor`, `valor_limite`, `mes_ano`                                  | `(Σ_gasto / limite) × 100 ≥ 80%` — RC-003                      | `FLUXO_MES["categoria_alerta"]` = "Lazer" |

### 4.3 Histórico Mensal (últimos 6 meses — gráfico de barras)

| Indicador na tela         | Tabela(s)               | Campos necessários                                                                 | Regra de cálculo                                                | Fallback mock              |
|---------------------------|-------------------------|------------------------------------------------------------------------------------|-----------------------------------------------------------------|----------------------------|
| Receitas por mês          | `transacoes`            | `valor`, `tipo`, `status`, `data_competencia`                                     | `GROUP BY DATE_TRUNC('month', data_competencia)` — últimos 6m  | `HISTORICO_MENSAL[n]["receitas"]` |
| Despesas por mês          | `transacoes`            | idem                                                                               | idem, tipo='despesa'                                            | `HISTORICO_MENSAL[n]["despesas"]` |

### 4.4 Orçamento por Categoria (barras de progresso)

| Indicador na tela         | Tabela(s)               | Campos necessários                                                                 | Regra de cálculo                                                | Fallback mock              |
|---------------------------|-------------------------|------------------------------------------------------------------------------------|-----------------------------------------------------------------|----------------------------|
| Gasto real por categoria  | `transacoes`, `categorias` | `categoria_id`, `valor`, `data_competencia`                                     | `Σ(despesas) por categoria no mês corrente`                     | `CATEGORIAS_DESPESA[n]["gasto"]` |
| Limite orçado             | `orcamentos`            | `valor_limite`, `categoria_id`, `mes_ano`                                         | leitura direta                                                  | `CATEGORIAS_DESPESA[n]["orcamento"]` |
| % usado                   | Calculado               | gasto / limite                                                                     | `gasto / valor_limite × 100` — RC-003                          | `CATEGORIAS_DESPESA[n]["pct_usado"]` |

### 4.5 Portfólio de Investimentos

| Indicador na tela         | Tabela(s)               | Campos necessários                                                                 | Regra de cálculo                                                | Fallback mock              |
|---------------------------|-------------------------|------------------------------------------------------------------------------------|-----------------------------------------------------------------|----------------------------|
| Rentabilidade Mês (%)     | `operacoes`, `cotacoes` | `quantidade`, `preco_unitario`, `fechamento` (atual vs. mês anterior)              | TWRR mensal — RI-002                                            | `PORTFOLIO["rentabilidade_mes_pct"]` = 3,2% |
| Rentabilidade Ano (%)     | `operacoes`, `cotacoes` | idem, janela anual                                                                 | TWRR anual — RI-002                                             | `PORTFOLIO["rentabilidade_ano_pct"]` = 12,4% |
| Dividendos do Mês (R$)    | `proventos`             | `valor_total`, `data_pagamento`                                                    | `Σ(valor_total) WHERE mes=corrente` — RI-004                   | `PORTFOLIO["dividendos_mes"]` = R$ 420 |
| Nº de Ativos              | `operacoes`             | `ativo_id` DISTINCT, `quantidade > 0`                                              | contagem de posições abertas                                    | `PORTFOLIO["num_ativos"]` = 12 |

### 4.6 Classes de Ativos (donut chart + rentabilidade)

| Indicador na tela         | Tabela(s)               | Campos necessários                                                                 | Regra de cálculo                                                | Fallback mock              |
|---------------------------|-------------------------|------------------------------------------------------------------------------------|-----------------------------------------------------------------|----------------------------|
| Valor por classe (R$)     | `ativos`, `operacoes`, `cotacoes` | `ativos.classe`, `operacoes.quantidade`, `cotacoes.fechamento`          | `Σ(qtd × preco_atual) GROUP BY classe`                         | `CLASSES_ATIVO[n]["valor"]` |
| % da carteira             | Calculado               | valor_classe / total_investido                                                     | `valor_classe / patrimonio_investido × 100`                    | `CLASSES_ATIVO[n]["pct_carteira"]` |
| Rentabilidade % no mês    | `operacoes`, `cotacoes` | fechamento atual vs. fechamento mês anterior por classe                            | `(valor_atual - valor_anterior) / valor_anterior × 100`        | `CLASSES_ATIVO[n]["rentab_mes_pct"]` |

---

## 5. Variáveis de Ambiente Necessárias

### 5.1 — Nomes atualmente usados no projeto (sem valores)

```
# core/config.py — já declaradas
DATABASE_URL          ← URL de conexão PostgreSQL direta (prioridade)
SUPABASE_DB_URL       ← Alternativa/alias para DATABASE_URL via Supabase
APP_ENV               ← "production" em deploy
MOCK_MODE             ← "false" na Fase 4 (mudar de "true")
```

### 5.2 — Variáveis NÃO usadas atualmente (e por quê)

| Variável              | Por que não está no projeto                                                    |
|-----------------------|--------------------------------------------------------------------------------|
| `SUPABASE_URL`        | Seria necessária se usasse `supabase-py` (REST API). App usa SQLAlchemy direto.|
| `SUPABASE_ANON_KEY`   | Idem — para Supabase JS client ou `supabase-py`, não para SQLAlchemy.          |
| `SUPABASE_SERVICE_ROLE_KEY` | **NUNCA deve ser adicionada** — bypassa RLS totalmente.               |

### 5.3 — Variáveis futuras por fase

| Variável          | Fase | Finalidade                                   |
|-------------------|:----:|----------------------------------------------|
| `DATABASE_URL`    | 4    | Conexão ao PostgreSQL (Supabase pool string) |
| `MOCK_MODE=false` | 4    | Ativar queries reais                          |
| `APP_ENV=production` | 4 | Desabilitar mensagens de dev               |
| `OPENAI_API_KEY`  | 8    | Módulo de alertas inteligentes com IA        |

### 5.4 — Forma de configurar no Streamlit Community Cloud

```toml
# .streamlit/secrets.toml  (NÃO commitar — já no .gitignore)
DATABASE_URL = "postgresql://..."
MOCK_MODE = "false"
APP_ENV = "production"
```

Os secrets do Streamlit Community Cloud são expostos como variáveis de ambiente
e são lidos corretamente pelo `os.getenv()` em `core/config.py`. ✅

---

## 6. Avaliação de Riscos de Segurança

### 🔴 Risco ALTO — S01: Conexão direta bypassa RLS

**Descrição:** O App 4 conecta via SQLAlchemy + psycopg2 diretamente ao PostgreSQL do
Supabase usando uma connection string. Isso **bypassa completamente o Row-Level Security**
do Supabase — que só funciona via Supabase Auth JWT (apps Next.js/JS).

**Impacto:** Sem `WHERE usuario_id = ?` nas queries, o app retorna dados de **todos os
usuários** do banco, não apenas do usuário autenticado.

**Estado atual:** Seguro — o app está em MOCK_MODE=true, sem conexão real.

**Mitigação obrigatória antes da Fase 4:**
1. Criar um role PostgreSQL dedicado `app4_reader` com:
   - `GRANT SELECT` apenas nas tabelas necessárias
   - **Sem** `BYPASSRLS`
   - `GRANT` somente ao usuário específico, não ao role `postgres`
2. Filtrar **todas as queries** por `usuario_id` hardcoded (Fase 4) ou por
   `st.session_state` com auth (Fase futura)
3. **Nunca usar** a `DATABASE_URL` do Supabase com role `postgres` ou `service_role`

```sql
-- SQL a executar no Supabase (mostrar antes de executar — não rodar agora)
CREATE ROLE app4_reader WITH LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE postgres TO app4_reader;
GRANT SELECT ON transacoes, contas, categorias, orcamentos,
               ativos, operacoes, proventos, cotacoes, metas
TO app4_reader;
-- NÃO CONCEDER: INSERT, UPDATE, DELETE, BYPASSRLS
```

### 🔴 Risco ALTO — S02: Sem autenticação de usuário

**Descrição:** O app não tem fluxo de login. Não há `usuario_id` do contexto de autenticação.

**Impacto:** As queries precisarão de um `usuario_id` para filtrar dados. Sem auth, o app
é necessariamente single-user (apenas para uso pessoal/local).

**Estado atual:** Aceitável para uso pessoal local. Se publicar no Streamlit Cloud:
os dados ficarão expostos a qualquer pessoa com acesso à URL.

**Mitigação (Fase 4):**
- Hardcodar o `usuario_id` do proprietário nas queries (solução temporária)
- Ou: adicionar senha de acesso via `st.secrets["APP_PASSWORD"]` + `st.text_input("Senha")`
- Auth completo Supabase Auth: aguarda decisão D03 (app local vs. hospedado)

### 🟡 Risco MÉDIO — S03: `OPENAI_API_KEY` em app hospedado

**Descrição:** Se publicado no Streamlit Cloud com `OPENAI_API_KEY` configurada, qualquer
usuário com acesso à URL pode indiretamente consumir a chave via ações no app.

**Estado atual:** Sem impacto — módulo de IA é Fase 8.

**Mitigação:** Rate limit por sessão + autenticação antes do Fase 8.

### 🟡 Risco MÉDIO — S04: Decisão D01 ainda pendente

**Descrição:** Não está definido se o App 4 usa `finapp-prod` (mesmo banco dos Apps 1–3)
ou um banco PostgreSQL separado.

**Impacto:** Se `finapp-prod`: risco de dados produtivos acessados por app experimental.
Se banco separado: dados desatualizados / duplicados.

**Recomendação:** Usar `finapp-dev` (ambiente de desenvolvimento do mesmo Supabase) nas
fases de desenvolvimento; migrar para `finapp-prod` com role de leitura após validação.

### ✅ Correto — Sem service_role_key

O código nunca referencia `service_role_key`. O comentário em `core/database.py`
proíbe explicitamente: *"service_role_key do Supabase jamais deve ser usado aqui."*

### ✅ Correto — Dados sensíveis não expostos no frontend

`core/database.py` → `get_db_status()` retorna apenas `{configurado: bool, conectado: bool,
mock_mode: bool}` — sem expor URL, host, usuário ou senha.

### ✅ Correto — Secrets fora do controle de versão

`.gitignore` já exclui `.env` e `.streamlit/secrets.toml`.

---

## 7. Plano de Implementação da Fase 4

> **Regra:** Não alterar código até este plano ser revisado e aprovado.

### Pré-condições (antes de escrever uma linha de código)

- [ ] **D01 resolvido:** definir se usa `finapp-dev` ou PostgreSQL local
- [ ] **D04 resolvido:** confirmar que schema do banco corresponde ao `modelagem_inicial.md`
- [ ] Criar role `app4_reader` no Supabase com permissões mínimas de leitura
- [ ] Criar `.env` local com `DATABASE_URL` apontando para `finapp-dev`
- [ ] Verificar que o banco tem pelo menos 1 usuário e dados de teste

### Etapa 4.1 — Conexão e health check

1. Configurar `.env` com `DATABASE_URL=` (connection string do `app4_reader`)
2. Testar `test_connection()` em `core/database.py` — deve retornar `True`
3. Verificar exibição em `pages/configuracoes.py` — badge "Banco conectado ✅"

### Etapa 4.2 — Implementar `_visao_geral_real()` em `core/financeiro.py`

Substituir o `raise NotImplementedError` por queries SQLAlchemy reais, **uma a uma**,
mantendo `MOCK_MODE=true` durante o desenvolvimento:

```
Ordem de implementação:
  1. PATRIMONIO    ← saldo_bancario via contas + transacoes
  2. FLUXO_MES     ← receitas/despesas via transacoes (mês corrente)
  3. HISTORICO_MENSAL ← GROUP BY mês, últimos 6 meses
  4. CATEGORIAS_DESPESA ← JOIN transacoes + orcamentos + categorias
  5. PORTFOLIO     ← operacoes + cotacoes (rentabilidade simplificada)
  6. CLASSES_ATIVO ← ativos + operacoes + cotacoes GROUP BY classe
  7. ALERTAS       ← regras calculadas sobre os dados acima
  8. PROXIMOS_PASSOS ← regras de negócio
```

### Etapa 4.3 — Validação dados mock vs. dados reais

Para cada bloco implementado:
1. Testar com `MOCK_MODE=true` → exibe dados mockados ✅
2. Testar com `MOCK_MODE=false` → exibe dados reais do banco
3. Comparar estrutura do dict retornado (mesmas chaves, mesmos tipos)

### Etapa 4.4 — Ativar `MOCK_MODE=false` em produção

1. Configurar `MOCK_MODE=false` em `.streamlit/secrets.toml` (local) e no Streamlit Cloud
2. Remover badge "Modo mock" do cabeçalho do dashboard
3. Testar fluxo completo com dados reais

---

## 8. Queries SQL Previstas (rascunho — não implementar ainda)

Estas queries são para referência de planejamento. O SQL final será revisado antes da implementação.

```sql
-- Q01: Saldo bancário total do usuário
SELECT COALESCE(
    c.saldo_inicial + COALESCE(SUM(t.valor) FILTER (WHERE t.status = 'liquidado'), 0),
    0
) AS saldo_bancario
FROM contas c
LEFT JOIN transacoes t ON t.conta_id = c.id
WHERE c.usuario_id = :usuario_id AND c.ativo = TRUE
GROUP BY c.id, c.saldo_inicial;

-- Q02: Receitas e despesas do mês corrente
SELECT
    SUM(valor) FILTER (WHERE tipo = 'receita') AS receitas,
    ABS(SUM(valor) FILTER (WHERE tipo = 'despesa')) AS despesas
FROM transacoes
WHERE usuario_id = :usuario_id
  AND status = 'liquidado'
  AND DATE_TRUNC('month', data_competencia) = DATE_TRUNC('month', CURRENT_DATE);

-- Q03: Histórico mensal (6 meses)
SELECT
    TO_CHAR(DATE_TRUNC('month', data_competencia), 'Mon') AS mes,
    SUM(valor) FILTER (WHERE tipo = 'receita') AS receitas,
    ABS(SUM(valor) FILTER (WHERE tipo = 'despesa')) AS despesas
FROM transacoes
WHERE usuario_id = :usuario_id
  AND status = 'liquidado'
  AND data_competencia >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '5 months'
GROUP BY 1
ORDER BY DATE_TRUNC('month', data_competencia);

-- Q04: Gastos por categoria vs. orçamento (mês corrente)
SELECT
    cat.nome,
    ABS(SUM(t.valor)) AS gasto,
    o.valor_limite AS orcamento,
    ROUND(ABS(SUM(t.valor)) / o.valor_limite * 100, 1) AS pct_usado
FROM transacoes t
JOIN categorias cat ON cat.id = t.categoria_id
JOIN orcamentos o ON o.categoria_id = t.categoria_id
    AND o.mes_ano = DATE_TRUNC('month', CURRENT_DATE)
    AND o.usuario_id = :usuario_id
WHERE t.usuario_id = :usuario_id
  AND t.tipo = 'despesa'
  AND t.status = 'liquidado'
  AND DATE_TRUNC('month', t.data_competencia) = DATE_TRUNC('month', CURRENT_DATE)
GROUP BY cat.nome, o.valor_limite
ORDER BY gasto DESC;
```

**⚠️ Nota:** Todas as queries filtram por `:usuario_id` — parâmetro obrigatório.
Nunca executar sem este filtro em produção.

---

## 9. Decisões Pendentes que Bloqueiam a Fase 4

| ID  | Decisão                                             | Impacto          | Quem decide |
|-----|-----------------------------------------------------|:----------------:|-------------|
| D01 | `finapp-dev`, `finapp-prod` ou PostgreSQL local?    | 🔴 Bloqueante    | Proprietário |
| D04 | Schema do banco coincide com `modelagem_inicial.md`? | 🔴 Bloqueante   | Verificar banco atual |
| D03 | App local (single-user) ou hospedado (multi-user)?  | 🟠 Define auth   | Proprietário |
| —   | Criar role `app4_reader` com permissões mínimas     | 🔴 Bloqueante    | Ação DBA    |
| —   | Definir `usuario_id` a ser usado nas queries        | 🔴 Bloqueante    | Proprietário |

---

## 10. Próximos Passos Concretos

1. **Resolver D01:** escolher o banco (`finapp-dev` recomendado para inicio)
2. **Resolver D04:** inspecionar o schema atual do banco e comparar com `modelagem_inicial.md`
3. **Criar role de leitura:** executar SQL do item 6 (S01) no Supabase SQL Editor
4. **Criar `.env` local** com `DATABASE_URL` da connection string do role criado
5. **Testar `test_connection()`** em `pages/configuracoes.py` — badge deve ficar verde
6. **Implementar etapa 4.2:** queries em ordem, uma a uma, validando a cada passo
7. **Criar `docs/status_fase_4.md`** ao concluir

---

## Links de Referência

- Vault: `MAPA_SUPABASE.md` — arquitetura dos projetos Supabase
- Vault: `modelagem_inicial.md` — schema SQL completo das tabelas
- Vault: `regras_principais.md` — RG-001 (filtro usuario_id), RC-001, RD-001, RD-004, RI-002
- Vault: `kpis_principais.md` — KPI-F01 a KPI-F05, KPI-I01 a KPI-I07, KPI-C01 a KPI-C04
- App: `core/financeiro.py` → `_visao_geral_real()` — placeholder a implementar
- App: `core/mock_data.py` — schema a espelhar nas queries reais
- App: `core/database.py` → `get_engine()` — engine SQLAlchemy pronta para uso
- App: `core/config.py` → `settings.db_url` — prioriza `DATABASE_URL`, fallback `SUPABASE_DB_URL`
