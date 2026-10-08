"""
core/financeiro.py
Camada de serviço financeiro — abstrai a fonte de dados entre mock e banco real.

USE_MOCK é controlado por settings.MOCK_MODE (lido do .env ou de Streamlit Secrets).
  MOCK_MODE=true  → retorna dados de core/mock_data.py
  MOCK_MODE=false → executa queries SQL via core/database.py

Chave "data_source" sempre presente no dict retornado:
  "real"          → dados do banco, tudo OK
  "mock"          → MOCK_MODE=true, mock intencional
  "mock_fallback" → banco falhou, caiu no mock automaticamente

Views consultadas (Fase 4.9):
  v_net_worth, v_account_balance (saldo bancário em reais), v_monthly_cashflow,
  v_category_spending_mtd, portfolio_positions (resumo por classe); proventos via core.proventos; budgets via core.orcamento

Padrão de uso nas páginas:
    from core.financeiro import get_visao_geral
    dados = get_visao_geral()
    fonte = dados["data_source"]   # "real" | "mock" | "mock_fallback"
    patrimonio = dados["patrimonio"]["total"]
"""
import logging
import math
from datetime import date

from core import orcamento
from core.config import settings
from core.fx_aquisicao import cambio_medio_de_aquisicao
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)


#: Janela da média de despesa da reserva: 6 meses FECHADOS (o LIMIT 7 da consulta
#: traz o corrente mais seis anteriores).
_JANELA_RESERVA_MESES = 6


def calcular_meses_reserva(
    saldo: float,
    cashflow: list[dict],
    hoje: date | None = None,
) -> tuple[float, str]:
    """Quantos meses de despesa o saldo bancário cobre.

    Divide pela média dos meses FECHADOS (até 6), nunca pelo mês corrente: no
    dia 4 o mês tem 4 dias de despesa e o saldo parecia durar 45,9 meses, contra
    19,8 pela média dos meses fechados (auditoria de 04/10/2026, CF-M1).

    ``cashflow``: dicts com ``month_year`` (date) e ``expenses`` (valor absoluto).
    Devolve ``(meses, base)``; ``base`` é ``"fechados"`` ou ``"mes_parcial"``
    (só quando NÃO existe mês fechado -- dado observado, mas parcial, e a tela
    avisa) ou ``"sem_dado"`` (0.0 = ausência, não "reserva zerada").
    """
    hoje = hoje or date.today()
    primeiro_do_mes = date(hoje.year, hoje.month, 1)

    def _dia(m) -> date:
        return m.date() if hasattr(m, "date") and callable(m.date) else m

    fechados = [r for r in cashflow if _dia(r["month_year"]) < primeiro_do_mes]
    fechados = sorted(fechados, key=lambda r: _dia(r["month_year"]), reverse=True)
    fechados = fechados[:_JANELA_RESERVA_MESES]
    if fechados:
        media = sum(float(r["expenses"] or 0) for r in fechados) / len(fechados)
        base = "fechados"
    else:
        parcial = [r for r in cashflow if float(r["expenses"] or 0) > 0]
        if not parcial:
            return 0.0, "sem_dado"
        media = float(parcial[0]["expenses"])
        base = "mes_parcial"
    return ((saldo / media) if media > 0 else 0.0), base


@user_cache_data(ttl=300)
def get_visao_geral() -> dict:
    """
    Retorna o dicionário completo de dados para o Dashboard Geral.

    Chaves retornadas:
        data_source       str   "real" | "mock" | "mock_fallback"
        mes_referencia    str
        patrimonio        dict  (total, investido, saldo_bancario, delta_mes_pct, saude_score,
                                 fora_do_total: o que ficou fora por falta de câmbio)
        fluxo_mes         dict  (receitas, despesas, economia, taxa_poupanca_pct, ...)
        historico_mensal  list  (6 meses: mes, receitas, despesas, economia, patrimonio)
        categorias_despesa list (nome, gasto, orcamento, pct_usado)
        portfolio         dict  (rentabilidade_mes_pct, dividendos_mes, ...)
        classes_ativo     list  (nome, valor, pct_carteira, rentab_mes_pct, cor)
        alertas           list  (tipo, icone, titulo, descricao, acao, modulo)
        proximos_passos   list  (numero, urgencia, titulo, descricao, modulo)
    """
    if settings.MOCK_MODE:
        dados = _visao_geral_mock()
        dados["data_source"] = "mock"
        return dados

    # Modo real: tenta banco — fallback para mock em caso de qualquer erro
    try:
        dados = _visao_geral_real()
        dados["data_source"] = "real"
        return dados
    except Exception as exc:
        from core.user_context import principal
        if principal():
            raise RuntimeError("Não foi possível carregar seus dados financeiros.") from None
        logger.warning(
            "[financeiro] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        dados = _visao_geral_mock()
        dados["data_source"] = "mock_fallback"
        return dados


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _visao_geral_mock() -> dict:
    """Constrói a visão geral a partir de core/mock_data.py."""
    import core.mock_data as m

    return {
        "mes_referencia":     m.MES_REFERENCIA,
        "patrimonio":         m.PATRIMONIO,
        "fluxo_mes":          m.FLUXO_MES,
        "historico_mensal":   m.HISTORICO_MENSAL,
        "categorias_despesa": m.CATEGORIAS_DESPESA,
        "portfolio":          m.PORTFOLIO,
        "classes_ativo":      m.CLASSES_ATIVO,
        "alertas":            m.ALERTAS_DASHBOARD,
        "proximos_passos":    m.PROXIMOS_PASSOS,
        # data_source injetado pelo caller (get_visao_geral)
    }


# ─────────────────────────────────────────────────────────────────────────────
# REAL — queries nas views do Supabase
# ─────────────────────────────────────────────────────────────────────────────

# Mapeamentos de classe de ativo
_CLASS_LABEL: dict[str, str] = {
    "reit":         "FII",
    "stock":        "Ações BR",
    "fixed_income": "Renda Fixa",
    "etf":          "ETF",
    "stock_us":     "Ações EUA",
    "etf_intl":     "ETF Internacional",
    "bdr":          "BDR",
    "crypto":       "Cripto",
}
_CLASS_COR: dict[str, str] = {
    "reit":         "#E84C9B",
    "fii":          "#E84C9B",
    "stock":        "#4C9BE8",
    "fixed_income": "#A855F7",
    "renda_fixa":   "#A855F7",
    "tesouro":      "#2ECC71",
    "fundo_rf":     "#7C3AED",
    "etf":          "#F5A623",
    "etf_br":       "#F5A623",
    "etf_intl":     "#63cab7",
    "stock_us":     "#3B82F6",
    "bdr":          "#4C9BE8",
    "crypto":       "#FF6B35",
    "other":        "#8b9ab0",
}
_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def _resumo_por_classe(linhas, universo=None, fx_compra=None,
                       cambio_hoje=None) -> tuple[list[dict], int, float]:
    """(classes_ativo, num_ativos, total_mercado_brl), uma linha por ativo.

    Em dólar, ``stock`` e ``etf`` do cadastro não dizem "Ações BR" nem "ETF"
    da B3: a classe sai de ``classe_ativo_usd`` (ticker, nome, cadastro).

    Posição em dólar entra em reais pela regra de ``core.investimentos``:
    mercado pelo câmbio de hoje (``usd_brl_rate`` da linha, senão yfinance),
    custo pelo câmbio da compra (``fx_compra``, de ``core.fx_aquisicao``) e,
    sem ele, pelo de hoje -- e então a rentabilidade da classe sai ``None``.
    Sem câmbio válido a posição fica fora do resumo -- somar dólar como real
    era o defeito da view.

    ``universo`` e ``cambio_hoje`` existem para teste.
    """
    from core.classe_exterior import classe_ativo_usd, universo_acoes_eua
    from core.fx_aquisicao import taxa_para

    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    usd = [r for r in linhas if (r.currency or "BRL").upper() == "USD"]
    if usd and cambio_hoje is None:
        cambio_hoje = max((_f(getattr(r, "usd_brl_rate", None)) for r in usd),
                          default=0.0)
        if cambio_hoje < 2.0:
            from core.investimentos import _get_usd_brl_live
            cambio_hoje = _get_usd_brl_live()
    if usd and (cambio_hoje is None or cambio_hoje < 2.0):
        logger.warning("[financeiro] sem USD/BRL válido: %s fora do resumo por classe",
                       ", ".join(sorted({str(r.ticker) for r in usd})))

    grupos: dict[str, dict] = {}
    for r in linhas:
        classe = r.asset_class or "other"
        investido, mercado = _f(r.total_invested), _f(r.current_market_value)
        custo_estimado = False
        if (r.currency or "BRL").upper() == "USD":
            if cambio_hoje is None or cambio_hoje < 2.0:
                continue
            taxa_compra = taxa_para(fx_compra or {}, r.ticker)
            custo_estimado = taxa_compra is None
            taxa_custo = taxa_compra or cambio_hoje
            investido, mercado = investido * taxa_custo, mercado * cambio_hoje
            if classe in {"stock", "etf"}:
                if universo is None:
                    universo = universo_acoes_eua()
                classe = classe_ativo_usd(r.ticker, r.asset_name, r.asset_class, universo)
        g = grupos.setdefault(classe, {"ids": set(), "inv": 0.0, "val": 0.0,
                                       "estimado": False})
        g["estimado"] = g["estimado"] or custo_estimado
        g["ids"].add(r.asset_id)
        g["inv"] += investido
        g["val"] += mercado

    total_val = sum(g["val"] for g in grupos.values())
    classes = []
    for classe, g in sorted(grupos.items(), key=lambda kv: kv[1]["inv"], reverse=True):
        classes.append({
            "nome":           _CLASS_LABEL.get(classe, classe.title()),
            "valor":          g["val"],
            "pct_carteira":   round(g["val"] / total_val * 100, 1) if total_val > 0 else 0.0,
            # None = indisponível: custo em dólar sem câmbio da compra faria
            # deste o retorno em USD com rótulo de BRL.
            "rentab_mes_pct": (None if g["estimado"]
                               else round((g["val"] - g["inv"]) * 100 / g["inv"], 2)
                               if g["inv"] else 0.0),
            "cor":            _CLASS_COR.get(classe, "#718096"),
        })
    return classes, sum(len(g["ids"]) for g in grupos.values()), total_val


def _cambio_hoje(taxa_banco) -> float | None:
    """USDBRL de hoje: o do banco se válido (>= 2.0), senão yfinance."""
    taxa = float(taxa_banco) if taxa_banco is not None else 0.0
    if taxa >= 2.0:
        return taxa
    from core.investimentos import _get_usd_brl_live
    return _get_usd_brl_live()


def _saldo_bancario_brl(contas, cambio_hoje) -> tuple[float, list[str]]:
    """(saldo das contas de caixa em reais, contas que ficaram fora).

    Conta em USD entra pelo câmbio de hoje. Em outra moeda (o banco só cota
    USDBRL) ou em USD sem câmbio válido, fica fora da soma e é nomeada -- a
    mesma regra da 081, que o resumo não pode pressupor aplicada: sem ela,
    ``v_net_worth.bank_balance`` soma dólar como real.
    """
    total, fora = 0.0, []
    for c in contas:
        moeda = (c.currency or "BRL").upper()
        saldo = float(c.current_balance or 0)
        if moeda == "BRL":
            total += saldo
        elif moeda == "USD" and cambio_hoje is not None and cambio_hoje >= 2.0:
            total += saldo * cambio_hoje
        else:
            fora.append(f"{c.account_name} ({moeda})")
    if fora:
        logger.warning("[financeiro] sem câmbio: %s fora do saldo bancário",
                       ", ".join(fora))
    return total, fora


def patrimonio_investido_confiavel(
    carteira: dict | None,
    patrimonio: dict | None = None,
) -> float | None:
    """Retorna o valor de mercado verificável da carteira, sem somar saldos estimados.

    O saldo de ``v_account_balance`` é reconstruído pelo saldo inicial mais o
    histórico de transações. Sem conciliação bancária explícita, ele não deve ser
    tratado como saldo atual nem somado novamente às posições de investimento.
    """
    candidatos = (
        (carteira or {}).get("total_mercado"),
        (patrimonio or {}).get("investido"),
    )
    for candidato in candidatos:
        if candidato is None:
            continue
        try:
            valor = float(candidato)
        except (TypeError, ValueError):
            continue
        if math.isfinite(valor) and valor >= 0:
            return valor
    return None


def dividendos_do_periodo(
    proventos: dict | None,
) -> tuple[float | None, float | None]:
    """(proventos do mês, proventos do ano) recebidos, via ``core.proventos``.

    A soma direta em ``dividends`` contava o mesmo pagamento duas vezes quando
    ele vinha da B3 e da XP ou do lote padrão e do fracionário (BBAS3/BBAS3F),
    agrupava por ``ex_date`` em vez da data do pagamento e somava amortização.
    ``core.proventos`` já resolve as três coisas; aqui só se lê o resultado.
    Sem proventos reais, ``None``: o dashboard não mistura mock com dado real
    e não confunde "fonte indisponível" com "nada recebido".
    """
    if not proventos or proventos.get("data_source") != "real":
        return None, None
    return (float(proventos.get("total_mes") or 0.0),
            float(proventos.get("total_ano") or 0.0))


def _visao_geral_real() -> dict:
    """
    Consulta as views do Supabase e monta o dict de visão geral.
    Retorna o mesmo schema que _visao_geral_mock().

    Qualquer exceção propaga para o caller → get_visao_geral() faz fallback.

    SEGURANÇA:
      - Nunca expõe connection strings nem credenciais.
      - Filtra todas as queries por OWNER_USER_ID.
      - Não executa DDL, DML de escrita nem SQL parametrizado com dados do usuário.
    """
    from sqlalchemy import text

    from core.database import get_engine

    # ── Pré-condições ─────────────────────────────────────────────────────
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

        # ── 1. Patrimônio (v_net_worth) ───────────────────────────────────
        # Só o investido sai daqui, e só quando não há carteira nem posições.
        # O saldo bancário é refeito em reais no passo 6.
        nw_row = conn.execute(
            text(
                "SELECT investment_total "
                "FROM v_net_worth WHERE user_id = :uid"
            ),
            {"uid": owner},
        ).fetchone()

        investment_total = float(getattr(nw_row, "investment_total", 0) or 0)

        # ── 2. Fluxo de caixa mensal (v_monthly_cashflow) ─────────────────
        cashflow_rows = conn.execute(
            text(
                "SELECT month_year, total_income, total_expenses_abs, net_cashflow "
                "FROM v_monthly_cashflow "
                "WHERE user_id = :uid "
                "ORDER BY month_year DESC LIMIT 7"
            ),
            {"uid": owner},
        ).fetchall()

        # ── 3. Categorias do mês (v_category_spending_mtd) ────────────────
        cat_rows = conn.execute(
            text(
                "SELECT category_name, total_spent "
                "FROM v_category_spending_mtd "
                "WHERE user_id = :uid "
                "ORDER BY total_spent DESC LIMIT 10"
            ),
            {"uid": owner},
        ).fetchall()

        # ── 4. Orçamentos em vigor no mês (mesma regra do Controle) ───────
        # v_budget_usage_mtd só enxergava o limite gravado no próprio mês;
        # o Controle carrega o último limite para a frente. As duas telas
        # agora leem a mesma regra (core.orcamento.vigentes).
        from datetime import date as _date_cls

        _hoje = _date_cls.today()
        budget_map = orcamento.carregar_vigentes(conn, owner, _date_cls(_hoje.year, _hoje.month, 1))

        # ── 5. Posições para o resumo por classe ──────────────────────────
        # Mesmo cálculo de v_investment_summary, mas por ativo: a view agrupa
        # só por assets.class e põe ação americana em "Ações BR" (stock) ou
        # "ETF" (cadastro errado da Nomad), e antes da 081 somava dólar como real.
        # _resumo_por_classe separa as classes e converte.
        inv_rows = conn.execute(
            text(
                "SELECT a.id::text AS asset_id, a.class AS asset_class, "
                "       a.ticker, a.name AS asset_name, a.currency, "
                "       pp.total_invested, fx.usd_brl_rate, "
                "       pp.quantity * COALESCE(lq.close, pp.average_price) "
                "           AS current_market_value "
                "FROM portfolio_positions pp "
                "JOIN assets a ON a.id = pp.asset_id "
                "LEFT JOIN LATERAL ( "
                "    SELECT close FROM asset_quotes aq "
                "    WHERE aq.asset_id = pp.asset_id "
                "    ORDER BY aq.timestamp DESC LIMIT 1 "
                ") lq ON TRUE "
                "LEFT JOIN LATERAL ( "
                "    SELECT aq2.close AS usd_brl_rate FROM asset_quotes aq2 "
                "    JOIN assets a2 ON a2.id = aq2.asset_id "
                "    WHERE a2.ticker = 'USDBRL' "
                "    ORDER BY aq2.timestamp DESC LIMIT 1 "
                ") fx ON TRUE "
                "WHERE pp.user_id = :uid"
            ),
            {"uid": owner},
        ).fetchall()
        fx_compra = (cambio_medio_de_aquisicao(conn, owner)
                     if any((r.currency or "").upper() == "USD" for r in inv_rows)
                     else {})

        # ── 6. Contas de caixa, para o saldo bancário em reais ────────────
        # Mesmo filtro de v_net_worth (ativas, sem cartão), mas a conversão
        # fica aqui: sem a 081, a view soma conta em dólar como real.
        contas = conn.execute(
            text(
                "SELECT account_name, currency, current_balance "
                "FROM v_account_balance "
                "WHERE user_id = :uid AND active = TRUE "
                "  AND account_type != 'credit_card'"
            ),
            {"uid": owner},
        ).fetchall()
        usd_brl_banco = conn.execute(
            text(
                "SELECT q.close FROM asset_quotes q "
                "JOIN assets a ON a.id = q.asset_id "
                "WHERE a.ticker = 'USDBRL' "
                "ORDER BY q.timestamp DESC LIMIT 1"
            )
        ).scalar()

    # Um câmbio só para contas e posições: yfinance no máximo uma vez.
    usa_dolar = any((r.currency or "").upper() == "USD"
                    for r in [*inv_rows, *contas])
    cambio_hoje = _cambio_hoje(usd_brl_banco) if usa_dolar else None
    bank_balance, fora_do_total = _saldo_bancario_brl(contas, cambio_hoje)
    net_worth_total = bank_balance + investment_total

    carteira_migrada = None
    proventos_migrados = None
    try:
        from core.investimentos import get_carteira
        from core.proventos import get_proventos

        carteira_migrada = get_carteira()
        proventos_migrados = get_proventos()
    except Exception as exc:
        logger.warning("[financeiro] Carteira migrada indisponivel no dashboard geral: %s", exc)

    if carteira_migrada and carteira_migrada.get("data_source") == "real":
        investment_total = float(carteira_migrada.get("total_mercado") or investment_total)
        net_worth_total = bank_balance + investment_total
    else:
        # 0.0, e não None, quando não há câmbio válido: None faria o resumo
        # tentar o yfinance de novo.
        resumo_classes, num_ativos_resumo, total_resumo = _resumo_por_classe(
            inv_rows, fx_compra=fx_compra, cambio_hoje=cambio_hoje or 0.0)
        if inv_rows:
            # Sem a 081 aplicada, v_net_worth soma dólar como real; com ela,
            # ainda não tem o fallback do yfinance quando falta USDBRL.
            investment_total = total_resumo
            net_worth_total = bank_balance + investment_total
            if not cambio_hoje or cambio_hoje < 2.0:
                fora_do_total += sorted({f"{r.ticker} (USD)" for r in inv_rows
                                         if (r.currency or "").upper() == "USD"})

    # ── Helpers ───────────────────────────────────────────────────────────
    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    # ── Cashflow: index 0 = mês mais recente (já ordenado DESC) ──────────
    cf_list = [
        {
            "month_year": r.month_year,
            "income":     _f(r.total_income),
            "expenses":   _f(r.total_expenses_abs),
            "economia":   _f(r.net_cashflow),
        }
        for r in cashflow_rows
    ]

    cur_cf  = cf_list[0] if len(cf_list) > 0 else None
    prev_cf = cf_list[1] if len(cf_list) > 1 else None

    # Mês referência
    if cur_cf:
        my = cur_cf["month_year"]
        mes_ref = f"{_MESES_PT[my.month]} {my.year}"
    else:
        from datetime import date as _date
        today = _date.today()
        mes_ref = f"{_MESES_PT[today.month]} {today.year}"

    receitas  = cur_cf["income"]   if cur_cf else 0.0
    despesas  = cur_cf["expenses"] if cur_cf else 0.0
    economia  = cur_cf["economia"] if cur_cf else 0.0
    taxa_poup = (economia / receitas * 100) if receitas > 0 else 0.0

    rec_ant  = prev_cf["income"]   if prev_cf else 0.0
    desp_ant = prev_cf["expenses"] if prev_cf else 0.0
    econ_ant = prev_cf["economia"] if prev_cf else 0.0
    taxa_ant = (econ_ant / rec_ant * 100) if rec_ant > 0 else 0.0

    meses_reserva, reserva_base = calcular_meses_reserva(bank_balance, cf_list)

    # Delta patrimônio mês a mês (aproximação pelo net_cashflow do mês atual)
    base_prev       = net_worth_total - economia
    delta_mes_pct   = (economia / base_prev * 100) if base_prev > 0 else 0.0
    delta_mes_valor = economia

    # ── Histórico mensal (últimos 6, ordem cronológica) ───────────────────
    hist_6 = cf_list[:6][::-1]
    historico_mensal = [
        {
            "mes":        _MESES_PT[r["month_year"].month],
            "receitas":   r["income"],
            "despesas":   r["expenses"],
            "economia":   r["economia"],
            "patrimonio": net_worth_total if i == len(hist_6) - 1 else 0.0,
        }
        for i, r in enumerate(hist_6)
    ]

    # ── Categorias de despesa ─────────────────────────────────────────────
    maior_cat_nome  = cat_rows[0].category_name if cat_rows else "—"
    maior_cat_valor = _f(cat_rows[0].total_spent) if cat_rows else 0.0

    # Sem limite cadastrado a categoria fica SEM orçamento: o antigo
    # "gasto × 1,2" deixava toda categoria 83% usada e dava 20 pontos cheios
    # de "orçamento respeitado" na nota de saúde a quem nunca orçou nada.
    categorias_despesa = [
        orcamento.linha_categoria(r.category_name, _f(r.total_spent),
                                  budget_map.get(r.category_name))
        for r in cat_rows[:7]
    ]
    todas_orcadas = orcamento.consumo(
        {r.category_name: _f(r.total_spent) for r in cat_rows}, budget_map)
    res_orc = orcamento.resumo(todas_orcadas)
    mais_usada = max(
        (c for c in todas_orcadas if c["pct_usado"] is not None),
        key=lambda c: c["pct_usado"], default=None,
    )
    cat_alerta_nome = mais_usada["nome"] if mais_usada else maior_cat_nome
    cat_alerta_pct = mais_usada["pct_usado"] if mais_usada else 0.0

    # ── Score de saúde ────────────────────────────────────────────────────
    saude_score = calcular_saude_score(
        taxa_poupanca=taxa_poup,
        meses_reserva=meses_reserva,
        categorias_estouradas=res_orc["categorias_estouradas"],
        total_categorias=res_orc["categorias_orcadas"],
        rentabilidade_positiva=False,   # cotações ausentes → retorno = 0
    )

    # ── Classes de ativos ─────────────────────────────────────────────────
    classes_ativo = []
    if carteira_migrada and carteira_migrada.get("data_source") == "real":
        for c in carteira_migrada.get("por_classe", []):
            classes_ativo.append({
                "nome":           c["nome"],
                "valor":          _f(c.get("valor_mercado")),
                "pct_carteira":   _f(c.get("pct_carteira")),
                "rentab_mes_pct": _f(c.get("rentab_pct")),
                "cor":            c.get("cor", "#718096"),
            })
        num_ativos = int(carteira_migrada.get("num_ativos") or 0)
    else:
        classes_ativo, num_ativos = resumo_classes, num_ativos_resumo
    maior_cls  = classes_ativo[0] if classes_ativo else {"nome": "—", "pct_carteira": 0.0}

    # ── Portfolio ─────────────────────────────────────────────────────────
    dividendos_mes, dividendos_ano = dividendos_do_periodo(proventos_migrados)
    rentab_invest = (
        _f(carteira_migrada.get("rentabilidade_total_pct"))
        if carteira_migrada and carteira_migrada.get("data_source") == "real"
        else 0.0
    )

    portfolio = {
        "rentabilidade_mes_pct":          rentab_invest,
        "rentabilidade_ano_pct":          rentab_invest,
        "rentabilidade_mes_anterior_pct": 0.0,
        "dividendos_mes":                 dividendos_mes,
        "dividendos_ano":                 dividendos_ano,
        "maior_concentracao_nome":        maior_cls["nome"],
        "maior_concentracao_pct":         maior_cls.get("pct_carteira", 0.0),
        "meta_concentracao_pct":          25.0,
        "num_ativos":                     num_ativos,
    }

    # ── Alertas e próximos passos a partir dos dados reais ────────────────
    alertas         = _gerar_alertas_real(meses_reserva, taxa_poup, maior_cls, cat_alerta_nome, cat_alerta_pct)
    proximos_passos = _gerar_proximos_passos_real(meses_reserva, taxa_poup, maior_cls)

    return {
        "mes_referencia":     mes_ref,
        "patrimonio": {
            "total":           net_worth_total,
            "investido":       investment_total,
            "saldo_bancario":  bank_balance,
            "delta_mes_pct":   round(delta_mes_pct, 1),
            "delta_mes_valor": round(delta_mes_valor, 2),
            "saude_score":     saude_score,
            # Nomeados, não somados: o total acima é menor que o real.
            "fora_do_total":   fora_do_total,
        },
        "fluxo_mes": {
            "receitas":               receitas,
            "despesas":               despesas,
            "economia":               economia,
            "taxa_poupanca_pct":      round(taxa_poup, 1),
            "receitas_mes_anterior":  rec_ant,
            "despesas_mes_anterior":  desp_ant,
            "economia_mes_anterior":  econ_ant,
            "taxa_poupanca_anterior": round(taxa_ant, 1),
            "meses_reserva":          round(meses_reserva, 1),
            "reserva_base":           reserva_base,
            "meta_meses_reserva":     6.0,
            "maior_categoria":        maior_cat_nome,
            "maior_categoria_valor":  maior_cat_valor,
            "categoria_alerta":       cat_alerta_nome,
            "categoria_alerta_pct":   cat_alerta_pct,
        },
        "historico_mensal":   historico_mensal,
        "categorias_despesa": categorias_despesa,
        "portfolio":          portfolio,
        "classes_ativo":      classes_ativo,
        "alertas":            alertas,
        "proximos_passos":    proximos_passos,
        # data_source injetado pelo caller (get_visao_geral)
    }


def _gerar_alertas_real(
    meses_reserva: float,
    taxa_poupanca: float,
    maior_cls: dict,
    cat_alerta_nome: str,
    cat_alerta_pct: float,
) -> list:
    """Gera lista de alertas calculados a partir dos dados reais."""
    alertas = []

    # Reserva de emergência
    if meses_reserva < 3:
        alertas.append({
            "tipo":      "alerta",
            "icone":     "🛡️",
            "titulo":    f"Reserva de emergência em {meses_reserva:.1f}×",
            "descricao": f"Saldo cobre {meses_reserva:.1f}× as despesas mensais. Meta: 6×.",
            "acao":      "Metas",
            "modulo":    "🎯 Metas",
        })
    elif meses_reserva < 6:
        alertas.append({
            "tipo":      "info",
            "icone":     "🛡️",
            "titulo":    f"Reserva em {meses_reserva:.1f}× (meta: 6×)",
            "descricao": f"Continue aportando. Você está em {meses_reserva:.1f}× as despesas.",
            "acao":      "Metas",
            "modulo":    "🎯 Metas",
        })

    # Categoria próxima do limite (apenas se houver orçamento)
    if cat_alerta_pct >= 90:
        alertas.append({
            "tipo":      "alerta",
            "icone":     "⚠️",
            "titulo":    f"{cat_alerta_nome} próximo do limite",
            "descricao": f"{cat_alerta_pct:.0f}% do orçamento utilizado.",
            "acao":      "Controle Financeiro",
            "modulo":    "💰 Controle Financeiro",
        })

    # Concentração elevada na carteira
    if maior_cls.get("pct_carteira", 0) > 40:
        alertas.append({
            "tipo":      "info",
            "icone":     "📊",
            "titulo":    f"Alta concentração em {maior_cls['nome']}",
            "descricao": (
                f"{maior_cls['nome']} representa {maior_cls['pct_carteira']:.1f}% "
                "da carteira. Considere diversificar."
            ),
            "acao":      "Carteira",
            "modulo":    "💼 Carteira",
        })

    # Taxa de poupança boa
    if taxa_poupanca >= 30:
        alertas.append({
            "tipo":      "sucesso",
            "icone":     "🎉",
            "titulo":    "Taxa de poupança acima da meta",
            "descricao": f"Você poupou {taxa_poupanca:.1f}% da renda este mês. Meta: 30%.",
            "acao":      "Metas",
            "modulo":    "🎯 Metas",
        })

    # Garantia: ao menos um alerta
    if not alertas:
        alertas.append({
            "tipo":      "info",
            "icone":     "✅",
            "titulo":    "Finanças em dia",
            "descricao": "Nenhum alerta crítico para este mês.",
            "acao":      "",
            "modulo":    "📊 Dashboard Geral",
        })

    return alertas


def _gerar_proximos_passos_real(
    meses_reserva: float,
    taxa_poupanca: float,
    maior_cls: dict,
) -> list:
    """Gera lista de próximos passos recomendados a partir dos dados reais."""
    passos: list[dict] = []
    n = 1

    if meses_reserva < 6:
        passos.append({
            "numero":    n,
            "urgencia":  "alta" if meses_reserva < 3 else "media",
            "titulo":    "Reforçar reserva de emergência",
            "descricao": f"Meta: 6× as despesas. Atual: {meses_reserva:.1f}×.",
            "modulo":    "🎯 Metas",
        })
        n += 1

    if maior_cls.get("pct_carteira", 0) > 40:
        passos.append({
            "numero":    n,
            "urgencia":  "media",
            "titulo":    f"Diversificar além de {maior_cls['nome']}",
            "descricao": (
                f"{maior_cls['nome']} em {maior_cls['pct_carteira']:.1f}% — "
                "acima do alvo de 40%. Diluir nos próximos aportes."
            ),
            "modulo":    "💼 Carteira",
        })
        n += 1

    if taxa_poupanca < 10:
        passos.append({
            "numero":    n,
            "urgencia":  "alta",
            "titulo":    "Aumentar taxa de poupança",
            "descricao": f"Taxa atual: {taxa_poupanca:.1f}%. Meta: ≥ 30%.",
            "modulo":    "💰 Controle Financeiro",
        })
        n += 1

    # Sempre inclui: alimentar cotações
    passos.append({
        "numero":    n,
        "urgencia":  "baixa",
        "titulo":    "Alimentar cotações de mercado",
        "descricao": (
            "Cadastre preços em asset_quotes para ativar rentabilidade real "
            "e valor de mercado da carteira."
        ),
        "modulo":    "💼 Carteira",
    })

    return passos[:4]  # máximo 4 itens


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de cálculo (sem dependência de fonte de dados)
# ─────────────────────────────────────────────────────────────────────────────

def calcular_saude_score(
    taxa_poupanca: float,
    meses_reserva: float,
    categorias_estouradas: int,
    total_categorias: int,
    rentabilidade_positiva: bool,
) -> int:
    """
    Calcula o score de saúde financeira (0–100).

    Componentes:
      40 pts → taxa de poupança ≥ 30% (proporcional)
      30 pts → reserva de emergência ≥ 6 meses (proporcional)
      20 pts → orçamento respeitado (proporção de categorias fora do limite);
               sem orçamento cadastrado, os outros 80 são reescalados para 100
      10 pts → investimentos com rentabilidade positiva

    `categorias_estouradas` conta categorias com uso ≥ 90% do orçamento (ruim);
    o componente de orçamento premia quem tem MENOS categorias estouradas.
    """
    score = 0.0

    # Taxa de poupança (meta: 30%)
    score += min(taxa_poupanca / 30.0, 1.0) * 40

    # Meses de reserva (meta: 6 meses)
    score += min(meses_reserva / 6.0, 1.0) * 30

    # Rentabilidade positiva
    if rentabilidade_positiva:
        score += 10

    # Orçamento respeitado (categorias abaixo de 90% do limite). Sem nenhum
    # orçamento o componente não existe: os outros 80 pontos são reescalados
    # para 100, em vez de dar 20 de graça (o antigo) ou tirar 20 de quem só
    # não orçou (punir a ausência como se fosse estouro).
    if total_categorias > 0:
        categorias_ok = total_categorias - categorias_estouradas
        score += (categorias_ok / total_categorias) * 20
    else:
        score = score * 100 / 80

    return round(score)
