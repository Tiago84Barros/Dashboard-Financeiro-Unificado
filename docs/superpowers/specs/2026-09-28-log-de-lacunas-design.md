# Log de lacunas e corretor autônomo — design

Data: 2026-09-28 · Branch: `lacunas-log`

## Objetivo

O app já declara com honestidade o que não sabe: limitações dos motores, avisos
de tela, respostas do LLM que dizem "não tenho esse dado" e exceções. Hoje isso
só aparece na tela e se perde. Este design grava cada lacuna num log único,
consolidado entre execução local e Streamlit Cloud, e dá a uma IA (Claude Code
headless, ou Codex pelo `AGENTS.md`) um procedimento diário para triar a lacuna
mais relevante e, quando ela for defeito ou falta de dado, abrir um PR com a
correção.

Nada entra na `main` sem merge humano.

## Decisões tomadas

| Pergunta | Decisão |
|---|---|
| Onde gravar | Local (JSONL fora do git) **e** Cloud (tabela `app_lacunas` no Supabase) |
| O que capturar | Limitações estruturadas, lacunas declaradas pelo LLM, avisos de tela, exceções |
| Autonomia | Tarefa agendada diária que corrige num branch e abre PR |
| Arquitetura | Log de eventos + sincronização local + corretor agendado (abordagem A) |

Descartadas: só-local (perde a Cloud) e issues do GitHub como log (exigiria token
de escrita do GitHub dentro do app publicado).

## 1. Evento e impressão digital

Módulo `core/lacunas/`, com uma única porta de entrada:

```python
registrar_lacuna(
    fonte: Literal["motor", "llm", "tela", "excecao"],
    codigo: str,                  # estável, escolhido por quem chama: "fii.vpa_sem_historico"
    mensagem: str,                # o texto que a pessoa viu
    modulo: str | None = None,    # inferido do frame chamador quando omitido
    entidade: str | None = None,  # ticker / CNPJ / tabela
    contexto: dict | None = None, # só chaves da lista branca
) -> None
```

- **Nunca levanta exceção nem bloqueia a tela.** Falha de gravação vai para
  `logging` e mais nada.
- **Impressão digital** = `sha1(fonte | modulo | codigo | entidade)`. A mensagem
  fica fora da chave porque texto com números muda a cada execução. Quando não
  houver `codigo` útil (LLM, aviso migrado sem código), a chave usa a mensagem
  **normalizada**: dígitos → `#`, datas → `<data>`, espaços colapsados, minúsculas.
- **Sanitização da mensagem:** remove URLs, e-mails, strings `postgresql://…` e
  valores monetários (`R$ …`, `US$ …`).
- **Lista branca do contexto:** `tabela`, `coluna`, `periodo`, `n_faltantes`,
  `versao_motor`. Qualquer outra chave é descartada em silêncio.
- **Deduplicação por sessão do Streamlit:** a mesma impressão é gravada uma vez
  por sessão (`st.session_state`), senão a frequência mediria cliques. Fora do
  Streamlit (scripts), deduplica por processo.

### Destino

A escolha **não** usa `e_local(get_engine())`: na máquina local `get_engine()`
também devolve o Supabase (ver `CLAUDE.md`), então essa regra mandaria tudo para
a nuvem. A ordem é:

1. `LACUNAS_DESTINO` (`local` · `supabase` · `desligado`) quando definida.
2. Rodando sob pytest (`PYTEST_CURRENT_TEST`) → `desligado`.
3. Repositório montado em `/mount/src` (Streamlit Community Cloud) → `supabase`.
4. Qualquer outro caso → `local`.

O nome do módulo inferido é `caminho/relativo.py:funcao`, **sem número de
linha**, porque o número muda a cada edição e mudaria a impressão digital.

Na Cloud, a gravação vai para uma thread de um único worker, para o UPSERT não
somar latência de rede ao render.

- **Local:** acrescenta uma linha JSON em `local_staging/lacunas/eventos.jsonl`
  (fora do git): `ts, impressao, fonte, modulo, codigo, entidade, mensagem,
  contexto`.
- **Cloud:** UPSERT em `app_lacunas`, uma linha por impressão:

| coluna | tipo | nota |
|---|---|---|
| `impressao` | text PK | sha1 hex |
| `fonte`, `modulo`, `codigo`, `entidade` | text | |
| `ultima_mensagem` | text | sanitizada, truncada em 500 caracteres |
| `contexto` | jsonb | lista branca |
| `primeira_vez`, `ultima_vez` | timestamptz | |
| `ocorrencias` | integer | somada no UPSERT |
| `status` | text | `aberta` · `legitima` · `em_pr` · `resolvida` · `incerta` |
| `reincidente` | boolean | |
| `pr_url`, `nota_triagem` | text | |

UPSERT sobre uma linha `resolvida` a devolve para `aberta` com
`reincidente = true`. Sobre `legitima`, `em_pr` ou `incerta` só atualiza os
contadores. O tamanho esperado é de dezenas de KB.

## 2. Captura

| Fonte | Ponto único de captura | Código / chave |
|---|---|---|
| `excecao` | fronteira de isolamento entre rotas em `app.py` (bloco que já usa `core/erro_diagnostico.py`) | `codigo` = tipo da exceção; `modulo` = frame mais interno do projeto (`arquivo:linha`). A mensagem da exceção nunca é gravada. |
| `llm` | `core/llm_b3.py::_chat_complete` e os assistentes que não passam por ele (a confirmar na implementação: `core/inteligencia/llm.py`, `core/llm_global.py`, `core/llm_ativo.py`, `core/llm_carteira.py`) | `modulo` = assistente; chave pela mensagem `falta` normalizada |
| `motor` | renderizadores que já iteram `.limitacoes` / `.alertas` (painel da Inteligência, dossiês, relatórios B3/EUA/FII), via `registrar_limitacoes(objeto, modulo=, entidade=)` | `codigo` derivado do tipo do objeto + índice estável ou texto normalizado |
| `tela` | wrapper `design/…::aviso_lacuna(mensagem, codigo=, nivel="warning"\|"info")`, que desenha e registra | `codigo` explícito |

### Bloco `<lacunas>` do LLM

Todo prompt de sistema ganha a regra: *se faltar dado para responder, termine
com um bloco `<lacunas>` contendo uma linha JSON por lacuna:
`{"codigo": "...", "falta": "...", "entidade": "..."}`*.

- O bloco é extraído **antes** do texto chegar à tela, e também antes da
  verificação de grounding numérico.
- Uma linha malformada é registrada como `llm.lacuna_malformada`, sem quebrar a
  resposta.
- Uma resposta sem bloco não gera nenhum registro.

### Por que capturar no render, e não no motor

Os mesmos motores rodam em backtests e scripts. Capturar no motor inundaria o log
com lacunas que ninguém viu.

### Migração dos avisos de tela

Um script lista os `st.warning`/`st.info` cujo texto diz que falta dado ("sem
dados", "não disponível", "insuficiente", "sem histórico"). O primeiro PR migra
os ~20–30 mais claros. `st.caption` fica fora (quase sempre nota metodológica).

## 3. Sincronização

`scripts/lacunas_sincronizar.py`, só local:

1. Agrega `eventos.jsonl` por impressão digital.
2. Lê `app_lacunas` pela engine de `core/database.py::get_engine()`.
3. Funde as duas por impressão: soma ocorrências e marca a origem (`local`,
   `cloud`, `ambos`).
4. Grava `local_staging/lacunas/abertas.json`, ordenado por
   **prioridade = ocorrências nos últimos 14 dias × peso da fonte**
   (`excecao` 3, `motor` 2, `tela` 2, `llm` 1).
5. Devolve para `app_lacunas` as mudanças de status feitas localmente pelo
   corretor.
6. Consulta `gh pr view` dos PRs `em_pr`:
   - PR mergeado → `resolvida`;
   - PR fechado sem merge → `aberta`, com a nota do motivo, para a próxima
     tentativa não repetir a mesma abordagem.
7. Rotação: no primeiro dia do mês, o `eventos.jsonl` do mês anterior é
   compactado como `eventos-AAAA-MM.jsonl.gz`.

Transições de estado:

```
aberta ──triagem──> legitima | incerta | em_pr
em_pr ──merge──> resolvida ──reaparece──> aberta (reincidente)
em_pr ──fechado sem merge──> aberta (com nota)
incerta ──> volta à fila com prioridade × 0,5
```

## 4. Corretor

**Disparo:** uma tarefa agendada diária às 06:00 na máquina local roda
`scripts/lacunas_sincronizar.py` e depois `claude -p "/corrigir-lacuna"` com teto
de turnos e de tempo. Com o PC desligado, a execução do dia não acontece e nada
se acumula. O `AGENTS.md` recebe o mesmo procedimento para o Codex.

**Skill `.claude/skills/corrigir-lacuna/SKILL.md`:**

1. **Seleção.** A lacuna `aberta` de maior prioridade. Se já houver 3 PRs de
   lacuna abertos, encerra sem fazer nada.
2. **Investigação**, nesta ordem: vault (`../ProjetoIA/`) e
   `graphify-out/GRAPH_REPORT.md` → warehouse local → código.
3. **Triagem**, com veredito escrito em `nota_triagem`:
   - `defeito`: o dado existe e o código não o alcança;
   - `dado`: falta ingestão ou atualização;
   - `legitima`: a limitação é real e o aviso está correto;
   - `incerta`: não conseguiu decidir.
4. **Ação:**
   - `legitima`: marca o status, sem PR.
   - `incerta`: registra o que investigou, sem mexer em nada.
   - `defeito` / `dado`:
     1. cria o worktree e o branch `lacuna/<impressao[:8]>`;
     2. escreve um teste que reproduz a lacuna e falha;
     3. corrige;
     4. roda a suíte com o Python312;
     5. faz o commit e abre o PR com `gh pr create`;
     6. marca `em_pr` com a `pr_url`.
   - Ingestão pesada ou escrita no Supabase não é executada: o PR entrega o
     script ou a correção, e a pessoa roda.
5. **Corpo do PR:** impressão digital, ocorrências, veredito, evidência de antes
   e depois de que o dado agora chega, e o que ficou de fora.

### Guardas contra "resolver apagando o aviso"

- **`tests/test_lacunas_guarda.py`**, que roda no CI. Compara o diff com
  `origin/main` e falha se o PR remover uma chamada `registrar_lacuna`,
  `registrar_limitacoes` ou `aviso_lacuna`, ou um literal dentro de
  `limitacoes=`. A exceção é o corpo do PR ter uma seção `## Aviso removido` com
  justificativa: a variável `PR_BODY` é lida pelo workflow do CI.
- **Critério de sucesso da skill:** a lacuna some **porque a condição mudou**. O
  teste precisa montar o cenário e mostrar o dado chegando. Um teste que só
  confirma que a mensagem sumiu não conta.
- **Proibições explícitas na skill:**
  - relaxar portão, limiar ou filtro;
  - trocar a ausência de dado por valor padrão;
  - mexer em pesos de score;
  - fazer merge;
  - fazer push na `main`;
  - abrir mais de 1 PR por execução.
- Estourar o teto de tempo ou de turnos registra `incerta` e encerra.

**Revisão humana:** os PRs, e um resumo semanal das lacunas marcadas como
`legitima` (gerado pelo sincronizador às segundas em
`local_staging/lacunas/resumo-semanal.md`).

## 5. Testes

Todos com o Python312 e com `load_dotenv` neutralizado.

- **`core/lacunas`:**
  - impressão estável quando os números mudam;
  - impressão diferente quando `codigo` ou `entidade` mudam;
  - sanitização;
  - lista branca do contexto;
  - deduplicação por sessão;
  - falha de gravação não levanta exceção.
- **Destino:**
  - o local é escolhido quando `e_local` e a nuvem nos outros casos;
  - o UPSERT soma as ocorrências;
  - `resolvida` reabre como `reincidente` (SQLite ou fake, sem banco real).
- **LLM:**
  - o bloco é extraído e não aparece no texto exibido;
  - o grounding não vê o bloco;
  - uma linha malformada vira `llm.lacuna_malformada`;
  - sem bloco, nada é registrado.
- **Motor e tela:** um AppTest confirma que o `aviso_lacuna` desenha **e**
  registra, e que o `registrar_limitacoes` percorre `limitacoes` e `alertas`.
- **Sincronizador:**
  - fusão local + Cloud;
  - janela de 14 dias;
  - transições de estado;
  - rotação mensal.
- **Guarda:** um diff sintético que remove `registrar_lacuna` falha, e o mesmo
  diff com `## Aviso removido` passa.

## Ordem de entrega

Um PR por fatia, sempre com merge para a `main`.

1. `core/lacunas` (evento, impressão digital, sanitização, destino local) +
   migration `app_lacunas` + destino Supabase.
2. Captura de exceções (`app.py`) e das limitações estruturadas (renderizadores).
3. Bloco `<lacunas>` do LLM.
4. `aviso_lacuna` + migração dos ~20–30 avisos mais claros.
5. `scripts/lacunas_sincronizar.py` + estados + resumo semanal.
6. Teste-guarda + skill `corrigir-lacuna` + `AGENTS.md` + tarefa agendada.

As fatias 1 e 2 já produzem um log útil. Os pesos da prioridade podem ser
ajustados com dado real antes do corretor existir.

## Fora do escopo

- Painel de lacunas dentro do app.
- Captura de `st.caption` e dos `except` locais.
- Autonomia para rodar ingestões ou escrever no Supabase fora de `app_lacunas`.
- Merge automático.
