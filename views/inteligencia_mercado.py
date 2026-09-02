"""Inteligência de Mercado: notícias, crise, memória e antifragilidade.

O que esta tela faz e o que ela não faz
---------------------------------------
Ela **não calcula nada**. Todo número vem de ``core.inteligencia.painel``, que
por sua vez só traduz o que os motores já produziram. A LLM, quando existe,
apenas explica o painel -- e a explicação dela passa por
``core.inteligencia.llm.validar`` antes de aparecer aqui.

Coleta é sob demanda, e o silêncio se declara
---------------------------------------------
A tela não sai buscando notícia sozinha ao abrir: rede na renderização
transforma provedor lento em página travada. A coleta acontece no botão
"Atualizar agora" e fica na sessão. Enquanto não acontecer, a seção de notícias
aparece marcada como **não coletada nesta sessão** -- e não como "sem notícias",
porque as duas coisas não são a mesma e confundi-las faz falha de coleta passar
por calmaria.

Reaproveitada pela aba de empresas
----------------------------------
:func:`render_fundamentos_cenario` é o bloco "Fundamentos + Cenário" de um
ativo. Ela existe separada para ``views/empresas_b3.py`` poder chamá-la sem
importar a tela inteira.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging

import pandas as pd
import streamlit as st

from core.eventos_extremos import antifragilidade as af
from core.inteligencia import alertas as al
from core.inteligencia import llm as intel_llm
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz
from design import inteligencia as ui
from design.componentes import (abas_secao, container_pagina, estado_vazio,
                                secao_titulo)

logger = logging.getLogger(__name__)

CHAVE_COLETA = "inteligencia_coleta"
CHAVE_PREFS = "inteligencia_prefs_alerta"
CHAVE_ALERTAS = "inteligencia_alertas"

MSG_SEM_COLETA = (
    "Nenhuma coleta de notícias foi executada nesta sessão. Isto não significa "
    "que não há notícias — significa que ainda não olhamos."
)

ABA_NOTICIAS = "📰 Notícias"
ABA_CRISE = "🛡️ Crise"
ABA_ANTIFRAGIL = "🧱 Antifragilidade"
ABA_MEMORIA = "🕰️ Memória de mercado"
ABA_EMPRESAS = "🏢 Fundamentos + Cenário"
ABA_EXPLICACAO = "💬 Explicação"
ABA_ALERTAS = "🔔 Alertas"


# ── Carregamento ─────────────────────────────────────────────────────────────
def carregar_posicoes() -> tuple[pd.DataFrame, str]:
    """Posições consolidadas da carteira, ou um quadro vazio com o motivo."""
    try:
        from core.global_portfolio.aggregate import montar_posicoes
        from core.portfolio.repository import load_allocation_targets
        from views.portfolio_global import carregar_snapshots

        snapshots = carregar_snapshots()
        alocacao = load_allocation_targets() or {}
        alvos = alocacao.get("targets") or {}
        df = montar_posicoes(snapshots, alvos,
                             total_brl=alocacao.get("total_brl"))
        if df is None or df.empty:
            return pd.DataFrame(), "nenhuma posição consolidada disponível"
        return df, ""
    except Exception as exc:  # noqa: BLE001 — a tela precisa abrir sem carteira
        logger.exception("falha ao carregar posições para a inteligência")
        return pd.DataFrame(), f"carteira indisponível: {type(exc).__name__}"


def situacao_dos_provedores() -> tuple[qz.Provedor, ...]:
    """Estado dos provedores sem tocar a rede e sem revelar chave."""
    try:
        from core.noticias.provedores import registro
        return tuple(
            qz.Provedor(nome=s.nome, disponivel=bool(s.disponivel),
                        detalhe=s.motivo or "")
            for s in registro.descrever())
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha ao descrever provedores de notícias")
        return (qz.Provedor(
            nome="notícias", disponivel=False,
            detalhe=f"registro indisponível: {type(exc).__name__}"),)


def coletar_noticias(tickers: tuple[str, ...]):
    """Executa a coleta. Só é chamada pelo botão de atualização manual."""
    try:
        from core.noticias import coleta
        from core.noticias.provedores import registro
        from core.noticias.provedores.base import Consulta

        provedores = registro.construir()
        if not provedores:
            return None, "nenhum provedor de notícias configurado"
        consulta = Consulta(tickers=tuple(tickers)[:20], limite=50)
        return coleta.coletar(consulta, provedores), ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("coleta de notícias falhou")
        return None, f"a coleta falhou: {type(exc).__name__}"


def carregar_acervo(limite: int = 50):
    """Notícias já gravadas pelo coletor automático, com o carimbo mais novo.

    Devolve ``(itens, atualizado_em, motivo)``. Acervo vazio não é erro: pode
    ser um app recém-instalado. O motivo distingue os dois casos para a tela
    não chamar de "nada coletado" o que na verdade foi "não consegui ler".
    """
    try:
        from core.noticias.armazenamento import ler_recentes
    except Exception as exc:  # noqa: BLE001
        return (), None, f"acervo indisponível ({type(exc).__name__})"

    linhas = ler_recentes(limite=limite)
    if not linhas:
        return (), None, ""
    itens = [P.item_de_linha(linha) for linha in linhas]
    carimbos = [linha.get("coletado_em") for linha in linhas
                if linha.get("coletado_em") is not None]
    return tuple(itens), (max(carimbos) if carimbos else None), ""


def montar_painel(*, agora: dt.datetime | None = None) -> P.Painel:
    """Reúne o que existir e entrega o painel. Nada aqui é obrigatório."""
    agora = agora or dt.datetime.now(dt.timezone.utc)
    posicoes, motivo_carteira = carregar_posicoes()

    indice = None
    if not posicoes.empty:
        try:
            indice = af.calcular(posicoes)
        except Exception:  # noqa: BLE001
            logger.exception("falha ao calcular antifragilidade")

    frescor = [qz.Frescor(
        "Carteira", atualizado_em=agora if not posicoes.empty else None,
        disponivel=not posicoes.empty, erro=motivo_carteira,
        validade_horas=P.VALIDADE_PADRAO_HORAS["carteira"])]

    coletado = st.session_state.get(CHAVE_COLETA)
    noticias: list[P.ItemNoticia] = []
    extras: list[str] = []
    if coletado is None:
        # A sessão não coletou -- mas o job do cron pode ter coletado horas
        # atrás. Ler o acervo é o que impede a tela de apresentar trabalho
        # feito como trabalho ausente.
        acervo, quando, motivo = carregar_acervo()
        if acervo:
            noticias = list(acervo)
            frescor.append(qz.Frescor(
                "Notícias", atualizado_em=quando,
                validade_horas=P.VALIDADE_PADRAO_HORAS["noticias"]))
            extras.append(
                "notícias vindas do acervo da coleta automática; "
                "esta sessão não consultou os provedores")
        else:
            frescor.append(qz.Frescor("Notícias", atualizado_em=None,
                                      disponivel=False,
                                      erro=motivo or MSG_SEM_COLETA))
    else:
        resultado, erro = coletado
        if resultado is None:
            frescor.append(qz.Frescor("Notícias", atualizado_em=None,
                                      disponivel=False, erro=erro))
        else:
            noticias = [P.item_de_avaliada(a) for a in resultado.avaliadas]
            frescor.append(qz.Frescor(
                "Notícias", atualizado_em=resultado.coletado_em,
                validade_horas=P.VALIDADE_PADRAO_HORAS["noticias"]))
            extras.extend(resultado.limitacoes)

    pn = P.montar(indice=indice, noticias=noticias,
                  provedores=situacao_dos_provedores(),
                  frescor=frescor, agora=agora)
    if extras:
        pn = dataclasses.replace(pn, limitacoes=pn.limitacoes + tuple(extras))
    return pn


# ── Seções ───────────────────────────────────────────────────────────────────
def _preferencias() -> al.Preferencias:
    return st.session_state.get(CHAVE_PREFS) or al.Preferencias()


def render_configuracao_de_alertas() -> None:
    """Canais e severidade. Externo exige autorização explícita, sempre."""
    prefs = _preferencias()
    secao_titulo("Alertas", "🔔",
                 "Nível 1 fica no painel. Nível 2 avisa quando toca a carteira. "
                 "Níveis 3 e 4 usam canal externo apenas com sua autorização.")

    severidade = st.select_slider(
        "Severidade mínima para notificar", options=[0, 1, 2, 3, 4],
        value=int(prefs.severidade_minima),
        format_func=lambda n: f"Nível {n}")
    so_carteira = st.checkbox(
        "Notificar apenas eventos que tocam minha carteira",
        value=bool(prefs.so_se_afetar_carteira))
    canais = st.multiselect("Canais externos configurados",
                            options=["telegram", "email"],
                            default=list(prefs.canais_externos))
    autorizou = st.checkbox(
        "Autorizo o envio de alertas para os canais externos acima",
        value=bool(prefs.autorizou_externo),
        help="Mesmo autorizado, o alerta externo carrega apenas nível, tipo de "
             "evento e abrangência. Nenhum dado da sua carteira sai daqui.")

    st.session_state[CHAVE_PREFS] = al.Preferencias(
        severidade_minima=int(severidade),
        canais_externos=tuple(canais),
        autorizou_externo=bool(autorizou),
        so_se_afetar_carteira=bool(so_carteira))

    st.caption(
        "O que sai num alerta externo, na íntegra: nível, rótulo do nível, "
        "tipo de evento, abrangência e data. Nunca o símbolo de um ativo, o "
        "peso na carteira, o valor em reais ou a prioridade de aporte.")

    historico = st.session_state.get(CHAVE_ALERTAS) or ()
    if historico:
        st.markdown("**Alertas ativos e histórico**")
        for alerta in historico:
            ui.cartao_alerta(alerta)
    else:
        estado_vazio("Nenhum alerta foi gerado nesta sessão.", "🔕")


def render_noticias(pn: P.Painel) -> None:
    secao_titulo("Notícias relevantes", "📰",
                 "Cada item traz fonte, data, hora e estado de verificação.")

    if not pn.noticias:
        estado_vazio(MSG_SEM_COLETA, "📭")
        return

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        f_texto = st.text_input("Empresa ou ticker", "")
    with col2:
        setores = sorted({s for n in pn.noticias for s in n.setores})
        f_setor = st.selectbox("Setor", ["(todos)"] + setores)
    with col3:
        paises = sorted({p for n in pn.noticias for p in n.paises})
        f_pais = st.selectbox("País", ["(todos)"] + paises)
    with col4:
        f_conf = st.selectbox("Verificação",
                              ["(todas)", "somente confirmadas",
                               "somente não confirmadas"])

    comuns = dict(
        setor=None if f_setor == "(todos)" else f_setor,
        pais=None if f_pais == "(todos)" else f_pais,
        confirmadas=(True if f_conf == "somente confirmadas"
                     else False if f_conf == "somente não confirmadas"
                     else None))
    itens = P.filtrar(pn.noticias, ticker=f_texto or None, **comuns)
    if f_texto and not itens:
        itens = P.filtrar(pn.noticias, empresa=f_texto, **comuns)

    if not itens:
        estado_vazio("Nenhuma notícia atende aos filtros escolhidos.", "🔎")
        return

    # Eventos iguais ficam agrupados: um bloco por tipo de evento.
    grupos: dict[str, list[P.ItemNoticia]] = {}
    for it in itens:
        grupos.setdefault(it.tipo_evento or "sem classificação", []).append(it)

    for tipo, doss in grupos.items():
        st.markdown(f"**{tipo.replace('_', ' ')}** — {len(doss)} notícia(s)")
        for inicio in range(0, len(doss), 2):
            cols = st.columns(2)
            for col, item in zip(cols, doss[inicio:inicio + 2]):
                with col:
                    ui.cartao_noticia(item)


def render_crise(pn: P.Painel) -> None:
    if pn.crise is None:
        estado_vazio("Nenhuma avaliação de crise disponível.", "🛡️")
        return
    secao_titulo("Situação de crise", "🛡️")
    ui.bloco_completo(pn.crise, agora=pn.gerado_em)
    st.caption("Ações são sugestões para decisão humana. Nenhuma operação é "
               "executada automaticamente, e nenhuma sugestão vira ordem.")


def render_antifragilidade(pn: P.Painel) -> None:
    if pn.antifragilidade is None:
        estado_vazio("Índice de antifragilidade não calculado.", "🧱")
        return
    secao_titulo("Antifragilidade da carteira", "🧱")
    ui.bloco_completo(pn.antifragilidade, agora=pn.gerado_em)
    ui.aviso_sem_garantia()


def render_memoria(pn: P.Painel) -> None:
    if pn.memoria is None:
        estado_vazio("Sem memória de mercado para o evento atual.", "🕰️")
        return
    secao_titulo("Memória de mercado", "🕰️")
    ui.bloco_completo(pn.memoria, agora=pn.gerado_em)
    st.caption("Comportamento passado não garante o futuro. A faixa é dispersão "
               "observada, não previsão.")


def render_explicacao(pn: P.Painel, *, simbolo: str | None = None) -> None:
    """A explicação em linguagem simples, com a área técnica ao lado."""
    secao_titulo("O que isso quer dizer", "💬")
    exp = intel_llm.explicar(pn, simbolo=simbolo)
    origem = ("gerada por LLM e validada contra o painel"
              if exp.gerada_por_llm else "gerada pelo backend, sem LLM")
    st.caption(f"Explicação {origem}.")
    st.markdown(exp.texto)
    if exp.validacao is not None and not exp.validacao.aprovada:
        st.warning("A resposta da LLM foi **descartada**: "
                   + exp.validacao.descrever())
    with st.expander("Área técnica — contexto exato entregue à LLM"):
        st.caption("A LLM recebe apenas este texto. Número que não estiver "
                   "aqui é recusado antes de chegar à tela.")
        st.code(exp.contexto, language="text")
    ui.aviso_sem_garantia()


def render_fundamentos_cenario(bloco: P.BlocoEmpresa,
                               agora: dt.datetime | None = None) -> None:
    """Fundamentos + Cenário de um ativo. Reutilizável por outras telas."""
    st.markdown(
        '<div class="app-section-heading">'
        f'<span class="app-section-title">{bloco.simbolo}</span>'
        + ui.selo_situacao(bloco.situacao) + "</div>",
        unsafe_allow_html=True)
    if bloco.bloco.explicacao_simples:
        st.caption(bloco.bloco.explicacao_simples)

    ui.grade_valores(bloco.bloco.valores, colunas=3)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**O que mudou**")
        for linha in bloco.o_que_mudou or ("Nada mudou desde a última leitura.",):
            st.markdown(f"- {linha}")
        st.markdown("**Evidências que sustentam**")
        for linha in bloco.evidencias or ("Nenhuma evidência registrada.",):
            st.markdown(f"- {linha}")
    with col2:
        st.markdown("**O que invalidaria esta análise**")
        for linha in bloco.invalidariam:
            st.markdown(f"- {linha}")

    ui.area_tecnica(bloco.bloco)
    ui.aviso_sem_garantia()


def render_empresas(pn: P.Painel) -> None:
    if not pn.empresas:
        estado_vazio("Nenhum ativo foi reavaliado nesta sessão.", "🏢")
        return
    for bloco in pn.empresas:
        render_fundamentos_cenario(bloco, pn.gerado_em)
        st.divider()


def estado_da_coleta():
    """Estado compartilhado do coletor. A tela lê; quem escreve é o job."""
    try:
        from core.noticias import estado_coleta as ec
        return ec.ler(), ""
    except Exception as exc:  # noqa: BLE001 — a tela abre sem o estado
        logger.exception("estado de coleta ilegível")
        return None, f"estado da coleta indisponível ({type(exc).__name__})"


def bloco_agendamento(estado) -> None:
    """Quem atualiza, quando, e o que já rodou. Sem prometer o que não há."""
    if estado is None or not getattr(estado, "disponivel", False):
        st.caption(
            "⚠ Estado do coletor indisponível: esta tela não sabe dizer "
            "quando foi a última coleta automática.")
        return

    from core.noticias import cadencia as cad

    ritmo = cad.cadencia(estado.modo)
    idade = estado.idade_min()
    quando = ("nunca" if idade is None
              else f"há {idade:.0f} min")
    proximo = (estado.proximo_ciclo_em.astimezone().strftime("%d/%m %H:%M")
               if estado.proximo_ciclo_em else "não previsto")
    st.caption(
        f"Coleta automática — {ritmo.descrever()}. Última bem-sucedida: "
        f"{quando}. Próximo ciclo previsto: {proximo}. "
        f"Situação: {cad.ROTULO_STATUS.get(estado.status, estado.status)}.")
    if not cad.permite_recomendacao_emergencial(estado.status):
        st.caption(
            "⚠ Enquanto a coleta estiver nesta situação, o APP4 não emite "
            "recomendação de emergência apoiada nestes dados — eles continuam "
            "à vista, com o carimbo de idade.")


def render_atualizacao(pn: P.Painel) -> None:
    """Última atualização, provedores e o botão de atualização manual."""
    ui.barra_frescor(pn)

    estado, motivo_estado = estado_da_coleta()
    if motivo_estado:
        st.caption(f"⚠ {motivo_estado}")
    bloco_agendamento(estado)

    from core.noticias import cadencia as cad

    ritmo = cad.cadencia(getattr(estado, "modo", cad.MODO_NORMAL))
    pode, motivo = (True, "")
    if estado is not None and getattr(estado, "disponivel", False):
        pode, motivo = cad.deve_coletar(estado.ultima_tentativa, ritmo)

    # O botão nunca some. Um controle que desaparece deixa o usuário sem saber
    # se a função existe; desabilitado com o motivo escrito diz por que não dá.
    clicou = st.button(
        "🔄 Atualizar notícias agora", type="primary", disabled=not pode,
        help=("Consulta os provedores configurados. Respeita o orçamento de "
              "requisições de cada um." if pode else motivo))
    if not pode:
        st.caption(f"⏳ {motivo}")

    if clicou:
        tickers = tuple(e.simbolo for e in pn.empresas)
        if not tickers:
            posicoes, _ = carregar_posicoes()
            if not posicoes.empty and "symbol" in posicoes.columns:
                tickers = tuple(str(s) for s in posicoes["symbol"].head(20))
        with st.spinner("Consultando os provedores de notícias..."):
            coleta, erro = coletar_noticias(tickers)
        if coleta is None:
            # Falha NÃO apaga a última coleta boa: o painel continua exibindo
            # o que tinha, com a idade à vista, e o erro aparece à parte.
            st.error(f"A atualização não foi concluída: {erro}. "
                     "Os dados exibidos continuam sendo os da última coleta "
                     "bem-sucedida.")
        else:
            st.session_state[CHAVE_COLETA] = (coleta, erro)
            st.rerun()

    for linha in pn.limitacoes:
        st.caption(f"⚠ {linha}")


# ── Entrada ──────────────────────────────────────────────────────────────────
def render() -> None:
    container_pagina(
        "Inteligência de Mercado",
        "Notícias, crise, memória de mercado e resistência da carteira. "
        "Nada aqui é recomendação de compra ou venda.",
        icone="🧭")

    pn = montar_painel()
    render_atualizacao(pn)

    secao = abas_secao(
        [ABA_NOTICIAS, ABA_CRISE, ABA_ANTIFRAGIL, ABA_MEMORIA, ABA_EMPRESAS,
         ABA_EXPLICACAO, ABA_ALERTAS],
        key="inteligencia_mercado")

    if secao == ABA_NOTICIAS:
        render_noticias(pn)
    elif secao == ABA_CRISE:
        render_crise(pn)
    elif secao == ABA_ANTIFRAGIL:
        render_antifragilidade(pn)
    elif secao == ABA_MEMORIA:
        render_memoria(pn)
    elif secao == ABA_EMPRESAS:
        render_empresas(pn)
    elif secao == ABA_EXPLICACAO:
        render_explicacao(pn)
    elif secao == ABA_ALERTAS:
        render_configuracao_de_alertas()
