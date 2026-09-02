"""
core/config.py
Carregamento e validacao de variaveis de ambiente.

Ordem de prioridade (da maior para a menor):
  1. st.secrets  — Streamlit Cloud (Settings > Secrets) ou .streamlit/secrets.toml local
  2. os.environ  — populado por load_dotenv() do arquivo .env local
  3. valor padrao embutido

Estrategia de banco (Fase 4.0):
  Prioridade de db_url: SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL
  - SUPABASE_UNIFICADO_URL      : projeto Supabase Dashboard Financeiro (banco central)
  - SUPABASE_ORIGEM_CONTROLE_URL: projeto Supabase Controle Financeiro (migracao)
  - SOURCE_DB_APP2              : SQLite do Dashboard-Investimentos (migracao)
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    """
    Le a variavel `key` em ordem de prioridade:
      1. st.secrets (Streamlit Cloud / .streamlit/secrets.toml)
      2. os.environ (populado por load_dotenv do .env local)
      3. default

    Nunca lanca excecao — seguro fora de contexto Streamlit (testes, CLI).
    Valores TOML nao-string (bool, int) sao convertidos para str antes de retornar.
    """
    try:
        import streamlit as st  # importacao local: evita falha em contextos sem Streamlit

        val = st.secrets.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.getenv(key, default)


def _para_num(valor: str, padrao: float, minimo: float | None = None) -> float:
    """
    Converte texto de configuracao em numero, caindo no padrao quando ilegivel.

    Um valor invalido nao pode derrubar o app na importacao do modulo: config
    e lida no import, e uma excecao aqui deixaria a aplicacao inteira sem subir
    por causa de um typo numa variavel opcional.
    """
    try:
        numero = float(str(valor).strip())
    except (TypeError, ValueError):
        return padrao
    if minimo is not None and numero < minimo:
        return padrao
    return numero


class Settings:
    # ── Banco unificado (Dashboard Financeiro — banco central do App 4) ───────
    # Connection string do pooler Supabase (Transaction Mode, porta 6543).
    # Formato: postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres
    SUPABASE_UNIFICADO_URL: str = _get_secret("SUPABASE_UNIFICADO_URL")
    SUPABASE_UNIFICADO_ANON_KEY: str = _get_secret("SUPABASE_UNIFICADO_ANON_KEY")

    # ── Banco de origem (Controle Financeiro — leitura durante migracao) ──────
    # Usado apenas para importar dados historicos. Nunca para gravacao.
    SUPABASE_ORIGEM_CONTROLE_URL: str = _get_secret("SUPABASE_ORIGEM_CONTROLE_URL")
    SUPABASE_ORIGEM_CONTROLE_ANON_KEY: str = _get_secret("SUPABASE_ORIGEM_CONTROLE_ANON_KEY")

    # ── Variaveis legadas (retrocompatibilidade) ──────────────────────────────
    # Mantidas para nao quebrar .env existentes. Preferir SUPABASE_UNIFICADO_URL.
    DATABASE_URL: str = _get_secret("DATABASE_URL")
    SUPABASE_DB_URL: str = _get_secret("SUPABASE_DB_URL")

    # ── Inteligencia artificial ───────────────────────────────────────────────
    OPENAI_API_KEY: str = _get_secret("OPENAI_API_KEY")
    AI_PROVIDER: str = _get_secret("AI_PROVIDER", "openai")
    AI_MODEL: str = _get_secret("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_S: int = int(_get_secret("AI_TIMEOUT_S", "45"))
    AI_MAX_RETRIES: int = int(_get_secret("AI_MAX_RETRIES", "2"))

    # ── OpenRouter / Nemotron (provedor primário) ─────────────────────────────
    # Substitui a OpenAI como primeiro da cadeia. A troca não foi feita por
    # reputação do modelo: `scripts/avaliar_provedor_llm.py` rodou o prompt de
    # produção contra dois casos-armadilha com resposta certa conhecida e o
    # `nemotron-3-super-120b` acertou o julgamento em 6/6, manteve o schema JSON
    # em 6/6 e respondeu em 17–48 s. O `ultra-550b` também acerta o julgamento,
    # mas leva 97–212 s e estouraria o timeout de 90 s da tela — por isso o
    # padrão é o super, e não o modelo maior.
    OPENROUTER_API_KEY: str = _get_secret("OPENROUTER_API_KEY")
    OPENROUTER_MODEL: str = _get_secret(
        "OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    OPENROUTER_REPORT_MODEL: str = _get_secret(
        "OPENROUTER_REPORT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    OPENROUTER_BASE_URL: str = _get_secret(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    # ── Gemini (provedor de fallback) ─────────────────────────────────────────
    # Usado automaticamente quando a OpenAI falha (ex.: cota 429). Acessado via
    # endpoint compatível com a API da OpenAI, então reaproveita o mesmo SDK.
    # Aceita GEMINI_API_KEY ou GOOGLE_API_KEY.
    GEMINI_API_KEY: str = _get_secret("GEMINI_API_KEY") or _get_secret("GOOGLE_API_KEY")
    GEMINI_MODEL: str = _get_secret("GEMINI_MODEL", "gemini-3.6-flash")
    GEMINI_BASE_URL: str = _get_secret(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

    # ── Empresas Americanas — fonte de dados ──────────────────────────────────
    # FONTE PADRÃO: SEC EDGAR (dados públicos, de domínio público) p/ fundamentos
    # + yfinance p/ preços. Escolhida após a leitura dos Termos da FMP, que
    # proíbem cópia/armazenamento sem autorização escrita e exigem apagar tudo
    # (inclusive cache) ao encerrar a assinatura — incompatível com o warehouse
    # local. Ver docs/empresas_americanas.md.
    #
    # A SEC EXIGE User-Agent identificando o consumidor, com e-mail de contato,
    # senão responde 403. NÃO é segredo (é identificação), mas é obrigatório.
    # Formato: "Seu Nome seu@email.com"
    SEC_USER_AGENT: str = _get_secret("SEC_USER_AGENT")

    # FMP: opcional e DESATIVADA por padrão. Só use com licença compatível com
    # armazenamento local. A chave, se existir, é usada APENAS pela ingestão —
    # NUNCA pela view — e jamais vai para banco, log ou UI.
    FMP_API_KEY: str = _get_secret("FMP_API_KEY") or _get_secret("FINANCIAL_MODELING_PREP_API_KEY")
    FMP_BASE_URL: str = _get_secret("FMP_BASE_URL", "https://financialmodelingprep.com/api")

    # Fonte de fundamentos: 'edgar' (padrão) | 'fmp'
    US_FUNDAMENTALS_SOURCE: str = _get_secret("US_FUNDAMENTALS_SOURCE", "edgar")

    # ── Motor Conjuntural de notícias ─────────────────────────────────────────
    # Chaves dos provedores. TODAS opcionais: sem nenhuma delas o motor ainda
    # funciona pelos feeds RSS, que não exigem credencial. A chave viaja em
    # query string nas duas APIs, então nada em core/noticias registra a URL
    # montada — só o nome do provedor e o status (ver transporte.Redator).
    ALPHAVANTAGE_API_KEY: str = _get_secret("ALPHAVANTAGE_API_KEY") or _get_secret(
        "ALPHA_VANTAGE_API_KEY")
    MARKETAUX_API_KEY: str = _get_secret("MARKETAUX_API_KEY")
    FINNHUB_API_KEY: str = _get_secret("FINNHUB_API_KEY")

    # Ordem de tentativa. O primeiro que responder atende; os seguintes só
    # entram quando o anterior falha ou está sem cota.
    NOTICIAS_PROVEDORES: str = _get_secret(
        "NOTICIAS_PROVEDORES", "alphavantage,marketaux,rss")

    # Validade do cache de resposta, em minutos.
    NOTICIAS_CACHE_TTL_MIN: str = _get_secret("NOTICIAS_CACHE_TTL_MIN", "15")

    # Cadência de coleta: normal e em regime de emergência (crise detectada).
    NOTICIAS_FREQ_NORMAL_MIN: str = _get_secret("NOTICIAS_FREQ_NORMAL_MIN", "240")
    NOTICIAS_FREQ_EMERGENCIA_MIN: str = _get_secret(
        "NOTICIAS_FREQ_EMERGENCIA_MIN", "30")

    # Idade a partir da qual a notícia deixa de ser tratada como corrente.
    # Ela continua no acervo; o que muda é que passa a ser exibida como
    # histórico datado, nunca como notícia atual.
    NOTICIAS_IDADE_MAX_HORAS: str = _get_secret("NOTICIAS_IDADE_MAX_HORAS", "72")

    # Itens pedidos por consulta a cada provedor.
    NOTICIAS_LIMITE_POR_CONSULTA: str = _get_secret(
        "NOTICIAS_LIMITE_POR_CONSULTA", "50")

    # ── Ambiente ──────────────────────────────────────────────────────────────
    APP_ENV: str = _get_secret("APP_ENV", "development")
    MOCK_MODE: bool = _get_secret("MOCK_MODE", "true").lower() == "true"

    # ── Autenticacao simples (Streamlit Cloud) ────────────────────────────────
    # Texto simples ou hash SHA-256 da senha. Vazio = sem senha (dev local).
    # Gerar hash: python -c "import hashlib; print(hashlib.sha256(b'senha').hexdigest())"
    APP_PASSWORD: str = _get_secret("APP_PASSWORD")

    # ── Usuario proprietario dos dados ────────────────────────────────────────
    # UUID do usuario na tabela `usuarios`. Todas as queries filtram por este ID.
    OWNER_USER_ID: str = _get_secret("OWNER_USER_ID")

    # ── Fontes de importacao (apps originais — somente leitura) ──────────────
    SOURCE_DB_APP1: str = _get_secret("SOURCE_DB_APP1")  # Dashboard (PostgreSQL)
    SOURCE_DB_APP2: str = _get_secret("SOURCE_DB_APP2")  # Dashboard-Investimentos (SQLite)
    SOURCE_DB_APP3: str = _get_secret("SOURCE_DB_APP3")  # Controle_Financeiro (alias legado)

    # ── Propriedades derivadas ────────────────────────────────────────────────

    @property
    def db_url(self) -> str:
        """
        Retorna a URL do banco unificado.
        Prioridade: SUPABASE_UNIFICADO_URL > DATABASE_URL > SUPABASE_DB_URL
        """
        return (
            self.SUPABASE_UNIFICADO_URL
            or self.DATABASE_URL
            or self.SUPABASE_DB_URL
            or ""
        )

    @property
    def url_origem_controle(self) -> str:
        """
        URL do Supabase Controle Financeiro (fonte temporaria de migracao).
        SUPABASE_ORIGEM_CONTROLE_URL tem prioridade; SOURCE_DB_APP3 e alias legado.
        """
        return self.SUPABASE_ORIGEM_CONTROLE_URL or self.SOURCE_DB_APP3

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def has_database(self) -> bool:
        return bool(self.db_url)

    @property
    def has_supabase_unificado(self) -> bool:
        """True se SUPABASE_UNIFICADO_URL estiver configurada (banco unificado ativo)."""
        return bool(self.SUPABASE_UNIFICADO_URL)

    @property
    def has_fmp(self) -> bool:
        """True se a chave da Financial Modeling Prep estiver configurada.

        Fonte OPCIONAL (padrão é a SEC EDGAR). Só a ingestão precisa dela.
        """
        return bool(self.FMP_API_KEY)

    @property
    def has_sec_user_agent(self) -> bool:
        """True se SEC_USER_AGENT estiver configurado (exigido pela SEC)."""
        return bool(self.SEC_USER_AGENT)

    @property
    def us_source(self) -> str:
        """Fonte de fundamentos EUA: 'edgar' (padrão) ou 'fmp'."""
        src = (self.US_FUNDAMENTALS_SOURCE or "edgar").strip().lower()
        return src if src in ("edgar", "fmp") else "edgar"

    @property
    def us_ingest_ready(self) -> bool:
        """True se a fonte escolhida tem o que precisa para ingerir."""
        return self.has_fmp if self.us_source == "fmp" else self.has_sec_user_agent

    @property
    def has_openai(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def has_gemini(self) -> bool:
        return bool(self.GEMINI_API_KEY)

    @property
    def has_openrouter(self) -> bool:
        return bool(self.OPENROUTER_API_KEY)

    @property
    def has_llm(self) -> bool:
        """True se houver ao menos um provedor LLM configurado."""
        return bool(self.OPENROUTER_API_KEY or self.OPENAI_API_KEY
                    or self.GEMINI_API_KEY)

    @property
    def has_owner(self) -> bool:
        """True se OWNER_USER_ID estiver configurado (necessario para queries reais)."""
        return bool(self.OWNER_USER_ID)

    @property
    def has_origem_controle(self) -> bool:
        """True se a URL de origem do Controle Financeiro estiver configurada."""
        return bool(self.url_origem_controle)

    @property
    def has_source_app1(self) -> bool:
        return bool(self.SOURCE_DB_APP1)

    @property
    def has_source_app2(self) -> bool:
        return bool(self.SOURCE_DB_APP2)

    @property
    def has_source_app3(self) -> bool:
        return bool(self.SOURCE_DB_APP3)

    # ── Motor Conjuntural: derivadas ──────────────────────────────────────────

    @property
    def provedores_noticias(self) -> list[str]:
        """Ordem de fallback dos provedores, já normalizada."""
        return [p.strip().lower()
                for p in (self.NOTICIAS_PROVEDORES or "").split(",")
                if p.strip()]

    @property
    def has_noticias_com_chave(self) -> bool:
        """True se ao menos um provedor com credencial estiver configurado."""
        return bool(self.ALPHAVANTAGE_API_KEY or self.MARKETAUX_API_KEY
                    or self.FINNHUB_API_KEY)

    @property
    def noticias_cache_ttl_s(self) -> float:
        return _para_num(self.NOTICIAS_CACHE_TTL_MIN, 15.0, minimo=0.0) * 60.0

    @property
    def noticias_freq_normal_min(self) -> float:
        return _para_num(self.NOTICIAS_FREQ_NORMAL_MIN, 240.0, minimo=1.0)

    @property
    def noticias_freq_emergencia_min(self) -> float:
        return _para_num(self.NOTICIAS_FREQ_EMERGENCIA_MIN, 30.0, minimo=1.0)

    @property
    def noticias_idade_max_horas(self) -> float:
        return _para_num(self.NOTICIAS_IDADE_MAX_HORAS, 72.0, minimo=1.0)

    @property
    def noticias_limite(self) -> int:
        return int(_para_num(self.NOTICIAS_LIMITE_POR_CONSULTA, 50.0, minimo=1.0))

    def chave_noticias(self, provedor: str) -> str:
        """Credencial do provedor, ou string vazia para quem não usa chave."""
        return {
            "alphavantage": self.ALPHAVANTAGE_API_KEY,
            "marketaux": self.MARKETAUX_API_KEY,
            "finnhub": self.FINNHUB_API_KEY,
        }.get((provedor or "").split(":", 1)[0].lower(), "")

    def validate(self) -> list[str]:
        """
        Retorna lista de avisos sobre variaveis ausentes.
        Nao lanca excecao — o app decide como tratar.
        """
        warnings = []
        if not self.has_database:
            warnings.append(
                "Banco de dados nao configurado — defina SUPABASE_UNIFICADO_URL "
                "no .env local ou em Streamlit Secrets (Settings > Secrets)."
            )
        if not self.has_llm:
            # Avisar pela ausencia de OPENAI_API_KEY especificamente diria "IA
            # desativada" para quem roda so com OpenRouter — um alarme falso que
            # convida a comprar credito sem necessidade.
            warnings.append(
                "Nenhum provedor LLM configurado (OPENROUTER_API_KEY, "
                "OPENAI_API_KEY ou GEMINI_API_KEY) — IA desativada.")
        if self.MOCK_MODE:
            warnings.append("MOCK_MODE=true — dados mockados em uso.")
        if not self.has_owner and not self.MOCK_MODE:
            warnings.append("OWNER_USER_ID nao configurado — filtro de usuario inativo.")
        if not self.has_noticias_com_chave:
            # Aviso, nao erro: sem chave o Motor Conjuntural ainda coleta pelos
            # feeds RSS. O que se perde e o sentimento e a cobertura dos EUA, e
            # a tela precisa poder dizer isso em vez de mostrar lista curta sem
            # explicacao.
            warnings.append(
                "Nenhuma chave de noticias configurada (ALPHAVANTAGE_API_KEY ou "
                "MARKETAUX_API_KEY) — Motor Conjuntural limitado aos feeds RSS.")
        return warnings


settings = Settings()
