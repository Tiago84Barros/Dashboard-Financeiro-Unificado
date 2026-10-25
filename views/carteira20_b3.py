"""Aba 📒 Carteira 20 (Empresas B3).

Só lê o registro append-only ``data/public/carteira20_registro.jsonl`` que
``scripts/carteira20_revisao.py`` grava e o desempenho diário
``data/public/carteira20_desempenho.json`` que ``scripts/carteira20_desempenho.py``
publica; nada é treinado nem calculado aqui. A tela mostra a carteira vigente
em cards, o caminho desde a execução contra os comparadores, o histórico das
revisões com os hashes, o estado da cadeia e o que a pesquisa espera -- sem
antecipar conclusão: o protocolo proíbe qualquer veredito antes de 12 meses de
registro.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core.carteira20 as c20
import core.carteira20_desempenho as cd
from design.componentes import badge_status, card_metrica, estado_vazio, secao_titulo
from design.lacunas import aviso_lacuna
from design.market_companies import company_logo_html

DESEMPENHO = c20.REGISTRO.parent / "carteira20_desempenho.json"
_LOGOS = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"
# O publicador roda à noite sobre o pregão do dia; com fim de semana e feriado,
# 5 dias corridos sem pregão novo ainda é série em dia.
_FOLGA_DIAS = 5


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


@st.cache_data(show_spinner=False)
def _ler_desempenho(caminho: str, mtime: float) -> dict:
    del mtime
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_desempenho(caminho: Path = DESEMPENHO) -> dict | None:
    """Artefato do desempenho diário, ou None se ainda não foi publicado."""
    p = Path(caminho)
    if not p.exists():
        return None
    try:
        return _ler_desempenho(str(p), p.stat().st_mtime)
    except (OSError, ValueError):
        return None


def desempenho_da_vigente(registros: list[dict], desempenho: dict | None) -> dict | None:
    """O artefato só vale para a revisão de que ele fala (mesmo hash)."""
    atual = c20.vigente(registros)
    if not (atual and desempenho and desempenho.get("pregoes")):
        return None
    if (desempenho.get("revisao") or {}).get("hash") != atual["registro"].get("hash"):
        return None
    return desempenho


def _pct(v: float | None, sinal: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v * 100:{'+' if sinal else ''}.1f}%".replace(".", ",")


def _reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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


_CSS_CARDS = """<style>
.c20-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:12px;
            margin:4px 0 10px; }
.c20-grid .b3-card { min-height:0;padding:12px 13px 11px; }
.c20-rank { margin-left:auto;align-self:flex-start;font-size:.68rem;font-weight:800;
            color:var(--app-info);border:1px solid var(--app-border-strong);border-radius:999px;
            padding:1px 8px;background:var(--app-surface-raised);white-space:nowrap; }
.c20-mets { display:grid;grid-template-columns:1fr 1fr;gap:6px 10px;margin-top:8px;
            padding-top:8px;border-top:1px solid var(--app-border); }
.c20-lbl  { font-size:.60rem;color:var(--app-muted);text-transform:uppercase;letter-spacing:.04em; }
.c20-val  { font-size:.84rem;font-weight:700;color:var(--app-text);font-variant-numeric:tabular-nums; }
.c20-val.c20-pos { color:var(--app-primary); }
.c20-val.c20-neg { color:var(--app-danger); }
.c20-val.c20-nd  { color:var(--app-subtle);font-weight:600; }
</style>"""


def _ticker(t: object) -> str:
    return str(t or "").strip().upper().replace(".SA", "")


def cadastro_por_ticker(cadastro: pd.DataFrame | None) -> dict[str, dict]:
    """Nome e setor por ticker, do cadastro que Empresas B3 já carregou."""
    if cadastro is None or cadastro.empty or "ticker" not in cadastro.columns:
        return {}
    saida = {}
    for linha in cadastro.to_dict("records"):
        partes = [str(linha.get(c) or "").strip() for c in ("SETOR", "SUBSETOR")]
        saida[_ticker(linha["ticker"])] = {
            "nome": str(linha.get("nome_empresa") or "").strip(),
            "setor": " · ".join(p for p in partes if p and p.lower() != "nan")}
    return saida


def _metrica(rotulo: str, valor: str, classe: str = "") -> str:
    return (f'<div><div class="c20-lbl">{html.escape(rotulo)}</div>'
            f'<div class="c20-val {classe}">{html.escape(valor)}</div></div>')


def cards_carteira(corpo: dict, cadastro: dict[str, dict], desempenho: dict | None) -> str:
    """Grade de cards da carteira vigente, num bloco só de HTML."""
    por_id = {a["id"]: a for a in (desempenho or {}).get("ativos") or []}
    ate = _data(desempenho["dados_ate"])[:5] if desempenho else ""
    cards = []
    for i, c in enumerate(corpo.get("carteira") or [], start=1):
        tk = _ticker(c.get("ticker"))
        info = cadastro.get(tk) or {}
        ativo = por_id.get(c.get("id"))
        if ativo:
            ret = ativo.get("retorno") or 0.0
            classe = "c20-pos" if ret > 0 else "c20-neg" if ret < 0 else ""
            peso = _metrica(f"Peso em {ate}", _pct(ativo.get("peso_atual")))
            retorno = _metrica("Desde a execução", _pct(ret, sinal=True), classe)
        else:
            peso = _metrica("Peso atual", "aguardando", "c20-nd")
            retorno = _metrica("Desde a execução", "1º pregão", "c20-nd")
        pontuacao = c.get("pontuacao")
        cards.append(
            '<div class="b3-card"><div class="b3-card-head">'
            f'{company_logo_html(tk, f"{_LOGOS}/{tk}.png")}'
            f'<div class="b3-card-idt"><div class="b3-card-ticker">{html.escape(tk)}</div>'
            f'<div class="b3-card-nome">{html.escape(info.get("nome") or "—")}</div></div>'
            f'<span class="c20-rank">#{i}</span></div>'
            f'<div class="b3-card-tag">{html.escape(info.get("setor") or "Setor não cadastrado")}'
            '</div><div class="c20-mets">'
            + _metrica("Pontuação",
                       "—" if pontuacao is None else f"{pontuacao:.3f}".replace(".", ","))
            + _metrica("Peso-alvo", _pct(c.get("peso_alvo")))
            + peso + retorno + "</div></div>")
    return _CSS_CARDS + '<div class="c20-grid">' + "".join(cards) + "</div>"


def _secao_carteira(registros: list[dict], cadastro: dict[str, dict],
                    desempenho: dict | None) -> None:
    atual = c20.vigente(registros)
    secao_titulo("Carteira vigente", "📒",
                 "Registrada antes da abertura do pregão em que passa a valer")
    if not atual:
        estado_vazio("Nenhuma revisão registrada ainda.", "📒")
        return
    corpo = atual["corpo"]
    st.markdown(cards_carteira(corpo, cadastro, desempenho), unsafe_allow_html=True)
    universo = (corpo.get("universo") or {}).get("n")
    st.caption(
        f"Ordem e pontuação da formação de {_data(corpo.get('data_formacao'))}: média dos "
        f"postos dos 4 modelos e do momento 12-2, entre {universo or '—'} ações líquidas. "
        "O retorno é total (proventos reinvestidos), sem custos; o peso atual é quanto a "
        "posição comprada a 5% representa hoje. Entre revisões, os aportes vão para as ações "
        "mais abaixo do peso-alvo, sem venda. Acompanhamento de pesquisa, não recomendação de "
        "compra ou venda.")
    if atual["correcoes"]:
        st.caption(f"Esta revisão tem {len(atual['correcoes'])} correção(ões) registrada(s); "
                   "os cards mostram o corpo corrigido.")


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


_SERIES = (("carteira20", "Carteira 20", "#00C896", 2.8, "solid"),
           ("pesos_iguais", "Pesos iguais do universo", "#F6C90E", 1.8, "solid"),
           ("bova11", "BOVA11", "#4A9EFF", 1.8, "solid"),
           ("cdi", "CDI", "#9CA3AF", 1.6, "dash"))


def figura_desempenho(desempenho: dict) -> go.Figure:
    """Retorno líquido acumulado de cada curva: diário no 1º ano, mensal depois."""
    datas = desempenho["datas"]
    idx = cd.amostrar(datas)
    x = [datas[i] for i in idx]
    modo = "lines+markers" if len(idx) <= 40 else "lines"
    fig = go.Figure()
    for chave, rotulo, cor, largura, traco in _SERIES:
        valores = (desempenho.get("curvas") or {}).get(chave)
        if not valores:
            continue
        fig.add_trace(go.Scatter(
            x=x, y=cd.retorno_acumulado([valores[i] for i in idx]), name=rotulo, mode=modo,
            line={"color": cor, "width": largura, "dash": traco}, marker={"size": 5},
            hovertemplate=f"{rotulo}: %{{y:+.2%}}<extra></extra>"))
    fig.add_hline(y=0, line={"color": "#64748B", "width": 1, "dash": "dot"})
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#9CA3AF",
        height=360, margin={"t": 10, "b": 10, "l": 0, "r": 0}, hovermode="x unified",
        legend={"orientation": "h", "y": -0.18, "bgcolor": "rgba(0,0,0,0)", "font": {"size": 11}},
        yaxis={"showgrid": True, "gridcolor": "#1E2533", "tickformat": "+.0%",
               "title": "Retorno líquido sobre R$ 1.000"},
        xaxis={"showgrid": False, "tickformat": "%d/%m/%y"})
    return fig


def _cards_desempenho(desempenho: dict) -> None:
    finais = {k: v[-1] for k, v in (desempenho.get("curvas") or {}).items() if v}
    base = finais.get("carteira20")
    for col, (chave, rotulo, _cor, _l, _t) in zip(st.columns(len(_SERIES)), _SERIES):
        v = finais.get(chave)
        with col:
            if v is None:
                card_metrica(rotulo, "—", positivo=None)
                continue
            if chave == "carteira20" or base is None:
                delta, positivo = None, None
            else:
                dif = _pct((base - v) / cd.CAPITAL, sinal=True).replace("%", " p.p.")
                delta, positivo = f"Carteira 20 {dif}", base >= v
            card_metrica(rotulo, _pct(v / cd.CAPITAL - 1, sinal=True), delta=delta,
                         positivo=positivo,
                         ajuda=f"{_reais(v)} líquidos de custo e IR, vendendo no último "
                               "fechamento.")


def _secao_desempenho(registros: list[dict], hoje: date, desempenho: dict | None) -> None:
    proto = c20.protocolo(registros) or {}
    medicao = proto.get("medicao") or {}
    execucao = ((c20.vigente(registros) or {}).get("corpo") or {}).get("pregao_execucao")
    secao_titulo("Desempenho", "📈",
                 "Desde o fechamento da execução, contra BOVA11, CDI e pesos iguais do universo")
    badge_status("Período curto demais para concluir qualquer coisa: o protocolo só lê o "
                 "resultado com 12 meses e só dá veredito com 24", "alerta")
    if desempenho:
        dados_ate = date.fromisoformat(desempenho["dados_ate"])
        if (hoje - dados_ate).days > _FOLGA_DIAS:
            badge_status(f"Dados até {dados_ate:%d/%m/%Y}: o desempenho diário não é "
                         "publicado desde então", "alerta")
            aviso_lacuna(f"Carteira 20: desempenho diário parado em {dados_ate:%d/%m/%Y}.",
                         codigo="tela.b3.carteira20.desempenho_parado")
        _cards_desempenho(desempenho)
        st.plotly_chart(figura_desempenho(desempenho), width="stretch",
                        config={"displayModeBar": False})
        base = (desempenho.get("revisao") or {}).get("pregao_execucao") or execucao
        # "\$": dois cifrões no mesmo texto viram fórmula LaTeX no markdown do Streamlit
        st.caption(
            f"{desempenho['pregoes']} pregão(ões) desde o fechamento de {_data(base)}, "
            f"dados até {dados_ate:%d/%m/%Y}. Cada curva é R\\$ 1.000 aplicados no fechamento "
            "da execução e o que sobraria vendendo tudo no fechamento do dia: ações e BOVA11 "
            "pagam 0,30% na compra e na venda; BOVA11 e CDI pagam 15% de IR sobre o ganho; a "
            "venda de até R\\$ 20 mil de ações no mês é isenta. Pesos iguais, sem aporte nem "
            "rebalanceamento até a próxima revisão.")
        for aviso in desempenho.get("avisos") or []:
            st.caption(f"⚠️ {_frase(aviso[:1].upper() + aviso[1:])}")
    elif execucao and hoje <= date.fromisoformat(execucao):
        estado_vazio(f"A carteira passa a valer no fechamento de {_data(execucao)}; o gráfico "
                     "começa no primeiro pregão seguinte.", "⏳")
    elif execucao:
        estado_vazio("O desempenho diário desde a execução ainda não foi publicado.", "⏳")
        aviso_lacuna("Carteira 20: desempenho diário não publicado "
                     f"({DESEMPENHO.name}); rode scripts/carteira20_desempenho.py.",
                     codigo="tela.b3.carteira20.desempenho_ausente")

    st.markdown("**Medição mensal do protocolo**")
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


def render(show_header: bool = False, hoje: date | None = None,
           cadastro: pd.DataFrame | None = None) -> None:
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
    desempenho = desempenho_da_vigente(registros, carregar_desempenho())
    _secao_carteira(registros, cadastro_por_ticker(cadastro), desempenho)
    _secao_desempenho(registros, hoje, desempenho)
    _secao_expectativa(registros)
    _secao_historico(registros)
