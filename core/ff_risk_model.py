"""
core/ff_risk_model.py — Modelo de risco Fama-French 3-5 fatores para Brasil.

Implementa a recomendação M1 do parecer da banca examinadora (2026-05-23):
estrutura de risco via modelo de fatores, permitindo estimar covariância
inter-ativos de forma mais estável do que a amostra N×N direta.

Arquitetura do modelo de risco fatorial:

    Σ_portfólio = B · Σ_F · B' + D_idio

onde:
  B        — matriz N×K de betas dos ativos nos fatores (N ativos, K ≤ 5 fatores)
  Σ_F      — matriz K×K de covariância dos fatores (muito estável: K pequeno)
  D_idio   — matriz diagonal de variâncias idiossincráticas

Vantagem vs Markowitz puro: evitar estimar N×N covariâncias com dados
escassos (instável quando N > T/5). Com K = 3-5 fatores, precisamos
estimar apenas N×K betas + K×K covariância dos fatores.

Fatores estimados para o Brasil:
  MKT-RF   — retorno excedente do mercado (BOVA11 – CDI mensal)
  SMB      — Small Minus Big (SMAL11 – BOVA11, proxy de tamanho)
  HML      — High Minus Low book-to-market (long low-P/VP, short high-P/VP)
             construído via portfólios de ativos do universo B3
  RMW      — Robust Minus Weak profitability (via sorts de ROE) [opcional]
  CMA      — Conservative Minus Aggressive investment (via sorts de crescimento) [opcional]

Referências:
  Fama, E. & French, K. (1993) "Common Risk Factors in the Returns on
    Stocks and Bonds." Journal of Financial Economics, 33, 3-56.
  Fama, E. & French, K. (2015) "A Five-Factor Asset Pricing Model."
    Journal of Financial Economics, 116(1), 1-22.
  Leal, R. & Carvalhal-da-Silva, A. (2010) "Factor Premia in Brazil."
    Working Paper, COPPEAD-UFRJ.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ETFs proxy para fatores no mercado brasileiro (yfinance)
_TICKER_MKT  = "BOVA11.SA"
_TICKER_SMAL = "SMAL11.SA"

# Taxa CDI mensal aproximada usada como risk-free (atualizar via BCB se necessário)
_CDI_ANNUAL_DEFAULT  = 0.1065
_CDI_MONTHLY_DEFAULT = (1 + _CDI_ANNUAL_DEFAULT) ** (1 / 12) - 1

# Número mínimo de observações para estimar betas confiáveis
_MIN_OBS_BETAS = 12


# ──────────────────────────────────────────────────────────────────────────────
# Tipos de dados
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FFFactors:
    """Séries temporais mensais dos retornos dos fatores."""
    dates:  pd.DatetimeIndex
    mkt_rf: np.ndarray    # MKT – RF
    smb:    np.ndarray    # Small Minus Big
    hml:    np.ndarray    # High Minus Low (value factor)
    rmw:    np.ndarray | None = None  # Robust Minus Weak
    cma:    np.ndarray | None = None  # Conservative Minus Aggressive

    @property
    def factor_matrix(self) -> np.ndarray:
        """Array T×K com os fatores disponíveis."""
        cols = [self.mkt_rf, self.smb, self.hml]
        if self.rmw is not None:
            cols.append(self.rmw)
        if self.cma is not None:
            cols.append(self.cma)
        return np.column_stack(cols)

    @property
    def factor_names(self) -> list[str]:
        names = ["MKT-RF", "SMB", "HML"]
        if self.rmw is not None:
            names.append("RMW")
        if self.cma is not None:
            names.append("CMA")
        return names

    def __len__(self) -> int:
        return len(self.dates)


@dataclass
class FFRiskModel:
    """Resultado completo do modelo de risco Fama-French."""
    tickers:       list[str]
    betas:         dict[str, np.ndarray]   # ticker → array K de betas
    idio_var:      dict[str, float]         # ticker → variância idiossincrática mensal
    factor_cov:    np.ndarray              # K×K covariância dos fatores
    factor_names:  list[str]
    r2:            dict[str, float]         # ticker → R² do ajuste OLS
    n_obs:         int                      # T (observações temporais usadas)
    warnings:      list[str] = field(default_factory=list)

    def covariance_matrix(self) -> tuple[np.ndarray, list[str]]:
        """
        Calcula Σ = B · Σ_F · B' + D_idio.

        Returns:
          (cov: ndarray N×N, tickers: list[str])
        """
        K = len(self.factor_names)
        N = len(self.tickers)
        B = np.zeros((N, K))
        D = np.zeros(N)

        for i, tk in enumerate(self.tickers):
            if tk in self.betas:
                B[i] = self.betas[tk]
                D[i] = self.idio_var.get(tk, 1e-4)
            else:
                # Sem betas estimados: assume beta de mercado 1 e alta vol idiossincrática
                B[i, 0] = 1.0
                D[i] = 5e-3

        cov = B @ self.factor_cov @ B.T + np.diag(D)
        cov = (cov + cov.T) / 2          # garante simetria numérica
        cov += 1e-8 * np.eye(N)          # regularização mínima
        return cov, self.tickers

    def factor_exposure_summary(self) -> pd.DataFrame:
        """
        Retorna DataFrame com exposição fatorial de cada ticker.
        Útil para exibir na UI (tabela de betas por ativo).
        """
        rows = []
        for tk in self.tickers:
            row = {"Ticker": tk, "R²": round(self.r2.get(tk, float("nan")), 3)}
            if tk in self.betas:
                for name, b in zip(self.factor_names, self.betas[tk]):
                    row[name] = round(float(b), 3)
            else:
                for name in self.factor_names:
                    row[name] = float("nan")
            row["Vol idio (% a.m.)"] = round(
                float(np.sqrt(self.idio_var.get(tk, 0))) * 100, 2
            )
            rows.append(row)
        return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Busca de preços via yfinance
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_monthly_prices(
    tickers: list[str],
    n_months: int = 60,
) -> pd.DataFrame:
    """
    Busca preços mensais de fechamento via yfinance.

    Returns DataFrame (datas × tickers) ou DataFrame vazio em caso de erro.
    """
    try:
        import yfinance as yf
        years = max(n_months // 12 + 1, 2)
        raw = yf.download(
            tickers,
            period=f"{min(years, 10)}y",
            interval="1mo",
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            level0 = raw.columns.get_level_values(0)
            if "Close" in level0:
                prices = raw["Close"]
            else:
                prices = raw.iloc[:, : len(tickers)]
                prices.columns = tickers[: prices.shape[1]]
        else:
            prices = raw[["Close"]] if "Close" in raw.columns else raw
            if len(tickers) == 1:
                prices.columns = [tickers[0]]
        return prices.dropna(how="all")
    except Exception:
        return pd.DataFrame()


def _monthly_returns_from_prices(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.pct_change().dropna(how="all")


# ──────────────────────────────────────────────────────────────────────────────
# Construção dos fatores BR
# ──────────────────────────────────────────────────────────────────────────────

def _long_short_factor(
    ret_df: pd.DataFrame,
    fund: pd.DataFrame,
    indicator: str,
    low_is_long: bool,
    common_dates: pd.DatetimeIndex,
    quantile_cut: float = 0.30,
) -> np.ndarray | None:
    """
    Constrói fator long-short via sort de indicador fundamentalista.

    Metodologia Fama-French: bottom 30% (ou top 30%, dependendo de
    low_is_long) vai para o lado long; lado oposto vai para short.
    Retorno do fator = média dos retornos long – média dos short.
    """
    if indicator not in fund.columns:
        return None

    tickers_disp = [t for t in fund.index if t in ret_df.columns]
    vals = pd.to_numeric(fund.loc[tickers_disp, indicator], errors="coerce").dropna()
    if len(vals) < 8:
        return None

    cut_low  = vals.quantile(quantile_cut)
    cut_high = vals.quantile(1 - quantile_cut)

    if low_is_long:
        long_tks  = vals[vals <= cut_low].index.tolist()
        short_tks = vals[vals >= cut_high].index.tolist()
    else:
        long_tks  = vals[vals >= cut_high].index.tolist()
        short_tks = vals[vals <= cut_low].index.tolist()

    long_tks  = [t for t in long_tks  if t in ret_df.columns]
    short_tks = [t for t in short_tks if t in ret_df.columns]

    if not long_tks or not short_tks:
        return None

    long_ret  = ret_df.loc[common_dates, long_tks].mean(axis=1).values
    short_ret = ret_df.loc[common_dates, short_tks].mean(axis=1).values
    return long_ret - short_ret


def build_br_factors_etf(
    n_months: int = 60,
    cdi_monthly: float = _CDI_MONTHLY_DEFAULT,
) -> FFFactors | None:
    """
    Constrói fatores MKT-RF e SMB via ETFs BOVA11 e SMAL11.
    HML = proxy via -SMB (correlação documentada para Brasil).

    Usado quando dados individuais de tickers não estão disponíveis.
    """
    prices = _fetch_monthly_prices([_TICKER_MKT, _TICKER_SMAL], n_months=n_months)
    if prices.empty or len(prices) < 12:
        return None
    if isinstance(prices.columns, pd.MultiIndex):
        prices.columns = prices.columns.get_level_values(-1)

    ret = _monthly_returns_from_prices(prices)
    if _TICKER_MKT not in ret.columns or _TICKER_SMAL not in ret.columns:
        return None

    n       = min(len(ret[_TICKER_MKT].dropna()), len(ret[_TICKER_SMAL].dropna()))
    ret     = ret.dropna(subset=[_TICKER_MKT, _TICKER_SMAL]).iloc[-n:]
    mkt_rf  = ret[_TICKER_MKT].values - cdi_monthly
    smb     = ret[_TICKER_SMAL].values - ret[_TICKER_MKT].values
    hml_px  = -0.30 * smb   # proxy: HML tende a ser negativamente correlacionado com SMB no Brasil

    return FFFactors(
        dates  = ret.index,
        mkt_rf = mkt_rf,
        smb    = smb,
        hml    = hml_px,
    )


def build_br_factors_full(
    df_fundamentals: pd.DataFrame,
    ticker_prices: dict[str, pd.DataFrame],
    n_months: int = 60,
    cdi_monthly: float = _CDI_MONTHLY_DEFAULT,
) -> FFFactors | None:
    """
    Constrói fatores 3-5 a partir de portfólios de ativos B3.

    Metodologia adaptada de Fama & French (1993, 2015) para o Brasil:

    MKT-RF : retorno excedente de BOVA11 sobre o CDI mensal
    SMB    : retorno médio de small caps (P/VP alto como proxy) minus large
    HML    : long low-P/VP (value) minus short high-P/VP (growth)
    RMW    : long high-ROE (robust) minus short low-ROE (weak)   [se ROE disponível]
    CMA    : long baixo crescimento (conservative) minus alto     [se slope disponível]

    Args:
      df_fundamentals : DataFrame com Ticker como coluna ou index + indicadores
      ticker_prices   : dict {ticker: DataFrame com coluna 'Preco' ou 'Close'}
      n_months        : janela histórica (meses)
      cdi_monthly     : taxa CDI mensal para excedente de mercado

    Returns:
      FFFactors ou None se dados insuficientes.
    """
    # ── Fator de mercado via ETF ──────────────────────────────────────────
    prices_etf = _fetch_monthly_prices([_TICKER_MKT], n_months=n_months)
    if prices_etf.empty or len(prices_etf) < 12:
        return None
    if isinstance(prices_etf.columns, pd.MultiIndex):
        prices_etf.columns = prices_etf.columns.get_level_values(-1)
    if _TICKER_MKT not in prices_etf.columns:
        return None

    mkt_ret_full = prices_etf[_TICKER_MKT].pct_change().dropna()

    # ── Retornos mensais dos tickers individuais ──────────────────────────
    all_rets: dict[str, pd.Series] = {}
    for tk, df_px in (ticker_prices or {}).items():
        if df_px is None or df_px.empty:
            continue
        df_px = df_px.copy()
        try:
            if "Data" in df_px.columns:
                df_px = df_px.set_index("Data")
            px_col = next((c for c in ("Preco", "Close", "Adj Close") if c in df_px.columns), None)
            if px_col is None:
                continue
            px = df_px[px_col].resample("ME").last().dropna()
            ret = px.pct_change().dropna()
            if len(ret) >= _MIN_OBS_BETAS:
                all_rets[tk] = ret
        except Exception:
            continue

    # Fallback: poucos tickers com preços → usa só ETF
    if len(all_rets) < 10:
        return build_br_factors_etf(n_months=n_months, cdi_monthly=cdi_monthly)

    # Alinha datas
    ret_df = pd.DataFrame(all_rets).dropna(how="all")
    common_dates = ret_df.index.intersection(mkt_ret_full.index)
    if len(common_dates) < 12:
        return build_br_factors_etf(n_months=n_months, cdi_monthly=cdi_monthly)

    ret_df   = ret_df.loc[common_dates]
    mkt_arr  = mkt_ret_full.loc[common_dates].values
    mkt_rf   = mkt_arr - cdi_monthly

    # Fundamentais indexados por ticker
    if "Ticker" in df_fundamentals.columns:
        fund = df_fundamentals.set_index("Ticker")
    else:
        fund = df_fundamentals.copy()

    tickers_fund = [t for t in ret_df.columns if t in fund.index]
    if len(tickers_fund) < 8:
        return build_br_factors_etf(n_months=n_months, cdi_monthly=cdi_monthly)

    # ── SMB (proxy via P/VP como inverso de market cap) ──────────────────
    # P/VP alto → possivelmente small/value. Aqui usamos como proxy de tamanho
    # (empresas maiores tendem a ter P/VP moderado-alto). Limitação conhecida:
    # sem market cap direto, P/VP é proxy imperfeito de tamanho.
    smb = _long_short_factor(
        ret_df, fund.loc[tickers_fund], "P/VP",
        low_is_long=False,        # P/VP baixo → large cap (short do SMB)
        common_dates=common_dates,
    )
    if smb is None:
        smb = np.zeros(len(common_dates))

    # ── HML (high book-to-market = low P/VP) ─────────────────────────────
    hml = _long_short_factor(
        ret_df, fund.loc[tickers_fund], "P/VP",
        low_is_long=True,         # P/VP baixo → value → long HML
        common_dates=common_dates,
    )
    if hml is None:
        hml = np.zeros(len(common_dates))

    # ── RMW (profitability factor via ROE) ────────────────────────────────
    rmw = None
    if "ROE" in fund.columns:
        rmw = _long_short_factor(
            ret_df, fund.loc[tickers_fund], "ROE",
            low_is_long=False,    # alto ROE → robust → long
            common_dates=common_dates,
        )

    # ── CMA (investment factor via slope de crescimento) ──────────────────
    cma = None
    growth_col = next(
        (c for c in ("Receita_slope_log", "Lucro_slope_log") if c in fund.columns), None
    )
    if growth_col:
        cma = _long_short_factor(
            ret_df, fund.loc[tickers_fund], growth_col,
            low_is_long=True,     # baixo crescimento → conservative → long CMA
            common_dates=common_dates,
        )

    T = len(common_dates)
    return FFFactors(
        dates  = common_dates,
        mkt_rf = mkt_rf[:T],
        smb    = smb[:T],
        hml    = hml[:T],
        rmw    = rmw[:T] if rmw is not None else None,
        cma    = cma[:T] if cma is not None else None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Estimação de betas por OLS
# ──────────────────────────────────────────────────────────────────────────────

def estimate_factor_betas(
    ticker_returns: pd.Series,
    factors: FFFactors,
) -> tuple[np.ndarray, float, float] | None:
    """
    Estima betas de um ativo nos fatores via OLS com intercepto.

    r_i = α + β_MKT·MKT_RF + β_SMB·SMB + β_HML·HML [+ ...]  + ε_i

    Returns:
      (betas: ndarray K, r2: float, idio_var: float) ou None se insuficiente.
    """
    y = ticker_returns.dropna()
    F = pd.DataFrame(
        factors.factor_matrix,
        index=factors.dates,
        columns=factors.factor_names,
    )
    common = y.index.intersection(F.index)
    if len(common) < _MIN_OBS_BETAS:
        return None

    y_c = y.loc[common].values.astype(float)
    X_c = F.loc[common].values.astype(float)
    X_int = np.column_stack([np.ones(len(y_c)), X_c])   # intercepto

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            coefs, _, _, _ = np.linalg.lstsq(X_int, y_c, rcond=None)
        betas    = coefs[1:]   # descarta intercepto
        y_hat    = X_int @ coefs
        ss_res   = float(np.sum((y_c - y_hat) ** 2))
        ss_tot   = float(np.sum((y_c - y_c.mean()) ** 2)) or 1e-12
        r2       = max(0.0, 1.0 - ss_res / ss_tot)
        idio_var = ss_res / max(len(y_c) - len(coefs), 1)
        return betas, r2, idio_var
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# API pública principal
# ──────────────────────────────────────────────────────────────────────────────

def build_ff_risk_model(
    tickers: list[str],
    ticker_returns: dict[str, pd.Series],
    factors: FFFactors,
) -> FFRiskModel:
    """
    Estima o modelo de risco FF para uma lista de ativos.

    Args:
      tickers        : ativos do portfólio (subconjunto de ticker_returns)
      ticker_returns : dict {ticker: pd.Series de retornos mensais}
      factors        : FFFactors com séries temporais dos fatores

    Returns:
      FFRiskModel com betas, R², variâncias idiossincráticas e Σ_F.
    """
    betas_d:  dict[str, np.ndarray] = {}
    idio_d:   dict[str, float]      = {}
    r2_d:     dict[str, float]      = {}
    warns:    list[str]             = []

    for tk in tickers:
        ret = ticker_returns.get(tk)
        if ret is None or ret.dropna().__len__() < _MIN_OBS_BETAS:
            warns.append(f"{tk}: retornos insuficientes (<{_MIN_OBS_BETAS} obs)")
            continue
        result = estimate_factor_betas(ret, factors)
        if result is None:
            warns.append(f"{tk}: regressão OLS falhou")
            continue
        betas_d[tk], r2_d[tk], idio_d[tk] = result

    # Covariância empírica dos fatores — muito estável (K×K pequeno)
    F_mat = factors.factor_matrix
    if len(F_mat) >= _MIN_OBS_BETAS:
        if F_mat.ndim == 1 or F_mat.shape[1] == 1:
            factor_cov = np.array([[float(np.var(F_mat))]])
        else:
            factor_cov = np.cov(F_mat, rowvar=False, ddof=1)
    else:
        K = F_mat.shape[1] if F_mat.ndim > 1 else 1
        factor_cov = np.eye(K) * 1e-3

    return FFRiskModel(
        tickers      = tickers,
        betas        = betas_d,
        idio_var     = idio_d,
        factor_cov   = factor_cov,
        factor_names = factors.factor_names,
        r2           = r2_d,
        n_obs        = len(factors),
        warnings     = warns,
    )


def ticker_monthly_returns_from_price_df(
    df_precos: pd.DataFrame,
    tickers: list[str],
) -> dict[str, pd.Series]:
    """
    Converte DataFrame de preços (linhas=datas, colunas=tickers) em dict de retornos.
    Compatível com o formato df_precos usado em views/empresas_b3.py.
    """
    rets: dict[str, pd.Series] = {}
    for tk in tickers:
        if tk in df_precos.columns:
            s = df_precos[tk].dropna()
            if len(s) >= _MIN_OBS_BETAS + 1:
                rets[tk] = s.pct_change().dropna()
    return rets
