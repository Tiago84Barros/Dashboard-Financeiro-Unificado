"""IPCA Pessoal: a inflação da cesta de consumo do usuário.

Conta pura, sem banco: recebe lançamentos e a série do IBGE e devolve um
``ResultadoIpcaPessoal``. Método em
``docs/superpowers/specs/2026-11-23-ipca-pessoal-design.md``.

Índice de Laspeyres com pesos fixos na janela de 12 meses:

    w_c       = gasto da categoria c na janela / gasto total da janela
    I_t       = Σ_c (w_c / Σ_{c com dado em t} w_c) · r_{c,t}
    Acum_12m  = Π_t (1 + I_t) − 1
    Contrib_c = Acum_12m · Σ_t parte_{c,t} / Σ_t I_t

``r_{c,t}`` vem dos itens recorrentes da categoria ("seus itens") quando eles
cobrem ≥30% do gasto dela; nos meses sem par válido, e nas demais categorias,
vem do IPCA do grupo equivalente ("estimativa oficial"), ou do Índice geral
("sem grupo definido").
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date

from core.categorias import normalizar
from core.controle import canonical_transaction_type
from core.ipca_grupos import (
    GRUPOS_SIDRA,
    INDICE_GERAL,
    SerieIpcaGrupos,
    grupo_da_categoria,
)

MESES_JANELA = 12
MIN_MESES_RECORRENTE = 3
COBERTURA_MINIMA = 0.30
VARIACAO_MAXIMA = 0.50
ATRASO_MAXIMO_DIAS = 60

# Normalizadas (core.categorias.normalizar). Financiamento é dívida, não
# consumo; imposto e taxa o IPCA também não mede.
CATEGORIAS_FORA = frozenset({"financiamento", "impostos e taxas"})
# Pagamento de fatura: as quatro categorias em que o importador de extratos
# (core/bank_statement_import.py, lista de candidatas ~l.1123) arquiva o
# pagamento, normalizadas. Só vale fora da conta do cartão (ver filtrar_consumo),
# então compras dentro de um CSV de cartão nessas categorias seguem como consumo.
CATEGORIAS_PAGAMENTO_CARTAO = frozenset({
    "pagamento de cartao", "pagamento de fatura", "cartao de credito", "cartao"})
CARTAO_SEM_DETALHE = "Cartão (sem detalhe)"
SEM_CATEGORIA = "Sem categoria"
SEM_GRUPO_DEFINIDO = "Sem grupo definido"

ORIGEM_ITENS = "seus itens"
ORIGEM_OFICIAL = "estimativa oficial"
ORIGEM_SEM_GRUPO = "sem grupo definido"


@dataclass(frozen=True)
class Lancamento:
    data: date
    valor: float           # sinal do banco: saída < 0
    categoria: str
    descricao: str
    tipo: str              # transactions.type
    conta_tipo: str        # accounts.type ('' sem conta)
    fonte: str             # transactions.source ('manual', 'csv', ...)
    parcela_total: int = 1


@dataclass(frozen=True)
class PontoMensal:
    mes: date
    pessoal: float
    oficial: float
    acum_pessoal: float
    acum_oficial: float


@dataclass(frozen=True)
class CategoriaIpca:
    categoria: str
    grupo: str
    grupo_definido: bool
    peso: float
    variacao_12m: float
    origem: str
    meses_proprios: int
    cobertura: float
    contribuicao: float
    meses_fora: tuple[date, ...] = ()


@dataclass(frozen=True)
class Ignorado:
    item: str
    categoria: str
    mes: date
    motivo: str


@dataclass(frozen=True)
class ResultadoIpcaPessoal:
    ok: bool
    motivo: str = ""
    inicio: date | None = None
    fim: date | None = None
    acumulado_12m: float | None = None
    oficial_12m: float | None = None
    mensal: tuple[PontoMensal, ...] = ()
    categorias: tuple[CategoriaIpca, ...] = ()
    pesos_por_grupo: tuple[tuple[str, float, float | None], ...] = ()
    ignorados: tuple[Ignorado, ...] = ()
    avisos: tuple[str, ...] = ()


# ── utilitários de mês ───────────────────────────────────────────────────────

def somar_meses(d: date, n: int) -> date:
    """Dia 1 do mês ``n`` meses depois de ``d`` (``n`` pode ser negativo)."""
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _mes(d: date) -> date:
    return date(d.year, d.month, 1)


def _distancia_meses(a: date, b: date) -> int:
    """Meses de ``a`` até ``b``."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def _falha(motivo: str, avisos=()) -> ResultadoIpcaPessoal:
    return ResultadoIpcaPessoal(ok=False, motivo=motivo, avisos=tuple(avisos))


# ── formatação ───────────────────────────────────────────────────────────────

def pct_br(x: float, casas: int = 2) -> str:
    """0.10034 → '10,03%'."""
    return f"{x * 100:.{casas}f}".replace(".", ",") + "%"


def pp_br(x: float) -> str:
    """0.0142 → '+1,42 p.p.'."""
    return f"{x * 100:+.2f}".replace(".", ",") + " p.p."


# ── filtro e normalização ────────────────────────────────────────────────────

def normalizar_descricao(texto: str | None) -> str:
    """Chave do item recorrente: sem o que vem depois de ' | ', sem sufixo entre
    colchetes da importação antiga, sem acento/caixa e sem token só de dígitos."""
    base = str(texto or "").split(" | ", 1)[0]
    base = re.sub(r"\[[^\]]*\]", " ", base)
    base = normalizar(base)
    return " ".join(tok for tok in base.split() if any(ch.isalpha() for ch in tok))


def filtrar_consumo(lancamentos: list[Lancamento]) -> list[Lancamento]:
    """Só despesa de consumo, com ``valor`` positivo.

    Fora: entrada, transferência, investimento, Financiamento, Impostos e Taxas.
    Pagamento de fatura (fora da conta do cartão) entra como
    ``CARTAO_SEM_DETALHE`` só no mês em que a fatura não foi importada em CSV;
    no mês com CSV, as compras do cartão já estão lá, e contar o pagamento
    seria contar o mesmo gasto duas vezes.

    Premissa: um cartão só. A regra é por mês, não por cartão: com mais de um
    cartão, importar a fatura de um também descarta, nesse mês, o pagamento da
    fatura do outro (o gasto dele some). Está dito na explicação da tela.
    """
    meses_com_fatura_csv = {
        _mes(lanc.data) for lanc in lancamentos
        if lanc.conta_tipo == "credit_card" and lanc.fonte == "csv" and lanc.valor < 0
    }
    saida: list[Lancamento] = []
    for lanc in lancamentos:
        if lanc.valor >= 0:
            continue
        if canonical_transaction_type(lanc.tipo, lanc.categoria, lanc.valor) != "expense":
            continue
        normalizada = normalizar(lanc.categoria)
        if normalizada in CATEGORIAS_FORA:
            continue
        categoria = (lanc.categoria or "").strip() or SEM_CATEGORIA
        if normalizada in CATEGORIAS_PAGAMENTO_CARTAO and lanc.conta_tipo != "credit_card":
            if _mes(lanc.data) in meses_com_fatura_csv:
                continue
            categoria = CARTAO_SEM_DETALHE
        saida.append(replace(lanc, valor=-lanc.valor, categoria=categoria))
    return saida


# ── preço pelos itens do usuário ─────────────────────────────────────────────

def _variacao_por_itens(linhas: list[Lancamento], categoria: str, base: date,
                        janela: list[date]):
    """Variação mensal pelos itens recorrentes de uma categoria.

    Devolve ``(r, cobertura, ignorados)``: ``r[mes]`` só nos meses com ao menos
    um par válido; ``cobertura`` = gasto recorrente na janela / gasto da
    categoria na janela.
    """
    meses_validos = {base, *janela}
    janela_set = set(janela)
    por_item: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for lanc in linhas:
        if lanc.parcela_total > 1:
            continue
        item = normalizar_descricao(lanc.descricao)
        mes = _mes(lanc.data)
        if item and mes in meses_validos:
            por_item[item][mes] += lanc.valor
    recorrentes = {i: v for i, v in por_item.items() if len(v) >= MIN_MESES_RECORRENTE}

    gasto_categoria = sum(lanc.valor for lanc in linhas if _mes(lanc.data) in janela_set)
    gasto_recorrente = sum(v for valores in recorrentes.values()
                           for m, v in valores.items() if m in janela_set)
    cobertura = gasto_recorrente / gasto_categoria if gasto_categoria > 0 else 0.0

    r: dict[date, float] = {}
    ignorados: list[Ignorado] = []
    for t in janela:
        anterior = somar_meses(t, -1)
        soma_base = 0.0
        soma_ponderada = 0.0
        for item, valores in sorted(recorrentes.items()):
            if anterior not in valores or t not in valores or valores[anterior] <= 0:
                continue
            v = valores[t] / valores[anterior] - 1
            if abs(v) > VARIACAO_MAXIMA:
                ignorados.append(Ignorado(
                    item, categoria, t,
                    f"variação de {v:+.0%} entre {anterior:%m/%Y} e {t:%m/%Y}: "
                    "tratada como troca de produto ou erro de lançamento"))
                continue
            soma_base += valores[anterior]
            soma_ponderada += valores[anterior] * v
        if soma_base > 0:
            r[t] = soma_ponderada / soma_base
    return r, cobertura, ignorados


# ── a conta ──────────────────────────────────────────────────────────────────

def calcular_ipca_pessoal(lancamentos: list[Lancamento], serie: SerieIpcaGrupos,
                          ano: int, mes: int) -> ResultadoIpcaPessoal:
    avisos: list[str] = []
    ultimo = serie.ultimo_mes()
    if ultimo is None:
        return _falha("A série do IPCA por grupo do IBGE (SIDRA, tabela 7060) não "
                      "está disponível: o arquivo publicado está ausente ou ilegível.")

    pedido = date(ano, mes, 1)
    fim = min(pedido, ultimo)
    if fim < pedido:
        avisos.append(f"O IBGE publicou o IPCA até {ultimo:%m/%Y}; a janela termina "
                      f"nesse mês, e não em {pedido:%m/%Y}.")
        if (pedido - ultimo).days > ATRASO_MAXIMO_DIAS:
            avisos.append(f"A série do IBGE está com mais de {ATRASO_MAXIMO_DIAS} dias "
                          "de atraso em relação ao mês pedido.")
    base = somar_meses(fim, -MESES_JANELA)
    janela = [somar_meses(base, k) for k in range(1, MESES_JANELA + 1)]
    janela_set = set(janela)

    consumo_ate_fim = [lanc for lanc in filtrar_consumo(lancamentos) if _mes(lanc.data) <= fim]
    primeiro = min((_mes(lanc.data) for lanc in consumo_ate_fim), default=None)
    if primeiro is None or primeiro > base:
        ha = 0 if primeiro is None else _distancia_meses(primeiro, fim) + 1
        falta = MESES_JANELA + 1 - ha
        return _falha(
            f"São precisos {MESES_JANELA + 1} meses de gastos ({base:%m/%Y} a "
            f"{fim:%m/%Y}); há {ha} meses e falta{'m' if falta > 1 else ''} {falta}.",
            avisos)
    consumo = [lanc for lanc in consumo_ate_fim if _mes(lanc.data) >= base]
    com_gasto = {_mes(lanc.data) for lanc in consumo}
    vazios = [m for m in (base, *janela) if m not in com_gasto]
    if vazios:
        avisos.append(
            f"Sem gastos registrados em {' e '.join(f'{m:%m/%Y}' for m in vazios)}"
            if len(vazios) <= 2 else
            f"Sem gastos registrados em {', '.join(f'{m:%m/%Y}' for m in vazios)}"
        )
        avisos[-1] += ": o peso e a variação usam só os outros meses (lançamentos ausentes?)."

    gasto: dict[str, float] = defaultdict(float)
    for lanc in consumo:
        if _mes(lanc.data) in janela_set:
            gasto[lanc.categoria] += lanc.valor
    total = sum(gasto.values())
    if total <= 0:
        return _falha(f"Não há gasto de consumo entre {janela[0]:%m/%Y} e {fim:%m/%Y}.",
                      avisos)
    pesos = {c: v / total for c, v in sorted(gasto.items()) if v > 0}

    oficial: dict[date, float] = {}
    for t in janela:
        v = serie.variacao.get((t, INDICE_GERAL))
        if v is None:
            return _falha(f"A série do IBGE não tem o Índice geral de {t:%m/%Y}.", avisos)
        oficial[t] = v / 100

    linhas_por_categoria: dict[str, list[Lancamento]] = defaultdict(list)
    for lanc in consumo:
        linhas_por_categoria[lanc.categoria].append(lanc)

    r_cat: dict[str, dict[date, float]] = {}
    info: dict[str, tuple] = {}
    ignorados: list[Ignorado] = []
    for cat in pesos:
        grupo, definido = grupo_da_categoria(cat)
        if cat == CARTAO_SEM_DETALHE:
            r_itens, cobertura, ign = {}, 0.0, []
        else:
            r_itens, cobertura, ign = _variacao_por_itens(
                linhas_por_categoria[cat], cat, base, janela)
        usa_itens = cobertura >= COBERTURA_MINIMA and bool(r_itens)
        r: dict[date, float] = {}
        fora: list[date] = []
        proprios = 0
        for t in janela:
            oficial_grupo = serie.variacao.get((t, grupo))
            if usa_itens and t in r_itens:
                r[t] = r_itens[t]
                proprios += 1
            elif oficial_grupo is not None:
                r[t] = oficial_grupo / 100
            else:
                fora.append(t)
        if usa_itens:
            origem = ORIGEM_ITENS
            ignorados.extend(ign)
        else:
            origem = ORIGEM_OFICIAL if definido else ORIGEM_SEM_GRUPO
        r_cat[cat] = r
        info[cat] = (grupo, definido, origem, proprios, cobertura, tuple(fora))

    mensal_pessoal: dict[date, float] = {}
    soma_partes: dict[str, float] = defaultdict(float)
    for t in janela:
        disponiveis = [c for c in pesos if t in r_cat[c]]
        soma_w = sum(pesos[c] for c in disponiveis)
        if soma_w <= 0:
            return _falha(f"Nenhuma categoria tem variação de preço em {t:%m/%Y}.",
                          avisos)
        indice = 0.0
        for c in disponiveis:
            parte = pesos[c] / soma_w * r_cat[c][t]
            soma_partes[c] += parte
            indice += parte
        mensal_pessoal[t] = indice

    mensal: list[PontoMensal] = []
    fator_pessoal = fator_oficial = 1.0
    for t in janela:
        fator_pessoal *= 1 + mensal_pessoal[t]
        fator_oficial *= 1 + oficial[t]
        mensal.append(PontoMensal(t, mensal_pessoal[t], oficial[t],
                                  fator_pessoal - 1, fator_oficial - 1))
    acumulado = fator_pessoal - 1
    soma_indices = sum(mensal_pessoal.values())
    reparte = abs(soma_indices) >= 1e-12
    if not reparte:
        avisos.append("A soma das variações mensais é zero: a repartição do "
                      "acumulado por categoria é aproximada.")

    categorias: list[CategoriaIpca] = []
    for c in pesos:
        grupo, definido, origem, proprios, cobertura, fora = info[c]
        contribuicao = (acumulado * soma_partes[c] / soma_indices if reparte
                        else soma_partes[c])
        categorias.append(CategoriaIpca(
            categoria=c, grupo=grupo, grupo_definido=definido, peso=pesos[c],
            variacao_12m=math.prod(1 + x for x in r_cat[c].values()) - 1,
            origem=origem, meses_proprios=proprios, cobertura=cobertura,
            contribuicao=contribuicao, meses_fora=fora))
        if fora:
            avisos.append(
                f"{c}: sem variação de preço em "
                f"{', '.join(f'{m:%m/%Y}' for m in fora)}; nesses meses o índice foi "
                "calculado sem ela, com os pesos das outras categorias renormalizados.")
    categorias.sort(key=lambda x: (-x.contribuicao, x.categoria))

    sem_grupo = [c for c in categorias if not c.grupo_definido]
    peso_sem_grupo = sum(c.peso for c in sem_grupo)
    if sem_grupo:
        nomes = ", ".join(c.categoria for c in sorted(sem_grupo, key=lambda x: -x.peso))
        avisos.append(
            f"{len(sem_grupo)} categoria(s) sem grupo do IPCA definido "
            f"({pct_br(peso_sem_grupo, 1)} do gasto) usam o Índice geral quando não "
            f"há medida pelos seus itens: {nomes}.")

    peso_grupo: dict[str, float] = defaultdict(float)
    for c in categorias:
        if c.grupo_definido:
            peso_grupo[c.grupo] += c.peso
    pesos_por_grupo: list[tuple[str, float, float | None]] = []
    for g in GRUPOS_SIDRA.values():
        if g == INDICE_GERAL:
            continue
        ibge = serie.peso.get((fim, g))
        pesos_por_grupo.append((g, peso_grupo.get(g, 0.0),
                                None if ibge is None else ibge / 100))
    if sem_grupo:
        pesos_por_grupo.append((SEM_GRUPO_DEFINIDO, peso_sem_grupo, None))

    return ResultadoIpcaPessoal(
        ok=True, inicio=janela[0], fim=fim, acumulado_12m=acumulado,
        oficial_12m=fator_oficial - 1, mensal=tuple(mensal),
        categorias=tuple(categorias), pesos_por_grupo=tuple(pesos_por_grupo),
        ignorados=tuple(sorted(ignorados, key=lambda i: (i.mes, i.categoria, i.item))),
        avisos=tuple(avisos))


# ── contexto do chat ─────────────────────────────────────────────────────────

def linhas_contexto_ipca(res: ResultadoIpcaPessoal | None) -> list[str]:
    """Bloco do contexto do chat financeiro. ``None`` = a leitura falhou."""
    linhas = ["", "IPCA PESSOAL (inflação da sua cesta, 12 meses):"]
    if res is None:
        linhas.append("  (não calculado nesta conversa: a leitura dos lançamentos "
                      "falhou — não conclua nada sobre a inflação pessoal)")
        return linhas
    if not res.ok:
        linhas.append(f"  Não calculado: {res.motivo}")
        return linhas
    linhas.append(f"  Janela: {res.inicio:%m/%Y} a {res.fim:%m/%Y}")
    linhas.append(
        f"  IPCA Pessoal: {pct_br(res.acumulado_12m)}; IPCA oficial (IBGE, Índice "
        f"geral): {pct_br(res.oficial_12m)}; diferença: "
        f"{pp_br(res.acumulado_12m - res.oficial_12m)}")
    linhas.append("  Maiores contribuições:")
    for c in res.categorias[:3]:
        linhas.append(
            f"    - {c.categoria}: peso {pct_br(c.peso, 1)}, variação de preço "
            f"{pct_br(c.variacao_12m)}, contribuição {pp_br(c.contribuicao)}, "
            f"origem: {c.origem}")
    for aviso in res.avisos:
        linhas.append(f"  Aviso: {aviso}")
    linhas.append(f"  Origem '{ORIGEM_ITENS}' = preço medido nos lançamentos "
                  f"recorrentes do usuário; '{ORIGEM_OFICIAL}' = variação do grupo "
                  f"do IPCA; '{ORIGEM_SEM_GRUPO}' = Índice geral.")
    return linhas
