"""Orçamento mensal por categoria e conciliação manual × extrato.

Por que existe:

1. Orçamento. Sem orçamento cadastrado, o Controle e o Dashboard Geral
   inventavam um: `gasto × 1,2`. Toda categoria aparecia "83% usada", o card
   "Maior Categoria" falava em "% do orçamento" de um limite que ninguém
   definiu, a IA recebia esse limite como fato, e o componente de orçamento
   da nota de saúde dava 20 pontos cheios a quem nunca orçou nada. Aqui,
   categoria sem limite fica SEM orçamento (`orcamento=None`), e quem mede
   trata isso como ausência, não como aprovação.

   O limite vale do mês em que foi gravado em diante, até outro mês gravar
   um valor novo. Limite 0 encerra o orçamento daquela categoria. Sem essa
   regra, o usuário teria de digitar o mesmo número todo mês, e um mês
   esquecido voltaria a parecer "sem orçamento".

2. Conciliação. O fluxo de caixa soma o lançamento manual (`source` manual)
   e o extrato importado (`source='import'`). Quem digita o gasto e depois
   sobe o extrato conta a mesma saída duas vezes, sem nenhum erro visível.
   `duplicatas_provaveis` aponta os pares, e só aponta: quem apaga é o
   usuário, porque dois cafés de R$ 12 no mesmo dia são legítimos.

Módulo puro, exceto `carregar_vigentes`, que recebe a conexão pronta.
"""
from __future__ import annotations

from datetime import date

LIMIAR_ESTOURO = 90.0
LIMIAR_ALERTA = 75.0

SQL_ORCAMENTOS_VIGENTES = """
    SELECT DISTINCT ON (b.category_id)
           b.category_id::text AS category_id,
           c.name              AS category_name,
           b.amount_limit,
           b.month_year
    FROM   budgets b
    JOIN   categories c ON c.id = b.category_id
    WHERE  b.user_id    = :uid
      AND  b.month_year <= :mes_inicio
    ORDER  BY b.category_id, b.month_year DESC
"""

SQL_UPSERT_ORCAMENTO = """
    INSERT INTO budgets (user_id, category_id, month_year, amount_limit)
    VALUES (CAST(:uid AS uuid), CAST(:category_id AS uuid), :mes_inicio, :limite)
    ON CONFLICT (user_id, category_id, month_year)
    DO UPDATE SET amount_limit = EXCLUDED.amount_limit
"""


def carregar_vigentes(conn, owner: str, mes_inicio: date) -> dict[str, float]:
    """{categoria: limite} vigente no mês. Limite 0 (encerrado) fica de fora."""
    from sqlalchemy import text

    rows = conn.execute(
        text(SQL_ORCAMENTOS_VIGENTES), {"uid": owner, "mes_inicio": mes_inicio}
    ).fetchall()
    return vigentes([
        {"categoria": r.category_name, "limite": r.amount_limit, "mes": r.month_year}
        for r in rows
    ], mes_inicio)


def vigentes(registros: list[dict], mes_inicio: date) -> dict[str, float]:
    """Limite em vigor por categoria: o último gravado até `mes_inicio`.

    `registros`: [{"categoria", "limite", "mes"}]. Registro de mês futuro não
    vale ainda; limite 0 ou negativo encerra o orçamento.
    """
    ultimo: dict[str, tuple[date, float]] = {}
    for r in registros or []:
        mes = r.get("mes")
        if mes is None or mes > mes_inicio:
            continue
        cat = r.get("categoria")
        try:
            limite = float(r.get("limite") or 0.0)
        except (TypeError, ValueError):
            continue
        if cat not in ultimo or mes > ultimo[cat][0]:
            ultimo[cat] = (mes, limite)
    return {cat: lim for cat, (_m, lim) in ultimo.items() if lim > 0}


def linha_categoria(nome: str, gasto: float, limite: float | None) -> dict:
    """Linha de consumo de uma categoria.

    Sem limite: `orcamento` e `pct_usado` são None e o badge é neutro. Não
    existe "orçamento implícito".
    """
    gasto = round(float(gasto or 0.0), 2)
    if not limite or limite <= 0:
        return {"nome": nome, "gasto": gasto, "orcamento": None,
                "pct_usado": None, "tipo_badge": "neutro"}
    pct = round(gasto / float(limite) * 100, 1)
    badge = ("erro" if pct >= LIMIAR_ESTOURO
             else "alerta" if pct >= LIMIAR_ALERTA else "sucesso")
    return {"nome": nome, "gasto": gasto, "orcamento": round(float(limite), 2),
            "pct_usado": pct, "tipo_badge": badge}


def consumo(gastos: dict[str, float], limites: dict[str, float]) -> list[dict]:
    """Uma linha por categoria com gasto OU orçamento, maior gasto primeiro.

    Categoria orçada e sem gasto no mês entra com 0%: é justamente a que
    está dentro do limite, e sumir com ela esconderia o orçamento.
    """
    nomes = set(gastos) | set(limites)
    linhas = [linha_categoria(n, gastos.get(n, 0.0), limites.get(n)) for n in nomes]
    return sorted(linhas, key=lambda c: (-c["gasto"], c["nome"]))


def resumo(linhas: list[dict]) -> dict:
    """Totais só sobre as categorias orçadas.

    `cobertura_pct` é a parte do gasto do mês que tem orçamento: 40% de
    cobertura com tudo "dentro do limite" não é um mês sob controle.
    """
    orcadas = [c for c in linhas if c.get("orcamento")]
    gasto_total = sum(c["gasto"] for c in linhas)
    gasto_orcado = sum(c["gasto"] for c in orcadas)
    return {
        "categorias_orcadas": len(orcadas),
        "categorias_estouradas": sum(
            1 for c in orcadas if (c.get("pct_usado") or 0) >= LIMIAR_ESTOURO),
        "limite_total": round(sum(c["orcamento"] for c in orcadas), 2),
        "gasto_orcado": round(gasto_orcado, 2),
        "cobertura_pct": (round(gasto_orcado / gasto_total * 100, 1)
                          if gasto_total > 0 else None),
    }


# ── Conciliação manual × extrato ─────────────────────────────────────────────

TOLERANCIA_DIAS = 3


def _eh_importado(tx: dict) -> bool:
    return str(tx.get("source") or "manual").strip().lower() == "import"


def _eh_fatura_csv(tx: dict) -> bool:
    return (str(tx.get("account_type") or "") == "credit_card"
            and str(tx.get("source") or "").strip().lower() == "csv")


def duplicatas_provaveis(transacoes: list[dict],
                         tolerancia_dias: int = TOLERANCIA_DIAS) -> list[dict]:
    """Pares (manual, importado) que parecem a mesma saída ou entrada.

    Critério: mesma conta, mesmo tipo de fluxo, mesmo valor em centavos e
    datas a até `tolerancia_dias` de distância (o banco lança D+1, D+2).
    Cada lançamento entra em no máximo um par, e o casamento escolhe a data
    mais próxima primeiro. A fatura CSV do cartão fica de fora: é fluxo
    futuro e não disputa o caixa do mês.

    Cada `transacao` precisa de: id, valor, data, tipo_fluxo, conta, source.
    """
    candidatos = [t for t in transacoes or [] if not _eh_fatura_csv(t) and t.get("data")]
    manuais = [t for t in candidatos if not _eh_importado(t)]
    importados = [t for t in candidatos if _eh_importado(t)]

    possiveis: list[tuple[int, str, str, dict, dict]] = []
    for m in manuais:
        for i in importados:
            if m.get("conta") != i.get("conta"):
                continue
            if m.get("tipo_fluxo") != i.get("tipo_fluxo"):
                continue
            if round(abs(float(m.get("valor") or 0)) * 100) != round(abs(float(i.get("valor") or 0)) * 100):
                continue
            dias = abs((m["data"] - i["data"]).days)
            if dias > tolerancia_dias:
                continue
            possiveis.append((dias, str(m.get("id")), str(i.get("id")), m, i))

    possiveis.sort(key=lambda p: (p[0], p[1], p[2]))
    usados: set[str] = set()
    pares: list[dict] = []
    for dias, id_m, id_i, m, i in possiveis:
        if id_m in usados or id_i in usados:
            continue
        usados.update((id_m, id_i))
        pares.append({"manual": m, "importado": i, "dias": dias,
                      "valor": abs(float(m.get("valor") or 0))})
    return sorted(pares, key=lambda p: (p["manual"]["data"], str(p["manual"].get("id"))))
