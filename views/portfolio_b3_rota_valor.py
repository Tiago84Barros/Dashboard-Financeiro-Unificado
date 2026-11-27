"""
views/portfolio_b3_rota_valor.py — carteira "rota de valor" na aba B3.

Estratégia padrão da aba desde 26/11/2026, por escolha do usuário depois da
comparação da §29 do guia (`core/b3_comparacao_estrategias.py`), confirmada em
27/11/2026 na régua 1.3.0. Nela, com a renda lida do pregão diário, a rota de
valor inteira passa o retorno sobre pesos iguais (+16,3 pp, IC acima de zero) e
o capital final pelo preço, mas falha a R2: a renda caiu em 3 anos, contra 2 de
pesos iguais (a terceira, 2024, de cerca de 1%). A regra escolheria
``nota_fatores``; o usuário manteve a rota, fora da regra. A tela lê as duas
escolhas do arquivo (``escolha`` e ``decisao_do_usuario``) e diz a diferença
(`ressalva_da_regra`), em vez de afirmar que a rota passa.

A regra é a da medição: `core.b3_estrategias_fundamentalistas.carteira_rota_valor`
sobre o quadro de decisão de hoje, com a Selic do ano anterior à safra
(`selic_da_safra`, gêmea da de `scripts/medir_estrategias_fundamentalistas_b3.py`),
compra em pesos iguais quem a rota de valor (`core.b3_value_route`) classifica
como ``oportunidade``. A evidência vem gravada em
`data/oos_comparacao_estrategias_b3.json` por `scripts/comparar_estrategias_b3.py`.

`selic_da_safra`, `formar_hoje`, `tabela_para_exibicao`, `renda_ano_a_ano`,
`avisos_da_medicao` e `ressalva_da_regra` são puras (sem streamlit, sem banco) para poderem ser
testadas direto.
"""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st

import core.b3_comparacao_estrategias as ce
import core.b3_estrategias_fundamentalistas as fe
from design.componentes import card_metrica
from design.lacunas import aviso_lacuna, detalhe_tecnico

CAMINHO_MEDICAO = (Path(__file__).resolve().parents[1] / "data"
                   / "oos_comparacao_estrategias_b3.json")

#: Chave do estado da sessão onde o "Rodar" da aba guarda a carteira de hoje.
CHAVE_ESTADO = "pb3_rota_valor"

#: Rótulo do braço na medição da §29.
BRACO = fe.ROTA_VALOR


def selic_da_safra(selic_macro: dict | None, safra: int, taxa_padrao: float) -> float:
    """Selic do ano N-1 (ou a última antes dele), como na medição.

    Só sem nenhuma observação até N-1 usa ``taxa_padrao`` (a média da aba).
    """
    anos = [int(a) for a in (selic_macro or {}) if int(a) <= int(safra) - 1]
    if not anos:
        return float(taxa_padrao)
    return float(selic_macro[max(anos)] if max(anos) in selic_macro
                 else selic_macro[str(max(anos))])


def formar_hoje(resultados: list[dict], df_decisao: pd.DataFrame | None, *,
                selic: float, perfil: str, hoje=None) -> dict | None:
    """Carteira "rota de valor" da safra corrente, com o universo da aba.

    Universo = todo nome com nota de hoje (``score_proximo``) em qualquer
    segmento processado, aprovado ou não, como o universo da safra na
    medição. Devolve ``None`` sem universo.
    """
    universo: set[str] = set()
    for res in resultados or []:
        universo.update(str(t).upper() for t in (res.get("score_proximo") or {}))
    if not universo:
        return None
    agora = pd.Timestamp.now() if hoje is None else pd.Timestamp(hoje)
    safra = int(agora.year)
    nomes = fe.carteira_rota_valor(df_decisao, universo, selic=selic)

    from core.b3_value_route import rank_value_opportunities
    quadro = fe._do_universo(df_decisao, universo)
    ranked = (rank_value_opportunities(quadro, selic=selic) if not quadro.empty
              else pd.DataFrame(columns=["Ticker", "classificacao"]))
    contagem = (ranked["classificacao"].value_counts().to_dict()
                if "classificacao" in ranked else {})
    return {
        "safra": safra,
        # A safra começa em abril; de janeiro a março a lista é prévia.
        "previa": agora < pd.Timestamp(year=safra, month=4, day=1),
        "perfil": str(perfil),
        "selic": float(selic),
        "n_universo": len(universo),
        "n_com_dado": int(len(quadro)),
        "contagem": {str(k): int(v) for k, v in contagem.items()},
        "pesos": fe.pesos_iguais(nomes),
        "tabela": ranked,
        "versao": fe.VERSAO,
    }


def tabela_para_exibicao(tabela: pd.DataFrame | None) -> pd.DataFrame:
    """As oportunidades, na ordem da rota, com as colunas que explicam a escolha."""
    from core.b3_value_route import OPORTUNIDADE
    if tabela is None or tabela.empty or "classificacao" not in tabela:
        return pd.DataFrame()
    t = tabela[tabela["classificacao"] == OPORTUNIDADE].copy()
    if t.empty:
        return pd.DataFrame()
    t["Ticker"] = t["Ticker"].astype(str)
    t = t.sort_values(["valor_score", "margem_valor", "Ticker"],
                      ascending=[False, False, True], kind="mergesort")
    return pd.DataFrame({
        "Ticker": t["Ticker"],
        "Nota da rota": pd.to_numeric(t["valor_score"], errors="coerce"),
        "Desconto (%)": pd.to_numeric(t["margem_valor"], errors="coerce") * 100,
        "Folga de solvência": pd.to_numeric(t["forca_solvencia"], errors="coerce"),
        "Por quê": t["explicacao"].astype(str),
    }).reset_index(drop=True)


def _pct_ou_none(v):
    return None if v is None else float(v) * 100


def renda_ano_a_ano(dados: dict | None) -> pd.DataFrame:
    """Renda e capital por safra: rota de valor ao lado de pesos iguais.

    O investidor gasta os dividendos; o capital cresce só pelo preço
    (`core.b3_comparacao_estrategias.trajetoria_de_renda`).
    """
    if not dados:
        return pd.DataFrame()
    rota = (((dados.get("estrategias") or {}).get(BRACO) or {}).get("renda") or {})
    ew = ((dados.get("pesos_iguais") or {}).get("renda") or {})
    por_ew = {int(a["safra"]): a for a in ew.get("anos") or []}
    linhas = []
    for a in rota.get("anos") or []:
        s = int(a["safra"])
        b = por_ew.get(s) or {}
        linhas.append({
            "Safra": s,
            "Renda da rota (R$)": a.get("renda"),
            "Renda de pesos iguais (R$)": b.get("renda"),
            # Sem dado fica vazio (None), nunca zero: zero seria renda medida.
            "Dividendos recebidos pela rota (%)": _pct_ou_none(a.get("renda_caixa")),
            "Retorno da rota (%)": _pct_ou_none(a.get("retorno")),
            "Capital da rota no início (R$)": a.get("capital_inicio"),
        })
    return pd.DataFrame(linhas)


def avisos_da_medicao(dados: dict | None, perfil_ativo: str | None,
                      versao_metodologia: str, versao_presets: str) -> list[str]:
    """Ressalvas que a tela tem de dizer sobre a medição gravada."""
    if not dados or BRACO not in (dados.get("estrategias") or {}):
        return ["Ainda não há medição gravada desta carteira."]
    motivos = ce.motivos_vencida(dados, versao_metodologia, versao_presets)
    avisos = []
    if motivos:
        avisos.append("A medição gravada é de uma versão anterior do método ("
                      + "; ".join(motivos) + "). O número abaixo pode não valer "
                      "para a carteira de hoje.")
    perfil_medido = str(dados.get("perfil") or "")
    if perfil_ativo and perfil_medido and str(perfil_ativo) != perfil_medido:
        avisos.append(f"A medição usou o perfil “{perfil_medido}”; a aba rodou "
                      f"com “{perfil_ativo}”. O universo muda com o perfil.")
    return avisos


#: Nome dos testes da §29 em palavras simples, para a ressalva da tela.
_TESTES_EM_PALAVRAS = {
    "r1_retorno": "o de retorno acima de pesos iguais",
    "r2_dividendo_crescente": "o de renda que cresce sem cair mais vezes que pesos iguais",
    "r3_valorizacao": "o de capital final pelo preço acima de pesos iguais",
}

#: Nome das estratégias que a regra pode escolher, como o seletor da aba as chama.
_NOMES = {
    fe.ROTA_VALOR: "rota de valor",
    "nota_fatores": "nota + fatores",
    "tirar_30_pela_nota": "tirar os 30% piores pela nota",
    "rota_valor_top20": "top 20 da rota de valor",
}


def ressalva_da_regra(dados: dict | None) -> str | None:
    """O que a tela diz quando a escolha do usuário não é a da regra da §29.

    Lê ``escolha`` (a da regra) e ``decisao_do_usuario`` do arquivo gravado, e o
    veredito da rota (``decisao.falhou``). Sem diferença, devolve ``None``.
    """
    if not dados or BRACO not in (dados.get("estrategias") or {}):
        return None
    regra = (dados.get("escolha") or {}).get("padrao")
    usuario = (dados.get("decisao_do_usuario") or {}).get("padrao")
    if not regra or regra == BRACO or usuario != BRACO:
        return None
    rota = dados["estrategias"][BRACO]
    falhou = (rota.get("decisao") or {}).get("falhou") or []
    renda = rota.get("renda") or {}
    renda_ew = ((dados.get("pesos_iguais") or {}).get("renda") or {})
    testes = ", ".join(_TESTES_EM_PALAVRAS.get(t, t) for t in falhou) or "nenhum"
    texto = f"Pela regra da comparação, a rota de valor não passa num teste: {testes}."
    if "r2_dividendo_crescente" in falhou:
        texto += (f" A renda dela caiu em {_fmt_int(renda.get('quedas_de_renda'))} "
                  f"anos, contra {_fmt_int(renda_ew.get('quedas_de_renda'))} de pesos "
                  "iguais.")
    texto += (f" A regra escolheria “{_NOMES.get(regra, regra)}”. A rota segue como "
              "padrão por escolha do usuário.")
    return texto


def _fmt_pp(v) -> str:
    return "—" if v is None else f"{v * 100:+.1f} pp".replace(".", ",")


def _fmt_pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def _fmt_int(v) -> str:
    """Trajetória insuficiente grava None: mostra "—", nunca "None"."""
    return "—" if v is None else str(int(v))


def _fmt_mil(v) -> str:
    if v is None:
        return "—"
    return f"R$ {v / 1000:,.0f} mil".replace(",", ".")


def _ic(resumo: dict) -> tuple[str | None, bool | None]:
    lo, hi = (resumo.get("ic95") or [None, None])
    if lo is None:
        return None, None
    return (f"IC 95% [{_fmt_pp(lo)}; {_fmt_pp(hi)}]",
            True if lo > 0 else False if hi is not None and hi < 0 else None)


_AJUDA = {
    "Nota da rota": "0 a 100: 60% pelo desconto e 40% pela folga de solvência. "
                    "É a ordem da rota; os pesos da carteira são iguais.",
    "Desconto (%)": "Quanto o preço está abaixo do valor intrínseco (média de "
                    "Graham e Bazin disponíveis). Entra a partir de 20%.",
    "Folga de solvência": "0 a 100: quanto a empresa sobra em dívida/PL e "
                          "liquidez corrente frente aos limites da rota.",
    "Por quê": "O motivo da classificação, com as ressalvas.",
}


def _column_config() -> dict:
    cfg = {"Ticker": st.column_config.TextColumn("Ticker"),
           "Por quê": st.column_config.TextColumn("Por quê", help=_AJUDA["Por quê"])}
    for col in ("Nota da rota", "Desconto (%)", "Folga de solvência"):
        cfg[col] = st.column_config.NumberColumn(col, help=_AJUDA[col], format="%.1f")
    return cfg


def _render_evidencia(dados: dict) -> None:
    rota = (dados.get("estrategias") or {}).get(BRACO) or {}
    vs = rota.get("vs_equal_weight") or {}
    com_ir = rota.get("com_ir_vs_equal_weight") or {}
    renda = rota.get("renda") or {}
    renda_ew = ((dados.get("pesos_iguais") or {}).get("renda") or {})

    c1, c2, c3 = st.columns(3, gap="small")
    delta, pos = _ic(vs)
    with c1:
        card_metrica("Retorno a mais que pesos iguais", _fmt_pp(vs.get("media")),
                     delta=delta, positivo=pos,
                     ajuda="Excesso médio por ano, líquido do custo do giro, sobre "
                           "dividir igualmente entre todo o universo da safra.")
    delta_ir, pos_ir = _ic(com_ir)
    with c2:
        card_metrica("O mesmo, depois do IR", _fmt_pp(com_ir.get("media")),
                     delta=delta_ir, positivo=pos_ir,
                     ajuda="Com o imposto de 15% sobre o ganho das vendas de "
                           "cada abril. O intervalo encosta no zero.")
    with c3:
        card_metrica("Renda crescendo por ano", _fmt_pct(renda.get("crescimento_renda_aa")),
                     delta=f"pesos iguais {_fmt_pct(renda_ew.get('crescimento_renda_aa'))}",
                     ajuda="Tendência dos dividendos de quem gasta a renda e "
                           "deixa o capital crescer só pelo preço.")
    c4, c5, c6 = st.columns(3, gap="small")
    with c4:
        card_metrica("Anos com renda menor", _fmt_int(renda.get("quedas_de_renda")),
                     delta=f"pesos iguais {_fmt_int(renda_ew.get('quedas_de_renda'))}",
                     ajuda="Quantas vezes a renda do ano caiu frente à do ano anterior.")
    with c5:
        card_metrica("R$ 100 mil viraram (só preço)", _fmt_mil(renda.get("capital_final")),
                     delta=f"pesos iguais {_fmt_mil(renda_ew.get('capital_final'))}",
                     ajuda="Capital no fim das safras sem contar os dividendos, "
                           "que foram gastos.")
    with c6:
        card_metrica("Dividendos recebidos no período", _fmt_mil(renda.get("renda_total")),
                     delta=f"pesos iguais {_fmt_mil(renda_ew.get('renda_total'))}",
                     ajuda="Soma da renda de todas as safras, a partir de R$ 100 mil.")

    import core.b3_oos_carteira as oos
    st.caption(oos.leitura_honesta(vs))
    ressalva = ressalva_da_regra(dados)
    if ressalva:
        st.info(ressalva, icon="ℹ️")

    tabela = renda_ano_a_ano(dados)
    if not tabela.empty:
        with st.expander("Renda e retorno safra a safra", expanded=False):
            st.dataframe(tabela, width="stretch", hide_index=True, column_config={
                "Safra": st.column_config.NumberColumn("Safra", format="%d"),
                **{c: st.column_config.NumberColumn(c, format="%.0f")
                   for c in tabela.columns if c.endswith("(R$)")},
                **{c: st.column_config.NumberColumn(c, format="%.1f")
                   for c in tabela.columns if c.endswith("(%)")},
            })

    nomes = rota.get("nomes_por_safra") or {}
    tam = sorted(int(v) for v in nomes.values()) if nomes else []
    st.caption(
        f"Medido em {len(dados.get('safras') or [])} safras "
        f"({html.escape(str(dados.get('janela') or '—'))}). Ressalvas: boa parte "
        f"da vantagem vem de {vs.get('melhor_safra', '—')}; sem esse ano, ela cai "
        f"para {_fmt_pp(vs.get('media_sem_melhor_safra'))}. A carteira troca cerca "
        f"de {_fmt_pct(rota.get('giro_medio_anual'))} dos nomes por ano"
        + (f" e teve de {tam[0]} a {tam[-1]} empresas" if tam else "")
        + ". Nove safras é pouco: use como ponto de partida, não como promessa.")
    detalhe_tecnico(
        "Régua da §29 (" + "; ".join(f"{k}: {v}" for k, v in
                                     (dados.get("regra") or {}).items()
                                     if k.startswith("r")) + "). Limites: "
        + "; ".join(dados.get("limites") or []) + f". Medição de "
        f"{str(dados.get('medido_em') or '')[:10]}, régua {dados.get('versao_regua')}, "
        f"perfil “{dados.get('perfil')}”. Escolha da regra: "
        f"{(dados.get('escolha') or {}).get('padrao', '—')}; escolha do usuário: "
        f"{(dados.get('decisao_do_usuario') or {}).get('padrao', '—')} "
        f"({(dados.get('decisao_do_usuario') or {}).get('motivo', '—')}).",
        codigo="portfolio_b3.rota_valor_medicao")


def render_rota_valor(perfil_ativo: str | None = None) -> None:
    """Seção "Carteira padrão: rota de valor" da aba de Criação de Portfólio B3.

    Desenhada logo abaixo do seletor de estratégia, que já traz o divisor.
    """
    import core.b3_oos_carteira as oos
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

    st.markdown(
        '<div style="font-size:0.75rem;font-weight:700;color:var(--app-text);'
        'margin:18px 0 8px;">💎 Carteira padrão: rota de valor</div>',
        unsafe_allow_html=True)
    st.caption(
        "A estratégia padrão desta aba. Compra, em partes iguais, toda empresa "
        "que está **barata frente ao seu valor** (desconto de 20% ou mais por "
        "Graham e Bazin) **e aguenta as dívidas** (dívida até 2,5 vezes o "
        "patrimônio e liquidez corrente de 1 ou mais). A carteira é refeita uma "
        "vez por ano, em abril. As outras estratégias continuam no seletor acima.")

    dados = oos.carregar(CAMINHO_MEDICAO)
    for aviso in avisos_da_medicao(dados, perfil_ativo, SCORE_VERSION, PRESETS_VERSION):
        st.warning(aviso, icon="⚠️")
    if dados and BRACO in (dados.get("estrategias") or {}):
        _render_evidencia(dados)

    estado = st.session_state.get(CHAVE_ESTADO)
    if not estado:
        st.caption("Clique em **🚀 Rodar Criação de Portfólio** para montar a "
                   "lista de hoje.")
        return
    exibir = tabela_para_exibicao(estado.get("tabela"))
    if exibir.empty:
        aviso_lacuna("Nenhuma empresa do universo de hoje passou na rota de valor.",
                     codigo="tela.portfolio_b3.rota_valor_sem_oportunidade")
        return
    if estado.get("previa"):
        st.info(f"Prévia: a safra {estado['safra']} só começa em abril. A lista "
                "usa os dados de hoje e pode mudar até lá.", icon="🕒")
    n = len(exibir)
    peso = f"{100 / n:.1f}".replace(".", ",")
    st.caption(
        f"Safra {estado['safra']}: **{n}** de {estado.get('n_universo', 0)} empresas "
        f"do universo passam na rota, cada uma com {peso}% da carteira.")
    st.dataframe(exibir, width="stretch", hide_index=True, column_config=_column_config())
    cont = estado.get("contagem") or {}
    detalhe_tecnico(
        f"Classificação do universo de hoje: {cont.get('oportunidade', 0)} "
        f"oportunidade(s), {cont.get('armadilha_potencial', 0)} armadilha(s) "
        f"(barata mas sem solvência), {cont.get('sem_margem', 0)} sem desconto e "
        f"{cont.get('sem_evidencia', 0)} sem dado crítico; "
        f"{estado.get('n_universo', 0) - estado.get('n_com_dado', 0)} nome(s) do "
        f"universo sem múltiplos no quadro de hoje. Selic usada na ressalva de "
        f"ROIC: {_fmt_pct(estado.get('selic'))} (a do ano anterior à safra). A "
        "lista lê o último balanço e o preço de hoje; a medição leu o que se "
        "sabia em cada abril. Não passa pela guarda de entrada, pelo piso de "
        "qualidade, pelo portão de LLM nem pelos tetos de setor -- a medição "
        f"também não passou. Regra {estado.get('versao')}.",
        codigo="portfolio_b3.rota_valor_cobertura")
