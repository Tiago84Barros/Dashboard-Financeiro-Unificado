# Armazém local ao alcance da produção (túnel só-leitura)

O app na Streamlit Cloud não enxerga o Docker desta máquina. Sem o túnel, as
LLMs em produção só recebem a **vitrine** de notícias do Supabase, que é um
recorte por ativo. Não recebem o acervo inteiro nem as séries do
`macro_staging`.

Com o túnel ligado, `core/contexto_mercado.py` tenta, nesta ordem:

1. o armazém direto (só em desenvolvimento);
2. o armazém **pelo túnel** (`core/armazem_remoto.py`);
3. a vitrine do Supabase.

O bloco entregue à LLM diz qual das três entrou. Quando o túnel falha, o bloco
diz isso antes da vitrine. O PC desligado não quebra nada: o app volta ao
comportamento anterior e explica o motivo.

## O que o serviço expõe e o que não expõe

- **Expõe:** notícias avaliadas, as últimas observações macro, o detalhe de
  mercado por ativo (`/eua/detalhe`, `/fii/detalhe`, `/b3/detalhe`) e o
  inventário por ativo (`/ativo/inventario`: quantas linhas e que período cada
  fonte do armazém tem de um ticker; só contagens e datas, nenhuma linha de
  dado). É dado público.
- **Não expõe:** finanças, carteira, cartão e o espelho do Supabase. Nenhuma
  rota lê essas tabelas.
- **Só leitura.** As rotas são GET, menos `POST /sql` (seção abaixo), que
  aceita um único SELECT e roda com o papel `app4_leitura`. Toda sessão do
  Postgres abre com `default_transaction_read_only=on`.
- **Só `127.0.0.1`.** Quem expõe o serviço é o túnel, e só ele.
- **Token de 32+ caracteres obrigatório**, comparado em tempo constante.
- **Tetos por chamada:** até 500 notícias e até 30 dias; na rota por ativo
  (`/noticias/ativos`), até 80 tickers e 30 dias.
- **Notícias por ativo:** a rota devolve as linhas cruas e o app agrega com a
  mesma fórmula da leitura direta (`core.conjuntura.ponte`). Depois de atualizar
  o código, **reinicie o serviço**: o processo antigo não conhece a rota nova e
  o app cai na vitrine avisando que o túnel respondeu 404.

## Passo a passo (feito por você: conta e instalação ficam fora do código)

### 1. Gerar o token

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Coloque o valor no `.env` desta máquina:

```
ARMAZEM_API_TOKEN=<o token>
```

### 2. Subir o serviço

```bash
python scripts/servir_armazem_leitura.py
```

Ele escuta em `http://127.0.0.1:8787`. Para conferir, rode em outro terminal:

```bash
curl -H "Authorization: Bearer <o token>" http://127.0.0.1:8787/saude
```

### 3. Instalar e ligar o túnel (Cloudflare)

Instale o `cloudflared` pelo site oficial da Cloudflare. Há dois modos de uso.

**Rápido, sem conta.** A URL muda a cada reinício:

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

Ele imprime um endereço `https://<aleatório>.trycloudflare.com`. Serve para
testar, mas cada reinício exige atualizar o segredo na Cloud.

**Fixo, com conta (o modo permanente).** Exige um domínio seu na Cloudflare.
O caminho mais curto é pelo painel, sem `tunnel login`:

1. Zero Trust → Networks → Tunnels → **Create a tunnel** → Cloudflared,
   com o nome `armazem`.
2. O painel mostra um comando com token. Rode-o num PowerShell **como
   administrador**. O executável está em `C:\Program Files (x86)\cloudflared\`:

   ```bash
   cloudflared service install <token-do-painel>
   ```

   Isso registra o `cloudflared` como serviço do Windows. Ele sobe no boot e
   se levanta sozinho se cair. O token é da sua conta: não o cole em chat nem
   em arquivo do repositório.
3. Na aba **Public Hostname**, cadastre `armazem.<seu-dominio>` → `HTTP` →
   `127.0.0.1:8787`.

Com o serviço instalado, o quick tunnel (`cloudflared tunnel --url ...`) deixa
de ser necessário. Feche-o.

### 4. Informar a produção

Em Streamlit Cloud → app → Settings → Secrets, acrescente:

```toml
ARMAZEM_API_URL = "https://armazem.<seu-dominio>"
ARMAZEM_API_TOKEN = "<o mesmo token>"
```

Salvar os segredos reinicia o app.

### 5. Deixar o serviço Python ligado sozinho

O `cloudflared` já sobe sozinho como serviço (passo 3). Para o servidor
Python também subir, use a tarefa de logon **`DFU - Armazem leitura`**.

1. **A pasta do serviço** é um worktree destacado em `origin/main`, separado
   da árvore de trabalho. Assim a branch do dia não derruba a rota nova. Só é
   preciso criá-la uma vez:

   ```bash
   git worktree add --detach ../dfu-armazem-servico origin/main
   ```

2. **Registrar a tarefa** num PowerShell comum, sem precisar de administrador:

   ```bash
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\registrar_armazem_leitura.ps1
   ```

A tarefa roda `scripts/iniciar_armazem_leitura.py` com `pythonw`, sem janela.
Esse supervisor:

- leva a pasta para `origin/main` a cada logon, desde que ela esteja
  destacada e limpa;
- lê o `.env` da árvore principal, que continua sendo o único;
- levanta o servidor de novo em 30 s se ele cair;
- para de vez se o token estiver ausente ou curto.

O log fica em `%LOCALAPPDATA%\DFU\armazem_leitura.log`.

O gatilho é de logon, e não de boot, porque o Docker Desktop só sobe quando
você entra. O servidor não espera o Docker: até o armazém aparecer, ele
responde 503 e o app avisa que caiu na vitrine.

Para subir agora sem reiniciar o PC, feche antes o servidor aberto à mão,
porque a porta 8787 é uma só:

```bash
powershell -Command "Start-ScheduledTask -TaskName 'DFU - Armazem leitura'"
```

## Varredura antes de afirmar lacuna (`/ativo/inventario`)

Regra de 08/11/2026: as análises de empresas do app e das LLMs, quando
encontram lacuna sobre um ativo, varrem o armazém local antes de concluir que
a informação não existe. O ponto único é `core/varredura_armazem.py`, que lê
`core/armazem_inventario.py` direto no desenvolvimento e por esta rota na
produção:

```
GET /ativo/inventario?classe=b3|fii|us&tickers=PETR4,XXXX3   (até 10 tickers)
```

A resposta traz, por ticker e por fonte (pregão, demonstrativos, proventos,
documentos, notícias, Memória de Mercado...), a contagem e o período coberto.
Fonte cujo banco não está configurado nesta máquina sai como `sem_banco`;
consulta que falha sai como `falha` com o motivo, e as outras fontes seguem.
O app formata isso como a seção `VARREDURA DO ARMAZÉM LOCAL` dos prompts, como
a frase da tela "ticker não encontrado" e como sufixo das lacunas que a LLM
declara. Rota nova exige reiniciar a tarefa `DFU - Armazem leitura`; até lá o
prompt diz "não feita (HTTP 404)" e não trata isso como ausência de dado.

## A B3 lida do armazém (`POST /sql`)

Decisão de 15/11/2026: o Supabase não recebe mais a B3, e o app lê a B3 do
armazém. O ponto único é `core/fonte_b3.py::consultar`, chamado por
`core.market_read._q_b3`. Ele escolhe, nesta ordem:

1. banco do app já no armazém (URL em localhost): lê direto;
2. `ARMAZEM_SQL_DB_URL` no ambiente: lê direto com o papel só-leitura;
3. túnel configurado: manda o mesmo SELECT para `POST /sql`;
4. nada disso, ou o degrau escolhido falhou: lê a cópia do Supabase congelada
   em 15/11 e registra o motivo. A tela de Empresas B3 mostra um aviso amarelo
   por até 12 h (o maior cache dos loaders) e o motivo vai para Configurações →
   Restrições (`detalhe.b3.fonte_reserva`). Dashboard Geral, Seleção de FIIs e
   Portfólio Global também leem a B3 por `core.market_read` (o Portfólio Global,
   pelos preços mensais das posições brasileiras) e mostram o mesmo aviso no
   topo, pelo roteador (`app.py::_ROTAS_QUE_LEEM_B3`).

A barreira de segurança da rota é o papel `app4_leitura`, não o filtro do
texto: ele só enxerga `market`, `market_us` e `public.setores`. Crie (ou troque
a senha) rodando, **na árvore principal**:

```
python scripts/criar_papel_leitura_armazem.py --apply
```

A URL vai para o `.env` da árvore principal, que é o único que o serviço lê
(a tarefa passa `--env` apontando para ele; a pasta `dfu-armazem-servico` não
tem `.env`). Rodando de outro worktree, aponte para o mesmo arquivo:
`--gravar-em ..\Dashboard-Financeiro-Unificado\.env`. Gravar em outro lugar
deixa o serviço sem a senha nova, e a rota `POST /sql` falha.

Depois, reinicie a tarefa `DFU - Armazem leitura`. O script também cria a política
`app4_leitura_select` (só SELECT, só para o papel) em cada tabela do escopo
com RLS ligado: o papel é `NOBYPASSRLS`, e GRANT sem política devolve zero
linhas sem erro (em 18/11/2026 eram 24 tabelas, entre elas
`market.b3_cotacao_atual`). No fim ele entra como o papel e lista a tabela que
tem linha para o dono e nenhuma para o papel; tabela que ganhar RLS depois
pede rodar o script de novo. Sem `ARMAZEM_SQL_DB_URL` no
serviço, a rota responde 503 e nunca cai no superusuário. Tetos: 400 mil
linhas, 50 parâmetros, listas de até 5 mil itens. Medido em 18/11/2026 com o
universo inteiro (440 tickers): as quatro leituras pesadas (múltiplos,
histórico de múltiplos, demonstrações e preços mensais) levam de 1,5 a 2,1 s
pelo túnel, contra 20 a 90 s no Supabase, e devolvem o mesmo quadro da leitura
direta.

Ficam fora do papel, e por isso no banco do app: `public.macro` e
`public.docs_corporativos` (no dossiê, `_rows(..., _banco_do_app=True)`).

## Como saber se está funcionando

No chat da Visão Geral, pergunte sobre o cenário. O bloco de contexto mostra
uma destas três linhas:

- `Acervo local, lido pelo túnel (...)`: funcionando.
- `Acervo local pelo túnel: indisponível (...)`: configurado, mas o PC ou o
  túnel está desligado.
- só a vitrine, sem aviso: os segredos não foram configurados.

As leituras pelo túnel ficam em cache por 2 minutos. Ao ligar o PC, a produção
volta a usar o túnel em no máximo 2 minutos.

## Trocar o token

1. Gere um token novo.
2. Atualize o `.env` e reinicie o serviço.
3. Atualize o segredo na Cloud.

Quem tiver o token antigo recebe 401 a partir do reinício.
