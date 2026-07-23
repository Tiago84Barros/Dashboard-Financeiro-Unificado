"""
core/llm_context_financeiro.py
Montagem do CONTEXTO FINANCEIRO auditável para o chat de Controle Financeiro.

Este módulo NÃO chama a LLM. Ele apenas transforma os dados REAIS já carregados
pela view (receitas, despesas, categorias, fluxo mensal, histórico anual,
evolução patrimonial e cartão) em:

  1. Um texto estruturado (o CONTEXTO) que a LLM lê como fatos.
  2. Um dicionário `chart_meta` com as SÉRIES NUMÉRICAS reais, usadas depois
     por core.financeiro_chat_charts para desenhar gráficos sem que a LLM
     precise gerar código.

Isolamento de usuário: todas as funções de dados de origem (core.controle,
core.investimentos) já filtram por settings.OWNER_USER_ID. Este módulo consome
apenas o que a view passou — nunca acessa o banco diretamente e nunca mistura
dados de outro usuário.
"""
from __future__ import annotations

import unicodedata
from datetime import date as _date

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

# ── Classificação de essencialidade ───────────────────────────────────────────
# Heurística por palavra-chave sobre o nome da categoria (sem acento, minúsculo).
# ESSENCIAL: necessidades básicas — moradia, alimentação de casa, contas fixas,
#            saúde, transporte para trabalho/estudo, educação, dívidas.
# NAO_ESSENCIAL: consumo discricionário — lazer, restaurante, compras, assinaturas.
# Categorias não reconhecidas ficam como "nao_classificada" e são reportadas como
# limitação, nunca forçadas a um dos lados.
_ESSENCIAL_TERMOS = (
    "mercado", "alimenta", "supermerc", "moradia", "aluguel", "condominio",
    "luz", "energia", "agua", "gas", "internet", "telefone", "transporte",
    "combustivel", "onibus", "metro", "saude", "plano de saude", "farmacia",
    "educacao", "escola", "faculdade", "financiamento", "emprestimo",
    "despesas domesticas", "domestica", "iptu", "seguro",
)
_NAO_ESSENCIAL_TERMOS = (
    "compras", "lazer", "restaurante", "bar", "assinatura", "streaming",
    "viagem", "presente", "hobby", "jogo", "delivery", "ifood", "shopping",
    "roupa", "beleza", "estetica",
)


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return text.encode("ascii", "ignore").decode("ascii").casefold().strip()


def classificar_essencialidade(nome: object) -> str:
    """Retorna 'essencial' | 'nao_essencial' | 'nao_classificada'."""
    n = _norm(nome)
    if not n:
        return "nao_classificada"
    if any(t in n for t in _ESSENCIAL_TERMOS):
        return "essencial"
    if any(t in n for t in _NAO_ESSENCIAL_TERMOS):
        return "nao_essencial"
    return "nao_classificada"


def _brl(v: float) -> str:
    return ("R$ " + f"{float(v or 0):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: float) -> str:
    return f"{float(v or 0):.1f}%".replace(".", ",")


# ── Montagem do contexto ──────────────────────────────────────────────────────

def build_financas_chat_context(
    *,
    user_question: str,
    dados_mes: dict,
    historico: list,
    hist_anual: dict,
    gastos_categoria_anual: list,
    gastos_cartao: dict,
    evolucao: dict,
    investido_mes: float,
    ano_ref: int,
    mes_ref: int,
) -> tuple[str, dict]:
    """
    Constrói (contexto_texto, chart_meta) a partir dos dados REAIS já carregados
    pela view de Controle Financeiro.

    Parâmetros (todos filtrados por OWNER_USER_ID na origem):
      dados_mes              : retorno de get_controle(ano, mes)
      historico              : get_cashflow_mensal() — últimos 12 meses
      hist_anual             : get_historico_anual()
      gastos_categoria_anual : get_gastos_categoria_anual(ano_ref)
      gastos_cartao          : {ano_str: [{mes,label,total}], "todos": [...]}
      evolucao               : get_evolucao_patrimonial()
      investido_mes          : investido no mês selecionado
      ano_ref, mes_ref       : mês selecionado no header
    """
    receitas = float(dados_mes.get("receitas", 0) or 0)
    despesas = float(dados_mes.get("despesas", 0) or 0)
    categorias = dados_mes.get("categorias", []) or []
    fonte = dados_mes.get("data_source", "mock")

    saldo = round(receitas - despesas - float(investido_mes or 0), 2)
    taxa_poupanca = round(saldo / receitas * 100, 1) if receitas > 0 else 0.0

    # ── Essencial vs não essencial (mês selecionado) ─────────────────────────
    ess = {"essencial": 0.0, "nao_essencial": 0.0, "nao_classificada": 0.0}
    for c in categorias:
        ess[classificar_essencialidade(c.get("nome"))] += float(c.get("gasto", 0) or 0)
    ess = {k: round(v, 2) for k, v in ess.items()}

    # ── chart_meta: séries numéricas reais para os gráficos ──────────────────
    fluxo_mensal = [
        {
            "label": h.get("label", ""),
            "ano": h.get("ano"),
            "mes": h.get("mes"),
            "receitas": round(float(h.get("receitas", 0) or 0), 2),
            "despesas": round(float(h.get("despesas", 0) or 0), 2),
            "saldo": round(float(h.get("saldo", 0) or 0), 2),
            "investimentos": round(float(h.get("investimentos", 0) or 0), 2),
        }
        for h in (historico or [])
    ]

    por_ano = hist_anual.get("por_ano", {}) or {}
    anos = sorted(hist_anual.get("anos", []) or [])

    cats_mes = [
        {"nome": c.get("nome", "—"), "gasto": round(float(c.get("gasto", 0) or 0), 2),
         "orcamento": round(float(c.get("orcamento", 0) or 0), 2),
         "pct_usado": float(c.get("pct_usado", 0) or 0),
         "essencialidade": classificar_essencialidade(c.get("nome"))}
        for c in categorias
    ]
    cats_anual = [
        {"nome": c.get("nome", "—"), "gasto": round(float(c.get("gasto", 0) or 0), 2),
         "essencialidade": classificar_essencialidade(c.get("nome"))}
        for c in (gastos_categoria_anual or [])
    ]

    chart_meta = {
        "ano_ref": ano_ref,
        "mes_ref": mes_ref,
        "mes_label": f"{_MESES_PT.get(mes_ref, '')}/{ano_ref}",
        "receitas_mes": round(receitas, 2),
        "despesas_mes": round(despesas, 2),
        "investido_mes": round(float(investido_mes or 0), 2),
        "saldo_mes": saldo,
        "taxa_poupanca_pct": taxa_poupanca,
        "categorias_mes": cats_mes,
        "categorias_anual": cats_anual,
        "fluxo_mensal": fluxo_mensal,
        "anos": anos,
        "por_ano": {str(a): por_ano.get(a, {}) for a in anos},
        "essencialidade_mes": ess,
        "data_source": fonte,
    }

    # ── Texto do contexto ────────────────────────────────────────────────────
    L: list[str] = []
    L.append("METADADOS:")
    L.append(f"  Data de hoje: {_date.today().strftime('%d/%m/%Y')}")
    L.append(f"  Mês selecionado no painel: {_MESES_PT.get(mes_ref, '')}/{ano_ref}")
    fonte_txt = {"real": "banco de dados real do usuário",
                 "mock": "dados de demonstração (MOCK — não são reais)",
                 "mock_fallback": "FALLBACK mock (o banco real falhou — dados NÃO são reais)"}
    L.append(f"  Origem dos dados: {fonte_txt.get(fonte, fonte)}")
    if fonte != "real":
        L.append("  ATENÇÃO: os números abaixo NÃO são reais; avise o usuário e evite conclusões.")

    L.append("")
    L.append(f"MÊS SELECIONADO ({_MESES_PT.get(mes_ref, '')}/{ano_ref}):")
    L.append(f"  Receitas: {_brl(receitas)}")
    L.append(f"  Despesas (exclui compras no cartão de crédito): {_brl(despesas)}")
    L.append(f"  Investimentos/aportes no mês: {_brl(investido_mes)}")
    L.append(f"  Saldo do mês (Receitas − Despesas − Investimentos): {_brl(saldo)}")
    L.append(f"  Taxa de poupança: {_pct(taxa_poupanca)} (meta de referência: 30%)")
    L.append(f"  Nº de lançamentos: {dados_mes.get('num_transacoes', 0)}")

    L.append("")
    L.append("DESPESAS POR CATEGORIA NO MÊS (gasto | orçamento | % usado | essencialidade):")
    if cats_mes:
        for c in cats_mes:
            L.append(f"  {c['nome']}: {_brl(c['gasto'])} | orç. {_brl(c['orcamento'])} | "
                     f"{_pct(c['pct_usado'])} | {c['essencialidade']}")
    else:
        L.append("  Sem despesas categorizadas no mês.")

    L.append("")
    L.append("DISTRIBUIÇÃO ESSENCIAL × NÃO ESSENCIAL NO MÊS:")
    total_desp_cat = sum(v for v in ess.values()) or 1.0
    L.append(f"  Essenciais: {_brl(ess['essencial'])} "
             f"({_pct(ess['essencial'] / total_desp_cat * 100)})")
    L.append(f"  Não essenciais: {_brl(ess['nao_essencial'])} "
             f"({_pct(ess['nao_essencial'] / total_desp_cat * 100)})")
    L.append(f"  Não classificadas: {_brl(ess['nao_classificada'])} "
             f"({_pct(ess['nao_classificada'] / total_desp_cat * 100)})")
    L.append("  (Classificação por heurística de nome; revise categorias ambíguas.)")

    L.append("")
    L.append("FLUXO DE CAIXA — ÚLTIMOS MESES (mês: receitas | despesas | saldo | invest.):")
    if fluxo_mensal:
        for h in fluxo_mensal:
            L.append(f"  {h['label']}: {_brl(h['receitas'])} | {_brl(h['despesas'])} | "
                     f"{_brl(h['saldo'])} | {_brl(h['investimentos'])}")
    else:
        L.append("  Sem histórico mensal disponível.")

    L.append("")
    L.append("COMPARATIVO ANO A ANO (ano: receitas | despesas | investimentos):")
    if anos:
        for a in anos:
            d = por_ano.get(a, {})
            L.append(f"  {a}: {_brl(d.get('receitas', 0))} | {_brl(d.get('despesas', 0))} | "
                     f"{_brl(d.get('investimentos', 0))}")
    else:
        L.append("  Sem histórico anual disponível.")

    L.append("")
    L.append("DESPESAS ANUAIS POR CATEGORIA (ano de referência do painel):")
    if cats_anual:
        tot_an = sum(c["gasto"] for c in cats_anual) or 1.0
        for c in cats_anual[:20]:
            L.append(f"  {c['nome']}: {_brl(c['gasto'])} "
                     f"({_pct(c['gasto'] / tot_an * 100)}) | {c['essencialidade']}")
    else:
        L.append("  Sem despesas anuais por categoria.")

    # ── Cartão de crédito (fluxo do mês, lançamentos manuais) ────────────────
    cartao_todos = (gastos_cartao or {}).get(str(ano_ref)) or []
    if cartao_todos:
        L.append("")
        L.append(f"PAGAMENTOS DE CARTÃO POR MÊS EM {ano_ref} (categoria 'Pagamento de Cartão'):")
        for item in cartao_todos:
            L.append(f"  {item.get('label', '')}: {_brl(item.get('total', 0))}")

    # ── Patrimônio investido ─────────────────────────────────────────────────
    snaps = (evolucao or {}).get("snapshots", []) or []
    if snaps:
        ult = snaps[-1]
        L.append("")
        L.append("PATRIMÔNIO INVESTIDO (evolução):")
        L.append(f"  Total investido (aportes): {_brl(evolucao.get('total_investido', 0))}")
        L.append(f"  Valor de mercado atual: {_brl(evolucao.get('total_mercado', 0))}")
        L.append(f"  Último ponto: {ult.get('mes_str', ult.get('label', ''))} — "
                 f"mercado {_brl(ult.get('valor_mercado', 0))}")

    L.append("")
    L.append("DEFINIÇÕES IMPORTANTES:")
    L.append("  - 'Despesas' do mês EXCLUEM compras no cartão de crédito (elas viram")
    L.append("    fatura futura e vivem em outra aba). Não confunda fluxo do mês com fatura.")
    L.append("  - 'Saldo do mês' já subtrai os investimentos/aportes.")
    L.append("  - Valores em Reais (BRL).")

    return "\n".join(L), chart_meta


# ── Contexto específico do CARTÃO DE CRÉDITO ─────────────────────────────────
# Essencialidade por categoria-alvo do cartão (taxonomia limpa). Heurística
# assumida — a IA deve tratar como premissa, não verdade absoluta.
_CARD_ESSENCIAL = {
    "Alimentação", "Mercado", "Transporte & Combustível",
    "Saúde & Bem-estar", "Educação & Profissional", "Tarifas & Anuidade",
    "Casa & Construção",
}
_CARD_NAO_ESSENCIAL = {
    "Compras / Varejo", "Assinaturas & Serviços digitais",
    "Lazer & Entretenimento", "Cuidados pessoais",
}
# Não são consumo (não entram na análise essencial × não essencial).
_CARD_NAO_CONSUMO = {"Pagamento de Cartão", "Créditos e Estornos"}


def _card_essencialidade(nome: object) -> str:
    n = str(nome or "").strip()
    if n in _CARD_ESSENCIAL:
        return "essencial"
    if n in _CARD_NAO_ESSENCIAL:
        return "nao_essencial"
    if n in _CARD_NAO_CONSUMO:
        return "nao_consumo"
    return "nao_classificada"


def build_cartao_chat_context(
    *,
    user_question: str,
    resumo: dict,
    categorias: list,
    estabelecimentos: list,
    parcelas: list,
    projecao: list,
    nao_consumo: list,
    evolucao_mensal: list,
    recorrentes: list,
    filtro_label: str,
    assinaturas: list | None = None,
) -> tuple[str, dict]:
    """
    Constrói (contexto_texto, chart_meta) para o chat da aba Cartão de Crédito, a
    partir das estruturas REAIS já calculadas pela view (todas derivadas de
    get_transacoes_cartao_credito, filtrado por OWNER_USER_ID).

    Parâmetros são listas de dicts (records) das tabelas do cartão:
      resumo          : _summary_credit_card(df)
      categorias      : _prepare_category_analysis(df) → records
      estabelecimentos: _prepare_merchant_analysis(df) → records
      parcelas        : _prepare_installment_analysis(df) → records
      projecao        : _prepare_future_invoice_projection(df) → records
      nao_consumo     : _prepare_non_consumption(df) → records
      evolucao_mensal : [{label, total}] (compras por mês)
      recorrentes     : _prepare_recurring_analysis(df) → records
      filtro_label    : descrição do filtro ativo (ano/mês/cartão)
    """
    def g(d, *keys, default=0.0):
        for k in keys:
            if k in d:
                return d[k]
        return default

    # ── chart_meta ───────────────────────────────────────────────────────────
    cats_meta = []
    ess = {"essencial": 0.0, "nao_essencial": 0.0, "nao_classificada": 0.0}
    for c in categorias or []:
        nome = str(g(c, "Categoria", default="—"))
        gasto = float(g(c, "Total (R$)", "total") or 0)
        e = _card_essencialidade(nome)
        cats_meta.append({"nome": nome, "gasto": round(gasto, 2), "essencialidade": e})
        if e in ess:
            ess[e] += gasto
    ess = {k: round(v, 2) for k, v in ess.items()}

    estab_meta = [
        {"nome": str(g(e, "Estabelecimento", default="—")),
         "gasto": round(float(g(e, "Total (R$)", "total") or 0), 2)}
        for e in (estabelecimentos or [])
    ]
    evol_meta = [
        {"label": str(x.get("label", "")), "total": round(float(x.get("total", 0) or 0), 2)}
        for x in (evolucao_mensal or [])
    ]
    proj_meta = [
        {"label": str(g(p, "Mes", default="")),
         "total": round(float(g(p, "Valor projetado", default=0) or 0), 2)}
        for p in (projecao or [])
    ]

    assin_meta = [
        {"nome": str(g(a, "Assinatura", default="—")),
         "gasto": round(float(g(a, "Media mensal", default=0) or 0), 2)}
        for a in (assinaturas or [])
    ]

    chart_meta = {
        "categorias_mes": [{"nome": c["nome"], "gasto": c["gasto"], "orcamento": 0.0,
                            "pct_usado": 0.0, "essencialidade": c["essencialidade"]}
                           for c in cats_meta],
        "categorias_anual": cats_meta,
        "essencialidade_mes": {k: ess.get(k, 0.0) for k in
                               ("essencial", "nao_essencial", "nao_classificada")},
        "cartao_estabelecimentos": estab_meta,
        "cartao_evolucao": evol_meta,
        "cartao_projecao": proj_meta,
        "cartao_assinaturas": assin_meta,
        "escopo_cartao": True,
    }

    # ── Texto do contexto ────────────────────────────────────────────────────
    L: list[str] = []
    L.append("CONTEXTO: aba CARTÃO DE CRÉDITO (fatura importada por CSV).")
    L.append(f"  Filtro ativo: {filtro_label}")
    L.append(f"  Data de hoje: {_date.today().strftime('%d/%m/%Y')}")
    L.append("  IMPORTANTE: aqui é a FATURA do cartão (fluxo futuro), NÃO o fluxo de caixa")
    L.append("  mensal manual. 'compra' entra na fatura; 'pagamento'/'estorno'/'tarifa' são")
    L.append("  ajustes. Valores em Reais (BRL).")

    L.append("")
    L.append("RESUMO DA FATURA (no filtro atual):")
    L.append(f"  Total de compras: {_brl(resumo.get('total_compras', 0))} "
             f"em {resumo.get('compras_qtd', 0)} compras")
    L.append(f"  Ticket médio: {_brl(resumo.get('ticket_medio', 0))}")
    L.append(f"  Maior compra: {_brl(resumo.get('maior_valor', 0))} "
             f"({resumo.get('maior_desc', '-')})")
    L.append(f"  Parceladas: {_brl(resumo.get('parceladas_total', 0))} "
             f"em {resumo.get('parceladas_qtd', 0)} lançamentos")
    L.append(f"  Tarifas: {_brl(resumo.get('tarifas', 0))} · "
             f"Estornos: {_brl(resumo.get('estornos', 0))} · "
             f"Pagamentos: {_brl(resumo.get('pagamentos', 0))}")
    L.append(f"  Total líquido (compras − créditos): {_brl(resumo.get('total_liquido', 0))}")
    L.append(f"  Categoria dominante: {resumo.get('categoria_dominante', '-')} "
             f"({_pct(resumo.get('categoria_pct', 0))} das compras)")

    # ── Assinaturas (lista completa, de TODAS as faturas) ────────────────────
    L.append("")
    L.append("ASSINATURAS / SERVIÇOS RECORRENTES (consolidado de TODAS as faturas,")
    L.append("por marca — inclui variações de nome do mesmo serviço e as linhas de IOF):")
    if assinaturas:
        total_mes = 0.0
        for a in assinaturas:
            media = float(g(a, "Media mensal", default=0) or 0)
            total_mes += media
            L.append(f"  {g(a, 'Assinatura', default='—')}: ~{_brl(media)}/mês "
                     f"(total {_brl(g(a, 'Total (R$)', default=0))} em "
                     f"{int(g(a, 'Meses', default=1) or 1)} mês(es); "
                     f"categoria: {g(a, 'Categoria', default='-')})")
        L.append(f"  SOMA ESTIMADA DAS ASSINATURAS: ~{_brl(total_mes)}/mês.")
        L.append("  OBS: esta é a fonte confiável para 'quais assinaturas eu tenho' — use-a")
        L.append("  em vez de inferir recorrência estabelecimento a estabelecimento. 'Meses' = ")
        L.append("  meses distintos com cobrança; média mensal = total ÷ meses.")
    else:
        L.append("  Nenhuma assinatura recorrente identificada nas faturas.")

    L.append("")
    L.append("COMPRAS POR CATEGORIA (categoria: total | nº | ticket | % | essencialidade):")
    if categorias:
        for c in categorias:
            nome = str(g(c, "Categoria", default="—"))
            L.append(f"  {nome}: {_brl(g(c, 'Total (R$)', 'total'))} | "
                     f"{int(g(c, 'Transacoes', 'transacoes') or 0)} | "
                     f"{_brl(g(c, 'Ticket medio', 'ticket'))} | "
                     f"{_pct(g(c, '% compras', 'pct'))} | {_card_essencialidade(nome)}")
    else:
        L.append("  Sem compras no filtro atual.")

    L.append("")
    L.append("ESSENCIAL × NÃO ESSENCIAL (compras do cartão):")
    tot_ess = sum(ess.values()) or 1.0
    L.append(f"  Essenciais: {_brl(ess['essencial'])} ({_pct(ess['essencial'] / tot_ess * 100)})")
    L.append(f"  Não essenciais: {_brl(ess['nao_essencial'])} "
             f"({_pct(ess['nao_essencial'] / tot_ess * 100)})")
    L.append(f"  Não classificadas: {_brl(ess['nao_classificada'])} "
             f"({_pct(ess['nao_classificada'] / tot_ess * 100)})")
    L.append("  (Essenciais: alimentação, mercado, transporte, saúde, educação, casa, tarifas;")
    L.append("   não essenciais: compras/varejo, assinaturas, lazer, cuidados pessoais.)")

    L.append("")
    L.append("MAIORES ESTABELECIMENTOS (estab.: total | nº | categoria | recorrência):")
    if estabelecimentos:
        for e in estabelecimentos[:15]:
            L.append(f"  {g(e, 'Estabelecimento', default='—')}: "
                     f"{_brl(g(e, 'Total (R$)', 'total'))} | "
                     f"{int(g(e, 'Transacoes', 'transacoes') or 0)} | "
                     f"{g(e, 'Categoria principal', default='-')} | "
                     f"{g(e, 'Recorrencia', default='-')}")
    else:
        L.append("  Sem estabelecimentos no filtro atual.")

    if recorrentes:
        L.append("")
        L.append("GASTOS RECORRENTES DETECTADOS (estab.: total | meses | valor médio):")
        for r in recorrentes[:12]:
            L.append(f"  {g(r, 'Estabelecimento', default='—')}: "
                     f"{_brl(g(r, 'Total (R$)', 'total'))} | "
                     f"{int(g(r, 'Meses', default=0) or 0)} meses | "
                     f"{_brl(g(r, 'Valor medio', default=0))}")

    if parcelas:
        L.append("")
        L.append("COMPRAS PARCELADAS (estab.: valor no mês | parcela | restantes | pendente):")
        for p in parcelas[:15]:
            L.append(f"  {g(p, 'Estabelecimento', default='—')}: "
                     f"{_brl(g(p, 'Valor no mes', default=0))} | "
                     f"{int(g(p, 'Parcela atual', default=1) or 1)}/"
                     f"{int(g(p, 'Total parcelas', default=1) or 1)} | "
                     f"faltam {int(g(p, 'Restantes', default=0) or 0)} | "
                     f"pendente {_brl(g(p, 'Pendente estimado', default=0))}")

    if projecao:
        L.append("")
        L.append("PROJEÇÃO DE FATURAS FUTURAS (pelas parcelas restantes) — ESTIMATIVA:")
        for p in projecao:
            L.append(f"  {g(p, 'Mes', default='')}: {_brl(g(p, 'Valor projetado', default=0))} "
                     f"({int(g(p, 'Parcelas futuras', default=0) or 0)} parcela(s))")

    if evolucao_mensal:
        L.append("")
        L.append("EVOLUÇÃO MENSAL DAS COMPRAS (mês: total):")
        for x in evolucao_mensal:
            L.append(f"  {x.get('label', '')}: {_brl(x.get('total', 0))}")

    if nao_consumo:
        L.append("")
        L.append("AJUSTES (tarifas/estornos/pagamentos) — top por valor:")
        for a in nao_consumo[:10]:
            L.append(f"  {g(a, 'Tipo', default='-')}: {g(a, 'Descricao', default='-')} "
                     f"({g(a, 'Categoria', default='-')}) {_brl(g(a, 'Valor (R$)', default=0))}")

    return "\n".join(L), chart_meta
