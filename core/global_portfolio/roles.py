"""Papel estrategico de cada ativo no patrimonio, com a evidencia numerica.

O Portfolio Global ja responde "quanto pesa", "quao concentrado esta" e
"como se move junto". Este modulo responde uma pergunta diferente: para que
serve cada ativo — renda, crescimento, diversificacao, protecao contra
inflacao, reserva de valor, reducao de volatilidade, hedge cambial.

Um papel sem o numero que o justificou e um rotulo, nao uma classificacao.
Por isso toda atribuicao gera uma `Evidencia` com o valor do ativo e a
referencia contra a qual ele foi comparado; a `justificativa` final e
montada a partir dessas evidencias, nunca texto livre.

Ativo sem dado suficiente para julgar um papel entra em `indeterminados`,
nunca e simplesmente negado — "nao sabemos" e "nao cumpre" sao coisas
diferentes. Volatilidade e correlacao dependem de serie de precos mensal,
que hoje so existe para as classes `b3` e `fii` (ver
core.global_portfolio.returns); o restante do patrimonio (~38%, a classe
`us`) fica indeterminado nesses dois papeis ate a serie americana entrar.

Camada pura: sem SQL, sem Streamlit, sem I/O. Coberto por
tests/test_global_roles.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.global_portfolio.fields import valor as campo_valor

PAPEIS: tuple[str, ...] = (
    "baixa_volatilidade",
    "crescimento",
    "diversificacao",
    "hedge_cambial",
    "protecao_inflacao",
    "renda",
    "reserva_valor",
)

ROTULOS_PAPEL: dict[str, str] = {
    "baixa_volatilidade": "Redução de volatilidade",
    "crescimento": "Crescimento",
    "diversificacao": "Diversificação",
    "hedge_cambial": "Hedge cambial",
    "protecao_inflacao": "Proteção contra inflação",
    "renda": "Geração de renda",
    "reserva_valor": "Reserva de valor",
}

# Limiares heuristicos: escolhas de desenho para calibrar as regras, nao
# fatos sobre os ativos. Ajustaveis conforme a carteira real for observada
# contra o classificador (ver Step 5 da Task 2 do plano de Fase 3a).
LIMIARES: dict[str, float] = {
    # Desvio-padrao do payout / media do payout, nos ultimos anos
    # disponiveis. Acima disto o payout e tratado como erratico demais
    # para sustentar o papel de renda, mesmo com DY alto.
    "payout_instavel": 0.30,
    # CAGR minimo de LPA nos ultimos anos disponiveis para justificar
    # "crescimento" (8% ao ano).
    "cagr_minimo": 0.08,
    # Volatilidade anualizada abaixo da qual o ativo conta como estabilizador
    # da carteira (15% ao ano).
    "vol_baixa": 0.15,
    # Correlacao media com os demais ativos abaixo da qual o ativo
    # diversifica de fato.
    "correlacao_baixa": 0.30,
    # Participacao minima de recebiveis ("papel") na carteira do FII para
    # contar como exposicao indexada (tipicamente IPCA/CDI).
    "papel_dominante": 80.0,
    # Participacao minima de imoveis fisicos ("tijolo") na carteira do FII
    # para contar como reserva de valor real.
    "tijolo_dominante": 80.0,
    # Meses minimos de serie mensal (metricas_mensais) para julgar renda ou
    # crescimento de FII. Abaixo disto o papel fica indeterminado, nunca
    # negado — poucos meses nao sustentam nem "cumpre" nem "nao cumpre".
    "meses_minimos_fii": 12,
}

# Anos minimos de historico exigidos para cada regra baseada em serie anual.
_MIN_ANOS_PAYOUT = 3
_MIN_ANOS_CAGR = 2
_JANELA_ANOS = 5


@dataclass(frozen=True)
class Evidencia:
    """O numero que justifica um papel: o valor do ativo e a referencia usada."""

    papel: str
    valor: float
    referencia: float
    texto: str


@dataclass(frozen=True)
class PapelDoAtivo:
    """Papeis atribuidos a um ativo, com evidencia e o que ficou indeterminado."""

    symbol: str
    papeis: tuple[str, ...]
    evidencias: tuple[Evidencia, ...]
    indeterminados: tuple[str, ...]
    justificativa: str


def _asset_class(linha: dict) -> str:
    return str(linha.get("asset_class") or "").strip().lower()


def _serie_historica(payload: dict, bloco: str) -> list[dict]:
    """Registros do bloco de historico (anual ou mensal), ordenados do mais
    antigo ao mais recente.

    Quando os registros trazem "Data", a ordenacao usa a data (a origem no
    banco nao garante ordem); sem "Data" (como nos testes) preserva a ordem
    recebida, que ja e cronologica por convencao dos fixtures e dos
    adaptadores (core/portfolio/adapters/b3.py e fii.py gravam do jeito que a
    consulta devolve).
    """
    registros = ((payload or {}).get("history", {}) or {}).get(bloco) or []
    if not isinstance(registros, list):
        return []
    if all(isinstance(r, dict) and r.get("Data") for r in registros):
        return sorted(registros, key=lambda r: r["Data"])
    return list(registros)


def _valores_validos(registros: list[dict], chave: str) -> list[float]:
    """Todos os valores numericos validos de `chave`, do mais antigo ao mais novo, sem janela."""
    valores: list[float] = []
    for r in registros:
        bruto = (r or {}).get(chave)
        if bruto is None or isinstance(bruto, bool):
            continue
        try:
            valores.append(float(bruto))
        except (TypeError, ValueError):
            continue
    return valores


def _janela_recente(registros: list[dict], chave: str, janela: int = _JANELA_ANOS) -> list[float]:
    """Ultimos `janela` valores numericos validos de `chave`, do mais antigo ao mais novo."""
    return _valores_validos(registros, chave)[-janela:]


def _desvio_relativo(valores: list[float]) -> float | None:
    """Desvio-padrao / media (amostral). None quando a media nao sustenta a razao."""
    if len(valores) < _MIN_ANOS_PAYOUT:
        return None
    serie = pd.Series(valores, dtype=float)
    media = serie.mean()
    if media == 0 or pd.isna(media):
        return None
    desvio = serie.std(ddof=1)
    if pd.isna(desvio):
        return None
    return float(abs(desvio / media))


def _cagr(valores: list[float]) -> float | None:
    """CAGR entre o primeiro e o ultimo valor da janela. None se a base nao presta.

    Uma base ou um valor final <= 0 nao sustenta taxa composta (a raiz de um
    numero negativo/zero nao tem leitura economica); o papel fica
    indeterminado nesse caso, nunca "sem crescimento".
    """
    if len(valores) < _MIN_ANOS_CAGR:
        return None
    inicio, fim = valores[0], valores[-1]
    if inicio <= 0 or fim <= 0:
        return None
    anos = len(valores) - 1
    if anos <= 0:
        return None
    return float((fim / inicio) ** (1.0 / anos) - 1.0)


def _mediana_dy_por_classe(linhas: list[dict]) -> dict[str, float]:
    """Mediana do DY calculada dentro de cada asset_class.

    Comparar o DY de um FII com o de uma acao de crescimento nao mede nada:
    as classes tem perfis de distribuicao completamente diferentes. A
    mediana e o piso justo dentro do proprio grupo de comparacao.
    """
    por_classe: dict[str, list[float]] = {}
    for linha in linhas:
        classe = _asset_class(linha)
        dy = campo_valor(linha.get("payload"), classe, "dy")
        if dy is not None:
            por_classe.setdefault(classe, []).append(dy)
    return {
        classe: float(np.median(vs))
        for classe, vs in por_classe.items()
        if vs
    }


def _avaliar_renda(linha: dict, mediana_classe: float | None) -> tuple[bool | None, Evidencia | None]:
    classe = _asset_class(linha)
    payload = linha.get("payload") or {}
    dy = campo_valor(payload, classe, "dy")
    if dy is None or mediana_classe is None:
        return None, None

    if classe == "fii":
        return _avaliar_renda_fii(payload, dy, mediana_classe)

    payout_valores = _janela_recente(_serie_historica(payload, "multiplos_anuais"), "Payout")
    desvio_rel = _desvio_relativo(payout_valores)
    if desvio_rel is None:
        return None, None

    cumpre = dy >= mediana_classe and desvio_rel < LIMIARES["payout_instavel"]
    texto = (
        f"DY {dy:.2f}% (mediana da classe {mediana_classe:.2f}%), "
        f"payout com desvio relativo de {desvio_rel:.2f} "
        f"(limite {LIMIARES['payout_instavel']:.2f})"
    )
    evidencia = Evidencia("renda", dy, mediana_classe, texto) if cumpre else None
    return cumpre, evidencia


def _avaliar_renda_fii(payload: dict, dy: float, mediana_classe: float) -> tuple[bool | None, Evidencia | None]:
    """Renda de FII: a estabilidade e medida no DY mensal (DY_Patrimonial de
    metricas_mensais), nao no payout anual — FII nao tem serie de payout.
    Mesmo criterio em espirito ao das acoes (DY acima da mediana da classe e
    estavel); so a fonte da estabilidade muda. Menos de
    LIMIARES["meses_minimos_fii"] meses de serie deixa o papel indeterminado.
    """
    dy_mensal = _valores_validos(_serie_historica(payload, "metricas_mensais"), "DY_Patrimonial")
    meses = len(dy_mensal)
    if meses < LIMIARES["meses_minimos_fii"]:
        return None, None

    desvio_rel = _desvio_relativo(dy_mensal)
    if desvio_rel is None:
        return None, None

    cumpre = dy >= mediana_classe and desvio_rel < LIMIARES["payout_instavel"]
    texto = (
        f"DY {dy:.2f}% (mediana da classe {mediana_classe:.2f}%), "
        f"DY_Patrimonial mensal com desvio relativo de {desvio_rel:.2f} "
        f"(limite {LIMIARES['payout_instavel']:.2f}) em {meses} meses de serie"
    )
    evidencia = Evidencia("renda", dy, mediana_classe, texto) if cumpre else None
    return cumpre, evidencia


def _avaliar_crescimento(linha: dict) -> tuple[bool | None, Evidencia | None]:
    classe = _asset_class(linha)
    payload = linha.get("payload") or {}

    if classe == "fii":
        return _avaliar_crescimento_fii(payload)

    lpa_valores = _janela_recente(_serie_historica(payload, "demonstracoes_anuais"), "LPA")
    cagr = _cagr(lpa_valores)
    if cagr is None:
        return None, None

    cumpre = cagr >= LIMIARES["cagr_minimo"]
    texto = (
        f"CAGR de LPA em {len(lpa_valores) - 1} anos de {cagr * 100:.2f}% "
        f"(minimo {LIMIARES['cagr_minimo'] * 100:.2f}%)"
    )
    evidencia = Evidencia("crescimento", cagr, LIMIARES["cagr_minimo"], texto) if cumpre else None
    return cumpre, evidencia


def _avaliar_crescimento_fii(payload: dict) -> tuple[bool | None, Evidencia | None]:
    """Crescimento de FII: CAGR do VPA (metricas_mensais) sobre a janela
    disponivel, anualizado pelo numero real de meses — nao pelos 5 anos que a
    regra de acoes assume. A serie hoje cobre 2024-01 a 2026-05 (~29 meses,
    ~2,4 anos): a janela real entra no texto da evidencia para nao apresentar
    um CAGR de dois anos e meio como se fosse de cinco. Menos de
    LIMIARES["meses_minimos_fii"] meses deixa o papel indeterminado.
    """
    vpa_mensal = _valores_validos(_serie_historica(payload, "metricas_mensais"), "VPA")
    meses = len(vpa_mensal)
    if meses < LIMIARES["meses_minimos_fii"]:
        return None, None

    inicio, fim = vpa_mensal[0], vpa_mensal[-1]
    if inicio <= 0 or fim <= 0:
        return None, None
    intervalos = meses - 1
    if intervalos <= 0:
        return None, None
    cagr = float((fim / inicio) ** (12.0 / intervalos) - 1.0)

    cumpre = cagr >= LIMIARES["cagr_minimo"]
    texto = (
        f"CAGR de VPA em {intervalos} meses de {cagr * 100:.2f}% "
        f"(minimo {LIMIARES['cagr_minimo'] * 100:.2f}%)"
    )
    evidencia = Evidencia("crescimento", cagr, LIMIARES["cagr_minimo"], texto) if cumpre else None
    return cumpre, evidencia


def _percentual(bruto) -> float | None:
    """Normaliza um campo de composicao de FII para escala de 0 a 100.

    Verificado contra o banco de producao: pct_imoveis/pct_papel sao gravados
    como fracao (0 a 1, somando ~1,0 entre as quatro categorias), enquanto
    LIMIARES e os fixtures de teste usam pontos percentuais (0 a 100). Um
    valor dentro de [-1, 1] e tratado como fracao e escalado por 100 — a
    ambiguidade so existiria para uma fatia real abaixo de 1%, que de
    qualquer forma nunca alcancaria um limiar de dominancia de 80%.
    """
    if bruto is None:
        return None
    try:
        v = float(bruto)
    except (TypeError, ValueError):
        return None
    return v * 100.0 if -1.0 <= v <= 1.0 else v


def _avaliar_hedge_cambial(linha: dict) -> tuple[bool, Evidencia | None]:
    moeda = str(linha.get("currency") or "BRL").strip().upper()
    cumpre = moeda != "BRL"
    if not cumpre:
        return False, None
    texto = f"Moeda de referencia {moeda}, fora do BRL"
    return True, Evidencia("hedge_cambial", 1.0, 0.0, texto)


def _avaliar_protecao_inflacao(linha: dict) -> tuple[bool, Evidencia | None]:
    classe = _asset_class(linha)
    setor = str(linha.get("sector") or "").strip().lower()
    payload = linha.get("payload") or {}
    composicao = ((payload.get("classification") or {}).get("composition") or {})

    if setor in {"utilities", "real_estate"}:
        texto = f"Setor '{setor}' e classicamente indexado/protegido da inflacao"
        return True, Evidencia("protecao_inflacao", 1.0, 1.0, texto)

    if classe == "fii":
        pct_papel = _percentual(composicao.get("pct_papel"))
        if pct_papel is not None and pct_papel >= LIMIARES["papel_dominante"]:
            texto = (
                f"FII de papel com {pct_papel:.1f}% em recebiveis "
                f"(limite {LIMIARES['papel_dominante']:.1f}%), tipicamente indexados"
            )
            return True, Evidencia("protecao_inflacao", pct_papel, LIMIARES["papel_dominante"], texto)

    return False, None


def _avaliar_reserva_valor(linha: dict) -> tuple[bool, Evidencia | None]:
    classe = _asset_class(linha)
    if classe != "fii":
        return False, None

    payload = linha.get("payload") or {}
    composicao = ((payload.get("classification") or {}).get("composition") or {})
    pct_imoveis = _percentual(composicao.get("pct_imoveis"))
    if pct_imoveis is None:
        return False, None

    cumpre = pct_imoveis >= LIMIARES["tijolo_dominante"]
    if not cumpre:
        return False, None
    texto = (
        f"FII de tijolo com {pct_imoveis:.1f}% em imoveis "
        f"(limite {LIMIARES['tijolo_dominante']:.1f}%)"
    )
    return True, Evidencia("reserva_valor", pct_imoveis, LIMIARES["tijolo_dominante"], texto)


def _avaliar_baixa_volatilidade(symbol: str, retornos: pd.DataFrame | None) -> tuple[bool | None, Evidencia | None]:
    if retornos is None or not isinstance(retornos, pd.DataFrame) or symbol not in retornos.columns:
        return None, None
    serie = pd.to_numeric(retornos[symbol], errors="coerce").dropna()
    if serie.empty:
        return None, None
    vol_anual = float(serie.std(ddof=1) * math.sqrt(12))
    if pd.isna(vol_anual):
        return None, None

    cumpre = vol_anual < LIMIARES["vol_baixa"]
    texto = (
        f"Volatilidade anualizada de {vol_anual * 100:.2f}% "
        f"(limite {LIMIARES['vol_baixa'] * 100:.2f}%)"
    )
    evidencia = Evidencia("baixa_volatilidade", vol_anual, LIMIARES["vol_baixa"], texto) if cumpre else None
    return cumpre, evidencia


def _avaliar_diversificacao(symbol: str, correlacoes: dict[str, float] | None) -> tuple[bool | None, Evidencia | None]:
    if not correlacoes or symbol not in correlacoes:
        return None, None
    corr = float(correlacoes[symbol])
    cumpre = corr < LIMIARES["correlacao_baixa"]
    texto = (
        f"Correlacao media de {corr:.2f} com os demais ativos "
        f"(limite {LIMIARES['correlacao_baixa']:.2f})"
    )
    evidencia = Evidencia("diversificacao", corr, LIMIARES["correlacao_baixa"], texto) if cumpre else None
    return cumpre, evidencia


def _justificativa(papeis: tuple[str, ...], evidencias: tuple[Evidencia, ...]) -> str:
    if not papeis:
        return "Nenhum papel identificado: nenhuma regra teve evidencia suficiente para se cumprir."
    por_papel = {e.papel: e.texto for e in evidencias}
    partes = [f"{ROTULOS_PAPEL[p]} — {por_papel[p]}" for p in papeis if p in por_papel]
    return "; ".join(partes)


def classificar(df_posicoes: pd.DataFrame, *,
                retornos: pd.DataFrame | None = None,
                correlacoes: dict[str, float] | None = None) -> list[PapelDoAtivo]:
    """Classifica cada ativo do quadro de posicoes por papel estrategico.

    `retornos` e `correlacoes` sao opcionais porque `baixa_volatilidade` e
    `diversificacao` dependem de serie de precos mensal, que so cobre as
    classes `b3` e `fii` (core.global_portfolio.returns). Sem eles, os dois
    papeis ficam indeterminados para todo ativo — nunca negados por omissao.
    """
    if df_posicoes is None or df_posicoes.empty:
        return []

    linhas = df_posicoes.to_dict(orient="records")
    mediana_dy = _mediana_dy_por_classe(linhas)

    saida: list[PapelDoAtivo] = []
    for linha in linhas:
        symbol = str(linha["symbol"])
        classe = _asset_class(linha)

        papeis: list[str] = []
        evidencias: list[Evidencia] = []
        indeterminados: list[str] = []

        avaliacoes: list[tuple[str, bool | None, Evidencia | None]] = [
            ("renda", *_avaliar_renda(linha, mediana_dy.get(classe))),
            ("crescimento", *_avaliar_crescimento(linha)),
            ("hedge_cambial", *_avaliar_hedge_cambial(linha)),
            ("protecao_inflacao", *_avaliar_protecao_inflacao(linha)),
            ("reserva_valor", *_avaliar_reserva_valor(linha)),
            ("baixa_volatilidade", *_avaliar_baixa_volatilidade(symbol, retornos)),
            ("diversificacao", *_avaliar_diversificacao(symbol, correlacoes)),
        ]

        for papel, cumpre, evidencia in avaliacoes:
            if cumpre is None:
                indeterminados.append(papel)
            elif cumpre:
                papeis.append(papel)
                if evidencia is not None:
                    evidencias.append(evidencia)

        # PAPEIS ja esta ordenado (garantido por test_papeis_sao_deterministicos);
        # filtrar por essa ordem em vez da ordem de avaliacao mantem a saida
        # determinística e independente da ordem das tuplas acima.
        papeis_ordenados = tuple(p for p in PAPEIS if p in papeis)
        indeterminados_ordenados = tuple(p for p in PAPEIS if p in indeterminados)
        evidencias_ordenadas = tuple(
            e for p in PAPEIS for e in evidencias if e.papel == p
        )

        saida.append(PapelDoAtivo(
            symbol=symbol,
            papeis=papeis_ordenados,
            evidencias=evidencias_ordenadas,
            indeterminados=indeterminados_ordenados,
            justificativa=_justificativa(papeis_ordenados, evidencias_ordenadas),
        ))

    return saida
