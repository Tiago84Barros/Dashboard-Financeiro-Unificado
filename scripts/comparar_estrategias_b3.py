"""
scripts/comparar_estrategias_b3.py — §29: todas as estratégias B3 na mesma
rodada, com retorno, renda de dividendos e valorização.

A régua (renda e valorização pelo pregão diário, trajetória de renda e a
regra R1/R2/R3) mora em `core/b3_comparacao_estrategias.py` e foi registrada antes
desta medição. As estratégias são as das §18–§28, montadas pelas mesmas
funções das medições originais (`montar_recorte` e companhia), sobre a mesma
rodada da aba (perfil Equilibrado).

Uso:
    python scripts/comparar_estrategias_b3.py --cache-dir <pasta> [--dry-run]

Grava `data/oos_comparacao_estrategias_b3.json`. A pasta do cache tem de ser
nova ou montada depois da última mudança de preços no armazém: a conferência
dos preços (`fonte_dos_precos_da_aba`) para a medição se o pickle não bate
com `market.historical_prices`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_comparacao_estrategias_b3.json"

LIDER = "lider_segmento"
NOTA_FATORES = "nota_fatores"
TIRAR_30 = "tirar_30_pela_nota"
LIDER_FILTRO = "lider_filtro_nota_fatores"
ROTA_MAIS_DIVIDENDO = "rota_mais_dividendo"
# Padrão da aba desde 26/11/2026, por escolha do usuário (§29). A regra pode
# apontar outra estratégia: a escolha dela vai em ``escolha`` e a do usuário em
# ``decisao_do_usuario``, lado a lado, para a tela dizer as duas.
ROTA_VALOR = "rota_valor"  # = core.b3_estrategias_fundamentalistas.ROTA_VALOR
PADRAO_ATUAL = ROTA_VALOR
DECISAO_DO_USUARIO = {
    "padrao": ROTA_VALOR,
    "em": "2026-11-26",
    "confirmada_em": "2026-11-27",
    "motivo": ("na régua 1.2.1 a regra escolhia a rota_valor_top20 e o usuário ficou "
               "com a rota inteira; na 1.3.0 (renda pelo pregão diário) a rota falha só "
               "a R2, por uma queda de renda a mais que pesos iguais (2024, cerca de "
               "1%), e a regra escolheria nota_fatores; o usuário manteve a rota, "
               "fora da regra, sem mudar a regra"),
}


def pontas_de_preco(tickers, safras):
    """{(ticker, safra): {"fator", "caixa", "preco"} | None} do pregão diário.

    Régua 1.3.0: o pregão bruto do COTAHIST (`market.b3_security_history`,
    os BDIs de ação do motor, `cr.BDIS_ACOES`: o lote padrão e os papéis em
    recuperação ou sob regime especial, que a rota de valor compra; o
    universo inteiro, que o mercado mediano do motor pede) e os
    proventos por data com passam pelo motor da Carteira 20
    (`core.carteira20_retornos.calcular(componentes=True)`), que devolve por
    dia o fechamento, as ações novas por antiga e o caixa por ação. Os
    proventos são os de `core.proventos_b3` (foto mais nova da brapi e, nos
    anos sem foto, o banco), com o provento da foto multiplicado de volta
    pelos desdobramentos, grupamentos e bonificações que a própria foto lista
    (`ce.eventos_de_acoes`, `ce.fator_de_desajuste`): a foto vem ajustada e o
    pregão é bruto. O código segue as trocas de `market.ticker_alias` e de
    `core.b3_troca_codigo.PARES_CONHECIDOS` (`mapa_de_trocas`): o COTAHIST
    guarda o passado no código antigo, a carteira usa o atual.

    Para cada safra, a série do papel vai da ponta inicial (último pregão até
    a véspera da vigência) à final (último pregão até o fim dela), as duas
    com a tolerância da régua; `ce.rendimentos_do_pregao` tira dela o fator,
    o caixa e a valorização só pelo preço.
    """
    import pandas as pd
    from sqlalchemy import text

    import core.b3_comparacao_estrategias as ce
    import core.carteira20_retornos as cr
    import core.proventos_b3 as pb
    from core.b3_troca_codigo import mapa_de_trocas
    from core.b3_vigencia import janela_de_vigencia
    from core.database import get_engine
    from core.dividend_types import renda_por_evento, sql_proventos_de_renda

    tickers = sorted({str(t).upper() for t in tickers})
    inicio0 = janela_de_vigencia(min(safras))[0]
    with get_engine().connect() as con:
        pg = pd.read_sql(text(
            "SELECT ticker, trade_date, bdi, specification, close::float AS close, "
            "fator_cotacao::float AS fator_cotacao FROM market.b3_security_history "
            "WHERE bdi = ANY(:bdis) AND trade_date >= :i AND close > 0"),
            con, params={"i": (inicio0 - pd.Timedelta(days=180)).date(),
                         "bdis": list(cr.BDIS_ACOES)})
        aliases = con.execute(text(
            "SELECT brapi_symbol, b3_ticker, motivo FROM market.ticker_alias")).fetchall()
        fotos = con.execute(text(pb.SQL_FOTOS)).fetchall()
        bruto = pd.read_sql_query(text(sql_proventos_de_renda("TRUE")), con)

    pg["ticker"] = pg["ticker"].astype(str).str.strip().str.upper()
    mapa = mapa_de_trocas(None, aliases)
    ids = {t: mapa.get(t, t) for t in set(pg["ticker"]) | set(mapa)}

    # Proventos: a foto (desajustada pelos eventos dela), os anos que ela não
    # tem e os papéis sem foto (do banco, como estão).
    eventos = {str(tk).strip().upper(): ce.eventos_de_acoes(dd) for tk, _, _, dd in fotos}
    banco = renda_por_evento(bruto)
    foto = pb.eventos_das_fotos(fotos).drop(columns="foto_em")
    cobertos = set(eventos)
    preenche = pb.anos_que_a_foto_nao_tem(foto, banco[banco["ticker"].isin(cobertos)])
    resto = banco[~banco["ticker"].isin(cobertos)]
    foto = foto.assign(amount=pd.to_numeric(foto["amount"], errors="coerce") * [
        ce.fator_de_desajuste(eventos.get(str(t).strip().upper()), d)
        for t, d in zip(foto["ticker"], pd.to_datetime(foto["ex_date"], errors="coerce"))])
    proventos = pd.concat([foto, preenche, resto], ignore_index=True)

    diario = cr.calcular(cr.pregoes_de_acoes(pg.to_dict("records"), set(ids)), proventos,
                         ids, (inicio0 - pd.Timedelta(days=90)).date(), componentes=True)
    diario["data"] = pd.to_datetime(diario["data"])
    diario = diario.sort_values(["id", "data"])
    por_id = {i: g for i, g in diario.groupby("id")}
    tol = pd.Timedelta(days=ce.TOLERANCIA_PONTA_DIAS)

    def ponta(g, data):
        h = g[(g["data"] <= data) & (g["data"] >= data - tol)]
        return None if h.empty else h["data"].iloc[-1]

    saida = {}
    for s in safras:
        inicio, fim_s = janela_de_vigencia(s)
        vespera = inicio - pd.Timedelta(days=1)
        for tk in tickers:
            g = por_id.get(ids.get(tk, tk))
            d0 = None if g is None else ponta(g, vespera)
            d1 = None if g is None else ponta(g, fim_s)
            if d0 is None or d1 is None or d1 <= d0:
                saida[(tk, s)] = None
                continue
            h = g[(g["data"] >= d0) & (g["data"] <= d1)]
            saida[(tk, s)] = ce.rendimentos_do_pregao(
                zip(h["preco"], h["ret"], h["k"], h["caixa"]))
    return saida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="a rodada da aba (o mesmo pickle de medir_oos_carteira_b3.py)")
    args = ap.parse_args()

    import core.b3_comparacao_estrategias as ce
    from scripts.medir_estrategias_fundamentalistas_b3 import (
        _cadeia_do_dividendo_segurado,
        diferenca_pareada,
        fonte_dos_precos_da_aba,
        montar_recorte,
    )
    from scripts.medir_lider_filtro_nota_fatores_b3 import aplicar_filtro_na_safra
    from scripts.medir_nota_fatores_b3 import _tirar_piores_pela_nota

    rc = montar_recorte(args.cache_dir)
    fonte = fonte_dos_precos_da_aba(rc["precos"])
    M, oos, fe, prep = rc["M"], rc["oos"], rc["fe"], rc["prep"]
    nf = prep["nf"]
    resultados, precos, params, decisao = prep["rodada"]
    ibov, cost_cfg, hoje = prep["ibov"], prep["cost_cfg"], prep["hoje"]
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from core.b3_quality_floor import reprovados
    from core.b3_vigencia import janela_de_vigencia
    from scripts.medir_vantagem_oos import _fonte_efetiva_b3

    medir, comeca_em = rc["medir"], rc["comeca_em"]
    por_ano = {int(p["safra"]): p for p in rc["por_safra"]}
    contexto, _ = M._segmentos_por_safra(resultados, precos, params, decisao, cost_cfg, hoje)
    tetos = {"cap": contexto["cap"], "teto_setor": params["teto_setor"],
             "teto_ciclico": params["teto_ciclico"]}

    def pesos_lider(p):
        itens, _ = oos.vetar_na_carteira(p["segmentos"], None, p["exclui"])
        cart = oos.montar_carteira(itens, **tetos, exclui=p["exclui"])
        return {str(t).upper(): float(w) for t, w in (cart.get("pesos") or {}).items()
                if w and w > 0}

    def iguais(nomes):
        nomes = [str(t).upper() for t in nomes]
        return {t: 1.0 / len(nomes) for t in nomes} if nomes else {}

    # --- composições (pesos por safra) e medições de retorno ----------------
    bracos: dict[str, dict] = {}
    pesos: dict[str, dict[int, dict[str, float]]] = {}

    def registrar(rotulo, medido, pesos_por_safra):
        bracos[rotulo] = medido
        pesos[rotulo] = pesos_por_safra

    # Líder de cada segmento: a carteira padrão das medições (sem portão).
    registrar(LIDER, {"vs_equal_weight": rc["padrao"]["vs_equal_weight"],
                      "safras": rc["padrao"]["safras"]},
              {s: pesos_lider(p) for s, p in por_ano.items()})

    # Nota + fatores (padrão da tela) e o corte de 30% só pela nota.
    res_ok = [r for r in resultados if r.get("_oos_ctx")]
    hist_batch = decisao.get("hist_batch") or {}
    pm = nf.precos_mensais(precos)

    def scores_da_safra(safra):
        sc: dict[str, float] = {}
        for res in res_ok:
            sc.update(oos._scores_do_ano(res, safra))
        return sc

    formadas = {s: nf.carteira_nota_fatores(p["universo"], scores_da_safra(s), precos,
                                            hist_batch, janela_de_vigencia(s)[0],
                                            precos_mes=pm)
                for s, p in por_ano.items()}
    cortadas = {s: list(_tirar_piores_pela_nota(p["universo"], scores_da_safra(s), nf.CORTE))
                for s, p in por_ano.items()}
    memo: dict = {}
    registrar(NOTA_FATORES,
              medir(lambda p: list(formadas[int(p["safra"])]["pesos"]), memo),
              {s: iguais(f["pesos"]) for s, f in formadas.items()})
    registrar(TIRAR_30, medir(lambda p: cortadas[int(p["safra"])], memo),
              {s: iguais(v) for s, v in cortadas.items()})

    # Líder com o filtro da nota + fatores (§18), régua da líder.
    original = M._segmentos_por_safra
    filtradas: dict[int, dict] = {}

    def com_filtro(*a, **k):
        ctx, ps = original(*a, **k)
        saida = []
        for p in ps:
            s = int(p["safra"])
            tabela = formadas[s]["tabela"] if s in formadas else nf.carteira_nota_fatores(
                p["universo"], scores_da_safra(s), precos, hist_batch,
                janela_de_vigencia(s)[0], precos_mes=pm)["tabela"]
            reprova = frozenset()
            if params["piso_qualidade"]:
                selic_n = float(ctx["selic_macro"].get(s - 1, ctx["taxa_selic_aa"]))
                candidatos = {str(t) for _s, _sel, _p, rk in p["segmentos"] for t, _sc in rk}
                reprova = reprovados(p["df_pit"], candidatos, selic=selic_n)
            q = aplicar_filtro_na_safra(p, nf.piores_da_tabela(tabela), nf.trocar_piores,
                                        reprovado_no_piso=reprova.__contains__)
            filtradas[s] = q
            saida.append(q)
        return ctx, saida

    M._segmentos_por_safra = com_filtro
    try:
        filtrado, _ = M._medir_perfil(resultados, precos, params, decisao, ibov,
                                      cost_cfg, hoje, comeca_em=comeca_em)
    finally:
        M._segmentos_por_safra = original
    f = filtrado["variantes"][oos.SEM_PORTAO]
    registrar(LIDER_FILTRO, {"vs_equal_weight": f["vs_equal_weight"], "safras": f["safras"]},
              {s: pesos_lider(q) for s, q in filtradas.items()})

    # §19, §20, §23, §28: escolhas de pesos iguais do recorte.
    rotulos = [fe.ROTA_VALOR, fe.FORMULA_MAGICA, fe.QUALIDADE_CONSTANTE,
               fe.QUALIDADE_3_ANOS, fe.LIDER_SEGURADO,
               *[fe.rotulo_rota_topn(n) for n in fe.TAMANHOS_ROTA_ENXUTA],
               fe.DIVIDENDO_6,
               *[fe.rotulo_dividendo_topn(n) for n in fe.TAMANHOS_DIVIDENDO_TOPN]]
    for rotulo in rotulos:
        escolher = rc["escolhas"][rotulo]
        comp = {s: list(escolher(p)) for s, p in por_ano.items()}
        registrar(rotulo, medir(lambda p, c=comp: c[int(p["safra"])], memo),
                  {s: iguais(v) for s, v in comp.items()})
        print(f"montado {rotulo}", flush=True)

    # §26/§27: o dividendo segurado e variantes, cadeia com aporte.
    _entrada, segurar = _cadeia_do_dividendo_segurado(rc)

    def aporte(itens, cfg):
        return fe.simular_aportes(itens, cfg, aporte=fe.APORTE_DIVIDENDO_SEGURADO)

    for rotulo, (cart, _sai) in (
            (fe.DIVIDENDO_SEGURADO, segurar(fe.MOTIVOS_DIVIDENDO_SEGURADO)),
            (fe.DIVIDENDO_SEGURADO_SO_SOLVENCIA, segurar(fe.MOTIVOS_SO_SOLVENCIA)),
            (fe.DIVIDENDO_SEGURADO_DOIS_ANOS,
             segurar(fe.MOTIVOS_DIVIDENDO_SEGURADO, fe.PERSISTENTES_DOIS_ANOS))):
        registrar(rotulo, medir(lambda p, c=cart: c.get(int(p["safra"]), []), memo,
                                simular=aporte),
                  {s: iguais(cart.get(s, [])) for s in por_ano})

    # §25: metade rota de valor, metade dividendo de 6%.
    a_r = {int(t["safra"]): t for t in bracos[fe.ROTA_VALOR]["safras"]}
    linhas = []
    for t in bracos[fe.DIVIDENDO_6]["safras"]:
        s = int(t["safra"])
        if s not in a_r:
            continue
        liq = 0.5 * (float(t["liquido"]) + float(a_r[s]["liquido"]))
        ir = 0.5 * (float(t["liquido_com_ir"]) + float(a_r[s]["liquido_com_ir"]))
        ew = float(t["equal_weight"])
        bru = 0.5 * (float(t["bruto"]) + float(a_r[s]["bruto"]))
        linhas.append({"safra": s, "liquido": liq, "bruto": bru, "liquido_com_ir": ir,
                       "equal_weight": ew, "excesso_ew": liq - ew,
                       "excesso_ew_com_ir": ir - ew})
    meio = {}
    for s in por_ano:
        pr, pd_ = pesos[fe.ROTA_VALOR].get(s, {}), pesos[fe.DIVIDENDO_6].get(s, {})
        if pr and pd_:
            meio[s] = {t: 0.5 * pr.get(t, 0) + 0.5 * pd_.get(t, 0) for t in set(pr) | set(pd_)}
        else:
            meio[s] = pr or pd_
    registrar(ROTA_MAIS_DIVIDENDO,
              {"vs_equal_weight": oos.resumir(linhas, "excesso_ew"),
               "com_ir_vs_equal_weight": oos.resumir(linhas, "excesso_ew_com_ir"),
               "safras": linhas}, meio)

    # --- renda ---------------------------------------------------------------
    # As safras medidas pela régua `medir_escolha` (a partir de `comeca_em`).
    # A líder (`_medir_perfil`) traz também as safras sem carteira antes
    # disso, sem `liquido`: ficam fora da trajetória e das pareadas.
    base_ew = {int(t["safra"]): float(t["equal_weight"])
               for t in bracos[NOTA_FATORES]["safras"]}
    safras = sorted(base_ew)
    todos = {t for d in pesos.values() for ps in d.values() for t in ps}
    for s in safras:
        todos |= {str(t).upper() for t in por_ano[s]["universo"]}
    rend = pontas_de_preco(todos, safras)

    rend_por_safra = {s: {tk: v for (tk, ss), v in rend.items() if ss == s}
                      for s in safras}
    ew_linhas, ew_cob = [], {}
    for s in safras:
        uni = list(dict.fromkeys(str(t).upper() for t in por_ano[s]["universo"]))
        lin = ce.linha_de_renda(s, {t: 1.0 for t in uni}, rend_por_safra[s],
                                liquido=base_ew[s])
        ew_cob[str(s)] = lin.pop("cobertura")
        ew_linhas.append(lin)
    renda_ew = ce.trajetoria_de_renda(ew_linhas)

    def pareada(a, b):
        par = diferenca_pareada(bracos[a]["safras"], bracos[b]["safras"], "liquido")
        return {**oos.resumir(par, "dif"), "safras": par}

    tabela = {}
    for rotulo, m in bracos.items():
        lin, cob, nomes = [], {}, {}
        for t in m["safras"]:
            s = int(t["safra"])
            if s not in base_ew or t.get("liquido") is None:
                continue
            ps = pesos[rotulo].get(s, {})
            linha = ce.linha_de_renda(s, ps, rend_por_safra.get(s, {}),
                                      liquido=t["liquido"], bruto=t.get("bruto"))
            cob[str(s)] = linha.pop("cobertura")
            nomes[str(s)] = len(ps)
            lin.append(linha)
        renda = ce.trajetoria_de_renda(lin)
        vs = m["vs_equal_weight"]
        tabela[rotulo] = {
            "vs_equal_weight": vs,
            "com_ir_vs_equal_weight": m.get("com_ir_vs_equal_weight"),
            "giro_medio_anual": m.get("giro_medio_anual"),
            "renda": renda,
            "cobertura_do_rendimento": cob,
            "nomes_por_safra": nomes,
            "decisao": ce.decidir_padrao(vs, renda, renda_ew),
            "vs_nota_fatores": pareada(NOTA_FATORES, rotulo) if rotulo != NOTA_FATORES else None,
            "vs_lider": pareada(LIDER, rotulo) if rotulo != LIDER else None,
        }
        r = tabela[rotulo]
        print(f"{rotulo}: media={vs['media']:+.4f} ic95={vs['ic95']} "
              f"sem_melhor={vs['media_sem_melhor_safra']} "
              f"renda_aa={renda['crescimento_renda_aa']} quedas={renda['quedas_de_renda']} "
              f"cap={renda['capital_final']} renda_tot={renda['renda_total']} "
              f"insuficiente={renda['insuficiente']} "
              f"falhou={r['decisao']['falhou']}", flush=True)
    print(f"pesos_iguais: renda_aa={renda_ew['crescimento_renda_aa']} "
          f"quedas={renda_ew['quedas_de_renda']} cap={renda_ew['capital_final']} "
          f"renda_tot={renda_ew['renda_total']} "
          f"insuficiente={renda_ew['insuficiente']}", flush=True)

    escolha = ce.escolher_padrao(tabela, PADRAO_ATUAL)
    print(f"padrao: {escolha}", flush=True)

    saida = {
        "versao_regua": ce.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "safras": safras,
        "janela": f"abril/{min(safras)} a março/{max(safras) + 1}",
        "regra": {
            "r1_retorno": ("excesso líquido médio sobre pesos iguais > 0, IC 95% acima "
                           "de zero, média sem a melhor safra > 0"),
            "r2_dividendo_crescente": ("tendência da renda >= a dos pesos iguais e anos "
                                       "de queda <= os dos pesos iguais"),
            "r3_valorizacao": "capital final (só preço) >= o dos pesos iguais",
            "escolha": ("candidata = R1, R2 e R3; vira padrão a de maior excesso médio; "
                        f"sem candidata fica {PADRAO_ATUAL}"),
            "capital_inicial": ce.CAPITAL_INICIAL,
            "fonte_da_renda": ("pregão diário bruto do COTAHIST (market.b3_security_history, "
                               "BDIs de ação do motor) e proventos por data com (foto mais nova da brapi, "
                               "multiplicada de volta pelos desdobramentos, grupamentos e "
                               "bonificações que a própria foto lista; banco nos anos sem "
                               "foto), pelo motor da Carteira 20 "
                               "(core.carteira20_retornos, componentes=True); código pelas "
                               "trocas de market.ticker_alias e de PARES_CONHECIDOS"),
            "rendimento": ("fator dos proventos reinvestidos, (1 + retorno total do "
                           "pregão diário) / (1 + valorização) - 1, da véspera ao fim da "
                           f"vigência, tolerância {ce.TOLERANCIA_PONTA_DIAS} dias, válido "
                           f"em [{ce.RENDIMENTO_MIN}, {ce.RENDIMENTO_MAX})"),
            "renda_caixa": ("provento recebido por quem não reinveste, em fração do "
                            "capital do início: soma de n x caixa do dia / P_0, com n as "
                            "ações acumuladas pelos desdobramentos e bonificações; válido "
                            f"em [{ce.RENDIMENTO_MIN}, {ce.RENDIMENTO_MAX})"),
            "valorizacao": ("valorização só pelo preço, n x P_fim / P_0 - 1 do pregão "
                            "diário de cada papel, ponderada papel a papel depois"),
            "trajetoria": ("investido = capital x (1 + líquido) / (1 + bruto), o custo "
                           "de abril; renda = investido x renda_caixa; capital seguinte "
                           "= investido x (1 + valorização); safra sem rendimento deixa "
                           "a trajetória insuficiente (R2 e R3 reprovam)"),
        },
        "fonte": {**fonte,
                  "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}"},
        "pesos_iguais": {"renda": renda_ew, "cobertura_do_rendimento": ew_cob},
        "estrategias": tabela,
        "escolha": escolha,
        "decisao_do_usuario": DECISAO_DO_USUARIO,
        "limites": [
            "nove safras; com ~20 estratégias, alguma passar R1 por sorte é provável",
            "estratégias sobrepostas: não são testes independentes",
            "com aporte (§26/§27) o rendimento usa pesos iguais na composição",
            "pesos iguais (benchmark) é bruto de custo: viés contra as estratégias",
            "pequenas baratas e os perfis Conservador e Amplo não foram refeitos",
        ],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if args.dry_run:
        print(texto[:4000])
    else:
        CAMINHO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
