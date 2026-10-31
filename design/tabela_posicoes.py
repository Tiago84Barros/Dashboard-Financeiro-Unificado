"""Tabela de posições da Visão Geral (Investimentos → Análise do Portfólio).

Renderiza o dict de `core.carteira_tabela.montar_tabela` como HTML: grupos por
tipo com subtotal, ticker com nome, números alinhados em tabular-nums, barra
de peso, corretora em etiqueta e rodapé com a procedência de cada coluna.

Só usa as variáveis ``--app-*`` do tema (com fallback escuro), então segue o
claro e o escuro sem CSS próprio por tema. O HTML sai numa linha só: o
markdown do Streamlit transforma linha indentada em bloco de código.
"""
from __future__ import annotations

import html as _html
from datetime import date

from core.carteira_tabela import (
    ABERTURA,
    GRUPO_ACOES,
    GRUPO_AMERICANAS,
    GRUPO_FII,
    GRUPO_RENDA_FIXA,
    INICIO_EXTRATO_B3,
)

_COR_GRUPO = {
    GRUPO_ACOES:      "#4C9BE8",
    GRUPO_FII:        "#E84C9B",
    GRUPO_AMERICANAS: "#22B8A6",
    GRUPO_RENDA_FIXA: "#A855F7",
}
_COR_OUTROS = "#F5A623"

_ICONE_GRUPO = {
    GRUPO_ACOES:      "📈",
    GRUPO_FII:        "🏢",
    GRUPO_AMERICANAS: "🌎",
    GRUPO_RENDA_FIXA: "🏦",
}

# (rótulo, alinhamento, dica) na ordem que o usuário pediu.
_COLUNAS = (
    ("Ativo", "l", ""),
    ("Valor investido", "r", "Custo de aquisição em R$"),
    ("Quantidade", "r", "Ações ou cotas em custódia"),
    ("Dividendos 12M", "r", "Dividendos, JCP e rendimentos recebidos nos últimos 12 meses"),
    ("Preço médio", "r", "Custo médio por ação/cota"),
    ("Valor de mercado", "r", "Quantidade × última cotação, em R$"),
    ("% patrimônio", "r", "Peso no valor de mercado total da carteira"),
    ("% do setor", "r", "Peso do ativo dentro das posições do mesmo setor"),
    ("1º aporte", "c", "Evidência de posse mais antiga nos extratos importados"),
    ("Corretora", "l", "Onde o ativo está custodiado"),
)

CSS = """
<style>
.tp-wrap{--tp-bg:var(--app-surface,#111827);--tp-raised:var(--app-surface-raised,#1a2333);
--tp-bd:var(--app-border,rgba(148,163,184,.18));--tp-tx:var(--app-text,#e5e7eb);
--tp-mu:var(--app-muted,#94a3b8);--tp-sb:var(--app-subtle,#64748b);
--tp-pos:#16a34a;--tp-neg:#dc2626;
border:1px solid var(--tp-bd);border-radius:14px;background:var(--tp-bg);
overflow:hidden;margin:6px 0 4px 0;box-shadow:var(--app-shadow,0 1px 2px rgba(0,0,0,.18));}
.tp-head{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;
gap:12px;padding:16px 20px 14px 20px;border-bottom:1px solid var(--tp-bd);}
.tp-title{font-size:1.02rem;font-weight:750;color:var(--tp-tx);letter-spacing:-.01em;}
.tp-sub{font-size:.78rem;color:var(--tp-mu);margin-top:2px;}
.tp-legend{display:flex;flex-wrap:wrap;gap:6px;}
.tp-chip{display:inline-flex;align-items:center;gap:6px;font-size:.72rem;font-weight:600;
color:var(--tp-tx);padding:3px 10px 3px 8px;border-radius:999px;border:1px solid var(--tp-bd);
background:var(--tp-raised);white-space:nowrap;}
.tp-chip i{width:8px;height:8px;border-radius:50%;display:inline-block;}
.tp-chip b{font-weight:700;color:var(--tp-mu);font-variant-numeric:tabular-nums;}
.tp-scroll{overflow-x:auto;max-height:720px;overflow-y:auto;}
.tp-t{border-collapse:separate;border-spacing:0;width:100%;min-width:1180px;font-size:.82rem;
color:var(--tp-tx);font-variant-numeric:tabular-nums;}
.tp-t thead th{position:sticky;top:0;z-index:3;background:var(--tp-raised);color:var(--tp-mu);
font-size:.66rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;padding:10px 10px;
border-bottom:1px solid var(--tp-bd);white-space:nowrap;cursor:default;}
.tp-t th.l,.tp-t td.l{text-align:left;}.tp-t th.r,.tp-t td.r{text-align:right;}
.tp-t th.c,.tp-t td.c{text-align:center;}
.tp-t thead th:first-child,.tp-t td.tp-ativo{position:sticky;left:0;z-index:2;}
.tp-t thead th:first-child{z-index:4;}
.tp-t td{padding:9px 10px;border-bottom:1px solid var(--tp-bd);white-space:nowrap;vertical-align:middle;}
.tp-t tr.tp-row td{background:var(--tp-bg);}
.tp-t tr.tp-row:hover td{background:var(--tp-raised);}
.tp-t td.tp-ativo{box-shadow:inset 3px 0 0 var(--tp-c);min-width:190px;max-width:260px;}
.tp-tk{font-weight:750;font-size:.84rem;letter-spacing:.01em;}
.tp-nm{font-size:.72rem;color:var(--tp-mu);overflow:hidden;text-overflow:ellipsis;max-width:240px;}
.tp-tag{display:inline-block;margin-left:6px;font-size:.6rem;font-weight:700;letter-spacing:.04em;
text-transform:uppercase;color:var(--tp-mu);border:1px solid var(--tp-bd);border-radius:4px;padding:0 4px;
vertical-align:1px;}
.tp-sm{display:block;font-size:.7rem;color:var(--tp-mu);margin-top:1px;}
.tp-pos{color:var(--tp-pos);}.tp-neg{color:var(--tp-neg);}
.tp-nil{color:var(--tp-sb);}
.tp-bar{display:flex;align-items:center;justify-content:flex-end;gap:8px;}
.tp-bar span.tp-track{width:44px;height:5px;border-radius:3px;background:var(--tp-bd);overflow:hidden;
display:inline-block;}
.tp-bar span.tp-fill{display:block;height:100%;border-radius:3px;background:var(--tp-c);}
.tp-brk{display:inline-block;font-size:.7rem;font-weight:650;padding:2px 8px;border-radius:6px;
background:var(--tp-raised);border:1px solid var(--tp-bd);margin:1px 4px 1px 0;}
.tp-t tr.tp-grp td{background:var(--tp-raised);border-bottom:1px solid var(--tp-bd);
border-top:1px solid var(--tp-bd);font-weight:700;padding:11px 12px;}
.tp-t tr.tp-grp td.tp-ativo{box-shadow:inset 3px 0 0 var(--tp-c);background:var(--tp-raised);}
.tp-gn{display:inline-flex;align-items:center;gap:8px;font-size:.8rem;text-transform:uppercase;
letter-spacing:.06em;}
.tp-gn i{width:9px;height:9px;border-radius:2px;background:var(--tp-c);display:inline-block;}
.tp-gc{font-size:.68rem;font-weight:650;color:var(--tp-mu);background:var(--tp-bg);border:1px solid var(--tp-bd);
border-radius:999px;padding:1px 8px;text-transform:none;letter-spacing:0;}
.tp-t tr.tp-tot td{position:sticky;bottom:0;z-index:2;background:var(--tp-raised);font-weight:800;
border-top:2px solid var(--tp-bd);border-bottom:none;padding:12px;}
.tp-t tr.tp-tot td.tp-ativo{z-index:3;box-shadow:none;text-transform:uppercase;letter-spacing:.06em;
font-size:.76rem;}
.tp-foot{padding:10px 20px 14px 20px;border-top:1px solid var(--tp-bd);font-size:.7rem;color:var(--tp-mu);
line-height:1.55;}
.tp-foot b{color:var(--tp-tx);font-weight:650;}
</style>
"""


# ── formatação ────────────────────────────────────────────────────────────────

def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _num_br(v: float, casas: int) -> str:
    return f"{v:_.{casas}f}".replace(".", ",").replace("_", ".")


def _moeda(v: float | None, simbolo: str = "R$") -> str:
    if v is None:
        return '<span class="tp-nil">—</span>'
    sinal = "−" if v < 0 else ""
    return f"{sinal}{simbolo}&nbsp;{_num_br(abs(v), 2)}"


def fmt_quantidade(q: float) -> str:
    """Inteiro sem casas; fração (exterior, Tesouro) com até 4, sem zeros à toa."""
    if abs(q - round(q)) < 1e-9:
        return _num_br(round(q), 0)
    txt = _num_br(q, 4).rstrip("0")
    return txt.rstrip(",")


def _pct(v: float | None, casas: int = 2) -> str:
    if v is None:
        return '<span class="tp-nil">—</span>'
    return f"{_num_br(v, casas)}%"


def fmt_data_aporte(iso: str | None, fonte: str | None, hoje: date | None = None) -> str:
    """Célula do 1º aporte: data + tempo de carteira, ou a razão de não ter data."""
    if fonte == ABERTURA:
        return (f'antes de {INICIO_EXTRATO_B3}'
                '<span class="tp-sm">posição de abertura</span>')
    if not iso:
        return '<span class="tp-nil">—</span>'
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return '<span class="tp-nil">—</span>'
    hoje = hoje or date.today()
    meses = (hoje.year - d.year) * 12 + (hoje.month - d.month) - (hoje.day < d.day)
    if meses >= 12:
        anos = meses // 12
        tempo = f"há {anos} ano{'s' if anos > 1 else ''}"
    elif meses >= 1:
        tempo = f"há {meses} {'mês' if meses == 1 else 'meses'}"
    else:
        tempo = "este mês"
    prefixo = "até " if fonte == "foto" else ""
    nota = "1ª foto de posição" if fonte == "foto" else tempo
    return f'{prefixo}{d.strftime("%d/%m/%Y")}<span class="tp-sm">{_e(nota)}</span>'


# ── células ───────────────────────────────────────────────────────────────────

def _cel_ativo(ln: dict) -> str:
    tag = ""
    if ln.get("classe") and ln.get("_grupo") == GRUPO_RENDA_FIXA:
        tag = f'<span class="tp-tag">{_e(ln["classe"])}</span>'
    elif ln.get("moeda") and ln["moeda"] != "BRL":
        tag = f'<span class="tp-tag">{_e(ln["moeda"])}</span>'
    nome = ln.get("nome") or ""
    nome_html = (f'<div class="tp-nm" title="{_e(nome)}">{_e(nome)}</div>'
                 if nome and nome.upper() != str(ln["ticker"]).upper() else "")
    return f'<div class="tp-tk">{_e(ln["ticker"])}{tag}</div>{nome_html}'


def _cel_mercado(ln: dict) -> str:
    base = _moeda(ln["valor_mercado"])
    r = ln.get("rentab_pct")
    if r is None:
        return base
    cls = "tp-pos" if r >= 0 else "tp-neg"
    seta = "▲" if r >= 0 else "▼"
    return f'{base}<span class="tp-sm {cls}">{seta} {_num_br(abs(r), 1)}%</span>'


def _cel_preco_medio(ln: dict) -> str:
    if not ln.get("preco_medio"):
        return '<span class="tp-nil">—</span>'
    base = _moeda(ln["preco_medio"])
    pm_orig = ln.get("preco_medio_moeda_original")
    if pm_orig and ln.get("moeda") == "USD":
        return f'{base}<span class="tp-sm">US$&nbsp;{_num_br(float(pm_orig), 2)}</span>'
    return base


def _cel_peso(v: float, escala: float) -> str:
    largura = max(2.0, min(100.0, v / escala * 100)) if escala > 0 and v > 0 else 0
    return (f'<div class="tp-bar">{_pct(v)}<span class="tp-track">'
            f'<span class="tp-fill" style="width:{largura:.1f}%"></span></span></div>')


def _cel_setor(ln: dict) -> str:
    return f'{_pct(ln.get("pct_setor"), 1)}<span class="tp-sm">{_e(ln.get("setor") or "—")}</span>'


def _cel_corretora(ln: dict) -> str:
    nomes = ln.get("corretoras") or []
    if not nomes:
        return '<span class="tp-nil">—</span>'
    return "".join(f'<span class="tp-brk">{_e(n)}</span>' for n in nomes)


def _cel_div(v: float) -> str:
    if not v:
        return '<span class="tp-nil">—</span>'
    return _moeda(v)


# ── tabela ────────────────────────────────────────────────────────────────────

def render_tabela_posicoes(tabela: dict, fontes_ausentes: list[str] | None = None) -> str:
    """HTML completo (CSS incluso) da tabela de posições."""
    grupos = tabela.get("grupos") or []
    tot = tabela.get("total") or {}
    if not grupos:
        return ""

    maior_peso = max((ln["pct_patrimonio"] for g in grupos for ln in g["linhas"]), default=0.0)

    legenda = "".join(
        f'<span class="tp-chip"><i style="background:{_COR_GRUPO.get(g["grupo"], _COR_OUTROS)}"></i>'
        f'{_e(g["grupo"])} <b>{_num_br(g["pct_patrimonio"], 1)}%</b></span>'
        for g in grupos
    )
    n_cls = len(grupos)
    head = (
        '<div class="tp-head"><div>'
        '<div class="tp-title">Posições por tipo de ativo</div>'
        f'<div class="tp-sub">{tot.get("n", 0)} ativos em {n_cls} '
        f'{"tipo" if n_cls == 1 else "tipos"} · valor de mercado '
        f'{_moeda(tot.get("valor_mercado"))}</div>'
        f'</div><div class="tp-legend">{legenda}</div></div>'
    )

    ths = "".join(
        f'<th class="{al}" title="{_e(dica)}">{_e(rot)}</th>' for rot, al, dica in _COLUNAS
    )

    corpo: list[str] = []
    for g in grupos:
        cor = _COR_GRUPO.get(g["grupo"], _COR_OUTROS)
        icone = _ICONE_GRUPO.get(g["grupo"], "◆")
        corpo.append(
            f'<tr class="tp-grp" style="--tp-c:{cor}">'
            f'<td class="l tp-ativo"><span class="tp-gn"><i></i>{icone} {_e(g["grupo"])}'
            f'<span class="tp-gc">{g["n"]} {"ativo" if g["n"] == 1 else "ativos"}</span></span></td>'
            f'<td class="r">{_moeda(g["total_investido"])}</td>'
            '<td></td>'
            f'<td class="r">{_cel_div(g["dividendos_12m"])}</td>'
            '<td></td>'
            f'<td class="r">{_moeda(g["valor_mercado"])}</td>'
            f'<td class="r">{_pct(g["pct_patrimonio"])}</td>'
            '<td></td><td></td><td></td></tr>'
        )
        for ln in g["linhas"]:
            ln = {**ln, "_grupo": g["grupo"]}
            corpo.append(
                f'<tr class="tp-row" style="--tp-c:{cor}">'
                f'<td class="l tp-ativo">{_cel_ativo(ln)}</td>'
                f'<td class="r">{_moeda(ln["total_investido"])}</td>'
                f'<td class="r">{fmt_quantidade(ln["quantidade"])}</td>'
                f'<td class="r">{_cel_div(ln["dividendos_12m"])}</td>'
                f'<td class="r">{_cel_preco_medio(ln)}</td>'
                f'<td class="r">{_cel_mercado(ln)}</td>'
                f'<td class="r">{_cel_peso(ln["pct_patrimonio"], maior_peso)}</td>'
                f'<td class="r">{_cel_setor(ln)}</td>'
                f'<td class="c">{fmt_data_aporte(ln.get("primeiro_aporte"), ln.get("aporte_fonte"))}</td>'
                f'<td class="l">{_cel_corretora(ln)}</td></tr>'
            )

    corpo.append(
        '<tr class="tp-tot">'
        f'<td class="l tp-ativo">Total · {tot.get("n", 0)} ativos</td>'
        f'<td class="r">{_moeda(tot.get("total_investido"))}</td>'
        '<td></td>'
        f'<td class="r">{_cel_div(tot.get("dividendos_12m") or 0)}</td>'
        '<td></td>'
        f'<td class="r">{_moeda(tot.get("valor_mercado"))}</td>'
        f'<td class="r">{_pct(tot.get("pct_patrimonio"))}</td>'
        '<td></td><td></td><td></td></tr>'
    )

    ausentes = [a for a in (fontes_ausentes or []) if a]
    aviso = (f' <b>Indisponível agora:</b> {_e(", ".join(ausentes))} — as células '
             'que dependem disso ficam "—".') if ausentes else ""
    rodape = (
        '<div class="tp-foot">'
        '<b>Valores em R$</b>; ativo em dólar convertido pelo câmbio da carteira. '
        '<b>% do setor</b>: peso do ativo entre as posições do mesmo setor. '
        f'<b>1º aporte</b>: compra mais antiga no extrato de Negociação da B3 (começa em '
        f'{INICIO_EXTRATO_B3}) ou nas notas da Nomad, ou o crédito mais antigo na Movimentação; '
        '"até" marca ativo que só aparece em foto de posição — a data é a da primeira foto, não a do aporte. '
        '<b>Corretora</b>: instituição do crédito mais recente na Movimentação da B3; fora da B3, '
        'a da foto da corretora.' + aviso + '</div>'
    )

    return (
        CSS
        + '<div class="tp-wrap">' + head
        + '<div class="tp-scroll"><table class="tp-t"><thead><tr>' + ths + '</tr></thead>'
        + '<tbody>' + "".join(corpo) + '</tbody></table></div>'
        + rodape + '</div>'
    )
