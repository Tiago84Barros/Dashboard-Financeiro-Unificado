"""Relatórios da empresa a partir dos lançamentos: puros, sem banco.

Um lançamento é um dict com ``data`` (date), ``valor`` (com sinal: entrada
positiva), ``categoria`` (código de :mod:`core.pj.plano_contas` ou ``None``) e
``status`` (``pago`` ou ``previsto``). Só o pago entra em DRE e caixa.

Lançamento sem categoria fica FORA da DRE e é contado à parte: somá-lo em
"outras despesas" daria um resultado com cara de certo e composição inventada.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from core.pj.plano_contas import CODIGOS_FOLHA, GRUPOS_DRE, POR_CODIGO, normalizar

_GRUPOS_RESULTADO = ("receita_bruta", "deducoes", "custos", "despesas_operacionais",
                     "pessoal", "financeiro")


def _pagos(lancs, periodos: set[tuple[int, int]] | None = None):
    for lanc in lancs:
        if lanc.get("status") != "pago" or not isinstance(lanc.get("data"), date):
            continue
        if periodos is not None and (lanc["data"].year, lanc["data"].month) not in periodos:
            continue
        yield lanc


def _grupo(lanc) -> str | None:
    cat = POR_CODIGO.get(lanc.get("categoria") or "")
    return cat.grupo if cat else None


def dre(lancs: list[dict], periodos: list[tuple[int, int]]) -> dict:
    """DRE gerencial (caixa) somando os ``periodos`` (ano, mês).

    Devolve linhas por grupo e por categoria, subtotais e o que ficou fora.
    """
    alvo = set(periodos)
    por_grupo: dict[str, float] = defaultdict(float)
    por_categoria: dict[str, float] = defaultdict(float)
    sem_cat_n, sem_cat_total = 0, 0.0
    for lanc in _pagos(lancs, alvo):
        grupo = _grupo(lanc)
        if grupo is None:
            sem_cat_n += 1
            sem_cat_total += float(lanc["valor"])
            continue
        por_grupo[grupo] += float(lanc["valor"])
        por_categoria[lanc["categoria"]] += float(lanc["valor"])

    g = {k: round(por_grupo.get(k, 0.0), 2) for k, _ in GRUPOS_DRE}
    receita_liquida = round(g["receita_bruta"] + g["deducoes"], 2)
    lucro_bruto = round(receita_liquida + g["custos"], 2)
    operacional = round(lucro_bruto + g["despesas_operacionais"] + g["pessoal"], 2)
    resultado = round(operacional + g["financeiro"], 2)
    return {
        "grupos": g,
        "categorias": {k: round(v, 2) for k, v in por_categoria.items()},
        "receita_liquida": receita_liquida,
        "lucro_bruto": lucro_bruto,
        "resultado_operacional": operacional,
        "resultado": resultado,
        "margem": round(resultado / g["receita_bruta"], 4) if g["receita_bruta"] > 0 else None,
        "sem_categoria_n": sem_cat_n,
        "sem_categoria_total": round(sem_cat_total, 2),
    }


def serie_mensal(lancs: list[dict], ate: date, meses: int = 12) -> list[dict]:
    """Últimos ``meses`` meses até ``ate`` (inclusive), do mais antigo ao atual."""
    periodos = []
    ano, mes = ate.year, ate.month
    for _ in range(meses):
        periodos.append((ano, mes))
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    periodos.reverse()
    saida = []
    for a, m in periodos:
        d = dre(lancs, [(a, m)])
        entradas = sum(float(x["valor"]) for x in _pagos(lancs, {(a, m)}) if float(x["valor"]) > 0)
        saidas = sum(-float(x["valor"]) for x in _pagos(lancs, {(a, m)}) if float(x["valor"]) < 0)
        saida.append({
            "ano": a, "mes": m,
            "receita": d["grupos"]["receita_bruta"],
            # Base do Simples: receita bruta menos devoluções e vendas
            # canceladas (LC 123/2006, art. 3º, § 1º). A devolução mora em
            # Deduções na DRE, mas o DAS e a RBT12 a descontam da receita.
            "receita_simples": round(d["grupos"]["receita_bruta"]
                                     + d["categorias"].get("devolucoes", 0.0), 2),
            "resultado": d["resultado"],
            "entradas": round(entradas, 2),
            "saidas": round(saidas, 2),
            "folha": round(-sum(float(x["valor"]) for x in _pagos(lancs, {(a, m)})
                                if x.get("categoria") in CODIGOS_FOLHA), 2),
            "das_pago": round(-sum(float(x["valor"]) for x in _pagos(lancs, {(a, m)})
                                   if x.get("categoria") == "das"), 2),
            "sem_categoria": d["sem_categoria_n"],
        })
    return saida


def meses_com_movimento(lancs: list[dict]) -> list[tuple[int, int]]:
    return sorted({(x["data"].year, x["data"].month) for x in _pagos(lancs)})


def saldo_ate(saldo_inicial: float, data_inicial: date | None, lancs: list[dict],
              data: date) -> float:
    """Saldo da conta ao fim de ``data``, ancorado no saldo de abertura.

    Depois da âncora: abertura + pagos entre a âncora e a data. ANTES dela
    (extrato mais antigo importado depois): abertura − pagos entre a data e a
    âncora -- sem isso a conferência de um extrato antigo acusava diferença
    que não existe (achado do Codex no PR #680). A mesma conta, em SQL, está em
    ``core.confianca_secao._SQL_CNPJ_DIFERENCA_DE_SALDO``.
    """
    total = float(saldo_inicial or 0.0)
    for x in _pagos(lancs):
        if data_inicial is None or data >= data_inicial:
            if (data_inicial is None or x["data"] > data_inicial) and x["data"] <= data:
                total += float(x["valor"])
        elif data < x["data"] <= data_inicial:
            total -= float(x["valor"])
    return round(total, 2)


def saldo_inicial_pelo_extrato(saldo_final: float, movimentos: list[dict],
                               data_saldo: date) -> tuple[float, date] | None:
    """Saldo de abertura deduzido do primeiro extrato: saldo final menos os
    movimentos até a data do saldo, na véspera do primeiro movimento."""
    ate = [m for m in movimentos if m["data"] <= data_saldo]
    if not ate:
        return None
    primeiro = min(m["data"] for m in ate)
    from datetime import timedelta
    return round(float(saldo_final) - sum(float(m["valor"]) for m in ate), 2), primeiro - timedelta(days=1)


def meses_desde_o_inicio(serie: list[dict], lancs: list[dict]) -> list[dict]:
    """Os meses de ``serie`` a partir do início da atividade (primeiro
    lançamento pago), para a RBT12 do Simples.

    Não corta na primeira RECEITA da janela: mês sem faturamento de empresa
    que já existia é zero de verdade, e descartá-lo faria a RBT12 anualizar
    só os meses bons (achado do Codex no PR #680).
    """
    datas = [x["data"] for x in _pagos(lancs)]
    if not datas:
        return []
    inicio = min(datas)
    return [s for s in serie if (s["ano"], s["mes"]) >= (inicio.year, inicio.month)]


def lancamentos_apos_importacao(existentes: list[dict], novos: list[dict],
                                vinculos: dict[int, str]) -> list[dict]:
    """Os lançamentos como ficam se a prévia for gravada, para a conferência
    de saldo usar a MESMA conta do saldo gravado (:func:`saldo_ate`).

    ``vinculos``: índice em ``novos`` -> id do lançamento existente que ele
    concilia. O conciliado vira pago na data do banco, como faz
    ``repositorio.importar_extrato``; o resto entra como pago novo. A prévia
    fazia uma conta própria só para frente e acusava diferença falsa em
    extrato mais antigo que a abertura (achado do Codex no PR #680).
    """
    por_id = {x["id"]: dict(x) for x in existentes if x.get("id")}
    saida = [x for x in existentes if not x.get("id")]
    for i, m in enumerate(novos):
        alvo = vinculos.get(i)
        if alvo and alvo in por_id:
            por_id[alvo].update(status="pago", data=m["data"])
        else:
            saida.append({"data": m["data"], "valor": m["valor"], "status": "pago"})
    return list(por_id.values()) + saida


def filtrar_lancamentos(lancs: list[dict], categorias: list[str | None] | None = None,
                        busca: str = "") -> list[dict]:
    """Lançamentos que passam no filtro de categoria e na busca por descrição.

    ``categorias`` vazio ou ``None`` não filtra; ``None`` dentro da lista é
    "sem categoria". A busca ignora acento, caixa e pontuação, e cada palavra
    digitada precisa aparecer na descrição ("luana pix" acha
    "Transf Pix enviada - Luana ..."), em qualquer ordem.
    """
    termos = normalizar(busca).split()
    filtro_cat = set(categorias) if categorias else None
    saida = []
    for x in lancs:
        if filtro_cat is not None and (x.get("categoria") or None) not in filtro_cat:
            continue
        if termos:
            desc = normalizar(x.get("descricao"))
            if not all(t in desc for t in termos):
                continue
        saida.append(x)
    return saida


def balanco(lancs: list[dict], saldo_inicial: float | None, data_inicial: date | None,
            data: date, contas_em_aberto: bool = True) -> dict | None:
    """Balanço patrimonial gerencial na ``data``, montado só com o que o app tem.

    Não há escrituração: o balanço sai do caixa conferido com o banco, das
    contas a pagar e a receber em aberto e das categorias "fora do resultado"
    (empréstimo, aplicação, aporte, distribuição, transferência). Fecha por
    construção, porque o caixa é o saldo de abertura mais os pagos:

        Caixa = saldo anterior + resultado + aportes + distribuições
                + empréstimos + aplicações + transferências + sem categoria

    com "saldo anterior" = caixa − todos os pagos registrados até a data, ou
    seja, o dinheiro que já estava na conta antes do primeiro lançamento.

    e cada parcela vai para o seu lado: aplicação e transferência viram ativo
    (o dinheiro foi para outro lugar da empresa), empréstimo vira passivo, o
    resto compõe o patrimônio líquido. As contas em aberto (previstos) entram
    no ativo/passivo e, pela diferença, no patrimônio, como resultado que o
    caixa ainda não viu. Previsto de categoria fora do resultado (parcela de
    empréstimo, por exemplo) fica de fora: a dívida já está em Empréstimos.

    ``contas_em_aberto=False`` para data passada: o app não guarda quando um
    previsto foi criado, então não sabe o que estava em aberto naquele dia.

    ``None`` sem saldo de abertura: sem ele não há caixa.
    """
    if saldo_inicial is None:
        return None
    caixa = saldo_ate(saldo_inicial, data_inicial, lancs, data)
    # Todo o histórico registrado até a data, inclusive o que é anterior à
    # âncora do saldo de abertura (extrato mais antigo importado depois): a
    # âncora serve para o CAIXA, não para descartar empréstimo ou aplicação
    # que o app conhece. O que sobra do caixa é o saldo anterior ao primeiro
    # lançamento registrado (achado do Codex no PR #686).
    fluxo = [x for x in _pagos(lancs) if x["data"] <= data]
    por_cat: dict[str, float] = defaultdict(float)
    resultado = sem_cat = 0.0
    for x in fluxo:
        grupo = _grupo(x)
        if grupo is None:
            sem_cat += float(x["valor"])
        elif grupo == "fora_do_resultado":
            por_cat[x["categoria"]] += float(x["valor"])
        else:
            resultado += float(x["valor"])
    abertura = round(caixa - sum(float(x["valor"]) for x in fluxo), 2)

    a_receber = a_pagar = 0.0
    if contas_em_aberto:
        for x in lancs:
            if x.get("status") != "previsto" or _grupo(x) == "fora_do_resultado":
                continue
            v = float(x["valor"])
            if v > 0:
                a_receber += v
            else:
                a_pagar -= v

    def r(v: float) -> float:
        return round(v, 2)

    ativo = [("Caixa e bancos", r(caixa)),
             ("Contas a receber", r(a_receber)),
             ("Aplicações financeiras", r(-por_cat["aplicacao"])),
             ("Outras contas da empresa (transferências)", r(-por_cat["transferencia"]))]
    passivo = [("Contas a pagar", r(a_pagar)),
               ("Empréstimos e financiamentos", r(por_cat["emprestimo"]))]
    pl = [("Saldo anterior aos lançamentos registrados", abertura),
          ("Aportes de sócios", r(por_cat["aporte_socio"])),
          ("Distribuição de lucros", r(por_cat["distribuicao_lucros"])),
          ("Resultado acumulado (DRE, caixa)", r(resultado)),
          ("Contas em aberto (a receber − a pagar)", r(a_receber - a_pagar)),
          ("Movimentos sem categoria", r(sem_cat))]
    total_ativo = r(sum(v for _, v in ativo))
    total_passivo = r(sum(v for _, v in passivo))
    total_pl = r(sum(v for _, v in pl))
    return {"data": data, "ativo": ativo, "passivo": passivo, "patrimonio_liquido": pl,
            "total_ativo": total_ativo, "total_passivo": total_passivo, "total_pl": total_pl,
            "contas_em_aberto": contas_em_aberto,
            "fecha": abs(total_ativo - total_passivo - total_pl) < 0.01}
