"""Mede o "Choque Eleitoral 2026" nos bancos e compara com o que está no código.

O cenário de ``core/stress_tests.py`` entrou em 03/11/2026 com números do
noticiário. Este script os confere contra as séries gravadas, do último pregão
antes do 2º turno (``--base``, 23/10/2026) até o último disponível (``--fim``,
hoje). **Só lê**: imprime atual × medido × diferença e um bloco pronto para
colar no ``StressScenario``. Quem altera o código é uma pessoa, num PR.

Cada série é procurada nos dois bancos (warehouse local e Supabase), e vale o
que cobre a janela até a data mais recente. A tabela diz de qual banco o número
saiu. Série que falta ou falha aparece nomeada, nunca some.

| choque            | série                                   |
|-------------------|-----------------------------------------|
| ações BR          | BOVA11 (proxy do IBOV), ``market.historical_prices`` |
| FII               | XFIX11 (proxy do IFIX), idem. O IFIX diário dessa tabela é captura da manhã, então não serve |
| exterior          | SPY, ``market_us.prices_daily``         |
| câmbio            | USDBRL, ``asset_quotes`` (último do dia) |
| prefixado e IPCA+ | ``tesouro_market_rates``: Tesouro Prefixado e Tesouro IPCA+ com o prazo mais perto de 5 anos, escalados para 5 anos (``DURATION_REFERENCIA_ANOS``). Vale a média dos dois |
| Tesouro Selic     | ``tesouro_market_rates``: Tesouro Selic com o prazo mais perto de 3 anos. O PU inclui os juros do período |
| fundo RF          | sem série em nenhum dos bancos: continua proxy |

Atenção: o Supabase só guarda 30 dias da curva dos títulos que não estão na
carteira. Por volta de 22/11/2026 a curva de 23/10 deixa de existir lá.

Uso (no PC, com o ``dfu_warehouse`` de pé e o ``.env`` do Supabase)::

    python scripts/medir_choque_eleitoral.py
    python scripts/medir_choque_eleitoral.py --fim 2026-11-06
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_PADRAO = date(2026, 10, 23)
CENARIO_PADRAO = "Choque Eleitoral 2026"

#: Distância máxima, em dias corridos, entre a data pedida e a cotação usada.
#: Cobre fim de semana e feriado (Finados caiu numa segunda); mais que isso é
#: buraco na coleta, e o número sairia de outro dia sem ninguém saber.
TOLERANCIA_DIAS = 4

#: Prazo-alvo, em anos, de cada título usado na medição.
ALVO_PREFIXADO_ANOS = 5.0
ALVO_IPCA_ANOS = 5.0
ALVO_SELIC_ANOS = 3.0

TITULO_PRE = "Tesouro Prefixado"
TITULO_IPCA = "Tesouro IPCA+"
TITULO_SELIC = "Tesouro Selic"

_SQL_HIST = ("SELECT date, close FROM market.historical_prices "
             "WHERE ticker = :t AND close > 0 AND date BETWEEN :ini AND :fim "
             "ORDER BY date")
_SQL_US = ("SELECT date, close FROM market_us.prices_daily "
           "WHERE symbol = :t AND close > 0 AND date BETWEEN :ini AND :fim "
           "ORDER BY date")
_SQL_FX = ("SELECT CAST(aq.timestamp AS DATE), aq.close FROM asset_quotes aq "
           "JOIN assets a ON a.id = aq.asset_id "
           "WHERE a.ticker = :t AND aq.close > 0 "
           "AND CAST(aq.timestamp AS DATE) BETWEEN :ini AND :fim "
           "ORDER BY aq.timestamp")
_SQL_TESOURO = ("SELECT title_name, maturity_date, base_date, base_pu "
                "FROM tesouro_market_rates "
                "WHERE base_date BETWEEN :ini AND :fim AND base_pu > 0 "
                "AND title_name IN (:pre, :ipca, :selic)")

#: atributo do cenário -> (rótulo, ticker, SQL)
SERIES_DE_PRECO: dict[str, tuple[str, str, str]] = {
    "shock_stock_br": ("BOVA11 (proxy do IBOV)", "BOVA11", _SQL_HIST),
    "shock_fii":      ("XFIX11 (proxy do IFIX)", "XFIX11", _SQL_HIST),
    "shock_etf_intl": ("SPY (S&P 500, em USD)", "SPY", _SQL_US),
    "cambio_usd_brl": ("USDBRL", "USDBRL", _SQL_FX),
}


@dataclass(frozen=True)
class Medida:
    """Um choque medido, ou o motivo de não ter sido."""
    valor: float | None
    detalhe: str
    fonte: str = ""


# ── Funções puras (testadas sem banco) ──────────────────────────────────────
def ponto_ate(serie: dict[date, float], dia: date) -> tuple[date, float] | None:
    """Última cotação em ``dia`` ou antes dele."""
    datas = [d for d in serie if d <= dia]
    if not datas:
        return None
    d = max(datas)
    return d, serie[d]


def retorno(serie: dict[date, float], base: date, fim: date,
            tolerancia: int = TOLERANCIA_DIAS) -> Medida:
    """Retorno simples da base até o fim, recusando ponta longe da data pedida."""
    ini = ponto_ate(serie, base)
    if ini is None or (base - ini[0]).days > tolerancia:
        return Medida(None, f"sem cotação até {tolerancia} dias antes de {base:%d/%m/%Y}")
    ult = ponto_ate(serie, fim)
    if ult is None or ult[0] <= ini[0]:
        return Medida(None, f"sem cotação depois de {ini[0]:%d/%m/%Y}")
    det = f"{ini[0]:%d/%m} {ini[1]:,.4f} → {ult[0]:%d/%m} {ult[1]:,.4f}"
    if (fim - ult[0]).days > tolerancia:
        det += f" (DEFASADA: última de {ult[0]:%d/%m/%Y})"
    return Medida(ult[1] / ini[1] - 1, det)


def prazo_anos(vencimento: date, dia: date) -> float:
    return max((vencimento - dia).days, 0) / 365.25


def escolher_titulo(linhas: list[tuple], tipo: str, base: date, fim: date,
                    alvo_anos: float) -> tuple[date, dict[date, float]] | None:
    """O vencimento do ``tipo`` com prazo mais perto do alvo e PU nas duas pontas.

    ``linhas`` = ``(title_name, maturity_date, base_date, base_pu)``.
    """
    por_venc: dict[date, dict[date, float]] = {}
    for titulo, venc, dia, pu in linhas:
        if titulo == tipo and pu:
            por_venc.setdefault(venc, {})[dia] = float(pu)
    candidatos = [v for v, s in por_venc.items()
                  if retorno(s, base, fim).valor is not None]
    if not candidatos:
        return None
    venc = min(candidatos, key=lambda v: abs(prazo_anos(v, base) - alvo_anos))
    return venc, por_venc[venc]


def choque_titulo(linhas: list[tuple], tipo: str, base: date, fim: date,
                  alvo_anos: float, escalar_para: float | None) -> Medida:
    """Variação do PU do título escolhido; escalada pela duration quando pedido.

    A escala é a mesma aproximação de primeira ordem do modelo
    (``core.stress_tests._choque_da_posicao``): choque ∝ duration. O prazo
    residual é a duration exata de título sem cupom, que é o caso dos dois
    usados aqui.
    """
    esc = escolher_titulo(linhas, tipo, base, fim, alvo_anos)
    if esc is None:
        return Medida(None, f"{tipo}: sem PU nas duas pontas")
    venc, serie = esc
    m = retorno(serie, base, fim)
    prazo = prazo_anos(venc, base)
    det = f"{tipo} {venc:%Y} ({prazo:.1f} anos): {m.detalhe}"
    valor = m.valor
    if escalar_para and valor is not None and prazo > 0:
        valor = valor * escalar_para / prazo
        det += f"; {m.valor:+.2%} escalado para {escalar_para:.0f} anos"
    return Medida(valor, det)


def medir_renda_fixa(linhas: list[tuple], base: date, fim: date,
                     duration_ref: float) -> Medida:
    """Média de Prefixado e IPCA+ de ~5 anos, escalados para a duration de referência."""
    pre = choque_titulo(linhas, TITULO_PRE, base, fim, ALVO_PREFIXADO_ANOS, duration_ref)
    ipca = choque_titulo(linhas, TITULO_IPCA, base, fim, ALVO_IPCA_ANOS, duration_ref)
    validos = [m.valor for m in (pre, ipca) if m.valor is not None]
    det = f"{pre.detalhe} | {ipca.detalhe}"
    if not validos:
        return Medida(None, det)
    return Medida(sum(validos) / len(validos), det)


def medir(precos: dict[str, tuple[dict[date, float], str]],
          tesouro: tuple[list[tuple], str] | None,
          base: date, fim: date, duration_ref: float) -> dict[str, Medida]:
    """Todos os choques. ``precos``: atributo -> (série, banco de origem)."""
    out: dict[str, Medida] = {}
    for attr, (rotulo, _ticker, _sql) in SERIES_DE_PRECO.items():
        serie, banco = precos.get(attr, ({}, ""))
        m = retorno(serie, base, fim) if serie else Medida(None, "série não encontrada")
        out[attr] = Medida(m.valor, f"{rotulo}: {m.detalhe}", banco)
    if tesouro is None:
        falta = Medida(None, "tesouro_market_rates não encontrada")
        out["shock_renda_fixa"] = out["shock_tesouro"] = falta
    else:
        linhas, banco = tesouro
        rf = medir_renda_fixa(linhas, base, fim, duration_ref)
        out["shock_renda_fixa"] = Medida(rf.valor, rf.detalhe, banco)
        lft = choque_titulo(linhas, TITULO_SELIC, base, fim, ALVO_SELIC_ANOS, None)
        out["shock_tesouro"] = Medida(lft.valor, lft.detalhe, banco)
    out["shock_fundo_rf"] = Medida(None, "sem série de cota de fundo nos bancos: "
                                         "continua proxy")
    return out


def relatorio(cenario, medidas: dict[str, Medida], base: date, fim: date) -> str:
    """Tabela atual × medido e o bloco para colar no ``StressScenario``."""
    from core.stress_tests import ROTULO_CHOQUE

    rotulos = {**ROTULO_CHOQUE, "cambio_usd_brl": "Câmbio USD/BRL"}
    linhas = [f"Cenário: {cenario.nome} | janela {base:%d/%m/%Y} → {fim:%d/%m/%Y}", "",
              f"{'choque':<24}{'no código':>11}{'medido':>10}{'diferença':>11}  banco"]
    for attr, m in medidas.items():
        atual = float(getattr(cenario, attr))
        med = f"{m.valor:+.2%}" if m.valor is not None else "—"
        dif = f"{(m.valor - atual) * 100:+.1f} pp" if m.valor is not None else ""
        linhas.append(f"{rotulos[attr]:<24}{atual:>+11.2%}{med:>10}{dif:>11}  "
                      f"{m.fonte or '—'}")
        linhas.append(f"    {m.detalhe}")

    # Câmbio não é choque de classe e o de ações BR é o conferido contra o
    # índice: nenhum dos dois pode entrar em ``choques_nao_medidos``.
    nao_medidos = tuple(a for a, m in medidas.items() if m.valor is None
                        and a not in ("cambio_usd_brl", "shock_stock_br"))
    linhas += ["", "Para colar em core/stress_tests.py (confira antes):", ""]
    for attr, m in medidas.items():
        if m.valor is not None:
            linhas.append(f"    {attr}={round(m.valor, 2):+.2f},")
    if medidas["shock_stock_br"].valor is not None:
        linhas.append(f"    retorno_indice_observado="
                      f"{round(medidas['shock_stock_br'].valor, 2):+.2f},")
        linhas.append('    indice_referencia="BOVA11",')
    linhas.append(f"    choques_nao_medidos={nao_medidos!r},")
    return "\n".join(linhas)


# ── Coleta (só no PC do usuário) ────────────────────────────────────────────
def _engines() -> dict[str, object]:
    """Os dois bancos que estiverem alcançáveis. Falha vira aviso, não exceção."""
    from sqlalchemy import create_engine

    engines: dict[str, object] = {}
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        engines["warehouse"] = create_engine(_warehouse_url())
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: warehouse local indisponível ({type(exc).__name__}: {exc})")
    try:
        from core.database import get_engine
        eng = get_engine()
        if eng is None:
            raise RuntimeError("DATABASE_URL não configurada")
        engines["supabase"] = eng
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: Supabase indisponível ({type(exc).__name__}: {exc})")
    return engines


def _melhor(candidatos: list[tuple[str, dict]]) -> tuple[dict, str]:
    """Entre os bancos, a série que chega mais longe no tempo."""
    candidatos = [(b, s) for b, s in candidatos if s]
    if not candidatos:
        return {}, ""
    banco, serie = max(candidatos, key=lambda c: max(c[1]))
    return serie, banco


def coletar(engines: dict[str, object], ini: date, fim: date):
    from sqlalchemy import text

    precos: dict[str, tuple[dict[date, float], str]] = {}
    for attr, (_rot, ticker, sql) in SERIES_DE_PRECO.items():
        cands = []
        for banco, eng in engines.items():
            try:
                with eng.connect() as c:
                    rows = c.execute(text(sql), {"t": ticker, "ini": ini, "fim": fim})
                    cands.append((banco, {r[0]: float(r[1]) for r in rows}))
            except Exception as exc:  # noqa: BLE001
                print(f"aviso: {ticker} no {banco}: {type(exc).__name__}: "
                      f"{str(exc).splitlines()[0][:120]}")
        precos[attr] = _melhor(cands)

    tesouro = None
    cands = []
    for banco, eng in engines.items():
        try:
            with eng.connect() as c:
                rows = [tuple(r) for r in c.execute(text(_SQL_TESOURO), {
                    "ini": ini, "fim": fim, "pre": TITULO_PRE,
                    "ipca": TITULO_IPCA, "selic": TITULO_SELIC})]
            if rows:
                cands.append((banco, rows))
        except Exception as exc:  # noqa: BLE001
            print(f"aviso: tesouro_market_rates no {banco}: {type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:120]}")
    if cands:
        tesouro = max(((r, b) for b, r in cands), key=lambda c: max(x[2] for x in c[0]))
    return precos, tesouro


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", type=date.fromisoformat, default=BASE_PADRAO,
                   help="último pregão antes do choque (padrão 2026-10-23)")
    p.add_argument("--fim", type=date.fromisoformat, default=date.today(),
                   help="fim da janela (padrão hoje)")
    p.add_argument("--cenario", default=CENARIO_PADRAO)
    args = p.parse_args(argv)

    from core import stress_tests as S

    cenario = next((c for c in S.SCENARIOS if c.nome == args.cenario), None)
    if cenario is None:
        print(f"cenário {args.cenario!r} não existe em core/stress_tests.py")
        return 1
    engines = _engines()
    if not engines:
        print("nenhum banco alcançável: nada medido")
        return 1
    ini = args.base - timedelta(days=TOLERANCIA_DIAS + 3)
    precos, tesouro = coletar(engines, ini, args.fim)
    medidas = medir(precos, tesouro, args.base, args.fim, S.DURATION_REFERENCIA_ANOS)
    print(relatorio(cenario, medidas, args.base, args.fim))
    return 0 if any(m.valor is not None for m in medidas.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
