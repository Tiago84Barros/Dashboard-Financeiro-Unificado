"""
views/ir_renda_variavel.py
Aba "Imposto de Renda" de Investimentos: apuração mensal do ganho de capital
em renda variável, DARF e prejuízo a compensar.

O cálculo mora em ``core/ir_renda_variavel.py``; aqui só se carrega, formata
e explica. A tela precisa dizer com a mesma ênfase o que sabe e o que não
sabe: mês com venda sem custo de aquisição não tem imposto conhecido.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from core.ir_renda_variavel import CESTAS, CODIGO_DARF, ROTULO_CESTA, apurar
from core.user_context import user_cache_data
from core.utils import fmt_moeda
from design.componentes import card_metrica, secao_titulo

_MESES = ["", "jan", "fev", "mar", "abr", "mai", "jun",
          "jul", "ago", "set", "out", "nov", "dez"]


@user_cache_data(ttl=900, show_spinner=False)
def _apuracao() -> dict | None:
    from core.config import settings
    from core.database import get_engine
    from core.ir_renda_variavel import carregar_operacoes

    engine = get_engine()
    owner = getattr(settings, "OWNER_USER_ID", None)
    if engine is None or not owner:
        return None
    transacoes, eventos = carregar_operacoes(engine, owner)
    return apurar(transacoes, eventos)


def _rotulo_mes(mes: str) -> str:
    return f"{_MESES[int(mes[5:7])]}/{mes[:4]}"


def _f(v: Decimal) -> float:
    return float(v or 0)


def _situacao(m: dict) -> str:
    if m["em_curso"]:
        return "Mês em curso"
    if m["incompleto"]:
        return "Incompleto: venda sem custo"
    if m["darf"] > 0:
        return f"DARF até {m['vencimento'].strftime('%d/%m/%Y')}"
    if m["acumulado_proximo"] > 0:
        return "Abaixo de R$ 10: acumula"
    return "Nada a pagar"


def _tabela_mensal(meses: list[dict]) -> pd.DataFrame:
    linhas = []
    for m in reversed(meses):
        c = m["cestas"]
        linhas.append({
            "Mês": _rotulo_mes(m["mes"]),
            "Vendas de ações": _f(m["vendas_acoes"]),
            "Isenção": "Sim" if m["isento_acoes"] else "Não",
            "Ganho isento": _f(c["comum"]["ganho_isento"]),
            "Comum": _f(c["comum"]["resultado"]),
            "Day trade": _f(c["day_trade"]["resultado"]),
            "FII": _f(c["fii"]["resultado"]),
            "Prejuízo usado": _f(sum((c[k]["prejuizo_compensado"] for k in CESTAS), Decimal(0))),
            "Imposto": _f(m["imposto"]),
            "IRRF est.": _f(m["irrf_deduzido"]),
            "DARF": _f(m["darf"]),
            "Situação": _situacao(m),
        })
    return pd.DataFrame(linhas)


def _tabela_anual(anos: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Ano": str(a["ano"]),
        "Ganho isento (ações até R$ 20 mil)": _f(a["ganho_isento"]),
        "DARFs do ano": _f(a["darf"]),
        "Prejuízo comum em 31/12": _f(a["prejuizo_fim"]["comum"]),
        "Prejuízo day trade em 31/12": _f(a["prejuizo_fim"]["day_trade"]),
        "Prejuízo FII em 31/12": _f(a["prejuizo_fim"]["fii"]),
        "IRRF a declarar (est.)": _f(a["irrf_a_declarar"]),
        "Meses incompletos": len(a["meses_incompletos"]),
    } for a in reversed(anos)])


_COLS_MOEDA = st.column_config.NumberColumn(format="R$ %.2f")


def render() -> None:
    secao_titulo(
        "Imposto de Renda — renda variável", "🧾",
        "Ganho de capital mês a mês, DARF (código 6015) e prejuízo a compensar, "
        "a partir do extrato de Negociação e da Movimentação da B3.",
    )
    try:
        res = _apuracao()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível carregar as operações: {type(exc).__name__}.", icon="🚫")
        return
    if res is None:
        st.info("A apuração precisa do banco com as operações importadas.", icon="ℹ️")
        return
    meses = res["meses"]
    if not meses:
        st.info("Nenhuma venda de ações, FII, ETF ou BDR no extrato importado.", icon="ℹ️")
        return

    hoje = date.today()
    ultimo = meses[-1]
    pend = res["pendente"]
    ano = next((a for a in res["anos"] if a["ano"] == hoje.year), None)
    prej = {c: ultimo["cestas"][c]["prejuizo_a_compensar"] for c in CESTAS}
    prej_incerto = any(ultimo["cestas"][c]["prejuizo_incerto"] for c in CESTAS)
    n_incompletos = sum(1 for m in meses if m["incompleto"])

    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        if pend:
            card_metrica(
                "DARF em aberto", fmt_moeda(_f(pend["darf"])),
                f"{_rotulo_mes(pend['mes'])} · vence {pend['vencimento'].strftime('%d/%m/%Y')}",
                positivo=False,
                ajuda="Último mês fechado com imposto cujo vencimento ainda não passou. "
                      "Confira se já foi pago: o app não lê pagamentos.",
            )
        else:
            card_metrica("DARF em aberto", "Nenhum", "sem mês fechado com imposto a vencer",
                         positivo=True)
    with c2:
        em_curso = ultimo if ultimo["em_curso"] else None
        card_metrica(
            "Mês em curso", fmt_moeda(_f(em_curso["imposto"])) if em_curso else "Sem vendas",
            (f"vendas de ações {fmt_moeda(_f(em_curso['vendas_acoes']))} de "
             f"{fmt_moeda(20000)} isentos") if em_curso else "nenhuma venda neste mês",
            positivo=None,
            ajuda="Imposto parcial do mês corrente, antes da compensação do IRRF.",
        )
    with c3:
        total_prej = sum(prej.values(), Decimal(0))
        card_metrica(
            "Prejuízo a compensar", fmt_moeda(_f(total_prej)),
            " · ".join(f"{ROTULO_CESTA[c]} {fmt_moeda(_f(v))}" for c, v in prej.items() if v)
            or "nenhum",
            positivo=None,
            ajuda="Só compensa dentro da mesma cesta. "
                  + ("Valor incerto: há venda sem custo conhecido no histórico." if prej_incerto
                     else ""),
        )
    with c4:
        card_metrica(
            f"Ganho isento em {hoje.year}",
            fmt_moeda(_f(ano["ganho_isento"])) if ano else fmt_moeda(0),
            "vai na ficha de rendimentos isentos da declaração",
            positivo=True,
        )

    if n_incompletos:
        sem = sorted({t for m in meses for c in CESTAS for t in m["cestas"][c]["sem_custo"]})
        st.warning(
            f"**{n_incompletos} mês(es) com venda sem custo de aquisição** — {', '.join(sem)}. "
            "São ativos comprados antes do início do extrato da B3 (nov/2019). O ganho dessas "
            "vendas não é conhecido, e não foi tratado nem como lucro nem como prejuízo: o "
            "imposto desses meses cobre só o resto, e o prejuízo que eles carregam para a "
            "frente fica incerto. Para fechar a conta, use o custo das notas de corretagem "
            "da época.",
            icon="⚠️",
        )
    if prej_incerto and not n_incompletos:
        st.caption("O prejuízo acumulado herda uma venda sem custo de meses anteriores.")

    st.markdown("<br>", unsafe_allow_html=True)
    secao_titulo("Apuração mensal", "📅", "Do mês mais recente para o mais antigo.")
    df = _tabela_mensal(meses)
    moeda = {c: _COLS_MOEDA for c in df.columns
             if c not in ("Mês", "Isenção", "Situação")}
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=moeda)

    secao_titulo("Resumo para a declaração anual", "📑")
    da = _tabela_anual(res["anos"])
    st.dataframe(da, hide_index=True, use_container_width=True,
                 column_config={c: _COLS_MOEDA for c in da.columns
                                if c not in ("Ano", "Meses incompletos")})

    with st.expander("Como o cálculo é feito e o que ele não cobre"):
        st.markdown(f"""
**Regras** (Lei 11.033/2004 e IN RFB 1.585/2015):
- Operações comuns em ações, units, ETF e BDR: **15%** sobre o ganho líquido do mês.
- Day trade: **20%**. FII, comum ou day trade: **20%**.
- Ganho em **ações** é isento no mês em que o total vendido de ações fica em até R$ 20 mil.
  ETF, BDR, FII, day trade e direito de subscrição não têm isenção. No mês isento, as ações se compensam entre si
  primeiro, e só o prejuízo líquido passa adiante.
- O prejuízo só compensa dentro da mesma cesta (comum, day trade, FII), sem prazo.
- Direito de subscrição recebido da empresa (GMAT1, HGLG12) tem custo zero: a venda é
  ganho integral, sem isenção.
- DARF código **{CODIGO_DARF}**, até o último dia útil do mês seguinte. Abaixo de R$ 10 o
  valor acumula para o próximo DARF.
- Custo: preço médio ponderado, com a corretagem da compra no custo e a da venda descontada
  da receita. Desdobro, grupamento, bonificação e subscrição da Movimentação entram pelas
  mesmas regras do preço médio da carteira.

**Limitações:**
- O **IRRF é estimado** (0,005% da venda, 1% do ganho em day trade): o extrato da B3 não traz
  as notas de corretagem. Confira com o informe da corretora.
- O fisco só reconhece day trade na **mesma corretora**. O extrato consolida as corretoras,
  então compra numa e venda noutra no mesmo dia aparece aqui como day trade.
- O app não lê DARFs pagos: "em aberto" quer dizer "calculado e ainda não vencido".
- Aluguel de ações, opções, termo, futuros e ativos no exterior ficam de fora.
- Incorporação e transferência de outra corretora continuam sem custo conhecido.
""")
    alertas = [a for a in res["alertas"] if a.get("type") != "fora_do_escopo"]
    if alertas:
        with st.expander(f"{len(alertas)} evento(s) da Movimentação não aplicados"):
            for a in alertas[:30]:
                st.caption(f"{a.get('ticker')}: {a.get('detail')}")
