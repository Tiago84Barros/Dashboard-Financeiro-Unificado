---
name: corrigir-lacuna
description: Pega a lacuna de maior prioridade do log de lacunas do app4 (local_staging/lacunas/abertas.json), faz a triagem (defeito, dado, legitima, incerta) e, quando for defeito ou falta de dado, corrige num branch e abre UM PR. Nunca faz merge. Use na rotina diária ou quando pedirem para "corrigir lacuna", "tratar a fila de lacunas" ou "/corrigir-lacuna".
---

# Corrigir lacuna

O app registra tudo o que admite não saber: limitação de motor, aviso de tela,
bloco `<lacunas>` da LLM e exceção de rota. O sincronizador funde esses registros
em `local_staging/lacunas/abertas.json`. Esta skill trata **uma** lacuna por
execução.

**Critério de sucesso:** a lacuna some **porque a condição mudou**, ou seja, o
dado agora chega. Não vale fazer o aviso sumir. Um teste que só confirma que a
mensagem não aparece mais não conta.

## Proibido, sem exceção

- Relaxar portão, limiar, filtro ou janela para a lacuna sumir.
- Trocar ausência de dado por valor padrão, zero, média ou chute.
- Mexer em pesos de score ou em parâmetros de metodologia.
- Remover `registrar_lacuna`, `registrar_limitacoes`, `aviso_lacuna` ou texto
  de `limitacoes`. O teste `tests/test_lacunas_guarda.py` barra no CI. Se a
  remoção for mesmo o certo, o corpo do PR precisa de `## Aviso removido` com
  justificativa.
- Fazer merge, fazer push na `main` ou abrir mais de 1 PR por execução.
- Rodar ingestão pesada ou escrever no Supabase. O PR entrega o script ou a
  correção, e a pessoa roda.

## Passo a passo

### 1. Sincronizar e checar o teto

```bash
python scripts/lacunas_sincronizar.py
gh pr list --state open --search "head:lacuna/" --json number --jq length
```

- Se houver **3 ou mais** PRs `lacuna/` abertos, encerre sem fazer nada.
- Leia `abertas.json`:
  - Se `fontes.cloud` não for `ok`, siga, mas registre no fim que a fila saiu só
    com o local.
  - Pegue o **primeiro** item de `fila`. Se a fila estiver vazia, encerre.

### 2. Investigar, nesta ordem

1. **O que o item já diz:** `fonte`, `modulo` (`arquivo.py:funcao`), `codigo`,
   `entidade`, `ultima_mensagem`, `nota_triagem` e `tentativas`.
   - Se houver `tentativas`, uma abordagem anterior foi recusada. **Não a
     repita.** Leia o PR fechado (`gh pr view <url> --comments`) para entender
     o motivo.
2. **O vault:** `../ProjetoIA/` e `graphify-out/GRAPH_REPORT.md`. A lacuna pode
   já estar documentada como decisão ou limitação conhecida.
3. **A memória do projeto:** o `MEMORY.md` carregado na sessão.
4. **O armazém local** (Docker `dfu_warehouse`, porta 5433). O dado
   frequentemente já está lá. A URL sai de:
   ```bash
   python -c "from scripts.publish_fii_selection_from_local import _warehouse_url; print(_warehouse_url())"
   ```
5. **O código:** a partir de `modulo`, siga até de onde o dado deveria vir.

Para a fonte `excecao`, a mensagem é só a identidade do erro, sem texto. Reproduza
o erro rodando o caminho da rota antes de concluir qualquer coisa.

### 3. Triagem: um veredito

| veredito | quando |
|---|---|
| `defeito` | o dado existe (banco, armazém, vitrine) e o código não o alcança |
| `dado` | o dado falta: ingestão, atualização ou publicação de vitrine pendente |
| `legitima` | a limitação é real e o aviso está correto (ex.: fundo novo sem 12 meses de histórico) |
| `incerta` | não deu para decidir com evidência |

`legitima` exige evidência de que o dado **não existe em nenhuma das duas
bases**, e não apenas de que o código não o encontrou. Na dúvida, o veredito é
`incerta`.

### 4. Agir

**`legitima`** (sem PR):

```bash
python scripts/lacunas_sincronizar.py marcar <impressao[:8]> --status legitima --nota "<evidência em 1-3 frases>"
```

**`incerta`** (não mexe em código):

```bash
python scripts/lacunas_sincronizar.py marcar <impressao[:8]> --status incerta --nota "<o que investigou e o que faltou para decidir>"
```

**`defeito` ou `dado`:**

1. Crie um worktree e um branch a partir da `main` atualizada:
   ```bash
   git fetch origin
   git worktree add ../lacuna-<impressao[:8]> -b lacuna/<impressao[:8]> origin/main
   ```
   O worktree não tem `.env`. Não conclua "está limpo" por falta de conexão.
2. Escreva um teste que **monta o cenário** e falha: o dado disponível, e o
   código não o entregando.
3. Corrija o código.
4. Rode a suíte **com o Python312**, não o do PATH:
   ```bash
   "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest -q tests/ -p no:cacheprovider
   ```
5. Faça o commit, o push e abra o PR:
   ```bash
   gh pr create --base main --title "fix(lacuna): <resumo>" --body "..."
   ```
6. Marque a lacuna:
   ```bash
   python scripts/lacunas_sincronizar.py marcar <impressao[:8]> --status em_pr --pr-url <url> --nota "<veredito>: <causa>"
   ```

Se o veredito for `dado` e a correção exigir rodar ingestão pesada, o PR entrega
o script ou o ajuste. O corpo do PR diz o comando exato que a pessoa deve rodar.

### 5. Corpo do PR

```markdown
## Lacuna
- impressão: `<impressao>` · fonte: <fonte> · módulo: `<modulo>` · código: `<codigo>` · entidade: <entidade>
- ocorrências em 14 dias: <n> (janela aproximada: sim/não) · total: <n>
- mensagem: <ultima_mensagem>

## Triagem
<defeito|dado>: <causa em 2-4 frases, com a evidência (consulta, arquivo, linha)>

## Antes e depois
<o teste que monta o cenário; o que o código entregava antes e o que entrega agora>

## Fora do escopo
<o que ficou de fora e por quê; comando a rodar, se houver ingestão>
```

## Tetos

- **1 lacuna por execução.**
- Se estourar o tempo ou os turnos antes de abrir o PR:
  - marque `incerta`, com o que investigou até ali;
  - se o worktree criado estiver sem alterações, remova-o;
  - encerre.
- Ao terminar, imprima uma linha: `<impressao[:8]> -> <veredito> [<pr_url>]`.
