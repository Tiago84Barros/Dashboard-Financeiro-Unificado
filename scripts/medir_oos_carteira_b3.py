# -*- coding: utf-8 -*-
"""Mede o fora da amostra da CARTEIRA montada da B3, por perfil (B3-03/B3-09).

`data/vantagem_oos.json` responde "o score ordena?" (Rank-IC do ranking, bruto,
sem portões). Este script responde a pergunta que o investidor faz: a carteira
que a aba entregaria a cada perfil, safra a safra, teria batido dividir
igualmente entre as empresas -- depois de pagar o giro?

Como no `medir_vantagem_oos.py`, a medição roda o MESMO código da tela: a aba
de Criação de Portfólio B3 é executada sem cabeça (`AppTest`) uma vez por
perfil, e o backtest por segmento que ela monta é a matéria-prima. A parte
point-in-time (aprovar o segmento só com o que se sabia em março/N, montar a
carteira com os tetos do perfil, descontar custo) mora em
`core/b3_oos_carteira.py`, pura e testada.

Os três perfis rodam no MESMO processo: os fundamentos vêm do banco que
`core.b3_db` resolver (hoje o publicado), e o `st.cache_data` compartilhado faz
a segunda e a terceira rodada não lerem de novo -- o Supabase está com egress
acima da cota (restrição em 14/10/2026).

Uso::

    python scripts/medir_oos_carteira_b3.py            # mede e grava
    python scripts/medir_oos_carteira_b3.py --dry-run  # mede e imprime

O armazém local precisa estar de pé (`docker ps --filter name=dfu_warehouse`):
preços saem de lá. Nada é gravado em banco nenhum -- só o JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
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

from scripts.medir_vantagem_oos import (  # noqa: E402
    _apontar_para_armazem_local,
    _desligar_llm,
    _fonte_efetiva_b3,
)

_SCRIPT_B3 = """
import views.portfolio_b3 as view
view.render(show_header=False)
"""

# Mapas da tela (views/portfolio_b3.py, expander de parâmetros). Duplicados
# aqui só para ler a configuração da rodada; se a tela mudar o rótulo, o
# `KeyError` derruba a medição em vez de gravar parâmetro errado.
_JANELA_ANOS = {"~24 meses": 2, "~36 meses": 3}

#: O histórico saneado de múltiplos que a tela passou aos segmentos da rodada
#: corrente (o mesmo objeto em todos). Fica fora de cada resultado para o
#: pickle do --cache-dir não repeti-lo por segmento.
_DECISAO: dict = {}


def _instrumentar_view(view) -> None:
    """Guarda em cada resultado os argumentos com que a tela o calculou.

    `_processar_segmento` devolve o backtest pronto, mas a medição PIT precisa
    refazê-lo cortado em cada março -- e para isso precisa do universo de
    entrada, do ROIC por exercício e da Selic que a tela usou. Embrulhar a
    função (e não reimplementá-la) mantém um só caminho de cálculo.
    """
    original = getattr(view._processar_segmento, "__wrapped_oos__", None) \
        or view._processar_segmento
    assinatura = inspect.signature(original)

    def _embrulho(*args, **kwargs):
        res = original(*args, **kwargs)
        if not res:
            return res
        b = assinatura.bind(*args, **kwargs)
        b.apply_defaults()
        a = b.arguments
        import pandas as pd
        roic: dict[str, list[tuple[int, float]]] = {}
        for tk in a["tickers"]:
            df_h = (a["hist_batch"] or {}).get(tk)
            if df_h is None or df_h.empty or "ROIC" not in df_h.columns \
                    or "Data" not in df_h.columns:
                continue
            anos = pd.to_datetime(df_h["Data"], errors="coerce").dt.year
            valores = pd.to_numeric(df_h["ROIC"], errors="coerce")
            roic[tk] = [(int(y), float(v)) for y, v in zip(anos, valores)
                        if pd.notna(y) and pd.notna(v)]
        _DECISAO["hist_batch"] = a["hist_batch"]
        res["_oos_ctx"] = {
            "tickers_entrada": list(a["tickers"]),
            "roic": roic,
            "aporte": float(a["aporte"]),
            "ano_inicio": int(a["ano_inicio"]),
            "taxa_selic_aa": float(a["taxa_selic_aa"]),
            "selic_macro": {int(k): float(v) for k, v in (a["selic_macro"] or {}).items()},
            "cap": float(a["cap"]), "gamma": float(a["gamma"]), "soft": float(a["soft"]),
            "janela_val_anos": int(a["janela_val_anos"]),
            "walk_forward": bool(a["walk_forward"]),
        }
        return res

    _embrulho.__wrapped_oos__ = original
    view._processar_segmento = _embrulho


def _rodar_perfil(nome: str, valores: dict, timeout: int = 1800):
    from streamlit.testing.v1 import AppTest

    from core.b3_portfolio_presets import identificar_perfil

    _DECISAO.clear()
    app = AppTest.from_string(_SCRIPT_B3)
    for chave, valor in valores.items():
        app.session_state[chave] = valor
    app.run(timeout=timeout)
    lidos = {k: app.session_state[k] for k in valores}
    perfil = identificar_perfil(lidos)
    if perfil != nome:
        raise SystemExit(f"perfil {nome!r} nao foi aplicado (a tela leu {perfil!r})")
    app.button(key="pb3_rodar").click().run(timeout=timeout)
    if app.exception:
        raise SystemExit(f"aba da B3 lancou excecao em {nome}: {app.exception[0].value}")
    try:
        resultados = list(app.session_state["pb3_resultados"] or [])
        precos = app.session_state["pb3_precos_all"]
    except (KeyError, AttributeError) as exc:
        raise SystemExit(f"aba da B3 nao concluiu em {nome}: nada gravado") from exc

    def _ss(chave, padrao):
        try:
            return app.session_state[chave]
        except KeyError:
            return padrao

    janela_label = _ss("pb3_janela_val", "~24 meses")
    if "Walk-forward" in str(janela_label):
        raise SystemExit("medição PIT não implementa a validação walk-forward")
    criterio = str(_ss("pb3_criterio_aprov2", "Econômico (Brasil)"))
    params = {
        "criterio_modo": "economico" if criterio.startswith("Econômico") else criterio,
        "thr_selic": float(_ss("pb3_thr_selic_hist", 0.0)),
        "thr_ew": float(_ss("pb3_thr_ew_oos24", 0.0)),
        "usar_ew": _ss("pb3_ew_floor_mode2", "") == "Exigir margem mínima",
        "max_anos_lid": int(_ss("pb3_max_anos", 5)),
        "exigir_resiliencia": bool(_ss("pb3_resiliencia", False)),
        "thr_roic_spread": float(_ss("pb3_roic_spread", 0.0)) / 100.0,
        "exigir_vantagem_selecao": bool(_ss("pb3_exigir_vantagem_sel", False)),
        "teto_setor": float(_ss("pb3_teto_setor", 100)) / 100.0,
        "teto_ciclico": float(_ss("pb3_teto_ciclico", 100)) / 100.0,
        "janela_val_anos": _JANELA_ANOS[str(janela_label)],
        "piso_qualidade": bool(_ss("pb3_piso_qualidade", True)),
    }
    classificacao = _ss("pb3_classificacao_hist", None)
    if classificacao is None or not _DECISAO.get("hist_batch"):
        raise SystemExit(f"aba da B3 nao expôs o insumo da guarda PIT em {nome}")
    decisao = {
        "hist_batch": _DECISAO["hist_batch"],
        "classificacao": classificacao,
        "anos_hist": dict(_ss("pb3_anos_hist_rec", {}) or {}),
    }
    return resultados, precos, params, decisao


def _portoes_da_safra(view, decisao: dict, universo: list[str], safra: int,
                      series_pl: dict):
    """O retrato e a guarda de entrada que a decisão da safra teria lido.

    Mesma função da tela (`_build_entry_guard`), agrupada pelo segmento real,
    sobre o universo elegível da safra -- mas com os múltiplos cortados em
    N-1 pela regra de vintage do motor de score e a contagem de exercícios
    sem os posteriores. Devolve (retrato enriquecido, guarda).
    """
    import pandas as pd

    import views.empresas_b3 as emp
    from core.b3_retrato_pit import anos_hist_ate, historico_ate, quadro_decisao_pit
    from core.b3_vigencia import REBAL_MONTH, ano_base_do_score

    ano_max = ano_base_do_score(safra)
    decisao_em = pd.Timestamp(int(safra), int(REBAL_MONTH), 1)

    def aceita(recorte):
        return emp._classificar_disponibilidade_pit(recorte, decisao_em)[1]

    alvos = set(universo)
    hist = {tk: df for tk, df in (decisao["hist_batch"] or {}).items() if tk in alvos}
    df_pit = quadro_decisao_pit(hist, series_pl, ano_max, aceita)
    cls = decisao["classificacao"]
    cls = cls[cls["ticker"].astype(str).isin(alvos)]
    guarda, _ = view._build_entry_guard(
        df_pit, cls, historico_ate(hist, ano_max, aceita),
        anos_hist_ate(decisao["anos_hist"], hist, ano_max) or None)
    return df_pit, guarda


def _segmentos_por_safra(resultados, precos, params, decisao, cost_cfg, hoje):
    """Aprovação PIT e seleção de cada segmento, safra a safra.

    Devolve ``(contexto, safras)``: o contexto da rodada (cap e Selic que a tela
    usou) e, por safra, os segmentos aprovados no formato de
    ``core.b3_oos_carteira.Segmento``, o universo e as saídas. O portão de
    LLM medido (`core.b3_portao_pit`) lê o mesmo: os nomes que a carteira
    mediria são os que o LLM avalia. Cada safra leva também a guarda de
    entrada da época (``exclui``) e o que o piso fez (2.32.0).
    """
    import pandas as pd

    import core.b3_oos_carteira as oos
    import views.portfolio_b3 as view
    from core.b3_vigencia import safra_completa

    resultados = [r for r in resultados if r.get("_oos_ctx")]
    if not resultados:
        raise SystemExit("nenhum resultado instrumentado: o embrulho nao pegou")
    ctx0 = resultados[0]["_oos_ctx"]
    contexto = {
        "cap": float(ctx0["cap"]),
        "selic_macro": ctx0["selic_macro"],
        "taxa_selic_aa": float(ctx0["taxa_selic_aa"]),
        "n_resultados": len(resultados),
    }
    primeira = int(ctx0["ano_inicio"]) + int(params["janela_val_anos"])
    anos = sorted({int(a) for r in resultados for a in (r.get("lids_por_ano") or {})})
    safras = [s for s in anos if s >= primeira and safra_completa(s, hoje=hoje)]

    from core.b3_retrato_pit import MAX_ANOS_PL
    from core.dossie_b3 import load_pl_lucro_anual_batch

    # Série inteira de PL e lucro, uma leitura por perfil; cada safra corta a
    # sua em N-1 e só então aplica o teto de anos do loader.
    series_pl = load_pl_lucro_anual_batch(
        tuple(sorted(decisao["hist_batch"])), max_anos=10 * MAX_ANOS_PL)

    saida = []
    for safra in safras:
        aprovados = []
        motivos: dict[str, int] = {}
        for res in resultados:
            m = oos.metricas_pit(res, safra, precos,
                                 simular=view._simular_seg_backtest, cost_cfg=cost_cfg)
            ok, motivo = oos.aprova_economico(m, params, safra)
            if ok:
                aprovados.append((res, m))
            else:
                motivos[motivo] = motivos.get(motivo, 0) + 1

        universo: list[str] = []
        saidas: dict = {}
        for res in resultados:
            universo.extend(str(r["ticker"]) for r in (res.get("score_rows") or [])
                            if int(r["Ano"]) == safra)
            universo.extend((res.get("tickers_saidos_por_ano") or {}).get(safra) or [])
            for tk, d in (res.get("saidas") or {}).items():
                if d is not None:
                    saidas[str(tk)] = pd.Timestamp(d)
        universo = list(dict.fromkeys(universo))

        # Piso de qualidade e Score de Entrada com o retrato da SAFRA (2.32.0):
        # antes ficavam fora da medição, e a evidência cobria uma regra que a
        # tela não entrega.
        df_pit, guarda = _portoes_da_safra(view, decisao, universo, safra, series_pl)

        def exclui(tk, _g=guarda):
            return view._entry_guard_exclui(_g, tk)

        selic_n = float(contexto["selic_macro"].get(safra - 1, contexto["taxa_selic_aa"]))
        piso_log: dict = {}
        vagas_piso = 0
        segmentos: list = []
        for res, m in aprovados:
            sel, pesos, ranking = oos.selecao_do_segmento(
                res, m, safra, max_anos_lid=params["max_anos_lid"],
                pode_incluir_maior=view._pode_incluir_maior_participacao)
            antes = len(sel)
            sel = oos.aplicar_piso(
                sel, ranking, pesos, df_decisao=df_pit,
                piso_ativo=params["piso_qualidade"],
                seg_label=f"{res.get('setor')} > {res.get('segmento')}",
                selic=selic_n, log=piso_log)
            vagas_piso += max(0, antes - len(sel))
            if sel:
                segmentos.append((str(res.get("setor") or ""), sel, pesos, ranking))
        # Quem a guarda tira no laço final da carteira sem portão.
        excluidos = sorted({str(tk) for _st, sel, _p, _rk in segmentos for tk in sel
                            if exclui(str(tk))})
        saida.append({"safra": safra, "segmentos": segmentos, "universo": universo,
                      "saidas": saidas, "n_aprovados": len(aprovados),
                      "motivos": motivos, "exclui": exclui,
                      "piso_reprovados": len(piso_log.get("reprovados", [])),
                      "piso_substituicoes": len(piso_log.get("substituicoes", [])),
                      "vagas_perdidas_piso": vagas_piso,
                      "excluidos_guarda": excluidos})
    return contexto, saida


def _medir_perfil(resultados, precos, params, decisao, ibov, cost_cfg, hoje, portao=None):
    """Mede as variantes de `oos.VARIANTES` e, com ``portao``, a do portão real.

    ``portao(segmentos, safra, exclui)`` devolve ``(itens, log)`` no formato de
    ``oos.aplicar_portao_medido``: os vereditos do LLM sobre o dossiê da
    época. Sem ele, só a banda (o comportamento até a 2.1.0).
    """
    import numpy as np

    import core.b3_oos_carteira as oos
    from core.b3_safras import SafraCarteira, retorno_da_safra
    from core.b3_vigencia import ano_base_do_score, janela_de_vigencia

    contexto, por_safra = _segmentos_por_safra(resultados, precos, params, decisao,
                                               cost_cfg, hoje)
    if portao is not None and hasattr(portao, "preparar"):
        # Busca na rede, em paralelo, todos os pareceres que o portão vai
        # pedir; o laço abaixo só lê o cache.
        portao.preparar(por_safra)
    cap = contexto["cap"]
    selic_macro = contexto["selic_macro"]
    taxa_selic_aa = contexto["taxa_selic_aa"]
    safras = [p["safra"] for p in por_safra]
    variantes = oos.VARIANTES + ((oos.PORTAO_LLM_MEDIDO,) if portao else ())

    linhas_por_variante = {v: [] for v in variantes}
    for p in por_safra:
        safra, segmentos = p["safra"], p["segmentos"]
        universo, saidas, motivos = p["universo"], p["saidas"], p["motivos"]
        exclui = p["exclui"]
        rets = oos.retornos_por_ticker(oos.tickers_da_banda(segmentos, exclui), universo,
                                       precos, safra, saidas)
        tetos = {"cap": cap, "teto_setor": params["teto_setor"],
                 "teto_ciclico": params["teto_ciclico"]}

        inicio, fim = janela_de_vigencia(safra)
        brutos: dict[str, float] = {}
        portao_log = None
        for variante in variantes:
            veto = None
            if variante == oos.SEM_PORTAO:
                itens, _ = oos.vetar_na_carteira(segmentos, None, exclui)
                cart = oos.montar_carteira(itens, **tetos, exclui=exclui)
            elif variante == oos.PORTAO_LLM_MEDIDO:
                # O portão de verdade: o parecer do LLM sobre o dossiê da
                # época decide quem sai e quem entra, como na tela.
                itens, portao_log = portao(segmentos, safra, exclui)
                cart = oos.montar_carteira(itens, **tetos, exclui=exclui)
                trocas = [f"{t['sai']} → {t['entra'] or 'ninguém'}"
                          for t in portao_log["trocas"]]
                veto = "; ".join(trocas) or None
            else:
                # Banda: o veto ÚNICO da safra que mais derruba (adversário)
                # ou mais sobe (favorável) a carteira -- ver PORTAO_LLM.
                esc = oos.escolher_veto(
                    segmentos, rets, **tetos, exclui=exclui,
                    veta="melhor" if variante == oos.PORTAO_VETA_O_MELHOR else "pior")
                itens, cart = esc["itens"], esc["carteira"]
                if esc["vetado"] is not None:
                    entra = sorted({str(t["entra"]) for t in esc["trocas"] if t["entra"]})
                    veto = f"{esc['vetado']} → {', '.join(entra) or 'ninguém'}"
            pesos_fin = cart["pesos"]
            base = retorno_da_safra(
                SafraCarteira(safra=safra, ano_base=ano_base_do_score(safra),
                              inicio=inicio, fim=fim, completa=True,
                              pesos=pesos_fin, universo=tuple(universo),
                              segmentos=len(itens), saidas=saidas),
                precos, selic_por_ano=selic_macro, taxa_selic_aa=taxa_selic_aa)
            rets_cart = oos.retornos_por_ticker(list(pesos_fin), universo, precos,
                                                safra, saidas)
            bruto = sum(w * float(rets_cart[tk] or 0.0) for tk, w in pesos_fin.items())
            if pesos_fin and base["retorno_estrategia"] is not None:
                # Mesma conta por dois caminhos: se divergirem, a carteira
                # medida não é a que `core.b3_safras` mediria.
                assert abs(bruto - base["retorno_estrategia"]) < 1e-9, (safra, variante)
            if variante in (oos.PORTAO_VETA_O_MELHOR, oos.PORTAO_VETA_O_PIOR):
                # O veto foi escolhido com os mesmos retornos que a carteira mede.
                assert abs(bruto - esc["bruto"]) < 1e-9, (safra, variante)
            brutos[variante] = bruto
            linhas_por_variante[variante].append({
                "safra": safra,
                "pesos": pesos_fin,
                "retornos": rets_cart,
                "segmentos_avaliados": contexto["n_resultados"],
                "segmentos_aprovados": p["n_aprovados"],
                "reprovacoes": motivos,
                "piso_reprovados": p["piso_reprovados"],
                "piso_substituicoes": p["piso_substituicoes"],
                "vagas_perdidas_piso": p["vagas_perdidas_piso"],
                "excluidos_guarda": p["excluidos_guarda"],
                "n_ativos": len(pesos_fin),
                "inviavel_no_cap": bool(cart["inviavel"]),
                "exige_revisao": bool(cart["exige_revisao"]),
                "ew": base["retorno_equal_weight"],
                "selic": base["retorno_selic"],
                "ibov": oos.retorno_benchmark(ibov, precos, safra),
                "mensuravel": bool(base["mensuravel"]),
                "veto": veto,
            })
        # "Não vetar" é opção das duas pontas: no bruto a banda contém o sem-portão.
        assert (brutos[oos.PORTAO_VETA_O_MELHOR] - 1e-9 <= brutos[oos.SEM_PORTAO]
                <= brutos[oos.PORTAO_VETA_O_PIOR] + 1e-9), (safra, brutos)

    perfis_var = {}
    for variante, linhas in linhas_por_variante.items():
        cadeia = oos.simular_custos(
            [{"safra": r["safra"], "pesos": r["pesos"] if r["mensuravel"] else {},
              "retornos": r["retornos"]} for r in linhas], cost_cfg)
        tabela = []
        for r, c in zip(linhas, cadeia):
            linha = {k: r[k] for k in ("safra", "n_ativos", "segmentos_avaliados",
                                       "segmentos_aprovados", "reprovacoes",
                                       "piso_reprovados", "piso_substituicoes",
                                       "vagas_perdidas_piso", "excluidos_guarda",
                                       "inviavel_no_cap", "exige_revisao", "veto")}
            linha["maiores"] = ", ".join(
                f"{tk} {w:.0%}" for tk, w in sorted(r["pesos"].items(),
                                                    key=lambda kv: (-kv[1], kv[0]))[:5])
            if c.get("medida"):
                liq = c["liquido"]
                linha.update({
                    "bruto": c["bruto"], "liquido": liq,
                    "liquido_com_ir": c["liquido_com_ir"],
                    "custo": c["custo_pct"], "giro": c["giro"],
                    "ew": r["ew"], "selic": r["selic"], "ibov": r["ibov"],
                    "excesso_ew": liq - r["ew"] if r["ew"] is not None else None,
                    "excesso_ew_bruto": c["bruto"] - r["ew"] if r["ew"] is not None else None,
                    "excesso_ew_com_ir": (c["liquido_com_ir"] - r["ew"]
                                          if r["ew"] is not None else None),
                    "excesso_selic": liq - r["selic"] if r["selic"] is not None else None,
                    "excesso_ibov": liq - r["ibov"] if r["ibov"] is not None else None,
                })
            else:
                linha["sem_carteira"] = True
            tabela.append(linha)
        giros = [t["giro"] for t in tabela if t.get("giro") is not None]
        custos = [t["custo"] for t in tabela if t.get("custo") is not None]
        perfis_var[variante] = {
            "vs_equal_weight": oos.resumir(tabela, "excesso_ew"),
            "vs_equal_weight_bruto": oos.resumir(tabela, "excesso_ew_bruto"),
            "vs_equal_weight_com_ir": oos.resumir(tabela, "excesso_ew_com_ir"),
            "vs_selic": oos.resumir(tabela, "excesso_selic"),
            "vs_ibovespa": oos.resumir(tabela, "excesso_ibov"),
            "giro_medio": float(np.mean(giros)) if giros else None,
            "custo_medio": float(np.mean(custos)) if custos else None,
            "safras_inviaveis_no_cap": [t["safra"] for t in tabela if t["inviavel_no_cap"]],
            "safras_com_revisao": [t["safra"] for t in tabela if t["exige_revisao"]],
            "safras_sem_carteira": [t["safra"] for t in tabela if t.get("sem_carteira")],
            "safras": tabela,
        }
    principal = perfis_var[oos.SEM_PORTAO]["vs_equal_weight"]
    return {
        "parametros": params,
        "leitura": oos.leitura_honesta(principal),
        "variantes": perfis_var,
    }, safras


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    ap.add_argument("--perfil", action="append",
                    help="mede só este perfil (nome exato; repetível)")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="guarda/reaproveita a rodada da aba por perfil (pickle), "
                         "para refazer só a parte PIT sem reler o banco")
    ap.add_argument("--portao-llm", action="store_true",
                    help="mede também o portão de LLM de verdade (parecer sobre o "
                         "dossiê da época, core.b3_portao_pit); chama o LLM")
    ap.add_argument("--portao-cache", type=Path,
                    default=ROOT / "data" / "portao_llm_b3_pareceres.json",
                    help="cache dos pareceres da época (reaproveitado entre rodadas)")
    ap.add_argument("--portao-workers", type=int, default=6)
    args = ap.parse_args()
    # A lacuna de dado de uma rodada de medição não é lacuna do app.
    os.environ["LACUNAS_DESTINO"] = "desligado"

    url = _apontar_para_armazem_local()
    print(f"armazem local: {url.split('@')[-1]}", flush=True)

    import pandas as pd

    from core.database import get_engine
    host = get_engine().url.host
    if host not in ("localhost", "127.0.0.1"):
        raise SystemExit(f"get_engine() aponta para {host}, não para o armazém local")

    # A aba grava a rodada de validação em `get_engine()` a cada execução;
    # numa medição isso seria escrita colateral. Desligado aqui.
    import core.b3_validation as _val
    _val.persist_validation_run = lambda *a, **k: None
    import core.llm_b3 as _llm
    cadeia_original = _llm._provider_chain
    _desligar_llm()
    if _llm._provider_chain():
        raise SystemExit("LLM continua ligado: a medição travaria na rede")

    import core.b3_oos_carteira as oos
    import views.portfolio_b3 as view
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import AMPLO, CONSERVADOR, PRESETS, RECOMENDADO
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from core.market_read import load_precos_mensais
    from core.transaction_costs import CostConfig

    _instrumentar_view(view)
    cost_cfg = CostConfig.brasil_pf_default()
    hoje = pd.Timestamp.now().normalize()
    ibov_df = load_precos_mensais(("BOVA11",))
    ibov = ibov_df["BOVA11"].dropna() if "BOVA11" in ibov_df.columns else None

    nomes = args.perfil or [RECOMENDADO, CONSERVADOR, AMPLO]
    # Fase 1, LLM desligado: a aba roda sem cabeça por perfil (a tese
    # narrativa dela travaria na rede). Fase 2: com o portão medido, a
    # cadeia volta só para os pareceres da época.
    rodadas: dict = {}
    for nome in nomes:
        print(f"\n== perfil {nome}: aba ==", flush=True)
        # hash() de str muda a cada processo; md5 do nome é estável.
        cache = (args.cache_dir / f"oos_{hashlib.md5(nome.encode()).hexdigest()[:10]}.pkl"
                 if args.cache_dir else None)
        rodada = None
        if cache is not None and cache.exists():
            with cache.open("rb") as fh:
                rodada = pickle.load(fh)
            if len(rodada) != 4:
                # Cache de antes da 2.32.0: sem o insumo da guarda PIT.
                print("  cache sem o insumo da guarda PIT: refazendo", flush=True)
                rodada = None
        if rodada is None:
            rodada = _rodar_perfil(nome, dict(PRESETS[nome].valores))
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                with cache.open("wb") as fh:
                    pickle.dump(rodada, fh)
        rodadas[nome] = rodada

    avaliador = None
    if args.portao_llm:
        import core.b3_portao_pit as pit
        _llm._provider_chain = cadeia_original
        if not _llm._provider_chain():
            raise SystemExit("--portao-llm sem provedor de LLM configurado")
        macro = pit.carregar_macro_por_ano()
        avaliador = pit.AvaliadorPortaoPIT(
            args.portao_cache,
            lambda tk, safra: pit.montar_entrada_pit(tk, safra, macro),
            pit.parecer_pit, pit.sonda_pit, workers=args.portao_workers, tentativas=3)

    perfis: dict = {}
    todas_safras: set[int] = set()
    avaliadas: set[int] = set()
    for nome in nomes:
        print(f"\n== perfil {nome}: medição ==", flush=True)
        resultados, precos, params, decisao = rodadas[nome]
        medido, safras = _medir_perfil(resultados, precos, params, decisao,
                                       ibov, cost_cfg, hoje,
                                       portao=avaliador)
        if avaliador is not None:
            medido["portao_llm_medido"] = avaliador.resumo(pares=avaliador.consultados)
        avaliadas.update(safras)
        # A janela declarada é a das safras que tiveram carteira: as primeiras
        # não têm líder anterior a elas (sem backtest, sem aprovação).
        todas_safras.update(t["safra"] for t in medido["variantes"][oos.SEM_PORTAO]["safras"]
                            if not t.get("sem_carteira"))
        perfis[nome] = medido
        print(medido["leitura"], flush=True)
        for v in medido["variantes"]:
            r = medido["variantes"][v]["vs_equal_weight"]
            print(f"  {v}: media={r['media']} ic95={r['ic95']} n={r['n_safras']} "
                  f"neg={r['safras_negativas']}", flush=True)

    saida = {
        "versao_medicao": oos.VERSAO_MEDICAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "safras": sorted(todas_safras),
        "safras_avaliadas": sorted(avaliadas),
        "janela": (f"abril/{min(todas_safras)} a março/{max(todas_safras) + 1}"
                   if todas_safras else None),
        "metrica_principal": (
            "excesso por safra (abril/N a março/N+1) da carteira montada, "
            "LÍQUIDA do giro, sobre o equal-weight do universo elegível da safra"),
        "custos": {
            "modelo": "CostConfig.brasil_pf_default()",
            "spread_bps_large_cap": cost_cfg.spread_bps_large,
            "spread_bps_demais": cost_cfg.spread_bps_small,
            "corretagem_fixa": cost_cfg.corretagem_fixa,
            "cobrado": "meia spread + corretagem na compra E na venda do giro de abril",
            "ir": (f"{cost_cfg.ir_rate:.0%} sobre ganho realizado, isenção de "
                   f"R$ {cost_cfg.isencao_mes:,.0f}/mês, carteira nocional de "
                   f"R$ {oos.CAPITAL_NOCIONAL:,.0f} (sensibilidade, não principal)"),
            "benchmarks": "brutos (EW, Selic e BOVA11 não pagam custo): viés contra a carteira",
        },
        "fonte": {
            "precos": "armazém local, market.historical_prices (mensal ajustado)",
            "fundamentos": f"aba de Criação de Portfólio B3 sobre {_fonte_efetiva_b3()}",
            "ibovespa": "BOVA11 (proxy; ETF com taxa de 0,10% a.a.)",
        },
        "portao_llm": oos.PORTAO_LLM,
        "portao_llm_medido": (
            {**avaliador.resumo(), "versao_dossie": pit.VERSAO_DOSSIE_PIT,
             "cache": str(args.portao_cache.relative_to(ROOT)
                          if args.portao_cache.is_relative_to(ROOT) else args.portao_cache)}
            if avaliador is not None else None),
        "fora_do_pit": list(oos.FORA_DO_PIT),
        "perfis": perfis,
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if args.dry_run:
        print(texto)
    else:
        oos.CAMINHO_MEDICAO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {oos.CAMINHO_MEDICAO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
