"""Composição calculada com saldo não alocado e metas pendentes explícitas.

Por que esta tela foi reescrita em cards
----------------------------------------
Ela é o fallback de **duas** telas — a carteira-modelo de FIIs e a criação de
portfólio em Empresas Americanas — e ocupava as duas inteiras quando nenhuma
carteira se formava. O que o usuário via era "Alocado 0,0% / Não alocado
100,0%" em dois ``st.metric`` crus, um parágrafo cinza e nada mais. Duas
consequências medidas:

* o resultado parecia um veredito sobre os ativos ("não há nada bom para
  comprar") quando quase sempre é um problema de **dado**: vitrine vencida,
  universo que chegou vazio, medição de liquidez fora do prazo;
* nenhuma informação ficava em card, contrariando o padrão visual do resto do
  app, e o diagnóstico — quando existia — chegava como legenda embaixo de um
  zero.

A distinção que a tela passa a fazer
------------------------------------
``solver_status`` já separa três mundos, e eles pedem providências opostas:

=================  =========================================================
``no_candidates``  nenhum candidato chegou ao otimizador. Não é excesso de
                   rigor: é universo vazio. Afrouxar parâmetro não resolve —
                   o remédio é republicar a vitrine / verificar a fonte.
``not_proven``     havia candidatos, o solver não comprovou a alocação.
``optimal`` /      alocou, mas parcialmente: o saldo não alocado é o que os
``feasible``       tetos de proteção não comportaram.
=================  =========================================================

O ``diagnostico`` opcional deixa a tela chamadora acrescentar a causa que só
ela conhece (ex.: "vitrine dos EUA com medição de giro de 20/08"), sem que este
componente precise saber de vitrines.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from design.componentes import card_metrica, secao_titulo

_COR_ALERTA = "#FC5C7D"
_COR_ATENCAO = "#F6C90E"
_COR_NEUTRA = "#4A9EFF"
_COR_OK = "#00C896"


def _card_causa(titulo: str, texto: str, *, cor: str, icone: str) -> None:
    """Card de causa/remédio. Existe para que o diagnóstico não seja legenda."""
    st.markdown(
        f'<div style="display:flex;gap:14px;align-items:flex-start;'
        f'padding:14px 16px;background:#1A1F2E;border:1px solid #2D3748;'
        f'border-left:4px solid {cor};border-radius:10px;margin-bottom:10px">'
        f'<div style="font-size:1.3rem;line-height:1.2">{icone}</div>'
        f'<div><div style="font-size:0.95rem;font-weight:700;color:#E2E8F0">'
        f'{titulo}</div>'
        f'<div style="font-size:0.84rem;color:#9CA3AF;margin-top:4px;'
        f'line-height:1.5">{texto}</div></div></div>',
        unsafe_allow_html=True,
    )


def _leitura_do_status(result) -> tuple[str, str, str, str]:
    """``(titulo, texto, cor, icone)`` — a frase muda com a causa, não com o zero.

    Separar ``no_candidates`` do resto é a razão de ser desta função: as duas
    situações produzem o mesmo "100% não alocado" na tela e pedem ações opostas
    do usuário. Confundi-las foi o que fez "nenhuma carteira foi formada" virar
    um beco sem saída.
    """
    status = str(result.get("solver_status") or "")
    if result.get("items"):
        return (
            "Alocação parcial: o saldo não é sobra, é limite de proteção",
            "Os tetos por ativo, setor e concentração impediram alocar o capital "
            "inteiro nos candidatos aprovados. O saldo não alocado não representa "
            "investimento nem retorno presumido.",
            _COR_ATENCAO, "⚖️")
    if status == "no_candidates":
        return (
            "Nenhum candidato chegou ao otimizador",
            "O universo chegou vazio a esta execução — nenhum ativo foi reprovado "
            "por mérito, porque nenhum foi avaliado. Afrouxar parâmetros não muda "
            "este resultado. Verifique a fonte de dados desta aba: vitrine não "
            "publicada, publicação vencida ou leitura que falhou produzem "
            "exatamente esta tela.",
            _COR_ALERTA, "🚫")
    if status == "not_proven":
        return (
            "A alocação não foi comprovada nesta execução",
            "Havia candidatos, mas o otimizador não conseguiu provar uma "
            "composição dentro dos limites no tempo disponível. Reduzir o número "
            "de ativos ou afrouxar um teto costuma destravar.",
            _COR_ATENCAO, "⏱️")
    return (
        "Nenhuma composição coube nos limites de proteção",
        "Havia candidatos, mas nenhuma combinação respeita simultaneamente os "
        "tetos configurados. Aqui, sim, afrouxar um limite é o caminho — comece "
        "pelo teto por ativo ou pelo máximo por setor.",
        _COR_ALERTA, "🔒")


def render_portfolio_review(result, *, key, diagnostico=None):
    """Renderiza a composição de revisão.

    ``diagnostico`` aceita ``str`` ou sequência de ``str``: são causas que a
    tela chamadora conhece e este componente não (frescor de vitrine, por
    exemplo). Entram em card, acima dos números, porque causa que interrompe a
    formação da carteira é informação de topo, não rodapé.
    """
    secao_titulo("Composição possível com proteção ao investidor", "🧮",
                 "Proposta para revisão. Os pesos se referem ao capital total.")

    titulo, texto, cor, icone = _leitura_do_status(result)
    _card_causa(titulo, texto, cor=cor, icone=icone)

    if diagnostico:
        linhas = [diagnostico] if isinstance(diagnostico, str) else list(diagnostico)
        for linha in linhas:
            if str(linha).strip():
                _card_causa("Diagnóstico desta aba", str(linha),
                            cor=_COR_ALERTA, icone="🩺")

    alocado = float(result.get("allocated_weight") or 0.0)
    esquerda, direita, terceira = st.columns(3)
    with esquerda:
        card_metrica("Alocado em ativos", f"{alocado:.1%}",
                     accent=_COR_OK if alocado > 0 else _COR_ALERTA)
    with direita:
        card_metrica("Capital não alocado",
                     f"{float(result.get('unallocated_weight') or 0.0):.1%}",
                     accent=_COR_ATENCAO if alocado > 0 else _COR_ALERTA)
    with terceira:
        card_metrica("Ativos na composição", len(result.get("items") or ()),
                     accent=_COR_NEUTRA)

    rows = [{"Ativo": row["ticker"], "Peso do capital total": f"{row['weight']:.2%}",
             "Categoria / setor": row.get("tipo") or row.get("sector_group") or row.get("setor"),
             "Score observado": row.get("type_score", row.get("entry_score", row.get("quality")))}
            for row in result.get("items") or ()]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    # As ressalvas deixam de ser `st.caption`: elas descrevem QUAIS proteções
    # continuaram valendo, e é essa lista que responde "o que eu poderia mudar".
    razoes = [str(reason) for reason in (result.get("reasons") or ()) if str(reason).strip()]
    if razoes:
        st.markdown(
            '<div style="padding:12px 16px;background:#1A1F2E;border:1px solid '
            '#2D3748;border-radius:10px;margin:6px 0 10px">'
            '<div style="font-size:0.8rem;font-weight:700;color:#E2E8F0;'
            'letter-spacing:.02em;text-transform:uppercase">Proteções aplicadas '
            'nesta execução</div>'
            + "".join(
                f'<div style="font-size:0.84rem;color:#9CA3AF;margin-top:6px;'
                f'line-height:1.45">• {reason}</div>' for reason in razoes)
            + "</div>",
            unsafe_allow_html=True,
        )

    targets = result.get("category_targets") or {}
    if targets:
        st.markdown("**Metas por categoria e composição obtida**")
        st.dataframe(pd.DataFrame([
            {"Categoria": kind, "Meta mínima": f"{values['minimum']:.0%}",
             "Meta máxima": f"{values['maximum']:.0%}",
             "Alocação obtida": f"{values['actual']:.1%}"}
            for kind, values in targets.items()]), hide_index=True, width="stretch")

    # Exportação só da composição visível e das ressalvas: nenhum dado bruto.
    payload = {"status": "proposta_para_revisao", "can_publish": False,
               "solver_status": result.get("solver_status"),
               "items": [{"ticker": row["ticker"], "weight": row["weight"]}
                         for row in result.get("items") or ()],
               "unallocated_weight": result.get("unallocated_weight"),
               "reasons": razoes, "category_targets": targets}
    st.download_button("Baixar composição para revisão",
                       json.dumps(payload, ensure_ascii=False, indent=2),
                       file_name=f"{key}_composicao.json", mime="application/json",
                       key=f"{key}_download")
