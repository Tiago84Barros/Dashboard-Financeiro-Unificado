"""Aba 📒 Carteira 20 (Empresas B3).

Só lê o registro append-only ``data/public/carteira20_registro.jsonl`` que
``scripts/carteira20_revisao.py`` grava; nada é treinado aqui. A tela mostra a
carteira vigente, o histórico das revisões com os hashes, o estado da cadeia e
o que a pesquisa espera -- sem antecipar conclusão: o protocolo proíbe
qualquer veredito antes de 12 meses de registro.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import core.carteira20 as c20
from design.componentes import badge_status, card_metrica, estado_vazio, secao_titulo
from design.lacunas import aviso_lacuna


@st.cache_data(show_spinner=False)
def _ler(caminho: str, mtime: float) -> list[dict]:
    del mtime  # só invalida o cache quando o arquivo muda
    return c20.ler(caminho)


def carregar(caminho: Path = c20.REGISTRO) -> list[dict] | None:
    """Registros da cadeia, ou None se o arquivo não existe ou não se lê."""
    p = Path(caminho)
    if not p.exists():
        return None
    try:
        return _ler(str(p), p.stat().st_mtime)
    except (OSError, ValueError):
        return None


def _data(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:  # registro adulterado: a cadeia já acusa, a tela não cai
        return f"inválida ({iso})"


def _momento(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso)).astimezone(c20.FUSO_B3).strftime(
            "%d/%m/%Y %H:%M")
    except ValueError:
        return f"inválido ({iso})"


def _curto(h: str | None) -> str:
    return f"{h[:12]}…" if h else "—"


_COMPARACOES = (("etf", "Contra o ETF"), ("cdi", "Contra o CDI"),
                ("pesos_iguais", "Contra pesos iguais"))


def _frase(texto: str) -> str:
    """Texto do protocolo terminado em um ponto só."""
    return texto.rstrip(". ") + "."


def proxima_revisao(registros: list[dict], hoje: date) -> dict | None:
    """Primeira formação do calendário ainda sem revisão.

    Fica visível até o prazo (abertura do pregão de execução) e, passado ele sem
    registro, continua visível marcada ``atrasada`` -- nunca é pulada em silêncio.
    """
    proto = c20.protocolo(registros) or {}
    feitas = {r["corpo"].get("data_formacao") for r in c20.revisoes(registros)}
    for c in (proto.get("calendario") or {}).get("planejado") or []:
        if c["data_formacao"] not in feitas:
            return {**c, "atrasada": hoje > date.fromisoformat(c["pregao_execucao"])}
    return None


def _secao_cadeia(registros: list[dict], status: c20.StatusCadeia) -> None:
    if status.integra:
        badge_status(f"Cadeia íntegra: {status.n} registros, último hash "
                     f"{_curto(status.ultimo_hash)}", "sucesso")
    else:
        badge_status(f"Cadeia QUEBRADA: {len(status.erros)} problema(s)", "erro")
        for erro in status.erros:
            st.caption(f"⚠️ {erro}")


def _cards(registros: list[dict], hoje: date) -> None:
    atual = c20.vigente(registros)
    prox = proxima_revisao(registros, hoje)
    c1, c2, c3 = st.columns(3)
    with c1:
        if atual:
            corpo = atual["corpo"]
            card_metrica("Revisão vigente", f"Formação {_data(corpo.get('data_formacao'))}",
                         delta=(f"Execução no fechamento de {_data(corpo.get('pregao_execucao'))}"
                                + (" · ATRASADA" if corpo.get("atrasada") else "")),
                         positivo=None,
                         ajuda="As 20 maiores pontuações do universo, a 5% cada.")
        else:
            card_metrica("Revisão vigente", "Nenhuma", positivo=None)
    with c2:
        card_metrica("Próxima revisão",
                     f"Formação {_data(prox['data_formacao'])}" if prox else "—",
                     delta=(None if not prox else
                            f"ATRASADA: o prazo era a abertura de {_data(prox['pregao_execucao'])}"
                            if prox["atrasada"] else
                            f"Registro antes da abertura de {_data(prox['pregao_execucao'])}"),
                     positivo=None,
                     ajuda="Último pregão de abril e de outubro; o calendário está no protocolo.")
    with c3:
        n_rev = len(c20.revisoes(registros))
        card_metrica("Revisões registradas", str(n_rev),
                     delta="Conclusão só com 12 meses; veredito com 24",
                     positivo=None,
                     ajuda="Critério fixado no protocolo antes da primeira revisão.")


def _tabela_carteira(corpo: dict) -> pd.DataFrame:
    return pd.DataFrame([{"#": i, "Ticker": c.get("ticker"), "Pontuação": c.get("pontuacao"),
                          "Peso-alvo": c.get("peso_alvo")}
                         for i, c in enumerate(corpo.get("carteira") or [], start=1)])


def _secao_carteira(registros: list[dict]) -> None:
    atual = c20.vigente(registros)
    secao_titulo("Carteira vigente", "📒",
                 "Registrada antes da abertura do pregão em que passa a valer")
    if not atual:
        estado_vazio("Nenhuma revisão registrada ainda.", "📒")
        return
    corpo = atual["corpo"]
    df = _tabela_carteira(corpo)
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={
                     "Pontuação": st.column_config.NumberColumn(
                         format="%.4f", help="Média dos postos dos 4 modelos e do momento 12-2."),
                     "Peso-alvo": st.column_config.NumberColumn(format="percent")})
    universo = (corpo.get("universo") or {}).get("n")
    st.caption(
        f"Escolhidas entre {universo or '—'} ações líquidas na formação de "
        f"{_data(corpo.get('data_formacao'))}. Entre revisões, os aportes vão para as "
        "ações mais abaixo do peso-alvo, sem venda. Acompanhamento de pesquisa, não "
        "recomendação de compra ou venda.")
    if atual["correcoes"]:
        st.caption(f"Esta revisão tem {len(atual['correcoes'])} correção(ões) registrada(s); "
                   "a tabela mostra o corpo corrigido.")


def _tabela_historico(registros: list[dict]) -> pd.DataFrame:
    linhas = []
    for r in registros:
        corpo = r.get("corpo") or {}
        if r.get("tipo") == "protocolo":
            resumo = f"Receita {corpo.get('versao_receita') or '—'} congelada"
        elif r.get("tipo") == "revisao":
            resumo = (f"Formação {_data(corpo.get('data_formacao'))} · execução "
                      f"{_data(corpo.get('pregao_execucao'))}"
                      + (" · atrasada" if corpo.get("atrasada") else ""))
        else:
            ref = corpo.get("referencia") or {}
            resumo = f"Corrige o registro {ref.get('seq')}: {corpo.get('motivo') or '—'}"
        linhas.append({"Seq": r.get("seq"), "Tipo": r.get("tipo"), "Registrado em":
                       _momento(r.get("registrado_em")), "Resumo": resumo,
                       "Hash": r.get("hash"), "Hash anterior": r.get("hash_anterior") or "—"})
    return pd.DataFrame(linhas)


def _secao_historico(registros: list[dict]) -> None:
    secao_titulo("Histórico do registro", "🔗",
                 "Cada registro carrega o hash do anterior; nada é editado depois")
    st.dataframe(_tabela_historico(registros), hide_index=True, width="stretch")
    st.caption(
        "O hash é o SHA-256 da forma canônica do registro. Mudar um byte de um registro "
        "antigo quebra o hash dele e o elo do seguinte, e o teste do repositório fixa os "
        "hashes já publicados. Uma correção é um registro novo que cita o corrigido.")


def primeiro_fechamento(registros: list[dict]) -> date | None:
    """Último pregão do mês seguinte à execução da primeira revisão."""
    rv = c20.revisoes(registros)
    if not rv:
        return None
    try:
        execucao = date.fromisoformat(rv[0]["corpo"]["pregao_execucao"])
    except (KeyError, TypeError, ValueError):
        return None
    ano, mes = (execucao.year + 1, 1) if execucao.month == 12 else (execucao.year,
                                                                   execucao.month + 1)
    return c20.ultimo_pregao_do_mes(ano, mes)


def _secao_desempenho(registros: list[dict], hoje: date) -> None:
    proto = c20.protocolo(registros) or {}
    medicao = proto.get("medicao") or {}
    secao_titulo("Desempenho", "📈", "Mensal, contra BOVA11, CDI e a carteira de pesos iguais")
    fecha = primeiro_fechamento(registros)
    if fecha is None or hoje <= fecha:
        estado_vazio("Nenhum mês fechado ainda"
                     + (f": o primeiro fecha em {fecha.strftime('%d/%m/%Y')}." if fecha else "."),
                     "⏳")
    else:
        badge_status(f"O primeiro mês fechou em {fecha.strftime('%d/%m/%Y')} e a medição "
                     "ainda não foi publicada", "alerta")
        aviso_lacuna("Carteira 20: mês fechado sem medição publicada.",
                     codigo="tela.b3.carteira20.medicao_pendente")
    comp = medicao.get("comparacoes") or {}
    for chave, rotulo in _COMPARACOES:
        if comp.get(chave):
            st.caption(f"{rotulo}: {_frase(comp[chave])}")
    if medicao.get("ir"):
        st.caption(f"Imposto: {_frase(medicao['ir'])}")


def _secao_expectativa(registros: list[dict]) -> None:
    proto = c20.protocolo(registros) or {}
    exp, crit = proto.get("expectativa") or {}, proto.get("criterio") or {}
    if not exp:
        return
    secao_titulo("O que a pesquisa espera", "🧪",
                 "Promissor no laboratório, não comprovado: é para isso que existe o registro")
    st.markdown(f"**Expectativa:** {_frase(exp.get('resumo') or '—')}")
    linhas = [{"Período": f.get("periodo"), "Rank-IC": f.get("ic"),
               "Excesso a.a. sobre pesos iguais": f.get("excesso_aa"), "t": f.get("t"),
               "Leitura": f.get("leitura") or f.get("pior_ano") or "—"}
              for f in exp.get("fora_da_amostra") or []]
    if linhas:
        st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch",
                     column_config={
                         "Rank-IC": st.column_config.NumberColumn(format="%.3f"),
                         "Excesso a.a. sobre pesos iguais": st.column_config.NumberColumn(
                             format="percent")})
    if exp.get("contra_o_indice"):
        st.caption(f"Contra o índice: {_frase(exp['contra_o_indice'])}")
    for chave, rotulo in (("minimo", "Mínimo"), ("veredito", "Veredito"),
                          ("mudanca", "Mudanças")):
        if crit.get(chave):
            st.caption(f"{rotulo}: {_frase(crit[chave])}")


def render(show_header: bool = False, hoje: date | None = None) -> None:
    del show_header
    registros = carregar()
    if not registros:
        aviso_lacuna("Registro da Carteira 20 ausente ou ilegível "
                     f"({c20.REGISTRO.name}); rode scripts/carteira20_revisao.py.",
                     codigo="tela.b3.carteira20.registro_ausente")
        estado_vazio("A Carteira 20 ainda não tem registro publicado.", "📒")
        return
    hoje = hoje or datetime.now(c20.FUSO_B3).date()
    secao_titulo("Carteira 20", "📒",
                 "20 ações da B3, revisão semestral, previsões gravadas antes dos retornos")
    _secao_cadeia(registros, c20.verificar_cadeia(registros))
    _cards(registros, hoje)
    _secao_carteira(registros)
    _secao_desempenho(registros, hoje)
    _secao_expectativa(registros)
    _secao_historico(registros)
