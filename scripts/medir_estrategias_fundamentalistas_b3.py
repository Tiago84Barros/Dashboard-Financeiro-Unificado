# -*- coding: utf-8 -*-
"""Mede fora da amostra cinco carteiras "de regra fundamentalista" da B3.

As regras moram em `core/b3_estrategias_fundamentalistas.py` (rota de valor,
fórmula mágica, qualidade constante, qualidade segurada por três anos e o
líder segurado até degradar). Aqui
elas são refeitas safra a safra, point-in-time, com a régua de
`scripts/medir_nota_fatores_b3.py::medir_escolha`:

- a aba de Criação de Portfólio B3 roda sem cabeça no perfil padrão, com o LLM
  desligado (`preparar`, o mesmo pickle com ``--cache-dir``);
- o universo, as saídas da bolsa e o retrato PIT de cada safra são os da
  medição oficial (`scripts/medir_oos_carteira_b3.py::_segmentos_por_safra`);
  o histórico de cada ticker é cortado em N-1 pela mesma regra de vintage
  (`_aceita_da_safra`);
- o custo do giro de abril é o de `CostConfig.brasil_pf_default()`, numa cadeia
  contínua desde a primeira safra com carteira da medição oficial; safra sem
  nome vira caixa à Selic.

Cada braço é comparado com pesos iguais no universo e, PAREADO safra a safra,
com a carteira padrão atual (Equilibrado, sem portão). A regra de decisão é
`core.b3_estrategias_fundamentalistas.REGRA_DE_DECISAO`, registrada antes de
medir.

Uso::

    python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta>
    python scripts/medir_estrategias_fundamentalistas_b3.py --dry-run
    python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> \n        --dividendo-outra-janela   # §24: safras 2013 a 2016
    python scripts/medir_estrategias_fundamentalistas_b3.py --cache-dir <pasta> \n        --sobreposicao-rota       # §25: dividendo de 6% sobre a rota de valor

O armazém local precisa estar de pé. Grava
`data/oos_estrategias_fundamentalistas_b3.json`; nada vai para banco nenhum.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_estrategias_fundamentalistas_b3.json"
CAMINHO_ENXUTA = ROOT / "data" / "oos_rota_valor_enxuta_b3.json"
CAMINHO_PEQUENAS_DIVIDENDO = ROOT / "data" / "oos_pequenas_dividendo_b3.json"
CAMINHO_DIVIDENDO_OUTRA_JANELA = ROOT / "data" / "oos_dividendo_outra_janela_b3.json"
CAMINHO_SOBREPOSICAO_ROTA = ROOT / "data" / "oos_sobreposicao_dividendo_rota_b3.json"
CAMINHO_DIVIDENDO_SEGURADO = ROOT / "data" / "oos_dividendo_segurado_b3.json"
CAMINHO_SOLVENCIA_DOIS_ANOS = ROOT / "data" / "oos_solvencia_dois_anos_b3.json"

#: A rodada sem piso de tamanho (§23): o perfil padrão com só o valor de
#: mercado mínimo trocado. A liquidez mínima (R$ 500 mil) fica.
VALORES_SEM_PISO = {"pb3_min_mcap": "Sem filtro"}
CHAVE_CACHE_SEM_PISO = "Equilibrado|pb3_min_mcap=Sem filtro"


def selic_ate(selic_por_ano: dict, ano: int) -> float | None:
    """Selic do ano mais recente até ``ano``; None se a série ainda não começou.

    Nunca cai na média da série inteira (``taxa_selic_aa`` da aba): ela inclui
    anos futuros e faria a carteira de uma safra antiga mudar quando a série
    ganha observações novas.
    """
    anos = [a for a in selic_por_ano if int(a) <= int(ano)]
    return float(selic_por_ano[max(anos)]) if anos else None


def diferenca_pareada(controle: list[dict], braco: list[dict], chave: str) -> list[dict]:
    """Diferença safra a safra só onde as duas mediram (mesma safra)."""
    base = {int(t["safra"]): t for t in controle if t.get(chave) is not None}
    return [{"safra": int(t["safra"]), "dif": float(t[chave]) - float(base[int(t["safra"])][chave])}
            for t in braco
            if t.get(chave) is not None and int(t["safra"]) in base]


def decidir(medido: dict, pareada: dict) -> dict:
    """Os três testes da regra pré-registrada e o veredito."""
    r = medido.get("vs_equal_weight") or {}
    lo = (r.get("ic95") or [None, None])[0]
    sem_melhor = r.get("media_sem_melhor_safra")
    testes = {
        "a_excesso_ew_ic_acima_de_zero": bool(
            r.get("media") is not None and r["media"] > 0 and lo is not None and lo > 0),
        "b_sem_melhor_safra_positiva": bool(sem_melhor is not None and sem_melhor > 0),
        "c_bate_a_carteira_padrao": bool(
            pareada.get("media") is not None and pareada["media"] > 0),
    }
    return {"testes": testes, "candidata": all(testes.values())}


def montar_recorte(cache_dir: Path | None) -> dict:
    """A rodada da aba, a carteira padrão medida e as escolhas de cada braço.

    Ponto único do recorte das carteiras de regra: esta medição e
    `scripts/medir_carteiras_aleatorias_b3.py` (que põe cada braço entre
    carteiras sorteadas do mesmo tamanho) leem daqui. ``escolhas`` traz os
    cinco braços da §19 e os da rota enxuta (§20); ``medir`` é a régua
    `medir_escolha` já presa a este recorte.
    """
    from scripts.medir_nota_fatores_b3 import medir_escolha, preparar

    prep = preparar(cache_dir)
    M, oos = prep["M"], prep["oos"]
    resultados, precos, params, decisao = prep["rodada"]
    ibov, cost_cfg, hoje, nome = prep["ibov"], prep["cost_cfg"], prep["hoje"], prep["perfil"]

    import core.b3_estrategias_fundamentalistas as fe
    from core.b3_retrato_pit import historico_ate, series_pl_ate
    from core.b3_vigencia import ano_base_do_score

    medido, _ = M._medir_perfil(resultados, precos, params, decisao, ibov, cost_cfg, hoje)
    padrao = medido["variantes"][oos.SEM_PORTAO]
    comeca_em = next((t["safra"] for t in padrao["safras"]
                      if not t.get("sem_carteira")), None)

    contexto, por_safra = M._segmentos_por_safra(resultados, precos, params, decisao,
                                                 cost_cfg, hoje)
    selic_macro, taxa = contexto["selic_macro"], contexto["taxa_selic_aa"]
    series_pl = contexto["series_pl"]
    selic_mensal = M._selic_mensal()
    hist_batch = decisao.get("hist_batch") or {}
    cls = decisao["classificacao"]
    setor_de = {str(t).upper(): s for t, s in zip(cls["ticker"], cls["SETOR"])}

    def selic_da_safra(safra):
        # PIT: a de N-1 ou a última antes dela. Só sem nenhuma usa a média da
        # aba, e isso fica registrado em "limites".
        s = selic_ate(selic_macro, safra - 1)
        return taxa if s is None else s

    def qualidade(p):
        safra = p["safra"]
        ano_max = ano_base_do_score(safra)
        alvos = set(p["universo"])
        hist = historico_ate({tk: df for tk, df in hist_batch.items() if tk in alvos},
                             ano_max, M._aceita_da_safra(safra))
        pl = series_pl_ate({tk: s for tk, s in series_pl.items() if tk in alvos}, ano_max)
        return fe.carteira_qualidade_constante(
            p["df_pit"], p["universo"], hist, pl, ano_max, selic_macro)
        # Sem selic_padrao: ano sem Selic medida fica fora da contagem. A média
        # da aba (taxa) vem da série inteira, futuro incluído (achado do Codex
        # no PR #796).

    por_ano = {p["safra"]: p for p in por_safra}
    cadeia = [s for s in sorted(por_ano) if comeca_em is None or s >= comeca_em]
    segurada = fe.segurar(
        cadeia, formar=lambda s: qualidade(por_ano[s]),
        manter=lambda s, lista: fe.manter_qualidade(
            lista, por_ano[s]["universo"], por_ano[s]["df_pit"]))

    tetos = {"cap": contexto["cap"], "teto_setor": params["teto_setor"],
             "teto_ciclico": params["teto_ciclico"]}

    def lideres_padrao(s):
        # Os nomes da carteira padrão da safra, montada como a variante sem
        # portão de `M._medir_perfil` (mesmo veto vazio, tetos e guarda).
        p = por_ano[s]
        itens, _ = oos.vetar_na_carteira(p["segmentos"], None, p["exclui"])
        cart = oos.montar_carteira(itens, **tetos, exclui=p["exclui"])
        return sorted(str(t).upper() for t, w in (cart.get("pesos") or {}).items()
                      if w and w > 0)

    saidas_lider: dict[int, dict] = {}

    def manter_lider(s, lista):
        p = por_ano[s]
        ano_max = ano_base_do_score(s)
        alvos = {str(t).upper() for t in lista}
        pl = series_pl_ate({tk: v for tk, v in series_pl.items()
                            if str(tk).upper() in alvos}, ano_max)
        motivos = fe.motivos_de_saida(lista, p["universo"], p["df_pit"], pl, ano_max)
        saidas_lider[s] = {t: m for t, m in motivos.items() if m}
        return sorted(t for t, m in motivos.items() if not m)

    lideres_por_safra = {s: lideres_padrao(s) for s in cadeia}
    lider_segurado = fe.segurar_lideres(cadeia, lideres_por_safra.__getitem__,
                                        manter_lider)

    escolhas = {
        fe.ROTA_VALOR: lambda p: fe.carteira_rota_valor(
            p["df_pit"], p["universo"], selic=selic_da_safra(p["safra"])),
        fe.FORMULA_MAGICA: lambda p: fe.carteira_formula_magica(
            p["df_pit"], p["universo"], setor_de=setor_de),
        fe.QUALIDADE_CONSTANTE: qualidade,
        fe.QUALIDADE_3_ANOS: lambda p: segurada.get(p["safra"], []),
        fe.LIDER_SEGURADO: lambda p: lider_segurado.get(p["safra"], []),
    }
    # Os braços da rota enxuta (PR de 22/11/2026): a rota de valor limitada
    # a N nomes, mesma régua.
    for n in fe.TAMANHOS_ROTA_ENXUTA:
        escolhas[fe.rotulo_rota_topn(n)] = (
            lambda p, n=n: fe.carteira_rota_valor_topn(
                p["df_pit"], p["universo"], n, selic=selic_da_safra(p["safra"])))

    def dividendo(p):
        # Bazin (§23): DY dos três exercícios até N-1, cortado pela vintage.
        safra = p["safra"]
        ano_max = ano_base_do_score(safra)
        alvos = set(p["universo"])
        hist = historico_ate({tk: df for tk, df in hist_batch.items() if tk in alvos},
                             ano_max, M._aceita_da_safra(safra))
        return fe.carteira_dividendo_bazin(p["df_pit"], p["universo"], hist, ano_max)

    escolhas[fe.DIVIDENDO_6] = dividendo

    def medir(escolha, memo=None, simular=None):
        return medir_escolha(por_safra, escolha, precos=precos, selic_macro=selic_macro,
                             taxa=taxa, selic_mensal=selic_mensal, cost_cfg=cost_cfg,
                             comeca_em=comeca_em, memo=memo, simular=simular)

    return {
        "prep": prep, "M": M, "oos": oos, "fe": fe, "nome": nome,
        "ibov": ibov, "precos": precos, "padrao": padrao, "comeca_em": comeca_em,
        "por_safra": por_safra, "cadeia": cadeia, "escolhas": escolhas,
        "medir": medir, "lideres_padrao": lideres_padrao,
        "selic_da_safra": selic_da_safra,
        "lideres_por_safra": lideres_por_safra, "lider_segurado": lider_segurado,
        "saidas_lider": saidas_lider, "series_pl": series_pl,
    }


def recorte_sem_piso(rc: dict, cache_dir: Path | None) -> dict:
    """A rodada da aba sem piso de tamanho e a régua presa a ela (§23).

    Mesmo perfil padrão, só ``pb3_min_mcap`` trocado: a tela lê
    "Personalizado", e o pickle tem chave própria. A cadeia de custos começa
    na MESMA safra da medição padrão (``comeca_em``), para o pareamento.
    """
    from core.b3_portfolio_presets import PERSONALIZADO, PRESETS
    from scripts.medir_nota_fatores_b3 import medir_escolha, rodada_da_aba

    prep, M, fe = rc["prep"], rc["M"], rc["fe"]
    valores = {**dict(PRESETS[prep["perfil"]].valores), **VALORES_SEM_PISO}
    resultados, precos, params, decisao = rodada_da_aba(
        M, prep["view"], PERSONALIZADO, valores, cache_dir,
        chave_cache=CHAVE_CACHE_SEM_PISO)
    contexto, por_safra = M._segmentos_por_safra(resultados, precos, params, decisao,
                                                 prep["cost_cfg"], prep["hoje"])
    selic_macro, taxa = contexto["selic_macro"], contexto["taxa_selic_aa"]
    selic_mensal = M._selic_mensal()
    com_piso = {p["safra"]: p["universo"] for p in rc["por_safra"]}
    selic_da_safra = rc["selic_da_safra"]

    def pequenas_baratas(p):
        # Safra sem universo com piso: não há como dizer quem é pequena.
        if p["safra"] not in com_piso:
            return []
        return fe.carteira_pequenas_baratas(
            p["df_pit"], p["universo"], com_piso[p["safra"]],
            selic=selic_da_safra(p["safra"]))

    escolhas = {
        fe.PEQUENAS_BARATAS: pequenas_baratas,
        fe.ROTA_VALOR_SEM_PISO: lambda p: fe.carteira_rota_valor(
            p["df_pit"], p["universo"], selic=selic_da_safra(p["safra"])),
    }

    def medir(escolha, memo=None):
        return medir_escolha(por_safra, escolha, precos=precos, selic_macro=selic_macro,
                             taxa=taxa, selic_mensal=selic_mensal,
                             cost_cfg=prep["cost_cfg"], comeca_em=rc["comeca_em"],
                             memo=memo)

    tamanhos = {p["safra"]: {
        "universo_sem_piso": len(p["universo"]),
        "universo_com_piso": len(com_piso.get(p["safra"], [])),
        "pequenas": len(fe.pequenas(p["universo"], com_piso.get(p["safra"], [])))
        if p["safra"] in com_piso else None,
    } for p in por_safra}
    return {"por_safra": por_safra, "escolhas": escolhas, "medir": medir,
            "tamanhos": tamanhos}


def _avaliar(rc: dict, m: dict) -> dict:
    """Pareada contra a carteira padrão (líquido) e os três testes."""
    oos, padrao = rc["oos"], rc["padrao"]
    par = diferenca_pareada(padrao["safras"], m["safras"], "liquido")
    resumo_par = oos.resumir(par, "dif")
    m["vs_carteira_padrao"] = {**resumo_par, "safras": par}
    m["decisao"] = decidir(m, resumo_par)
    return m


def excesso_sobre_outro_ew(braco: list[dict], referencia: list[dict]) -> list[dict]:
    """Líquido do braço menos os pesos iguais de OUTRO universo, safra a safra.

    Informativo da §23: as pequenas contra os pesos iguais do universo com
    piso (o que a carteira padrão usa como régua).
    """
    ew = {int(t["safra"]): t["equal_weight"] for t in referencia
          if t.get("equal_weight") is not None}
    return [{"safra": int(t["safra"]), "dif": float(t["liquido"]) - float(ew[int(t["safra"])])}
            for t in braco if t.get("liquido") is not None and int(t["safra"]) in ew]


def medir_pequenas_dividendo(rc: dict, cache_dir: Path | None, *,
                             dry_run: bool = False) -> int:
    """Os braços da §23: dividendo de 6%, pequenas baratas e a rota sem piso."""
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    oos, fe = rc["oos"], rc["fe"]
    sp = recorte_sem_piso(rc, cache_dir)

    bracos = {}
    bracos[fe.DIVIDENDO_6] = _avaliar(rc, rc["medir"](rc["escolhas"][fe.DIVIDENDO_6]))
    for rotulo in (fe.PEQUENAS_BARATAS, fe.ROTA_VALOR_SEM_PISO):
        m = _avaliar(rc, sp["medir"](sp["escolhas"][rotulo]))
        ref = excesso_sobre_outro_ew(m["safras"], bracos[fe.DIVIDENDO_6]["safras"])
        m["vs_equal_weight_com_piso"] = {**oos.resumir(ref, "dif"), "safras": ref}
        bracos[rotulo] = m
    for rotulo, m in bracos.items():
        r = m["vs_equal_weight"]
        print(f"{rotulo}: media={r['media']} ic95={r['ic95']} n={r['n_safras']} "
              f"sem_melhor={r['media_sem_melhor_safra']} "
              f"vs_padrao={m['vs_carteira_padrao']['media']} "
              f"candidata={m['decisao']['candidata']}", flush=True)

    sob_regra = (fe.PEQUENAS_BARATAS, fe.DIVIDENDO_6)
    safras_medidas = sorted({t["safra"] for m in bracos.values() for t in m["safras"]})
    saida = {
        "versao_medicao": fe.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "perfil_sem_piso": {"valores_trocados": VALORES_SEM_PISO,
                            "chave_cache": CHAVE_CACHE_SEM_PISO},
        "safras": safras_medidas,
        "janela": (f"abril/{min(safras_medidas)} a março/{max(safras_medidas) + 1}"
                   if safras_medidas else None),
        "regra_de_decisao": fe.REGRA_DE_DECISAO_PEQUENAS_DIVIDENDO,
        "sob_a_regra": list(sob_regra),
        "informativos": [fe.ROTA_VALOR_SEM_PISO],
        "regras": {
            fe.PEQUENAS_BARATAS: (
                "nomes no universo da rodada sem piso de valor de mercado e fora "
                "do universo com piso (R$ 1 bi), mesma liquidez mínima (R$ 500 mil), "
                "que a rota de valor classifica como 'oportunidade' (Selic de N-1)"),
            fe.DIVIDENDO_6: (
                f"DY médio dos {fe.ANOS_DY_BAZIN} exercícios até N-1 (todos presentes, "
                f"DY entre 0 e {fe.DY_MAXIMO_CRIVEL:.0%}) >= {fe.DY_ALVO_BAZIN:.0%}; "
                "sem falha da trava de solvência; universo da carteira padrão"),
            fe.ROTA_VALOR_SEM_PISO: (
                "informativo: a rota de valor em todo o universo sem piso de tamanho"),
            "pesos": "iguais entre os escolhidos",
            "formacao": "em abril (início da vigência)",
        },
        "metrica_principal": (
            "excesso por safra (abril/N a março/N+1), LÍQUIDO do giro, sobre "
            "pesos iguais no universo da própria carteira (sem piso para as "
            "pequenas e a rota sem piso; com piso para o dividendo)"),
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "benchmarks": "brutos (pesos iguais não paga custo): viés contra a carteira",
        },
        "fonte": {
            **fonte_dos_precos_da_aba(rc["precos"]),
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "limites": [
            "DY e múltiplos anuais com o preço de fim de cada exercício, não o de abril/N",
            "'pequena' inclui quem não tem valor de mercado medido: o piso também o corta",
            ("pequena é quem sai do universo sem o piso; uma empresa grande cujo "
             "segmento só existe sem o piso também entraria"),
            "safra sem nenhum nome vira caixa à Selic",
            "nove safras não separam sorte de regra com folga; o teste (b) existe por isso",
            "a liquidez mínima de R$ 500 mil continua valendo nas duas rodadas",
            "custo do giro igual ao das grandes: o spread maior das pequenas não entra",
        ],
        "estrategias": bracos,
        "carteira_padrao": {
            "vs_equal_weight": rc["padrao"]["vs_equal_weight"],
            "fonte": "scripts/medir_oos_carteira_b3.py, mesma rodada, sem portão",
        },
        "tamanho_dos_universos": {str(k): v for k, v in sorted(sp["tamanhos"].items())},
        "candidatas": [k for k in sob_regra if bracos[k]["decisao"]["candidata"]],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if dry_run:
        print(texto)
    else:
        CAMINHO_PEQUENAS_DIVIDENDO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO_PEQUENAS_DIVIDENDO}")
    return 0


FONTE_DO_ARMAZEM = "market.historical_prices"


def procedencia_dos_precos(df) -> dict:
    """De onde vieram os preços, lido do próprio carregador (§24).

    ``_batch_yf_precos_mensais`` cai no yfinance quando o armazém falha e
    rotula a troca em ``attrs["fonte_precos"]``/``attrs["fonte_precos_erro"]``.
    A §24 só vale sobre o armazém: o yfinance com ``period="max"`` é outra
    fita, com outro ajuste. Fonte diferente ou ausente para a medição, em vez
    de publicar uma confirmação com o rótulo errado.
    """
    attrs = getattr(df, "attrs", {}) or {}
    fonte, erro = attrs.get("fonte_precos"), attrs.get("fonte_precos_erro")
    if fonte != FONTE_DO_ARMAZEM:
        raise RuntimeError(
            f"preços sem corte vieram de {fonte or 'fonte não rotulada'}"
            f"{f' ({erro})' if erro else ''}, não de {FONTE_DO_ARMAZEM}: "
            "a §24 só mede sobre o armazém local")
    return {"fonte_precos": fonte, "fonte_precos_erro": erro}


def precos_sem_corte(precos_da_aba):
    """Os preços da aba sem o corte de 10 anos (§24).

    A aba lê ``period="10y"`` (views/portfolio_b3.py, "Carregando preços
    mensais ajustados"), e por isso a primeira safra com carteira é 2017. Aqui
    a MESMA leitura, com as mesmas trocas de código e as mesmas empresas que
    saíram da bolsa, sem o corte. Só as colunas da aba: o universo não muda.
    Devolve ``(precos, conferencia)``: ``conferencia`` mede quanto o trecho
    comum difere dos preços da aba (tem de dar zero).
    """
    import numpy as np
    import pandas as pd

    import core.b3_saidas as _saidas
    import core.b3_troca_codigo as _troca
    import views.portfolio_b3 as view
    from views.empresas_b3 import _batch_yf_precos_mensais

    df_set = view._so_acoes(view._db.load_setores())
    _df, mapa = _troca.deduplicar_por_empresa(df_set)
    trocas = tuple(sorted(mapa.items()))
    colunas = [str(c) for c in precos_da_aba.columns]
    bruto = _batch_yf_precos_mensais(tuple(colunas) + tuple(sorted(mapa)),
                                     period="max", trocas=trocas)
    procedencia = procedencia_dos_precos(bruto)
    longo = _troca.dobrar_colunas(bruto, mapa)
    faltam = [c for c in colunas if c not in longo.columns]
    if faltam:
        p_s = _saidas.precos_mensais(_saidas.carregar())
        p_s = p_s[[c for c in faltam if c in p_s.columns]]
        if not p_s.empty:
            longo = longo.join(p_s, how="outer").sort_index()
    longo = longo.reindex(columns=colunas)

    comum = precos_da_aba.index.intersection(longo.index)
    a = precos_da_aba.loc[comum, colunas].astype(float)
    b = longo.loc[comum, colunas].astype(float)
    ambos = a.notna() & b.notna()
    rel = ((b - a).abs() / a.abs().replace(0, np.nan)).where(ambos)
    conferencia = {
        **procedencia,
        "fonte_precos_da_aba": (getattr(precos_da_aba, "attrs", {}) or {}).get(
            "fonte_precos"),
        "inicio_aba": str(precos_da_aba.index.min().date()),
        "inicio_sem_corte": str(longo.dropna(how="all").index.min().date()),
        "colunas": len(colunas),
        "colunas_sem_preco_sem_corte": int(longo.notna().sum().eq(0).sum()),
        "pontos_comparados": int(ambos.to_numpy().sum()),
        "pontos_que_a_aba_tem_e_o_longo_nao": int((a.notna() & b.isna()).to_numpy().sum()),
        "maior_diferenca_relativa": float(np.nanmax(rel.to_numpy()))
        if ambos.to_numpy().any() else None,
    }
    return pd.DataFrame(longo), conferencia


TOLERANCIA_CONFERENCIA = 1e-9


def exigir_precos_conferidos(conferencia: dict) -> dict:
    """Para a medição se os preços da aba não são, ponto a ponto, os do armazém.

    Recebe a ``conferencia`` de `precos_sem_corte` (que já exige que a releitura
    venha de ``market.historical_prices``). Exige pontos comparados, nenhum
    ponto da aba sem par na releitura e diferença relativa máxima dentro da
    tolerância. Devolve a própria conferência para ir ao JSON.
    """
    dif = conferencia.get("maior_diferenca_relativa")
    problemas = []
    if conferencia.get("fonte_precos") != FONTE_DO_ARMAZEM:
        problemas.append(f"releitura de {conferencia.get('fonte_precos')}")
    if not conferencia.get("pontos_comparados"):
        problemas.append("nenhum ponto comparado")
    if conferencia.get("pontos_que_a_aba_tem_e_o_longo_nao"):
        problemas.append(f"{conferencia['pontos_que_a_aba_tem_e_o_longo_nao']} "
                         "pontos da aba sem par no armazém")
    if dif is None or dif > TOLERANCIA_CONFERENCIA:
        problemas.append(f"maior diferença relativa {dif}")
    if problemas:
        raise RuntimeError(
            f"os preços da aba não conferem com {FONTE_DO_ARMAZEM} "
            f"({'; '.join(problemas)}): a medição não publica rótulo de fonte sem prova")
    return conferencia


def fonte_dos_precos_da_aba(precos_da_aba) -> dict:
    """O bloco de preços da ``fonte``, provado em vez de declarado (§25, #804).

    A rodada da aba guardada em pickle perde ``attrs``: o rótulo que o
    carregador põe quando cai no yfinance não chega aqui. Por isso relê as
    mesmas colunas do armazém (`precos_sem_corte`) e exige que o trecho comum
    seja idêntico (`exigir_precos_conferidos`). Vale para toda medição que
    usa os preços da aba: §23, §25, rota enxuta e nota + fatores.
    """
    _longo, conferencia = precos_sem_corte(precos_da_aba)
    return {
        "precos": (f"mensal ajustado de {FONTE_DO_ARMAZEM}: os da aba, conferidos "
                   "ponto a ponto contra uma releitura do armazém"),
        "conferencia_dos_precos": exigir_precos_conferidos(conferencia),
    }


def medir_dividendo_outra_janela(rc: dict, *, dry_run: bool = False) -> int:
    """O dividendo de 6% nas safras 2013 a 2016 (§24), mesma régua da §23.

    O universo, o retrato PIT e o histórico de cada safra saem da MESMA rodada
    da aba (``primeira`` antecipada em `_segmentos_por_safra`); só os preços
    perdem o corte de 10 anos. A cadeia de custos começa em 2013.
    """
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from scripts.medir_nota_fatores_b3 import medir_escolha
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    prep, M, oos, fe = rc["prep"], rc["M"], rc["oos"], rc["fe"]
    resultados, precos_aba, params, decisao = prep["rodada"]
    precos, conferencia = precos_sem_corte(precos_aba)
    print(f"precos sem corte: {conferencia}", flush=True)

    contexto, por_safra = M._segmentos_por_safra(
        resultados, precos_aba, params, decisao, prep["cost_cfg"], prep["hoje"],
        primeira=fe.PRIMEIRA_SAFRA_OUTRA_JANELA)
    m = medir_escolha(por_safra, rc["escolhas"][fe.DIVIDENDO_6], precos=precos,
                      selic_macro=contexto["selic_macro"],
                      taxa=contexto["taxa_selic_aa"], selic_mensal=M._selic_mensal(),
                      cost_cfg=prep["cost_cfg"],
                      comeca_em=fe.PRIMEIRA_SAFRA_OUTRA_JANELA)

    def recorte(linhas, ate=None, desde=None):
        return [t for t in linhas if (ate is None or t["safra"] <= ate)
                and (desde is None or t["safra"] >= desde)]

    janela = recorte(m["safras"], ate=fe.ULTIMA_SAFRA_OUTRA_JANELA)
    resumo = oos.resumir(janela, "excesso_ew")
    decisao_24 = fe.leitura_outra_janela(resumo)

    secao_23 = {}
    if CAMINHO_PEQUENAS_DIVIDENDO.exists():
        doc = json.loads(CAMINHO_PEQUENAS_DIVIDENDO.read_text(encoding="utf-8"))
        secao_23 = {int(t["safra"]): t["excesso_ew"] for t in
                    doc["estrategias"][fe.DIVIDENDO_6]["safras"]}
    depois = recorte(m["safras"], desde=fe.ULTIMA_SAFRA_OUTRA_JANELA + 1)
    conferencia_23 = [{"safra": t["safra"], "excesso_aqui": t["excesso_ew"],
                       "excesso_secao_23": secao_23.get(t["safra"]),
                       "dif": (t["excesso_ew"] - secao_23[t["safra"]])
                       if t["safra"] in secao_23 else None}
                      for t in depois]

    print(f"{fe.DIVIDENDO_6} 2013-2016: media={resumo['media']} ic95={resumo['ic95']} "
          f"n={resumo['n_safras']} sem_melhor={resumo['media_sem_melhor_safra']} "
          f"leitura={decisao_24['leitura']}", flush=True)

    saida = {
        "versao_medicao": fe.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "janela": (f"abril/{fe.PRIMEIRA_SAFRA_OUTRA_JANELA} a "
                   f"março/{fe.ULTIMA_SAFRA_OUTRA_JANELA + 1}"),
        "safras_medidas": [t["safra"] for t in janela],
        "regra_de_decisao": fe.REGRA_DE_DECISAO_DIVIDENDO_OUTRA_JANELA,
        "regra": (
            f"DY médio dos {fe.ANOS_DY_BAZIN} exercícios até N-1 (todos presentes, "
            f"DY entre 0 e {fe.DY_MAXIMO_CRIVEL:.0%}) >= {fe.DY_ALVO_BAZIN:.0%}; "
            "sem falha da trava de solvência; universo da carteira padrão; "
            "pesos iguais; formada em abril"),
        "metrica_principal": (
            "excesso por safra (abril/N a março/N+1), LÍQUIDO do giro, sobre "
            "pesos iguais no universo da carteira padrão"),
        "resultado": {
            "vs_equal_weight": resumo,
            "com_ir_vs_equal_weight": oos.resumir(janela, "excesso_ew_com_ir"),
            "bruto_vs_equal_weight": oos.resumir(janela, "excesso_ew_bruto"),
            "leitura": decisao_24["leitura"],
            "testes": decisao_24["testes"],
            "safras": janela,
        },
        "informativos": {
            "janela_inteira_2013_2025": {
                "vs_equal_weight": m["vs_equal_weight"],
                "com_ir_vs_equal_weight": m["com_ir_vs_equal_weight"],
                "giro_medio_anual": m["giro_medio_anual"],
                "custo_medio_anual": m["custo_medio_anual"],
            },
            "conferencia_com_a_secao_23": {
                "nota": ("2017-2025 com os preços sem corte; a cadeia de custos "
                         "começa em 2013, então 2017 paga um giro diferente"),
                "safras": conferencia_23,
            },
        },
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "benchmarks": "brutos (pesos iguais não paga custo): viés contra a carteira",
        },
        "fonte": {
            "precos": (f"mensal ajustado de {conferencia['fonte_precos']} (lido do "
                       "carregador; outra fonte para a medição), a mesma leitura "
                       "da aba sem o corte de 10 anos"),
            "conferencia_dos_precos": conferencia,
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "limites": [
            "só quatro safras: o IC 95% raramente fica acima de zero com tão pouco",
            "o universo antigo usa o piso nominal de R$ 1 bi da aba, sem correção pela inflação",
            "o DY de 2013 precisa dos exercícios 2010 a 2012; quem não tem os três fica fora",
            "DY e múltiplos anuais com o preço de fim de cada exercício, não o de abril/N",
            "safra sem nenhum nome vira caixa à Selic",
            "sem o teste (c): a carteira padrão não tem carteira antes de 2017",
        ],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if dry_run:
        print(texto)
    else:
        CAMINHO_DIVIDENDO_OUTRA_JANELA.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO_DIVIDENDO_OUTRA_JANELA}")
    return 0


def meio_a_meio(a: list[dict], b: list[dict], chave: str) -> list[dict]:
    """Média safra a safra de ``chave`` nas duas carteiras (só onde as duas mediram).

    Informativo da §25: aproxima metade do dinheiro em cada carteira,
    rebalanceada a 50% em abril sem custo extra.
    """
    base = {int(t["safra"]): t for t in a if t.get(chave) is not None}
    return [{"safra": int(t["safra"]),
             "dif": 0.5 * (float(t[chave]) + float(base[int(t["safra"])][chave]))}
            for t in b if t.get(chave) is not None and int(t["safra"]) in base]


def medir_sobreposicao_rota(rc: dict, *, dry_run: bool = False) -> int:
    """§25: o dividendo de 6% repete a rota de valor? Somar os dois rende mais?"""
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    oos, fe = rc["oos"], rc["fe"]
    escolher_rota = rc["escolhas"][fe.ROTA_VALOR]
    escolher_div = rc["escolhas"][fe.DIVIDENDO_6]
    guardado: dict[int, tuple[list[str], list[str]]] = {}

    def duas(p):
        # Cada carteira é montada uma vez por safra; os braços só combinam.
        s = int(p["safra"])
        if s not in guardado:
            guardado[s] = (list(escolher_rota(p)), list(escolher_div(p)))
        return guardado[s]

    memo: dict = {}
    m_rota = rc["medir"](lambda p: duas(p)[0], memo)
    m_div = rc["medir"](lambda p: duas(p)[1], memo)
    m_uniao = rc["medir"](lambda p: fe.carteira_uniao(*duas(p)), memo)
    m_fora = rc["medir"](lambda p: fe.carteira_fora_de(duas(p)[1], duas(p)[0]), memo)

    par = diferenca_pareada(m_rota["safras"], m_uniao["safras"], "liquido")
    resumo = oos.resumir(par, "dif")
    decisao = fe.leitura_sobreposicao(resumo)
    par_ir = diferenca_pareada(m_rota["safras"], m_uniao["safras"], "liquido_com_ir")
    fora_vs_rota = diferenca_pareada(m_rota["safras"], m_fora["safras"], "liquido")
    div_vs_rota = diferenca_pareada(m_rota["safras"], m_div["safras"], "liquido")
    meio_ew = meio_a_meio(m_rota["safras"], m_div["safras"], "excesso_ew")
    meio_vs_rota = [{"safra": t["safra"], "dif": 0.5 * t["dif"]} for t in div_vs_rota]

    medidas = {int(t["safra"]) for t in m_rota["safras"]}
    por_safra_sob = {}
    for p in rc["por_safra"]:
        s = int(p["safra"])
        if s in medidas:
            rota, div = duas(p)
            por_safra_sob[str(s)] = fe.sobreposicao(rota, div)
    fracoes = [v["fracao_do_dividendo_na_rota"] for v in por_safra_sob.values()
               if v["fracao_do_dividendo_na_rota"] is not None]
    jaccards = [v["jaccard"] for v in por_safra_sob.values() if v["jaccard"] is not None]

    def _curto(m):
        return {k: m[k] for k in ("vs_equal_weight", "com_ir_vs_equal_weight",
                                  "bruto_vs_equal_weight", "giro_medio_anual",
                                  "custo_medio_anual", "safras") if k in m}

    for rotulo, m in ((fe.ROTA_VALOR, m_rota), (fe.DIVIDENDO_6, m_div),
                      (fe.ROTA_MAIS_DIVIDENDO, m_uniao),
                      (fe.DIVIDENDO_FORA_DA_ROTA, m_fora)):
        r = m["vs_equal_weight"]
        print(f"{rotulo}: media={r['media']} ic95={r['ic95']} n={r['n_safras']} "
              f"sem_melhor={r['media_sem_melhor_safra']}", flush=True)
    print(f"uniao - rota: media={resumo['media']} ic95={resumo['ic95']} "
          f"sem_melhor={resumo['media_sem_melhor_safra']} leitura={decisao['leitura']}",
          flush=True)

    safras_medidas = sorted(medidas)
    saida = {
        "versao_medicao": fe.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "safras": safras_medidas,
        "janela": (f"abril/{min(safras_medidas)} a março/{max(safras_medidas) + 1}"
                   if safras_medidas else None),
        "regra_de_decisao": fe.REGRA_DE_DECISAO_SOBREPOSICAO_ROTA,
        "sob_a_regra": [fe.ROTA_MAIS_DIVIDENDO],
        "regras": {
            fe.ROTA_VALOR: "a da §19, sem mudança",
            fe.DIVIDENDO_6: "a da §23, sem mudança",
            fe.ROTA_MAIS_DIVIDENDO: "a união dos nomes das duas, cada nome uma vez",
            fe.DIVIDENDO_FORA_DA_ROTA: "os nomes do dividendo que a rota não tem",
            "pesos": "iguais entre os escolhidos",
            "formacao": "em abril (início da vigência)",
        },
        "metrica_principal": (
            "diferença pareada, safra a safra, LÍQUIDA do giro: rota + dividendo "
            "menos a rota de valor sozinha"),
        "resultado": {
            "uniao_menos_rota": {**resumo, "safras": par},
            "leitura": decisao["leitura"],
            "testes": decisao["testes"],
        },
        "sobreposicao": {
            "por_safra": por_safra_sob,
            "fracao_media_do_dividendo_na_rota": (
                sum(fracoes) / len(fracoes) if fracoes else None),
            "jaccard_medio": sum(jaccards) / len(jaccards) if jaccards else None,
        },
        "informativos": {
            "uniao_menos_rota_com_ir": {**oos.resumir(par_ir, "dif"), "safras": par_ir},
            "dividendo_fora_da_rota_menos_rota": {
                **oos.resumir(fora_vs_rota, "dif"), "safras": fora_vs_rota},
            "dividendo_menos_rota": {**oos.resumir(div_vs_rota, "dif"), "safras": div_vs_rota},
            "meio_a_meio_vs_equal_weight": {**oos.resumir(meio_ew, "dif"), "safras": meio_ew},
            "meio_a_meio_menos_rota": {
                **oos.resumir(meio_vs_rota, "dif"), "safras": meio_vs_rota},
        },
        "estrategias": {
            fe.ROTA_VALOR: _curto(m_rota),
            fe.DIVIDENDO_6: _curto(m_div),
            fe.ROTA_MAIS_DIVIDENDO: _curto(m_uniao),
            fe.DIVIDENDO_FORA_DA_ROTA: _curto(m_fora),
        },
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "benchmarks": "brutos (pesos iguais não paga custo): viés contra a carteira",
        },
        "fonte": {
            **fonte_dos_precos_da_aba(rc["precos"]),
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "limites": [
            "nove safras: diferenças pequenas entre carteiras parecidas não se separam do ruído",
            "a união dá peso igual a cada nome, uma vez só, esteja em uma ou nas duas regras",
            ("o meio a meio é aproximado: média dos retornos líquidos, cada metade "
             "volta a 50% em abril sem custo extra"),
            "safra em que o 'fora da rota' não tem nome vira caixa à Selic",
        ],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if dry_run:
        print(texto)
    else:
        CAMINHO_SOBREPOSICAO_ROTA.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO_SOBREPOSICAO_ROTA}")
    return 0


def _cadeia_do_dividendo_segurado(rc: dict):
    """Entrada da §23 por safra e o `segurar` das §26 e §27.

    ``segurar(considerar, persistentes=())`` devolve (carteiras por safra,
    saídas por safra). Os motivos de ``persistentes`` só tiram o nome se ele
    também os tinha na safra anterior (`fe.motivos_persistentes`, §27).
    """
    from core.b3_retrato_pit import series_pl_ate
    from core.b3_vigencia import ano_base_do_score

    fe = rc["fe"]
    por_ano = {int(p["safra"]): p for p in rc["por_safra"]}
    cadeia = [int(s) for s in rc["cadeia"]]
    escolher_div = rc["escolhas"][fe.DIVIDENDO_6]
    entrada = {s: sorted(str(t).upper() for t in escolher_div(por_ano[s])) for s in cadeia}

    def motivos(s, lista):
        p = por_ano.get(s)
        if p is None:
            return {}
        ano_max = ano_base_do_score(s)
        alvos = {str(t).upper() for t in lista}
        pl = series_pl_ate({tk: v for tk, v in rc["series_pl"].items()
                            if str(tk).upper() in alvos}, ano_max)
        return fe.motivos_de_saida(lista, p["universo"], p["df_pit"], pl, ano_max)

    def segurar(considerar, persistentes=()):
        saidas: dict[int, dict[str, list[str]]] = {}

        def manter(s, lista):
            m = motivos(s, lista)
            if persistentes:
                m = fe.motivos_persistentes(m, motivos(s - 1, lista), persistentes)
            saidas[s] = {tk: [x for x in ms if x in considerar]
                         for tk, ms in m.items() if any(x in considerar for x in ms)}
            return fe.quem_fica(m, considerar)

        return (fe.segurar_lideres(cadeia, entrada.__getitem__, manter,
                                   barrar_reentrada=True), saidas)

    return entrada, segurar


def voltas(carteiras: dict[int, list[str]], saidas: dict[int, dict]) -> int:
    """Quantas saídas voltaram à carteira numa safra posterior."""
    n = 0
    for s, quem in saidas.items():
        depois: set[str] = set()
        for k, v in carteiras.items():
            if k > s:
                depois |= set(v)
        n += sum(1 for tk in quem if tk in depois)
    return n


def medir_solvencia_dois_anos(rc: dict, *, dry_run: bool = False) -> int:
    """§27: a §26 com a falha de solvência exigida em dois anos seguidos."""
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    oos, fe = rc["oos"], rc["fe"]
    cadeia = [int(s) for s in rc["cadeia"]]
    escolher_div = rc["escolhas"][fe.DIVIDENDO_6]
    entrada, segurar = _cadeia_do_dividendo_segurado(rc)
    considerar = fe.MOTIVOS_DIVIDENDO_SEGURADO

    c26, s26 = segurar(considerar)
    c27, s27 = segurar(considerar, fe.PERSISTENTES_DOIS_ANOS)
    cpj, spj = segurar(considerar, fe.PERSISTENTES_COM_PREJUIZO)

    def aporte(itens, cfg):
        return fe.simular_aportes(itens, cfg, aporte=fe.APORTE_DIVIDENDO_SEGURADO)

    def da(carteiras):
        return lambda p: carteiras.get(int(p["safra"]), [])

    memo: dict = {}
    medir = rc["medir"]
    m27 = _avaliar(rc, medir(da(c27), memo, simular=aporte))
    mpj = _avaliar(rc, medir(da(cpj), memo, simular=aporte))
    m26 = _avaliar(rc, medir(da(c26), memo, simular=aporte))
    mdiv = _avaliar(rc, medir(escolher_div, memo))

    def pareada(controle, braco, chave="liquido"):
        par = diferenca_pareada(controle["safras"], braco["safras"], chave)
        return {**oos.resumir(par, "dif"), "safras": par}

    def contagem(saidas):
        out: dict[str, int] = {}
        for v in saidas.values():
            for ms in v.values():
                for x in ms:
                    out[x] = out.get(x, 0) + 1
        return out

    resumo_saidas = {
        nome: {"saidas": sum(len(v) for v in sd.values()), "voltas": voltas(c, sd),
               "motivos": contagem(sd),
               "nomes_por_safra": {str(s): len(c.get(s, [])) for s in cadeia}}
        for nome, c, sd in ((fe.DIVIDENDO_SEGURADO_DOIS_ANOS, c27, s27),
                            (fe.DIVIDENDO_SEGURADO_DOIS_ANOS_COM_PREJUIZO, cpj, spj),
                            (fe.DIVIDENDO_SEGURADO, c26, s26))}

    rotulo = fe.DIVIDENDO_SEGURADO_DOIS_ANOS
    for nome, m in ((rotulo, m27), ("com_prejuizo", mpj), ("secao_26", m26),
                    (fe.DIVIDENDO_6, mdiv)):
        r = m["vs_equal_weight"]
        print(f"{nome}: media={r['media']} ic95={r['ic95']} n={r['n_safras']} "
              f"sem_melhor={r['media_sem_melhor_safra']} giro={m['giro_medio_anual']} "
              f"giro_venda={m.get('giro_venda_medio_anual')} "
              f"vs_padrao={m['vs_carteira_padrao']['media']} "
              f"candidata={m['decisao']['candidata']}", flush=True)
    print(json.dumps(resumo_saidas, ensure_ascii=False), flush=True)

    vs26 = pareada(m26, m27)
    g27, g26 = m27.get("giro_venda_medio_anual"), m26.get("giro_venda_medio_anual")
    hi = (vs26.get("ic95") or [None, None])[1]
    hipotese = {
        "giro_venda_abaixo_da_26": bool(g27 is not None and g26 is not None and g27 < g26),
        "pareada_contra_26_nao_inteira_abaixo_de_zero": bool(hi is None or hi >= 0),
    }
    hipotese["confirma"] = all(hipotese.values())
    print(f"hipotese={hipotese} vs26={vs26.get('media')} ic={vs26.get('ic95')}", flush=True)

    def _curto(m):
        return {k: m[k] for k in ("vs_equal_weight", "com_ir_vs_equal_weight",
                                  "bruto_vs_equal_weight", "vs_carteira_padrao",
                                  "decisao", "giro_medio_anual",
                                  "giro_venda_medio_anual", "custo_medio_anual",
                                  "safras") if k in m}

    medidas = sorted(int(t["safra"]) for t in m27["safras"])
    composicao = {}
    anterior: list[str] = []
    for s in cadeia:
        atual = c27.get(s, [])
        composicao[str(s)] = {
            "n": len(atual), "carteira": atual,
            "entraram": sorted(set(atual) - set(anterior)),
            "sairam": s27.get(s, {}),
            "a_mais_que_a_26": sorted(set(atual) - set(c26.get(s, []))),
            "seguem_sem_passar_na_entrada": sorted(set(atual) - set(entrada[s])),
        }
        anterior = atual

    saida = {
        "versao_medicao": fe.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "safras": medidas,
        "janela": (f"abril/{min(medidas)} a março/{max(medidas) + 1}"
                   if medidas else None),
        "regra_de_decisao": fe.REGRA_DE_DECISAO_SOLVENCIA_DOIS_ANOS,
        "sob_a_regra": [rotulo],
        "metrica_principal": ("excesso médio LÍQUIDO sobre pesos iguais no universo, "
                              "retorno ponderado pelo tempo (o aporte não conta como ganho)"),
        "resultado": {
            "excesso_sobre_equal_weight": m27["vs_equal_weight"],
            "vs_carteira_padrao": m27["vs_carteira_padrao"],
            "testes": m27["decisao"]["testes"],
            "candidata": m27["decisao"]["candidata"],
            "giro_medio_anual": m27["giro_medio_anual"],
            "giro_venda_medio_anual": g27,
            "hipotese_do_giro": hipotese,
        },
        "informativos": {
            "dois_anos_menos_26": vs26,
            "dois_anos_menos_dividendo_6": pareada(mdiv, m27),
            "com_prejuizo_menos_dois_anos": pareada(m27, mpj),
            "excesso_com_ir": m27["com_ir_vs_equal_weight"],
            "saidas": resumo_saidas,
        },
        "composicao": composicao,
        "estrategias": {
            rotulo: _curto(m27),
            fe.DIVIDENDO_SEGURADO_DOIS_ANOS_COM_PREJUIZO: _curto(mpj),
            fe.DIVIDENDO_SEGURADO: _curto(m26),
            fe.DIVIDENDO_6: _curto(mdiv),
        },
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "benchmarks": ("brutos e sem aporte (pesos iguais não paga custo e "
                           "volta a 1/N todo abril): viés contra a carteira"),
        },
        "fonte": {
            **fonte_dos_precos_da_aba(rc["precos"]),
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "limites": [
            "nove safras: diferenças pequenas não se separam do ruído",
            ("a hipótese nasceu da §26 medida nas mesmas safras: o teste do giro "
             "não é fora da amostra"),
            ("o dividendo é reinvestido no próprio nome (preço ajustado), não vai "
             "para os que estão abaixo do alvo"),
            ("muitos braços medidos da §19 à §27 no mesmo período: um falso positivo "
             "fica mais provável do que o IC sugere"),
            "as §1 a §22 do guia ainda usam a leitura antiga dos fundamentos",
        ],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if dry_run:
        print(texto)
    else:
        CAMINHO_SOLVENCIA_DOIS_ANOS.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO_SOLVENCIA_DOIS_ANOS}")
    return 0


def medir_dividendo_segurado(rc: dict, *, dry_run: bool = False) -> int:
    """§26: o dividendo de 6% segurado, com equilíbrio pelo aporte.

    Entra quem passa na regra da §23; sai só quem degrada (saída do líder
    segurado da §19). O peso-alvo é 1/N, e o aporte anual compra primeiro
    quem está mais abaixo (`fe.simular_aportes`): ninguém é vendido por ter
    subido.
    """
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    oos, fe = rc["oos"], rc["fe"]
    cadeia = [int(s) for s in rc["cadeia"]]
    escolher_div = rc["escolhas"][fe.DIVIDENDO_6]
    entrada, segurar = _cadeia_do_dividendo_segurado(rc)

    segurada, saidas = segurar(fe.MOTIVOS_DIVIDENDO_SEGURADO)
    so_solvencia, saidas_solvencia = segurar(fe.MOTIVOS_SO_SOLVENCIA)

    def aportes(fracao):
        return lambda itens, cfg: fe.simular_aportes(itens, cfg, aporte=fracao)

    def da(carteiras):
        return lambda p: carteiras.get(int(p["safra"]), [])

    memo: dict = {}
    medir = rc["medir"]
    m_seg = _avaliar(rc, medir(da(segurada), memo,
                               simular=aportes(fe.APORTE_DIVIDENDO_SEGURADO)))
    m_sens = {f"aporte_{round(a * 100)}": _avaliar(rc, medir(da(segurada), memo,
                                                              simular=aportes(a)))
              for a in fe.APORTES_SENSIBILIDADE}
    m_regua19 = _avaliar(rc, medir(da(segurada), memo))
    m_solv = _avaliar(rc, medir(da(so_solvencia), memo,
                                simular=aportes(fe.APORTE_DIVIDENDO_SEGURADO)))
    m_div = _avaliar(rc, medir(escolher_div, memo))
    m_div_aporte = _avaliar(rc, medir(escolher_div, memo,
                                      simular=aportes(fe.APORTE_DIVIDENDO_SEGURADO)))

    def pareada(controle, braco, chave="liquido"):
        par = diferenca_pareada(controle["safras"], braco["safras"], chave)
        return {**oos.resumir(par, "dif"), "safras": par}

    rotulo = fe.DIVIDENDO_SEGURADO
    for nome, m in ((rotulo, m_seg), ("regua_19", m_regua19), ("so_solvencia", m_solv),
                    (fe.DIVIDENDO_6, m_div), ("dividendo_6_com_aporte", m_div_aporte),
                    *m_sens.items()):
        r = m["vs_equal_weight"]
        print(f"{nome}: media={r['media']} ic95={r['ic95']} n={r['n_safras']} "
              f"sem_melhor={r['media_sem_melhor_safra']} giro={m['giro_medio_anual']} "
              f"giro_venda={m.get('giro_venda_medio_anual')} "
              f"vs_padrao={m['vs_carteira_padrao']['media']} "
              f"candidata={m['decisao']['candidata']}", flush=True)

    medidas = sorted(int(t["safra"]) for t in m_seg["safras"])
    composicao = {}
    anterior: list[str] = []
    for s in cadeia:
        atual = segurada.get(s, [])
        composicao[str(s)] = {
            "n": len(atual),
            "carteira": atual,
            "entraram": sorted(set(atual) - set(anterior)),
            "sairam": saidas.get(s, {}),
            "seguem_sem_passar_na_entrada": sorted(set(atual) - set(entrada[s])),
            "n_so_solvencia": len(so_solvencia.get(s, [])),
            "sairam_so_solvencia": saidas_solvencia.get(s, {}),
        }
        anterior = atual
    contagem_motivos: dict[str, int] = {}
    for v in saidas.values():
        for ms in v.values():
            for x in ms:
                contagem_motivos[x] = contagem_motivos.get(x, 0) + 1

    def _curto(m):
        return {k: m[k] for k in ("vs_equal_weight", "com_ir_vs_equal_weight",
                                  "bruto_vs_equal_weight", "vs_carteira_padrao",
                                  "decisao", "giro_medio_anual",
                                  "giro_venda_medio_anual", "custo_medio_anual",
                                  "safras") if k in m}

    saida = {
        "versao_medicao": fe.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "safras": medidas,
        "janela": (f"abril/{min(medidas)} a março/{max(medidas) + 1}"
                   if medidas else None),
        "regra_de_decisao": fe.REGRA_DE_DECISAO_DIVIDENDO_SEGURADO,
        "sob_a_regra": [rotulo],
        "regras": {
            "entrada": "a do dividendo de 6% da §23, sem mudança",
            "saida": ("a do líder segurado da §19: saiu do universo, falha na "
                      "trava de solvência ou prejuízo no último exercício"),
            "pesos": ("alvo 1/N; o aporte anual compra de baixo para cima; "
                      "ninguém é vendido por estar acima do alvo"),
            "aporte": f"{fe.APORTE_DIVIDENDO_SEGURADO:.0%} do patrimônio de abril",
            "formacao": "em abril (início da vigência)",
        },
        "metrica_principal": ("excesso médio LÍQUIDO sobre pesos iguais no universo, "
                              "retorno ponderado pelo tempo (o aporte não conta como ganho)"),
        "resultado": {
            "excesso_sobre_equal_weight": m_seg["vs_equal_weight"],
            "vs_carteira_padrao": m_seg["vs_carteira_padrao"],
            "testes": m_seg["decisao"]["testes"],
            "candidata": m_seg["decisao"]["candidata"],
            "giro_medio_anual": m_seg["giro_medio_anual"],
            "giro_venda_medio_anual": m_seg.get("giro_venda_medio_anual"),
        },
        "informativos": {
            "segurado_menos_dividendo_6": pareada(m_div, m_seg),
            "segurado_menos_dividendo_6_mesma_cadeia": pareada(m_div_aporte, m_seg),
            "segurado_menos_regua_19": pareada(m_regua19, m_seg),
            "so_solvencia_menos_segurado": pareada(m_seg, m_solv),
            "excesso_com_ir": m_seg["com_ir_vs_equal_weight"],
            "motivos_de_saida": contagem_motivos,
        },
        "composicao": composicao,
        "estrategias": {
            rotulo: _curto(m_seg),
            **{f"{rotulo}_{k}": _curto(m) for k, m in m_sens.items()},
            f"{rotulo}_regua_19": _curto(m_regua19),
            fe.DIVIDENDO_SEGURADO_SO_SOLVENCIA: _curto(m_solv),
            fe.DIVIDENDO_6: _curto(m_div),
            f"{fe.DIVIDENDO_6}_com_aporte": _curto(m_div_aporte),
        },
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "benchmarks": ("brutos e sem aporte (pesos iguais não paga custo e "
                           "volta a 1/N todo abril): viés contra a carteira"),
        },
        "fonte": {
            **fonte_dos_precos_da_aba(rc["precos"]),
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "limites": [
            "nove safras: diferenças pequenas não se separam do ruído",
            ("o dividendo é reinvestido no próprio nome (preço ajustado), não vai "
             "para os que estão abaixo do alvo"),
            "'saiu do universo' inclui cair abaixo do piso de tamanho ou de liquidez",
            ("a carteira padrão do teste (c) usa a régua da §19 (volta a 1/N todo "
             "abril, sem aporte)"),
            ("muitos braços medidos da §19 à §26 no mesmo período: um falso positivo "
             "fica mais provável do que o IC sugere"),
            "as §1 a §22 do guia ainda usam a leitura antiga dos fundamentos",
        ],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if dry_run:
        print(texto)
    else:
        CAMINHO_DIVIDENDO_SEGURADO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO_DIVIDENDO_SEGURADO}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="reaproveita a rodada da aba (o mesmo pickle de "
                         "medir_oos_carteira_b3.py --cache-dir)")
    ap.add_argument("--enxuta", action="store_true",
                    help="mede só a rota de valor completa e os braços top-10/15/20 "
                         "(grava data/oos_rota_valor_enxuta_b3.json)")
    ap.add_argument("--pequenas-dividendo", action="store_true",
                    help="mede as pequenas baratas e o dividendo de 6%% da §23 "
                         "(grava data/oos_pequenas_dividendo_b3.json)")
    ap.add_argument("--dividendo-outra-janela", action="store_true",
                    help="mede o dividendo de 6%% nas safras 2013 a 2016 da §24 "
                         "(grava data/oos_dividendo_outra_janela_b3.json)")
    ap.add_argument("--sobreposicao-rota", action="store_true",
                    help="mede a sobreposição do dividendo de 6%% com a rota de "
                         "valor da §25 (grava "
                         "data/oos_sobreposicao_dividendo_rota_b3.json)")
    ap.add_argument("--dividendo-segurado", action="store_true",
                    help="mede o dividendo de 6%% segurado, com equilíbrio pelo "
                         "aporte, da §26 (grava data/oos_dividendo_segurado_b3.json)")
    ap.add_argument("--solvencia-dois-anos", action="store_true",
                    help="mede a §27: a §26 com a falha de solvência exigida em dois "
                         "anos seguidos (grava data/oos_solvencia_dois_anos_b3.json)")
    args = ap.parse_args()

    rc = montar_recorte(args.cache_dir)
    if args.solvencia_dois_anos:
        return medir_solvencia_dois_anos(rc, dry_run=args.dry_run)
    if args.dividendo_segurado:
        return medir_dividendo_segurado(rc, dry_run=args.dry_run)
    if args.pequenas_dividendo:
        return medir_pequenas_dividendo(rc, args.cache_dir, dry_run=args.dry_run)
    if args.dividendo_outra_janela:
        return medir_dividendo_outra_janela(rc, dry_run=args.dry_run)
    if args.sobreposicao_rota:
        return medir_sobreposicao_rota(rc, dry_run=args.dry_run)
    oos, fe, nome, padrao = rc["oos"], rc["fe"], rc["nome"], rc["padrao"]
    medir, cadeia = rc["medir"], rc["cadeia"]
    lideres_por_safra, lider_segurado = rc["lideres_por_safra"], rc["lider_segurado"]
    saidas_lider = rc["saidas_lider"]

    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    if args.enxuta:
        # Só a Rota de valor limitada a N nomes (PR de 22/11/2026): braços
        # novos, mesma régua, outro JSON. A rota completa vem junto para a
        # comparação direta.
        rotulos = [fe.ROTA_VALOR] + [fe.rotulo_rota_topn(n) for n in fe.TAMANHOS_ROTA_ENXUTA]
    else:
        rotulos = [fe.ROTA_VALOR, fe.FORMULA_MAGICA, fe.QUALIDADE_CONSTANTE,
                   fe.QUALIDADE_3_ANOS, fe.LIDER_SEGURADO]
    escolhas = {k: rc["escolhas"][k] for k in rotulos}

    bracos = {}
    for rotulo, escolha in escolhas.items():
        m = medir(escolha)
        par = diferenca_pareada(padrao["safras"], m["safras"], "liquido")
        resumo_par = oos.resumir(par, "dif")
        m["vs_carteira_padrao"] = {**resumo_par, "safras": par}
        m["decisao"] = decidir(m, resumo_par)
        if args.enxuta and rotulo != fe.ROTA_VALOR:
            # Informativo, fora da regra de decisão: quanto o corte custa
            # frente à rota completa (pareado, líquido).
            pc = diferenca_pareada(bracos[fe.ROTA_VALOR]["safras"], m["safras"], "liquido")
            m["vs_rota_completa"] = {**oos.resumir(pc, "dif"), "safras": pc}
        bracos[rotulo] = m
        r = m["vs_equal_weight"]
        print(f"{rotulo}: media={r['media']} ic95={r['ic95']} n={r['n_safras']} "
              f"sem_melhor={r['media_sem_melhor_safra']} "
              f"vs_padrao={resumo_par['media']} candidata={m['decisao']['candidata']}",
              flush=True)

    rp = padrao["vs_equal_weight"]
    safras_medidas = sorted({t["safra"] for m in bracos.values() for t in m["safras"]})
    saida = {
        "versao_medicao": fe.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": nome,
        "safras": safras_medidas,
        "janela": (f"abril/{min(safras_medidas)} a março/{max(safras_medidas) + 1}"
                   if safras_medidas else None),
        "regra_de_decisao": fe.REGRA_DE_DECISAO,
        "regras": {
            fe.ROTA_VALOR: ("classificação 'oportunidade' da rota de valor "
                            "(core.b3_value_route) no retrato da safra, Selic de N-1"),
            fe.FORMULA_MAGICA: (
                f"soma dos postos de ROIC e 1/EV_EBIT; terço melhor, mínimo "
                f"{fe.MIN_NOMES}; sem financeiras; só EV_EBIT > 0"),
            fe.QUALIDADE_CONSTANTE: (
                f"ROIC acima da Selic do ano em ≥ {fe.FRACAO_ANOS_ACIMA:.0%} de "
                f"{fe.JANELA_ANOS} exercícios (mín. {fe.MIN_ANOS}); lucro N-1 > 0 e "
                f"> lucro de {fe.ANOS_CRESCIMENTO} anos antes; sem falha de solvência"),
            fe.QUALIDADE_3_ANOS: (
                f"a anterior refeita a cada {fe.ANOS_SEGURANDO} safras; no meio só sai "
                "quem quebra a solvência ou deixa o universo"),
            fe.LIDER_SEGURADO: (
                "carteira padrão da safra (Equilibrado, sem portão) mais os nomes "
                "das safras anteriores que não degradaram; degradar = deixar o "
                "universo, falhar na trava de solvência ou lucro de N-1 <= 0; "
                "dado ausente não degrada"),
            **{fe.rotulo_rota_topn(n): (
                f"a rota de valor limitada a {n} nomes: os {n} de maior valor_score "
                "da própria rota (desempate por margem e ticker); com menos de "
                f"{n} oportunidades, todas") for n in fe.TAMANHOS_ROTA_ENXUTA},
            "pesos": "iguais entre os escolhidos",
            "formacao": "em abril (início da vigência)",
        },
        "metrica_principal": (
            "excesso por safra (abril/N a março/N+1), LÍQUIDO do giro, sobre "
            "pesos iguais em todo o universo da safra"),
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "benchmarks": "brutos (pesos iguais não paga custo): viés contra a carteira",
        },
        "fonte": {
            **fonte_dos_precos_da_aba(rc["precos"]),
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
        },
        "limites": [
            "múltiplos anuais com o preço de fim do exercício N-1, não o de abril/N",
            "lucro da série de PL cortado por ano do exercício, sem data de publicação",
            "cinco braços: a chance de um falso positivo sobe para perto de 1 em 4",
            "nove safras não separam sorte de regra com folga; o teste (b) existe por isso",
            ("qualidade: ano sem Selic medida não conta na janela de ROIC; rota de "
             "valor: Selic de N-1 ou a última antes dela (média da série só se "
             "não houver nenhuma)"),
            ("líder segurado: os pesos voltam a ser iguais em abril, então o "
             "vencedor antigo é aparado todo ano; a tela compra só com o aporte "
             "e não apara. A carteira cresce com os anos, e mais nomes a "
             "aproximam dos pesos iguais no universo"),
        ],
        "fora_da_regra": [
            "guarda de entrada e piso de qualidade não filtram estas carteiras",
            "portão de LLM não avalia estas carteiras",
            "tetos de setor e de ciclo do perfil não se aplicam",
        ],
        "estrategias": bracos,
        "carteira_padrao": {
            "vs_equal_weight": rp,
            "fonte": "scripts/medir_oos_carteira_b3.py, mesma rodada, sem portão",
        },
        "candidatas": [k for k, m in bracos.items() if m["decisao"]["candidata"]],
        "lider_segurado_por_safra": {
            str(s): {"lideres": lideres_por_safra[s],
                     "carteira": lider_segurado.get(s, []),
                     "saidas": saidas_lider.get(s, {})}
            for s in cadeia},
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if args.dry_run:
        print(texto)
    else:
        destino = CAMINHO_ENXUTA if args.enxuta else CAMINHO
        destino.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
