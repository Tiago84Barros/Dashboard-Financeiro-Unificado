# Deploy — Dashboard Financeiro Unificado

> Gerado em: 2026-05-13
> Versão do app: Fase 3 concluída (mock data, dashboard completo)

---

## ⚠️ Por Que a Vercel Não Se Aplica a Este Projeto

Este documento foi criado a partir de um checklist de deploy Vercel. Antes de prosseguir,
é fundamental entender por que a Vercel **não é compatível** com este projeto e qual é
a plataforma correta.

### Diagnóstico Técnico

| Item verificado          | Esperado (Vercel)         | Encontrado neste projeto          |
|--------------------------|---------------------------|-----------------------------------|
| `package.json`           | ✅ Obrigatório             | ❌ Não existe                      |
| Framework JS/TS          | Next.js, Vite, React…     | ❌ Python 3.9 + Streamlit 1.39.0  |
| Comando de build         | `npm run build`           | ❌ Não existe (Python não compila) |
| Diretório de saída       | `.next/` ou `dist/`       | ❌ Não existe                      |
| Arquivo de entrada       | `pages/index.tsx` etc.    | `app.py` (Python)                 |
| Servidor                 | Node.js / Edge Runtime    | Python + WebSocket (Streamlit)    |

### Por Que Streamlit ≠ Vercel

A Vercel executa **funções serverless stateless** com timeout de 10–300 s.
O Streamlit requer um **processo servidor de longa duração** com:
- Conexão **WebSocket persistente** entre browser e servidor
- Estado de sessão mantido em memória (`st.session_state`)
- Servidor `asyncio` próprio que não é WSGI/ASGI compatível

Mesmo o suporte Python da Vercel (via `api/` functions) **não resolve** isso —
ele executa scripts isolados, sem servidor WebSocket.

---

## ✅ Stack Identificada

```
Linguagem:   Python 3.9
Framework:   Streamlit 1.39.0
Banco:       PostgreSQL via SQLAlchemy 2.0 (Fase 4+)
IA:          OpenAI API gpt-4o-mini (Fase 8+)
Cotações:    yfinance 0.2.26 (Fase 5+)
Linter:      ruff
Entry point: app.py
Config:      .streamlit/config.toml
Deps:        requirements.txt
```

---

## Plataforma Recomendada: Streamlit Community Cloud

**URL:** https://share.streamlit.io
**Custo:** Gratuito (1 app público, recursos modestos)
**Requisitos:** Conta GitHub + repositório público ou privado

### Por Que é a Escolha Certa para Este Projeto

| Critério               | Streamlit Community Cloud        |
|------------------------|----------------------------------|
| Suporte ao stack       | ✅ Oficial — feito para Streamlit |
| Configuração           | ✅ Zero (detecta `requirements.txt` e `app.py`) |
| Variáveis de ambiente  | ✅ Interface UI para secrets      |
| `.streamlit/config.toml` | ✅ Lido automaticamente          |
| Custo                  | ✅ Gratuito                       |
| CI/CD                  | ✅ Redeploy automático no push    |
| Domínio                | `seu-app.streamlit.app`          |

---

## Comando de Instalação

```bash
# Desenvolvimento local
pip install -r requirements.txt

# Não há "npm install" — Python usa pip
```

Todas as dependências já estão instaladas e dentro das faixas especificadas:

| Pacote             | Versão local | Faixa em requirements.txt      |
|--------------------|:------------:|--------------------------------|
| streamlit          | 1.39.0       | `>=1.39.0,<2.0.0` ✅           |
| pandas             | 2.2.3        | `>=2.2.0,<3.0.0` ✅            |
| plotly             | 5.24.1       | `>=5.24.0,<6.0.0` ✅           |
| sqlalchemy         | 2.0.35       | `>=2.0.0,<3.0.0` ✅            |
| psycopg2-binary    | 2.9.12       | `>=2.9.0,<3.0.0` ✅            |
| python-dotenv      | 1.0.1        | `>=1.0.0,<2.0.0` ✅            |
| openai             | 1.63.2       | `>=1.63.0,<2.0.0` ✅           |
| yfinance           | 0.2.26       | `>=0.2.26,<0.3.0` ✅           |
| requests           | 2.32.3       | `>=2.32.0,<3.0.0` ✅           |

**Dependências faltando:** nenhuma. ✅

---

## Comando de Build

**Não existe.** Streamlit não tem etapa de compilação.
O equivalente funcional ao `npm run build` é:

```bash
# 1. Lint (equivalente ao eslint)
python -m ruff check .

# 2. Verificação de sintaxe (equivalente ao tsc --noEmit)
python -m py_compile app.py

# 3. Startup test (equivalente ao next build)
streamlit run app.py --server.headless true --server.port 8502
```

---

## Diretório de Saída

**Não existe.** Streamlit não gera assets estáticos.
O app é servido diretamente pelo servidor Python em runtime.

Estrutura que vai para o deploy (o repositório completo):
```
Dashboard-Financeiro-Unificado/
├── app.py                    ← entry point
├── requirements.txt          ← dependências
├── .streamlit/
│   └── config.toml           ← tema + configuração do servidor
├── core/
│   ├── config.py
│   ├── database.py
│   ├── financeiro.py
│   ├── mock_data.py
│   └── utils.py
├── design/
│   ├── componentes.py
│   └── tema.py
└── pages/
    ├── dashboard_geral.py
    └── ... (10 pages)
```

---

## Variáveis de Ambiente

### Fase 3 (estado atual — dados mockados)

Apenas uma variável é **necessária** agora. As demais têm padrão seguro:

| Variável     | Valor para deploy | Padrão se ausente | Obrigatória agora? |
|--------------|:-----------------:|:-----------------:|:------------------:|
| `MOCK_MODE`  | `"true"`          | `"true"` ✅       | Não (já é o padrão) |

> `core/config.py` linha 26: `os.getenv("MOCK_MODE", "true").lower() == "true"`
> O app funciona sem nenhuma variável configurada na Fase 3.

### Fase 4 — Integração Supabase (futuro)

| Variável          | Descrição                              | Obrigatória |
|-------------------|----------------------------------------|:-----------:|
| `DATABASE_URL`    | `postgresql://user:pass@host:5432/db`  | ✅ Sim       |
| `SUPABASE_DB_URL` | Alternativa a DATABASE_URL (Supabase)  | Se não usar DATABASE_URL |
| `MOCK_MODE`       | Mudar para `"false"` na Fase 4         | ✅ Sim       |

> ⚠️ **Segurança:** `DATABASE_URL` direto ao PostgreSQL do Supabase bypassa RLS.
> Avaliar uso do `supabase-py` + service_role_key (decisão D01).

### Fase 8 — Módulo de IA (futuro)

| Variável       | Descrição                       | Obrigatória |
|----------------|---------------------------------|:-----------:|
| `OPENAI_API_KEY` | Chave da API OpenAI           | ✅ Sim       |
| `AI_MODEL`     | `gpt-4o-mini` (padrão já setado) | Opcional   |
| `AI_TIMEOUT_S` | Timeout em segundos (padrão: 45) | Opcional   |

### Variáveis completas para referência

```toml
# .streamlit/secrets.toml  (NÃO committar — já no .gitignore)
# Equivalente ao .env mas no formato Streamlit Community Cloud

DATABASE_URL = "postgresql://usuario:senha@host:5432/database"
SUPABASE_DB_URL = "postgresql://usuario:senha@host:5432/database"
OPENAI_API_KEY = "sk-..."
AI_PROVIDER = "openai"
AI_MODEL = "gpt-4o-mini"
AI_TIMEOUT_S = "45"
AI_MAX_RETRIES = "2"
APP_ENV = "production"
MOCK_MODE = "false"
```

> **Nota:** `core/config.py` usa `os.getenv()` que é compatível com os secrets do
> Streamlit Community Cloud (eles são injetados como variáveis de ambiente).

---

## Passo a Passo: Publicar na Streamlit Community Cloud

### Pré-requisitos
- [ ] Repositório no GitHub (público ou privado)
- [ ] Conta em https://share.streamlit.io (gratuita, login com GitHub)

### Passo 1 — Verificar que o repositório está pronto

Confirmar que estes arquivos existem no branch `main`:
- `app.py` ← entry point principal
- `requirements.txt` ← dependências Python
- `.streamlit/config.toml` ← tema dark + headless=true ✅ (já existe)

**Verificar que estes arquivos NÃO estão commitados:**
- `.env` ← está no `.gitignore` ✅
- `.streamlit/secrets.toml` ← está no `.gitignore` ✅

### Passo 2 — Criar o app na Streamlit Community Cloud

1. Acesse https://share.streamlit.io
2. Clique em **"New app"**
3. Preencha:
   - **Repository:** `Tiago84Barros/Dashboard-Financeiro-Unificado`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** `dashboard-financeiro` (ou o slug desejado)
4. Clique em **"Deploy!"**

### Passo 3 — Configurar variáveis de ambiente (Fase 3)

Na Fase 3 (mock data), não é necessário nenhum secret.
O app roda sem configuração adicional.

Para fases futuras:
1. No painel do app → clique em **"⋮" → Settings**
2. Vá na aba **"Secrets"**
3. Cole o conteúdo do `.streamlit/secrets.toml` local

### Passo 4 — Verificar o deploy

- O Community Cloud mostra logs em tempo real durante o boot
- Boot time esperado: 30–90 segundos (instalação de dependências na 1ª vez)
- URL final: `https://seu-app.streamlit.app`

### Passo 5 — Atualizações futuras

```bash
# Toda vez que fizer push para main, o app é redeploy automaticamente
git push origin main
```

---

## Alternativas de Deploy (se Community Cloud não for suficiente)

### Railway (recomendado para produção)
```bash
# Procfile (criar na raiz)
web: streamlit run app.py --server.port $PORT --server.address 0.0.0.0

# Ou railway.json
{
  "build": { "builder": "NIXPACKS" },
  "deploy": { "startCommand": "streamlit run app.py --server.port $PORT --server.address 0.0.0.0" }
}
```
**Custo:** ~$5/mês | **Vantagem:** mais recursos, variáveis de env via UI

### Render (free tier disponível)
- New Web Service → selecionar repositório Python
- Start Command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
- **Custo:** Grátis (com sleep após inatividade) ou $7/mês

### Docker (qualquer cloud com container)
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

---

## Checklist de Prontidão para Deploy

| Critério                                              | Status |
|-------------------------------------------------------|:------:|
| `requirements.txt` com versões fixadas                | ✅     |
| `.env` no `.gitignore`                                | ✅     |
| `.streamlit/secrets.toml` no `.gitignore`             | ✅     |
| `headless = true` em `.streamlit/config.toml`         | ✅     |
| Nenhuma credencial hardcoded no código                | ✅     |
| Graceful degradation sem `DATABASE_URL`               | ✅     |
| Graceful degradation sem `OPENAI_API_KEY`             | ✅     |
| `MOCK_MODE` default `"true"` — app funciona sem .env  | ✅     |
| `ruff check .` → zero erros                           | ✅     |
| Startup test → HTTP 200                               | ✅     |
| Sem `package.json` / toolchain JS                     | N/A    |
| Vercel deploy                                         | ❌ Incompatível com este stack |
| Streamlit Community Cloud deploy                      | ✅ Plataforma correta          |
