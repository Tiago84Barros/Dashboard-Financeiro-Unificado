"""
views/ir_renda_variavel.py
Aba "Imposto de Renda" de Investimentos. O ano-calendário escolhido no topo
recorta a tela inteira: os números do ano, a declaração ficha a ficha, com o
nome das linhas e os códigos do programa IRPF (exercício 2026), e a apuração
mês a mês daquele ano. A opção "Visão geral" junta todos os anos: a situação
do DARF hoje, a apuração inteira e o resumo por ano.

O cálculo mora em ``core/ir_renda_variavel.py`` (apuração) e em
``core/irpf_declaracao.py`` (layout das fichas); aqui só se carrega, formata
e explica. A tela precisa dizer com a mesma ênfase o que sabe e o que não
sabe: mês com venda sem custo de aquisição não tem imposto conhecido.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from core.ir_renda_variavel import CESTAS, CODIGO_DARF, ROTULO_CESTA, apurar
from core.irpf_declaracao import (
    DIGITAR,
    DIGITAR_EM_JANEIRO,
    DIGITAR_FII,
    LINHAS_FII,
    LINHAS_RV,
    LINHAS_RV_CONSOLIDACAO,
    _fmt_qtd,
    anos_disponiveis,
    montar,
)
from core.user_context import user_cache_data
from core.utils import fmt_moeda
from design.componentes import card_metrica, secao_titulo
from design.lacunas import aviso_lacuna, detalhe_tecnico, falha_de_acao

_MESES = ["", "jan", "fev", "mar", "abr", "mai", "jun",
          "jul", "ago", "set", "out", "nov", "dez"]
_DIGITA = "✍️ "
_VISAO_GERAL = "geral"


@user_cache_data(ttl=900, show_spinner=False)
def _dados() -> dict | None:
    """Insumos da declaração + a apuração com a foto de 31/12 de cada ano."""
    from core.config import settings
    from core.database import get_engine
    from core.irpf_declaracao import carregar_insumos

    engine = get_engine()
    owner = getattr(settings, "OWNER_USER_ID", None)
    if engine is None or not owner:
        return None
    ins = carregar_insumos(engine, owner)
    datas = ([str(t["transaction_date"]) for t in ins["transacoes"]]
             + [str(p["data"]) for p in ins["proventos"] + ins["posicoes"]])
    hoje = date.today()
    primeiro = int(min(datas)[:4]) if datas else hoje.year
    cortes = tuple(f"{a:04d}-12-31" for a in range(primeiro - 1, hoje.year + 1))
    ins["apuracao"] = apurar(ins["transacoes"], ins["eventos"], cortes=cortes)
    return ins


def _rotulo_mes(mes: str) -> str:
    return f"{_MESES[int(mes[5:7])]}/{mes[:4]}"


def _rotulo_ano(ano) -> str:
    if ano == _VISAO_GERAL:
        return "Visão geral · todos os anos"
    return f"{ano} · declaração {ano + 1}"


def _do_ano(meses: list[dict], ano: int) -> list[dict]:
    return [m for m in meses if m["mes"].startswith(f"{ano:04d}")]


def _f(v: Decimal) -> float:
    return float(v or 0)


def _n(v) -> str:
    """Valor como o programa mostra e aceita colar: 1.234,56. ``None`` é o
    que o app não sabe: "—", nunca 0,00."""
    if v is None:
        return "—"
    return f"{_f(v):_.2f}".replace(".", ",").replace("_", ".")


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


# ─────────────────────────────────────────────────────────────────────────────
# Fichas da declaração
# ─────────────────────────────────────────────────────────────────────────────

def _rotulo_linha(chave: str, rotulo: str, digitar: set[str]) -> str:
    if chave in digitar:
        return _DIGITA + rotulo
    if chave in DIGITAR_EM_JANEIRO:
        return f"{rotulo} ({_DIGITA.strip()} só em JAN)"
    return rotulo


def _celula(m: dict, chave: str, v) -> str:
    if chave == "aliquota":
        return f"{_f(v) * 100:.0f}%"
    if chave == "imposto_pago" and v is None:
        # O app não sabe o que foi recolhido; mostra o DARF que o mês gera
        return f"confirmar: DARF {_n(m['darf_calculado'])}" if m["darf_calculado"] else "—"
    return _n(v)


def _grade(meses: list[dict], linhas: tuple, valores, digitar: set[str]) -> pd.DataFrame:
    """Linhas da ficha x 12 meses, como o programa as lista."""
    dados = {}
    for chave, rotulo in linhas:
        dados[_rotulo_linha(chave, rotulo, digitar)] = [
            _celula(m, chave, valores(m)[chave]) for m in meses]
    df = pd.DataFrame(dados, index=[m["rotulo"] for m in meses]).T
    df.index.name = "Linha da ficha"
    return df.reset_index()


def ficha_rv_tabelas(rv: list[dict]) -> dict[str, pd.DataFrame]:
    return {
        "comum": _grade(rv, LINHAS_RV, lambda m: m["comum"], DIGITAR),
        "day_trade": _grade(rv, LINHAS_RV, lambda m: m["day_trade"], DIGITAR),
        "consolidacao": _grade(rv, LINHAS_RV_CONSOLIDACAO, lambda m: m["consolidacao"], DIGITAR),
    }


def _mostrar_grade(df: pd.DataFrame) -> None:
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Linha da ficha": st.column_config.TextColumn(width="large")})


def _ha_valor(meses: list[dict], valores) -> bool:
    return any(v is not None and v != 0
               for m in meses for k, v in valores(m).items() if k != "aliquota")


def _codigo(item: dict) -> str:
    return (item["codigo"] or "?") + ("*" if item.get("conferir") else "")


def _fontes_pagadoras(item: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        "Beneficiário": "Titular",
        "CNPJ da fonte pagadora": x["cnpj"] or "— (consta no informe)",
        "Nome da fonte pagadora": x["nome"] or "",
        "Ativos": ", ".join(x["tickers"]),
        "Valor": _n(x["valor"]),
    } for x in item["linhas"]])


def _bloco_rendimento(item: dict, vazio: str) -> None:
    st.markdown(f"**{_codigo(item)} — {item['descricao']}** · total **R\\$ {_n(item['total'])}**")
    if item["linhas"]:
        st.dataframe(_fontes_pagadoras(item), hide_index=True, width="stretch")
    else:
        st.caption(vazio)


def _observacao_bem(b: dict) -> str:
    obs = []
    if b["sem_custo"]:
        obs.append("custo incompleto: venda de cota anterior ao extrato")
    elif b["situacao_atual"] is None or b["situacao_anterior"] is None:
        obs.append("custo não informado pela corretora: copie do informe")
    elif b["situacao_atual"] == 0:
        obs.append("vendido no ano: situação final zerada")
    if b["codigo"] is None:
        obs.append("código do fundo pelo informe do administrador")
    if b.get("amortizacao"):
        obs.append(f"amortização de R$ {_n(b['amortizacao'])} recebida: confira o custo")
    return "; ".join(obs)


def tabela_bens(bens: list[dict], ano: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "Grupo": b["grupo"],
        "Código": _codigo(b),
        "Discriminação": b["discriminacao"],
        "CNPJ": b["cnpj"] or "—",
        "Código de negociação": b["codigo_negociacao"] or "",
        f"Situação em 31/12/{ano - 1}": _n(b["situacao_anterior"]),
        f"Situação em 31/12/{ano}": _n(b["situacao_atual"]),
        "Observação": _observacao_bem(b),
    } for b in bens])


def _conferencias(d: dict) -> list[str]:
    """O que precisa de olho humano antes de transmitir, em frases."""
    tipos = {b["ticker"]: b["tipo"] for b in d["bens"]}

    def un(t: str) -> str:
        # Ação, unit e BDR em "ações"; fundo em "cotas"
        return "ações" if tipos.get(t, "acao") in ("acao", "bdr", "direito") else "cotas"

    out = []
    for p in d["proventos_a_classificar"]["linhas"]:
        out.append(
            f"**{', '.join(p['tickers'])}** — R\\$ {_n(p['valor'])} lançados no extrato da B3 como "
            "*Rendimento*. O informe de rendimentos da empresa diz se é dividendo "
            "(Isentos 09) ou JCP (Exclusiva 10); por isso não entrou em nenhuma ficha.")
    for c in d["custodia_maior"]:
        if c["troca_de_codigo"]:
            novo, antigo = c["ticker"], c["troca_de_codigo"]
            out.append(
                f"**{antigo} → {novo}**: a corretora tem {_fmt_qtd(c['custodia'])} {un(novo)} de "
                f"{novo} em 31/12; a apuração tem {_fmt_qtd(c['apurado'])} de {novo} e as "
                f"outras {_fmt_qtd(c['custodia'] - c['apurado'])} ainda como {antigo}. Troca "
                "de código sem o evento na Movimentação: declare tudo num item só, com o "
                "código novo e a soma dos dois custos.")
        else:
            out.append(
                f"**{c['ticker']}**: a corretora tem {_fmt_qtd(c['custodia'])} "
                f"{un(c['ticker'])} em 31/12 e a apuração, {_fmt_qtd(c['apurado'])}. A diferença "
                "foi comprada antes do extrato da B3 e está sem custo em Bens e Direitos.")
    for c in d["custodia_menor"]:
        out.append(
            f"**{c['ticker']}**: a apuração tem {_fmt_qtd(c['apurado'])} {un(c['ticker'])} em "
            "31/12 e a posição da corretora não mostra nenhuma. Confira se houve venda ou "
            "transferência fora do extrato antes de declarar o saldo.")
    for b in d["bens"]:
        em_carteira = b["situacao_atual"] is None or b["situacao_atual"] > 0 or b["qtd_atual"] > 0
        if not b["cnpj"] and em_carteira:
            out.append(f"**{b['ticker']}**: sem CNPJ "
                       + ("no cadastro da CVM" if b["negociado_em_bolsa"]
                          else "na posição da corretora")
                       + "; copie do informe de rendimentos.")
        if b["situacao_atual"] is None or b["situacao_anterior"] is None:
            out.append(f"**{b['ticker']}**: a posição da corretora não traz o valor investido; "
                       "o custo de aquisição está no informe de rendimentos.")
        if b["codigo"] is None:
            out.append(f"**{b['ticker']}**: fundo sem tipo no nome. O código do grupo 07 "
                       "depende do tipo e da tributação; está no informe do administrador.")
        if b.get("amortizacao"):
            fonte = ("a apuração não a desconta" if b["negociado_em_bolsa"]
                     else "a corretora pode ou não já tê-la descontado")
            out.append(f"**{b['ticker']}**: R\\$ {_n(b['amortizacao'])} de amortização recebida "
                       f"até 31/12/{d['ano']}. Amortização devolve capital e reduz o custo, e "
                       f"{fonte}: confira a situação com o informe do administrador.")
    darfs = [(m, f) for f, meses in (("Renda Variável", d["renda_variavel"]),
                                     ("FII", d["fii"]))
             for m in meses if m["darf_calculado"]]
    if darfs:
        out.append("DARF calculado em "
                   + ", ".join(f"{m['rotulo']} ({f}, R\\$ {_n(m['darf_calculado'])})"
                               for m, f in darfs)
                   + ". O app não sabe se foi pago: em *Imposto pago* digite o valor de cada "
                   "DARF efetivamente recolhido, pelo comprovante.")
    if d["pago_retido"]["valor"] or any(
            m["consolidacao"]["irrf_lei_mes"] or m["consolidacao"]["irrf_dt_mes"]
            for m in d["renda_variavel"]) or any(m["linhas"]["retido_mes"] for m in d["fii"]):
        out.append("IR fonte **estimado** (0,005% das vendas, 1% do ganho no day-trade), na "
                   "ficha mensal e em Imposto Pago/Retido. O retido de verdade está nas notas "
                   "de corretagem e no informe da corretora: digite o deles.")
    if d["meses_incompletos"]:
        out.append("Meses com venda sem custo de aquisição: "
                   + ", ".join(_rotulo_mes(m) for m in d["meses_incompletos"])
                   + ". O resultado desses meses está subestimado. Informe a posição que "
                   "você tinha antes do extrato em Evolução Patrimonial › **Posição "
                   "anterior ao extrato da B3**.")
    return out


def _render_declaracao(ins: dict, ano: int) -> None:
    secao_titulo(
        f"Declaração IRPF {ano + 1}", "📑",
        f"Ano-calendário {ano}. As fichas do programa da Receita, na ordem e com o nome de "
        f"cada linha. {_DIGITA.strip()} marca o que você digita; o resto o programa calcula "
        "sozinho.",
    )
    d = montar(ins["apuracao"], ano, ins["proventos"], ins["posicoes"], ins["emissores"],
               ins["mercado"], ins["etfs"], ins["nomes_fii"])
    if ins["falhas"]:
        aviso_lacuna("IRPF: fonte não carregou: " + ", ".join(ins["falhas"]),
                     codigo="tela.ir.fonte_da_declaracao")
        st.warning("Parte dos dados não carregou (" + ", ".join(ins["falhas"])
                   + "); as fichas que dependem dela estão incompletas.", icon="⚠️")

    conferir = _conferencias(d)
    abas = st.tabs([
        "Renda Variável", "FII / Fiagro", "Rendimentos Isentos",
        "Tributação Exclusiva", "Imposto Pago/Retido", "Bens e Direitos",
        f"Conferir antes de enviar ({len(conferir)})",
    ])
    rv, fii = d["renda_variavel"], d["fii"]

    with abas[0]:
        st.caption("Ficha **Renda Variável › Operações Comuns / Day-Trade**, mês a mês. "
                   "No programa, abra cada mês e copie a coluna dele.")
        tabelas = ficha_rv_tabelas(rv)
        st.markdown("**Operações comuns**")
        _mostrar_grade(tabelas["comum"])
        st.markdown("**Day-trade**")
        if _ha_valor(rv, lambda m: m["day_trade"]):
            _mostrar_grade(tabelas["day_trade"])
        else:
            st.caption(f"Nenhuma operação de day-trade em {ano}: os campos ficam zerados.")
        st.markdown("**Consolidação do mês**")
        _mostrar_grade(tabelas["consolidacao"])
        st.caption("IR fonte estimado pelas vendas: confira nas notas de corretagem. "
                   "*Imposto pago* é o que foi recolhido; o app só sabe o DARF que o mês gera.")
        isento = d["isentos"]["acoes_ate_20mil"]["valor"]
        if isento:
            st.caption(f"Ganho isento das vendas de ações até R\\$ 20 mil no mês: R\\$ {_n(isento)}. "
                       "Não entra nesta ficha: vai em Rendimentos Isentos, código 20.")
        oi = d["outras_informacoes"]
        st.markdown("**Outras informações** (o programa soma sozinho)")
        st.dataframe(pd.DataFrame([
            {"Linha": "Total do imposto devido", "Valor": _n(oi["imposto_devido"])},
            {"Linha": "Total do imposto pago",
             "Valor": (f"confirmar: DARFs de {_n(oi['darf_calculado'])}"
                       if oi["darf_calculado"] else "—")},
            {"Linha": "Imposto retido na fonte (Lei nº 11.033/2004)",
             "Valor": _n(oi["irrf_lei_11033"])},
        ]), hide_index=True, width="stretch")

    with abas[1]:
        st.caption("Ficha **Renda Variável › Fundos de Investimento Imobiliário ou nas Cadeias "
                   "Produtivas Agroindustriais**, mês a mês.")
        if _ha_valor(fii, lambda m: m["linhas"]):
            _mostrar_grade(_grade(fii, LINHAS_FII, lambda m: m["linhas"], DIGITAR_FII))
        else:
            st.caption(f"Nenhuma venda de FII ou Fiagro em {ano} e nenhum prejuízo a carregar.")

    with abas[2]:
        st.caption("Ficha **Rendimentos Isentos e Não Tributáveis**. Uma linha por fonte "
                   "pagadora.")
        ac = d["isentos"]["acoes_ate_20mil"]
        st.markdown(f"**{_codigo(ac)} — {ac['descricao']}** · valor **R\\$ {_n(ac['valor'])}**")
        _bloco_rendimento(d["isentos"]["dividendos"], f"Nenhum dividendo recebido em {ano}.")
        _bloco_rendimento(d["isentos"]["fii"], f"Nenhum rendimento de FII ou Fiagro em {ano}.")
        if d["isentos"]["fip_ie"]["linhas"]:
            _bloco_rendimento(d["isentos"]["fip_ie"], "")

    with abas[3]:
        st.caption("Ficha **Rendimentos Sujeitos à Tributação Exclusiva/Definitiva**.")
        _bloco_rendimento(d["exclusiva"]["jcp"],
                          f"Nenhum juro sobre capital próprio recebido em {ano}.")
        st.caption("JCP vai pelo valor líquido (já descontados os 15% retidos). Os rendimentos "
                   "de Tesouro Direto e CDB resgatados (código 06) vêm do informe de "
                   "rendimentos da corretora.")

    with abas[4]:
        pr = d["pago_retido"]
        st.caption("Ficha **Imposto Pago/Retido**.")
        st.markdown(f"**{_codigo(pr)} — {pr['descricao']}** · valor **estimado** "
                    f"**R\\$ {_n(pr['valor'])}**")
        st.caption("É o IR fonte que sobrou em dezembro sem abater imposto de renda variável "
                   "nem de FII, estimado em 0,005% das vendas. O retido de verdade está nas "
                   "notas de corretagem e no informe da corretora: digite o deles.")
        if d["irrf_day_trade_restituir"]:
            st.markdown(f"IR fonte de day-trade que sobrou no ano: "
                        f"**R\\$ {_n(d['irrf_day_trade_restituir'])}**. Não vai nesta ficha: "
                        "recupera-se por pedido de restituição (PER/DCOMP).")

    with abas[5]:
        st.caption("Ficha **Bens e Direitos**, a custo de aquisição (nunca a valor de mercado). "
                   "Em ações e fundos marque *negociados em bolsa* e informe o código de "
                   "negociação.")
        if d["bens"]:
            st.dataframe(tabela_bens(d["bens"], ano), hide_index=True, width="stretch",
                         column_config={"Discriminação": st.column_config.TextColumn(
                             width="large")})
        else:
            st.caption(f"Nenhum bem em 31/12/{ano - 1} nem em 31/12/{ano}.")

    with abas[6]:
        if conferir:
            for frase in conferir:
                st.markdown(f"- {frase}")
        else:
            st.caption("Nada a conferir: custódia, proventos, CNPJs, custos, DARFs e IR fonte "
                       "fecham com a apuração.")

    if any(i.get("conferir") for i in [*d["isentos"].values(), d["pago_retido"], *d["bens"]]):
        st.caption("\\* Código que as fontes oficiais não confirmam por inteiro: confira se a "
                   "descrição bate com a da lista do programa.")
        detalhe_tecnico(
            "Códigos marcados conferir=True no IRPF: Isentos 20 e 99 (FII, FIP-IE), "
            "Bens 04-04 (BDR/direito), 07-05 (ETF/FIP) e 07-06 (FIP-IE).",
            codigo="ir.codigos_a_conferir")
    for lim in (
        "Bonificação em ações (Isentos 18) não é calculada.",
        "FIP e FIP-IE saem sem CNPJ: a posição consolidada não traz o do fundo.",
        "Tributação Exclusiva 06 (Tesouro/CDB resgatados) não é calculada: vem do informe.",
    ):
        detalhe_tecnico(lim, codigo="ir.limitacoes_declaracao")


def render() -> None:
    secao_titulo(
        "Imposto de Renda — renda variável", "🧾",
        "As fichas da declaração do ano-calendário escolhido, prontas para digitar, e a "
        "apuração mês a mês daquele ano (DARF código 6015), a partir dos extratos da B3 e "
        "da posição da corretora.",
    )
    try:
        ins = _dados()
    except Exception as exc:  # noqa: BLE001
        falha_de_acao("Não foi possível carregar as operações.", exc)
        return
    if ins is None:
        aviso_lacuna("A apuração precisa do banco com as operações importadas.",
                     codigo="tela.ir.sem_banco_ou_operacoes")
        st.info("Esta análise não está disponível no momento.", icon="ℹ️")
        return
    res = ins["apuracao"]
    meses = res["meses"]
    if not meses and not ins["proventos"] and not ins["posicoes"]:
        st.info("Nenhuma venda, provento ou posição no extrato importado.", icon="ℹ️")
        return

    _registrar_lacunas(res)
    hoje = date.today()
    anos = anos_disponiveis(res, ins["proventos"], ins["posicoes"])
    padrao = anos.index(hoje.year - 1) if hoje.year - 1 in anos else 0
    ano = st.selectbox("Ano-calendário", [*anos, _VISAO_GERAL], index=padrao,
                       key="irpf_ano", format_func=_rotulo_ano)

    if ano == _VISAO_GERAL:
        if not meses:
            st.caption("Nenhuma venda no extrato importado: não há imposto a apurar.")
            return
        _render_darf(res)
        _render_apuracao(res)
        return

    if ano == hoje.year:
        st.info(f"{ano} ainda não terminou: a tela mostra o ano até hoje.", icon="ℹ️")
    if meses:
        _render_darf(res, ano)
    _render_declaracao(ins, ano)
    _render_apuracao(res, ano)


def _registrar_lacunas(res: dict) -> None:
    """O que a apuração não sabe vai para o administrador, em qualquer ano
    escolhido; nada daqui aparece na tela de uso."""
    meses = res["meses"]
    if meses:
        n_incompletos = sum(1 for m in meses if m["incompleto"])
        prej_incerto = any(meses[-1]["cestas"][c]["prejuizo_incerto"] for c in CESTAS)
        if n_incompletos:
            sem = sorted({t for m in meses for c in CESTAS for t in m["cestas"][c]["sem_custo"]})
            detalhe_tecnico(
                "Ativos comprados antes do início do extrato da B3 (nov/2019): o ganho "
                "dessas vendas não foi tratado nem como lucro nem como prejuízo.",
                codigo="ir.venda_sem_custo_origem",
            )
            for t in sem:
                aviso_lacuna(f"Venda de {t} sem custo de aquisição conhecido.",
                             codigo="tela.ir.venda_sem_custo", entidade=t)
        elif prej_incerto:
            aviso_lacuna("O prejuízo acumulado herda uma venda sem custo de meses anteriores.",
                         codigo="tela.ir.prejuizo_incerto")
    alertas = [a for a in res["alertas"] if a.get("type") != "fora_do_escopo"]
    for a in alertas[:30]:
        aviso_lacuna(f"Evento da Movimentação não aplicado: {a.get('detail')}",
                     codigo="tela.ir.evento_nao_aplicado",
                     entidade=str(a.get("ticker") or "") or None)


def _render_darf(res: dict, ano: int | None = None) -> None:
    """Os quatro números do topo. Sem ``ano`` (visão geral), a situação de
    hoje; com ``ano``, a daquele ano-calendário: até hoje no ano em curso, em
    31/12 nos anteriores."""
    hoje = date.today()
    ref = hoje.year if ano is None else ano
    corrente = ref == hoje.year
    meses = [m for m in res["meses"] if m["mes"] <= f"{ref:04d}-12"]
    pend = res["pendente"]
    if ano is not None and pend and not pend["mes"].startswith(f"{ano:04d}"):
        pend = None
    resumo = next((a for a in res["anos"] if a["ano"] == ref), None)
    ultimo = meses[-1] if meses else None
    prej = {c: ultimo["cestas"][c]["prejuizo_a_compensar"] if ultimo else Decimal(0)
            for c in CESTAS}
    prej_incerto = bool(ultimo) and any(ultimo["cestas"][c]["prejuizo_incerto"] for c in CESTAS)

    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        vence = (f"{_rotulo_mes(pend['mes'])} · vence {pend['vencimento'].strftime('%d/%m/%Y')}"
                 if pend else "")
        if corrente and pend:
            card_metrica(
                "DARF em aberto", fmt_moeda(_f(pend["darf"])), vence,
                positivo=False,
                ajuda="Último mês fechado com imposto cujo vencimento ainda não passou. "
                      "Confira se já foi pago: o app não lê pagamentos.",
            )
        elif corrente:
            card_metrica("DARF em aberto", "Nenhum", "sem mês fechado com imposto a vencer",
                         positivo=True)
        else:
            # Ano fechado fica no total do ano; o DARF de dezembro que ainda não
            # venceu (em janeiro) vai no subtítulo, sem trocar o card
            n_darf = sum(1 for m in _do_ano(meses, ref) if m["darf"] > 0)
            card_metrica(
                f"DARFs de {ref}", fmt_moeda(_f(resumo["darf"]) if resumo else 0),
                f"em aberto: {vence}" if pend
                else f"{n_darf} mês(es) com imposto a recolher" if n_darf
                else "nenhum mês com imposto a recolher",
                positivo=False if pend else None,
                ajuda="Soma dos DARFs que a apuração calcula para o ano. O app não lê "
                      "pagamentos: confira com os comprovantes.",
            )
    with c2:
        if corrente:
            em_curso = ultimo if ultimo and ultimo["em_curso"] else None
            card_metrica(
                "Mês em curso", fmt_moeda(_f(em_curso["imposto"])) if em_curso else "Sem vendas",
                (f"vendas de ações {fmt_moeda(_f(em_curso['vendas_acoes']))} de "
                 f"{fmt_moeda(20000)} isentos") if em_curso else "nenhuma venda neste mês",
                positivo=None,
                ajuda="Imposto parcial do mês corrente, antes da compensação do IRRF.",
            )
        else:
            card_metrica(
                "IR fonte a declarar",
                fmt_moeda(_f(resumo["irrf_a_declarar"]) if resumo else 0),
                "ficha Imposto Pago/Retido · estimado",
                positivo=None,
                ajuda="IR fonte que sobrou em dezembro sem abater imposto, estimado em "
                      "0,005% das vendas. O retido de verdade está no informe da corretora.",
            )
    with c3:
        total_prej = sum(prej.values(), Decimal(0))
        card_metrica(
            "Prejuízo a compensar" if corrente else f"Prejuízo em 31/12/{ref}",
            fmt_moeda(_f(total_prej)),
            " · ".join(f"{ROTULO_CESTA[c]} {fmt_moeda(_f(v))}" for c, v in prej.items() if v)
            or "nenhum",
            positivo=None,
            ajuda="Só compensa dentro da mesma cesta. "
                  + ("Valor incerto: há venda sem custo conhecido no histórico." if prej_incerto
                     else ""),
        )
    with c4:
        card_metrica(
            f"Ganho isento em {ref}",
            fmt_moeda(_f(resumo["ganho_isento"])) if resumo else fmt_moeda(0),
            "vai na ficha de rendimentos isentos da declaração",
            positivo=True,
        )
    st.markdown("<br>", unsafe_allow_html=True)


def _render_apuracao(res: dict, ano: int | None = None) -> None:
    """Sem ``ano``, a apuração inteira e o resumo por ano; com ``ano``, só os
    meses dele."""
    meses = res["meses"] if ano is None else _do_ano(res["meses"], ano)
    st.markdown("<br>", unsafe_allow_html=True)
    secao_titulo("Apuração mensal" if ano is None else f"Apuração mensal de {ano}", "📅",
                 "Do mês mais recente para o mais antigo.")
    if meses:
        df = _tabela_mensal(meses)
        moeda = {c: _COLS_MOEDA for c in df.columns
                 if c not in ("Mês", "Isenção", "Situação")}
        st.dataframe(df, hide_index=True, width="stretch", column_config=moeda)
    else:
        st.caption(f"Nenhuma venda em {ano}: não há imposto de renda variável a apurar.")

    if ano is None:
        secao_titulo("Resumo por ano", "🗂️")
        da = _tabela_anual(res["anos"])
        st.dataframe(da, hide_index=True, width="stretch",
                     column_config={c: _COLS_MOEDA for c in da.columns
                                    if c not in ("Ano", "Meses incompletos")})

    with st.expander("Como o cálculo é feito"):
        st.markdown(f"""
**Regras** (Lei 11.033/2004 e IN RFB 1.585/2015):
- Operações comuns em ações, units, ETF e BDR: **15%** sobre o ganho líquido do mês.
- Day trade: **20%**. FII, comum ou day trade: **20%**.
- Ganho em **ações** é isento no mês em que o total vendido de ações fica em até R\\$ 20 mil.
  ETF, BDR, FII, day trade e direito de subscrição não têm isenção. No mês isento, as ações se compensam entre si
  primeiro, e só o prejuízo líquido passa adiante.
- O prejuízo só compensa dentro da mesma cesta (comum, day trade, FII), sem prazo.
- Direito de subscrição recebido da empresa (GMAT1, HGLG12) tem custo zero: a venda é
  ganho integral, sem isenção.
- DARF código **{CODIGO_DARF}**, até o último dia útil do mês seguinte. Abaixo de R\\$ 10 o
  valor acumula para o próximo DARF.
- Custo: preço médio ponderado, com a corretagem da compra no custo e a da venda descontada
  da receita. Desdobro, grupamento, bonificação e subscrição da Movimentação entram pelas
  mesmas regras do preço médio da carteira.
- IR fonte: cada ficha tem o seu saldo, zerado em janeiro. O de day-trade abate primeiro,
  depois o da Lei 11.033; o de FII só abate imposto de FII. A sobra da Lei 11.033 em
  dezembro vai para Imposto Pago/Retido (03).
- Bens e Direitos: custo médio em 31/12; ativo vendido no ano continua com a situação final
  zerada. Tesouro, CDB e FIP saem do valor investido da posição da corretora em dezembro.
""")
    for lim in (
        "O IRRF é estimado (0,005% da venda, 1% do ganho em day trade): o extrato da B3 "
        "não traz as notas de corretagem.",
        "O fisco só reconhece day trade na mesma corretora; o extrato consolida as "
        "corretoras, então compra numa e venda noutra no mesmo dia aparece como day trade.",
        "O app não lê DARFs pagos: 'em aberto' quer dizer 'calculado e ainda não vencido'.",
        "Aluguel de ações, opções, termo, futuros e ativos no exterior ficam de fora.",
        "Incorporação e transferência de outra corretora continuam sem custo conhecido.",
    ):
        detalhe_tecnico(lim, codigo="ir.limitacoes_calculo")
