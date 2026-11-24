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

    def medir(escolha, memo=None):
        return medir_escolha(por_safra, escolha, precos=precos, selic_macro=selic_macro,
                             taxa=taxa, selic_mensal=selic_mensal, cost_cfg=cost_cfg,
                             comeca_em=comeca_em, memo=memo)

    return {
        "prep": prep, "M": M, "oos": oos, "fe": fe, "nome": nome,
        "ibov": ibov, "precos": precos, "padrao": padrao, "comeca_em": comeca_em,
        "por_safra": por_safra, "cadeia": cadeia, "escolhas": escolhas,
        "medir": medir, "lideres_padrao": lideres_padrao,
        "selic_da_safra": selic_da_safra,
        "lideres_por_safra": lideres_por_safra, "lider_segurado": lider_segurado,
        "saidas_lider": saidas_lider,
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
            "precos": "os da aba (mensal ajustado do armazém local)",
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
    args = ap.parse_args()

    rc = montar_recorte(args.cache_dir)
    if args.pequenas_dividendo:
        return medir_pequenas_dividendo(rc, args.cache_dir, dry_run=args.dry_run)
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
            "precos": "os da aba (mensal ajustado do armazém local)",
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
