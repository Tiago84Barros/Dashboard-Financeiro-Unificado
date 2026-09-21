# Aba "Geral" em Configurações + Grau de Confiança sob demanda

Data: 2026-09-21
Estado: aprovado pelo dono do app em 21/09/2026

## Problema

Duas coisas, pedidas juntas porque a segunda esvazia a primeira.

**A aba Grau de Confiança mede tudo a cada abertura de Configurações.** Ela é a
primeira aba desde 17/09/2026, e `st.tabs` executa o corpo de *todas* as abas em
toda execução do script — trocar de aba é client-side e não gera rerun. Logo, a
posição não protege ninguém: `relatorio()` (`core/confianca_secao.py:691`)
percorre sete seções, cada uma consultando o banco, e não tem cache nenhum.
O docstring de `_render_confianca` já dizia, desde setembro, que adiar exige um
portão explícito. Este spec constrói esse portão.

**Tema e troca de usuário moram na sidebar**, visíveis em toda tela, e não há
lugar nenhum para criar categoria do Controle Financeiro nem para limpar a
memória da LLM por seção. O pedido é concentrar isso numa aba "Geral", primeira
de Configurações, e tirar da sidebar o que migrar.

## Onde as coisas estão hoje (verificado, não suposto)

| Coisa | Lugar real |
|---|---|
| Medição de confiança | `core/confianca_secao.py::relatorio()` — sem cache |
| Tabela de rigor dos 3 motores | `views/confianca.py::_rigor()` — `user_cache_data(ttl=900)` |
| Seletor de tema | `app.py:68` → `design/theme_selector.py` |
| Trocar usuário | `app.py:66` → `core/auth.py::encerrar_sessao()` |
| Listas de categoria | **literais Python** em `views/controle_financeiro.py:97-107` |
| Categorias no banco | `categories` — 57 `expense`, 9 `transfer`, 7 `income`, **0 `investment`** |
| "O que é investimento" | `_INVESTMENT_CATEGORY_SQL` — **duas cópias** (`core/controle.py:91`, `core/investimentos.py:1118`) |
| Memória da LLM | `core/chat_repository.py` — chave `prefixo:sha256(contexto)[:24]` |

### O banco real não tem as constraints que o repositório declara

Consulta a `pg_constraint` em 21/09/2026 devolveu **zero** `CHECK` de `type` em
`categories` e `transactions`. O DDL do repositório
(`supabase_unificado/schema/002_financial_tables.sql:72` e `:97`) declara
`CHECK (type IN ('income','expense','transfer'))` nas duas.

É por isso que existem 12 linhas com `transactions.type = 'investment'` apesar
de o DDL proibir. **Um banco novo criado a partir de `schema/` teria a
constraint e quebraria no primeiro lançamento de investimento.** Verificador e
escritor lendo estruturas diferentes é o defeito de
`memoria: verificador-e-escritor-listas-diferentes`; aqui ele já está instalado,
e este spec o conserta de passagem porque a aba nova passa a gravar
`categories.type = 'investment'`.

## Escopo e faseamento

Três PRs. O terceiro mexe em agregação financeira e não pode viajar junto com
mudança de tela.

### PR 1 — Grau de Confiança: última medição gravada + recálculo sob comando

**Tabela nova** (migration `071_confianca_snapshots.sql`):

```sql
CREATE TABLE IF NOT EXISTS confianca_snapshots (
    id         BIGSERIAL    PRIMARY KEY,
    medido_em  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    payload    JSONB        NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_confianca_snapshots_medido_em
    ON confianca_snapshots (medido_em DESC);
```

Sem `user_id`: a medição é do aplicativo, não da pessoa — ela lê qualidade de
dado compartilhado, e só o admin vê a aba.

**`core/confianca_snapshot.py`** (módulo novo, separado de `confianca_secao.py`
para que a medição não passe a depender de persistência):

- `serializar(secoes, rigor) -> dict` — `ConfiancaSecao` é dataclass; a
  serialização é explícita, campo a campo, com uma chave `versao`.
- `desserializar(payload) -> (secoes, rigor)`.
- `gravar(secoes, rigor) -> datetime`.
- `carregar_ultimo() -> (secoes, rigor, medido_em) | None`.
- `podar(manter=50)` — chamado no `gravar`. O `DELETE` varre a tabela inteira,
  não um subconjunto filtrado (`memoria: remocao-escopada-pelo-filtro-da-leitura`).

**`views/confianca.py::render_corpo()`** passa a:

1. Chamar `carregar_ultimo()`.
2. Sem snapshot: estado vazio explicando que a medição é cara, e o botão. **Não
   mede sozinha** — senão o primeiro acesso volta a pagar tudo e o portão não
   serviu para nada.
3. Com snapshot: desenha a partir dele, com o carimbo `Medido em DD/MM/AAAA HH:MM`
   e o botão "Recalcular agora".
4. O botão chama `relatorio()` + `_rigor()`, grava e faz rerun.

**A idade é requisito, não enfeite.** A medição envelhece enquanto o dado
embaixo dela muda. Um snapshot de três semanas marcando "Alta" soa como rigor e
já é falso — é `memoria: aviso-que-envelhece-invertido`. Então:

- a idade aparece sempre, em texto derivado de `medido_em`, nunca fixo;
- acima de 7 dias, o cabeçalho ganha aviso visível de que a medição está velha;
- o texto do aviso é **derivado da medição**, não escrito à mão.

**Testes:**
- `serializar` → `desserializar` preserva `pct`, `faixa`, `cobertura_da_medicao`
  e componentes não medidos como `None` (nunca `0.0` —
  `memoria: medicao-que-pune-a-evidencia`).
- Abrir a aba **não** chama `relatorio()`. Este é o teste que prova o pedido.
- Snapshot com `medido_em` antigo produz o aviso; recente não produz.
- `podar` deixa exatamente `manter` linhas.

### PR 2 — Aba "Geral" (tema, trocar usuário, limpar LLM) e limpeza da sidebar

**Arquivo novo `views/configuracoes_geral.py`.** `views/configuracoes.py` já tem
1831 linhas; quatro blocos novos ali pioram um arquivo grande demais.
`configuracoes.py` só ganha a aba e a chamada.

Ordem das abas passa a: `⚙️ Geral`, `🎯 Grau de Confiança`, `🔁 Atualização de
dados`, `🔄 Dados de mercado`, `🗄️ Banco de dados`, `🔒 Segurança`.

**Bloco Tema** — move `render_theme_selector()` para cá, sem mudar a lógica de
persistência (o callback `_persist_choice` já resolve o bug do rerun; não tocar).

**Bloco Trocar de usuário** — `encerrar_sessao()`, com confirmação explícita:
é ação irreversível na sessão (limpa `session_state` inteiro).

**Bloco Limpar histórico da LLM** — seleção de seção, mais "Todas".

`core/chat_repository.py` ganha `clear_prefixo(prefixo) -> int`, devolvendo
quantas conversas apagou. Hoje só existe `clear(conversa)`, de chave exata: uma
seção tem **N** conversas (uma por assinatura de contexto — conjunto de tickers,
símbolos, `_ctx_sig`), então apagar por chave exata limparia uma e deixaria as
outras de pé, parecendo ter funcionado.

As **oito** seções e seus prefixos, lidos do código (`grep conversation_key`):

| Seção | Prefixo | Chave de sessão |
|---|---|---|
| Controle Financeiro | `controle_financeiro` | `cf_chat_history` |
| Cartão de Crédito | `cartao_credito` | `cc_chat_history` |
| Análise de Portfólio B3 | `apb3` | `apb3_chat_history` |
| Análise de Portfólio EUA | `apus` | `apus_chat_history` |
| Seleção de FIIs | `fii_portfolio` | `fii_chat_history` |
| Portfólio Global | `portfolio_global` | `portfolio_global_chat_historico` |
| Ativo individual (B3, EUA e FIIs) | `chat_ativo` | `chat_ativo_*` |
| Carteira por classe | `chat_carteira` | `chat_carteira_*` |

**A primeira versão deste spec listava seis.** Faltavam `chat_ativo`
(`design/chat_ativo.py:112`, usado por Empresas B3, Empresas Americanas e
Seleção de FIIs) e `chat_carteira` (`design/chat_carteira.py:125`, usado por
Investimentos). Escrever a lista à mão errou na primeira tentativa, que é
exatamente o que o teste abaixo existe para impedir.

O mapa mora em **um** lugar (`core/chat_repository.py::SECOES`), e dois testes o
conferem contra o código-fonte: os prefixos, derivados das chamadas reais de
`conversation_key`, e as chaves de sessão, derivadas dos `session_key=` passados
a `load/save/clear_chat_history`.

**A limpeza é do banco e da sessão.** Apagar só a preferência deixaria a tela
aberta ainda exibindo o histórico morto — e regravando-o na mensagem seguinte,
porque o `save` parte do que está em `st.session_state`.

**Sidebar** (`app.py`): saem o `selectbox` de tema e o botão "Sair / trocar
usuário". Sai também `Conectado como X`, conforme escolhido. Fica marca +
navegação.

**Testes:**
- `clear_prefixo("apb3")` apaga as N conversas do prefixo e **não** toca em
  `apus` (o prefixo mais curto não pode pegar o mais longo por acidente).
- Os prefixos declarados batem com os que o código realmente usa.
- A sidebar não renderiza mais tema nem logout.

### PR 3 — Categorias configuráveis, e uma só definição de "investimento"

**Migration `072_categorias_investimento.sql`:**

- Reconcilia o DDL com o banco real: recria os `CHECK` de `categories.type` e
  `transactions.type` incluindo `'investment'`.
- Idempotente (`DROP CONSTRAINT IF EXISTS` antes de `ADD`), porque no banco vivo
  eles não existem e no banco novo existem.

**`core/categorias.py`** (módulo novo):

- `listar(tipo) -> list[Categoria]` — `user_id = :uid OR user_id IS NULL`.
- `criar(nome, tipo)` — normaliza, rejeita duplicata por nome normalizado dentro
  do tipo, grava com `user_id = :uid`.
- `arquivar(id)` — não apaga. Categoria em uso por transação não pode sumir sem
  levar histórico junto; a regra do CLAUDE.md é não apagar funcionalidade sem
  validação, e o mesmo vale para dado.
- `SEED` com as listas atuais de `views/controle_financeiro.py:97-107`, aplicado
  a quem ainda não tem categoria daquele tipo. Ninguém perde categoria.

**`views/controle_financeiro.py`**: os três literais saem; o `selectbox` passa a
ler `listar(tipo)`.

**A unificação, que é o motivo de este PR andar sozinho.** Hoje
`_INVESTMENT_CATEGORY_SQL` é um literal com 19 nomes, duplicado em dois módulos.
Ele **não** afeta o lançamento manual — esse é carregado por
`t.type = 'investment'`. Ele afeta **todo lançamento importado**: extrato
bancário entra como `expense`, e é só pelo nome que vira aporte
(`core/controle.py:319-344`).

Consequência de deixar como está: criar "Cripto Exchange" na aba nova, importar
um extrato com aporte nessa categoria, e o valor é contado como **despesa**. Sem
erro, sem linha a menos. É `memoria: guarda-duplicada-diverge` combinado com
`memoria: guarda-no-consumidor-nao-cobre-os-outros`.

Então as duas cópias são substituídas por **uma** função em `core/categorias.py`
que deriva a lista de `categories WHERE type = 'investment'`, e os dois módulos
passam a chamá-la.

**Testes:**
- Categoria de investimento criada pelo usuário é reconhecida como aporte por
  `get_historico_anual` **e** pelo caminho de `core/investimentos.py` — o teste
  compara os dois caminhos, porque foi a divergência entre eles que criou o risco.
- Não sobra nenhum literal de nome de categoria de investimento no código
  (checagem por AST/grep, não por comportamento — `memoria: guarda-duplicada-diverge`).
- `criar` rejeita duplicata ignorando acento e caixa ("Renda Variavel" x "Renda Variável").
- Seed não duplica quando roda duas vezes.
- Migration é idempotente contra banco com e sem a constraint.

## Fora de escopo

- Reescrever o motor de confiança. O spec só muda **quando** ele roda.
- Agendar a medição de confiança por rotina noturna. Pode vir depois; o snapshot
  criado aqui é o que uma rotina precisaria para existir.
- Hierarquia de categorias (`categories.parent_id` existe e continua sem uso).
- Editar/renomear categoria. Só criar e arquivar.

## Critério de pronto

1. Abrir Configurações não dispara `relatorio()`; a aba mostra a última medição
   com a data, e o recálculo só acontece no clique.
2. A aba "Geral" é a primeira, e tema, troca de usuário, criação de categoria e
   limpeza de histórico da LLM funcionam por ela.
3. Sidebar sem tema e sem logout.
4. Categoria de investimento criada pelo usuário aparece no lançamento manual
   **e** é contada como aporte na agregação de importados.
5. Suíte verde nos três PRs.
