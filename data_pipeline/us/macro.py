"""
data_pipeline/us/macro.py
Ingestão das séries macroeconômicas dos EUA (FRED) para ``market_us``.

Por que existe: o regime macro da seção Empresas Americanas nascia de literais
no código — Fed a 4,25%, CPI a 2,5%. Esses números apareciam na tela sob o
título "Cenário Macroeconômico" e iam para o relatório institucional como se
fossem leitura de mercado. Um relatório que afirma "com o Fed em 4,25%" a
partir de uma constante está afirmando algo que ninguém verificou.

A rede vive AQUI, na ingestão, nunca na interface — o módulo americano é
offline-first na leitura. O endpoint público de CSV do FRED não exige chave.

As funções de transformação (``parse_fred_csv``, ``yoy_from_index``,
``montar_snapshot``) são puras e testadas; ``run`` orquestra com a engine.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
from typing import Iterable

logger = logging.getLogger(__name__)

_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# série FRED → (campo do USMacroSnapshot, unidade, precisa de variação a/a)
SERIES: dict[str, tuple[str, str, bool]] = {
    "FEDFUNDS":      ("fed_funds", "percent", False),
    "CPIAUCSL":      ("cpi_yoy", "index", True),
    "A191RL1Q225SBEA": ("real_gdp_yoy", "percent", False),
    "UNRATE":        ("unemployment", "percent", False),
    "T10Y2Y":        ("yield_curve_10y_2y", "pp", False),
    "BAMLH0A0HYM2":  ("high_yield_spread", "pp", False),
}


def parse_fred_csv(conteudo: str) -> list[tuple[_dt.date, float]]:
    """(data, valor) da série. Descarta '.' — o marcador de ausente do FRED."""
    linhas: list[tuple[_dt.date, float]] = []
    leitor = csv.reader(io.StringIO(conteudo))
    cabecalho = next(leitor, None)
    if not cabecalho or len(cabecalho) < 2:
        return []
    for linha in leitor:
        if len(linha) < 2:
            continue
        bruto = (linha[1] or "").strip()
        if not bruto or bruto == ".":
            continue
        try:
            data = _dt.date.fromisoformat((linha[0] or "").strip())
            valor = float(bruto)
        except (TypeError, ValueError):
            continue
        linhas.append((data, valor))
    return sorted(linhas)


def yoy_from_index(serie: list[tuple[_dt.date, float]]) -> list[tuple[_dt.date, float]]:
    """Converte série de índice (CPI) em variação percentual a/a.

    Casa por data-1 ano em vez de '12 observações atrás': série mensal com
    buraco produziria uma janela de 13 ou 11 meses sem avisar, e um CPI a/a
    calculado sobre janela errada é pior que CPI ausente.
    """
    por_data = dict(serie)
    saida: list[tuple[_dt.date, float]] = []
    for data, valor in serie:
        try:
            anterior_data = data.replace(year=data.year - 1)
        except ValueError:            # 29/02 em ano não bissexto
            anterior_data = data.replace(year=data.year - 1, day=28)
        anterior = por_data.get(anterior_data)
        if anterior in (None, 0):
            continue
        saida.append((data, (valor / anterior - 1.0) * 100.0))
    return saida


def montar_snapshot(observacoes: dict[str, tuple[_dt.date, float]]) -> dict:
    """Último valor de cada indicador + a data-base mais ANTIGA entre eles.

    A mais antiga, e não a mais recente: o regime só é válido até onde a série
    mais defasada alcança. Carimbar a data do indicador mais fresco faria o
    conjunto parecer mais atual do que é.
    """
    from core.us_macro import FONTE_OBSERVADO

    if not observacoes:
        return {}
    snapshot: dict = {campo: valor for campo, (_, valor) in observacoes.items()}
    datas = [data for data, _ in observacoes.values()]
    snapshot["fonte"] = FONTE_OBSERVADO
    snapshot["as_of"] = min(datas).isoformat()
    return snapshot


def _baixar(series_id: str, timeout: int = 20) -> str:
    import requests

    resposta = requests.get(_FRED_CSV.format(series=series_id), timeout=timeout)
    resposta.raise_for_status()
    return resposta.text


def coletar(series: Iterable[str] | None = None) -> dict[str, list[tuple[_dt.date, float]]]:
    """Baixa e transforma cada série. Falha de uma não derruba as demais."""
    alvo = list(series or SERIES)
    saida: dict[str, list[tuple[_dt.date, float]]] = {}
    for series_id in alvo:
        _campo, _unidade, precisa_yoy = SERIES.get(series_id, ("", "", False))
        try:
            linhas = parse_fred_csv(_baixar(series_id))
        except Exception as exc:  # noqa: BLE001 - série indisponível não bloqueia
            logger.warning("FRED %s indisponível: %s", series_id, exc)
            continue
        if precisa_yoy:
            linhas = yoy_from_index(linhas)
        if linhas:
            saida[series_id] = linhas
    return saida


def run(engine, anos: int = 6) -> dict:
    """Grava as séries no warehouse e devolve o resumo da execução."""
    from sqlalchemy import text

    if engine is None:
        return {"ok": False, "reason": "engine indisponível"}

    corte = _dt.date.today() - _dt.timedelta(days=365 * max(anos, 1))
    coletado = coletar()
    if not coletado:
        return {"ok": False, "reason": "nenhuma série do FRED pôde ser lida"}

    gravadas = 0
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS market_us"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS market_us.macro_observations (
                id           BIGSERIAL PRIMARY KEY,
                series_id    TEXT NOT NULL,
                indicator    TEXT NOT NULL,
                observed_at  DATE NOT NULL,
                value        NUMERIC(14,6) NOT NULL,
                unit         TEXT,
                source       TEXT NOT NULL DEFAULT 'FRED',
                ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_us_macro UNIQUE (series_id, observed_at)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_us_macro_indicator
            ON market_us.macro_observations (indicator, observed_at DESC)
        """))
        for series_id, linhas in coletado.items():
            campo, unidade, _ = SERIES[series_id]
            for data, valor in linhas:
                if data < corte:
                    continue
                conn.execute(
                    text("""
                        INSERT INTO market_us.macro_observations
                            (series_id, indicator, observed_at, value, unit)
                        VALUES (:sid, :ind, :dt, :val, :unit)
                        ON CONFLICT (series_id, observed_at)
                        DO UPDATE SET value = EXCLUDED.value,
                                      ingested_at = NOW()
                    """),
                    {"sid": series_id, "ind": campo, "dt": data,
                     "val": float(valor), "unit": unidade},
                )
                gravadas += 1

    return {
        "ok": True,
        "series": sorted(coletado),
        "observacoes_gravadas": gravadas,
        "anos": anos,
    }
