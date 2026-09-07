"""
design/tesouro_mtm_cards.py — cards da marcação a mercado do Tesouro Direto.

Só monta HTML. Nenhuma função aqui decide nada: o veredito vem pronto de
`core.tesouro_mtm`, e o que se faz aqui é **mostrar a conta**, não refazê-la.
Essa separação é deliberada — número que a tela calcula sozinha não é testável
e diverge do motor sem ninguém perceber.

Cada card sai em **uma** string e vai para um único `st.markdown`. Abrir a
`<div>` num bloco e fechar em outro já produziu moldura vazia neste projeto.
"""
from __future__ import annotations

from datetime import date

from core.tesouro_mtm import (
    AVALIAR_TROCA,
    MANTER,
    SEM_BASE,
    VENDA_DESVANTAJOSA,
    Comparacao,
)
from core.utils import fmt_moeda

_FUNDO = "#12151E"
_BORDA = "#1E2533"
_TEXTO = "#E2E8F0"
_TEXTO2 = "#CBD5E0"
_TEXTO3 = "#9CA3AF"
_TEXTO4 = "#718096"

POSITIVO = "#00C896"
NEGATIVO = "#FC5C7D"
INFO = "#4A9EFF"
ALERTA = "#F6C90E"
NEUTRO = "#9CA3AF"

_COR_VEREDITO = {
    MANTER: POSITIVO,
    AVALIAR_TROCA: ALERTA,
    VENDA_DESVANTAJOSA: NEGATIVO,
    SEM_BASE: NEUTRO,
}

_ICONE_VEREDITO = {
    MANTER: "🟢",
    AVALIAR_TROCA: "🟡",
    VENDA_DESVANTAJOSA: "🔴",
    SEM_BASE: "⚪",
}

_ROTULO_FONTE = {
    "curva": "Preço de mercado da curva oficial",
    "extrato": "Valor congelado do extrato — a curva não cobre este título",
    "indisponivel": "Sem preço: nem curva, nem valor no extrato",
}


def _pct(valor: float | None, casas: int = 2, sinal: bool = False) -> str:
    if valor is None:
        return "—"
    fmt = f"{{:+.{casas}f}}%" if sinal else f"{{:.{casas}f}}%"
    return fmt.format(valor * 100)


def _dinheiro(valor: float | None, sinal: bool = False) -> str:
    if valor is None:
        return "—"
    prefixo = "+" if (sinal and valor >= 0) else ""
    return f"{prefixo}{fmt_moeda(valor)}"


def _celula(rotulo: str, valor: str, cor: str = _TEXTO2) -> str:
    return (f'<div><div style="font-size:0.65rem;color:{_TEXTO4};'
            f'letter-spacing:0.04em;">{rotulo}</div>'
            f'<div style="font-size:0.85rem;font-weight:700;color:{cor};">{valor}</div></div>')


def card_titulo_html(titulo) -> str:
    """Card de um `core.tesouro_posicao.TituloAnalitico`.

    A procedência do preço aparece sempre, e não só quando falta: valor da data
    do extrato com cara de preço de hoje é exatamente o defeito que a barra de
    'fonte' existe para impedir.
    """
    mtm = titulo.mtm_pct
    cor = NEUTRO if mtm is None else (POSITIVO if mtm >= 0 else NEGATIVO)
    fonte = titulo.fonte_preco
    cor_fonte = POSITIVO if fonte == "curva" else ALERTA
    data_curva = titulo.data_curva.strftime("%d/%m/%Y") if titulo.data_curva else "—"
    venc = titulo.vencimento.strftime("%d/%m/%Y") if titulo.vencimento else "—"
    lotes = len(titulo.lotes)

    taxas = sorted({lote.taxa_contratada.texto for lote in titulo.lotes
                    if lote.taxa_contratada is not None})
    taxa_txt = taxas[0] if len(taxas) == 1 else (f"{len(taxas)} taxas contratadas" if taxas else "—")

    aviso = ""
    if titulo.aproximado:
        aviso = (f'<div style="font-size:0.72rem;color:{ALERTA};margin-top:8px;">'
                 f'⚠️ Título com juros semestrais: o cupom não cancela na razão de PUs, '
                 f'então a marcação aqui é aproximação, não identidade.</div>')

    return (
        f'<div style="background:{_FUNDO};border:1px solid {_BORDA};'
        f'border-left:4px solid {cor};border-radius:10px;padding:16px 18px;margin-bottom:12px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">'
        f'  <div>'
        f'    <div style="font-size:1.05rem;font-weight:800;color:{_TEXTO};">{titulo.titulo}</div>'
        f'    <div style="font-size:0.72rem;color:{_TEXTO3};margin-top:2px;">'
        f'      Vencimento {venc} · {lotes} aplicaç{"ões" if lotes != 1 else "ão"} · {taxa_txt}</div>'
        f'  </div>'
        f'  <div style="text-align:right;">'
        f'    <div style="font-size:0.65rem;font-weight:800;color:{cor};'
        f'      text-transform:uppercase;letter-spacing:0.06em;">MARCAÇÃO A MERCADO</div>'
        f'    <div style="font-size:1.15rem;font-weight:800;color:{cor};margin-top:4px;">'
        f'      {_pct(mtm, sinal=True)}</div>'
        f'    <div style="font-size:0.68rem;color:{_TEXTO3};">'
        f'      {_dinheiro(titulo.ganho_mtm_reais, sinal=True)}</div>'
        f'  </div>'
        f'</div>'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;'
        f'  padding:8px 0;border-top:1px solid {_BORDA};">'
        f'{_celula("INVESTIDO", _dinheiro(titulo.valor_investido))}'
        f'{_celula("BRUTO HOJE", _dinheiro(titulo.valor_bruto))}'
        f'{_celula("IR + IOF", _dinheiro((titulo.ir or 0.0) + (titulo.iof or 0.0)), ALERTA)}'
        f'{_celula("LÍQUIDO SE RESGATAR", _dinheiro(titulo.valor_liquido), _TEXTO)}'
        f'</div>'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;'
        f'  padding:8px 0;border-top:1px solid {_BORDA};">'
        f'{_celula("TAXA CONTRATADA", taxa_txt)}'
        f'{_celula("TAXA DE MERCADO (VENDA)", _pct(titulo.taxa_mercado_venda))}'
        f'{_celula("ÍNDICE IMPLÍCITO", _pct(titulo.taxa_indice) if titulo.indexador != "PRE" else "n/a")}'
        f'{_celula("QUANTIDADE", f"{titulo.quantidade:.4f}".replace(".", ","))}'
        f'</div>'
        f'<div style="font-size:0.72rem;color:{cor_fonte};margin-top:8px;">'
        f'  ● {_ROTULO_FONTE.get(fonte, fonte)}'
        f'{f" · curva de {data_curva}" if fonte == "curva" else ""}</div>'
        f'{aviso}'
        f'</div>'
    )


def card_veredito_html(titulo, comparacao: Comparacao,
                       alternativa: str | None = None) -> str:
    """Card do veredito, com a aritmética à vista.

    O veredito não é conselho: é o resultado de uma comparação entre duas
    pernas que terminam na mesma data. Por isso o card imprime as duas pontas
    e o imposto antecipado — quem lê pode refazer a conta e discordar.
    """
    cor = _COR_VEREDITO.get(comparacao.veredito, NEUTRO)
    icone = _ICONE_VEREDITO.get(comparacao.veredito, "⚪")
    alvo = alternativa or "nenhuma alternativa escolhida"

    if comparacao.valor_final_carregando is None:
        grade = ""
    else:
        grade = (
            f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;'
            f'  padding:8px 0;border-top:1px solid {_BORDA};margin-top:8px;">'
            f'{_celula("CARREGAR ATÉ O FIM", _dinheiro(comparacao.valor_final_carregando))}'
            f'{_celula("VENDER E TROCAR", _dinheiro(comparacao.valor_final_vendendo))}'
            f'{_celula("DIFERENÇA", _dinheiro(comparacao.vantagem_reais, sinal=True), cor)}'
            f'{_celula("IR/IOF ANTECIPADO", _dinheiro(comparacao.imposto_antecipado), ALERTA)}'
            f'</div>'
        )

    return (
        f'<div style="background:{_FUNDO};border:1px solid {_BORDA};'
        f'border-left:4px solid {cor};border-radius:10px;padding:16px 18px;margin-bottom:12px;">'
        f'<div style="font-size:0.65rem;font-weight:800;color:{_TEXTO4};'
        f'  text-transform:uppercase;letter-spacing:0.06em;">VEREDITO · {titulo.titulo}</div>'
        f'<div style="font-size:1.25rem;font-weight:800;color:{cor};margin:4px 0 6px;">'
        f'  {icone} {comparacao.veredito}</div>'
        f'<div style="font-size:0.78rem;color:{_TEXTO3};line-height:1.5;">{comparacao.motivo}</div>'
        f'{grade}'
        f'<div style="font-size:0.70rem;color:{_TEXTO4};margin-top:8px;line-height:1.5;">'
        f'  Alternativa comparada: <strong style="color:{_TEXTO2};">{alvo}</strong> · '
        f'  {comparacao.du_restante} dias úteis até o vencimento · '
        f'  faixa de indiferença de 0,5% do valor da posição.'
        f'{" · Aproximação (título com cupom)." if comparacao.aproximado else ""}</div>'
        f'</div>'
    )


def card_conjuntura_html(*, data_curva: date | None, pre_curto: dict | None,
                         pre_longo: dict | None, inflacao_implicita: float | None,
                         macro_ano: dict | None = None) -> str:
    """Leitura de conjuntura **datada**, feita de preço, não de opinião.

    Deliberadamente não usa `public.macro` como "hoje": aquela tabela é anual e
    o valor mais recente pode ter meses. Os números de mercado abaixo têm a data
    da curva impressa ao lado; os anuais aparecem rotulados pelo ano a que se
    referem.
    """
    quando = data_curva.strftime("%d/%m/%Y") if data_curva else "—"

    def taxa(linha: dict | None) -> str:
        if not linha:
            return "—"
        return _pct(linha.get("sell_rate_dec"))

    def nome(linha: dict | None) -> str:
        if not linha:
            return "sem título"
        venc = linha.get("maturity_date")
        return f"{linha.get('title_name', '')} {venc.year if venc else ''}".strip()

    inclinacao = None
    if pre_curto and pre_longo:
        curta, longa = pre_curto.get("sell_rate_dec"), pre_longo.get("sell_rate_dec")
        if curta is not None and longa is not None:
            inclinacao = longa - curta

    if inclinacao is None:
        leitura = "Sem dois vértices prefixados para medir a inclinação da curva."
    elif inclinacao > 0.005:
        leitura = ("Curva ascendente: o mercado cobra prêmio para prazo longo. Prefixado "
                   "longo paga mais, e é onde a marcação oscila mais se o juro subir de novo.")
    elif inclinacao < -0.005:
        leitura = ("Curva invertida: juro curto acima do longo. Historicamente é o desenho "
                   "de expectativa de queda de juros à frente — o cenário em que prefixado "
                   "e IPCA+ longos valorizam e pós-fixado perde atratividade relativa.")
    else:
        leitura = "Curva praticamente plana entre os vértices observados."

    linha_macro = ""
    if macro_ano:
        ano = macro_ano.get("ano")
        partes = [f"Selic {macro_ano['selic']:.2f}%" if macro_ano.get("selic") is not None else None,
                  f"IPCA {macro_ano['ipca']:.2f}%" if macro_ano.get("ipca") is not None else None]
        partes = [p for p in partes if p]
        if partes:
            linha_macro = (f'<div style="font-size:0.70rem;color:{_TEXTO4};margin-top:8px;">'
                           f'Referência anual de <strong>{ano}</strong> (tabela macro, não é '
                           f'leitura de hoje): {" · ".join(partes)}.</div>')

    return (
        f'<div style="background:{_FUNDO};border:1px solid {_BORDA};'
        f'border-left:4px solid {INFO};border-radius:10px;padding:16px 18px;margin-bottom:12px;">'
        f'<div style="font-size:0.65rem;font-weight:800;color:{INFO};'
        f'  text-transform:uppercase;letter-spacing:0.06em;">CONJUNTURA · CURVA DE {quando}</div>'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;'
        f'  padding:10px 0 8px;">'
        f'{_celula(f"PRÉ CURTO ({nome(pre_curto)})", taxa(pre_curto))}'
        f'{_celula(f"PRÉ LONGO ({nome(pre_longo)})", taxa(pre_longo))}'
        f'{_celula("INCLINAÇÃO (LONGO − CURTO)", _pct(inclinacao, sinal=True))}'
        f'{_celula("INFLAÇÃO IMPLÍCITA", _pct(inflacao_implicita))}'
        f'</div>'
        f'<div style="font-size:0.78rem;color:{_TEXTO3};line-height:1.5;'
        f'  border-top:1px solid {_BORDA};padding-top:8px;">{leitura}</div>'
        f'{linha_macro}'
        f'</div>'
    )
