"""Resumo de valuation na carteira, sem persistência nem envio de posições."""
from datetime import datetime, timezone

import streamlit as st

from core.portfolio_valuations import (
    CLASS_LABELS,
    METRICS,
    METRICS_BY_CLASS,
    aggregate_tesouro,
    aggregate_valuations,
    load_valuation_fundamentals,
    metric_spec,
)
from design.lacunas import aviso_lacuna, detalhe_tecnico


@st.cache_data(ttl=3600, show_spinner=False)
def _load(stocks, fiis):
    data, unavailable = load_valuation_fundamentals(stocks, fiis)
    return data, unavailable, datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')


_FUNDO_CARD = 'var(--app-surface)'
_BORDA_CARD = 'var(--app-border)'


def _card(titulo: str, valor: str, sub: str, cor: str) -> str:
    """Card CSS em UM único bloco — moldura e conteúdo nunca se separam."""
    return (
        f'<div style="background:{_FUNDO_CARD};border:1px solid {_BORDA_CARD};'
        f'border-radius:10px;padding:14px 14px 12px;height:100%;">'
        f'<div style="font-size:0.58rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.12em;color:var(--app-subtle);margin-bottom:6px;">{titulo}</div>'
        f'<div style="font-size:1.35rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:5px;">{valor}</div>'
        f'<div style="font-size:0.68rem;color:var(--app-subtle);line-height:1.3;">{sub}</div>'
        f'</div>'
    )


def _registrar_cobertura(rotulo: str, item: dict, denominador: str) -> None:
    """Contagem de ativos e cobertura da média vão para o log, não para o card."""
    texto = (f"{rotulo}: {item['assets']} ativo(s) · {item['coverage']:.1%} "
             f"do valor da {denominador}")
    if item['value'] is None:
        aviso_lacuna(texto, codigo='tela.carteira.valuation_sem_media', entidade=rotulo)
    else:
        detalhe_tecnico(texto, codigo='carteira.valuation_cobertura', entidade=rotulo)


def render_portfolio_valuations(positions):
    st.subheader('Valuation médio do portfólio')
    stocks, fiis, aliases = set(), set(), {}
    for pos in positions:
        ticker = str(pos.get('ticker') or '').strip().upper()
        cls = str(pos.get('classe') or '').lower()
        if str(pos.get('moeda') or 'BRL').upper() != 'BRL' or str(
            pos.get('pais') or pos.get('country') or 'BR'
        ).upper() not in ('BR', ''):
            continue
        if 'fii' in cls or 'fundo imob' in cls:
            fiis.add(ticker)
            aliases[ticker] = ticker
        elif any(term in cls for term in ('ação', 'ações', 'acoes', 'acao')):
            base = ticker[:-1] if ticker.endswith('F') and len(ticker) > 4 else ticker
            stocks.add(base)
            aliases[ticker] = base
    with st.spinner('Consolidando valuations disponíveis…'):
        data, unavailable, consulted = _load(tuple(sorted(stocks)), tuple(sorted(fiis)))
    fundamentals = {ticker: data.get(base, {}) for ticker, base in aliases.items()}
    result = aggregate_valuations(positions, fundamentals)
    # Mesmos cards CSS dos painéis por classe. Aqui o denominador é a carteira
    # INTEIRA — renda fixa e exterior incluídos —, então cobertura baixa é o
    # estado normal; ela vai para o log de lacunas, não para o card.
    itens = list(METRICS.items())
    for offset in range(0, len(itens), 4):
        for col, (key, label) in zip(st.columns(4), itens[offset:offset + 4]):
            item = result[key]
            valor = item['value']
            sufixo = '%' if key == 'dy' else 'x'
            _registrar_cobertura(label, item, 'carteira')
            with col:
                st.markdown(_card(
                    label,
                    '—' if valor is None else f'{valor:.2f}{sufixo}',
                    'média ponderada pelo valor',
                    'var(--app-text)',
                ), unsafe_allow_html=True)
    if unavailable:
        aviso_lacuna('Fonte indisponível para: ' + ', '.join(unavailable),
                     codigo='tela.carteira.fonte_valuation_indisponivel', nivel='warning')
    st.caption('Médias aritméticas ponderadas pelo valor de mercado em BRL dos ativos com dado válido. '
               'Sem dado não significa zero; DY zero é incluído. Múltiplos nulos ou negativos são excluídos.')
    detalhe_tecnico(
        'Fontes: reconciliação B3/Fundamentus para ações e Fundamentus para FIIs, '
        'as mesmas da aba Análise. DY em percentual informado pela fonte, não yield on cost '
        'nem renda efetivamente recebida. Tesouro, renda fixa, ETFs, BDRs e exterior não '
        'entram enquanto não houver indicadores comparáveis integrados neste painel. '
        'Cobertura sobre o valor positivo conhecido da carteira, incluindo renda fixa.',
        codigo='carteira.valuation_fontes')
    detalhe_tecnico(
        'Média dos múltiplos não equivale a preço total dividido por lucro ou patrimônio '
        'consolidado. EV/EBIT e EV/EBITDA são médias descritivas ponderadas por posição, '
        'não agregações de enterprise value. As fontes podem ter datas e janelas distintas; '
        'não se trata de uma fotografia contábil sincronizada nem previsão de retorno.',
        codigo='carteira.valuation_limitacoes')
    detalhe_tecnico(f'Consulta: {consulted} · cache de até 1 hora. '
                    'Data-base contábil individual não disponível neste resumo.',
                    codigo='carteira.valuation_consulta')


# ══════════════════════════════════════════════════════════════════════════════
# Painéis por classe — usados nas sub-abas da Análise do Portfólio.
# Os fundamentos chegam JÁ carregados pela aba: refazer a busca aqui daria
# médias de uma foto e cards de outra, e a divergência não apareceria na tela.
# ══════════════════════════════════════════════════════════════════════════════

def render_valuations_classe(classe, positions, fundamentals, *, colunas: int = 4):
    """Médias da classe a partir dos fundamentos que a aba já buscou.

    Devolve o dicionário de agregados para que o chat receba exatamente os
    mesmos números exibidos.
    """
    metricas = METRICS_BY_CLASS.get(classe, tuple(METRICS))
    resultado = aggregate_valuations(positions, fundamentals, metricas)
    apurados = [k for k in metricas if resultado[k]['value'] is not None]
    st.markdown(f'##### 📐 Médias de {CLASS_LABELS.get(classe, classe)} na carteira')
    if not apurados:
        st.info('Esta análise não está disponível no momento.')
        aviso_lacuna('Nenhum indicador desta classe pôde ser agregado com os dados '
                     'carregados agora. Ausência de dado não é zero — a média não '
                     'existe, e não vale a pena exibir um número que não descreve '
                     'ativo nenhum.',
                     codigo='tela.carteira.valuation_classe_sem_media',
                     entidade=str(classe))
        return resultado
    for offset in range(0, len(metricas), colunas):
        fatia = list(metricas)[offset:offset + colunas]
        for col, chave in zip(st.columns(colunas), fatia):
            item = resultado[chave]
            spec = metric_spec(chave)
            valor = item['value']
            texto = '—' if valor is None else f'{valor:.2f}{spec.unit}'
            _registrar_cobertura(spec.label, item, 'classe')
            with col:
                st.markdown(_card(
                    spec.label, texto,
                    'média ponderada pelo valor',
                    'var(--app-text)',
                ), unsafe_allow_html=True)
    st.caption(
        'Média aritmética ponderada pelo valor de mercado dos ativos COM dado '
        'válido nesta classe. '
        'Múltiplo nulo ou negativo é excluído (indefinido, não barato); '
        'margem e crescimento negativos entram, porque são observação legítima. '
        'Não é múltiplo contábil consolidado nem previsão de retorno.'
    )
    return resultado


def render_valuations_tesouro(positions, ano_atual: int):
    """Prazo, retorno e composição por indexador — o que é agregável em renda fixa."""
    resumo = aggregate_tesouro(positions, ano_atual)
    st.markdown('##### 📐 Médias do Tesouro Direto na carteira')
    st.caption(
        'Título público não tem lucro nem patrimônio, logo não tem P/L, P/VP '
        'nem DY comparável — inventar um múltiplo aqui seria pior que declarar '
        'a ausência. O que se agrega é prazo, retorno e indexador.'
    )
    prazo = resumo['prazo_medio_anos']
    retorno = resumo['retorno_pct']
    composicao = resumo['por_indexador']
    principal = next(iter(composicao.items()), None)
    cartoes = [
        ('Títulos distintos', str(resumo['titulos']), 'posições agregadas por código', 'var(--app-text)'),
        ('Prazo médio',
         '—' if prazo is None else f'{prazo:.1f} anos',
         'ponderado pelo valor',
         'var(--app-text)'),
        ('Retorno mercado/custo',
         '—' if retorno is None else f'{retorno:+.2f}%',
         'acumulado desde o aporte, não taxa ao ano',
         'var(--app-primary)' if (retorno or 0) >= 0 else 'var(--app-danger)'),
        ('Maior indexador',
         '—' if principal is None else f'{principal[1]:.0%}',
         '—' if principal is None else principal[0],
         'var(--app-info)'),
    ]
    detalhe_tecnico(
        f"Prazo médio: cobertura {resumo['cobertura_prazo']:.0%} do valor"
        + (' · Educa+ usa o ano de conversão' if 'Educa+' in composicao else ''),
        codigo='carteira.tesouro_prazo_cobertura')
    for col, (titulo, valor, sub, cor) in zip(st.columns(len(cartoes)), cartoes):
        with col:
            st.markdown(_card(titulo, valor, sub, cor), unsafe_allow_html=True)
    if composicao:
        st.caption('Composição por indexador: ' + ' · '.join(
            f'{nome} {peso:.1%}' for nome, peso in composicao.items()))
    if resumo['valor_sem_ano'] > 0:
        aviso_lacuna('Parte do valor está em títulos sem ano identificável no código '
                     'e ficou fora do prazo médio.',
                     codigo='tela.carteira.tesouro_titulo_sem_ano')
    detalhe_tecnico('O valor de mercado vem do saldo informado pela corretora, não de '
                    'marcação a mercado independente título a título neste app.',
                    codigo='carteira.tesouro_valor_de_mercado')
    return resumo
