"""Atualidade trimestral da B3 medida por COBERTURA DO UNIVERSO, não por MAX(data).

Achado B3-02 da auditoria de 04/10/2026: ``MAX(year, quarter)`` de
``market.income_statements`` no armazém local dizia 2026T2, mas o 2026T2 tinha
1 empresa contra 397 no 2026T1. Uma única linha adiantada basta para o máximo
declarar a base em dia. O que importa é o trimestre que o UNIVERSO tem.

**Regra.** O trimestre vigente é o mais recente cuja contagem de empresas chega
a ``FRACAO_MINIMA`` do maior dos ``JANELA_REFERENCIA`` trimestres anteriores.
Calibração medida em 04/10/2026 nos dois bancos, de 2023T1 a 2026T2: trimestre
completo nunca ficou abaixo de 92,8% do maior dos quatro anteriores (389 de 419
no armazém, 392 de 421 no Supabase). O trimestre quebrado ficou em 0,24% (1 de
413). Com 80% sobra margem dos dois lados: entrega tardia de umas poucas
dezenas de empresas não derruba o trimestre, e uma ingestão que parou no meio
não o promove.

**Esperado.** O trimestre que o calendário da CVM já obriga a estar publicado.
O ITR (1T a 3T) vence 45 dias após o fim do trimestre, e a DFP (4T) vence três
meses após o fim do exercício. ``FOLGA_DIAS`` cobre o atraso entre o protocolo
na CVM e a chegada ao provedor (brapi). Sem folga, todo 15/05 amanheceria com
aviso.

**De quando é o score.** A métrica TTM não carrega o trimestre em que foi
calculada (``year=0, quarter=0`` na chave). Mas ``Margem_Liquida`` pelo método
``net_income/revenue`` é a razão entre as somas de quatro trimestres
consecutivos, e só uma janela reproduz o valor gravado. Validado no armazém
local em 04/10/2026, onde métrica e demonstração vêm da mesma base: 363 de 368
tickers casaram na janela que termina no último trimestre do próprio ticker. Os
outros 5 não casaram com janela nenhuma e ficam ``None``, sem palpite. No
Supabase, que o app lê, 344 dos 358 que casaram tinham o score calculado com
trimestres até o 2026T1, embora a vitrine já tivesse o 2026T2 de 392 empresas.

Módulo puro. Os leitores SQL recebem a conexão e não abrem engine própria.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from sqlalchemy import text

Trimestre = tuple[int, int]

FRACAO_MINIMA = 0.80
JANELA_REFERENCIA = 4
FOLGA_DIAS = 14
# Tolerância relativa ao casar a margem gravada com a janela recalculada. A
# métrica sai de float8 (15 dígitos) e as somas saem de numeric. Medido: as 363
# que casaram diferem em menos de 1e-9. Janelas vizinhas de uma mesma empresa
# diferem na 3ª casa (PETR4: 0,216898 contra 0,243861).
TOLERANCIA_MARGEM = 1e-6

# (mês, dia) do prazo do ITR. O 4T vai pela DFP, em 31/03 do ano seguinte.
_PRAZO_ITR = {1: (5, 15), 2: (8, 14), 3: (11, 14)}


def serial(t: Trimestre) -> int:
    return int(t[0]) * 4 + int(t[1]) - 1


def de_serial(s: int) -> Trimestre:
    return (s // 4, s % 4 + 1)


def rotulo(t: Trimestre | None) -> str:
    """Mesmo formato do dossiê (``2026T2``)."""
    return "—" if t is None else f"{int(t[0])}T{int(t[1])}"


def prazo_cvm(t: Trimestre) -> date:
    ano, tri = int(t[0]), int(t[1])
    if tri == 4:
        return date(ano + 1, 3, 31)
    mes, dia = _PRAZO_ITR[tri]
    return date(ano, mes, dia)


def trimestre_esperado(hoje: date) -> Trimestre:
    """Trimestre mais recente cujo prazo CVM, mais a folga, já passou."""
    s = serial((hoje.year, (hoje.month - 1) // 3 + 1))
    while prazo_cvm(de_serial(s)) + timedelta(days=FOLGA_DIAS) > hoje:
        s -= 1
    return de_serial(s)


def _referencia(contagens: Mapping[Trimestre, int], t: Trimestre) -> int:
    s = serial(t)
    return max((int(contagens.get(de_serial(s - k), 0) or 0)
                for k in range(1, JANELA_REFERENCIA + 1)), default=0)


def trimestre_vigente(contagens: Mapping[Trimestre, int]) -> Trimestre | None:
    """Trimestre mais recente com cobertura de pelo menos ``FRACAO_MINIMA``.

    Sem trimestre anterior para comparar (início do histórico), o trimestre
    vale por si: não há referência que o desminta.
    """
    for t in sorted((t for t, n in contagens.items() if (n or 0) > 0),
                    key=serial, reverse=True):
        ref = _referencia(contagens, t)
        if ref == 0 or contagens[t] >= FRACAO_MINIMA * ref:
            return t
    return None


def avaliar_universo(contagens: Mapping[Trimestre, int], hoje: date) -> dict:
    """Vigente, esperado e o trimestre parcial que o MAX(data) confundiria."""
    contagens = {(int(a), int(t)): int(n or 0) for (a, t), n in contagens.items()}
    esperado = trimestre_esperado(hoje)
    vigente = trimestre_vigente(contagens)
    out: dict = {"vigente": vigente, "esperado": esperado, "parcial": None,
                 "empresas_vigente": None, "cobertura_vigente": None,
                 "defasagem": None, "atrasada": None}
    if vigente is None:
        out["texto"] = "Sem demonstrações trimestrais no banco para medir a atualidade."
        return out
    ref = _referencia(contagens, vigente)
    out["empresas_vigente"] = contagens[vigente]
    out["cobertura_vigente"] = (contagens[vigente] / ref) if ref else None
    posteriores = [t for t, n in contagens.items()
                   if n > 0 and serial(t) > serial(vigente)]
    if posteriores:
        t = max(posteriores, key=serial)
        out["parcial"] = {"trimestre": t, "empresas": contagens[t],
                          "fracao": contagens[t] / max(contagens[vigente], 1)}
    out["defasagem"] = serial(esperado) - serial(vigente)
    out["atrasada"] = out["defasagem"] > 0

    base = (f"Demonstrações vigentes: {rotulo(vigente)} "
            f"({contagens[vigente]} empresas).")
    if out["atrasada"]:
        prazo = prazo_cvm(esperado).strftime("%d/%m/%Y")
        n_esp = contagens.get(esperado, 0)
        frac = n_esp / max(contagens[vigente], 1)
        out["texto"] = (
            f"{base} O calendário da CVM já exige o {rotulo(esperado)} "
            f"(prazo em {prazo}, mais {FOLGA_DIAS} dias de folga), e ele tem "
            f"{n_esp} empresa(s), {frac:.0%} do trimestre vigente. Números "
            f"trimestrais e TTM estão {out['defasagem']} trimestre(s) atrás.")
    else:
        out["texto"] = f"{base} Em dia com o calendário da CVM ({rotulo(esperado)})."
    return out


# ── De quando é o score: a janela que reproduz a margem TTM gravada ─────────

def base_ttm_da_margem(serie: Iterable[tuple], margem: float | None
                       ) -> Trimestre | None:
    """Último trimestre da janela de 4 que reproduz ``margem``, ou ``None``.

    ``serie``: ``(ano, tri, lucro_liquido, receita)`` em qualquer ordem.
    Procura da janela mais recente para a mais antiga e devolve a primeira que
    casa. Janela com lacuna, com valor nulo ou com receita somando zero não
    conta.
    """
    if margem is None:
        return None
    try:
        alvo = float(margem)
    except (TypeError, ValueError):
        return None
    linhas = sorted(((int(a), int(t), lu, rc) for a, t, lu, rc in serie),
                    key=lambda r: serial(r[:2]))
    for fim in range(len(linhas), 3, -1):
        janela = linhas[fim - 4:fim]
        if serial(janela[-1][:2]) - serial(janela[0][:2]) != 3:
            continue
        if any(r[2] is None or r[3] is None for r in janela):
            continue
        receita = sum(float(r[3]) for r in janela)
        if receita == 0:
            continue
        calc = sum(float(r[2]) for r in janela) / receita
        if abs(calc - alvo) <= TOLERANCIA_MARGEM * max(1.0, abs(alvo)):
            return janela[-1][:2]
    return None


def medir_base_do_score(series: Mapping[str, list[tuple]],
                        margens: Mapping[str, float]) -> dict[str, dict]:
    """``{ticker: {"base": trimestre|None, "ultimo": trimestre|None}}``."""
    out: dict[str, dict] = {}
    for tk, margem in margens.items():
        serie = series.get(tk) or []
        ultimo = max((r[:2] for r in serie), key=serial) if serie else None
        out[tk] = {"base": base_ttm_da_margem(serie, margem),
                   "ultimo": (int(ultimo[0]), int(ultimo[1])) if ultimo else None}
    return out


def resumo_base_do_score(bases: Mapping[str, dict]) -> dict:
    """Quantos scores usam trimestre anterior ao que o próprio banco já tem."""
    medidos = {tk: b for tk, b in bases.items()
               if b.get("base") is not None and b.get("ultimo") is not None}
    defasados = {tk: b for tk, b in medidos.items()
                 if serial(b["base"]) < serial(b["ultimo"])}
    comum = Counter(b["base"] for b in defasados.values()).most_common(1)
    return {"medidos": len(medidos), "defasados": len(defasados),
            "sem_casamento": len(bases) - len(medidos),
            "base_mais_comum": comum[0][0] if comum else None}


def avaliar_ticker(ultimo: Trimestre | None, base_score: Trimestre | None,
                   esperado: Trimestre, vigente_universo: Trimestre | None) -> dict:
    """Atualidade de UMA empresa: a demonstração e a base do score.

    Devolve campos estruturados (``usado``, ``atraso``) e as linhas de flag. As
    linhas não substituem os campos: dado que só existe como texto no contexto
    a LLM não cita (PRs #407, #412 a #416).
    """
    usado = base_score or ultimo
    out = {"ultimo_trimestre": ultimo, "base_score": base_score,
           "usado": usado, "esperado": esperado,
           "atraso": (serial(esperado) - serial(usado)) if usado else None,
           "flags": [], "aviso": None}
    if base_score is not None and ultimo is not None \
            and serial(base_score) < serial(ultimo):
        n = serial(ultimo) - serial(base_score)
        # `DADOS:` é defeito do NOSSO banco (core/severidade_flags.py): a
        # demonstração chegou e as métricas não foram recalculadas sobre ela.
        out["flags"].append(
            f"DADOS: o score e os múltiplos TTM foram calculados com trimestres "
            f"até o {rotulo(base_score)}, mas o banco já tem o {rotulo(ultimo)} "
            f"({n} trimestre(s) a mais). As métricas não foram recalculadas "
            "sobre a demonstração mais recente.")
    if ultimo is not None and serial(ultimo) < serial(esperado):
        if vigente_universo is not None and serial(vigente_universo) >= serial(esperado):
            causa = (f"o universo já tem o {rotulo(esperado)}: atraso da empresa "
                     "na entrega ou da ingestão deste ticker")
        else:
            causa = (f"a base inteira está atrás (vigente: "
                     f"{rotulo(vigente_universo)})")
        out["flags"].append(
            f"COBERTURA: a última demonstração trimestral no banco é do "
            f"{rotulo(ultimo)}, e o calendário da CVM já exige o "
            f"{rotulo(esperado)}. Causa: {causa}.")
    if out["atraso"] and out["atraso"] > 0:
        out["aviso"] = (f"Score calculado sobre demonstrações até o {rotulo(usado)}. "
                        f"O esperado para hoje é o {rotulo(esperado)} "
                        f"({out['atraso']} trimestre(s) de atraso).")
    return out


# ── Portão de publicação: não trocar base nova por base velha ───────────────

def bloqueio_de_publicacao(contagens_origem: Mapping[Trimestre, int],
                           contagens_destino: Mapping[Trimestre, int]) -> str | None:
    """Motivo para NÃO publicar métricas da origem no destino, ou ``None``.

    Em 28/09/2026 o alvo semanal ``b3_metrics`` republicou na vitrine o TTM do
    armazém local, calculado com demonstrações até o 2026T1, por cima de uma
    vitrine que já tinha o 2026T2 de 392 empresas. Medido em 04/10/2026: 344 dos
    358 scores mensuráveis passaram a usar um trimestre a menos do que o banco
    tinha. Publicar só faz sentido se a origem tiver pelo menos o trimestre
    vigente do destino.
    """
    origem = trimestre_vigente(contagens_origem)
    destino = trimestre_vigente(contagens_destino)
    if destino is None or (origem is not None and serial(origem) >= serial(destino)):
        return None
    return (f"a origem tem demonstrações vigentes até o {rotulo(origem)}, e a "
            f"vitrine já tem o {rotulo(destino)}. Publicar trocaria o TTM da "
            "vitrine por um calculado com trimestre a menos. Atualize as "
            "demonstrações da origem e rode o reprocess antes de publicar.")


# ── Leitores SQL (recebem a conexão; quem chama escolhe o banco) ────────────

def desde_ano(hoje: date) -> int:
    """Primeiro ano que cobre o esperado e os 4 trimestres de referência."""
    return hoje.year - 2


def ler_contagens(conn, desde: int) -> dict[Trimestre, int]:
    rows = conn.execute(text(
        "SELECT year, quarter, count(DISTINCT ticker) "
        "FROM market.income_statements "
        "WHERE period = 'quarterly' AND year >= :d AND quarter BETWEEN 1 AND 4 "
        "GROUP BY 1, 2"), {"d": int(desde)}).fetchall()
    return {(int(a), int(t)): int(n) for a, t, n in rows}


def ler_series_e_margens(conn, desde: int, ticker: str | None = None
                         ) -> tuple[dict[str, list[tuple]], dict[str, float]]:
    """Série trimestral (lucro, receita) e a margem TTM gravada por ticker."""
    filtro = " AND ticker = :t" if ticker else ""
    params: dict = {"d": int(desde)}
    if ticker:
        params["t"] = ticker
    series: dict[str, list[tuple]] = {}
    for tk, a, t, lu, rc in conn.execute(text(
            "SELECT ticker, year, quarter, net_income, revenue "
            "FROM market.income_statements "
            "WHERE period = 'quarterly' AND year >= :d AND quarter BETWEEN 1 AND 4"
            + filtro), params).fetchall():
        series.setdefault(str(tk), []).append((int(a), int(t), lu, rc))
    margens = {str(tk): float(v) for tk, v in conn.execute(text(
        "SELECT ticker, metric_value FROM market.calculated_metrics "
        "WHERE period = 'ttm' AND metric_name = 'Margem_Liquida' "
        "AND calculation_method = 'net_income/revenue' "
        "AND metric_value IS NOT NULL" + filtro), params).fetchall()}
    return series, margens


def medir_banco(conn, hoje: date) -> dict:
    """Universo e base do score de um banco, numa leitura só.

    Custo medido no Supabase em 04/10/2026: cerca de 4 mil linhas de
    (ticker, ano, tri, lucro, receita) mais 368 margens, na casa de 200 kB.
    Quem chama na tela guarda o resultado em cache por uma hora.
    """
    desde = desde_ano(hoje)
    universo = avaliar_universo(ler_contagens(conn, desde), hoje)
    series, margens = ler_series_e_margens(conn, desde)
    score = resumo_base_do_score(medir_base_do_score(series, margens))
    return {"universo": universo, "score": score}


def texto_do_score(score: Mapping) -> str | None:
    """Frase do aviso de base defasada, ou ``None`` se não há o que avisar."""
    if not score or not score.get("defasados"):
        return None
    return (f"{score['defasados']} de {score['medidos']} scores mensuráveis usam "
            f"o TTM calculado até o {rotulo(score.get('base_mais_comum'))} ou "
            "antes, embora o banco já tenha demonstração mais recente da mesma "
            "empresa. As métricas não foram recalculadas sobre ela.")
