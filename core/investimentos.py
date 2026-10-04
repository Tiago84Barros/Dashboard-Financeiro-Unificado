"""
core/investimentos.py
Camada de serviço de investimentos — posições da carteira, alocação e custo médio.

Política de origem:
  MOCK_MODE=true  → retorna dados mockados de forma intencional
  MOCK_MODE=false → usa somente dados reais; falhas permanecem explícitas

Tabelas consultadas (Fase 5.1):
  portfolio_positions  — posições atuais (qty, preço médio, total investido)
  assets               — ticker, nome, classe (class), setor, moeda
  asset_quotes         — cotação mais recente via LATERAL join (NULL quando vazia)

Query principal:
  SELECT pp.quantity, pp.average_price, pp.total_invested,
         a.ticker, a.name, a.class AS asset_class, a.sector, a.currency,
         aq.close AS current_price
  FROM   portfolio_positions pp
  JOIN   assets a ON a.id = pp.asset_id
  LEFT JOIN LATERAL (
      SELECT close FROM asset_quotes
      WHERE  asset_id = pp.asset_id
      ORDER  BY timestamp DESC LIMIT 1
  ) aq ON true
  WHERE  pp.user_id = :uid
  ORDER  BY pp.total_invested DESC

Chave "data_source" sempre presente no dict retornado:
  "real"  → dados do banco
  "mock"  → MOCK_MODE=true, mock intencional
  "error" → fonte real indisponível, sem substituição sintética

Schema do dict retornado por get_carteira():
  data_source              str   "real" | "mock" | "error"
  total_investido          float  Custo histórico total (qty × avg_price)
  total_mercado            float  Valor de mercado (= total_investido sem cotações)
  rentabilidade_total_pct  float  Rentabilidade total (0.0 sem cotações)
  num_ativos               int    Número de posições ativas
  cotacoes_disponiveis     bool   True quando asset_quotes estiver populado
  posicoes                 list   Ver _POSICAO_SCHEMA abaixo
  por_classe               list   Ver _CLASSE_SCHEMA abaixo
  por_setor                list   Ver _SETOR_SCHEMA abaixo

_POSICAO_SCHEMA: { ticker, nome, classe, setor, moeda,
                   quantidade, preco_medio, total_investido,
                   preco_atual, valor_mercado, rentab_pct, pct_carteira, cor }

_CLASSE_SCHEMA:  { nome, valor_mercado, total_investido,
                   pct_carteira, num_ativos, rentab_pct, cor }

_SETOR_SCHEMA:   { nome, valor_mercado, pct_carteira }
"""
import logging
import re
from collections import defaultdict

from core.categorias import SQL_INVESTIMENTO
from core.config import settings
from core.currency_returns import retorno_em_brl, retorno_moeda_origem
from core.fx_aquisicao import cambio_medio_de_aquisicao, taxa_para
from core.market_freshness import classificar_cotacao, intervalo_referencia
from core.precos_medios_manuais import listar as listar_precos_manuais
from core.tesouro_nomes import nome_amigavel
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

# ── Mapeamentos de classe de ativo ────────────────────────────────────────────
_CLASS_LABEL: dict[str, str] = {
    "reit":         "FII",
    "fii":          "FII",
    "stock":        "Ações BR",
    "fixed_income": "Renda Fixa",
    "renda_fixa":   "Renda Fixa",
    "tesouro":      "Tesouro Direto",
    "fundo_rf":     "Fundo RF",
    "fip":          "FIP",
    "etf":          "ETF",
    "etf_br":       "ETF Brasil",
    "etf_intl":     "ETF Internacional",
    "bdr":          "BDR",
    "crypto":       "Cripto",
    "other":        "Outros",
}

_CLASS_COR: dict[str, str] = {
    "reit":         "#E84C9B",
    "fii":          "#E84C9B",
    "stock":        "#4C9BE8",
    "fixed_income": "#A855F7",
    "renda_fixa":   "#A855F7",
    "tesouro":      "#2ECC71",
    "fundo_rf":     "#7C3AED",
    "fip":          "#C084FC",
    "etf":          "#F5A623",
    "etf_br":       "#F5A623",
    "etf_intl":     "#63cab7",
    "bdr":          "#4C9BE8",
    "crypto":       "#FF6B35",
    "other":        "#8b9ab0",
}

_SETOR_LABEL: dict[str, str] = {
    "real_estate":      "Imóveis / FII",
    "financials":       "Financeiro",
    "utilities":        "Utilidades",
    "energy":           "Energia",
    "materials":        "Materiais",
    "industrials":      "Industrial",
    "consumer":         "Consumo",
    "consumer_staples": "Consumo Básico",
    "health_care":      "Saúde",
    "technology":       "Tecnologia",
    "telecom":          "Telecom",
    "other":            "Outros",
}

# Posições mock baseadas nas 34 posições reais do banco (subconjunto de 20)
# Formato: (ticker, nome, classe, setor, moeda, qty, preco_medio, total_investido)
_MOCK_POSICOES_RAW: list[tuple] = [
    ("BITH11",  "BTG Pactual Infra FII",         "reit",  "real_estate", "BRL", 190,    137.87, 26_195.40),
    ("PSSA3",   "Porto Seguro",                   "stock", "financials",  "BRL", 643,     35.15, 22_598.69),
    ("EQTL3F",  "Equatorial Energia",             "stock", "utilities",   "BRL", 591,     24.25, 14_329.72),
    ("MXRF15",  "Maxi Renda FII",                 "stock", "real_estate", "BRL", 1352,    10.29, 13_912.08),
    ("BBAS3F",  "Banco do Brasil",                "stock", "financials",  "BRL", 339,     33.30, 11_289.32),
    ("BRCO11",  "Bresco Logística FII",           "reit",  "real_estate", "BRL",  60,    117.52,  7_051.36),
    ("HGLG11",  "CSHG Logística FII",             "reit",  "real_estate", "BRL",  44,    157.06,  6_910.78),
    ("KNCR11",  "Kinea CR FII",                   "reit",  "real_estate", "BRL",  64,    106.25,  6_799.76),
    ("VISC11",  "Vinci Shopping Centers FII",     "reit",  "real_estate", "BRL",  62,    108.85,  6_748.58),
    ("PETR3F",  "Petrobras PN",                   "stock", "energy",      "BRL", 230,     28.78,  6_620.29),
    ("GMAT3",   "Getnet S.A.",                    "stock", "financials",  "BRL", 4600,     1.40,  6_448.42),
    ("TRPL3F",  "CTEEP",                          "stock", "utilities",   "BRL", 200,     29.19,  5_838.32),
    ("ROMI3",   "Romi S.A.",                      "stock", "industrials", "BRL", 602,      8.38,  5_042.49),
    ("ITUB3F",  "Itaú Unibanco",                  "stock", "financials",  "BRL", 174,     25.37,  4_414.29),
    ("HGRE11",  "Pátria Escritórios FII",          "reit",  "real_estate", "BRL",  30,    120.00,  3_600.00),
    ("SBSP3",   "Sabesp",                         "stock", "utilities",   "BRL", 118,     33.87,  3_997.22),
    ("CSMG3F",  "COPASA",                         "stock", "utilities",   "BRL",  94,     40.83,  3_837.85),
    ("BRAP3",   "Bradespar",                      "stock", "materials",   "BRL", 206,     18.47,  3_804.64),
    ("ISAE3",   "Isa Energia",                    "stock", "utilities",   "BRL", 102,     33.15,  3_381.20),
    ("BRAP3F",  "Bradespar PN",                   "stock", "materials",   "BRL", 206,     18.47,  3_804.64),
]


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

@user_cache_data(ttl=300)
def get_carteira() -> dict:
    """
    Retorna o dicionário completo de dados para a página Carteira.
    Cache de 5 minutos — mesma estratégia do get_visao_geral().

    Retorna dados mockados se MOCK_MODE=true.
    Tenta banco real se MOCK_MODE=false; falhas retornam estado vazio explícito.
    """
    if settings.MOCK_MODE:
        dados = _carteira_mock()
        dados["data_source"] = "mock"
        return dados

    try:
        dados = _carteira_real()
        dados["data_source"] = "real"
        return dados
    except Exception as exc:
        logger.warning(
            "[investimentos] Banco real indisponível (%s).",
            type(exc).__name__,
        )
        return {
            "data_source": "error",
            "error_message": "Não foi possível carregar a carteira real.",
            "total_investido": 0.0,
            "total_mercado": 0.0,
            "diferenca_reais": 0.0,
            "rentabilidade_total_pct": 0.0,
            "rentabilidade_total_disponivel": False,
            "num_ativos": 0,
            "cotacoes_disponiveis": False,
            "n_cotacoes_live": 0,
            "posicoes": [],
            "por_classe": [],
            "por_setor": [],
            "avisos_dados": ["Carteira indisponível; nenhum dado sintético foi substituído."],
        }


_TIPOS_TESOURO = {"tesouro", "fixed_income", "renda_fixa"}


def _nome_exibicao(nome: str | None, ticker: str, asset_type: str | None) -> str:
    """Nome do ativo como a tela mostra.

    So o Tesouro passa pela traducao, e de proposito: `nome_amigavel` decide
    pelo prefixo do texto, e uma empresa chamada "LFT Participacoes" viraria
    "Tesouro Selic" se a regra valesse para todo mundo.

    A traducao acontece aqui ALEM de na importacao porque `get_or_create_asset`
    nao reescreve `assets.name` de um ticker que ja existe: as linhas ja
    gravadas como "LFT mar/2031" continuariam saindo em codigo para sempre.
    """
    base = (nome or "").strip() or ticker
    if (asset_type or "").strip().lower() in _TIPOS_TESOURO:
        return nome_amigavel(base)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _carteira_mock() -> dict:
    """Constrói o dict de carteira a partir de dados mock estáticos."""
    total_inv = sum(r[7] for r in _MOCK_POSICOES_RAW)

    posicoes = []
    for row in _MOCK_POSICOES_RAW:
        ticker, nome, classe_raw, setor_raw, moeda, qty, pm, ti = row
        pct = ti / total_inv * 100 if total_inv > 0 else 0.0
        posicoes.append({
            "ticker":          ticker,
            "nome":            nome,
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "setor":           _SETOR_LABEL.get(setor_raw, setor_raw.title()),
            "moeda":           moeda,
            "quantidade":      float(qty),
            "preco_medio":     round(float(pm), 6),
            "total_investido": round(float(ti), 2),
            "preco_atual":     round(float(pm), 6),    # sem cotações = preço médio
            "valor_mercado":   round(float(ti), 2),    # sem cotações = custo histórico
            "rentab_pct":      0.0,
            "rentab_moeda":    "BRL",
            "rentab_brl_pct":  0.0,
            "retorno_brl_disponivel": True,
            "pct_carteira":    pct,
            "cor":             _CLASS_COR.get(classe_raw, "#718096"),
        })

    return {
        "total_investido":         round(total_inv, 2),
        "total_mercado":           round(total_inv, 2),
        "rentabilidade_total_pct": 0.0,
        "rentabilidade_total_disponivel": True,
        "num_ativos":              len(posicoes),
        "cotacoes_disponiveis":    False,
        "posicoes":                posicoes,
        "por_classe":              _agregar_por_classe(posicoes),
        "por_setor":               _agregar_por_setor(posicoes),
        # data_source injetado pelo caller
    }


# ─────────────────────────────────────────────────────────────────────────────
# REAL — queries nas tabelas do Supabase
# ─────────────────────────────────────────────────────────────────────────────

# SQL isolado para facilitar manutenção e testes.
# fx.close traz a cotação USD/BRL mais recente do ativo sintético USDBRL
# populado por `data_pipeline/jobs/update_fx_rates.py`. Quando o ativo
# da posição é em USD, a camada Python multiplica os valores por essa
# cotação. Quando o ativo USDBRL ainda não existe (banco novo), o LEFT JOIN
# devolve NULL e o Python usa rate=1.0 (sem conversão).
_SQL_POSICOES = """
    SELECT
        pp.quantity,
        pp.average_price,
        pp.total_invested,
        a.ticker,
        a.name          AS asset_name,
        a.class         AS asset_class,
        a.sector,
        a.currency,
        aq.close        AS current_price,
        aq.timestamp    AS current_price_timestamp,
        fx.close        AS usd_brl_rate,
        fx.timestamp    AS usd_brl_timestamp
    FROM   portfolio_positions pp
    JOIN   assets a ON a.id = pp.asset_id
    LEFT JOIN LATERAL (
        SELECT close, timestamp
        FROM   asset_quotes
        WHERE  asset_id = pp.asset_id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq ON true
    LEFT JOIN LATERAL (
        SELECT aq2.close, aq2.timestamp
        FROM   asset_quotes aq2
        JOIN   assets a2 ON a2.id = aq2.asset_id
        WHERE  a2.ticker = 'USDBRL'
        ORDER  BY aq2.timestamp DESC
        LIMIT  1
    ) fx ON true
    WHERE  pp.user_id = :uid
    ORDER  BY pp.total_invested DESC
"""

_SQL_POSICOES_SNAPSHOT = """
    -- Compatibilidade: a primeira versao do importador do Tesouro reutilizou
    -- o insert da XP e rotulou seis linhas como xp_consolidado. A origem real
    -- continua identificavel pelo prefixo imutavel td-snap- do source_id.
    WITH normalized_snapshots AS (
        SELECT
            pps.*,
            CASE
                WHEN pps.source_id LIKE 'td-snap-%' THEN 'tesouro_direto'
                ELSE pps.source_table
            END AS effective_source_table
        FROM portfolio_position_snapshots pps
        WHERE pps.user_id = :uid
    ),
    latest_source AS (
        SELECT
            source_system,
            effective_source_table,
            MAX(report_date) AS report_date
        FROM normalized_snapshots
        GROUP BY source_system, effective_source_table
    ),
    latest_rows AS (
        SELECT pps.*
        FROM normalized_snapshots pps
        JOIN latest_source ls
          ON ls.source_system = pps.source_system
         AND ls.effective_source_table = pps.effective_source_table
         AND ls.report_date = pps.report_date
    ),
    ranked_snapshots AS (
        SELECT
            pps.*,
            DENSE_RANK() OVER (
                PARTITION BY pps.asset_id
                ORDER BY
                    pps.report_date DESC,
                    -- Desempate SO quando o mesmo ativo aparece na MESMA
                    -- data em duas origens (report_date DESC vem antes). A
                    -- ordem e por autoridade da fonte, nao por antiguidade
                    -- do importador:
                    --   tesouro_direto       -- unica fonte com o titulo
                    --   b3_posicao_detalhada -- custodia central, TODAS as
                    --                           corretoras
                    --   xp_consolidado       -- uma corretora
                    --   xp_positions         -- legado
                    -- ELSE existe so para nao quebrar com dado antigo. Uma
                    -- origem NOVA que caia nele perde todo empate em
                    -- silencio, entao test_prioridade_fonte_snapshot.py
                    -- falha quando um importador grava source_table que nao
                    -- esta nomeado aqui.
                    CASE pps.effective_source_table
                        WHEN 'tesouro_direto' THEN 0
                        WHEN 'b3_posicao_detalhada' THEN 1
                        WHEN 'xp_consolidado' THEN 2
                        WHEN 'xp_positions' THEN 3
                        ELSE 4
                    END,
                    pps.source_system,
                    pps.effective_source_table
            ) AS asset_source_rank
        FROM latest_rows pps
    ),
    -- ── REGRA DE CUSTO: o preco medio sai do extrato da B3, nunca do
    -- consolidado da corretora (o .xlsx da Rico/XP, gravado como
    -- 'xp_consolidado').
    --
    -- Isto NAO e o mesmo que o DENSE_RANK abaixo. Aquele ordena por
    -- report_date PRIMEIRO, e so desempata por autoridade da fonte dentro da
    -- mesma data -- ou seja, um consolidado da corretora mais recente que o
    -- ultimo extrato da B3 passa a ditar quantidade, valor de mercado E
    -- custo de todo ativo que as duas fontes cobrem. Para a foto da posicao
    -- isso esta certo (o mais recente e o mais verdadeiro). Para o CUSTO nao:
    -- preco medio nao envelhece como cotacao envelhece -- ele so muda quando
    -- ha compra ou venda --, e a B3 e a custodia central, com o historico de
    -- TODAS as corretoras, enquanto o consolidado enxerga so a propria.
    --
    -- Por isso o custo tem a sua propria fonte, decidida fora do ranking:
    -- a foto mais recente da B3 que publica custo, independente de quem tenha
    -- ganhado o ranking da posicao.
    -- ── ENCERRAMENTO: ausencia na foto seguinte nao apaga a venda.
    --
    -- O extrato da B3 publica o ativo vendido com quantidade e saldo zero, e
    -- o importador grava esse zero de proposito -- e a evidencia de que a
    -- posicao acabou. Mas `latest_rows` so preserva as linhas da data MAXIMA
    -- de cada fonte: quando chega um extrato mais novo, a linha zerada do
    -- extrato anterior e descartada, e se a B3 ja parou de listar o ativo
    -- (ela para, algumas semanas depois da venda) o encerramento some junto.
    -- HGRE11 voltou assim: zerado em 2026-09-21, ausente em 2026-09-22, e
    -- ressuscitado pelo consolidado da Rico de 2026-07-31, que ainda o
    -- mostrava com 31 cotas.
    --
    -- Por isso o encerramento e lido na ULTIMA linha que a B3 publicou para
    -- AQUELE ativo, nao na ultima foto da fonte. Recompra volta a aparecer
    -- sozinha: a linha nova, com quantidade > 0, passa a ser a ultima.
    b3_ultima_linha AS (
        SELECT s.asset_id, MAX(s.report_date) AS report_date
        FROM normalized_snapshots s
        WHERE s.effective_source_table = 'b3_posicao_detalhada'
        GROUP BY s.asset_id
    ),
    b3_encerrados AS (
        -- Soma lote padrao + fracionario da mesma data: encerrado e o ativo
        -- cuja ultima linha na custodia central nao tem nem quantidade nem
        -- saldo.
        SELECT s.asset_id
        FROM normalized_snapshots s
        JOIN b3_ultima_linha u
          ON u.asset_id = s.asset_id
         AND u.report_date = s.report_date
        WHERE s.effective_source_table = 'b3_posicao_detalhada'
        GROUP BY s.asset_id
        HAVING COALESCE(SUM(s.quantity), 0) = 0
           AND COALESCE(SUM(s.market_value), 0) = 0
    ),
    b3_cost_latest AS (
        SELECT
            REGEXP_REPLACE(a.ticker, 'F$', '') AS base_ticker,
            MAX(s.report_date) AS report_date
        FROM normalized_snapshots s
        JOIN assets a ON a.id = s.asset_id
        WHERE s.effective_source_table = 'b3_posicao_detalhada'
          AND s.invested_value > 0
          AND s.quantity > 0
        GROUP BY 1
    ),
    b3_cost AS (
        -- Soma lote padrao + fracionario da MESMA data antes de dividir: o
        -- preco medio de PETR3 e PETR3F e um so para o investidor.
        SELECT
            c.base_ticker,
            SUM(s.invested_value) / NULLIF(SUM(s.quantity), 0) AS b3_avg_price,
            SUM(s.quantity)                                    AS b3_quantity,
            c.report_date                                      AS b3_report_date
        FROM normalized_snapshots s
        JOIN assets a ON a.id = s.asset_id
        JOIN b3_cost_latest c
          ON c.base_ticker = REGEXP_REPLACE(a.ticker, 'F$', '')
         AND c.report_date = s.report_date
        WHERE s.effective_source_table = 'b3_posicao_detalhada'
          AND s.invested_value > 0
          AND s.quantity > 0
        GROUP BY c.base_ticker, c.report_date
    ),
    pp_base AS (
        SELECT
            REGEXP_REPLACE(a.ticker, 'F$', '') AS base_ticker,
            SUM(pp.quantity) AS pp_quantity,
            SUM(pp.total_invested) AS pp_total_invested,
            SUM(CASE WHEN pp.total_invested > 0 THEN pp.quantity ELSE 0 END) AS pp_cost_quantity,
            SUM(pp.total_invested) / NULLIF(SUM(CASE WHEN pp.total_invested > 0 THEN pp.quantity ELSE 0 END), 0) AS pp_average_price
        FROM portfolio_positions pp
        JOIN assets a ON a.id = pp.asset_id
        WHERE pp.user_id = :uid
        GROUP BY REGEXP_REPLACE(a.ticker, 'F$', '')
    )
    SELECT
        pps.quantity,
        pps.report_date,
        pps.market_price,
        pps.market_value,
        pps.invested_value,
        pp_base.pp_quantity,
        pp_base.pp_total_invested,
        pp_base.pp_cost_quantity,
        pp_base.pp_average_price,
        b3_cost.b3_avg_price,
        b3_cost.b3_quantity,
        b3_cost.b3_report_date,
        pps.is_loaned,
        pps.asset_type,
        pps.asset_name,
        pps.currency,
        pps.country,
        a.ticker,
        a.class AS asset_class,
        a.sector,
        aq_live.close        AS live_price,
        aq_live.timestamp    AS live_timestamp,
        fx.close             AS usd_brl_rate,
        fx.timestamp         AS usd_brl_timestamp
    FROM ranked_snapshots pps
    JOIN assets a ON a.id = pps.asset_id
    LEFT JOIN pp_base
      ON pp_base.base_ticker = REGEXP_REPLACE(a.ticker, 'F$', '')
    LEFT JOIN b3_cost
      ON b3_cost.base_ticker = REGEXP_REPLACE(a.ticker, 'F$', '')
    LEFT JOIN LATERAL (
        SELECT close, timestamp
        FROM   asset_quotes
        WHERE  asset_id = pps.asset_id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq_live ON true
    LEFT JOIN LATERAL (
        SELECT aq2.close, aq2.timestamp
        FROM   asset_quotes aq2
        JOIN   assets a2 ON a2.id = aq2.asset_id
        WHERE  a2.ticker = 'USDBRL'
        ORDER  BY aq2.timestamp DESC
        LIMIT  1
    ) fx ON true
    WHERE pps.user_id = :uid
      AND pps.asset_source_rank = 1
      AND pps.asset_id NOT IN (SELECT asset_id FROM b3_encerrados)
    ORDER BY pps.market_value DESC
"""


_SQL_POSICOES_EXTRAS_FORA_SNAPSHOT = """
    SELECT
        pp.quantity,
        pp.average_price,
        pp.total_invested,
        a.ticker,
        a.name          AS asset_name,
        a.class         AS asset_class,
        a.sector,
        a.currency,
        aq.close        AS current_price,
        aq.timestamp    AS current_price_timestamp,
        fx.close        AS usd_brl_rate,
        fx.timestamp    AS usd_brl_timestamp
    FROM   portfolio_positions pp
    JOIN   assets a ON a.id = pp.asset_id
    LEFT JOIN LATERAL (
        SELECT close, timestamp
        FROM   asset_quotes
        WHERE  asset_id = pp.asset_id
        ORDER  BY timestamp DESC
        LIMIT  1
    ) aq ON true
    LEFT JOIN LATERAL (
        SELECT aq2.close, aq2.timestamp
        FROM   asset_quotes aq2
        JOIN   assets a2 ON a2.id = aq2.asset_id
        WHERE  a2.ticker = 'USDBRL'
        ORDER  BY aq2.timestamp DESC
        LIMIT  1
    ) fx ON true
    WHERE  pp.user_id = :uid
      AND  pp.quantity > 0.000001
      AND  a.currency = 'USD'
      AND  pp.asset_id NOT IN (
               SELECT DISTINCT pps.asset_id
               FROM   portfolio_position_snapshots pps
               WHERE  pps.user_id = :uid
           )
    ORDER  BY pp.total_invested DESC
"""


def _get_usd_brl_live() -> float | None:
    """Tenta buscar USD/BRL via yfinance; ausência permanece explícita."""
    try:
        import yfinance as yf
        df = yf.download("USDBRL=X", period="3d", interval="1d",
                         auto_adjust=True, progress=False)
        if not df.empty:
            return float(df["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return None


def _sem_cambio_historico(posicoes: list) -> list[str]:
    """Tickers cujo retorno em BRL é desconhecido, na ordem em que aparecem.

    A lista existe para que o aviso da tela nomeie as posições em vez de
    afirmar genericamente que "falta câmbio". Texto de limitação que não
    deriva da medição continua soando verdadeiro depois que a causa some.
    """
    return [str(p.get("ticker") or "?") for p in posicoes
            if p.get("retorno_brl_disponivel") is False]


def _adicionar_extras_ao_snapshot(carteira: dict, extra_rows: list,
                                  fx_compra: dict | None = None) -> None:
    """Acrescenta posições USD fora do snapshot XP (ETFs Nomad) ao dict de carteira.

    Somente ativos com currency='USD' chegam aqui (filtro no SQL).

    O **valor de mercado** usa o câmbio de hoje; o **custo** usa o câmbio da
    data de cada compra, vindo de ``fx_compra`` (ver core.fx_aquisicao). São
    taxas diferentes de propósito: é essa diferença que faz o retorno em BRL
    existir. Sem ``fx_compra`` para o ticker, o custo volta a ser estimado pelo
    câmbio de hoje e a posição declara que seu retorno em BRL é desconhecido.
    """
    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    if not extra_rows:
        return

    # Taxa fx: pega do primeiro row que tiver; fallback yfinance
    fx_rate = _f(extra_rows[0].usd_brl_rate) if extra_rows else 0.0
    if fx_rate < 2.0:  # valor inválido ou ausente
        fx_rate = _get_usd_brl_live()
    if fx_rate is None or fx_rate < 2.0:
        carteira.setdefault("avisos_dados", []).append(
            "Posições em USD sem cotação USD/BRL válida foram mantidas fora da avaliação consolidada."
        )
        return

    tickers_existentes = {p["ticker"].upper() for p in carteira.get("posicoes", [])}
    novas: list[dict] = []

    for r in extra_rows:
        ticker = (r.ticker or "").upper().strip()
        if not ticker or ticker in tickers_existentes:
            continue

        qty     = _f(r.quantity)
        pm_raw  = _f(r.average_price)   # em USD
        ti_raw  = _f(r.total_invested)  # em USD
        if qty <= 0:
            continue

        ccy = (r.currency or "USD").upper()

        # Preço atual: cotação do banco (USD) ou fallback para pm_raw
        pr_raw = _f(r.current_price) if r.current_price is not None else pm_raw
        cotacao_ts = getattr(r, "current_price_timestamp", None)
        status_cotacao = classificar_cotacao(cotacao_ts) if r.current_price is not None else "missing"
        cotacao_fonte = (
            "live" if status_cotacao == "fresh"
            else "stale" if status_cotacao in {"stale", "invalid"}
            else "custo_fallback"
        )

        # Converte tudo para BRL. O custo tem taxa própria: a da aquisição.
        custo_usd = ti_raw if ti_raw > 0 else pm_raw * qty
        taxa_aquisicao = taxa_para(fx_compra or {}, ticker)
        custo_historico = taxa_aquisicao is not None
        taxa_custo = taxa_aquisicao if custo_historico else fx_rate
        pm       = pm_raw  * taxa_custo
        ti       = custo_usd * taxa_custo
        pr_atual = pr_raw  * fx_rate

        if ti <= 0:
            ti = pm * qty

        vm      = round(qty * pr_atual, 2)
        valor_atual_usd = qty * pr_raw
        retorno_local = retorno_moeda_origem(valor_atual_usd, custo_usd)
        rentab = round(retorno_local * 100, 2) if retorno_local is not None else None
        retorno_brl = retorno_em_brl(valor_atual_usd, custo_usd, fx_rate,
                                     taxa_custo if custo_historico else None)
        classe_raw = "etf_intl"

        novas.append({
            "ticker":          ticker,
            "nome":            r.asset_name or ticker,
            "classe":          _CLASS_LABEL.get(classe_raw, "ETF Internacional"),
            "setor":           _SETOR_LABEL.get(r.sector or "other", r.sector or "ETF Internacional"),
            "moeda":           ccy,
            "pais":            "US" if ccy == "USD" else "BR",
            "quantidade":      qty,
            "preco_medio":     round(pm, 6),
            "total_investido": round(ti, 2),
            "preco_medio_moeda_original": round(pm_raw, 6),
            "total_investido_moeda_original": round(custo_usd, 2),
            "preco_atual_moeda_original": round(pr_raw, 6),
            "fx_rate_atual": fx_rate,
            "fx_rate_compra": taxa_aquisicao,
            "custo_estimado":  not custo_historico,
            "custo_fonte":     ("cambio_historico_compras" if custo_historico
                                else "cambio_atual_estimado"),
            "cotacao_fonte":   cotacao_fonte,
            "cotacao_timestamp": cotacao_ts,
            "data_referencia": cotacao_ts,
            "preco_atual":     round(pr_atual, 6),
            "valor_mercado":   vm,
            "diferenca_reais": round(vm - ti, 2),
            "rentab_pct":      rentab,
            "rentab_moeda":    ccy,
            "rentab_brl_pct":  (round(retorno_brl * 100, 2)
                                if retorno_brl is not None else None),
            "retorno_brl_disponivel": retorno_brl is not None,
            "pct_carteira":    0.0,
            "cor":             _CLASS_COR.get(classe_raw, "#4A9EFF"),
        })

    if not novas:
        return

    carteira["posicoes"].extend(novas)
    carteira["posicoes"].sort(key=lambda p: p["valor_mercado"], reverse=True)

    ti_total = sum(p["total_investido"] for p in carteira["posicoes"])
    vm_total = sum(p["valor_mercado"]   for p in carteira["posicoes"])
    base     = vm_total if vm_total > 0 else ti_total
    for p in carteira["posicoes"]:
        p["pct_carteira"] = p["valor_mercado"] / base * 100 if base > 0 else 0.0

    carteira["total_investido"]         = round(ti_total, 2)
    carteira["total_mercado"]           = round(vm_total, 2)
    carteira["num_ativos"]              = len(carteira["posicoes"])
    carteira["rentabilidade_total_pct"] = round(
        (vm_total - ti_total) / ti_total * 100, 2
    ) if ti_total > 0 else 0.0
    sem_cambio = _sem_cambio_historico(carteira["posicoes"])
    carteira["posicoes_sem_cambio_historico"] = sem_cambio
    carteira["rentabilidade_total_disponivel"] = not sem_cambio
    carteira["n_cotacoes_live"] = sum(
        1 for p in carteira["posicoes"] if p.get("cotacao_fonte") == "live"
    )
    carteira["n_cotacoes_stale"] = sum(
        1 for p in carteira["posicoes"] if p.get("cotacao_fonte") == "stale"
    )
    data_ref_min, data_ref_max = intervalo_referencia(
        [p.get("data_referencia") for p in carteira["posicoes"]]
    )
    carteira["data_referencia_min"] = data_ref_min
    carteira["data_referencia_max"] = data_ref_max
    novos_stale = sum(1 for p in novas if p.get("cotacao_fonte") == "stale")
    if novos_stale:
        carteira.setdefault("avisos_dados", []).append(
            f"{novos_stale} posição(ões) USD usam cotação desatualizada com sinalização explícita."
        )
    carteira["por_classe"] = _agregar_por_classe(carteira["posicoes"])
    carteira["por_setor"]  = _agregar_por_setor(carteira["posicoes"])


def _carteira_snapshot_consolidada(conn, owner: str, rows: list) -> dict:
    """Carteira de hoje: ultimo snapshot XP + posicoes USD fora dele.

    Unica montagem do "quanto vale a carteira hoje". O Dashboard e o ponto
    corrente da Evolucao Patrimonial leem daqui: a evolucao montava so o
    snapshot e deixava de fora os ETFs da Nomad, e o "Valor de Mercado
    Atual" do Historico saia menor que o "Patrimonio Total" do Dashboard
    pela fatia do exterior.
    """
    from sqlalchemy import text

    tx_costs = _calcular_custos_transacoes(conn, owner)
    fx_compra = cambio_medio_de_aquisicao(conn, owner)
    carteira = _montar_carteira_snapshot(
        rows, tx_costs, listar_precos_manuais(owner, conn)
    )
    # Adiciona posições que existem em portfolio_positions mas NÃO
    # no snapshot XP (ex: ETFs Nomad — SPY, IEFA — importados via PDF).
    extra_rows = conn.execute(
        text(_SQL_POSICOES_EXTRAS_FORA_SNAPSHOT), {"uid": owner}
    ).fetchall()
    if extra_rows:
        _adicionar_extras_ao_snapshot(carteira, extra_rows, fx_compra)
    return carteira


def _carteira_real() -> dict:
    """
    Consulta portfolio_positions + assets + asset_quotes (LATERAL) e monta o dict.
    Retorna o mesmo schema que _carteira_mock().

    Lança RuntimeError em qualquer problema (engine ausente, sem dados, etc.).
    O caller (get_carteira) captura e retorna estado indisponível sem mock.

    SEGURANÇA:
      - Sem credenciais no código.
      - Todas as queries filtradas por OWNER_USER_ID.
      - Somente SELECT — sem DDL nem DML de escrita.
    """
    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError(
            "Engine indisponível — configure SUPABASE_UNIFICADO_URL "
            "no .env local ou em Streamlit Secrets."
        )

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado — filtro de usuário inativo.")

    with engine.connect() as conn:
        has_snapshots = bool(conn.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'portfolio_position_snapshots'
            )
        """)).scalar())
        if has_snapshots:
            rows = conn.execute(text(_SQL_POSICOES_SNAPSHOT), {"uid": owner}).fetchall()
            if rows:
                return _carteira_snapshot_consolidada(conn, owner, rows)
        rows = conn.execute(text(_SQL_POSICOES), {"uid": owner}).fetchall()
        fx_compra = cambio_medio_de_aquisicao(conn, owner)

    if not rows:
        raise RuntimeError(
            "Nenhuma posição encontrada em portfolio_positions para este usuário. "
            "Verifique OWNER_USER_ID e importe as negociações (recompute_for_user em data_pipeline/importers/investments/positions.py recalcula as posições)."
        )

    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    cotacoes_disponiveis = any(r.current_price is not None for r in rows)

    total_investido = 0.0
    total_mercado   = 0.0
    posicoes        = []
    avisos_dados    = []

    for r in rows:
        qty    = _f(r.quantity)
        pm     = _f(r.average_price)
        ti     = _f(r.total_invested)
        preco_atual = _f(r.current_price) if r.current_price is not None else pm
        preco_medio_original = pm
        total_investido_original = ti
        preco_atual_original = preco_atual

        # Conversão USD → BRL para posições internacionais (Nomad, BDR USD).
        # fx_rate vem do ativo sintético USDBRL (LATERAL JOIN no SQL acima).
        # Sem USD/BRL válido, a posição não entra silenciosamente em um total BRL.
        ccy = (r.currency or "BRL").upper()
        fx_rate = _f(getattr(r, "usd_brl_rate", None))
        if ccy == "USD" and fx_rate < 2.0:
            fx_rate = _get_usd_brl_live() or 0.0
        if ccy == "USD" and fx_rate < 2.0:
            avisos_dados.append(
                f"{r.ticker}: posição USD sem USD/BRL válido, excluída da avaliação consolidada."
            )
            continue
        # Custo converte pelo câmbio da compra; mercado, pelo de hoje.
        taxa_aquisicao = (taxa_para(fx_compra, r.ticker) if ccy == "USD" else None)
        if ccy == "USD":
            preco_atual *= fx_rate
            taxa_custo = taxa_aquisicao if taxa_aquisicao is not None else fx_rate
            pm *= taxa_custo
            ti *= taxa_custo

        vm     = round(qty * preco_atual, 2)
        if ccy == "USD":
            retorno_local = retorno_moeda_origem(
                qty * preco_atual_original, total_investido_original
            )
            rentab = round(retorno_local * 100, 2) if retorno_local is not None else None
            retorno_brl = retorno_em_brl(
                qty * preco_atual_original, total_investido_original,
                fx_rate, taxa_aquisicao,
            )
            rentab_brl = (round(retorno_brl * 100, 2)
                          if retorno_brl is not None else None)
            retorno_brl_disponivel = retorno_brl is not None
        else:
            rentab = round((vm - ti) / ti * 100, 2) if ti > 0 else None
            rentab_brl = rentab
            retorno_brl_disponivel = rentab is not None

        classe_raw = r.asset_class or "other"
        setor_raw  = r.sector or "other"

        total_investido += ti
        total_mercado   += vm

        cotacao_ts = getattr(r, "current_price_timestamp", None)
        status_cotacao = classificar_cotacao(cotacao_ts) if r.current_price is not None else "missing"
        cotacao_fonte = (
            "live" if status_cotacao == "fresh"
            else "stale" if status_cotacao in {"stale", "invalid"}
            else "snapshot"
        )
        posicoes.append({
            "ticker":            r.ticker,
            "nome":              _nome_exibicao(r.asset_name, r.ticker, classe_raw),
            "classe":            _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "setor":             _SETOR_LABEL.get(setor_raw, setor_raw.title()),
            "moeda":             ccy,
            "quantidade":        qty,
            "preco_medio":       pm,
            "total_investido":   ti,
            "cotacao_fonte":     cotacao_fonte,
            "cotacao_timestamp": cotacao_ts,
            "data_referencia":   cotacao_ts,
            "preco_atual":       preco_atual,
            "valor_mercado":     vm,
            "diferenca_reais":   round(vm - ti, 2),
            "rentab_pct":        rentab,
            "rentab_moeda":      ccy,
            "rentab_brl_pct":    rentab_brl,
            "retorno_brl_disponivel": retorno_brl_disponivel,
            "preco_medio_moeda_original": preco_medio_original,
            "total_investido_moeda_original": total_investido_original,
            "preco_atual_moeda_original": preco_atual_original,
            "fx_rate_atual": fx_rate if ccy == "USD" else None,
            "fx_rate_compra": taxa_aquisicao,
            "pct_carteira":      0.0,
            "cor":               _CLASS_COR.get(classe_raw, "#718096"),
        })

    posicoes = _juntar_fracionario_posicoes(posicoes)

    # Preenche pct_carteira com base no total_mercado consolidado
    base = total_mercado if total_mercado > 0 else total_investido
    n_live = sum(1 for p in posicoes if p.get("cotacao_fonte") == "live")
    n_stale = sum(1 for p in posicoes if p.get("cotacao_fonte") == "stale")
    for p in posicoes:
        p["pct_carteira"] = p["valor_mercado"] / base * 100 if base > 0 else 0.0

    diferenca_total = round(total_mercado - total_investido, 2)
    rentabilidade_total = round(
        diferenca_total / total_investido * 100, 2
    ) if total_investido > 0 else 0.0
    data_ref_min, data_ref_max = intervalo_referencia(
        [p.get("data_referencia") for p in posicoes]
    )
    if n_stale:
        avisos_dados.append(
            f"{n_stale} cotação(ões) excedem o limite de frescor e foram marcadas como desatualizadas."
        )

    return {
        "total_investido":         round(total_investido, 2),
        "total_mercado":           round(total_mercado, 2),
        "diferenca_reais":         diferenca_total,
        "rentabilidade_total_pct": rentabilidade_total,
        "rentabilidade_total_disponivel": not _sem_cambio_historico(posicoes),
        "posicoes_sem_cambio_historico": _sem_cambio_historico(posicoes),
        "num_ativos":              len(posicoes),
        "cotacoes_disponiveis":    cotacoes_disponiveis,
        "n_cotacoes_live":         n_live,
        "n_cotacoes_stale":        n_stale,
        "data_referencia_min":     data_ref_min,
        "data_referencia_max":     data_ref_max,
        "posicoes":                posicoes,
        "por_classe":              _agregar_por_classe(posicoes),
        "por_setor":               _agregar_por_setor(posicoes),
        "avisos_dados":            avisos_dados,
        # data_source injetado pelo caller
    }


def _juntar_fracionario_posicoes(posicoes: list) -> list:
    """Uma posição por ticker-base: BBAS3F soma em BBAS3.

    Caminho de reserva de `_carteira_real` (sem snapshots), que lê
    portfolio_positions linha a linha, uma por asset_id. O caminho principal
    já agrupa pelo ticker-base no SQL. Quantidade, custo e mercado somam;
    preço médio e rentabilidade são recalculados; nome, cotação e o resto
    vêm da linha do lote padrão quando ela existe. Posição no exterior não
    tem fracionário e passa intacta.
    """
    grupos: dict[object, dict] = {}
    for i, p in enumerate(posicoes):
        if (p.get("moeda") or "BRL") != "BRL":
            grupos[("exterior", i)] = p
            continue
        base = _base_ticker(p.get("ticker") or "")
        atual = grupos.get(base)
        if atual is None:
            grupos[base] = {**p, "ticker": base}
            continue
        e_lote_padrao = str(p.get("ticker") or "").upper().strip() == base
        principal, outra = (p, atual) if e_lote_padrao else (atual, p)
        qtd = float(principal.get("quantidade") or 0) + float(outra.get("quantidade") or 0)
        ti = float(principal.get("total_investido") or 0) + float(outra.get("total_investido") or 0)
        vm = round(float(principal.get("valor_mercado") or 0)
                   + float(outra.get("valor_mercado") or 0), 2)
        rentab = round((vm - ti) / ti * 100, 2) if ti > 0 else None
        pm = ti / qtd if qtd else 0.0
        grupos[base] = {
            **principal,
            "ticker": base,
            "quantidade": qtd,
            "total_investido": ti,
            "total_investido_moeda_original": ti,
            "preco_medio": pm,
            "preco_medio_moeda_original": pm,
            "valor_mercado": vm,
            "diferenca_reais": round(vm - ti, 2),
            "rentab_pct": rentab,
            "rentab_brl_pct": rentab,
            "retorno_brl_disponivel": rentab is not None,
        }
    return list(grupos.values())


def _base_ticker(ticker: str) -> str:
    t = (ticker or "").upper().strip()
    if t.endswith("11F"):
        return t[:-1]
    if t.endswith("F") and len(t) > 4:
        return t[:-1]
    return t


# FIP (fundo de investimento em participações) não é renda fixa: é private
# equity ilíquido, sem curva nem cotação. A XP o entrega como fixed_income e o
# código da B3 é numérico ("4606422UNA", "5082123CA1"), então o ticker não
# denuncia -- a identificação é pelo nome ("... FIP ..." / "Fundo de
# Investimento em Participações") ou por esta lista, que nasce da auditoria de
# 04/10/2026 (INV-A1) e cresce quando surgir outro.
_FIPS_CONHECIDOS = frozenset({"4606422UNA", "5082123CA1"})
_RE_FIP_NOME = re.compile(
    r"\bFIP\b|FUNDO\s+DE\s+INVEST\w*\s+EM\s+PARTICIPA", re.IGNORECASE)


def eh_fip(ticker: str | None, nome: str | None = None) -> bool:
    return ((ticker or "").upper().strip() in _FIPS_CONHECIDOS
            or bool(_RE_FIP_NOME.search(nome or "")))


def _class_key_from_snapshot(raw_type: str | None, ticker: str, country: str | None,
                             nome: str | None = None) -> str:
    raw = (raw_type or "").strip().lower()
    t = (ticker or "").upper().strip()
    c = (country or "BR").upper().strip()
    if c not in ("", "BR"):
        return "etf_intl" if raw == "etf" else raw or "other"
    if raw in {"renda_fixa", "fixed_income", "fundo_rf", "other", ""} and eh_fip(t, nome):
        return "fip"
    if raw == "tesouro":
        return "tesouro"
    if raw in {"renda_fixa", "fixed_income"}:
        # Tesouro Direto via XP vem como fixed_income — detecta por ticker.
        # Cobre TSELIC*, TIPCA*, TPRE*, TEDUCA* (XP) + LFT/LTN/NTN* (legado).
        if t.startswith(("TSELIC", "TIPCA", "TPRE", "TEDUCA", "LFT", "LTN", "NTN", "TESOURO")):
            return "tesouro"
        if t.startswith(("CDB", "LCI", "LCA", "CRI", "CRA")):
            return "renda_fixa"
        return "fundo_rf"
    if raw == "fii":
        return "fii"
    if raw == "etf":
        return "etf_br"
    return raw or "other"


def _calcular_custos_transacoes(conn, owner_id: str) -> dict[str, dict]:
    """Calcula qty/ti/pm por base_ticker direto de investment_transactions.

    Retorna {base_ticker: {qty, ti, pm}} — usado pelo _montar_carteira_snapshot
    para detectar venda parcial (qty_snap < tx_qty) e ajustar o custo da
    posicao atual mantendo o PM historico.

    Hoje os dados consistentes ja estao em portfolio_positions (via pp_base
    no SQL), entao retornamos {} — a logica de venda parcial usa pp_qty/pp_ti
    direto. Funcao mantida pra extensibilidade futura (ex: usar transactions
    para FIFO em vez de avg ponderado).
    """
    return {}


#: Classes sem cotação diária: o valor vem da foto da corretora.
_CLASSES_SEM_COTACAO = frozenset({"renda_fixa", "fundo_rf", "fip"})


def sem_marcacao_renda_fixa(classe_raw: str, custo_fonte: str, cotacao_fonte: str,
                            custo: float, valor: float) -> bool:
    """``True`` quando o retorno de um papel sem cotação é ausência, não 0,0%.

    Vale para renda fixa privada, fundo e FIP (Tesouro tem marcação própria em
    ``core.tesouro_mtm``). Dois sinais: (a) o custo é o próprio valor de
    mercado, imputado por falta de fonte (``mercado_fallback``); (b) o custo
    veio da foto e é igual ao valor da foto até o centavo -- um papel que rende
    não fecha assim, é o relatório da corretora repetindo o mesmo número nas
    duas colunas. Medido em 04/10/2026: 5 CDBs e 2 fundos (16,8% do valor)
    apareciam com 0,0%. Papel com cotação viva (``live``) nunca cai aqui.
    """
    if classe_raw not in _CLASSES_SEM_COTACAO or cotacao_fonte in ("live", "stale"):
        return False
    if custo_fonte == "mercado_fallback":
        return True
    return custo_fonte == "snapshot" and abs(float(custo) - float(valor)) < 0.005


def resumo_sem_marcacao(posicoes: list) -> dict:
    """Quanto da carteira está sem marcação: ``{n, valor, pct, tickers}``."""
    sem = [p for p in posicoes if p.get("sem_marcacao")]
    total = sum(float(p.get("valor_mercado") or 0) for p in posicoes)
    valor = sum(float(p.get("valor_mercado") or 0) for p in sem)
    return {"n": len(sem), "valor": round(valor, 2),
            "pct": round(valor / total * 100, 2) if total > 0 else 0.0,
            "tickers": sorted(str(p.get("ticker")) for p in sem)}


def _montar_carteira_snapshot(rows: list, tx_costs: dict | None = None,
                              precos_manuais: dict | None = None) -> dict:
    """Monta a carteira real a partir do ultimo snapshot XP + custo da B3.

    Estrategia de unificacao (corrige bug 2026-05-22 onde card misturava
    qty do snapshot XP com PM agregado da B3, gerando custos inconsistentes):

      1. Agrupa rows do SQL por base_ticker (BBAS3 + BBAS3F → BBAS3)
      2. Soma quantidade e market_value de todos os snapshots do base_ticker
         desconsiderando emprestimos de ativos (filtrados no SQL).
      3. Para o CUSTO: o preco medio vem do que foi COMPRADO -- o agregado
         das notas de negociacao (pp_base) -- sempre que esse historico
         cobrir a quantidade de hoje. Quando nao cobre, o que existe e uma
         amostra, e entra o preco medio declarado no extrato "Posicao
         Detalhada" da B3 (CTE b3_cost, decidida FORA do ranking da
         posicao), que e calculado sobre o historico inteiro. Sem nenhum
         dos dois, a amostra esticada; depois o invested_value da linha
         vencedora (CDBs e titulos privados, que so existem no .xlsx da
         corretora); e em ultimo caso o market_value.
      4. Acima de todas: o preco medio que o PROPRIO investidor declarou
         (`precos_manuais`, tabela investment_manual_costs). Vem primeiro
         porque so e preenchido quando a pessoa sabe que as outras fontes
         estao erradas ou ausentes -- e vai rotulado como "informado por
         voce", nunca como declarado pela B3.
    """
    precos_manuais = precos_manuais or {}
    # ── 1. Agrupa rows por base_ticker
    grupos: dict[str, dict] = {}
    for r in rows:
        qty = float(r.quantity or 0)
        vm = float(r.market_value or 0)
        # Filtra posicoes vazias. Excecao: Tesouro/RF que a XP reporta com
        # qty=0 (arredondamento) mas vm > 0 — esses sao posicoes reais com
        # valor de cota inteiro (ex: TSELIC2028 R$ 5.483 mas qty=0).
        tipo_row = str(getattr(r, "asset_type", "") or "").lower()
        # Posicao encerrada chega aqui como vm = 0 (o extrato da B3 publica o
        # ativo vendido com saldo e quantidade zero, e o importador GRAVA esse
        # zero de proposito, para a foto de hoje ganhar da foto antiga de
        # outra fonte no DENSE_RANK). E aqui que ela sai da carteira.
        if vm <= 0:
            continue
        if qty <= 0 and tipo_row not in ("fixed_income", "tesouro", "renda_fixa"):
            continue

        base = _base_ticker(r.ticker)
        if base not in grupos:
            grupos[base] = {
                "ticker":         base,
                "rows":           [],
                "qty_snap_sum":   0.0,
                "vm_sum":         0.0,
                "vi_snap_sum":    0.0,
                "preco_mkt_pond": 0.0,
                "live_price":     None,   # preço mais recente de asset_quotes
                "live_ts":        None,   # timestamp do preço live
                "usd_brl_rate":   None,   # taxa USD/BRL (apenas para USD assets)
                "report_dates":   [],
            }
        g = grupos[base]
        g["rows"].append(r)
        g["qty_snap_sum"] += qty
        g["vm_sum"]       += vm
        g["vi_snap_sum"]  += float(r.invested_value or 0)
        g["report_dates"].append(getattr(r, "report_date", None))
        # Captura preço live do primeiro row com cotação disponível
        lp = getattr(r, "live_price", None)
        if lp is not None and g["live_price"] is None:
            g["live_price"] = float(lp)
            g["live_ts"]    = getattr(r, "live_timestamp", None)
        fx = getattr(r, "usd_brl_rate", None)
        if fx is not None and g["usd_brl_rate"] is None:
            g["usd_brl_rate"] = float(fx)

    # ── 2. Para cada base_ticker, calcula campos consolidados
    total_investido = 0.0
    total_mercado = 0.0
    posicoes = []

    for base, g in grupos.items():
        # Representante: prefere row sem 'F' (lote padrao) para metadados
        primary = next((r for r in g["rows"] if not (r.ticker or "").upper().endswith("F")), g["rows"][0])

        qty_snap = g["qty_snap_sum"]
        vm       = g["vm_sum"]
        vi_snap  = g["vi_snap_sum"]

        # pp_base ja vem agregado por base_ticker no SQL — todas as rows do
        # mesmo grupo trarao o MESMO valor de pp_quantity/pp_total_invested.
        pp_qty   = float(getattr(primary, "pp_quantity", 0) or 0)
        pp_ti    = float(getattr(primary, "pp_total_invested", 0) or 0)
        pp_avg   = float(getattr(primary, "pp_average_price", 0) or 0)

        # Preco medio da B3, decidido fora do ranking da posicao (ver a CTE
        # b3_cost). Tambem agregado por base_ticker: todas as rows do grupo
        # trazem o mesmo valor.
        b3_avg   = float(getattr(primary, "b3_avg_price", 0) or 0)
        b3_qty   = float(getattr(primary, "b3_quantity", 0) or 0)

        # ── Ativos em USD (Nomad): snapshot ja vem convertido em BRL pelo
        # importer com cambio do dia. pp_base guarda em USD (sem conversao).
        # Se pp_qty > qty_snap (novas compras Nomad apos o snapshot), escala
        # os valores BRL do snapshot proporcionalmente para refletir a posicao atual.
        ccy = str(primary.currency or "BRL").upper()

        # ── O preco medio que o INVESTIDOR declarou vem antes de tudo.
        #
        # Nao e por ele ser mais confiavel: e por so existir quando as outras
        # fontes ja falharam. O caso que criou esta porta foi o BBAS3 -- o
        # relatorio de Negociacao da B3 comeca em nov/2019, as compras
        # anteriores nao existem em fonte nenhuma, e o numero que o app
        # exibia como "declarado pela B3" tinha sido DIGITADO A MAO pelo
        # usuario na planilha de posicao que ele subiu. O palpite vinha com
        # a autoridade da custodia central.
        #
        # Aqui ele entra pela porta da frente e com procedencia propria: a
        # tela diz "informado por voce". Apagar a declaracao devolve o ativo
        # a cadeia normal, sem nenhum outro efeito.
        pm_manual = float((precos_manuais.get(base) or {}).get("preco_medio", 0) or 0)
        if pm_manual > 0 and qty_snap > 0:
            qty         = qty_snap
            preco_medio = pm_manual
            ti          = pm_manual * qty_snap
            custo_fonte = "informado_pelo_usuario"
        elif ccy == "USD" and vi_snap > 0:
            if pp_qty > qty_snap * 1.005 and qty_snap > 0:
                # Novas cotas Nomad apos snapshot → escala BRL proporcionalmente
                scale   = pp_qty / qty_snap
                qty     = pp_qty
                ti      = vi_snap * scale
                vm      = g["vm_sum"] * scale
                custo_fonte = "nomad_scaled"
            else:
                qty     = qty_snap
                ti      = vi_snap
                custo_fonte = "snapshot"
            preco_medio = ti / qty if qty > 0 else 0.0
        # ── CUSTO: prioridade para o custo da PROPRIA linha do snapshot.
        #
        # A "Posicao Detalhada" da B3 publica o preco medio que a corretora
        # calcula sobre o historico INTEIRO -- inclusive o pedaco anterior a
        # qualquer arquivo que este app tenha importado. `pp_base` agrega so
        # as notas de negociacao que chegaram ate aqui.
        #
        # Ate 2026-09-22 `pp_base` vinha primeiro, e o efeito era esticar um
        # preco medio parcial sobre a quantidade de hoje: BBAS3 saia com
        # custo de R$ 15.374,93 (PM R$ 10,40, de 1.086 cotas de historico)
        # contra os R$ 35.377,68 do extrato (PM R$ 23,92 sobre as 1.479 em
        # carteira). Lucro fantasma de +121% no lugar do prejuizo de -3,85%.
        #
        # O criterio nao e "a corretora e mais confiavel": e que quantidade,
        # valor de mercado e custo da MESMA linha sao consistentes entre si.
        # Cruzar a quantidade de uma fonte com o PM de outra produz um custo
        # que nenhuma das duas afirma.
        #
        # ── Mas "a linha que ganhou o ranking" nao e necessariamente a B3.
        # O ranking ordena por data PRIMEIRO, entao um consolidado da Rico/XP
        # subido depois do ultimo extrato assume quantidade, valor de mercado
        # E custo. Para a posicao isso esta certo; para o CUSTO nao -- preco
        # medio so muda quando ha compra ou venda, e a B3 e a custodia
        # central, com o historico de TODAS as corretoras, enquanto o
        # consolidado enxerga so a propria. Por isso o preco medio da B3 vem
        # antes, venha de onde vier a foto da posicao.
        # ── CUSTO: o preco medio sai do que foi COMPRADO -- o agregado das
        # notas de negociacao (pp_base, reconstruido de investment_transactions
        # por positions.recompute_for_user) --, nao do preco medio declarado
        # na planilha de posicao.
        #
        # A planilha e uma foto de terceiro: o PM que ela publica depende de
        # qual corretora calculou, de que eventos ela considerou e de quando
        # a foto foi tirada. As compras sao o fato primario, e sao nossas.
        #
        # A ressalva vale para o proximo leitor, porque ela tem tamanho: o
        # historico de notas NAO cobre todo ativo. Em 2026-09-23, das 29
        # posicoes, DIRR3 tinha nota para 37% das cotas de hoje, SBSP3 para
        # 69% e BBAS3 para 73%. Dividir o total comprado pela quantidade de
        # hoje nesses casos nao devolve o preco medio: devolve o preco medio
        # de um pedaco do historico esticado sobre a posicao inteira -- foi
        # o defeito que o PR #309 corrigiu (BBAS3 com PM de R$ 10,40 contra
        # os R$ 23,92 reais, lucro fantasma de +121%).
        #
        # Por isso a precedencia das compras e condicionada a UMA verificacao,
        # que e a propria pergunta "as compras explicam a posicao?": o
        # historico precisa cobrir a quantidade de hoje. Quando cobre, o PM
        # das notas vale, venha o que vier na planilha. Quando nao cobre, o
        # que existe nao e um preco medio -- e uma amostra --, e o extrato da
        # B3 (custodia central, historico de TODAS as corretoras) volta a ser
        # a melhor fonte.
        #
        # Venda parcial nao quebra a cobertura: sob preco medio, vender nao
        # muda o PM, entao pp_qty > qty_snap continua coberto.
        elif pp_ti > 0 and qty_snap > 0 and pp_qty >= qty_snap * 0.99:
            qty         = qty_snap
            preco_medio = pp_avg if pp_avg > 0 else (pp_ti / pp_qty)
            if abs(qty_snap - pp_qty) / pp_qty <= 0.01:
                # Mesma quantidade: o custo e a soma das notas, nao uma conta.
                ti = pp_ti
            else:
                # Houve venda depois das compras importadas. O PM sobrevive
                # a venda; o total, nao.
                ti = preco_medio * qty_snap
            custo_fonte = "b3_negociacao"
        elif b3_avg > 0:
            # As notas nao cobrem a posicao (ou nao existem). O extrato da B3
            # calcula o PM sobre o historico INTEIRO, inclusive o pedaco
            # anterior a qualquer arquivo que este app tenha importado.
            qty         = qty_snap
            preco_medio = b3_avg
            ti          = b3_avg * qty_snap
            if b3_qty > 0 and abs(qty_snap - b3_qty) / b3_qty <= 0.01:
                # Mesma quantidade do extrato: o custo e o declarado, nao uma
                # conta nossa.
                custo_fonte = "b3_posicao_detalhada"
            else:
                # O extrato e de antes da ultima movimentacao. O PM continua
                # sendo o melhor que existe, mas o total e extrapolacao -- e
                # o card precisa rotular como estimado.
                custo_fonte = "b3_preco_medio_escalado"
        elif pp_ti > 0 and pp_qty > 0:
            # Nem extrato nem cobertura: so resta esticar a amostra sobre a
            # posicao de hoje. E o unico numero que existe, e vai rotulado
            # como estimado justamente por isso.
            qty         = qty_snap
            preco_medio = pp_avg if pp_avg > 0 else (pp_ti / pp_qty)
            ti          = preco_medio * qty_snap
            custo_fonte = "preco_medio_estimado"
        elif vi_snap > 0:
            # Nenhuma fonte da B3 cobre este ativo -- e o caso dos CDBs e dos
            # titulos privados, que existem so no consolidado da corretora.
            # A regra e de prioridade, nao de proibicao: descartar este numero
            # trocaria um custo correto por "Nao informado".
            ti          = vi_snap
            qty         = qty_snap
            preco_medio = ti / qty if qty > 0 else 0.0
            custo_fonte = "snapshot"
        else:
            # Sem custo conhecido — usa market_value como estimativa
            ti          = vm
            qty         = qty_snap
            preco_medio = ti / qty if qty > 0 else 0.0
            custo_fonte = "mercado_fallback"

        # ── PRECO/VALOR DE MERCADO: preferência para cotação live de asset_quotes.
        # Para ativos de renda fixa / tesouro não há cotação diária — mantém
        # snapshot como fallback.  Para ações, ETFs e FIIs, usa o preço mais
        # recente disponível (yfinance via pipeline) para refletir o valor atual.
        live_price   = g.get("live_price")
        usd_brl_live = g.get("usd_brl_rate") or 1.0
        # Do ativo DESTE grupo. Ate 2026-09-22 esta linha lia a variavel que
        # sobrava do laco anterior, ou seja, o tipo da ULTIMA row que o SQL
        # devolveu -- o mesmo `is_rf` valia para a carteira inteira, e um
        # titulo do Tesouro no fim da lista congelava toda acao no preco do
        # snapshot em vez da cotacao viva.
        is_rf = str(primary.asset_type or "").lower() in (
            "fixed_income", "tesouro", "renda_fixa", "fundo_rf"
        )

        status_cotacao = classificar_cotacao(g.get("live_ts")) if live_price else "missing"
        if live_price and not is_rf:
            ccy_snap = str(primary.currency or "BRL").upper()
            if ccy_snap == "USD":
                preco_atual = live_price * usd_brl_live
            else:
                preco_atual = live_price
            vm_calc = round(qty * preco_atual, 2)
            cotacao_fonte = "live" if status_cotacao == "fresh" else "stale"
        else:
            # Fallback: preço implícito do snapshot
            preco_atual = (vm / qty) if qty > 0 else 0.0
            vm_calc = round(vm, 2)
            cotacao_fonte = "snapshot"
        report_date = max((d for d in g["report_dates"] if d is not None), default=None)
        data_referencia = g.get("live_ts") if cotacao_fonte in {"live", "stale"} else report_date

        rentab = round((vm_calc - ti) / ti * 100, 2) if ti > 0 else 0.0
        # Custo declarado pela corretora nao e estimativa -- e o unico numero
        # autoritativo que existe sobre o assunto.
        custo_estimado = custo_fonte not in (
            "b3_posicao_detalhada", "b3_negociacao", "snapshot",
        )

        classe_raw = _class_key_from_snapshot(primary.asset_type, base, primary.country,
                                              primary.asset_name)
        # Renda fixa, fundo e FIP não têm cotação diária: o "retorno" sai de
        # custo vs. valor da foto. Quando a foto traz custo == valor (ou não
        # traz custo nenhum), esse 0,0% é ausência de marcação, não resultado.
        sem_marcacao = sem_marcacao_renda_fixa(classe_raw, custo_fonte,
                                               cotacao_fonte, ti, vm_calc)
        if sem_marcacao:
            rentab = None

        total_investido += ti
        total_mercado   += vm_calc
        posicoes.append({
            "ticker":          base,
            "nome":            _nome_exibicao(primary.asset_name, base,
                                              primary.asset_type),
            "classe":          _CLASS_LABEL.get(classe_raw, classe_raw.title()),
            "setor":           _SETOR_LABEL.get(primary.sector or "other", (primary.sector or "other").title()),
            "moeda":           primary.currency or "BRL",
            "pais":            primary.country or "BR",
            "quantidade":      qty,
            "preco_medio":     round(preco_medio, 6),
            "total_investido":    round(ti, 2),
            "custo_estimado":     custo_estimado,
            "custo_fonte":        custo_fonte,
            "cotacao_fonte":      cotacao_fonte,  # "live" | "snapshot"
            "cotacao_timestamp":  g.get("live_ts"),
            "snapshot_report_date": report_date,
            "data_referencia":    data_referencia,
            "preco_atual":        round(preco_atual, 6),
            "valor_mercado":      vm_calc,
            "diferenca_reais":    round(vm_calc - ti, 2),
            "rentab_pct":         rentab,
            "rentab_moeda":       "BRL",
            "rentab_brl_pct":     rentab,
            "retorno_brl_disponivel": True,
            "sem_marcacao":       sem_marcacao,
            "pct_carteira":       0.0,
            "cor":                _CLASS_COR.get(classe_raw, "#718096"),
        })

    # Ordena por valor de mercado DESC (mantém UX original)
    posicoes.sort(key=lambda p: p["valor_mercado"], reverse=True)

    base_total = total_mercado if total_mercado > 0 else total_investido
    n_live = sum(1 for p in posicoes if p.get("cotacao_fonte") == "live")
    n_stale = sum(1 for p in posicoes if p.get("cotacao_fonte") == "stale")
    for p in posicoes:
        p["pct_carteira"] = p["valor_mercado"] / base_total * 100 if base_total > 0 else 0.0

    diferenca_total = round(total_mercado - total_investido, 2)
    rentabilidade_total = round(
        diferenca_total / total_investido * 100, 2
    ) if total_investido > 0 else 0.0
    data_ref_min, data_ref_max = intervalo_referencia(
        [p.get("data_referencia") for p in posicoes]
    )
    avisos_dados = []
    if n_stale:
        avisos_dados.append(
            f"{n_stale} cotação(ões) excedem o limite de frescor e foram marcadas como desatualizadas."
        )

    return {
        "total_investido":         round(total_investido, 2),
        "total_mercado":           round(total_mercado, 2),
        "diferenca_reais":         diferenca_total,
        "rentabilidade_total_pct": rentabilidade_total,
        "rentabilidade_total_disponivel": True,
        "num_ativos":              len(posicoes),
        "cotacoes_disponiveis":    n_live > 0,
        "n_cotacoes_live":         n_live,
        "n_cotacoes_stale":        n_stale,
        "data_referencia_min":     data_ref_min,
        "data_referencia_max":     data_ref_max,
        "posicoes":                posicoes,
        "por_classe":              _agregar_por_classe(posicoes),
        "por_setor":               _agregar_por_setor(posicoes),
        "sem_marcacao":            resumo_sem_marcacao(posicoes),
        "avisos_dados":            avisos_dados,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de agregação (sem dependência de fonte de dados)
# ─────────────────────────────────────────────────────────────────────────────

def _agregar_por_classe(posicoes: list) -> list:
    """
    Agrega posições por classe de ativo.
    Retorna lista ordenada por valor_mercado DESC.
    """
    buckets: dict[str, dict] = {}
    for p in posicoes:
        cls = p["classe"]
        if cls not in buckets:
            buckets[cls] = {
                "nome":            cls,
                "valor_mercado":   0.0,
                "total_investido": 0.0,
                "num_ativos":      0,
                "cor":             p["cor"],
            }
        buckets[cls]["valor_mercado"]   += p["valor_mercado"]
        buckets[cls]["total_investido"] += p["total_investido"]
        buckets[cls]["num_ativos"]      += 1
        # O retorno da classe só soma quem tem marcação: um papel com custo
        # igual ao valor puxaria a média para zero sem ter retorno medido.
        if p.get("sem_marcacao"):
            buckets[cls]["sem_marcacao_n"] = buckets[cls].get("sem_marcacao_n", 0) + 1
        else:
            buckets[cls]["vm_marcado"] = buckets[cls].get("vm_marcado", 0.0) + p["valor_mercado"]
            buckets[cls]["ti_marcado"] = buckets[cls].get("ti_marcado", 0.0) + p["total_investido"]

    total = sum(b["valor_mercado"] for b in buckets.values()) or 1.0
    resultado = []
    for b in sorted(buckets.values(), key=lambda x: x["valor_mercado"], reverse=True):
        vm = b["valor_mercado"]
        ti_m = b.pop("ti_marcado", 0.0)
        vm_m = b.pop("vm_marcado", 0.0)
        rentab = round((vm_m - ti_m) / ti_m * 100, 2) if ti_m > 0 else 0.0
        resultado.append({
            **b,
            "pct_carteira": vm / total * 100,
            "rentab_pct":   rentab,
            "sem_marcacao_n": b.get("sem_marcacao_n", 0),
        })
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# API pública — cashflow mensal (para página Investimentos)
# ─────────────────────────────────────────────────────────────────────────────

_MESES_PT_CF = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

# Fonte única em ``core/categorias.py``: este literal existia em DUAS cópias
# byte-a-byte (aqui e em ``core/investimentos.py``) e um ``frozenset`` que não
# batia com elas (``memoria: guarda-duplicada-diverge``).
_INVESTMENT_CATEGORY_SQL = SQL_INVESTIMENTO

# Consulta própria para não herdar a v_monthly_cashflow, que classifica por sinal.
_SQL_CASHFLOW = f"""
    SELECT
        DATE_TRUNC('month', t.due_date)::DATE AS month_year,
        SUM(CASE
            WHEN t.type IN ('income', 'entrada')
                 AND COALESCE(c.name, '') NOT IN ({_INVESTMENT_CATEGORY_SQL})
                THEN t.amount
            ELSE 0
        END)                                  AS total_income,
        SUM(CASE
            WHEN t.type IN ('expense', 'saida')
                 AND COALESCE(a.type, '') != 'credit_card'
                 AND COALESCE(c.name, '') NOT IN ({_INVESTMENT_CATEGORY_SQL})
                THEN ABS(t.amount)
            ELSE 0
        END)                                  AS total_expenses_abs,
        SUM(CASE
            WHEN t.type IN ('investment', 'investimento')
                 OR COALESCE(c.name, '') IN ({_INVESTMENT_CATEGORY_SQL})
                THEN ABS(t.amount)
            ELSE 0
        END)                                  AS total_investments
    FROM transactions t
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN accounts a ON a.id = t.account_id
    WHERE t.user_id = :uid
      AND t.status  = 'settled'
      AND (
          t.type IN ('income', 'entrada', 'expense', 'saida', 'investment', 'investimento', 'transfer')
          OR COALESCE(c.name, '') IN ({_INVESTMENT_CATEGORY_SQL})
      )
    GROUP BY DATE_TRUNC('month', t.due_date)::DATE
    ORDER BY month_year DESC
    LIMIT 12
"""


@user_cache_data(ttl=300)
def get_cashflow_mensal() -> list:
    """
    Retorna os últimos 12 meses de cashflow em ordem cronológica.
    Cada item: {label, ano, mes, receitas, despesas, saldo}
    Uso: gráfico de barras na página Investimentos.
    """
    if settings.MOCK_MODE:
        return _cashflow_mock()
    try:
        return _cashflow_real()
    except Exception as exc:
        logger.warning("[investimentos] cashflow indisponível (%s).", type(exc).__name__)
        return []


def _cashflow_mock() -> list:
    from datetime import date as _date
    hoje = _date.today()
    result = []
    for i in range(11, -1, -1):
        m = hoje.month - i
        y = hoje.year
        while m <= 0:
            m += 12
            y -= 1
        receitas      = 8_500.0 + (i % 3) * 250.0
        despesas      = 3_400.0 + (i % 5) * 280.0
        investimentos = 5_000.0 + (i % 4) * 500.0
        result.append({
            "label":         f"{_MESES_PT_CF[m]}/{str(y)[-2:]}",
            "ano":           y,
            "mes":           m,
            "receitas":      round(receitas, 2),
            "despesas":      round(despesas, 2),
            "saldo":         round(receitas - despesas, 2),
            "investimentos": round(investimentos, 2),
        })
    return result


def _cashflow_real() -> list:
    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_CASHFLOW), {"uid": owner}).fetchall()

    result = []
    for r in reversed(rows):
        my  = r.month_year
        receitas = float(r.total_income or 0)
        despesas = float(r.total_expenses_abs or 0)
        investimentos = float(r.total_investments or 0)
        result.append({
            "label":         f"{_MESES_PT_CF[my.month]}/{str(my.year)[-2:]}",
            "ano":           my.year,
            "mes":           my.month,
            "receitas":      round(receitas, 2),
            "despesas":      round(despesas, 2),
            "saldo":         round(receitas - despesas, 2),
            "investimentos": round(investimentos, 2),
        })
    return result


def _agregar_por_setor(posicoes: list) -> list:
    """
    Agrega posições por setor.
    Retorna lista ordenada por valor_mercado DESC.
    """
    buckets: dict[str, float] = defaultdict(float)
    for p in posicoes:
        buckets[p["setor"]] += p["valor_mercado"]

    total = sum(buckets.values()) or 1.0
    return sorted(
        [
            {
                "nome":           setor,
                "valor_mercado":  round(vm, 2),
                "pct_carteira":   vm / total * 100,
            }
            for setor, vm in buckets.items()
        ],
        key=lambda x: x["valor_mercado"],
        reverse=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# API pública — evolução patrimonial histórica
# ─────────────────────────────────────────────────────────────────────────────

# Só ativos em reais: somar preço em dólar da Nomad com preço em reais da B3
# dava um "aporte" que não existiu em moeda nenhuma.
_SQL_EVOLUCAO_TX = """
    SELECT
        DATE_TRUNC('month', t.transaction_date) AS mes,
        SUM(CASE WHEN t.type = 'buy'  THEN  t.quantity * t.unit_price
                 WHEN t.type = 'sell' THEN -(t.quantity * t.unit_price)
                 ELSE 0
        END) AS delta_investido
    FROM investment_transactions t
    JOIN assets a ON a.id = t.asset_id
    WHERE t.user_id = :uid
      AND COALESCE(a.currency, 'BRL') = 'BRL'
    GROUP BY 1
    ORDER BY 1
"""

# Ticker-base em SQL, com a regra de `_base_ticker`: BBAS3F e MXRF11F caem em
# BBAS3 e MXRF11. O dedup de proventos particiona por ele, e não por asset_id,
# porque o mesmo pagamento gravado como BBAS3 (B3) e BBAS3F (XP) são dois
# asset_id e entrava duas vezes nas somas.
_SQL_TICKER_BASE = """
    CASE WHEN UPPER(TRIM(a.ticker)) LIKE '%F'
              AND (LENGTH(TRIM(a.ticker)) > 4 OR UPPER(TRIM(a.ticker)) LIKE '%11F')
         THEN LEFT(UPPER(TRIM(a.ticker)), LENGTH(TRIM(a.ticker)) - 1)
         ELSE UPPER(TRIM(a.ticker))
    END
"""

_SQL_EVOLUCAO_DIV = """
    WITH dedup AS (
        SELECT
            d.*,
            ROW_NUMBER() OVER (
                PARTITION BY """ + _SQL_TICKER_BASE + """, d.payment_date, d.type, ROUND(d.total_amount::numeric, 2)
                ORDER BY CASE
                    WHEN d.external_id LIKE 'b3mov-%' THEN 0
                    WHEN d.external_id LIKE 'xpcsl-%' THEN 1
                    ELSE 2
                END,
                d.id
            ) AS rn
        FROM dividends d
        JOIN assets a ON a.id = d.asset_id
        WHERE d.user_id = :uid
          AND d.payment_date IS NOT NULL
          -- Provento anunciado e ainda não pago não é ganho.
          AND d.payment_date <= CURRENT_DATE
    )
    SELECT
        DATE_TRUNC('month', payment_date) AS mes,
        -- Amortização devolve o próprio capital: não é rendimento (A-128, e
        -- INV-A2 da auditoria de 04/10/2026, que achou R$ 6,0 mil dela
        -- somados ao ganho). Sai numa coluna à parte.
        SUM(total_amount) FILTER (WHERE LOWER(COALESCE(type, '')) <> 'amortization')
            AS delta_dividendos,
        SUM(total_amount) FILTER (WHERE LOWER(COALESCE(type, '')) = 'amortization')
            AS delta_amortizacao
    FROM dedup
    WHERE rn = 1
    GROUP BY 1
    ORDER BY 1
"""

_SQL_EVOLUCAO_RATIO = """
    SELECT
        COALESCE(SUM(pp.total_invested), 1) AS total_inv,
        COALESCE(SUM(pp.quantity * COALESCE(aq.close, pp.average_price)), 1) AS total_mkt
    FROM portfolio_positions pp
    LEFT JOIN LATERAL (
        SELECT close FROM asset_quotes WHERE asset_id = pp.asset_id
        ORDER BY timestamp DESC LIMIT 1
    ) aq ON true
    WHERE pp.user_id = :uid
"""

_SQL_EVOLUCAO_SNAPSHOTS = """
    -- Serie temporal de patrimonio baseada nos snapshots XP.
    -- Aceita tanto a fonte antiga (source_table='xp_positions', migracao
    -- via migrate_app2_investimentos.py) quanto a nova
    -- (source_table='xp_consolidado', upload manual em Configuracoes).
    -- DISTINCT ON garante uma linha por report_date mesmo se ambas as
    -- fontes existirem (prioriza xp_consolidado > xp_positions).
    -- Atualizado 2026-05-23 — antes era hardcoded source_system='app2'.
    WITH xp_pref AS (
        SELECT DISTINCT ON (report_date)
            report_date, source_system, source_table
        FROM portfolio_position_snapshots
        WHERE user_id = :uid
          AND source_table IN ('xp_consolidado', 'xp_positions')
          AND COALESCE(source_id, '') NOT LIKE 'td-snap-%'
        ORDER BY report_date,
                 CASE source_table
                     WHEN 'xp_consolidado' THEN 0
                     WHEN 'xp_positions'   THEN 1
                     ELSE 2
                 END
    ),
    xp_snaps AS (
        SELECT
            pps.report_date,
            SUM(pps.market_value)              AS vm,
            -- Custo so quando TODA posicao com valor tem custo. Soma parcial
            -- e amostra (as fotos antigas da XP vem sem invested_value) e o
            -- grafico desenhava "custo zero" sob R$ 600 mil de mercado.
            CASE
                WHEN BOOL_AND(COALESCE(pps.invested_value, 0) > 0)
                     FILTER (WHERE pps.market_value > 0)
                THEN SUM(pps.invested_value) FILTER (WHERE pps.market_value > 0)
            END AS vi
        FROM portfolio_position_snapshots pps
        JOIN xp_pref xp
          ON xp.report_date  = pps.report_date
         AND xp.source_system = pps.source_system
         AND xp.source_table  = pps.source_table
        -- Acao emprestada (aba "Posicao - Emprestimos") continua sendo do
        -- investidor e entra no patrimonio. Sem ela, dez/23 dava R$ 254 mil
        -- contra R$ 590.520,23 da Evolucao Patrimonial da B3; com ela,
        -- 2022 a 2025 batem com a B3 no centavo.
        WHERE pps.user_id = :uid
          -- As seis linhas do Tesouro rotuladas xp_consolidado caem na mesma
          -- data do consolidado de ago/26; sem este filtro o Tesouro somava
          -- duas vezes.
          AND COALESCE(pps.source_id, '') NOT LIKE 'td-snap-%'
        GROUP BY pps.report_date
    )
    SELECT
        x.report_date AS mes,
        x.vm AS valor_mercado,
        x.vi AS valor_investido_snapshot
    FROM xp_snaps x
    ORDER BY x.report_date
"""

# Exterior (Nomad) nas datas das fotos da B3. A B3 nao enxerga a Nomad, entao
# o ponto historico e recomposto: quantidade pelas notas de corretagem ate a
# data, preco do ativo e USDBRL pelo ultimo fechamento de asset_quotes ate ela.
_SQL_EVOLUCAO_EXTERIOR_TX = """
    SELECT upper(a.ticker)            AS ticker,
           it.asset_id::text          AS asset_id,
           it.transaction_date::date  AS data,
           lower(it.type)             AS tipo,
           it.quantity                AS quantidade,
           it.unit_price              AS preco
    FROM   investment_transactions it
    JOIN   assets a ON a.id = it.asset_id
    WHERE  it.user_id = :uid
      AND  upper(coalesce(a.currency, 'BRL')) = 'USD'
      AND  lower(it.type) IN ('buy', 'sell')
      AND  it.quantity > 0
    ORDER  BY it.transaction_date, it.id
"""

_SQL_EVOLUCAO_EXTERIOR_PRECOS = """
    SELECT d.data, t.asset_id::text AS asset_id, q.close
    FROM   unnest(CAST(:datas AS date[])) AS d(data)
    CROSS  JOIN unnest(CAST(:ativos AS uuid[])) AS t(asset_id)
    LEFT JOIN LATERAL (
        SELECT close
        FROM   asset_quotes
        WHERE  asset_id = t.asset_id
          AND  close > 0
          AND  timestamp::date <= d.data
          AND  timestamp::date >= d.data - :janela
        ORDER  BY timestamp DESC
        LIMIT  1
    ) q ON true
"""

_SQL_EVOLUCAO_EXTERIOR_FX = """
    SELECT d.data, q.close
    FROM   unnest(CAST(:datas AS date[])) AS d(data)
    LEFT JOIN LATERAL (
        SELECT aq.close
        FROM   asset_quotes aq
        JOIN   assets fa ON fa.id = aq.asset_id
        WHERE  fa.ticker = 'USDBRL'
          AND  aq.close > 0
          AND  aq.timestamp::date <= d.data
          AND  aq.timestamp::date >= d.data - :janela
        ORDER  BY aq.timestamp DESC
        LIMIT  1
    ) q ON true
"""

#: Dias que a busca de preco anda para tras da data da foto: fim de mes em
#: fim de semana mais feriado americano.
_JANELA_EXTERIOR_DIAS = 10


def ganho_total(evolucao: dict, realizado: dict | None = None) -> dict | None:
    """Ganho da carteira: (mercado − custo) + lucro realizado em vendas + proventos.

    Não é "mercado + proventos": o provento reinvestido já virou cota e está no
    valor de mercado, e somá-lo de novo conta duas vezes. Aqui ele entra uma
    vez só, reinvestido ou sacado, porque a valorização é medida contra o
    custo -- e o custo do que foi comprado com provento está nele.

    O custo é o da carteira ATUAL: o que já foi vendido saiu dele. Sem o
    ``realizado`` (de ``get_resultado_realizado``), o lucro ou prejuízo dessas
    vendas sumia da conta. ``None`` sem custo ou sem valor de mercado.
    """
    mercado = (evolucao or {}).get("total_mercado")
    custo = (evolucao or {}).get("total_investido")
    if mercado is None or not custo:
        return None
    proventos = float((evolucao or {}).get("total_dividendos") or 0.0)
    valorizacao = float(mercado) - float(custo)
    disponivel = bool(realizado) and realizado.get("ganho") is not None
    vendas = float(realizado["ganho"]) if disponivel else 0.0
    return {
        "valorizacao": valorizacao,
        "realizado": vendas if disponivel else None,
        "proventos": proventos,
        "ganho": valorizacao + vendas + proventos,
    }


#: Classes cujo rendimento aparece como provento em ``dividends``. Renda fixa,
#: fundo e FIP rendem dentro do valor de mercado e não geram linha de provento.
_CLASSES_COM_PROVENTO = frozenset({
    "Ações BR", "FII", "ETF", "ETF Brasil", "ETF Internacional", "BDR",
})


def _pct_valor(posicoes: list, predicado) -> float | None:
    total = sum(float(p.get("valor_mercado") or 0) for p in posicoes)
    if total <= 0:
        return None
    parte = sum(float(p.get("valor_mercado") or 0) for p in posicoes if predicado(p))
    return round(parte / total * 100, 1)


def _custo_confiavel(p: dict) -> bool:
    return (not p.get("sem_marcacao")
            and not p.get("custo_estimado")
            and p.get("custo_fonte") != "mercado_fallback")


def decompor_ganho(evolucao: dict, realizado: dict | None = None,
                   carteira: dict | None = None) -> dict | None:
    """Ganho em três parcelas, cada uma com a cobertura e o que ficou de fora.

    O "ganho total" único somava populações diferentes (INV-A2, 04/10/2026):
    R$ 208,7 mil que contradiziam a TIR de -0,94% a.a. das ações. Aqui as
    parcelas ficam separadas e nenhuma é apresentada como retorno da carteira:

    * ``nao_realizado`` -- mercado menos custo da carteira de hoje. Cobertura:
      % do valor de mercado cujo custo é confiável (nem estimado, nem imputado
      pelo valor de mercado, nem papel sem marcação).
    * ``realizado`` -- lucro de vendas de renda variável da B3 pelo extrato de
      negociação, a preço médio. Cobertura: parcela do valor vendido que tinha
      custo no extrato (venda de posição anterior ao extrato fica fora e é
      declarada em ``valor_sem_custo``). ``None`` se o extrato não foi lido.
    * ``proventos`` -- só renda (dividendo, JCP, rendimento). Amortização sai
      em ``devolucao_capital``: é o dinheiro do próprio cotista de volta.
      Cobertura: % do patrimônio nas classes que distribuem proventos; o
      resto rende dentro do valor de mercado.

    ``None`` sem custo ou sem valor de mercado.
    """
    evolucao = evolucao or {}
    mercado = evolucao.get("total_mercado")
    custo = evolucao.get("total_investido")
    if mercado is None or not custo:
        return None
    posicoes = list((carteira or {}).get("posicoes") or [])

    nao_realizado = {
        "valor": float(mercado) - float(custo),
        "cobertura_pct": _pct_valor(posicoes, _custo_confiavel) if posicoes else None,
        "nota": "mercado - custo da carteira de hoje",
    }

    disponivel = bool(realizado) and realizado.get("ganho") is not None
    if disponivel:
        vendido = float(realizado.get("valor_vendido") or 0.0)
        sem_custo = float(realizado.get("valor_sem_custo") or 0.0)
        cobre = vendido / (vendido + sem_custo) * 100 if vendido + sem_custo > 0 else None
        parc_real = {"valor": float(realizado["ganho"]),
                     "cobertura_pct": round(cobre, 1) if cobre is not None else None,
                     "valor_sem_custo": sem_custo,
                     "nota": "vendas de renda variável da B3, a preço médio"}
    else:
        parc_real = {"valor": None, "cobertura_pct": None, "valor_sem_custo": None,
                     "nota": (realizado or {}).get("motivo")
                     or "extrato de negociação indisponível"}

    amort = evolucao.get("total_amortizacao")
    proventos = {
        "valor": float(evolucao.get("total_dividendos") or 0.0),
        "cobertura_pct": (_pct_valor(posicoes, lambda p: p.get("classe") in _CLASSES_COM_PROVENTO)
                          if posicoes else None),
        "devolucao_capital": float(amort) if amort is not None else None,
        "nota": "dividendos, JCP e rendimentos pagos; amortização é devolução de capital",
    }
    return {"nao_realizado": nao_realizado, "realizado": parc_real, "proventos": proventos}


def _meses_entre(de: str, ate: str) -> int:
    """Meses de ``de`` a ``ate``, ambos ``YYYY-MM``."""
    a0, m0 = (int(x) for x in de.split("-"))
    a1, m1 = (int(x) for x in ate.split("-"))
    return (a1 - a0) * 12 + (m1 - m0)


def crescimento_patrimonio(snapshots: list) -> dict | None:
    """Taxa média anual (CAGR) do valor de mercado, da primeira à última foto.

    Mede o crescimento do PATRIMÔNIO, aportes incluídos -- não é rentabilidade.
    Começa na primeira foto com valor positivo; exige ao menos 12 meses entre
    as pontas, porque anualizar menos que isso infla qualquer variação.
    ``None`` sem pontas utilizáveis.
    """
    pontos = [s for s in snapshots or []
              if s.get("mes_str") and (s.get("valor_mercado") or 0) > 0]
    if len(pontos) < 2:
        return None
    ini, fim = pontos[0], pontos[-1]
    meses = _meses_entre(ini["mes_str"], fim["mes_str"])
    if meses < 12:
        return None
    anos = meses / 12
    taxa = (fim["valor_mercado"] / ini["valor_mercado"]) ** (1 / anos) - 1
    return {"taxa": taxa, "de": ini["label"], "ate": fim["label"], "anos": anos}


def crescimento_proventos(historico_anual: list, primeiro_pagamento=None,
                          hoje=None) -> dict | None:
    """Taxa média anual (CAGR) dos proventos recebidos por ano civil.

    Só anos completos: sai o ano corrente e sai o primeiro ano quando o
    primeiro pagamento não foi em janeiro -- um ano de meio expediente na
    ponta inicial inflaria a taxa. Exige dois anos completos com o primeiro
    positivo. ``None`` sem isso.
    """
    from datetime import date as _date

    hoje = hoje or _date.today()
    anos = [a for a in historico_anual or [] if a["ano"] < hoje.year]
    if anos and primeiro_pagamento and primeiro_pagamento.month > 1 \
            and anos[0]["ano"] == primeiro_pagamento.year:
        anos = anos[1:]
    if len(anos) < 2 or anos[0]["total"] <= 0:
        return None
    ini, fim = anos[0], anos[-1]
    n = fim["ano"] - ini["ano"]
    taxa = (max(fim["total"], 0.0) / ini["total"]) ** (1 / n) - 1
    return {"taxa": taxa, "de": ini["ano"], "ate": fim["ano"],
            "valor_de": ini["total"], "valor_ate": fim["total"]}


@user_cache_data(ttl=300)
def get_resultado_realizado() -> dict:
    """Lucro realizado em vendas da renda variável B3, pelo extrato de negociação.

    ``{"ganho": None, "motivo": ...}`` quando não dá para calcular: o card
    mostra o ganho sem as vendas e diz por quê, em vez de tratar como zero.
    """
    if settings.MOCK_MODE:
        return {"ganho": None, "motivo": "lucro de vendas não é simulado em modo mock"}
    try:
        from core.database import get_engine
        from core.ir_renda_variavel import carregar_operacoes, resultado_realizado

        engine = get_engine()
        if engine is None:
            raise RuntimeError("Engine indisponível.")
        transacoes, eventos = carregar_operacoes(engine, settings.OWNER_USER_ID)
        return resultado_realizado(transacoes, eventos)
    except Exception as exc:
        logger.warning("[investimentos] lucro realizado indisponível (%s).", type(exc).__name__)
        return {"ganho": None,
                "motivo": "não foi possível ler o extrato de negociação da B3"}


@user_cache_data(ttl=300)
def get_evolucao_patrimonial() -> dict:
    """
    Retorna série histórica mensal para o gráfico de Evolução Patrimonial.
    Schema: {data_source, snapshots, total_investido, total_mercado, total_dividendos}
    Cada snapshot: {label, mes_str, valor_investido, valor_mercado, valor_com_dividendos}

    Quando portfolio_position_snapshots existir (caminho preferencial):
      - Usa snapshots XP (source_system='app2') como série histórica.
      - Para o snapshot mais recente, soma automaticamente os valores
        de outras fontes (Nomad etc.) ao invés de criar pontos separados.
    Fallback: investment_transactions + ratio de rentabilidade atual.
    """
    if settings.MOCK_MODE:
        d = _evolucao_mock()
        d["data_source"] = "mock"
        return d
    try:
        d = _evolucao_real()
        d["data_source"] = "real"
        return d
    except Exception as exc:
        logger.warning("[investimentos] evolução indisponível (%s).", type(exc).__name__)
        return {
            "data_source": "error",
            "error_message": "Não foi possível carregar a evolução patrimonial real.",
            "snapshots": [],
            "total_investido": 0.0,
            "total_mercado": 0.0,
            "total_dividendos": 0.0,
        }


def _evolucao_real() -> dict:
    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        has_snapshots = bool(conn.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'portfolio_position_snapshots'
            )
        """)).scalar())
        if has_snapshots:
            snap_rows = conn.execute(text(_SQL_EVOLUCAO_SNAPSHOTS), {"uid": owner}).fetchall()
            if snap_rows:
                div_rows = conn.execute(text(_SQL_EVOLUCAO_DIV), {"uid": owner}).fetchall()
                tx_rows = conn.execute(text(_SQL_EVOLUCAO_TX), {"uid": owner}).fetchall()
                current_rows = conn.execute(text(_SQL_POSICOES_SNAPSHOT), {"uid": owner}).fetchall()
                # A evolucao compara custo com mercado; se o custo da tela
                # vem da declaracao do usuario, o da curva tem de vir dela
                # tambem -- duas respostas para "quanto custou" e um degrau
                # inexplicavel no grafico.
                # O ponto de hoje e o mesmo Patrimonio Total do Dashboard,
                # exterior incluido: a mesma pergunta com duas montagens dava
                # dois numeros.
                current_totals = (
                    _carteira_snapshot_consolidada(conn, owner, current_rows)
                    if current_rows else None
                )
                try:
                    exterior = _exterior_nas_datas(conn, owner, [r.mes for r in snap_rows])
                    exterior_ok = True
                except Exception as exc:
                    logger.warning("[investimentos] exterior historico indisponivel (%s).",
                                   type(exc).__name__)
                    exterior, exterior_ok = {}, False
                d = _montar_evolucao_snapshot(snap_rows, div_rows, current_totals, tx_rows,
                                              exterior)
                d["exterior_historico_ok"] = exterior_ok
                return d
        tx_rows   = conn.execute(text(_SQL_EVOLUCAO_TX),    {"uid": owner}).fetchall()
        div_rows  = conn.execute(text(_SQL_EVOLUCAO_DIV),   {"uid": owner}).fetchall()
        ratio_row = conn.execute(text(_SQL_EVOLUCAO_RATIO), {"uid": owner}).fetchone()

    total_inv_atual = float(ratio_row.total_inv or 1)
    total_mkt_atual = float(ratio_row.total_mkt or total_inv_atual)
    rentab_ratio    = total_mkt_atual / total_inv_atual

    tx_map  = {r.mes: float(r.delta_investido  or 0) for r in tx_rows}
    div_map = {r.mes: float(r.delta_dividendos or 0) for r in div_rows}

    all_months = sorted(set(tx_map) | set(div_map))
    if not all_months:
        raise RuntimeError("Sem transações de investimento.")

    cum_inv = 0.0
    cum_div = 0.0
    snapshots    = []
    fluxo_mensal = []
    for mes in all_months:
        delta    = tx_map.get(mes, 0.0)
        cum_inv += delta
        cum_div += div_map.get(mes, 0.0)
        cum_mkt  = round(max(cum_inv, 0) * rentab_ratio, 2)
        label    = f"{_MESES_PT_CF[mes.month]}/{str(mes.year)[-2:]}"
        mes_str  = mes.strftime("%Y-%m")
        snapshots.append({
            "label":               label,
            "mes_str":             mes_str,
            "valor_investido":     round(cum_inv, 2),
            "valor_mercado":       cum_mkt,
            "valor_com_dividendos": round(cum_mkt + cum_div, 2),
        })
        fluxo_mensal.append({
            "label":   label,
            "mes_str": mes_str,
            "aporte":  round(delta, 2),
            "ano":     mes.year,
            "mes":     mes.month,
        })

    return {
        "snapshots":        snapshots,
        "fluxo_mensal":     fluxo_mensal,
        "total_investido":  round(cum_inv, 2),
        "total_mercado":    round(total_mkt_atual, 2),
        "total_dividendos": round(cum_div, 2),
    }


def _exterior_nas_datas(conn, owner: str, datas: list) -> dict:
    """``{data: {"vm", "vi", "faltando"}}`` do exterior em cada data de foto.

    ``vm`` soma so as posicoes com preco na data; as sem preco vao para
    ``faltando`` pelo ticker, em vez de entrarem a zero sem aviso. ``vi`` e o
    custo em BRL pelo cambio de cada compra; ``None`` se faltar esse cambio.
    """
    from sqlalchemy import text

    tx = conn.execute(text(_SQL_EVOLUCAO_EXTERIOR_TX), {"uid": owner}).fetchall()
    if not tx or not datas:
        return {}
    datas = sorted(set(datas))
    ativos = sorted({r.asset_id for r in tx})
    datas_fx = sorted(set(datas) | {r.data for r in tx})
    params = {"janela": _JANELA_EXTERIOR_DIAS}
    precos = {
        (r.data, r.asset_id): float(r.close)
        for r in conn.execute(text(_SQL_EVOLUCAO_EXTERIOR_PRECOS),
                              {**params, "datas": datas, "ativos": ativos}).fetchall()
        if r.close is not None
    }
    fx = {
        r.data: float(r.close)
        for r in conn.execute(text(_SQL_EVOLUCAO_EXTERIOR_FX),
                              {**params, "datas": datas_fx}).fetchall()
        if r.close is not None
    }

    # Por ticker: quantidade, custo em BRL (None = cambio da compra ausente).
    carteira: dict[str, dict] = {}
    resultado: dict = {}
    i = 0
    for data in datas:
        while i < len(tx) and tx[i].data <= data:
            r = tx[i]
            i += 1
            pos = carteira.setdefault(r.ticker, {"asset_id": r.asset_id, "qtd": 0.0, "custo": 0.0})
            qtd = float(r.quantidade or 0)
            if r.tipo == "buy":
                taxa = fx.get(r.data)
                pos["qtd"] += qtd
                if pos["custo"] is not None:
                    pos["custo"] = (pos["custo"] + qtd * float(r.preco or 0) * taxa
                                    if taxa else None)
            elif pos["qtd"] > 0:
                fracao = min(qtd, pos["qtd"]) / pos["qtd"]
                if pos["custo"] is not None:
                    pos["custo"] -= pos["custo"] * fracao
                pos["qtd"] = max(pos["qtd"] - qtd, 0.0)

        vm, vi, faltando = 0.0, 0.0, []
        taxa_hoje = fx.get(data)
        for ticker, pos in sorted(carteira.items()):
            if pos["qtd"] <= 1e-6:
                continue
            preco = precos.get((data, pos["asset_id"]))
            if preco is None or not taxa_hoje:
                faltando.append(ticker)
            else:
                vm += pos["qtd"] * preco * taxa_hoje
            vi = None if vi is None or pos["custo"] is None else vi + pos["custo"]
        resultado[data] = {"vm": round(vm, 2),
                           "vi": round(vi, 2) if vi is not None else None,
                           "faltando": faltando}
    return resultado


def _montar_evolucao_snapshot(snap_rows: list, div_rows: list, current_totals: dict | None = None,
                              tx_rows: list | None = None, exterior: dict | None = None) -> dict:
    """Série de patrimônio pelas fotos e fluxo de aporte pelo extrato.

    ``aporte`` é compra − venda do extrato no mês. Antes era a variação do
    valor de mercado entre duas fotos: valorização virava "aporte" e uma
    queda de preço virava "resgate", e entre fotos anuais o mês inteiro do
    ano sumia dentro de um único ponto.
    """
    # Proventos acumulados ATÉ o mês de cada foto, não só os pagos no mês da
    # foto: com fotos anuais (dez/20 … dez/25) a soma por mês igual deixava de
    # fora tudo o que foi pago de janeiro a novembro, e o "Ganho total" saía
    # com uma fração dos proventos.
    div_por_mes = sorted(
        ((r.mes.year, r.mes.month), float(r.delta_dividendos or 0)) for r in div_rows
    )

    def _div_ate(ano: int, mes_: int) -> float:
        return sum(v for chave, v in div_por_mes if chave <= (ano, mes_))

    amort_por_mes = [
        ((r.mes.year, r.mes.month), float(getattr(r, "delta_amortizacao", None) or 0))
        for r in div_rows
    ]

    snapshots = []
    fluxo_mensal = []
    cum_div = 0.0
    cum_amort = 0.0

    for r in snap_rows:
        mes = r.mes
        vm = float(r.valor_mercado or 0)
        vi = r.valor_investido_snapshot
        vi = float(vi) if vi is not None else None
        # A foto da B3 nao tem a Nomad; o exterior da mesma data entra aqui,
        # senao o ponto de hoje (que tem) salta ~R$ 100 mil sobre o anterior.
        ext = (exterior or {}).get(mes)
        if ext:
            vm += ext["vm"]
            if vi is not None:
                vi = vi + ext["vi"] if ext["vi"] is not None else None
        cum_div = _div_ate(mes.year, mes.month)
        cum_amort = sum(v for chave, v in amort_por_mes if chave <= (mes.year, mes.month))
        label = f"{_MESES_PT_CF[mes.month]}/{str(mes.year)[-2:]}"
        mes_str = mes.strftime("%Y-%m")
        snapshots.append({
            "label":               label,
            "mes_str":             mes_str,
            # None = custo desconhecido na foto: o grafico abre uma lacuna
            # em vez de desenhar custo zero.
            "valor_investido":     round(vi, 2) if vi is not None else None,
            "valor_mercado":       round(vm, 2),
            "valor_com_dividendos": round(vm + cum_div, 2),
            "exterior_sem_preco":  list(ext["faltando"]) if ext else [],
        })

    if current_totals:
        from datetime import date as _date

        hoje = _date.today()
        current_month = _date(hoje.year, hoje.month, 1)
        label = f"{_MESES_PT_CF[current_month.month]}/{str(current_month.year)[-2:]}"
        mes_str = current_month.strftime("%Y-%m")
        current_vm = round(float(current_totals.get("total_mercado") or 0), 2)
        current_vi = round(float(current_totals.get("total_investido") or 0), 2)
        cum_div = _div_ate(current_month.year, current_month.month)
        cum_amort = sum(v for chave, v in amort_por_mes
                        if chave <= (current_month.year, current_month.month))
        current_snapshot = {
            "label":               label,
            "mes_str":             mes_str,
            "valor_investido":     current_vi,
            "valor_mercado":       current_vm,
            "valor_com_dividendos": round(current_vm + cum_div, 2),
        }
        if snapshots and snapshots[-1]["mes_str"] == mes_str:
            snapshots[-1] = current_snapshot
        else:
            snapshots.append(current_snapshot)

    for r in sorted(tx_rows or [], key=lambda r: r.mes):
        mes = r.mes
        fluxo_mensal.append({
            "label":   f"{_MESES_PT_CF[mes.month]}/{str(mes.year)[-2:]}",
            "mes_str": mes.strftime("%Y-%m"),
            "aporte":  round(float(r.delta_investido or 0), 2),
            "ano":     mes.year,
            "mes":     mes.month,
        })

    latest = snapshots[-1] if snapshots else {}
    return {
        "snapshots":        snapshots,
        "fluxo_mensal":     fluxo_mensal,
        "total_investido":  latest.get("valor_investido") or 0.0,
        "total_mercado":    latest.get("valor_mercado", 0.0),
        "total_dividendos": round(cum_div, 2),
        "total_amortizacao": round(cum_amort, 2),
    }


def _evolucao_mock() -> dict:
    from datetime import date as _date
    hoje  = _date.today()
    start = hoje.year - 4

    cum_inv = 0.0
    cum_div = 0.0
    snapshots    = []
    fluxo_mensal = []
    for yr in range(start, hoje.year + 1):
        for mo in range(1, 13):
            if yr == hoje.year and mo > hoje.month:
                break
            age_frac = ((yr - start) * 12 + mo) / (4 * 12)
            delta    = 3_200.0 + (mo % 4) * 450.0
            cum_inv += delta
            cum_div += 320.0 + (mo % 3) * 110.0
            cum_mkt  = round(cum_inv * (1.0 + 0.18 * age_frac), 2)
            label    = f"{_MESES_PT_CF[mo]}/{str(yr)[-2:]}"
            mes_str  = f"{yr}-{mo:02d}"
            snapshots.append({
                "label":               label,
                "mes_str":             mes_str,
                "valor_investido":     round(cum_inv, 2),
                "valor_mercado":       cum_mkt,
                "valor_com_dividendos": round(cum_mkt + cum_div, 2),
            })
            fluxo_mensal.append({
                "label":   label,
                "mes_str": mes_str,
                "aporte":  round(delta, 2),
                "ano":     yr,
                "mes":     mo,
            })

    return {
        "snapshots":        snapshots,
        "fluxo_mensal":     fluxo_mensal,
        "total_investido":  round(cum_inv, 2),
        "total_mercado":    round(snapshots[-1]["valor_mercado"], 2) if snapshots else 0.0,
        "total_dividendos": round(cum_div, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# API pública — rentabilidade (TIR) da renda variável na B3 contra o CDI
# ─────────────────────────────────────────────────────────────────────────────

_SQL_RENTAB_TX_B3 = """
    SELECT t.transaction_date::date AS data, a.ticker, t.type AS tipo,
           t.quantity AS quantidade, t.unit_price AS preco,
           COALESCE(t.fees, 0) AS taxas
    FROM investment_transactions t
    JOIN assets a ON a.id = t.asset_id
    WHERE t.user_id = :uid
      AND t.broker = 'B3'
      AND t.type IN ('buy', 'sell')
    ORDER BY t.transaction_date
"""

# Mesma deduplicação de _SQL_EVOLUCAO_DIV, por evento e não por mês. Só o que
# já foi pago: provento anunciado com data futura ainda não é dinheiro.
_SQL_RENTAB_PROVENTOS_B3 = """
    WITH dedup AS (
        SELECT d.*, a.ticker,
            ROW_NUMBER() OVER (
                PARTITION BY """ + _SQL_TICKER_BASE + """, d.payment_date, d.type, ROUND(d.total_amount::numeric, 2)
                ORDER BY CASE
                    WHEN d.external_id LIKE 'b3mov-%' THEN 0
                    WHEN d.external_id LIKE 'xpcsl-%' THEN 1
                    ELSE 2
                END,
                d.id
            ) AS rn
        FROM dividends d
        JOIN assets a ON a.id = d.asset_id
        WHERE d.user_id = :uid
          AND d.payment_date IS NOT NULL
          AND d.payment_date <= CURRENT_DATE
          AND COALESCE(a.currency, 'BRL') = 'BRL'
    )
    SELECT payment_date::date AS data, ticker, total_amount AS valor
    FROM dedup
    WHERE rn = 1
"""

# Linhas cruas da Movimentação da B3 (SQL 075). Só as que mexem em quantidade
# ou caixa fora da negociação interessam; o filtro fica em core.movimentacao_b3.
_SQL_RENTAB_EVENTOS_B3 = """
    SELECT event_date AS data, ticker, movement AS movimento,
           direction AS sentido, quantity AS quantidade, total_value AS valor
    FROM investment_movement_events
    WHERE user_id = :uid
      AND event_date <= CURRENT_DATE
"""


def _ler_eventos_movimentacao(engine, owner: str) -> list | None:
    """Linhas da Movimentação, ou None se a tabela ainda não existe.

    Conexão própria: no Postgres a falha de uma consulta aborta a transação
    inteira, e a leitura das negociações não pode morrer junto.
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            return conn.execute(text(_SQL_RENTAB_EVENTOS_B3), {"uid": owner}).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.info("[rentabilidade] movimentação da B3 indisponível: %s", type(exc).__name__)
        return None


# Classes da posição que são negociadas na B3 em reais.
_CLASSES_RV_B3 = {"Ações BR", "FII", "ETF", "ETF Brasil", "BDR"}


@user_cache_data(ttl=3600)
def _cdi_diario_cache(inicio_iso: str, fim_iso: str) -> dict:
    from datetime import date as _date

    from core.rentabilidade import obter_cdi

    return obter_cdi(_date.fromisoformat(inicio_iso), _date.fromisoformat(fim_iso))


@user_cache_data(ttl=300)
def get_rentabilidade_rv_b3() -> dict:
    """TIR da renda variável negociada na B3 e a mesma sequência de fluxos no CDI.

    Mede só os ativos cujo extrato de negociação fecha com a posição de hoje
    (ver ``core.rentabilidade.conciliar_universo``) e devolve a cobertura:
    quanto do valor atual em renda variável B3 entrou na conta.
    """
    if settings.MOCK_MODE:
        return {"data_source": "mock", "disponivel": False,
                "motivo": "Rentabilidade não é simulada em modo mock."}
    try:
        return _rentabilidade_rv_b3_real()
    except Exception as exc:
        logger.warning("[investimentos] rentabilidade indisponível (%s).", type(exc).__name__)
        return {"data_source": "error", "disponivel": False,
                "motivo": "Não foi possível carregar o extrato de negociação."}


def _rentabilidade_rv_b3_real() -> dict:
    from datetime import date as _date

    from sqlalchemy import text

    from core.database import get_engine
    from core.movimentacao_b3 import interpretar
    from core.rentabilidade import cdi_anualizado, comparar_com_cdi, conciliar_universo

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        tx_rows = conn.execute(text(_SQL_RENTAB_TX_B3), {"uid": owner}).fetchall()
        prov_rows = conn.execute(text(_SQL_RENTAB_PROVENTOS_B3), {"uid": owner}).fetchall()

    if not tx_rows:
        return {"data_source": "real", "disponivel": False,
                "motivo": "Nenhuma negociação da B3 importada."}

    transacoes = [
        {"data": r.data, "ticker": _base_ticker(r.ticker), "tipo": r.tipo,
         "quantidade": float(r.quantidade or 0), "preco": float(r.preco or 0),
         "taxas": float(r.taxas or 0)}
        for r in tx_rows
    ]
    proventos = [
        {"data": r.data, "ticker": _base_ticker(r.ticker), "valor": float(r.valor or 0)}
        for r in prov_rows
    ]

    # O valor final é o da MESMA posição que a aba Carteira mostra — preço,
    # preço manual e fonte já resolvidos lá. Uma segunda leitura da posição
    # daria dois patrimônios para a mesma pergunta.
    carteira = get_carteira()
    posicoes: dict[str, dict] = {}
    for p in carteira.get("posicoes") or []:
        if p.get("moeda") != "BRL" or p.get("classe") not in _CLASSES_RV_B3:
            continue
        tk = _base_ticker(p.get("ticker") or "")
        cur = posicoes.setdefault(tk, {"quantidade": 0.0, "valor_mercado": 0.0})
        cur["quantidade"] += float(p.get("quantidade") or 0)
        cur["valor_mercado"] += float(p.get("valor_mercado") or 0)

    ev_rows = _ler_eventos_movimentacao(engine, owner)
    movimentacao = interpretar([
        {"data": r.data, "ticker": _base_ticker(r.ticker), "movimento": r.movimento,
         "sentido": r.sentido, "quantidade": r.quantidade, "valor": r.valor}
        for r in ev_rows or []
    ])
    universo = conciliar_universo(transacoes, proventos, posicoes, movimentacao=movimentacao)
    hoje = _date.today()
    fluxos = universo["fluxos"]
    if not fluxos:
        return {"data_source": "real", "disponivel": False,
                "motivo": "Nenhum ativo com extrato que feche com a posição.",
                "excluidos": universo["excluidos"]}

    inicio = fluxos[0][0]
    cdi_info = _cdi_diario_cache(inicio.isoformat(), hoje.isoformat())
    cdi = cdi_info["serie"]
    comp = comparar_com_cdi(fluxos, universo["valor_final"], hoje, cdi)

    aportes = -sum(v for _, v in fluxos if v < 0)
    retiradas = sum(v for _, v in fluxos if v > 0)
    cobertura = (
        universo["valor_final"] / universo["valor_total"]
        if universo["valor_total"] > 0 else None
    )
    return {
        "data_source": "real",
        "disponivel": comp["tir_carteira"] is not None,
        "motivo": None if comp["tir_carteira"] is not None else
                  "Os fluxos não têm troca de sinal suficiente para uma TIR.",
        "inicio": inicio,
        "fim": hoje,
        "tir_carteira": comp["tir_carteira"],
        "tir_cdi": comp["tir_cdi"],
        "cdi_periodo_aa": cdi_anualizado(cdi, inicio, hoje) if comp["cobertura_cdi"] else None,
        "valor_final": universo["valor_final"],
        "valor_cdi": comp["valor_cdi"],
        "diferenca": comp["diferenca"],
        "pme": comp["pme"],
        "cdi_disponivel": comp["cobertura_cdi"],
        "cdi_fonte": cdi_info["fonte"],
        "cdi_motivo": None if comp["cobertura_cdi"] else
                      (cdi_info["motivo"] or "A série do CDI não cobre o período."),
        "aportes": aportes,
        "retiradas": retiradas,
        "cobertura_valor": cobertura,
        "n_incluidos": len(universo["incluidos"]),
        "n_em_carteira": universo["n_em_carteira"],
        "n_em_carteira_incluidos": universo["n_em_carteira_incluidos"],
        "incluidos": universo["incluidos"],
        "excluidos": universo["excluidos"],
        # 0 = a Movimentação nunca foi subida com a tabela de eventos; a tela
        # avisa, porque bonificação e subscrição ficam de fora por isso.
        "eventos_movimentacao": len(ev_rows or []),
    }
