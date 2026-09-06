"""Mede a camada macro: ela ordena alguma coisa, e quanto ela move.

Por que este script existe
--------------------------
Dois limites da ``MacroTiltConfig`` estão na ``main`` movendo peso e nota sem
procedência medida: ``max_score_adjustment=10`` e
``max_relative_weight_tilt=0,15``. Números redondos, escolhidos a priori. O
``_simular_seg_backtest`` valida o motor fundamentalista; o tilt macro é
aplicado **depois** dele e nunca foi medido.

Este script não é uma tentativa de provar que o tilt funciona. Ele responde três
perguntas separadas, e a segunda e a terceira continuam valendo mesmo quando a
primeira dá "não":

1. **O impacto macro ordena retorno futuro?** Rank-IC transversal do impacto
   contra o retorno do mês seguinte, em cortes point-in-time.
2. **O teto de peso chega a morder?** Um teto que a própria escala do dado
   nunca alcança não é um limite -- é decoração.
3. **De quanto é o efeito de ``max_score_adjustment`` na ordenação?** Esta não
   é validável contra desfecho: não existe série histórica de nota
   fundamentalista para comparar. O que se pode medir, e é o que importa, é
   **tamanho**: quantas posições da tabela o ajuste desloca.

O que este script não faz, e por que
------------------------------------
**Não reimplementa point-in-time.** Ele chama
``load_portfolio_macro_snapshot``, que é o mesmo caminho que a tela usa. Duas
implementações da mesma reconstrução dariam duas respostas conforme quem
perguntou, e o projeto já pagou por isso.

**Não grava nada, em lugar nenhum.** É leitura do armazém local.

Limitações declaradas do que ele mede
-------------------------------------
- **Não existe vintage de verdade.** ``retrieved_at`` só cobre 2026 -- toda a
  história foi carregada de uma vez. Cortes históricos leem valores **já
  revisados**. Séries não revisadas (DGS10, FEDFUNDS, selic, câmbio) não são
  afetadas; CPIAUCSL, UNRATE, GDPC1 e pib são. É look-ahead declarado, e o modo
  ``reconstructed`` é o mais honesto disponível, não o correto.
- **Universo 100% sobrevivente.** Os ativos vêm de ``macro_portfolio_assets``,
  que é a carteira de hoje. Quem saiu não está lá. Isso infla qualquer resultado
  positivo e não salva um resultado nulo -- que é o que se encontrou.
- **Preço da B3 só a partir de 2010.** Antes disso a série tem meses inteiros de
  retorno exatamente zero (LEVE3: 85% dos meses em 2000), que é série parada, não
  ativo sem volatilidade. Começar em 2010 é medição, não gosto.
- **A camada doméstica é anual, com 17 pontos.** ``selic``, ``ipca``, ``pib`` e
  as outras vão de 2010-12-31 a 2026-12-31 com passo de 365 dias. Um insumo que
  muda uma vez por ano não ganha poder estatístico por ser lido todo mês: o N
  efetivo é o número de vetores de impacto **distintos**, que o script conta e
  reporta ao lado do número de cortes.
- **Janela sobreposta infla t.** Para horizonte maior que um mês o script
  reporta Newey-West junto com o t simples. A diferença entre os dois já foi
  inteira a diferença entre "achado" e "artefato" aqui.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import timezone
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from core.macro_data.database import get_local_macro_engine
from core.macro_data.portfolio_context import load_portfolio_macro_snapshot
from core.macro_data.portfolio_tilt import MacroTiltConfig

logger = logging.getLogger(__name__)

#: Piso da medição de preço da B3. Antes disto a série tem meses com retorno
#: exatamente zero em bloco -- ver o cabeçalho.
PISO_B3 = "2011-01-31"

CLASSES = ("b3", "fii", "us")


def _warehouse_engine() -> Engine:
    """Engine do armazém local de preços, sem duplicar a montagem da URL."""
    from scripts.publish_fii_selection_from_local import _warehouse_url

    return create_engine(_warehouse_url())


def carregar_ativos(engine: Engine, asset_class: str) -> dict[str, str]:
    """Símbolo -> setor da classe pedida.

    ``DISTINCT`` não é otimização: ``macro_portfolio_assets`` guarda o mesmo FII
    em mais de um modelo, e sem ele o mesmo ativo entraria repetido no painel,
    pesando mais na estatística transversal por acidente de cadastro.
    """
    with engine.connect() as conn:
        linhas = conn.execute(
            text("SELECT DISTINCT symbol, sector FROM macro_portfolio_assets "
                 "WHERE asset_class = :a"),
            {"a": asset_class},
        ).all()
    return {str(r.symbol): str(r.sector or "") for r in linhas}


def montar_painel_impacto(engine: Engine, asset_class: str,
                          cortes: pd.DatetimeIndex,
                          ativos: dict[str, str]) -> pd.DataFrame:
    """Um corte point-in-time por linha, um ativo por coluna.

    Reusa ``load_portfolio_macro_snapshot`` de propósito: é o mesmo caminho da
    tela. ``knowledge_mode="reconstructed"`` porque ``strict`` exigiria
    ``retrieved_at <= as_of``, e ``retrieved_at`` só existe a partir de 2026 --
    em modo estrito todo corte histórico voltaria vazio.
    """
    linhas: dict[pd.Timestamp, dict[str, float]] = {}
    for corte in cortes:
        snap = load_portfolio_macro_snapshot(
            engine, asset_class=asset_class, assets=ativos,
            as_of=corte.to_pydatetime(), knowledge_mode="reconstructed",
        )
        linhas[corte] = dict(snap.impacts)
    return pd.DataFrame(linhas).T.sort_index()


def carregar_retornos(asset_class: str, simbolos: list[str]) -> pd.DataFrame:
    """Retorno mensal dos símbolos, do armazém local."""
    engine = _warehouse_engine()
    try:
        with engine.connect() as conn:
            if asset_class == "us":
                bruto = pd.read_sql(
                    text("SELECT symbol AS ticker, month_end AS date, "
                         "COALESCE(adjusted_close, close) AS close "
                         "FROM market_us.prices_monthly "
                         "WHERE symbol = ANY(:s) ORDER BY month_end"),
                    conn, params={"s": simbolos},
                )
            else:
                bruto = pd.read_sql(
                    text("SELECT ticker, date, close FROM market.historical_prices "
                         "WHERE ticker = ANY(:s) AND date >= :piso ORDER BY date"),
                    conn, params={"s": simbolos, "piso": "2010-01-01"},
                )
    finally:
        engine.dispose()

    if bruto.empty:
        return pd.DataFrame()
    px = bruto.pivot_table(index="date", columns="ticker", values="close")
    px.index = pd.to_datetime(px.index)
    mensal = px.resample("ME").last()
    return mensal.pct_change(fill_method=None)


def t_newey_west(x: np.ndarray, lags: int) -> float:
    """t da média com correção de autocorrelação (Bartlett).

    Existe porque janela sobreposta de h meses gera h-1 defasagens de
    autocorrelação na série de IC, e o t simples ali **não é um t**: no arm dos
    EUA ele deu -3,83 num efeito cujo Newey-West é -1,73.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")
    e = x - x.mean()
    s = float(e @ e) / n
    for lag in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - lag / (lags + 1)) * float(e[lag:] @ e[:-lag]) / n
    if s <= 0:
        return float("nan")
    return float(x.mean() / np.sqrt(s / n))


def rank_ic(painel: pd.DataFrame, retornos: pd.DataFrame,
            horizonte: int = 1) -> dict[str, Any]:
    """Correlação de posto entre impacto e retorno futuro, mês a mês.

    Meses em que o impacto é constante entre ativos são **descartados**, não
    contados como IC zero: ali o tilt não emitiu opinião nenhuma, e registrar
    isso como acerto ou erro seria inventar observação.
    """
    ret = retornos.reindex(columns=painel.columns)
    if horizonte == 1:
        futuro = ret.shift(-1)
    else:
        futuro = (1 + ret).rolling(horizonte).apply(np.prod, raw=True).shift(-horizonte) - 1
    futuro = futuro.reindex(painel.index)

    serie: dict[pd.Timestamp, float] = {}
    descartados = 0
    for mes in painel.index:
        x, y = painel.loc[mes], futuro.loc[mes]
        ok = x.notna() & y.notna()
        if ok.sum() < 4 or float(x[ok].std()) < 1e-9:
            descartados += 1
            continue
        serie[mes] = float(stats.spearmanr(x[ok], y[ok]).statistic)

    s = pd.Series(serie)
    if len(s) < 3:
        return {"horizonte": horizonte, "meses": len(s), "descartados": descartados}

    t_simples = float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))
    rng = np.random.default_rng(20260905)
    boot = [float(rng.choice(s.values, len(s), replace=True).mean()) for _ in range(2000)]
    return {
        "horizonte": horizonte,
        "meses": len(s),
        "descartados": descartados,
        "ic": float(s.mean()),
        "t_simples": t_simples,
        "t_newey_west": t_newey_west(s.values, max(horizonte - 1, 1)),
        "p_valor": float(2 * (1 - stats.t.cdf(abs(t_simples), len(s) - 1))),
        "acerto_de_sinal": float((np.sign(s) > 0).mean()),
        "ic95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
    }


def efeito_na_carteira(painel: pd.DataFrame, retornos: pd.DataFrame,
                       k: float, escala: float = 1.0) -> dict[str, Any]:
    """Equal-weight base contra a mesma carteira inclinada pelo impacto.

    O peso do mês entra multiplicando o retorno do mês **seguinte**
    (``shift(1)``): usar o peso do próprio mês leria o retorno antes de decidir
    o peso, que é a forma mais barata de fabricar desempenho.
    """
    # A janela é a do painel, não a do preço. Sem este recorte, todo mês fora
    # dos cortes entraria com impacto zero por causa do ``fillna(0)`` abaixo --
    # carteira inclinada idêntica à base, diferença zero -- e centenas de meses
    # em que o tilt nunca opinou diluiriam o t de um efeito que só existe
    # dentro da janela medida. No arm dos EUA isso são 557 meses contra 188
    # cortes: dois terços da amostra seriam enchimento.
    ret = retornos.reindex(columns=painel.columns).dropna(how="all")
    ret = ret.loc[(ret.index >= painel.index.min()) & (ret.index <= painel.index.max())]
    base = ret.notna().astype(float)
    base = base.div(base.sum(axis=1).replace(0, np.nan), axis=0)

    mult = 1 + (painel.reindex(ret.index).fillna(0) / 100 * k * escala)
    inclinada = base * mult
    inclinada = inclinada.div(inclinada.sum(axis=1).replace(0, np.nan), axis=0)

    dif = ((inclinada - base).shift(1) * ret).sum(axis=1).dropna()
    if len(dif) < 3 or float(dif.std(ddof=1)) == 0:
        return {"k": k, "meses": len(dif)}
    t = float(dif.mean() / (dif.std(ddof=1) / np.sqrt(len(dif))))
    return {
        "k": k,
        "escala": escala,
        "meses": len(dif),
        "dif_mensal": float(dif.mean()),
        "dif_anualizada": float((1 + dif.mean()) ** 12 - 1),
        "t_simples": t,
        "t_newey_west": t_newey_west(dif.values, 3),
    }


def teto_de_peso_morde(painel: pd.DataFrame, config: MacroTiltConfig) -> dict[str, Any]:
    """O teto de ``max_relative_weight_tilt`` é alcançável pelo dado que o alimenta?

    ``apply_macro_tilt`` calcula ``1 + impacto/100 * teto``. O teto só é atingido
    com ``|impacto| = 100``. Se o impacto observado nunca chega perto disso, o
    teto não é um limite -- é um número que nunca entra em contato com nada.
    """
    valores = painel.stack().abs()
    if valores.empty:
        return {}
    maximo = float(valores.max())
    return {
        "impacto_absoluto_mediana": float(valores.median()),
        "impacto_absoluto_p95": float(valores.quantile(0.95)),
        "impacto_absoluto_maximo": maximo,
        "variacao_de_peso_maxima_pct": maximo * config.max_relative_weight_tilt,
        "meses_que_encostam_no_teto": int((painel.abs() >= 100).any(axis=1).sum()),
        "teto_morde": bool((painel.abs() >= 100).any().any()),
    }


def efeito_do_ajuste_de_nota(painel: pd.DataFrame,
                             config: MacroTiltConfig) -> dict[str, Any]:
    """Quantas posições da tabela o ajuste de nota desloca.

    Não há série histórica de nota fundamentalista, então **não dá para validar
    ``max_score_adjustment`` contra desfecho** -- e dizer que ele foi validado
    seria inventar a conclusão. O que dá para medir é tamanho, contra a
    distribuição real de nota da safra corrente da vitrine dos EUA: quantas
    empresas cabem dentro de um ajuste de X pontos a partir da mediana.
    """
    engine = _warehouse_engine()
    try:
        with engine.connect() as conn:
            versao = conn.execute(text(
                "SELECT score_version, as_of_date FROM market_us.score_vintages "
                "ORDER BY as_of_date DESC, created_at DESC LIMIT 1")).one_or_none()
            if versao is None:
                return {"indisponivel": "sem safra de nota para medir densidade"}
            notas = pd.read_sql(
                text("SELECT score FROM market_us.score_vintages "
                     "WHERE score_version = :v AND as_of_date = :d AND score IS NOT NULL"),
                conn, params={"v": versao[0], "d": versao[1]},
            )["score"]
    finally:
        engine.dispose()

    if notas.empty:
        return {"indisponivel": "safra sem nota"}
    notas = notas.sort_values(ascending=False).reset_index(drop=True)
    mediana = float(notas.iloc[len(notas) // 2])

    impacto_p95 = float(painel.stack().abs().quantile(0.95))
    deslocamentos = {}
    for modo, escala in (("moderate", 1.0), ("scenario", 1.5)):
        ajuste = impacto_p95 / 100 * config.max_score_adjustment * escala
        movidas = int(((notas > mediana) & (notas <= mediana + ajuste)).sum())
        deslocamentos[modo] = {
            "ajuste_em_pontos": ajuste,
            "posicoes_deslocadas": movidas,
            "fracao_da_tabela": movidas / len(notas),
        }
    teto = config.max_score_adjustment * 1.5
    no_teto = int(((notas > mediana) & (notas <= mediana + teto)).sum())
    return {
        "safra": f"{versao[0]} @ {versao[1]}",
        "empresas": int(len(notas)),
        "gap_mediano_entre_posicoes": float((-notas.diff().dropna()).median()),
        "impacto_p95": impacto_p95,
        "no_p95_do_impacto": deslocamentos,
        "no_teto_do_ajuste": {
            "ajuste_em_pontos": teto,
            "posicoes_deslocadas": no_teto,
            "fracao_da_tabela": no_teto / len(notas),
        },
    }


def medir_classe(engine: Engine, asset_class: str, inicio: str, fim: str,
                 config: MacroTiltConfig) -> dict[str, Any]:
    """Roda o conjunto inteiro de medições para uma classe de ativo."""
    ativos = carregar_ativos(engine, asset_class)
    if not ativos:
        return {"classe": asset_class, "erro": "sem ativos em macro_portfolio_assets"}

    cortes = pd.date_range(inicio, fim, freq="ME", tz=timezone.utc)
    painel = montar_painel_impacto(engine, asset_class, cortes, ativos)
    if painel.empty:
        return {"classe": asset_class, "erro": "painel de impacto vazio"}

    retornos = carregar_retornos(asset_class, sorted(painel.columns))
    if retornos.empty:
        return {"classe": asset_class, "erro": "sem preco no armazem local"}
    retornos.index = retornos.index.tz_localize("UTC")

    # N efetivo: o que muda de um corte para o outro, e não quantas vezes se
    # perguntou. Insumo anual lido todo mês repete o mesmo vetor doze vezes.
    assinaturas = {tuple(sorted(painel.loc[m].items())) for m in painel.index}
    dispersao = painel.std(axis=1, ddof=0)

    return {
        "classe": asset_class,
        "ativos": int(painel.shape[1]),
        "cortes": int(painel.shape[0]),
        "vetores_distintos": len(assinaturas),
        "dispersao_transversal_media": float(dispersao.mean()),
        "meses_sem_dispersao": int((dispersao.fillna(0) < 1e-9).sum()),
        "teto_de_peso": teto_de_peso_morde(painel, config),
        "rank_ic": [rank_ic(painel, retornos, h) for h in (1, 3, 12)],
        "carteira": [efeito_na_carteira(painel, retornos, k)
                     for k in (0.05, config.max_relative_weight_tilt, 0.50)],
        "ajuste_de_nota": efeito_do_ajuste_de_nota(painel, config),
    }


def _imprimir(res: dict[str, Any]) -> None:
    if "erro" in res:
        print(f"\n=== {res['classe'].upper()} === {res['erro']}")
        return
    print(f"\n=== {res['classe'].upper()} ===")
    print(f"  ativos={res['ativos']}  cortes={res['cortes']}  "
          f"vetores de impacto DISTINTOS={res['vetores_distintos']}  "
          f"(N efetivo, nao o numero de cortes)")
    print(f"  dispersao transversal media={res['dispersao_transversal_media']:.2f} pontos  "
          f"meses sem dispersao={res['meses_sem_dispersao']}")

    t = res["teto_de_peso"]
    print(f"  teto de peso ({t['impacto_absoluto_maximo']:.1f} = |impacto| maximo observado): "
          f"move no maximo {t['variacao_de_peso_maxima_pct']:.2f}% de peso relativo; "
          f"morde={t['teto_morde']}")

    for ic in res["rank_ic"]:
        if "ic" not in ic:
            continue
        print(f"  Rank-IC h={ic['horizonte']:2d}m  meses={ic['meses']:3d}  "
              f"IC={ic['ic']:+.4f}  t={ic['t_simples']:+.2f}  "
              f"t_NW={ic['t_newey_west']:+.2f}  acerto={ic['acerto_de_sinal']:.1%}")

    for c in res["carteira"]:
        if "dif_anualizada" not in c:
            continue
        print(f"  carteira k={c['k']:.2f}  meses={c['meses']:3d}  "
              f"anualizado={c['dif_anualizada']:+.3%}  t_NW={c['t_newey_west']:+.2f}")

    aj = res["ajuste_de_nota"]
    if "no_p95_do_impacto" in aj:
        for modo, v in aj["no_p95_do_impacto"].items():
            print(f"  ajuste de nota ({modo}, p95 do impacto): {v['ajuste_em_pontos']:.2f} pt "
                  f"-> {v['posicoes_deslocadas']} posicoes "
                  f"({v['fracao_da_tabela']:.1%} da tabela de {aj['empresas']})")
        v = aj["no_teto_do_ajuste"]
        print(f"  ajuste de nota (no teto): {v['ajuste_em_pontos']:.2f} pt -> "
              f"{v['posicoes_deslocadas']} posicoes ({v['fracao_da_tabela']:.1%} da tabela)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classe", choices=(*CLASSES, "todas"), default="todas")
    parser.add_argument("--inicio", default=PISO_B3,
                        help="piso da medicao; padrao 2011-01-31 (ver preco parado da B3)")
    parser.add_argument("--fim", default="2026-08-31")
    parser.add_argument("--json", dest="saida_json",
                        help="grava o resultado bruto em JSON")
    args = parser.parse_args(argv)

    engine = get_local_macro_engine()
    if engine is None:
        print("MACRO_LOCAL_DB_URL nao configurada -- este script le o armazem local.")
        return 1

    config = MacroTiltConfig()
    classes = CLASSES if args.classe == "todas" else (args.classe,)
    try:
        resultados = [medir_classe(engine, c, args.inicio, args.fim, config)
                      for c in classes]
    finally:
        engine.dispose()

    print(f"Backtest da camada macro | {args.inicio} .. {args.fim} | "
          f"max_score_adjustment={config.max_score_adjustment} "
          f"max_relative_weight_tilt={config.max_relative_weight_tilt}")
    print("Modo de conhecimento: reconstructed. Sem vintage real -- cortes historicos")
    print("leem valores ja revisados. Universo 100% sobrevivente. Leitura, nao gravacao.")
    for res in resultados:
        _imprimir(res)

    if args.saida_json:
        with open(args.saida_json, "w", encoding="utf-8") as fh:
            json.dump(resultados, fh, indent=2, default=str)
        print(f"\nJSON: {args.saida_json}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
