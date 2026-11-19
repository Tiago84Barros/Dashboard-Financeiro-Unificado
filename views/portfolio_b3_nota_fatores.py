"""
views/portfolio_b3_nota_fatores.py — carteira ampla "nota + fatores" na aba B3.

Alternativa medida à carteira concentrada: em vez de escolher o líder de cada
segmento, tira os 30% de pior sinal do universo comprável e divide igualmente
entre o resto. A regra mora em `core/b3_nota_fatores.py` (a mesma que a
medição usa); a evidência vem gravada em `data/oos_nota_fatores_b3.json` por
`scripts/medir_nota_fatores_b3.py`.

`formar_hoje`, `tabela_para_exibicao`, `series_do_acumulado` e
`avisos_da_medicao` são puras (sem
streamlit, sem banco) para poderem ser testadas direto -- nesta base, teste
via AppTest vaza atribuição de módulo (nota `apptest-vaza-atribuicao-de-modulo`).
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

import pandas as pd
import streamlit as st

import core.b3_nota_fatores as nf
from core.b3_safras import (
    acumulado_das_safras,
    fmt_ao_ano,
    fmt_reais,
    legenda_acumulado,
)
from design.componentes import card_metrica
from design.lacunas import aviso_lacuna, detalhe_tecnico

logger = logging.getLogger(__name__)

CAMINHO_MEDICAO = Path(__file__).resolve().parents[1] / "data" / "oos_nota_fatores_b3.json"

#: Chave do estado da sessão onde o "Rodar" da aba guarda a carteira de hoje.
CHAVE_ESTADO = "pb3_nota_fatores"


def formar_hoje(resultados: list[dict], precos: pd.DataFrame,
                hist_batch: dict | None, *, perfil: str, hoje=None) -> dict | None:
    """Carteira "nota + fatores" da safra corrente, com o universo da aba.

    Universo = todo nome com nota de hoje (``score_proximo``) em qualquer
    segmento processado -- aprovado ou não, como na medição, que usa o
    universo inteiro da safra. Devolve ``None`` sem universo.
    """
    scores: dict[str, float] = {}
    for res in resultados or []:
        for tk, sc in (res.get("score_proximo") or {}).items():
            scores[str(tk)] = sc
    if not scores:
        return None
    agora = pd.Timestamp.now() if hoje is None else pd.Timestamp(hoje)
    # A safra leva o ano corrente, como o ``score_proximo`` da aba; entre
    # janeiro e março ela ainda não começou e a lista sai como prévia.
    safra = int(agora.year)
    inicio = nf.inicio_da_formacao(safra, agora)
    out = nf.carteira_nota_fatores(sorted(scores), scores, precos, hist_batch, inicio)
    out["safra"] = safra
    out["previa"] = inicio != pd.Timestamp(nf.janela_de_vigencia(safra)[0])
    out["perfil"] = str(perfil)
    return out


def tabela_para_exibicao(tabela: pd.DataFrame) -> pd.DataFrame:
    """Quem fica, em ordem de sinal, com as partes em escala legível (0–100)."""
    if tabela is None or tabela.empty:
        return pd.DataFrame()
    t = tabela[tabela["fica"]].copy()
    t = t.reset_index().rename(columns={"index": "ticker"})
    t = t.sort_values(["sinal", "ticker"], ascending=[False, True], kind="mergesort")
    out = pd.DataFrame({
        "Ticker": t["ticker"].astype(str),
        "Sinal": t["sinal"] * 100,
        "Nota (percentil)": t["nota_pct"] * 100,
        "Fatores (percentil)": t["fatores"] * 100,
        "Momento 12-1 (%)": t["mom"] * 100,
        "Volatilidade mensal (%)": -t["lowvol"] * 100,
        "ROE (%)": t["roe"] * 100,
    })
    return out.reset_index(drop=True)


def series_do_acumulado(dados: dict | None) -> dict:
    """Séries por safra, em fração, para `core.b3_safras.acumulado_das_safras`.

    As duas carteiras saem LÍQUIDAS do custo do giro (sem IR), como o cartão
    de excesso ao lado; pesos iguais é bruto e vem das safras da principal.
    """
    if not dados:
        return {}
    principal = (dados.get("nota_fatores") or {}).get("safras") or []
    so_nota = (((dados.get("comparacoes") or {}).get("tirar_30_pela_nota") or {})
               .get("safras") or [])

    def _col(linhas, campo):
        return {int(t["safra"]): t.get(campo) for t in linhas if "safra" in t}

    series = {}
    if principal:
        series["nota_fatores"] = _col(principal, "liquido")
        series["equal_weight"] = _col(principal, "equal_weight")
    if so_nota:
        series["so_nota"] = _col(so_nota, "liquido")
    return series


def avisos_da_medicao(dados: dict | None, motivos_vencida: list[str],
                      perfil_ativo: str | None) -> list[str]:
    """Ressalvas que a tela tem de dizer sobre a medição gravada."""
    if not dados:
        return ["Ainda não há medição gravada desta carteira."]
    avisos = []
    if motivos_vencida:
        avisos.append("A medição gravada é de uma versão anterior do método ("
                      + "; ".join(motivos_vencida) + "). O número abaixo pode "
                      "não valer para a carteira de hoje.")
    perfil_medido = dados.get("perfil")
    if perfil_ativo and perfil_medido and perfil_ativo != perfil_medido:
        avisos.append(f"A medição usou o perfil “{perfil_medido}”; a aba rodou "
                      f"com “{perfil_ativo}”. A nota muda com o perfil.")
    return avisos


_AJUDA = {
    "Sinal": "Média do percentil da nota e do percentil dos fatores (0 a 100). "
             "Saem da carteira os 30% de menor sinal.",
    "Nota (percentil)": "Posição da nota do motor no universo inteiro de hoje "
                        "(100 = a maior). Vazio: o nome não tem nota.",
    "Fatores (percentil)": "Média das posições em momento, baixa volatilidade "
                           "e ROE. Sem nenhum desses dados, fica em 50.",
    "Momento 12-1 (%)": "Retorno do mês t-12 ao t-1 antes da formação (o "
                        "último mês fica de fora).",
    "Volatilidade mensal (%)": "Desvio-padrão dos retornos mensais em até 36 "
                               "meses (pede 24). Menor é melhor.",
    "ROE (%)": "Último ROE publicado antes da formação.",
}


def _column_config() -> dict:
    cfg = {"Ticker": st.column_config.TextColumn("Ticker")}
    for col, ajuda in _AJUDA.items():
        cfg[col] = st.column_config.NumberColumn(col, help=ajuda, format="%.1f")
    return cfg


def _fmt_pp(v) -> str:
    return "—" if v is None else f"{v * 100:+.1f} pp".replace(".", ",")


def _render_acumulado(dados: dict) -> None:
    """R$ 1 encadeado pelas safras medidas: resultado, ao lado do ano a ano."""
    acum = acumulado_das_safras(series_do_acumulado(dados))
    if not acum["n"]:
        return
    rotulos = (("nota_fatores", "R$ 1 em nota + fatores"),
               ("so_nota", "R$ 1 só na nota, tirando 30%"),
               ("equal_weight", "R$ 1 em pesos iguais"))
    presentes = [(k, r) for k, r in rotulos if k in acum["series"]]
    for col, (chave, rotulo) in zip(st.columns(len(presentes), gap="small"), presentes):
        s = acum["series"][chave]
        with col:
            card_metrica(rotulo, fmt_reais(s["valor_final"]), delta=fmt_ao_ano(s["anual"]),
                         ajuda="Quanto R$ 1 vira reaplicado a cada abril pelas safras "
                               "medidas, e a taxa composta ao ano.")
    st.caption(legenda_acumulado(
        acum, base="as carteiras já líquidas do custo do giro, sem IR; pesos iguais bruto"))


def render_nota_fatores(perfil_ativo: str | None = None) -> None:
    """Seção "Carteira ampla: nota + fatores" da aba de Criação de Portfólio B3."""
    import core.b3_oos_carteira as oos
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:0.75rem;font-weight:700;color:var(--app-text);'
        'margin:18px 0 8px;">🧺 Carteira ampla: nota + fatores (alternativa medida)</div>',
        unsafe_allow_html=True)
    st.caption(
        "Outra forma de usar a mesma nota. Em vez de escolher o líder de cada "
        "segmento, a carteira **tira os 30% piores** do universo e divide "
        "igualmente entre o resto. O sinal junta a nota do motor com três "
        "fatores de mercado: momento, baixa volatilidade e ROE. A carteira é "
        "refeita uma vez por ano, em abril.")

    dados = oos.carregar(CAMINHO_MEDICAO)
    motivos = oos.vencida(dados, SCORE_VERSION, PRESETS_VERSION,
                          versao_medicao=nf.VERSAO) if dados else []
    for aviso in avisos_da_medicao(dados, motivos, perfil_ativo):
        st.warning(aviso, icon="⚠️")

    if dados:
        principal = (dados.get("nota_fatores") or {})
        resumo = principal.get("vs_equal_weight") or {}
        comp = dados.get("comparacoes") or {}
        so_nota = (comp.get("tirar_30_pela_nota") or {}).get("vs_equal_weight") or {}
        concentrada = (comp.get("concentrada_sem_portao") or {}).get("vs_equal_weight") or {}
        lo, hi = (resumo.get("ic95") or [None, None])
        c1, c2, c3 = st.columns(3, gap="small")
        with c1:
            card_metrica(
                "Nota + fatores vs. pesos iguais", _fmt_pp(resumo.get("media")),
                delta=(f"IC 95% [{_fmt_pp(lo)}; {_fmt_pp(hi)}]" if lo is not None else None),
                positivo=(True if lo is not None and lo > 0
                          else False if hi is not None and hi < 0 else None),
                ajuda="Excesso médio por ano, líquido do custo do giro de abril, "
                      "sobre dividir igualmente entre todo o universo da safra.")
        with c2:
            card_metrica("Só a nota, tirando 30%", _fmt_pp(so_nota.get("media")),
                         ajuda="Mesma régua, com o sinal só da nota do motor.")
        with c3:
            card_metrica("Carteira concentrada da aba", _fmt_pp(concentrada.get("media")),
                         ajuda="A carteira de líderes por segmento, sem o portão de "
                               "LLM, na mesma rodada e na mesma régua.")
        st.caption(oos.leitura_honesta(resumo))
        _render_acumulado(dados)
        st.caption(
            f"Medido em {len(dados.get('safras') or [])} safras "
            f"({html.escape(str(dados.get('janela') or '—'))}). É pouco ano e o "
            "efeito é pequeno: um ano ruim pode apagar a vantagem. Use como "
            "alternativa ampla, não como promessa.")
        detalhe_tecnico(
            "Fora da regra medida: " + "; ".join(dados.get("fora_da_regra") or [])
            + ". Custos: " + str((dados.get("custos") or {}).get("cobrado") or "—")
            + f". Medição {dados.get('versao_medicao')} de "
            f"{str(dados.get('medido_em') or '')[:10]}, perfil "
            f"“{dados.get('perfil')}”.",
            codigo="portfolio_b3.nota_fatores_medicao")

    estado = st.session_state.get(CHAVE_ESTADO)
    if not estado:
        st.caption("Clique em **🚀 Rodar Criação de Portfólio** para montar a "
                   "lista de hoje.")
        return
    tabela = estado.get("tabela")
    exibir = tabela_para_exibicao(tabela)
    if exibir.empty:
        aviso_lacuna("Nenhum nome com nota para montar a carteira ampla.",
                     codigo="tela.portfolio_b3.nota_fatores_sem_universo")
        return
    cob = estado.get("cobertura") or {}
    n_fica = len(exibir)
    inicio = pd.Timestamp(estado["inicio"])
    if estado.get("previa"):
        st.info(f"Prévia: a safra {estado['safra']} só começa em abril. A lista "
                "usa os dados de hoje e pode mudar até lá.", icon="🕒")
    peso = f"{100 / n_fica:.1f}".replace(".", ",")
    st.caption(
        f"Safra {estado['safra']}, formada em {inicio:%d/%m/%Y} com preços até "
        f"{estado['mes_fatores']}. Ficam **{n_fica}** de {cob.get('n', 0)} nomes, "
        f"cada um com {peso}% da carteira.")
    st.dataframe(exibir, width="stretch", hide_index=True, column_config=_column_config())
    detalhe_tecnico(
        f"Cobertura dos dados: nota em {cob.get('nota', 0)}, momento em "
        f"{cob.get('mom', 0)}, volatilidade em {cob.get('lowvol', 0)} e ROE em "
        f"{cob.get('roe', 0)} de {cob.get('n', 0)} nomes. Falta de fator "
        "não exclui o nome: ele fica no meio (50) naquela parte. Esta lista não "
        "passa pela guarda de entrada, pelo piso de qualidade nem pelos tetos de "
        "setor do perfil -- a medição também não passou.",
        codigo="portfolio_b3.nota_fatores_cobertura")
