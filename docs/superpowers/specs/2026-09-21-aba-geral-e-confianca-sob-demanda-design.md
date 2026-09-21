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
| Categorias no banco | `categories` — 57 `expense`, 9 `transfer`, 7 `income`, **0 `investment`**; 4 das `transfer` são de investimento (ver PR 3) |
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

> **Corrigido em 21/09/2026, durante a implementação.** O plano original mandava
> semear as categorias de investimento por `INSERT`, apoiado na leitura "0
> `investment`" da tabela acima. O banco vivo desmentiu: `Exterior`,
> `Renda Fixa`, `Renda Variável` e `Aporte em Investimento` **já existem**, como
> `type = 'transfer'`, e **21 lançamentos apontam para elas**. Inserir cópias
> criaria duas `Exterior` e partiria o histórico em duas fatias que nenhuma tela
> soma. O que vale é o parágrafo abaixo.

**Migration `072_categorias.sql`** (roda à mão no SQL Editor do Supabase,
idempotente, tudo dentro de um `BEGIN/COMMIT`):

1. `active BOOLEAN NOT NULL DEFAULT TRUE` em `categories`. Não existia, e sem ela
   tirar uma categoria do seletor só seria possível APAGANDO a linha — junto com
   a classificação dos lançamentos que a usam.
2. **Retipar, não inserir**: `UPDATE categories SET type = 'investment'` nas
   quatro linhas `transfer` acima. Como a agregação classifica por `c.name` e por
   `t.type`, e **nunca** por `categories.type`, retipar não move nenhum número.
   `Resgate de Investimento` fica em `transfer` de propósito — é o caminho
   inverso do aporte, e somá-lo ao aporte dá um número que não é nenhum dos dois
   (`memoria: convencao-nao-pode-apagar-o-observado`). Um teste lê o texto da
   migration e prova que o resgate não está no `UPDATE`.
3. `INSERT` só dos cinco pares `(nome, tipo)` que realmente faltavam:
   `Dividendos`/`income`, `Outros`/`income`, `Restaurante`/`expense`,
   `Reserva de Despesa`/`investment`, `Outros`/`investment`.
4. Índice único por `(COALESCE(user_id, uuid-zero), lower(name), type)` —
   `NULL` nunca conflita com `NULL`, então sem o `COALESCE` as categorias de
   sistema poderiam ser duplicadas à vontade.
5. Os `CHECK` passam a **existir de fato**, com os valores que o banco contém:
   `'investment'` nos dois `type` e `'csv_migration'` em `transactions.source`
   (o `source` declarado no `002` também já não descrevia o dado gravado).

**Três defeitos silenciosos do formulário, fechados aqui.** Nenhum corrompe dado
hoje (0 `category_id` nulo, 0 lançamento em resgate); todos eram latentes:

- `Dividendos`, `Restaurante` e `Reserva de Despesa` estavam no literal do
  seletor e **não existiam** como categoria: escolher um gravava
  `category_id = NULL`, calado.
- `Outros` existia só como `expense`, e o `next(...)` que resolvia o id ignorava
  o tipo — uma **entrada** "Outros" recebia a categoria de **despesa**.
- `is_investment_category("Resgate de Investimento")` devolvia `True`: a
  heurística casava `"invest" in norm`. O caminho SQL, de lista fechada,
  devolvia `False`. As duas definições discordavam, e um resgate entraria em
  "Investido no mês".

**`core/categorias.py`** (módulo novo), com duas responsabilidades distintas:

- `listar(tipo)` / `criar(nome, tipo)` / `arquivar(id)` sobre `categories`.
  `listar` devolve `{"id", "nome", "minha"}`; `id = None` marca nome do `SEED`
  que o banco ainda não tem, e a tela diz isso em vez de gravar sem categoria.
  Como a coluna `active` só chega com a migration, a leitura tem duas formas e
  a segunda roda em **conexão nova** (`memoria: fallback-morre-com-a-transacao-abortada`).
- `NOMES_DE_INVESTIMENTO` e as duas formas derivadas dela (`SQL_INVESTIMENTO`,
  `CHAVES_DE_INVESTIMENTO`). É **constante, não consulta**: se a lista viesse do
  banco, criar uma categoria reclassificaria histórico já fechado sem ninguém
  pedir. Categoria nova entra no agregado pelo `transactions.type`, que o
  formulário já grava como `investment`.

**`views/controle_financeiro.py`**: os três literais saem; o `selectbox` lê
`listar(tipo)` e o id vem da própria opção escolhida, não de uma busca por nome.

**A unificação, que é o motivo de este PR andar sozinho.** Havia **três**
definições de "é investimento": duas cópias byte-a-byte do literal SQL
(`core/controle.py`, `core/investimentos.py`) e um `frozenset` normalizado que
não batia com elas — ele conhecia `acao`, `aporte investimento` e
`fundo imobiliario`, que o SQL não citava. O literal não afeta o lançamento
manual (esse é classificado por `t.type = 'investment'`); afeta **todo
lançamento importado**, que entra como `expense` e só vira aporte pelo nome
(`core/controle.py:319-344`). É `memoria: guarda-duplicada-diverge` combinado
com `memoria: guarda-no-consumidor-nao-cobre-os-outros`.

**Testes:**
- Nenhuma segunda cópia da lista no repositório — checagem por leitura do
  código-fonte, não por comportamento: duas cópias idênticas hoje passam em
  qualquer teste de comportamento e divergem amanhã.
- Piso de regressão: cada um dos 18 nomes do SQL antigo e cada chave do
  `frozenset` antigo continuam classificados como aporte. A lista pode crescer;
  encolher é mudança silenciosa em histórico fechado.
- `Resgate de Investimento` não é aporte por nenhum dos dois caminhos.
- `listar` marca com `id = None` o nome do seed que falta no banco, não duplica
  por acento, e devolve o seed inteiro quando as duas leituras falham.
- `criar` rejeita duplicata ignorando acento e caixa; `arquivar` recusa
  categoria de sistema.
- A view não tem mais os três literais e chama `listar_categorias` (AST).
- O texto da migration não retipa o resgate, cita `investment` e
  `csv_migration`, e insere os nomes que o `SEED` promete.

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
