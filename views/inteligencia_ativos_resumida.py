"""
views/inteligencia_ativos_resumida.py
Página resumida da Inteligência dos Ativos (30/09/2026).

Segue o rascunho do usuário: reserva de emergência e renda fixa numa lista
curta; ações, FIIs e internacional com uma caixa por ativo. Dentro da caixa,
na ordem do rascunho: quanto tem e quanto deveria ter, manter / comprar /
vender, o substituto se for vender, dois pares numa tabela, o papel na
carteira, notícias, relatórios e o que do macro pesa. O detalhe completo
(13 etapas e Portfolio Fit) segue na aba, sob "Análise detalhada".

Os cartões saem num st.markdown só e só usam tokens var(--app-*).
Dados: core/inteligencia_ativos/resumida.py (puro).
"""
from __future__ import annotations

from html import escape

import streamlit as st

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import destaques_relatorios as dr
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import resumida as rs
from core.utils import fmt_moeda
from design.lacunas import aviso_lacuna, detalhe_tecnico

DETALHE_KEY = "ia_detalhe"   # liga a "Análise detalhada" abaixo
SELECAO_KEY = "ia_ativo"     # o mesmo seletor da análise detalhada

_ICONE = {rs.RESERVA: "🛟", rs.RENDA_FIXA: "🏦", rs.ACOES: "📈",
          rs.FIIS: "🏢", rs.EXTERIOR: "🌎", rs.OUTROS: "📦"}
_COR_DECISAO = {rs.COMPRAR: "var(--app-info)",
                rs.VENDER: "var(--app-danger)",
                rs.MANTER: "var(--app-muted)"}

_CAIXA = ('border:1px solid var(--app-border);border-radius:10px;'
          'background:var(--app-surface);padding:10px 14px;margin:6px 0;')
_SUB = ('font-size:0.7rem;font-weight:700;letter-spacing:.05em;'
        'text-transform:uppercase;color:var(--app-subtle);margin:10px 0 4px')
_TD = 'style="padding:3px 12px 3px 0;vertical-align:top;color:var(--app-text)"'
_TH = ('style="padding:3px 12px 3px 0;text-align:left;font-weight:600;'
       'color:var(--app-muted);font-size:0.78rem"')


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def _moeda(x: float | None, moeda: str = "BRL") -> str:
    if x is None:
        return "—"
    return fmt_moeda(x) if moeda == "BRL" else f"{moeda} {x:,.2f}"


def _link(texto: str, url: str | None) -> str:
    if url and url.lower().startswith(("http://", "https://")):
        return (f'<a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer" style="color:var(--app-text)">'
                f'{escape(texto)}</a>')
    return escape(texto)


def _secao(titulo: str) -> str:
    return f'<div style="{_SUB}">{escape(titulo)}</div>'


def _nota(texto: str) -> str:
    return (f'<div style="font-size:0.75rem;color:var(--app-subtle);'
            f'margin-top:4px">{escape(texto)}</div>')


# -- cabeçalho de grupo e lista simples ---------------------------------------------

def cabecalho_grupo(b: rs.Bloco, meta_reserva: str | None = None) -> str:
    """Nome do grupo, valor e % da carteira, e o alvo da classe. Puro."""
    alvo = ""
    if b.alvo_classe is not None:
        if b.chave == rs.RESERVA:
            alvo = (f"conta no alvo da renda fixa ({_pct(b.alvo_classe)}; "
                    f"renda fixa hoje {_pct(b.peso_classe)})")
        elif b.chave == rs.RENDA_FIXA:
            alvo = (f"alvo da renda fixa {_pct(b.alvo_classe)}, reserva "
                    f"incluída; hoje {_pct(b.peso_classe)}")
        else:
            alvo = f"alvo da classe {_pct(b.alvo_classe)}"
    extra = " · ".join(x for x in (alvo, meta_reserva or "") if x)
    return (
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:baseline;flex-wrap:wrap;gap:6px;margin-top:14px">'
        f'<div style="font-size:1.05rem;font-weight:700;color:var(--app-text)">'
        f'{_ICONE.get(b.chave, "")} {escape(b.rotulo)}</div>'
        f'<div style="color:var(--app-text);font-weight:600">'
        f'{escape(_moeda(b.valor))} · {escape(_pct(b.peso))}</div></div>'
        + (f'<div style="font-size:0.8rem;color:var(--app-subtle)">'
           f'{escape(extra)}</div>' if extra else ""))


def cartao_lista(b: rs.Bloco) -> str:
    """Reserva e renda fixa: cada título com valor e % da carteira. Puro."""
    linhas = "".join(
        f'<tr><td {_TD}><b>{escape(ln.ticker)}</b>'
        f'<div style="font-size:0.78rem;color:var(--app-subtle)">'
        f'{escape(ln.nome or "")}</div></td>'
        f'<td {_TD}>{escape(_moeda(ln.valor, ln.moeda))}</td>'
        f'<td {_TD}>{escape(_pct(ln.peso))}</td></tr>' for ln in b.linhas)
    return (f'<div style="{_CAIXA}"><table style="border-collapse:collapse;'
            f'width:100%;font-size:0.88rem">{linhas}</table></div>')


# -- a caixa de cada ativo ------------------------------------------------------------

def rotulo_expander(a: m.AnaliseAtivo,
                    sugerido: rs.AlvoSugerido | None = None,
                    avaliacao: av_.Avaliacao | None = None) -> str:
    """"BBAS3 · 8,5% → 2,3% · Vender": peso atual, % devida e a decisão."""
    d = rs.decisao(a, sugerido, avaliacao)
    alvo = rs.alvo_do_ativo(a, sugerido)
    peso = _pct(a.ativo.peso_atual)
    if alvo is not None:
        peso += f" → {_pct(alvo)}"
    return f"{a.ativo.ticker} · {peso} · {d.rotulo}"


def cartao_posicao(a: m.AnaliseAtivo,
                   sugerido: rs.AlvoSugerido | None = None,
                   avaliacao: av_.Avaliacao | None = None) -> str:
    """% atual, % devida e manter / comprar / vender. Puro."""
    d = rs.decisao(a, sugerido, avaliacao)
    devido = rs.peso_devido(a, sugerido)
    cor = _COR_DECISAO[d.codigo]

    def _kpi(rotulo, valor, detalhe="", estilo=""):
        return (f'<div style="flex:1;min-width:140px">'
                f'<div style="{_SUB};margin-top:0">{escape(rotulo)}</div>'
                f'<div style="font-size:1.25rem;font-weight:700;'
                f'color:var(--app-text);{estilo}">{escape(valor)}</div>'
                f'<div style="font-size:0.78rem;color:var(--app-subtle)">'
                f'{escape(detalhe)}</div></div>')
    return (
        f'<div style="{_CAIXA}"><div style="display:flex;gap:14px;'
        f'flex-wrap:wrap">'
        + _kpi("Porcentagem atual", _pct(a.ativo.peso_atual),
               _moeda(a.ativo.valor_mercado, a.ativo.moeda))
        + _kpi("Porcentagem devida", devido.valor, devido.detalhe)
        + _kpi("Manter, comprar ou vender", d.rotulo, d.detalhe,
               f"color:{cor}")
        + "</div>"
        + (f'<div style="font-size:0.82rem;color:var(--app-muted);'
           f'margin-top:6px">{escape(d.motivo)}</div>' if d.motivo else "")
        + _nota(rs.AVISO_DECISAO) + "</div>")


AVISO_AVALIACAO = ("Avaliação por regras sobre os dados do app, não "
                   "recomendação. O momento de preço descreve o passado "
                   "recente e não promete o próximo ano.")

_COR_LEITURA = {
    av_.FORTE: "var(--app-primary)", av_.BARATO: "var(--app-primary)",
    av_.POSITIVO: "var(--app-primary)",
    av_.ADEQUADA: "var(--app-text)", av_.JUSTO: "var(--app-text)",
    av_.NEUTRO: "var(--app-text)", av_.MISTO: "var(--app-warning)",
    av_.FRAGIL: "var(--app-danger)", av_.CARO: "var(--app-warning)",
    av_.NEGATIVO: "var(--app-danger)",
    av_.INSUFICIENTE: "var(--app-subtle)", av_.SEM_LEITURA: "var(--app-subtle)",
}
_SINAL = {1: ("+", "var(--app-primary)"), -1: ("−", "var(--app-danger)"),
          0: ("·", "var(--app-subtle)")}


def _bloco_dimensao(d: av_.Dimensao) -> str:
    if d.nota:
        detalhe_tecnico(f"{d.rotulo}: {d.nota}",
                        codigo="inteligencia.avaliacao_nota_dimensao")
    cor = _COR_LEITURA.get(d.leitura, "var(--app-text)")
    itens = "".join(
        f'<div style="display:flex;gap:6px;margin:2px 0;font-size:0.82rem;'
        f'color:var(--app-text)"><span style="font-weight:700;width:10px;'
        f'flex:none;color:{_SINAL[c.sinal][1]}">{_SINAL[c.sinal][0]}</span>'
        f'<span>{escape(c.texto)}</span></div>'
        for c in d.criterios)
    contagem = (f"{d.favoraveis} a favor · {d.desfavoraveis} contra"
                if d.criterios else "")
    return (
        f'<div style="border:1px solid var(--app-border);border-radius:8px;'
        f'padding:8px 10px;background:var(--app-surface-raised)">'
        f'<div style="{_SUB};margin-top:0">{escape(d.rotulo)}</div>'
        f'<div style="font-size:1.05rem;font-weight:700;color:{cor}">'
        f'{escape(d.rotulo_leitura)}</div>'
        + (f'<div style="font-size:0.74rem;color:var(--app-subtle);'
           f'margin-bottom:4px">{escape(contagem)}</div>' if contagem else "")
        + itens
        + "</div>")


def cartao_avaliacao(av: av_.Avaliacao) -> str:
    """Qualidade, preço e mercado com os critérios que pesaram e os alertas.
    Puro."""
    detalhe_tecnico(f"Régua setorial: {av.rotulo_perfil}",
                    codigo="inteligencia.avaliacao_regua_setorial")
    detalhe_tecnico(av_.AVISO, codigo="inteligencia.avaliacao_metodologia")
    alertas = "".join(
        f'<div style="margin:3px 0;font-size:0.84rem;color:'
        f'{"var(--app-danger)" if al.critico else "var(--app-warning)"}">'
        f'<b>{"Alerta eliminatório" if al.critico else "Alerta"}:</b> '
        f'{escape(al.texto)}</div>' for al in av.alertas)
    grade = "".join(_bloco_dimensao(d) for d in av.dimensoes)
    return (f'<div style="{_CAIXA}">{_secao("Avaliação do ativo")}'
            f'{alertas}'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,'
            f'minmax(240px,1fr));gap:8px;margin-top:4px">{grade}</div>'
            f'{_nota(AVISO_AVALIACAO)}</div>')


def cartao_substitutos(subs: tuple[prs.Par, ...]) -> str:
    """Só aparece quando a leitura é vender. Puro."""
    if not subs:
        corpo = ('<div style="color:var(--app-muted)">Nenhum par do mesmo '
                 'grupo fora da sua carteira para comparar.</div>')
    else:
        corpo = "".join(
            f'<div style="margin:2px 0"><b style="color:var(--app-text)">'
            f'{escape(p.ticker)}</b> <span style="color:var(--app-muted)">'
            f'{escape(p.nome or "")}</span><div style="font-size:0.78rem;'
            f'color:var(--app-subtle)">{escape(p.motivo)}</div></div>'
            for p in subs)
    return (f'<div style="{_CAIXA}">{_secao("Se vender, qual substituir?")}'
            f'{corpo}{_nota(rs.AVISO_SUBSTITUTO)}</div>')


def _regua(mt: rs.MetricaPares) -> str:
    """Uma régua por métrica: o ativo (ponto cheio), os pares (vazados) e a
    mediana do segmento (traço). A régua vai do menor ao maior valor
    mostrado; não há lado bom nem ruim."""
    marcas = []
    if mt.mediana_pos is not None:
        marcas.append(
            f'<div title="mediana do segmento" style="position:absolute;'
            f'left:{mt.mediana_pos:.1f}%;top:-4px;width:2px;height:14px;'
            f'margin-left:-1px;background:var(--app-text);opacity:.55"></div>')
    for pt in sorted(mt.pontos, key=lambda x: x.eh_ativo):
        tam = 12 if pt.eh_ativo else 9
        estilo = ("background:var(--app-primary);border:2px solid "
                  "var(--app-surface)" if pt.eh_ativo else
                  "background:var(--app-surface);border:2px solid var(--app-muted)")
        marcas.append(
            f'<div title="{escape(pt.ticker, quote=True)}: '
            f'{escape(pt.texto, quote=True)}" style="position:absolute;'
            f'left:{pt.posicao:.1f}%;top:{3 - tam / 2:.1f}px;width:{tam}px;'
            f'height:{tam}px;margin-left:-{tam / 2:.1f}px;border-radius:50%;'
            f'{estilo}"></div>')
    return ('<div style="position:relative;height:6px;margin:12px 6px 8px;'
            'border-radius:3px;background:var(--app-border)">'
            + "".join(marcas) + "</div>")


def _bloco_metrica(mt: rs.MetricaPares) -> str:
    pares = " · ".join(f"{escape(pt.ticker)} {escape(pt.texto)}"
                       for pt in mt.pontos if not pt.eh_ativo)
    return (
        f'<div title="{escape(mt.leitura, quote=True)}" style="border:1px '
        f'solid var(--app-border);border-radius:8px;padding:8px 10px;'
        f'background:var(--app-surface-raised)">'
        f'<div style="{_SUB};margin-top:0">{escape(mt.rotulo)}</div>'
        f'<div style="display:flex;align-items:baseline;gap:8px;'
        f'flex-wrap:wrap"><span style="font-size:1.15rem;font-weight:700;'
        f'color:var(--app-text)">{escape(mt.texto_ativo)}</span>'
        f'<span style="font-size:0.75rem;color:var(--app-muted)">'
        f'{escape(rs.ROTULO_POSICAO.get(mt.posicao, ""))}</span></div>'
        + _regua(mt)
        + f'<div style="font-size:0.74rem;color:var(--app-subtle)">'
          f'Mediana do segmento {escape(mt.texto_mediana)} '
          f'({mt.n_pares} {"par" if mt.n_pares == 1 else "pares"})</div>'
        + (f'<div style="font-size:0.74rem;color:var(--app-muted)">'
           f'{pares}</div>' if pares else "")
        + "</div>")


def cartao_pares(t: rs.TabelaPares) -> str:
    """O ativo contra o segmento, uma régua por indicador. Puro."""
    titulo = "Comparação com o mesmo segmento"
    if not t.linhas:
        aviso_lacuna(f"Comparação com o segmento: {t.motivo or 'sem dado'}",
                     codigo="tela.inteligencia.pares_sem_dado")
        return (f'<div style="{_CAIXA}">{_secao(titulo)}<div style="color:'
                'var(--app-muted)">Comparação indisponível no momento.'
                '</div></div>')
    ponto = ('<span style="display:inline-block;width:9px;height:9px;'
             'border-radius:50%;margin-right:4px;vertical-align:middle;{}">'
             '</span>')
    legenda = []
    for i, (tk, nome) in enumerate(t.nomes):
        estilo = ("background:var(--app-primary)" if i == 0 else
                  "border:2px solid var(--app-muted)")
        legenda.append(f'<span style="margin-right:12px;white-space:nowrap">'
                       f'{ponto.format(estilo)}<b>{escape(tk)}</b> '
                       f'<span style="color:var(--app-subtle)">'
                       f'{escape(nome)}</span></span>')
    legenda.append('<span style="white-space:nowrap"><span style="display:'
                   'inline-block;width:2px;height:11px;margin-right:4px;'
                   'vertical-align:middle;background:var(--app-text);'
                   'opacity:.55"></span>mediana do segmento</span>')
    grade = "".join(_bloco_metrica(mt) for mt in t.metricas)
    return (f'<div style="{_CAIXA}">{_secao(f"{titulo} · {t.titulo}")}'
            f'<div style="font-size:0.8rem;color:var(--app-text);'
            f'margin-bottom:8px">{"".join(legenda)}</div>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,'
            f'minmax(200px,1fr));gap:8px">{grade}</div>'
            f'{_nota(prs.RODAPE)}</div>')


def cartao_papel(a: m.AnaliseAtivo) -> str:
    """Papel na carteira e se a tese segue de pé. Puro."""
    papel = a.papel_principal.rotulo if a.papel_principal else "—"
    outros = [p.rotulo for p in a.papeis
              if a.papel_principal is None or p.codigo != a.papel_principal.codigo]
    tese = a.tese
    linhas = [f"<b>Papel:</b> {escape(papel)}"
              + (f' <span style="color:var(--app-subtle)">(também: '
                 f'{escape(", ".join(outros))})</span>' if outros else "")]
    if tese.por_que_esta_na_carteira:
        linhas.append(f"<b>Por que está na carteira:</b> "
                      f"{escape(tese.por_que_esta_na_carteira)}")
    if tese.alinhamento:
        linhas.append(f"<b>Alinhamento com a estratégia:</b> "
                      f"{escape(tese.alinhamento)}")
    return (f'<div style="{_CAIXA}">{_secao("Papel na carteira")}'
            + "".join(f'<div style="margin:2px 0;color:var(--app-text)">'
                      f'{x}</div>' for x in linhas) + "</div>")


def _manchete(i) -> str:
    origem = f"{i.ticker} · " if getattr(i, "ticker", None) else ""
    return (f'<div style="margin:3px 0"><span style="color:var(--app-subtle);'
            f'font-size:0.78rem">{escape(origem)}'
            f'{escape(rs.inf._data_br(i.date))} · {escape(i.source or "—")}'
            f'</span><div style="color:var(--app-text)">'
            f'{_link(i.headline, i.url)}</div></div>')


def _manchete_cenario(i: rs.ItemCenario) -> str:
    pais = f" · {i.pais}" if i.pais else ""
    return (f'<div style="margin:3px 0"><span style="color:var(--app-subtle);'
            f'font-size:0.78rem">{escape(i.tema.capitalize())}{escape(pais)} · '
            f'{escape(i.data)} · {escape(i.veiculo or "—")}</span>'
            f'<div style="color:var(--app-text)">{_link(i.titulo, i.url)}'
            f'</div></div>')


def cartao_noticias(a: m.AnaliseAtivo,
                    gerais: tuple[list[dict], str] | None = None) -> str:
    """Notícias do ativo; sem elas, as do segmento; e, se ainda faltar, o
    cenário econômico e político que pesa na classe do ativo. Nunca em
    branco. Puro."""
    itens, motivo = rs.noticias(a)
    partes = []
    if itens:
        partes.append("".join(_manchete(i) for i in itens))
    else:
        aviso_lacuna(f"Notícias do ativo: {motivo or 'sem notícias'}",
                     codigo="tela.inteligencia.noticias_do_ativo_sem_dado",
                     entidade=str(a.ativo.ticker))
        partes.append('<div style="color:var(--app-muted)">Nenhuma notícia '
                      'disponível para este ativo.</div>')
    setor, rotulo = rs.noticias_setor(a)
    if setor:
        partes.append(_secao("Como está o segmento"
                             + (f" · {rotulo}" if rotulo else "")))
        partes.append("".join(_manchete(i) for i in setor))
        partes.append(_nota("Notícias dos pares do mesmo segmento, não do "
                            "ativo."))
    if gerais is not None and rs.precisa_noticiario_geral(a):
        brutos, origem = gerais
        falta = max(rs.N_NOTICIAS - len(itens) - len(setor), 1)
        cenario = rs.noticias_cenario(a, brutos, falta)
        temas = rs.temas_do_ativo(a.ativo.classe_politica)
        partes.append(_secao("Cenário econômico e político"))
        if cenario:
            partes.append("".join(_manchete_cenario(i) for i in cenario))
        else:
            partes.append('<div style="color:var(--app-muted)">Nenhuma '
                          'manchete de juros, inflação, câmbio, fiscal ou '
                          'política no período.</div>')
        detalhe_tecnico("Notícias do cenário"
                        + (f" escolhidas por {temas}" if temas else "")
                        + (f"; fonte: {origem}" if origem else ""),
                        codigo="inteligencia.noticias_cenario_origem",
                        entidade=str(a.ativo.ticker))
        partes.append(_nota("Fatos do país que pesam no ativo, não de outras "
                            "empresas."))
    elif not itens and not setor:
        aviso_lacuna("Cenário econômico e político indisponível",
                     codigo="tela.inteligencia.cenario_noticias_indisponivel",
                     entidade=str(a.ativo.ticker))
    return f'<div style="{_CAIXA}">{_secao("Notícias")}{"".join(partes)}</div>'


@st.cache_data(ttl=900, show_spinner=False)
def _noticiario_geral() -> tuple[list[dict], str]:
    """Noticiário geral cru (acervo, túnel ou vitrine), com o tipo de evento
    de cada item. Falha vira lista vazia com a fonte nomeada."""
    try:
        from core.contexto_mercado import itens_gerais
        return itens_gerais()
    except Exception as exc:  # noqa: BLE001 - fonte fora do ar não derruba a página
        return [], f"leitura falhou ({type(exc).__name__})"


@st.cache_data(ttl=3600, show_spinner=False)
def _destaques_crus(ticker: str) -> tuple[tuple, ...]:
    """Frases de fato dos documentos do ativo, do corpus RAG, em tuplas
    simples. Falha vira vazio: a caixa cai na lista de documentos.

    O cache guarda tuplas, e não ``dr.Destaque``: no deploy,
    ``core/modulos_frescos`` descarta o módulo enquanto outra sessão ainda
    roda o velho, e o pickle recusa o ``Destaque`` dele por não ser o objeto
    do módulo novo (``UnserializableReturnValueError``, 03/10/2026)."""
    try:
        return tuple((d.titulo, d.data, d.tipo, tuple(d.frases))
                     for d in dr.ler(ticker))
    except Exception:  # noqa: BLE001 - corpus ilegível não derruba a página
        return ()


def _destaques(ticker: str) -> tuple[dr.Destaque, ...]:
    return tuple(dr.Destaque(*c) for c in _destaques_crus(ticker))


def cartao_relatorios(a: m.AnaliseAtivo,
                      destaques: tuple[dr.Destaque, ...] = ()) -> str:
    """O que os documentos dizem (frases do emissor com fato e número), não
    o link para o documento inteiro. Documento sem texto extraído aparece
    só com título e data. Puro."""
    docs, motivo = rs.relatorios(a)
    partes = []
    for d in destaques:
        partes.append(
            '<div style="margin:6px 0 8px 0"><div style="font-size:0.78rem;'
            f'color:var(--app-subtle)">{escape(rs.inf._data_br(d.data))} · '
            f'{escape(d.tipo)}</div><div style="color:var(--app-text);'
            f'font-weight:700;margin:1px 0 3px 0">{escape(d.titulo)}</div>'
            '<ul style="margin:0 0 0 18px;padding:0;color:var(--app-text)">'
            + "".join(f'<li style="margin:2px 0">{escape(f)}</li>'
                      for f in d.frases)
            + "</ul></div>")
    restantes = [d for d in docs
                 if not any(dr.mesmo_documento(d.titulo, d.reference_date, x)
                            for x in destaques)]
    if restantes:
        partes.append(
            f'<div style="margin-top:6px;color:var(--app-muted);'
            f'font-size:0.8rem"><b>{"Outros documentos recentes" if destaques else "Documentos recentes"}'
            ':</b></div>'
            + "".join(
                '<div style="margin:2px 0;font-size:0.82rem;color:var(--app-'
                f'text)">{escape(rs.inf._data_br(d.reference_date))} · '
                f'{escape(d.rotulo or "")}: {escape(d.titulo or "")}</div>'
                for d in restantes))
    if restantes:
        aviso_lacuna("Relatórios: documentos recentes ainda sem texto "
                     "extraído no acervo",
                     codigo="tela.inteligencia.relatorios_sem_texto",
                     entidade=str(a.ativo.ticker))
    if not partes:
        aviso_lacuna(f"Relatórios: {motivo or rs.inf.NAO_DISPONIVEL}",
                     codigo="tela.inteligencia.relatorios_sem_dado",
                     entidade=str(a.ativo.ticker))
        partes.append('<div style="color:var(--app-muted)">Nenhum relatório '
                      'disponível para este ativo.</div>')
    nota = ("Trechos literais dos documentos oficiais. A leitura do que isso "
            "muda na tese está na análise detalhada." if destaques else
            "Os pontos principais aparecem aqui quando o texto dos "
            "documentos estiver disponível.")
    return (f'<div style="{_CAIXA}">{_secao("Relatórios relevantes")}'
            f'{"".join(partes)}{_nota(nota)}</div>')


def cartao_macro(mc: rs.Macro, impacto: str | None = None) -> str:
    """Como o macro influencia: canais da classe, o seu cenário e, se já
    gerada, a leitura do Portfolio Fit. Puro."""
    partes = [f'<div style="color:var(--app-text)"><b>O que mais pesa nesta '
              f'classe:</b> {escape(", ".join(mc.canais) or "—")}</div>']
    if mc.sem_cenario:
        aviso_lacuna("Macro: as séries do banco não puderam ser lidas",
                     codigo="tela.inteligencia.macro_series_ilegiveis")
        partes.append('<div style="color:var(--app-muted)">Cenário macro '
                      'indisponível no momento.</div>')
    elif mc.premissas:
        partes.append("<div style=\"color:var(--app-text);margin-top:4px\">"
                      "<b>Como estão agora (lido dos dados):</b></div><ul "
                      "style=\"margin:2px 0 0 18px;padding:0;"
                      "color:var(--app-text)\">"
                      + "".join(f"<li>{escape(p)}</li>" for p in mc.premissas)
                      + "</ul>")
    else:
        aviso_lacuna("Macro: sem dado para as variáveis desta classe",
                     codigo="tela.inteligencia.macro_sem_variaveis")
        partes.append('<div style="color:var(--app-muted)">Cenário macro '
                      'indisponível no momento.</div>')
    if mc.sinais:
        partes.append("<ul style=\"margin:4px 0 0 18px;padding:0;"
                      "color:var(--app-warning)\">"
                      + "".join(f"<li>{escape(s)}</li>" for s in mc.sinais)
                      + "</ul>")
    if impacto:
        partes.append(f'<div style="color:var(--app-text);margin-top:6px">'
                      f'<b>Leitura do Portfolio Fit:</b> {escape(impacto)}</div>')
    else:
        partes.append(_nota("A leitura do impacto sai do Portfolio Fit, na "
                            "análise detalhada."))
    return (f'<div style="{_CAIXA}">{_secao("Como o macro influencia")}'
            + "".join(partes) + "</div>")


_SETA_TENDENCIA = {"alta": ("▲ alta", "var(--app-warning)"),
                   "queda": ("▼ queda", "var(--app-info)"),
                   "estavel": ("● estável", "var(--app-muted)"),
                   "incerta": ("? incerta", "var(--app-subtle)")}


def cartao_cenario(linhas: tuple[rs.LinhaCenario, ...]) -> str:
    """Cenário econômico atual, lido dos dados: uma peça por variável, com
    valor, tendência calculada, fonte e data. Puro."""
    if not linhas or not any(ln.valor for ln in linhas):
        aviso_lacuna("Cenário econômico: as séries macro do banco não puderam "
                     "ser lidas",
                     codigo="tela.inteligencia.cenario_series_ilegiveis")
        return (f'<div style="{_CAIXA}">{_secao("🌎 Cenário econômico atual")}'
                '<div style="color:var(--app-muted)">Cenário econômico '
                'indisponível no momento.</div></div>')
    detalhe_tecnico("Cenário lido pelo programa das séries do banco (Selic, "
                    "IPCA, curva do Tesouro, dólar, juros e crédito nos EUA). "
                    "Commodities e risco geopolítico não têm série: as LLMs "
                    "os leem nas notícias.",
                    codigo="inteligencia.cenario_metodologia")
    pecas = []
    for ln in linhas:
        if ln.valor:
            seta, cor = _SETA_TENDENCIA.get(ln.tendencia or "incerta",
                                            _SETA_TENDENCIA["incerta"])
            detalhe_tecnico(f"{ln.rotulo}: {ln.fonte}"
                            + (f" · {ln.referencia}" if ln.referencia else ""),
                            codigo="inteligencia.cenario_fonte_serie",
                            entidade=ln.rotulo)
            corpo = (f'<div style="color:var(--app-text);font-size:0.86rem;'
                     f'margin-top:2px">{escape(ln.valor)}</div>'
                     f'<div style="font-size:0.74rem;font-weight:700;color:{cor};'
                     f'margin-top:4px">{escape(seta)}</div>')
        else:
            aviso_lacuna(f"Cenário econômico — {ln.rotulo}: sem dado "
                         f"({ln.fonte or 'ausente'})",
                         codigo="tela.inteligencia.cenario_variavel_sem_dado",
                         entidade=ln.rotulo)
            corpo = ('<div style="color:var(--app-subtle);font-size:0.8rem;'
                     'margin-top:2px">Sem dado.</div>')
        pecas.append('<div style="border:1px solid var(--app-border);'
                     'border-radius:8px;background:var(--app-surface-raised);'
                     'padding:8px 10px"><div style="font-size:0.72rem;'
                     'font-weight:700;color:var(--app-muted);text-transform:'
                     f'uppercase;letter-spacing:.04em">{escape(ln.rotulo)}</div>'
                     f'{corpo}</div>')
    return (f'<div style="{_CAIXA}">{_secao("🌎 Cenário econômico atual")}'
            '<div style="display:grid;grid-template-columns:repeat(auto-fill,'
            f'minmax(220px,1fr));gap:8px">{"".join(pecas)}</div>'
            + _nota("A tendência é calculada a partir dos dados, não é "
                    "opinião.")
            + "</div>")


def _impacto_fit(a: m.AnaliseAtivo) -> str | None:
    """Leitura de Portfolio Fit já gerada nesta sessão, se houver."""
    prefixo = f"ia_fit_{a.ativo.ticker}_{a.versao_politica}_"
    for k, v in st.session_state.items():
        if str(k).startswith(prefixo):
            textos = getattr(v, "textos", None) or {}
            if textos.get("scenario_impact"):
                return str(textos["scenario_impact"])
    return None


def _abrir_detalhe(ticker: str) -> None:
    st.session_state[SELECAO_KEY] = ticker
    st.session_state[DETALHE_KEY] = True


def _render_ativo(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor,
                  na_carteira: tuple[str, ...],
                  sugerido: rs.AlvoSugerido | None = None) -> None:
    av = av_.avaliar(a)
    with st.expander(rotulo_expander(a, sugerido, av)):
        d = rs.decisao(a, sugerido, av)
        html = cartao_posicao(a, sugerido, av)
        if d.codigo == rs.VENDER:
            html += cartao_substitutos(rs.substitutos(a, na_carteira))
        html += (cartao_avaliacao(av) + cartao_pares(rs.tabela_pares(a)) + cartao_papel(a)
                 + cartao_noticias(a, _noticiario_geral()
                                   if rs.precisa_noticiario_geral(a) else None)
                 + cartao_relatorios(a, _destaques(a.ativo.ticker))
                 + cartao_macro(rs.macro(a, ctx), _impacto_fit(a)))
        st.markdown(html, unsafe_allow_html=True)
        st.button("Ver análise completa", key=f"ia_resumo_{a.ativo.ticker}",
                  on_click=_abrir_detalhe, args=(a.ativo.ticker,))


def render(analises, ctx: m.ContextoInvestidor,
           politica: dict | None = None) -> None:
    na_carteira = tuple(a.ativo.ticker for a in analises)
    with st.expander("🌎 Cenário econômico atual (lido dos dados)"):
        st.markdown(cartao_cenario(rs.cenario_atual(ctx.cenario)),
                    unsafe_allow_html=True)
    for b in rs.blocos(analises, ctx):
        meta = rs.meta_reserva(politica) if b.chave == rs.RESERVA else None
        st.markdown(cabecalho_grupo(b, meta), unsafe_allow_html=True)
        if b.chave in rs.GRUPOS_EM_LISTA:
            st.markdown(cartao_lista(b), unsafe_allow_html=True)
            continue
        alvos = b.alvos
        for a in b.analises:
            _render_ativo(a, ctx, na_carteira, alvos.get(a.ativo.ticker))
