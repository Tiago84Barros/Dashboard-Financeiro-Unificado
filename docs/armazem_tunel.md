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

- **Expõe:** notícias avaliadas e as últimas observações macro. É dado público.
- **Não expõe:** finanças, carteira, cartão e o espelho do Supabase. Nenhuma
  rota lê essas tabelas.
- **Só GET.** A sessão do Postgres abre com `default_transaction_read_only=on`.
- **Só `127.0.0.1`.** Quem expõe o serviço é o túnel, e só ele.
- **Token de 32+ caracteres obrigatório**, comparado em tempo constante.
- **Tetos por chamada:** até 500 notícias e até 30 dias.

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

**Fixo, com conta.** Exige um domínio seu na Cloudflare:

```bash
cloudflared tunnel login
cloudflared tunnel create armazem
cloudflared tunnel route dns armazem armazem.<seu-dominio>
cloudflared tunnel run --url http://127.0.0.1:8787 armazem
```

### 4. Informar a produção

Em Streamlit Cloud → app → Settings → Secrets, acrescente:

```toml
ARMAZEM_API_URL = "https://armazem.<seu-dominio>"
ARMAZEM_API_TOKEN = "<o mesmo token>"
```

Salvar os segredos reinicia o app.

### 5. Deixar ligado sozinho (opcional)

Tanto o serviço quanto o `cloudflared` podem ir para o Agendador de Tarefas,
com gatilho de logon, como a rotina noturna. O `cloudflared` também se instala
como serviço do Windows (`cloudflared service install`).

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
