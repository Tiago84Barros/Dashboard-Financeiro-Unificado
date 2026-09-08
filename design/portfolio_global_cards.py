"""
design/portfolio_global_cards.py — cards por ativo do Portfólio Global.

Substitui os `st.expander` de "Papel estratégico" e "Recomendações do motor de
movimentação". Um expander por ativo escondia o conteúdo atrás de um clique
cada: com 41 recomendações e 13 papéis, ler a tela custava 54 cliques, e o que
não se lê não influencia decisão nenhuma.

Só monta HTML. Nenhuma função aqui decide nada — papéis vêm de
`core.global_portfolio.roles`, recomendações de `...advisor`, e o custo de
`views.portfolio_global.texto_de_custo`. Refazer a conta aqui daria dois
números para o mesmo ativo, e a divergência não apareceria na tela.

Cada card sai em **uma** string e vai para um único `st.markdown`. Abrir a
`<div>` num bloco e fechar em outro já produziu moldura vazia neste projeto.
"""
from __future__ import annotations

from html import escape

from core.global_portfolio import advisor, roles

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


def _t(texto: object) -> str:
    """Escapa e achata — linha em branco fecha o bloco HTML do Streamlit."""
    return escape(" ".join(str(texto).split()), quote=True)


def _moldura(accent: str, miolo: str) -> str:
    return (
        f'<div style="background:{_FUNDO};border:1px solid {_BORDA};'
        f'border-left:3px solid {accent};border-radius:10px;'
        f'padding:12px 14px;height:100%;margin-bottom:10px;">{miolo}</div>'
    )


def _cabecalho(symbol: str, rotulo: str, cor: str,
               subtitulo: str = "") -> str:
    sub = (
        f'<div style="font-size:0.63rem;color:{_TEXTO4};'
        f'margin:-4px 0 8px 0;">{_t(subtitulo)}</div>' if subtitulo else ""
    )
    return (
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:baseline;gap:8px;margin-bottom:8px;">'
        f'<div style="font-size:1.0rem;font-weight:800;color:{_TEXTO};'
        f'letter-spacing:-0.01em;">{_t(symbol)}</div>'
        f'<div style="font-size:0.62rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.08em;color:{cor};">{_t(rotulo)}</div></div>'
        f'{sub}'
    )


def _procedencia(classe_label: str | None, peso: float | None) -> str:
    """Linha 'de onde vem e quanto pesa', sob o ticker.

    O card dizia só o ticker. Fora da seção da classe, PETR4, MXRF11 e AAPL
    ficam indistinguíveis quanto à origem, e nada na tela diz se o papel
    atribuído vale para 0,4% ou para 12% do patrimônio. Peso ausente não vira
    0,0%: omitir é diferente de afirmar que a posição é irrelevante.
    """
    partes: list[str] = []
    if classe_label:
        partes.append(str(classe_label))
    try:
        if peso is not None and not (isinstance(peso, float) and peso != peso):
            partes.append(f"{float(peso) * 100:.2f}% do patrimônio")
    except (TypeError, ValueError):
        pass
    return " · ".join(partes)


def _linha_papel(icone: str, titulo: str, detalhe: str, cor: str) -> str:
    detalhe_html = (
        f'<div style="font-size:0.68rem;color:{_TEXTO4};line-height:1.35;'
        f'margin-left:16px;">{_t(detalhe)}</div>' if detalhe else ""
    )
    return (
        f'<div style="margin-bottom:5px;">'
        f'<div style="font-size:0.74rem;color:{cor};font-weight:600;">'
        f'{icone} {_t(titulo)}</div>{detalhe_html}</div>'
    )


def card_papel_html(entrada: roles.PapelDoAtivo, *,
                    classe_label: str | None = None,
                    peso: float | None = None) -> str:
    """Card de um `roles.PapelDoAtivo` — o mesmo conteúdo da tabela antiga.

    A tabela tinha uma linha por papel, sete no total, e para a maioria dos
    ativos cinco delas eram "— Não cumpre / —": régua de nada. Aqui cada papel
    cumprido ganha sua evidência numérica e os demais são agrupados em uma
    linha só. O que **não** muda é a distinção de três estados: "indeterminado"
    (sem dado para avaliar) nunca se mistura com "não cumpre" (regra avaliada e
    negada) — dizer "não cumpre" onde o certo é "não sabemos" afirmaria algo
    que o dado não sustenta.

    `classe_label` e `peso` são opcionais e só compõem a linha de procedência
    sob o ticker: origem e importância no patrimônio. Ausentes, o card é o que
    sempre foi — quem chama de fora do painel (teste, chat) não fica obrigado
    a inventar um peso.
    """
    evidencia_por_papel = {e.papel: e for e in entrada.evidencias}
    cumpridos = [p for p in roles.PAPEIS if p in entrada.papeis]
    indeterminados = [p for p in roles.PAPEIS if p in entrada.indeterminados]
    nao_cumpre = [
        p for p in roles.PAPEIS
        if p not in entrada.papeis and p not in entrada.indeterminados
    ]

    accent = INFO if cumpridos else NEGATIVO
    rotulo = (
        f"{len(cumpridos)} papel" if len(cumpridos) == 1
        else f"{len(cumpridos)} papéis" if cumpridos
        else "nenhum papel"
    )
    partes = [_cabecalho(entrada.symbol, rotulo, accent,
                         _procedencia(classe_label, peso))]

    for papel in cumpridos:
        evidencia = evidencia_por_papel.get(papel)
        partes.append(_linha_papel(
            "✅", roles.ROTULOS_PAPEL[papel],
            evidencia.texto if evidencia else "", POSITIVO,
        ))
    if not cumpridos:
        partes.append(_linha_papel(
            "🔴", "Nenhum papel identificado",
            "nenhuma regra teve evidência suficiente para este ativo", NEGATIVO,
        ))
    # Indeterminado com causa conhecida ganha linha própria com a causa:
    # "sem dado suficiente" descreve o sintoma e não diz o que falta buscar.
    # Os demais continuam agrupados sob a frase genérica, que ali é honesta —
    # a falta é pontual do ativo, não uma lacuna de fonte.
    motivos = dict(getattr(entrada, "motivos_indeterminado", ()) or ())
    for papel in [p for p in indeterminados if p in motivos]:
        partes.append(_linha_papel(
            "❔", f"Indeterminado: {roles.ROTULOS_PAPEL[papel]}",
            motivos[papel], ALERTA,
        ))
    restantes = [p for p in indeterminados if p not in motivos]
    if restantes:
        partes.append(_linha_papel(
            "❔", "Indeterminado: " + ", ".join(
                roles.ROTULOS_PAPEL[p] for p in restantes),
            "sem dado suficiente para avaliar — não é o mesmo que não cumprir",
            ALERTA,
        ))
    if nao_cumpre:
        partes.append(_linha_papel(
            "—", "Não cumpre: " + ", ".join(
                roles.ROTULOS_PAPEL[p] for p in nao_cumpre),
            "", _TEXTO4,
        ))
    if entrada.justificativa:
        partes.append(
            f'<div style="font-size:0.66rem;color:{_TEXTO4};line-height:1.35;'
            f'border-top:1px solid {_BORDA};margin-top:8px;padding-top:7px;">'
            f'{_t(entrada.justificativa)}</div>'
        )
    return _moldura(accent, "".join(partes))


def card_recomendacao_html(acao: advisor.Acao, rotulo: str, accent: str,
                           custo: str) -> str:
    """Card de uma `advisor.Acao`.

    `rotulo`, `accent` e `custo` chegam prontos da view — em especial `custo`,
    que é `texto_de_custo` e existe para interceptar o `math.nan` da classe não
    calibrada ANTES de qualquer formatação numérica. Formatar o custo aqui
    imprimiria "nan" onde a regra manda dizer "não calibrado".
    """
    partes = [_cabecalho(acao.symbol, rotulo, accent)]
    partes.append(
        f'<div style="font-size:0.95rem;font-weight:800;color:{_TEXTO};'
        f'letter-spacing:-0.01em;">'
        f'{acao.peso_atual * 100:.2f}% → {acao.peso_sugerido * 100:.2f}%</div>'
        f'<div style="font-size:0.66rem;color:{_TEXTO4};line-height:1.35;'
        f'margin-bottom:7px;">{_t(custo)}</div>'
    )
    if acao.score is not None:
        partes.append(
            f'<div style="font-size:0.68rem;color:{_TEXTO3};">'
            f'score {acao.score:+.3f}</div>'
        )
    if acao.componentes:
        # Os sinais em linha, ordenados por nome: o dataframe antigo custava um
        # clique e uma tabela inteira para mostrar quatro pares nome/valor.
        sinais = " · ".join(
            f'{_t(nome)} <span style="color:{_TEXTO2};font-weight:700;">'
            f'{valor:+.3f}</span>'
            for nome, valor in sorted(acao.componentes.items())
        )
        partes.append(
            f'<div style="font-size:0.66rem;color:{_TEXTO4};line-height:1.5;'
            f'margin-top:4px;">{sinais}</div>'
        )
    if acao.analisadores:
        partes.append(
            f'<div style="font-size:0.63rem;color:{_TEXTO4};margin-top:5px;">'
            f'analisadores: {_t(", ".join(sorted(acao.analisadores)))}</div>'
        )
    else:
        partes.append(
            f'<div style="font-size:0.63rem;color:{_TEXTO4};margin-top:5px;">'
            f'nenhum analisador produziu sinal para este ativo</div>'
        )
    if acao.macro_delta is not None:
        cor = POSITIVO if acao.macro_delta >= 0 else NEGATIVO
        partes.append(
            f'<div style="font-size:0.66rem;color:{cor};margin-top:5px;">'
            f'macro desde a criação: {acao.macro_delta:+.2f}/100</div>'
        )
    if not acao.custo_calibrado:
        partes.append(
            f'<div style="font-size:0.63rem;color:{ALERTA};margin-top:6px;'
            f'border-top:1px solid {_BORDA};padding-top:6px;">'
            f'⚠️ custo não calibrado — mantida em vez de executada</div>'
        )
    return _moldura(accent, "".join(partes))
