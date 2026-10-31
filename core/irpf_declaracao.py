"""
core/irpf_declaracao.py
Os investimentos do ano-calendário no formato das fichas do programa IRPF
(exercício 2026 em diante): cada número sai com o nome da linha e o código
que o programa usa, pronto para digitar.

  RENDA VARIÁVEL - OPERAÇÕES COMUNS/DAYTRADE   12 meses, linha a linha
  FUNDOS DE INVESTIMENTO IMOBILIÁRIO ...        12 meses, linha a linha
  RENDIMENTOS ISENTOS E NÃO TRIBUTÁVEIS         20 (ações até R$ 20 mil),
                                                09 (dividendos), 99 (FII)
  RENDIMENTOS SUJEITOS À TRIBUTAÇÃO EXCLUSIVA   10 (JCP)
  IMPOSTO PAGO/RETIDO                           03 (IRRF Lei 11.033)
  BENS E DIREITOS                               situação em 31/12 de ano-1 e ano

A apuração é a de ``core.ir_renda_variavel.apurar``; aqui só se reorganiza
pelo layout do programa. O programa calcula sozinho a maior parte das linhas;
``DIGITAR`` diz quais o contribuinte preenche, para a tela destacar.

Código que as fontes não fecham entre si sai com ``conferir=True`` e a
descrição oficial do item, que é o que aparece na lista do programa.

O que o app não sabe fica ``None``, nunca zero: *Imposto pago* (o DARF que o
mês gera não prova que foi recolhido; vai em ``darf_calculado``) e o custo de
posição que a corretora não informou.

Tudo aqui é puro, menos ``carregar_insumos``.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from core.fii_ticker import e_ticker_fii
from core.ir_renda_variavel import _TICKER_B3, ALIQUOTA, _base, ler_cnpj_emissores

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_CENTAVO = Decimal("0.01")
# Primeiro ano-calendário no layout destas fichas (exercício 2026). Os anos
# antes continuam na apuração -- carregam prejuízo e IRRF --, mas não viram
# declaração: os códigos de Bens e Direitos eram outros.
ANO_MINIMO = 2025
MESES = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
         "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")

# ─────────────────────────────────────────────────────────────────────────────
# Linhas das fichas mensais, na ordem do programa
# ─────────────────────────────────────────────────────────────────────────────

LINHAS_RV = (
    ("mercado_vista_acoes", "Mercado à vista – ações"),
    ("resultado_liquido", "Resultado líquido do mês"),
    ("resultado_negativo_anterior", "Resultado negativo até o mês anterior"),
    ("base_calculo", "Base de cálculo do imposto"),
    ("prejuizo_compensar", "Prejuízo a compensar"),
    ("aliquota", "Alíquota do imposto"),
    ("imposto_devido", "Imposto devido"),
)
LINHAS_RV_CONSOLIDACAO = (
    ("total_imposto_devido", "Total do imposto devido"),
    ("irrf_dt_mes", "IR fonte de Day-Trade no mês"),
    ("irrf_dt_anteriores", "IR fonte de Day-Trade nos meses anteriores"),
    ("irrf_dt_compensar", "IR fonte de Day-Trade a compensar"),
    ("irrf_lei_mes", "IR fonte (Lei nº 11.033/2004) no mês"),
    ("irrf_lei_anteriores", "IR fonte (Lei nº 11.033/2004) nos meses anteriores"),
    ("irrf_lei_compensar", "IR fonte (Lei nº 11.033/2004) a compensar"),
    ("imposto_a_pagar", "Imposto a pagar"),
    ("imposto_pago", "Imposto pago"),
)
LINHAS_FII = (
    ("resultado_liquido", "Resultado líquido do mês"),
    ("resultado_negativo_anterior", "Resultado negativo até o mês anterior"),
    ("base_calculo", "Base de cálculo do imposto"),
    ("prejuizo_compensar", "Prejuízo a compensar"),
    ("aliquota", "Alíquota do imposto"),
    ("imposto_devido", "Imposto devido"),
    ("retido_mes", "Imposto retido no mês"),
    ("retido_anteriores", "Imposto retido em meses anteriores"),
    ("retido_compensar", "Imposto retido a compensar"),
    ("imposto_a_pagar", "Imposto a pagar"),
    ("imposto_pago", "Imposto pago"),
)

# O que o contribuinte digita; o resto o programa calcula. O resultado
# negativo anterior só se digita em janeiro (vem da declaração do ano antes).
DIGITAR = {
    "mercado_vista_acoes", "irrf_dt_mes", "irrf_lei_mes", "imposto_pago",
    "retido_mes",
}
DIGITAR_EM_JANEIRO = {"resultado_negativo_anterior"}
DIGITAR_FII = {"resultado_liquido", "retido_mes", "imposto_pago"}

# ─────────────────────────────────────────────────────────────────────────────
# Códigos
# ─────────────────────────────────────────────────────────────────────────────

ISENTO_ACOES = {"codigo": "20", "conferir": True,
                "descricao": "Ganhos líquidos em operações no mercado à vista de ações "
                             "negociadas em bolsas de valores nas alienações realizadas até "
                             "R$ 20.000,00 em cada mês, para o conjunto de ações"}
ISENTO_DIVIDENDOS = {"codigo": "09", "conferir": False,
                     "descricao": "Lucros e dividendos recebidos"}
ISENTO_FII = {"codigo": "99", "conferir": True,
              "descricao": "Outros (rendimentos de FII e Fiagro)"}
EXCLUSIVA_JCP = {"codigo": "10", "conferir": False,
                 "descricao": "Juros sobre capital próprio"}
# Distribuição de FIP-IE a pessoa física tem alíquota zero (Lei 11.478/2007,
# art. 2º); o programa não tem linha própria e as fontes divergem entre 99 e
# Tributação Exclusiva com valor zero.
ISENTO_FIP_IE = {"codigo": "99", "conferir": True,
                 "descricao": "Outros (rendimentos de FIP-IE, alíquota zero)"}
# O valor é ESTIMADO (0,005% das vendas, 1% do ganho de day-trade): o retido
# de verdade está nas notas de corretagem e no informe da corretora.
PAGO_RETIDO_LEI = {"codigo": "03", "conferir": True, "estimado": True,
                   "descricao": "Imposto sobre a renda na fonte (Lei nº 11.033/2004)"}

BENS = {
    "acao": {"grupo": "03", "codigo": "01", "conferir": False,
             "descricao": "Ações (inclusive as listadas em Bolsa)"},
    "bdr": {"grupo": "04", "codigo": "04", "conferir": True,
            "descricao": "Ativos Negociados em Bolsa no Brasil, Exceto Ações e Fundos"},
    "direito": {"grupo": "04", "codigo": "04", "conferir": True,
                "descricao": "Ativos Negociados em Bolsa no Brasil, Exceto Ações e Fundos"},
    "fii": {"grupo": "07", "codigo": "03", "conferir": False,
            "descricao": "Fundos de Investimento Imobiliário (FII)"},
    "fiagro": {"grupo": "07", "codigo": "02", "conferir": False,
               "descricao": "Fiagro"},
    "etf": {"grupo": "07", "codigo": "05", "conferir": True,
            "descricao": "FIP - Entidade de investimento, FIDC - Entidade de investimento "
                         "sem tributação periódica (come-cotas), ETF - Entidade de investimento"},
    "fip": {"grupo": "07", "codigo": "05", "conferir": True,
            "descricao": "FIP - Entidade de investimento, FIDC - Entidade de investimento "
                         "sem tributação periódica (come-cotas), ETF - Entidade de investimento"},
    "fip_ie": {"grupo": "07", "codigo": "06", "conferir": True,
               "descricao": "FIP-IE e FIP-PD&I"},
    # Fundo da posição consolidada que o nome não diz qual é: o código do
    # grupo 07 depende do tipo e da tributação, que só o informe traz.
    "fundo": {"grupo": "07", "codigo": None, "conferir": True,
              "descricao": "Fundo: o código depende do tipo e da tributação "
                           "(informe do administrador)"},
    "renda_fixa": {"grupo": "04", "codigo": "02", "conferir": False,
                   "descricao": "Títulos Públicos e Privados sujeitos à Tributação "
                                "(Tesouro Direto, CDB, RDB e outros)"},
}

# Instituições que aparecem como custodiante na posição consolidada. Título
# público vai com o CNPJ do agente de custódia (a corretora).
CNPJ_INSTITUICAO = {
    "XP INVESTIMENTOS": "02.332.886/0001-04",
}

# Tesouro Direto: ticker da posição -> título e data de vencimento como a
# declaração os nomeia. O Educa+ leva no ticker o ano do último pagamento.
_TESOURO = (
    (re.compile(r"^TPRE(\d{4})$"), "LTN 01/01/{}"),
    (re.compile(r"^TSELIC(\d{4})$"), "LFT 01/03/{}"),
    (re.compile(r"^TIPCA(\d{4})$"), "NTN-B PRINCIPAL 15/05/{}"),
    (re.compile(r"^TEDUCA(\d{4})$"), "NTN-B1 15/12/{}"),
    (re.compile(r"^TRENDA(\d{4})$"), "NTN-B1 15/12/{}"),
)


def _d(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def _c(v: Any) -> Decimal:
    """Em centavos, como o programa aceita (e sem o 0E-24 da aritmética)."""
    r = _d(v).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    return r if r != 0 else _ZERO.quantize(_CENTAVO)


def _limpar(s: str | None) -> str:
    """Texto de discriminação: maiúsculo, sem acento nem caractere estranho
    (o cadastro traz o travessão de 'CDB - BANCO BMG' corrompido)."""
    n = _sem_acento(str(s or "")).upper()
    n = re.sub(r"[^A-Z0-9 /.,&()-]+", " - ", n)
    n = re.sub(r"\s*-\s*(-\s*)*", " - ", n)
    return re.sub(r"\s+", " ", n).strip(" -")


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _fmt_qtd(q: Decimal) -> str:
    q = _d(q)
    if q == q.to_integral_value():
        return f"{int(q):d}"
    return f"{q.normalize():f}".replace(".", ",")


# ─────────────────────────────────────────────────────────────────────────────
# Fichas mensais
# ─────────────────────────────────────────────────────────────────────────────

def _cesta_vazia(prejuizo: Decimal, cesta: str) -> dict:
    return {"resultado": _ZERO, "prejuizo_anterior": prejuizo, "base": _ZERO,
            "prejuizo_a_compensar": prejuizo, "imposto": _ZERO,
            "sem_custo": [], "incompleta": False}


def _meses_do_ano(apuracao: dict, ano: int) -> list[dict | None]:
    por_mes = {m["mes"]: m for m in apuracao.get("meses") or []}
    return [por_mes.get(f"{ano:04d}-{i:02d}") for i in range(1, 13)]


def _prejuizo_antes(apuracao: dict, ano: int) -> dict[str, Decimal]:
    """Prejuízo de cada cesta ao fim do último mês apurado antes do ano."""
    antes = [m for m in apuracao.get("meses") or [] if m["mes"] < f"{ano:04d}-01"]
    if not antes:
        return {c: _ZERO for c in ("comum", "day_trade", "fii")}
    fim = antes[-1]["cestas"]
    return {c: fim[c]["prejuizo_a_compensar"] for c in fim}


def _linhas_cesta(c: dict, cesta: str) -> dict:
    return {
        "mercado_vista_acoes": _c(c["resultado"]),
        "resultado_liquido": _c(c["resultado"]),
        "resultado_negativo_anterior": _c(c["prejuizo_anterior"]),
        "base_calculo": _c(c["base"]),
        "prejuizo_compensar": _c(c["prejuizo_a_compensar"]),
        "aliquota": ALIQUOTA[cesta],
        "imposto_devido": _c(c["imposto"]),
    }


def ficha_renda_variavel(apuracao: dict, ano: int) -> list[dict]:
    """Os 12 meses da ficha RENDA VARIÁVEL - OPERAÇÕES COMUNS/DAYTRADE.

    Mês sem venda repete o prejuízo e os saldos de IRRF do mês anterior, como
    o programa faz; o saldo de IRRF começa o ano zerado.
    """
    prejuizo = _prejuizo_antes(apuracao, ano)
    saldo = {"day_trade": _ZERO, "lei_11033": _ZERO}
    out = []
    for i, m in enumerate(_meses_do_ano(apuracao, ano)):
        if m is None:
            cestas = {c: _cesta_vazia(prejuizo[c], c) for c in ("comum", "day_trade")}
            potes = {k: {"no_mes": _ZERO, "meses_anteriores": saldo[k],
                         "a_compensar": saldo[k]} for k in saldo}
            rv = {"imposto_devido": _ZERO, "a_pagar": _ZERO, "darf": _ZERO}
        else:
            cestas, potes, rv = m["cestas"], m["irrf"], m["rv"]
        for c in ("comum", "day_trade"):
            prejuizo[c] = cestas[c]["prejuizo_a_compensar"]
        for k in saldo:
            saldo[k] = potes[k]["a_compensar"]
        out.append({
            "mes": i + 1,
            "rotulo": MESES[i],
            "comum": _linhas_cesta(cestas["comum"], "comum"),
            "day_trade": _linhas_cesta(cestas["day_trade"], "day_trade"),
            "consolidacao": {
                "total_imposto_devido": _c(rv["imposto_devido"]),
                "irrf_dt_mes": _c(potes["day_trade"]["no_mes"]),
                "irrf_dt_anteriores": _c(potes["day_trade"]["meses_anteriores"]),
                "irrf_dt_compensar": _c(potes["day_trade"]["a_compensar"]),
                "irrf_lei_mes": _c(potes["lei_11033"]["no_mes"]),
                "irrf_lei_anteriores": _c(potes["lei_11033"]["meses_anteriores"]),
                "irrf_lei_compensar": _c(potes["lei_11033"]["a_compensar"]),
                "imposto_a_pagar": _c(rv["a_pagar"]),
                "imposto_pago": None,
            },
            "darf_calculado": _c(rv["darf"]),
            "ganho_isento": _c(cestas["comum"].get("ganho_isento", _ZERO)),
            "vendas_acoes": _c(m["vendas_acoes"] if m else _ZERO),
            "sem_custo": sorted(set(cestas["comum"]["sem_custo"])
                                | set(cestas["day_trade"]["sem_custo"])),
            "em_curso": bool(m and m["em_curso"]),
            "com_operacao": m is not None,
        })
    return out


def ficha_fii(apuracao: dict, ano: int) -> list[dict]:
    """Os 12 meses da ficha FUNDOS DE INVESTIMENTO IMOBILIÁRIO OU NAS CADEIAS
    PRODUTIVAS AGROINDUSTRIAIS."""
    prejuizo = _prejuizo_antes(apuracao, ano)["fii"]
    saldo = _ZERO
    out = []
    for i, m in enumerate(_meses_do_ano(apuracao, ano)):
        if m is None:
            c = _cesta_vazia(prejuizo, "fii")
            pote = {"no_mes": _ZERO, "meses_anteriores": saldo, "a_compensar": saldo}
            fii = {"imposto_devido": _ZERO, "a_pagar": _ZERO, "darf": _ZERO}
        else:
            c, pote, fii = m["cestas"]["fii"], m["irrf"]["fii"], m["fii"]
        prejuizo, saldo = c["prejuizo_a_compensar"], pote["a_compensar"]
        out.append({
            "mes": i + 1,
            "rotulo": MESES[i],
            "linhas": {
                "resultado_liquido": _c(c["resultado"]),
                "resultado_negativo_anterior": _c(c["prejuizo_anterior"]),
                "base_calculo": _c(c["base"]),
                "prejuizo_compensar": _c(c["prejuizo_a_compensar"]),
                "aliquota": ALIQUOTA["fii"],
                "imposto_devido": _c(c["imposto"]),
                "retido_mes": _c(pote["no_mes"]),
                "retido_anteriores": _c(pote["meses_anteriores"]),
                "retido_compensar": _c(pote["a_compensar"]),
                "imposto_a_pagar": _c(fii["a_pagar"]),
                "imposto_pago": None,
            },
            "darf_calculado": _c(fii["darf"]),
            "sem_custo": list(c["sem_custo"]),
            "em_curso": bool(m and m["em_curso"]),
            "com_operacao": m is not None,
        })
    return out


def outras_informacoes(rv: list[dict], fii: list[dict]) -> dict:
    """Totais do quadro OUTRAS INFORMAÇÕES das duas fichas.

    O total pago é a soma dos DARFs recolhidos, que o app não tem: fica
    ``None``, e ``darf_calculado`` diz quanto as fichas mandariam recolher.
    """
    return {"imposto_pago": None} | {k: _c(v) for k, v in {
        "darf_calculado": sum((m["darf_calculado"] for m in rv + fii), _ZERO),
        "imposto_devido": sum((m["consolidacao"]["total_imposto_devido"] for m in rv), _ZERO)
        + sum((m["linhas"]["imposto_devido"] for m in fii), _ZERO),
        "irrf_lei_11033": sum((m["consolidacao"]["irrf_lei_mes"] for m in rv), _ZERO)
        + sum((m["linhas"]["retido_mes"] for m in fii), _ZERO),
    }.items()}


# ─────────────────────────────────────────────────────────────────────────────
# CNPJ
# ─────────────────────────────────────────────────────────────────────────────

def cnpj_de(ticker: str, emissores: dict, mercado: dict) -> tuple[str | None, str | None]:
    """(CNPJ, nome) do ticker: CVM pelo ticker, CVM pela raiz de 4 letras
    (PETR3 e PETR4 são a mesma empresa), e por fim o cadastro de mercado."""
    t = _base(ticker)
    if t in emissores:
        return tuple(emissores[t])  # type: ignore[return-value]
    if t not in mercado:
        raiz = t[:4]
        for k, v in emissores.items():
            if k[:4] == raiz and not e_ticker_fii(t):
                return tuple(v)  # type: ignore[return-value]
    if t in mercado:
        return mercado[t]
    return None, None


def _formatar_cnpj(digitos: str | None) -> str | None:
    d = re.sub(r"\D", "", str(digitos or ""))
    if len(d) != 14:
        return None
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def _cnpj_instituicao(nome: str | None) -> str | None:
    n = _sem_acento(str(nome or "")).upper()
    return next((c for k, c in CNPJ_INSTITUICAO.items() if k in n), None)


def _cnpj_emissor_por_nome(nome: str, emissores: dict) -> tuple[str | None, str | None]:
    """Banco emissor de CDB pelo nome ('BANCO BMG S/A' -> Banco BMG na CVM)."""
    n = _sem_acento(nome).upper()
    n = re.sub(r"\b(BCO|BANCO)\b", "", n)
    n = re.sub(r"\bS[./]?A\b\.?", "", n)
    palavras = [p for p in re.split(r"[^A-Z0-9]+", n) if p]
    if not palavras:
        return None, None
    alvo = palavras[0]
    for cnpj, razao in emissores.values():
        r = _sem_acento(razao).upper()
        if re.search(rf"\bBANCO\s+{re.escape(alvo)}\b", r):
            return cnpj, razao
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Rendimentos
# ─────────────────────────────────────────────────────────────────────────────

def _e_fii(ticker: str, etfs: set[str], emissores: dict) -> bool:
    """FII/Fiagro pelo código -- menos ETF e menos unit de companhia aberta
    (SAPR11, TAEE11 terminam em 11 e estão no cadastro da CVM)."""
    from core.fii_ticker import e_ticker_fii

    t = _base(ticker)
    return t not in etfs and t not in emissores and e_ticker_fii(t)


def _e_fip_ie(nome: str | None) -> bool:
    n = _sem_acento(str(nome or "")).upper()
    return "INFRAESTRUTURA" in n and "PARTICIPAC" in n


def rendimentos(proventos: list[dict], ano: int, emissores: dict, mercado: dict,
                etfs: set[str]) -> dict:
    """Proventos do ano por ficha, somados por fonte pagadora (CNPJ).

    ``proventos``: dicts com data, ticker, nome, tipo ('dividend', 'jcp',
    'reit_income', 'amortization', 'other') e valor (JCP já líquido).

      dividend em ação ............ Isentos 09
      jcp ......................... Exclusiva 10
      qualquer um em FII/Fiagro ... Isentos 99
      provento de FIP-IE .......... Isentos 99 (alíquota zero)
      amortization ................ fora: devolve capital, reduz o custo
      o resto ..................... a_classificar

    "Rendimento" em ação (reit_income) é o rótulo do extrato da B3; pode ser
    dividendo, JCP ou reembolso de aluguel (BTC). O informe da empresa diz
    qual; adivinhar põe o valor na ficha errada.
    """
    grupos: dict[str, dict[tuple, dict]] = {
        "dividendos": {}, "jcp": {}, "fii": {}, "fip_ie": {}, "a_classificar": {}}
    fora: list[dict] = []
    for p in proventos:
        if str(p["data"])[:4] != f"{ano:04d}":
            continue
        t = _base(str(p["ticker"] or ""))
        tipo = str(p.get("tipo") or "").lower()
        valor = _d(p["valor"])
        if tipo == "amortization":
            fora.append({"ticker": t, "tipo": tipo, "valor": _c(valor)})
            continue
        cnpj, nome = cnpj_de(t, emissores, mercado)
        if _e_fii(t, etfs, emissores) and tipo in ("dividend", "reit_income", "other"):
            ficha = "fii"
        elif not _TICKER_B3.match(t) and _e_fip_ie(p.get("nome")):
            # Antes de jcp/dividend: o extrato pode rotular a distribuição do
            # FIP-IE como dividendo, e ela tem alíquota zero (não é lucro de S.A.)
            ficha = "fip_ie"
            # "4606422UNA - PATRIA INFRAESTRUTURA ..." -> sem o código interno
            nome = _limpar(re.sub(r"^\S*\d\S*\s*\W\s*", "", str(p.get("nome"))))
        elif tipo == "jcp":
            ficha = "jcp"
        elif tipo == "dividend":
            ficha = "dividendos"
        else:
            ficha = "a_classificar"
        chave = (t, tipo) if ficha == "a_classificar" else (cnpj or nome or f"?{t}",)
        g = grupos[ficha].setdefault(chave, {"cnpj": cnpj, "nome": nome, "tickers": set(),
                                             "tipo": tipo, "valor": _ZERO})
        g["tickers"].add(t)
        g["valor"] += valor
    out: dict[str, Any] = {}
    for ficha, g in grupos.items():
        linhas = sorted(({**v, "tickers": sorted(v["tickers"]), "valor": _c(v["valor"])}
                         for v in g.values()), key=lambda x: -x["valor"])
        out[ficha] = {"linhas": linhas,
                      "total": _c(sum((x["valor"] for x in linhas), _ZERO))}
    out["fora"] = fora
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Bens e Direitos
# ─────────────────────────────────────────────────────────────────────────────

def _discriminacao_rv(ticker: str, tipo: str, qtd: Decimal, nome: str | None) -> str:
    n = _limpar(nome)
    q = _fmt_qtd(qtd)
    if tipo == "acao":
        return f"ACOES DA EMPRESA {ticker}{' - ' + n if n else ''}, COM O TOTAL DE {q} ACOES"
    if tipo in ("fii", "fiagro"):
        return f"{q} COTAS DO FUNDO {n or ticker} ({ticker})"
    if tipo == "etf":
        return f"{q} COTAS DO FUNDO DE INDICE {n or ticker} ({ticker})"
    if tipo == "bdr":
        return f"{q} BDR {ticker}{' - ' + n if n else ''}"
    return f"{q} {ticker}{' - ' + n if n else ''}"


def _tipo_fundo(ticker: str, tipo: str, nomes_fii: dict) -> str:
    if tipo == "fii" and "FIAGRO" in _sem_acento(nomes_fii.get(ticker, "")).upper():
        return "fiagro"
    return tipo


def bens_renda_variavel(fotos: dict, ano: int, emissores: dict, mercado: dict,
                        nomes_fii: dict | None = None) -> list[dict]:
    """Itens de Bens e Direitos das ações, FII, ETF e BDR, a custo médio.

    ``fotos``: saída de ``apurar(..., cortes=(31/12 de ano-1, 31/12 de ano))``.
    Ativo vendido no ano entra com 31/12 do ano zerado: o programa exige que
    o item continue, com a situação final em branco.
    """
    nomes_fii = nomes_fii or {}
    antes = fotos.get(f"{ano - 1:04d}-12-31") or {}
    depois = fotos.get(f"{ano:04d}-12-31") or {}
    itens = []
    for t in sorted(set(antes) | set(depois)):
        a, d = antes.get(t), depois.get(t)
        ref = d or a
        tipo = _tipo_fundo(t, ref["tipo"], nomes_fii)
        if tipo not in BENS:
            continue
        cnpj, nome = cnpj_de(t, emissores, mercado)
        itens.append({
            **BENS[tipo],
            "tipo": tipo,
            "ticker": t,
            "discriminacao": _discriminacao_rv(t, tipo, (d or a)["qtd"], nome),
            "cnpj": cnpj,
            "nome": nome,
            "qtd_anterior": a["qtd"] if a else _ZERO,
            "qtd_atual": d["qtd"] if d else _ZERO,
            "situacao_anterior": _c(a["custo"] if a else _ZERO),
            "situacao_atual": _c(d["custo"] if d else _ZERO),
            "codigo_negociacao": t,
            "negociado_em_bolsa": True,
            "sem_custo": bool((a and a["sem_custo"]) or (d and d["sem_custo"])),
        })
    return itens


def _discriminacao_rf(ticker: str, nome: str) -> str:
    t = (ticker or "").upper().strip()
    for rx, modelo in _TESOURO:
        m = rx.match(t)
        if m:
            return modelo.format(m.group(1))
    return f"{_limpar(nome).replace(' - ', ' ')} {t}".strip()


# Desempate entre origens da posição: a mesma ordem do CASE de
# core.investimentos._SQL_POSICOES_SNAPSHOT (um teste prende as duas juntas).
PRIORIDADE_FONTE = {"tesouro_direto": 0, "b3_posicao": 1,
                    "b3_posicao_detalhada": 2, "xp_consolidado": 3,
                    "xp_positions": 4}


def _ultimo_ate(posicoes: list[dict], corte: str) -> list[dict]:
    """A posição em ``corte`` montada como a carteira a monta: a última data
    de CADA fonte até o corte (no mesmo ano) e, por ativo, a fonte da linha
    mais recente -- empatada a data, a de maior autoridade. Ficar só com a
    última data geral perde o ativo que a fonte mais nova não cobre (o
    Tesouro de 30/12 atrás de um consolidado de 31/12 só de ações)."""
    no_ano = [p for p in posicoes
              if str(p["data"])[:10] <= corte and str(p["data"])[:4] == corte[:4]]
    ultima: dict[Any, str] = {}
    for p in no_ano:
        f = p.get("fonte")
        ultima[f] = max(ultima.get(f, ""), str(p["data"])[:10])
    vigentes = [p for p in no_ano if str(p["data"])[:10] == ultima[p.get("fonte")]]

    def ativo(p: dict) -> str:
        return str(p.get("ativo") or p["ticker"])

    def ordem(p: dict) -> tuple:
        return (str(p["data"])[:10], -PRIORIDADE_FONTE.get(p.get("fonte"), 9))

    vence: dict[str, dict] = {}
    for p in vigentes:
        if ativo(p) not in vence or ordem(p) > ordem(vence[ativo(p)]):
            vence[ativo(p)] = p
    # Todas as linhas do ativo na fonte e data vencedoras (custódia em duas
    # contas da mesma origem soma).
    return [p for p in vigentes
            if (ordem(p), p.get("fonte")) == (ordem(vence[ativo(p)]), vence[ativo(p)].get("fonte"))]


def _custo_posicao(p: dict | None) -> Decimal | None:
    if p is None:
        return _c(_ZERO)
    return None if p.get("investido") is None else _c(p["investido"])


def bens_posicao_consolidada(posicoes: list[dict], ano: int, emissores: dict) -> list[dict]:
    """Renda fixa (Tesouro, CDB) e FIP a custo, da posição consolidada da
    corretora no último dia do ano (o investido; não o valor de mercado).

    ``posicoes``: dicts com data, ticker, nome, tipo ('fixed_income',
    'tesouro', 'fundo_rf'), quantidade, investido, instituicao e fonte.

    Investido que a corretora não informou fica ``None`` (a tela pede o
    custo do informe); zero seria declarar um bem sem custo.
    """
    antes = {p["ticker"]: p for p in _ultimo_ate(posicoes, f"{ano - 1:04d}-12-31")}
    depois = {p["ticker"]: p for p in _ultimo_ate(posicoes, f"{ano:04d}-12-31")}
    itens = []
    for t in sorted(set(antes) | set(depois)):
        a, d = antes.get(t), depois.get(t)
        ref = d or a
        nome = str(ref.get("nome") or "")
        n = _sem_acento(nome).upper()
        if ref["tipo"] == "fundo_rf":
            # Só o nome diz que é FIP; "fundo_rf" do consolidado também é
            # fundo de renda fixa, multimercado... -- código pelo informe.
            if "PARTICIPAC" in n:
                tipo = "fip_ie" if "INFRAESTRUTURA" in n else "fip"
            else:
                tipo = "fundo"
            cnpj, razao = None, None
            fundo = re.sub(r"^CFF\b\W*", "", _limpar(nome)).strip(" -")
            disc = f"{_fmt_qtd(ref['quantidade'])} COTAS DO {fundo}"
        else:
            tipo = "renda_fixa"
            if re.match(r"^T[A-Z]+\d{4}$", t.upper()):
                cnpj, razao = _cnpj_instituicao(ref.get("instituicao")), ref.get("instituicao")
            else:
                emissor = re.sub(r"^CDB\b\W*", "", _limpar(nome)).strip(" -")
                cnpj, razao = _cnpj_emissor_por_nome(emissor, emissores)
            disc = _discriminacao_rf(t, nome)
        itens.append({
            **BENS[tipo],
            "tipo": tipo,
            "ticker": t,
            "discriminacao": disc,
            "cnpj": cnpj,
            "nome": razao,
            "qtd_anterior": _d(a["quantidade"]) if a else _ZERO,
            "qtd_atual": _d(d["quantidade"]) if d else _ZERO,
            "situacao_anterior": _custo_posicao(a),
            "situacao_atual": _custo_posicao(d),
            "codigo_negociacao": None,
            "negociado_em_bolsa": False,
            "sem_custo": False,
        })
    return itens


def _custodia(posicoes_rv: list[dict], ano: int) -> dict[str, Decimal]:
    custodia: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for p in _ultimo_ate(posicoes_rv, f"{ano:04d}-12-31"):
        custodia[_base(p["ticker"])] += _d(p["quantidade"])
    return dict(custodia)


def conferir_custodia(bens_rv: list[dict], posicoes_rv: list[dict], ano: int) -> list[dict]:
    """Ativos em que a custódia da corretora em 31/12 tem MAIS cotas que a
    apuração: a diferença foi comprada fora do extrato e está sem custo.

    Muda ``bens_rv``: o item marcado ``sem_custo`` (já teve venda de cota
    anterior ao extrato) cuja custódia bate com a apuração deixa de ser --
    a cota antiga já saiu toda e o que está na foto tem custo (SAPR11:
    vendida inteira em 2023, recomprada em 2024).
    """
    custodia = _custodia(posicoes_rv, ano)
    for b in bens_rv:
        q = custodia.get(b["ticker"])
        if b["sem_custo"] and q is not None and abs(q - b["qtd_atual"]) <= Decimal("0.0001"):
            b["sem_custo"] = False
    apurado = {b["ticker"]: b["qtd_atual"] for b in bens_rv}
    ausentes = {t: q for t, q in apurado.items() if q > 0 and t not in custodia}
    out = []
    for t, q in sorted(custodia.items()):
        dif = q - apurado.get(t, _ZERO)
        if dif <= Decimal("0.0001"):
            continue
        # A sobra é exatamente um ativo que a custódia não tem: o mesmo papel
        # com código novo (MRFG3 -> MBRF3) sem o evento na Movimentação.
        troca = next((a for a, qa in sorted(ausentes.items())
                      if abs(qa - dif) <= Decimal("0.0001")), None)
        out.append({"ticker": t, "custodia": q, "apurado": apurado.get(t, _ZERO),
                    "troca_de_codigo": troca})
    return out


def apurado_sem_custodia(bens_rv: list[dict], posicoes_rv: list[dict], ano: int,
                         maior: list[dict]) -> list[dict]:
    """Posição que a apuração tem em 31/12 e a custódia da corretora não --
    venda ou transferência fora do extrato. Só com custódia no ano (uma foto
    de dezembro vazia não diz nada) e fora as trocas de código já achadas."""
    custodia = _custodia(posicoes_rv, ano)
    if not custodia:
        return []
    trocas = {m["troca_de_codigo"] for m in maior if m.get("troca_de_codigo")}
    return [{"ticker": b["ticker"], "apurado": b["qtd_atual"]}
            for b in bens_rv
            if b["qtd_atual"] > 0 and b["ticker"] not in custodia
            and b["ticker"] not in trocas]


# ─────────────────────────────────────────────────────────────────────────────
# Declaração
# ─────────────────────────────────────────────────────────────────────────────

def _marcar_amortizacao(bens: list[dict], proventos: list[dict], ano: int) -> list[dict]:
    """Põe em cada bem a amortização recebida até 31/12 do ano.

    Amortização devolve capital e reduz o custo, e nenhuma das duas fontes
    de custo daqui a abate com certeza: a apuração não a conhece, e o
    investido da corretora pode ou não estar líquido dela. O valor não é
    descontado (seria adivinhar desde quando o ativo é do investidor); vai
    para a tela conferir com o informe do administrador.
    """
    corte = f"{ano:04d}-12-31"
    amort: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for p in proventos:
        if str(p.get("tipo") or "").lower() == "amortization" and str(p["data"])[:10] <= corte:
            amort[_base(str(p["ticker"] or ""))] += _d(p["valor"])
    for b in bens:
        b["amortizacao"] = _c(amort[_base(b["ticker"])]) if _base(b["ticker"]) in amort else None
    return bens


def anos_disponiveis(apuracao: dict, proventos: list[dict],
                     posicoes: list[dict] | None = None) -> list[int]:
    """Anos com algo a declarar -- operação, provento, ativo na foto de 31/12
    ou posição de dezembro (quem só tem Tesouro/CDB também declara) --, a
    partir de ``ANO_MINIMO``."""
    anos = {int(m["mes"][:4]) for m in apuracao.get("meses") or []}
    anos |= {int(str(p["data"])[:4]) for p in proventos}
    anos |= {int(str(p["data"])[:4]) for p in posicoes or []}
    anos |= {int(c[:4]) for c in apuracao.get("fotos") or {}
             if (apuracao["fotos"][c])}
    return sorted((a for a in anos if a >= ANO_MINIMO), reverse=True)


def montar(apuracao: dict, ano: int, proventos: list[dict], posicoes: list[dict],
           emissores: dict, mercado: dict, etfs: set[str],
           nomes_fii: dict | None = None) -> dict:
    """Todas as fichas do ano-calendário ``ano``.

    ``apuracao`` precisa ter as fotos de 31/12 de ano-1 e de ano.
    """
    rv = ficha_renda_variavel(apuracao, ano)
    fii = ficha_fii(apuracao, ano)
    ano_ap = next((a for a in apuracao.get("anos") or [] if a["ano"] == ano), None)
    rend = rendimentos(proventos, ano, emissores, mercado, etfs)
    bens_rv = bens_renda_variavel(apuracao.get("fotos") or {}, ano, emissores, mercado,
                                  nomes_fii)
    rf = [p for p in posicoes if p["tipo"] in ("fixed_income", "tesouro", "fundo_rf")]
    rv_custodia = [p for p in posicoes if p["tipo"] in ("stock", "fii", "etf")]
    maior = conferir_custodia(bens_rv, rv_custodia, ano)
    return {
        "ano": ano,
        "exercicio": ano + 1,
        "renda_variavel": rv,
        "fii": fii,
        "outras_informacoes": outras_informacoes(rv, fii),
        "isentos": {
            "acoes_ate_20mil": {**ISENTO_ACOES,
                                "valor": _c(sum((m["ganho_isento"] for m in rv), _ZERO))},
            "dividendos": {**ISENTO_DIVIDENDOS, **rend["dividendos"]},
            "fii": {**ISENTO_FII, **rend["fii"]},
            "fip_ie": {**ISENTO_FIP_IE, **rend["fip_ie"]},
        },
        "exclusiva": {"jcp": {**EXCLUSIVA_JCP, **rend["jcp"]}},
        "pago_retido": {**PAGO_RETIDO_LEI,
                        "valor": _c(ano_ap["irrf_a_declarar"] if ano_ap else _ZERO)},
        "irrf_day_trade_restituir": _c((ano_ap or {}).get("irrf_day_trade_restituir", _ZERO)),
        "bens": _marcar_amortizacao(bens_rv + bens_posicao_consolidada(rf, ano, emissores),
                                    proventos, ano),
        "custodia_maior": maior,
        "custodia_menor": apurado_sem_custodia(bens_rv, rv_custodia, ano, maior),
        "proventos_a_classificar": rend["a_classificar"],
        "proventos_fora": rend["fora"],
        "meses_incompletos": (ano_ap or {}).get("meses_incompletos", []),
        "prejuizo_fim": {k: _c(v) for k, v in ((ano_ap or {}).get("prejuizo_fim") or {}).items()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Carregamento
# ─────────────────────────────────────────────────────────────────────────────

_SQL_PROVENTOS = """
    WITH dedup AS (
        SELECT d.payment_date, d.type, d.total_amount, a.ticker, a.name,
            ROW_NUMBER() OVER (
                PARTITION BY {base}, d.payment_date, d.type, ROUND(d.total_amount::numeric, 2)
                ORDER BY CASE
                    WHEN d.external_id LIKE 'b3mov-%' THEN 0
                    WHEN d.external_id LIKE 'xpcsl-%' THEN 1
                    ELSE 2
                END,
                d.id
            ) AS rn
        FROM dividends d
        JOIN assets a ON a.id = d.asset_id
        WHERE d.user_id = :uid
          AND d.payment_date IS NOT NULL
          AND d.payment_date <= CURRENT_DATE
          AND COALESCE(a.currency, 'BRL') = 'BRL'
    )
    SELECT payment_date::date, ticker, type, total_amount, name
    FROM dedup
    WHERE rn = 1
    ORDER BY payment_date
"""

_SQL_POSICOES = """
    SELECT s.report_date, a.ticker, s.asset_name, s.asset_type, s.quantity,
           s.invested_value, s.institution,
           -- mesma normalização de core.investimentos._SQL_POSICOES_SNAPSHOT
           CASE WHEN s.source_id LIKE 'td-snap-%' THEN 'tesouro_direto'
                ELSE s.source_table END AS fonte,
           s.asset_id
    FROM portfolio_position_snapshots s
    JOIN assets a ON a.id = s.asset_id
    WHERE s.user_id = :uid
      AND s.source_system = 'app4'
      AND EXTRACT(MONTH FROM s.report_date) = 12
      AND COALESCE(s.currency, 'BRL') = 'BRL'
      -- Aluguel tomado (BTC Tomador) é devido, não bem: somado, abateria a
      -- custódia do mesmo papel.
      AND NOT (COALESCE(s.is_loaned, FALSE) AND s.quantity < 0)
"""


def _consulta(conn, sql: str, params: dict | None = None) -> list | None:
    """Uma consulta num SAVEPOINT: a falha de uma não derruba as outras."""
    from sqlalchemy import text

    try:
        with conn.begin_nested():
            return conn.execute(text(sql), params or {}).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.info("irpf_declaracao: consulta falhou: %s", exc)
        return None


def carregar_insumos(engine, user_id: str) -> dict:
    """Operações, proventos, posição de dezembro e cadastros de CNPJ.

    Cada fonte que falha vai para ``falhas`` com o nome; a tela diz qual.
    """
    from core.investimentos import _SQL_TICKER_BASE
    from core.ir_renda_variavel import _tickers_etf, carregar_operacoes

    transacoes, eventos = carregar_operacoes(engine, user_id)
    falhas: list[str] = []
    with engine.connect() as conn:
        prov = _consulta(conn, _SQL_PROVENTOS.format(base=_SQL_TICKER_BASE), {"uid": user_id})
        pos = _consulta(conn, _SQL_POSICOES, {"uid": user_id})
        fiis = _consulta(conn, "SELECT UPPER(ticker), cnpj, name FROM market.fiis")
        empresas = _consulta(conn, """
            SELECT UPPER(ma.ticker), co.cnpj, co.name
            FROM market.assets ma JOIN market.companies co ON co.id = ma.company_id
            WHERE ma.exchange = 'B3' AND co.cnpj IS NOT NULL""")
        etfs = _tickers_etf(conn)
    for nome, r in (("proventos", prov), ("posição de dezembro", pos),
                    ("cadastro de FII", fiis), ("cadastro de empresas", empresas)):
        if r is None:
            falhas.append(nome)
    mercado: dict[str, tuple] = {}
    for t, cnpj, nome in (empresas or []) + (fiis or []):
        f = _formatar_cnpj(cnpj)
        if f:
            mercado[t] = (f, nome)
    return {
        "transacoes": transacoes,
        "eventos": eventos,
        "proventos": [{"data": r[0], "ticker": r[1], "tipo": r[2], "valor": r[3],
                       "nome": r[4]} for r in prov or []],
        "posicoes": [{"data": r[0], "ticker": r[1], "nome": r[2], "tipo": r[3],
                      "quantidade": r[4], "investido": r[5], "instituicao": r[6],
                      "fonte": r[7], "ativo": r[8]}
                     for r in pos or []],
        "mercado": mercado,
        "nomes_fii": {r[0]: r[2] or "" for r in fiis or []},
        "etfs": etfs,
        "emissores": ler_cnpj_emissores(),
        "falhas": falhas,
    }
