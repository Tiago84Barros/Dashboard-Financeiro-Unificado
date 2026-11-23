# -*- coding: utf-8 -*-
"""Mede fora da amostra a carteira ampla "nota + fatores" da B3.

A regra mora em `core/b3_nota_fatores.py` e é a mesma que a tela usa. Aqui ela
é refeita safra a safra, point-in-time, com a régua de
`scripts/medir_oos_carteira_b3.py`:

- a aba de Criação de Portfólio B3 roda sem cabeça no perfil padrão
  (Equilibrado), com o LLM desligado, e entrega os scores por safra, os preços
  mensais e o histórico de fundamentos da própria tela;
- o universo de cada safra é o da medição oficial (quem tinha score na safra
  mais quem saiu da bolsa nela);
- o retorno de cada papel na vigência é o de `core.b3_safras.retorno_da_safra`
  e o custo do giro de abril é o de `oos.simular_custos`
  (`CostConfig.brasil_pf_default()`), cadeia contínua desde a primeira safra
  com carteira da medição oficial.

Grava `data/oos_nota_fatores_b3.json`, separado de `data/oos_carteira_b3.json`
(este custa chamadas de LLM e não é refeito aqui). Junto da carteira principal
vão duas comparações: "tirar 30% pela nota" e a carteira concentrada da aba,
sem portão.

Uso::

    python scripts/medir_nota_fatores_b3.py --cache-dir <pasta>   # mede e grava
    python scripts/medir_nota_fatores_b3.py --dry-run             # só imprime

O armazém local precisa estar de pé. Nada é gravado em banco nenhum.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_nota_fatores_b3.json"


def _tirar_piores_pela_nota(universo, scores, corte):
    """Comparação: tira os piores só entre quem tem nota; sem nota fica."""
    import pandas as pd

    from core.b3_nota_fatores import tirar_piores

    com = pd.Series({t: scores[t] for t in universo if t in scores}, dtype=float)
    _ficam, saem = tirar_piores(com, corte)
    return [t for t in universo if t not in set(saem)]


def medir_escolha(por_safra, escolha, *, precos, selic_macro, taxa, selic_mensal,
                  cost_cfg, comeca_em, memo: dict | None = None) -> dict:
    """Carteira de pesos iguais em ``escolha(safra)``, medida contra pesos iguais.

    Ponto único da régua das carteiras "de regra" da B3 (nota + fatores, o
    corte pela nota e as estratégias fundamentalistas de
    `scripts/medir_estrategias_fundamentalistas_b3.py`): o retorno de cada
    safra é o de `core.b3_safras.retorno_da_safra`, o giro de abril paga o
    custo de `oos.simular_custos` numa cadeia contínua desde ``comeca_em``, e
    safra sem nome vira caixa à Selic (`oos.itens_da_cadeia`).

    ``memo`` (um dict vazio, reaproveitado entre chamadas): guarda por safra o
    que não depende da escolha -- pesos iguais, Selic, se é mensurável e o
    retorno winsorizado de cada papel do universo (os limites saem só do
    universo). Serve a quem mede milhares de escolhas no mesmo recorte, como
    `scripts/medir_carteiras_aleatorias_b3.py`; o resultado é o mesmo de sem
    ``memo`` (o script confere com ``--conferir``).
    """
    import core.b3_oos_carteira as oos
    from core.b3_safras import SafraCarteira, retorno_da_safra
    from core.b3_vigencia import ano_base_do_score, janela_de_vigencia

    linhas = []
    for p in por_safra:
        safra, universo, saidas = p["safra"], p["universo"], p["saidas"]
        fica = escolha(p)
        pesos = {t: 1.0 / len(fica) for t in fica} if fica else {}
        guardado = memo.get(safra) if memo is not None else None
        if guardado is None:
            inicio, fim = janela_de_vigencia(safra)
            base = retorno_da_safra(
                SafraCarteira(safra=safra, ano_base=ano_base_do_score(safra), inicio=inicio,
                              fim=fim, completa=True, pesos=pesos, universo=tuple(universo),
                              segmentos=1, saidas=saidas),
                precos, selic_por_ano=selic_macro, taxa_selic_aa=taxa,
                selic_mensal=selic_mensal)
            if memo is not None:
                guardado = memo[safra] = {
                    "base": {k: base[k] for k in ("mensuravel", "retorno_selic",
                                                  "retorno_equal_weight")},
                    "rets": oos.retornos_por_ticker(list(universo), universo, precos,
                                                    safra, saidas)}
        if guardado is None:
            rets = oos.retornos_por_ticker(list(pesos), universo, precos, safra, saidas)
        else:
            base = guardado["base"]
            fora = [t for t in pesos if t not in guardado["rets"]]
            rets = {t: guardado["rets"][t] for t in pesos if t in guardado["rets"]}
            if fora:
                rets.update(oos.retornos_por_ticker(fora, universo, precos, safra, saidas))
            rets = {t: rets[t] for t in pesos}
        linhas.append({"safra": safra, "pesos": pesos, "retornos": rets,
                       "inviavel_no_cap": False, "mensuravel": bool(base["mensuravel"]),
                       "selic": base["retorno_selic"], "ew": base["retorno_equal_weight"],
                       "n_universo": len(universo), "n_carteira": len(pesos)})
    cadeia = oos.simular_custos(oos.itens_da_cadeia(linhas, comeca_em=comeca_em),
                                cost_cfg)
    tabela = []
    for r, c in zip(linhas, cadeia):
        if c.get("medida") and r["ew"] is not None:
            tabela.append({
                "safra": r["safra"],
                "n_universo": r["n_universo"], "n_carteira": r["n_carteira"],
                "liquido": c["liquido"], "bruto": c["bruto"],
                "liquido_com_ir": c["liquido_com_ir"],
                "equal_weight": r["ew"],
                "excesso_ew": c["liquido"] - r["ew"],
                "excesso_ew_bruto": c["bruto"] - r["ew"],
                "excesso_ew_com_ir": c["liquido_com_ir"] - r["ew"],
                "giro": c["giro"], "custo_pct": c["custo_pct"],
            })
    resumo = oos.resumir(tabela, "excesso_ew")
    giros = [t["giro"] for t in tabela[1:]]
    return {
        "vs_equal_weight": resumo,
        "bruto_vs_equal_weight": oos.resumir(tabela, "excesso_ew_bruto"),
        "com_ir_vs_equal_weight": oos.resumir(tabela, "excesso_ew_com_ir"),
        "giro_medio_anual": (sum(giros) / len(giros)) if giros else None,
        "custo_medio_anual": (sum(t["custo_pct"] for t in tabela[1:]) / len(giros))
        if giros else None,
        "leitura": oos.leitura_honesta(resumo),
        "safras": tabela,
    }


def preparar(cache_dir: Path | None) -> dict:
    """Armazém local, LLM desligado e a rodada da aba no perfil padrão.

    Ponto único das duas medições da nota + fatores (esta e
    `scripts/medir_lider_filtro_nota_fatores_b3.py`): as duas leem a mesma
    rodada (o mesmo pickle com ``--cache-dir``) e a mesma régua.
    """
    os.environ["LACUNAS_DESTINO"] = "desligado"
    import scripts.medir_oos_carteira_b3 as M
    from scripts.medir_vantagem_oos import (
        _apontar_para_armazem_local,
        _desligar_llm,
    )

    url = _apontar_para_armazem_local()
    print(f"armazem local: {url.split('@')[-1]}", flush=True)

    import pandas as pd

    from core.database import get_engine
    host = get_engine().url.host
    if host not in ("localhost", "127.0.0.1"):
        raise SystemExit(f"get_engine() aponta para {host}, não para o armazém local")
    import core.b3_validation as _val
    _val.persist_validation_run = lambda *a, **k: None
    import core.llm_b3 as _llm
    _desligar_llm()
    if _llm._provider_chain():
        raise SystemExit("LLM continua ligado: a medição travaria na rede")

    import core.b3_nota_fatores as nf
    import core.b3_oos_carteira as oos
    import views.portfolio_b3 as view
    from core.b3_portfolio_presets import PRESETS, RECOMENDADO
    from core.market_read import load_precos_mensais
    from core.transaction_costs import CostConfig

    M._instrumentar_view(view)
    cost_cfg = CostConfig.brasil_pf_default()
    hoje = pd.Timestamp.now().normalize()
    ibov_df = load_precos_mensais(("BOVA11",))
    ibov = ibov_df["BOVA11"].dropna() if "BOVA11" in ibov_df.columns else None

    nome = RECOMENDADO
    resultados, precos, params, decisao = rodada_da_aba(
        M, view, nome, dict(PRESETS[nome].valores), cache_dir)
    return {"M": M, "oos": oos, "nf": nf, "view": view,
            "rodada": (resultados, precos, params, decisao),
            "ibov": ibov, "cost_cfg": cost_cfg, "hoje": hoje, "perfil": nome}


def _sem_desconto(score_map, anos_lideranca=None, **_):
    """O desconto de liderança desligado: devolve os scores sem mexer."""
    return dict(score_map)


def caminho_do_cache(cache_dir: Path | None, nome: str, *,
                     sem_decaimento: bool = False) -> Path | None:
    """Pickle da rodada da aba; a rodada sem desconto tem o seu, separado."""
    if cache_dir is None:
        return None
    sufixo = "_sem_decaimento" if sem_decaimento else ""
    return cache_dir / f"oos_{hashlib.md5(nome.encode()).hexdigest()[:10]}{sufixo}.pkl"


def rodada_da_aba(M, view, nome: str, valores: dict, cache_dir: Path | None, *,
                  sem_decaimento: bool = False):
    """A rodada da aba no perfil ``nome``, lida do pickle ou refeita.

    ``sem_decaimento``: a aba roda com `views.portfolio_b3._apply_decay_penalty`
    trocado pela identidade (o desconto de 7% a.a. por ano consecutivo de
    liderança desligado, nas duas chamadas: o laço anual e a carteira atual).
    A troca vale só durante a rodada.
    """
    cache = caminho_do_cache(cache_dir, nome, sem_decaimento=sem_decaimento)
    rodada = None
    if cache is not None and cache.exists():
        with cache.open("rb") as fh:
            rodada = pickle.load(fh)
        if len(rodada) != 4:
            rodada = None
    if rodada is None:
        rotulo = " sem desconto de liderança" if sem_decaimento else ""
        print(f"== perfil {nome}{rotulo}: aba ==", flush=True)
        original = view._apply_decay_penalty
        if sem_decaimento:
            view._apply_decay_penalty = _sem_desconto
        try:
            rodada = M._rodar_perfil(nome, valores)
        finally:
            view._apply_decay_penalty = original
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with cache.open("wb") as fh:
                pickle.dump(rodada, fh)
    return rodada


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="reaproveita a rodada da aba (o mesmo pickle de "
                         "medir_oos_carteira_b3.py --cache-dir)")
    args = ap.parse_args()

    prep = preparar(args.cache_dir)
    M, oos, nf = prep["M"], prep["oos"], prep["nf"]
    resultados, precos, params, decisao = prep["rodada"]
    ibov, cost_cfg, hoje, nome = prep["ibov"], prep["cost_cfg"], prep["hoje"], prep["perfil"]
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from core.b3_vigencia import janela_de_vigencia
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    # Régua oficial da carteira concentrada: controle e início da cadeia.
    medido, _ = M._medir_perfil(resultados, precos, params, decisao, ibov, cost_cfg, hoje)
    concentrada = medido["variantes"][oos.SEM_PORTAO]
    comeca_em = next((t["safra"] for t in concentrada["safras"]
                      if not t.get("sem_carteira")), None)

    contexto, por_safra = M._segmentos_por_safra(resultados, precos, params, decisao,
                                                 cost_cfg, hoje)
    selic_macro, taxa = contexto["selic_macro"], contexto["taxa_selic_aa"]
    selic_mensal = M._selic_mensal()
    res_ok = [r for r in resultados if r.get("_oos_ctx")]
    hist_batch = decisao.get("hist_batch") or {}
    pm = nf.precos_mensais(precos)

    def scores_da_safra(safra):
        s: dict[str, float] = {}
        for res in res_ok:
            s.update(oos._scores_do_ano(res, safra))
        return s

    formadas = {}
    for p in por_safra:
        inicio, _ = janela_de_vigencia(p["safra"])
        formadas[p["safra"]] = nf.carteira_nota_fatores(
            p["universo"], scores_da_safra(p["safra"]), precos, hist_batch, inicio,
            precos_mes=pm)

    def medir(escolha):
        return medir_escolha(por_safra, escolha, precos=precos, selic_macro=selic_macro,
                             taxa=taxa, selic_mensal=selic_mensal, cost_cfg=cost_cfg,
                             comeca_em=comeca_em)

    principal = medir(lambda p: list(formadas[p["safra"]]["pesos"]))
    so_nota = medir(lambda p: _tirar_piores_pela_nota(
        p["universo"], scores_da_safra(p["safra"]), nf.CORTE))
    for rotulo, m in (("nota + fatores", principal), ("só a nota", so_nota)):
        r = m["vs_equal_weight"]
        print(f"{rotulo}: media={r['media']:+.4f} ic95={r['ic95']} n={r['n_safras']} "
              f"neg={r['safras_negativas']} sem_melhor={r['media_sem_melhor_safra']}",
              flush=True)
    rc = concentrada["vs_equal_weight"]
    print(f"concentrada sem portão: media={rc['media']} ic95={rc['ic95']}", flush=True)

    cobertura = {str(s): f["cobertura"] for s, f in formadas.items()
                 if comeca_em is None or s >= comeca_em}
    safras_medidas = [t["safra"] for t in principal["safras"]]
    saida = {
        "versao_medicao": nf.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": nome,
        "safras": safras_medidas,
        "janela": (f"abril/{min(safras_medidas)} a março/{max(safras_medidas) + 1}"
                   if safras_medidas else None),
        "regra": {
            "sinal": ("média do percentil da nota do motor e do composto de fatores "
                      "(média dos percentis de momento 12-1, baixa volatilidade em "
                      f"{nf.JANELA_VOL} meses e ROE publicado antes de abril)"),
            "corte": nf.CORTE,
            "pesos": "iguais entre os que ficam",
            "formacao": "uma vez por ano, em abril (início da vigência)",
            "desempate": "sinal decrescente, depois ticker",
        },
        "metrica_principal": (
            "excesso por safra (abril/N a março/N+1), LÍQUIDO do giro, sobre "
            "pesos iguais em todo o universo da safra"),
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "cobrado": "meia spread + corretagem na compra E na venda do giro de abril",
            "benchmarks": "brutos (pesos iguais não paga custo): viés contra a carteira",
        },
        "fonte": {
            "precos": "os da aba (mensal ajustado do armazém local)",
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "fora_da_regra": [
            "guarda de entrada e piso de qualidade não filtram esta carteira",
            "portão de LLM não avalia esta carteira",
            "tetos de setor e de ciclo do perfil não se aplicam",
        ],
        "cobertura_por_safra": cobertura,
        "nota_fatores": principal,
        "comparacoes": {
            "tirar_30_pela_nota": so_nota,
            "concentrada_sem_portao": {
                "vs_equal_weight": rc,
                "fonte": "scripts/medir_oos_carteira_b3.py, mesma rodada",
            },
        },
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if args.dry_run:
        print(texto)
    else:
        CAMINHO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
