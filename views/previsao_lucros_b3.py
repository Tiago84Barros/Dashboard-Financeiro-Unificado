"""Aba 🔮 Previsão de Lucros (Empresas B3).

Só lê o artefato que ``scripts/treinar_previsao_lucros_b3.py`` publica em
``data/public/previsao_lucros_b3.json.gz``; nada é treinado aqui. A tela mostra
o que a medição decidiu -- inclusive REPROVADO -- sem afrouxar a régua: o
veredito é o do holdout (usado uma vez), não o do desenvolvimento.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

import core.b3_previsao_lucros as pl
from design.componentes import badge_status, card_metrica, estado_vazio, secao_titulo
from design.lacunas import aviso_lacuna, detalhe_tecnico

_NOME_MODELO = {"ingenuo": "Ingênuo", "ridge": "Ridge", "gbm": "Gradient boosting",
                "mlp": "Rede neural (MLP)"}


@st.cache_data(show_spinner=False)
def _artefato(caminho: str, mtime: float) -> dict | None:
    del mtime  # só invalida o cache quando o arquivo muda
    return pl.carregar_artefato(caminho)


def carregar() -> dict | None:
    p = Path(__file__).resolve().parents[1] / pl.ARTEFATO
    return _artefato(str(p), p.stat().st_mtime) if p.exists() else None


def _pct(v, casas: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{casas}f}%".replace(".", ",")


def _num(v, casas: int = 4) -> str:
    return "—" if v is None else f"{v:.{casas}f}".replace(".", ",")


def _sinal(v, casas: int = 4) -> str:
    return "—" if v is None else f"{v:+.{casas}f}".replace(".", ",")


def _tipo(veredito: str | None) -> str:
    return {"APROVADO": "sucesso", "MELHORA": "sucesso",
            "REPROVADO": "erro"}.get(str(veredito), "neutro")


def _cards_veredito(art: dict) -> None:
    v = (art.get("holdout") or {}).get("veredito") or {}
    ric = art.get("rank_ic") or {}
    modelo = _NOME_MODELO.get(v.get("modelo"), v.get("modelo") or "—")
    dif = v.get("dif_erro_abs") or {}
    c1, c2, c3 = st.columns(3)
    with c1:
        card_metrica(
            "Erro vs. modelo ingênuo", v.get("veredito") or "—",
            delta=(f"Δ erro médio {_sinal(dif.get('media'))} · "
                   f"IC95% [{_sinal(dif.get('ic_low'))}; {_sinal(dif.get('ic_high'))}]"),
            positivo=v.get("veredito") == "APROVADO" if v else None,
            ajuda=("Aprovado só se o intervalo de 95% da diferença de erro absoluto "
                   "(reamostrado por empresa) ficar inteiro abaixo de zero."))
    with c2:
        card_metrica(
            "Acerto do sinal da variação", v.get("sinal") or "—",
            delta=(f"{_pct(v.get('acerto_sinal'))} vs {_pct(v.get('acerto_sinal_majoritario'))} "
                   f"do sinal majoritário · n={v.get('n_sinal') or 0}"),
            positivo=v.get("sinal") == "APROVADO" if v else None,
            ajuda=("Aprovado só se vencer 50% (binomial) E o chute do sinal mais "
                   "frequente no treino (McNemar), ambos com p < 0,05."))
    with c3:
        if ric.get("status") == "medido":
            d = ric.get("delta") or {}
            card_metrica(
                "Efeito no Rank-IC do score", ric.get("veredito") or "—",
                delta=(f"ΔIC médio {_sinal(d.get('mean'))} · "
                       f"IC95% [{_sinal(d.get('ci_low'))}; {_sinal(d.get('ci_high'))}]"),
                positivo=ric.get("veredito") == "MELHORA",
                ajuda=("Score sozinho contra score + previsão (50/50), nas mesmas "
                       "empresas e anos. Melhora só se o intervalo do ΔIC ficar acima de zero."))
        else:
            card_metrica("Efeito no Rank-IC do score", "Não medido", positivo=None)
    st.caption(
        f"Modelo escolhido no desenvolvimento: {modelo}. Veredito medido no holdout "
        f"(safras {_faixa(art, 'safras_holdout')}), avaliado uma única vez, com todas "
        "as empresas listadas na safra -- inclusive as que depois saíram da bolsa.")


def _faixa(art: dict, chave: str) -> str:
    s = (art.get("configuracao") or {}).get(chave) or []
    return f"{min(s)}–{max(s)}" if s else "—"


def _tabela_por_safra(art: dict) -> pd.DataFrame:
    modelo = ((art.get("holdout") or {}).get("veredito") or {}).get("modelo")
    linhas = []
    for fase, bloco in (("Desenvolvimento", art.get("dev")),
                        ("Holdout", art.get("holdout"))):
        for m in (bloco or {}).get("por_safra") or []:
            mm, mi = (m.get(modelo) or {}), (m.get("ingenuo") or {})
            mae_m, mae_i = mm.get("mae"), mi.get("mae")
            linhas.append({
                "Safra": m.get("safra"), "Fase": fase,
                "Empresas previstas": m.get("n_previstas"),
                "Sem desfecho": m.get("n_sem_alvo"),
                "MAE modelo": mae_m, "MAE ingênuo": mae_i,
                "Erro / ingênuo": (mae_m / mae_i) if mae_m is not None and mae_i else None,
                "Acerto de sinal": mm.get("acerto_sinal"),
            })
    return pd.DataFrame(linhas)


def _secao_por_safra(art: dict) -> None:
    secao_titulo("Veredito por safra", "📅",
                 "Cada safra prevê o exercício seguinte com o que era público em 1º de abril")
    df = _tabela_por_safra(art)
    if df.empty:
        estado_vazio("Sem safras medidas.", "📅")
        return
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={
            "MAE modelo": st.column_config.NumberColumn(format="%.4f"),
            "MAE ingênuo": st.column_config.NumberColumn(format="%.4f"),
            "Erro / ingênuo": st.column_config.NumberColumn(
                format="%.3f", help="Abaixo de 1: o modelo errou menos que o ingênuo nessa safra."),
            "Acerto de sinal": st.column_config.NumberColumn(format="percent"),
        })
    st.caption("A razão por safra é descritiva. O veredito é o agregado acima, "
               "com intervalo de confiança; uma safra boa sozinha não aprova nada.")


def _tabela_previsoes(art: dict) -> pd.DataFrame:
    linhas = []
    for r in art.get("previsoes_atuais") or []:
        linhas.append({
            "Ticker": ", ".join(r.get("tickers") or []) or "—",
            "Empresa": r.get("nome") or f"CVM {r.get('cd_cvm')}",
            "Setor": r.get("setor") or "—",
            "Lucro do ano-base (R$ mi)": _mi(r.get("lucro_base")),
            "Lucro previsto (R$ mi)": _mi(r.get("lucro_previsto")),
            "Δ previsto / ativo": r.get("y_previsto"),
            "IC 80% baixo": r.get("ic_low"),
            "IC 80% alto": r.get("ic_high"),
        })
    df = pd.DataFrame(linhas)
    return df.sort_values("Δ previsto / ativo", ascending=False) if not df.empty else df


def _mi(v):
    return None if v is None else v / 1e6


def _secao_previsoes(art: dict) -> None:
    safra = (art.get("configuracao") or {}).get("safra_viva")
    secao_titulo(f"Previsão para o exercício {safra} " if safra else "Previsões atuais", "🔮",
                 "Variação do lucro em proporção do ativo total, com intervalo de 80%")
    df = _tabela_previsoes(art)
    if df.empty:
        estado_vazio("Nenhuma empresa com demonstração publicada até a data de decisão.", "🔮")
        return
    busca = st.text_input("Filtrar por ticker ou empresa", key="pl_b3_busca").strip().lower()
    if busca:
        df = df[df["Ticker"].str.lower().str.contains(busca, regex=False)
                | df["Empresa"].str.lower().str.contains(busca, regex=False)]
    pct = st.column_config.NumberColumn(format="percent")
    mi = st.column_config.NumberColumn(format="%.1f")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Δ previsto / ativo": pct, "IC 80% baixo": pct,
                                "IC 80% alto": pct, "Lucro do ano-base (R$ mi)": mi,
                                "Lucro previsto (R$ mi)": mi})
    cob = ((art.get("holdout") or {}).get("cobertura_intervalo") or {}).get("cobertura")
    st.caption(
        "Intervalo a partir dos erros fora da amostra das safras anteriores"
        + (f"; no holdout ele conteve o resultado real em {_pct(cob)} dos casos." if cob is not None
           else ".")
        + " Previsão estatística, não recomendação de compra ou venda.")


def _secao_rank_ic(art: dict) -> None:
    ric = art.get("rank_ic") or {}
    if ric.get("status") != "medido":
        return
    secao_titulo("Efeito no Rank-IC do score B3", "📈",
                 "Correlação de postos entre a nota de abril e o retorno dos 12 meses seguintes")
    c1, c2, c3 = st.columns(3)
    for col, chave, titulo in ((c1, "score", "Score sozinho"),
                               (c2, "previsao", "Previsão sozinha"),
                               (c3, "combinado", "Score + previsão")):
        b = ric.get(chave) or {}
        with col:
            card_metrica(titulo, _num(b.get("mean")),
                         delta=f"IC95% [{_sinal(b.get('ci_low'))}; {_sinal(b.get('ci_high'))}]",
                         positivo=None)
    anos = ric.get("ic_por_ano") or {}
    if anos:
        df = pd.DataFrame([{"Ano": int(a), "Score": v.get("score"), "Previsão": v.get("previsao"),
                            "Combinado": v.get("combinado"), "Δ": v.get("delta")}
                           for a, v in sorted(anos.items())])
        fmt = st.column_config.NumberColumn(format="%.3f")
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={k: fmt for k in ("Score", "Previsão", "Combinado", "Δ")})
    st.caption(f"Comparação nas mesmas empresas e anos ({_pct(ric.get('cobertura_previsao'), 0)} "
               "dos pares do score têm previsão). Peso 50/50 fixado antes da medição.")


def _registrar_detalhes(art: dict) -> None:
    detalhe_tecnico(
        f"Previsão de lucros B3: metodologia {art.get('versao_metodologia')}, "
        f"config {art.get('config_hash')}, gerado em {art.get('gerado_em')}, "
        f"holdout executado {art.get('holdout_execucoes')}x.",
        codigo="tela.b3.previsao_lucros.procedencia")
    for i, txt in enumerate(art.get("limitacoes") or []):
        detalhe_tecnico(txt, codigo=f"tela.b3.previsao_lucros.limitacao_{i}")
    ric = art.get("rank_ic") or {}
    if ric.get("status") != "medido":
        detalhe_tecnico(f"Rank-IC não medido: {ric.get('motivo') or ric.get('status')}",
                        codigo="tela.b3.previsao_lucros.rank_ic_nao_medido")


def render(show_header: bool = False) -> None:
    del show_header
    art = carregar()
    if not art:
        aviso_lacuna("Artefato da previsão de lucros ausente ou ilegível "
                     f"({pl.ARTEFATO}); rode scripts/treinar_previsao_lucros_b3.py.",
                     codigo="tela.b3.previsao_lucros.artefato_ausente")
        estado_vazio("A previsão de lucros ainda não foi publicada.", "🔮")
        return
    secao_titulo("Previsão de Lucros", "🔮",
                 "Prever a variação do lucro do próximo ano vence o 'lucro igual ao deste ano'?")
    v = ((art.get("holdout") or {}).get("veredito") or {}).get("veredito")
    badge_status(f"Veredito do holdout: {v or 'não medido'}", _tipo(v))
    _cards_veredito(art)
    _secao_por_safra(art)
    _secao_previsoes(art)
    _secao_rank_ic(art)
    _registrar_detalhes(art)
