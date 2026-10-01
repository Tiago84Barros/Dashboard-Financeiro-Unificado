# Log de lacunas — Fatia 1 (núcleo + destinos) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar `registrar_lacuna(...)`, que monta uma lacuna sanitizada com
impressão digital estável e a grava em JSONL local ou em `app_lacunas` no
Supabase, sem nunca levantar exceção.

**Architecture:** Três arquivos em `core/lacunas/`:
- `evento.py`: dados puros (normalização, sanitização, impressão digital, dataclass);
- `destino.py`: escolha do destino e gravação (arquivo / UPSERT SQL);
- `registro.py`: porta pública (inferência de módulo, deduplicação por sessão,
  engole falhas).

A migration `077_app_lacunas.sql` cria a tabela na nuvem.

**Tech Stack:** Python 3.12, SQLAlchemy (`text()`), Streamlit `session_state`,
pytest com SQLite em memória.

Spec: `docs/superpowers/specs/2026-09-28-log-de-lacunas-design.md`, seção 1.

## Global Constraints

- Testes rodam com `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest`.
- A suíte é offline por construção (`tests/conftest.py` bloqueia socket). Nada de rede.
- Sob pytest (`PYTEST_CURRENT_TEST`), o destino padrão é `desligado`. Um teste
  que queira gravar define `LACUNAS_DESTINO` por `monkeypatch.setenv`.
- Chaves de contexto permitidas: `tabela`, `coluna`, `periodo`, `n_faltantes`, `versao_motor`.
- `ultima_mensagem` / `mensagem` truncada em 500 caracteres.
- Status válidos: `aberta`, `legitima`, `em_pr`, `resolvida`, `incerta`.
- `local_staging/lacunas/` fica fora do git.

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `core/lacunas/__init__.py` | reexporta `registrar_lacuna`, `Lacuna` |
| `core/lacunas/evento.py` | `normalizar`, `sanitizar`, `impressao_digital`, `filtrar_contexto`, `Lacuna`, `construir_lacuna` |
| `core/lacunas/destino.py` | `escolher_destino`, `gravar_local`, `gravar_banco`, `ARQUIVO_LOCAL` |
| `core/lacunas/registro.py` | `registrar_lacuna`, deduplicação, inferência de módulo |
| `supabase_unificado/schema/077_app_lacunas.sql` | tabela `app_lacunas` |
| `tests/test_lacunas_evento.py`, `tests/test_lacunas_destino.py`, `tests/test_lacunas_registro.py` | testes |
| `.gitignore` | `local_staging/lacunas/` |

---

### Task 1: evento (dados puros)

**Files:** Create `core/lacunas/evento.py`, `core/lacunas/__init__.py` (vazio
por ora). Test: `tests/test_lacunas_evento.py`.

**Produces:**
- `normalizar(texto: str) -> str`
- `sanitizar(texto: str) -> str`
- `impressao_digital(fonte, modulo, codigo, entidade, mensagem) -> str` (sha1 hex, 40 caracteres)
- `filtrar_contexto(ctx: dict | None) -> dict`
- `@dataclass(frozen=True) Lacuna(ts, impressao, fonte, modulo, codigo, entidade, mensagem, contexto)`
- `construir_lacuna(*, fonte, codigo, mensagem, modulo, entidade=None, contexto=None, agora=None) -> Lacuna`, que levanta `ValueError` quando a fonte é inválida.

- [ ] Step 1: escrever os testes. A impressão digital é estável quando os
  números da mensagem mudam e muda quando `codigo` ou `entidade` mudam. Sem
  `codigo`, a mensagem normalizada vira a chave. `sanitizar` remove URL,
  `postgresql://`, e-mail e `R$ 1.234,56`. `filtrar_contexto` descarta chaves
  fora da lista branca. Fonte inválida levanta `ValueError`. A mensagem é
  truncada em 500 caracteres. A entidade tem caixa normalizada (`petr4` = `PETR4`).
- [ ] Step 2: rodar e ver falhar (`ModuleNotFoundError`).
- [ ] Step 3: implementar (código em `core/lacunas/evento.py`).
- [ ] Step 4: rodar e ver passar.
- [ ] Step 5: commit `feat(lacunas): evento com impressão digital estável e sanitização`.

### Task 2: destino (arquivo e banco) + migration

**Files:** Create `core/lacunas/destino.py`,
`supabase_unificado/schema/077_app_lacunas.sql`; Modify `.gitignore`. Test:
`tests/test_lacunas_destino.py`.

**Consumes:** `Lacuna` (Task 1).

**Produces:**
- `escolher_destino(env: Mapping | None = None, raiz: Path | None = None) -> str` (`local` | `supabase` | `desligado`)
- `gravar_local(lacuna: Lacuna, arquivo: Path | None = None) -> None`
- `gravar_banco(engine, lacuna: Lacuna) -> None`
- `ARQUIVO_LOCAL: Path`

- [ ] Step 1: escrever os testes.
  - Ordem de precedência de `escolher_destino`: variável > pytest >
    `/mount/src` > local; um valor inválido na variável é ignorado.
  - `gravar_local` acrescenta linhas JSON e cria o diretório.
  - `gravar_banco` em SQLite:
    - a 1ª gravação cria a linha com `ocorrencias=1` e `status=aberta`;
    - a 2ª soma;
    - sobre `resolvida`, reabre com `reincidente=1`;
    - sobre `legitima`, mantém o status.
  - Cada coluna usada no UPSERT existe na migration 077.
- [ ] Step 2: rodar e ver falhar.
- [ ] Step 3: implementar `destino.py`, a migration e a linha no `.gitignore`.
- [ ] Step 4: rodar e ver passar.
- [ ] Step 5: commit `feat(lacunas): destinos local (JSONL) e Supabase (app_lacunas, migration 077)`.

### Task 3: registro (porta pública)

**Files:** Create `core/lacunas/registro.py`; Modify `core/lacunas/__init__.py`.
Test: `tests/test_lacunas_registro.py`.

**Consumes:** `construir_lacuna`, `escolher_destino`, `gravar_local`, `gravar_banco`.

**Produces:**
- `registrar_lacuna(fonte, codigo, mensagem, *, modulo=None, entidade=None, contexto=None) -> None`
- `_limpar_vistas() -> None` (só para testes)

- [ ] Step 1: escrever os testes.
  - Com `LACUNAS_DESTINO=local`, grava uma linha no arquivo apontado por
    `ARQUIVO_LOCAL`.
  - A mesma impressão, chamada duas vezes, grava uma única vez.
  - Sob pytest, sem a variável, não grava nada.
  - Uma exceção em `gravar_local` não propaga.
  - Uma fonte inválida não propaga.
  - O módulo inferido é `tests/test_lacunas_registro.py:<nome_do_teste>`, sem
    número de linha.
  - Com `LACUNAS_DESTINO=supabase` e `get_engine()` devolvendo `None`, nada
    acontece e nada levanta.
- [ ] Step 2: rodar e ver falhar.
- [ ] Step 3: implementar.
- [ ] Step 4: rodar os 3 arquivos de teste e `ruff check core/lacunas tests/test_lacunas_*.py`.
- [ ] Step 5: commit `feat(lacunas): registrar_lacuna com deduplicação por sessão e sem exceção`.

## Próximas fatias (planos separados)

2. Captura de exceções (`app.py`) e das limitações estruturadas.
3. Bloco `<lacunas>` do LLM.
4. `aviso_lacuna`.
5. Sincronizador.
6. Guarda + skill + tarefa agendada.
