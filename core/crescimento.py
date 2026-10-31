"""Crescimento anual e retorno por ano — fonte única das telas B3 e EUA.

Regressão logarítmica não existe para valor ≤ 0. As duas telas filtravam esses
anos e regrediam sobre o que sobrava: um lucro 100 → −50 → −30 → 200 saía como
crescimento contínuo, sem dizer que dois anos de prejuízo tinham sido apagados.
Aqui a regressão corre só sobre a sequência final de valores positivos — a que
começa depois do último ano ≤ 0 — e o resultado diz de onde partiu.

O retorno anual do preço tinha o mesmo tipo de silêncio: o ano em curso entrava
na barra como se estivesse fechado. ``retornos_anuais`` marca o ano cuja última
cotação não chega ao fim de dezembro.

O preço ajustado por proventos também pode sair impossível. O yfinance multiplica
o histórico por ``1 − provento/preço``; quando um provento supera o preço do dia
(um provento de R$ 0,60 com o preço em R$ 0,05, por exemplo), o fator fica
negativo e a série cruza o zero — o gráfico chegou a mostrar −10.081% num ano. ``ultimo_preco_nao_positivo`` acha
esse ponto, e só o trecho depois dele é uma série de retorno coerente.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MOTIVOS = {
    "sem_dado": "Sem histórico",
    "ultimo_nao_positivo": "Último ano ≤ 0 — CAGR não se aplica",
    "historico_curto": "Menos de 2 anos positivos seguidos",
}


@dataclass(frozen=True)
class Crescimento:
    taxa: float | None
    inicio: int | None = None
    fim: int | None = None
    truncado: bool = False  # anos anteriores cortados por um valor ≤ 0
    motivo: str | None = None

    def legenda(self, base: str = "Regressão log") -> str:
        """Subtítulo do card: método e janela, ou por que não há número."""
        if self.taxa is None:
            return MOTIVOS.get(self.motivo or "sem_dado", MOTIVOS["sem_dado"])
        texto = f"{base} · {self.inicio}–{self.fim}"
        return texto + " (desde o último ano ≤ 0)" if self.truncado else texto


def cagr_regressao(anos, valores, minimo: int = 2) -> Crescimento:
    """Taxa anual pela inclinação de ``log(valor)`` contra ``anos``.

    ``anos`` em anos (fração permitida); ausentes são descartados antes, mas
    zero e negativo são dado — interrompem a sequência em vez de sumir.
    """
    x = pd.to_numeric(pd.Series(list(anos)), errors="coerce")
    y = pd.to_numeric(pd.Series(list(valores)), errors="coerce")
    serie = pd.DataFrame({"x": x, "y": y}).dropna().sort_values("x")
    if serie.empty:
        return Crescimento(None, motivo="sem_dado")
    nao_positivo = np.flatnonzero(serie["y"].to_numpy() <= 0)
    if len(nao_positivo) and nao_positivo[-1] == len(serie) - 1:
        return Crescimento(None, motivo="ultimo_nao_positivo")
    corte = int(nao_positivo[-1]) + 1 if len(nao_positivo) else 0
    trecho = serie.iloc[corte:]
    if len(trecho) < minimo:
        return Crescimento(None, motivo="historico_curto")
    xs = trecho["x"].to_numpy(dtype=float)
    inclinacao, _ = np.polyfit(xs - xs[0], np.log(trecho["y"].to_numpy(dtype=float)), deg=1)
    taxa = float(np.exp(inclinacao) - 1.0)
    if not np.isfinite(taxa):
        return Crescimento(None, motivo="sem_dado")
    return Crescimento(taxa, int(xs[0]), int(xs[-1]), truncado=corte > 0)


def anos_de_datas(datas) -> pd.Series:
    """Datas → anos com fração (31/12/2023 ≈ 2023,997) para ``cagr_regressao``."""
    d = pd.to_datetime(pd.Series(list(datas)), errors="coerce")
    return d.dt.year + (d.dt.dayofyear - 1) / 365.25


def sem_ano_em_curso(frame: pd.DataFrame, coluna_ano: str, hoje=None) -> pd.DataFrame:
    """Tira o ano civil corrente de uma soma anual de eventos (proventos):
    o ano que ainda não acabou entra no CAGR como queda. Tira também os anos
    seguintes, que só têm o pagamento declarado e ainda não feito."""
    if frame is None or frame.empty or coluna_ano not in frame:
        return frame
    ano = (pd.Timestamp(hoje) if hoje is not None else pd.Timestamp.now()).year
    col = frame[coluna_ano]
    anos = (pd.to_datetime(col, errors="coerce").dt.year
            if not pd.api.types.is_numeric_dtype(col) else col)
    return frame[anos < ano]


def ultimo_preco_nao_positivo(datas, precos) -> pd.Timestamp | None:
    """Data do último preço ≤ 0 da série, ou ``None`` se não houver.

    Preço ≤ 0 só aparece quando o ajuste por proventos quebrou. Antes dessa
    data a série está noutra escala (o fator negativo inverte o sinal e
    distorce o nível), então quem calcula retorno usa só o que vem depois.
    """
    s = pd.DataFrame({"d": pd.to_datetime(pd.Series(list(datas)), errors="coerce"),
                      "p": pd.to_numeric(pd.Series(list(precos)), errors="coerce")})
    s = s.dropna()
    quebra = s.loc[s["p"] <= 0, "d"]
    return None if quebra.empty else pd.Timestamp(quebra.max())


def retornos_anuais(datas, precos) -> pd.DataFrame:
    """Retorno % de cada ano, último preço contra o do ano anterior.

    Colunas ``Ano`` (int), ``Retorno`` e ``Rotulo`` — o ano cuja última
    cotação cai antes de 24/12 sai como "2026 (até 28/10)", porque o número é
    acumulado no ano, não o do ano. Se a série tem preço ≤ 0, só o trecho
    depois do último deles entra, e ``attrs["corte"]`` guarda essa data.
    """
    colunas = ["Ano", "Retorno", "Rotulo"]
    s = pd.DataFrame({"d": pd.to_datetime(pd.Series(list(datas)), errors="coerce"),
                      "p": pd.to_numeric(pd.Series(list(precos)), errors="coerce")})
    s = s.dropna().sort_values("d")
    corte = ultimo_preco_nao_positivo(s["d"], s["p"])
    if corte is not None:
        s = s[s["d"] > corte]
    if s.empty:
        vazio = pd.DataFrame(columns=colunas)
        vazio.attrs["corte"] = corte
        return vazio
    ultimo = s.groupby(s["d"].dt.year).agg(p=("p", "last"), d=("d", "last"))
    ret = (ultimo["p"].pct_change() * 100).dropna()
    out = pd.DataFrame({"Ano": ret.index.astype(int), "Retorno": ret.to_numpy()})
    fim = ultimo.loc[ret.index, "d"]

    def _rotulo(ano: int, data: pd.Timestamp) -> str:
        if data < pd.Timestamp(ano, 12, 24):
            return f"{ano} (até {data:%d/%m})"
        return str(ano)

    out["Rotulo"] = [_rotulo(a, d) for a, d in zip(out["Ano"], fim)]
    out = out.reset_index(drop=True)
    out.attrs["corte"] = corte
    return out
