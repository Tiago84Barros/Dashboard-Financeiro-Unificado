"""Sinais normalizados para o motor de movimentacao, com procedencia.

Cada `Sinal` e uma evidencia isolada — um numero, um sentido, o texto que o
justifica — mais o `analisador` que efetivamente o produziu. Essa procedencia
nao e decoracao: e o que a spec (secao 8) exige para o motor (Fase 3b, Task 4)
nao virar um painel bonito que ninguem consulta. `analisador` so pode valer um
dos modulos que de fato calculou o numero: `concentration`, `correlation`,
`factors`, `risk`, `roles`, `metrics` ou `targets`. Rotular um sinal com um
analisador que nao produziu o valor seria mentir sobre a origem — o oposto do
que este modulo existe para garantir.

Convencao de sinal, identica nos sete: `valor` vive em [-1, +1], negativo
empurra para reduzir a posicao, positivo empurra para aumentar. `direcao` e
so a leitura textual de `valor` ("reduzir"/"aumentar"/"neutro") — nunca uma
fonte de verdade paralela, para nao existir risco de um sinal dizer "reduzir"
com valor positivo por descuido de quem escreve o texto.

Normalizacao por percentil, quando usada, e SEMPRE dentro da classe do ativo
(b3/fii/us), nunca contra o mercado inteiro: as escalas de P/L, P/VP e score
de qualidade nao sao comparaveis entre classes (Fase 2a ja registrou isso ao
reportar qualidade por classe em metrics.py). Correlacao e a razao
contribuicao/peso, ao contrario, sao grandezas adimensionais e comparaveis
por natureza entre classes — por isso os sinais 3 e 4 nao percentilam por
classe, usam a grandeza bruta (clampada em [-1, +1]) diretamente.

Ativo sem o dado que um sinal precisa NAO gera sinal com valor 0.0 — zero e
uma posicao real na escala (neutro) e seria uma afirmacao fabricada sobre um
ativo que nao foi de fato medido. Mesmo principio de
`core.global_portfolio.roles.indeterminados` e de `risk.contribuicao_marginal`.

Funcoes puras: recebem os quadros (`df_posicoes`) e as saidas ja calculadas
dos analisadores (`contribuicoes`, `pares_redundantes`, `papeis`, `alvos`,
`confianca`). Sem SQL, sem Streamlit, sem I/O — quem chama ja calculou essas
saidas (varias delas custosas, como `risk.contribuicao_marginal` e
`roles.classificar`) para exibir na tela; este modulo nao recalcula.

Coberto por tests/test_global_signals.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.global_portfolio import concentration
from core.global_portfolio.correlation import LIMIAR_REDUNDANCIA
from core.global_portfolio.fields import valor as campo_valor
from core.global_portfolio.risk import ContribuicaoRisco
from core.global_portfolio.roles import PAPEIS, PapelDoAtivo

# Campo canonico de valuation por classe: P/L para b3 e us, P/VP para fii
# (fields.py nao define "pe" para fii nem "pvp" para us — cada classe cai
# no campo que ela de fato tem).
_CAMPO_VALUATION_POR_CLASSE: dict[str, str] = {"b3": "pe", "us": "pe", "fii": "pvp"}

# Onde mora a serie historica do MESMO campo de valuation, para o percentil
# contra o proprio historico do ativo. us nao entra: o snapshot americano
# (core/portfolio/adapters/us.py) so guarda "financials_anuais", sem
# multiplo historico — o componente historico fica ausente para us, nao
# fabricado a partir de outra coisa.
_HISTORICO_VALUATION: dict[str, tuple[str, str]] = {
    "b3": ("multiplos_anuais", "P/L"),
    "fii": ("metricas_mensais", "P/VP"),
}
_MIN_HIST_VALUATION = 3  # mesmo piso de amostra de core.global_portfolio.roles

# De-para do score de qualidade dentro de payload["metrics"], por classe.
# Mesma correspondencia de core.global_portfolio.metrics._SCORE_POR_CLASSE
# (duplicado aqui de proposito: 3 entradas estaveis, e este modulo nao
# importa simbolo privado de outro).
_SCORE_POR_CLASSE: dict[str, str] = {"b3": "score", "us": "entry_score", "fii": "score"}

_MIN_ATIVOS_PERCENTIL = 2  # com 1 ativo, todo percentil seria 1.0 por construcao

LIMITE_ATIVO_DEFAULT = 0.10
LIMITE_SETOR_DEFAULT = 0.30
LIMITE_PAIS_DEFAULT = 0.60


@dataclass(frozen=True)
class Sinal:
    """Uma evidencia normalizada, com o analisador que a produziu.

    `valor` em [-1, +1]; negativo empurra para reduzir. `direcao` e derivada
    de `valor` (nunca atribuida independentemente), so para leitura humana.
    """

    nome: str
    symbol: str
    valor: float
    direcao: str
    analisador: str
    texto: str


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, float(v)))


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _direcao(valor: float) -> str:
    if valor > 0:
        return "aumentar"
    if valor < 0:
        return "reduzir"
    return "neutro"


def _percentil_por_classe(valores: dict[str, float], classes: dict[str, str]) -> dict[str, float]:
    """Percentil (0 a 1) de cada valor dentro do proprio asset_class.

    Usa a posicao de plotagem centrada `(rank - 0.5) / n` em vez do
    `rank / n` cru de `Series.rank(pct=True)`: com `rank/n`, o menor valor
    de uma classe de 2 ativos cai em 0.5 (nao perto de 0), porque o rank
    minimo e 1, nao 0 — o sinal do pior ativo da classe ficaria artificialmente
    perto de neutro em vez de negativo. `(rank-0.5)/n` distribui os percentis
    simetricamente ao redor de 0.5 mesmo com poucos ativos.

    Classe com menos de `_MIN_ATIVOS_PERCENTIL` valores nao produz percentil:
    com um so valor, o percentil seria sempre 0.5 (rank 1, n 1) — sem
    comparacao nenhuma, e um artefato, nao informacao. Determinístico:
    agrupa e ordena por classe e simbolo antes de computar, nunca depende
    da ordem de iteracao dos dicts recebidos.
    """
    por_classe: dict[str, list[str]] = {}
    for symbol in sorted(valores):
        classe = classes.get(symbol)
        if classe is not None:
            por_classe.setdefault(classe, []).append(symbol)

    saida: dict[str, float] = {}
    for classe in sorted(por_classe):
        symbols = por_classe[classe]
        n = len(symbols)
        if n < _MIN_ATIVOS_PERCENTIL:
            continue
        serie = pd.Series({s: valores[s] for s in symbols}, dtype=float)
        rank = serie.rank(method="average")
        for s in symbols:
            saida[s] = float((rank[s] - 0.5) / n)
    return saida


def _serie_historica(payload: dict, bloco: str) -> list[dict]:
    registros = ((payload or {}).get("history", {}) or {}).get(bloco) or []
    return list(registros) if isinstance(registros, list) else []


def _valores_validos(registros: list[dict], chave: str) -> list[float]:
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


def _scores_qualidade_brutos(df_posicoes: pd.DataFrame) -> tuple[dict[str, float], dict[str, str]]:
    """Score de qualidade bruto (nao normalizado) por simbolo, mais a classe de cada um."""
    linhas = df_posicoes.to_dict(orient="records")
    scores: dict[str, float] = {}
    classes: dict[str, str] = {}
    for linha in linhas:
        symbol = str(linha["symbol"])
        classe = str(linha.get("asset_class") or "").strip().lower()
        chave = _SCORE_POR_CLASSE.get(classe)
        if not chave:
            continue
        bruto = (linha.get("payload") or {}).get("metrics", {}).get(chave)
        if bruto is None or isinstance(bruto, bool):
            continue
        try:
            v = float(bruto)
        except (TypeError, ValueError):
            continue
        scores[symbol] = v
        classes[symbol] = classe
    return scores, classes


# ---------------------------------------------------------------------------
# 1. Valuation — percentil na classe e contra o proprio historico do ativo
# ---------------------------------------------------------------------------

def sinais_valuation(df_posicoes: pd.DataFrame) -> list[Sinal]:
    """Valuation caro (percentil alto, na classe ou no proprio historico) empurra para reduzir."""
    if df_posicoes is None or df_posicoes.empty:
        return []
    linhas = df_posicoes.to_dict(orient="records")

    atuais: dict[str, float] = {}
    classes: dict[str, str] = {}
    campos: dict[str, str] = {}
    percentil_historico: dict[str, float] = {}

    for linha in linhas:
        symbol = str(linha["symbol"])
        classe = str(linha.get("asset_class") or "").strip().lower()
        campo = _CAMPO_VALUATION_POR_CLASSE.get(classe)
        if not campo:
            continue
        payload = linha.get("payload") or {}
        v = campo_valor(payload, classe, campo)
        # Multiplo nao positivo (prejuizo) nao tem leitura de "caro/barato".
        if v is None or v <= 0:
            continue
        atuais[symbol] = v
        classes[symbol] = classe
        campos[symbol] = campo

        origem = _HISTORICO_VALUATION.get(classe)
        if origem:
            bloco, chave = origem
            historico = [h for h in _valores_validos(_serie_historica(payload, bloco), chave) if h > 0]
            if len(historico) >= _MIN_HIST_VALUATION:
                n_menor_ou_igual = sum(1 for h in historico if h <= v)
                percentil_historico[symbol] = n_menor_ou_igual / len(historico)

    percentil_classe = _percentil_por_classe(atuais, classes)

    saida: list[Sinal] = []
    for symbol in sorted(atuais):
        componentes: list[float] = []
        partes_texto: list[str] = []
        if symbol in percentil_classe:
            p = percentil_classe[symbol]
            componentes.append(1.0 - 2.0 * p)
            partes_texto.append(f"percentil {p * 100:.0f} na classe {classes[symbol]}")
        if symbol in percentil_historico:
            p = percentil_historico[symbol]
            componentes.append(1.0 - 2.0 * p)
            partes_texto.append(f"percentil {p * 100:.0f} no proprio historico")
        if not componentes:
            continue
        valor_final = _clamp(sum(componentes) / len(componentes))
        texto = (f"{campos[symbol].upper()} atual {atuais[symbol]:.2f}: "
                 + "; ".join(partes_texto))
        saida.append(Sinal(nome="valuation", symbol=symbol, valor=valor_final,
                           direcao=_direcao(valor_final), analisador="metrics", texto=texto))
    return saida


# ---------------------------------------------------------------------------
# 2. Qualidade e data_confidence
# ---------------------------------------------------------------------------

def sinais_qualidade(df_posicoes: pd.DataFrame, *, confianca: dict[str, float] | None = None) -> list[Sinal]:
    """Score de qualidade no percentil da classe, ponderado pela confianca dos dados.

    `confianca` e o score 0-100 de `core.data_confidence.score_ticker` (ou
    equivalente), por simbolo — este modulo nao o calcula (exigiria consulta
    ao banco). Sem confianca informada para o ativo, o sinal de qualidade
    entra com peso pleno: a ausencia de uma medida de confianca nao e, por
    si so, evidencia de baixa confianca.
    """
    if df_posicoes is None or df_posicoes.empty:
        return []
    scores, classes = _scores_qualidade_brutos(df_posicoes)
    percentil = _percentil_por_classe(scores, classes)
    confianca = confianca or {}

    saida: list[Sinal] = []
    for symbol in sorted(percentil):
        p = percentil[symbol]
        valor_qualidade = 2.0 * p - 1.0  # percentil alto (boa qualidade) empurra para aumentar
        peso_confianca = 1.0
        nota_confianca = ""
        bruto_confianca = confianca.get(symbol)
        if bruto_confianca is not None:
            try:
                peso_confianca = _clamp01(float(bruto_confianca) / 100.0)
                nota_confianca = f"; confianca dos dados {float(bruto_confianca):.0f}/100 pondera o sinal"
            except (TypeError, ValueError):
                peso_confianca = 1.0
        valor_final = _clamp(valor_qualidade * peso_confianca)
        texto = (f"Score de qualidade no percentil {p * 100:.0f} da classe {classes[symbol]}"
                 + nota_confianca)
        saida.append(Sinal(nome="qualidade", symbol=symbol, valor=valor_final,
                           direcao=_direcao(valor_final), analisador="metrics", texto=texto))
    return saida


# ---------------------------------------------------------------------------
# 3. Contribuicao marginal ao risco versus peso
# ---------------------------------------------------------------------------

def sinais_risco(contribuicoes: dict[str, ContribuicaoRisco], df_posicoes: pd.DataFrame) -> list[Sinal]:
    """Razao contribuicao/peso alta (carrega mais risco do que a fatia sugere) empurra para reduzir.

    `contribuicoes` e a saida de `risk.contribuicao_marginal` (Task 1) — este
    modulo nao recalcula a covariancia, so normaliza e rotula o que ja veio
    pronto. Ativo com `razao is None` (peso zero: divisao por zero sem leitura,
    ver docstring de `ContribuicaoRisco`) ou ausente do dict (serie insuficiente)
    nao gera sinal.
    """
    if not contribuicoes or df_posicoes is None or df_posicoes.empty:
        return []

    saida: list[Sinal] = []
    for symbol in sorted(contribuicoes):
        c = contribuicoes[symbol]
        if c.razao is None:
            continue
        valor_final = _clamp(1.0 - c.razao)
        texto = (f"Contribuicao marginal ao risco {c.contribuicao * 100:.2f}% ao mes, "
                 f"razao contribuicao/peso {c.razao:.2f}")
        saida.append(Sinal(nome="risco_vs_peso", symbol=symbol, valor=valor_final,
                           direcao=_direcao(valor_final), analisador="risk", texto=texto))
    return saida


# ---------------------------------------------------------------------------
# 4. Redundancia — correlacao alta com par melhor classificado
# ---------------------------------------------------------------------------

def _perdedor_do_par(a: str, b: str, scores: dict[str, float], pesos: dict[str, float]) -> str:
    """Qual dos dois fica com o sinal de reduzir: o de pior qualidade; empatado
    ou sem score dos dois lados, o de menor peso (reduzir a posicao menor
    perturba menos o portfolio); empatado ainda, o de simbolo menor
    (desempate final deterministico, independente de ordem de dict/set)."""
    sa, sb = scores.get(a), scores.get(b)
    if sa is not None and sb is not None and sa != sb:
        return a if sa < sb else b
    pa, pb = pesos.get(a, 0.0), pesos.get(b, 0.0)
    if pa != pb:
        return a if pa < pb else b
    return min(a, b)


def sinais_redundancia(pares: list[tuple[str, str, float]], df_posicoes: pd.DataFrame,
                       *, limiar: float = LIMIAR_REDUNDANCIA) -> list[Sinal]:
    """Do par redundante, o pior classificado (qualidade, ou peso em empate) empurra para reduzir.

    `pares` e a saida de `correlation.pares_redundantes` (ja filtrada acima do
    limiar) — este modulo nao recalcula a matriz de correlacao. Correlacao e
    grandeza adimensional entre -1 e 1 para qualquer classe, entao a
    intensidade do sinal usa o valor bruto (rescalado linearmente entre o
    limiar e 1.0), sem percentil por classe: nao ha o problema de escala que
    justifica percentilar valuation e qualidade.
    """
    if not pares or df_posicoes is None or df_posicoes.empty:
        return []

    linhas = df_posicoes.to_dict(orient="records")
    pesos = {str(l["symbol"]): float(l.get("weight_global") or 0.0) for l in linhas}
    scores, _classes = _scores_qualidade_brutos(df_posicoes)

    faixa = max(1.0 - limiar, 1e-9)
    pior_por_symbol: dict[str, tuple[float, str]] = {}

    for a, b, corr in pares:
        if corr < limiar:
            continue
        perdedor = _perdedor_do_par(a, b, scores, pesos)
        vencedor = b if perdedor == a else a
        intensidade = _clamp(-(corr - limiar) / faixa)
        criterio = ("menor score de qualidade" if perdedor in scores and vencedor in scores
                    else "menor peso na carteira")
        texto = (f"Correlacao de {corr:.2f} com {vencedor} (limiar {limiar:.2f}); "
                 f"{perdedor} e o redundante por ter {criterio} entre os dois")
        atual = pior_por_symbol.get(perdedor)
        if atual is None or intensidade < atual[0]:
            pior_por_symbol[perdedor] = (intensidade, texto)

    saida = [
        Sinal(nome="redundancia", symbol=symbol, valor=valor,
              direcao=_direcao(valor), analisador="correlation", texto=texto)
        for symbol, (valor, texto) in sorted(pior_por_symbol.items())
    ]
    return saida


# ---------------------------------------------------------------------------
# 5. Estouro de limite — por ativo, setor ou pais
# ---------------------------------------------------------------------------

def sinais_concentracao(df_posicoes: pd.DataFrame, *,
                        limite_ativo: float = LIMITE_ATIVO_DEFAULT,
                        limite_setor: float = LIMITE_SETOR_DEFAULT,
                        limite_pais: float = LIMITE_PAIS_DEFAULT) -> list[Sinal]:
    """Peso acima do limite (ativo, setor ou pais) empurra para reduzir.

    Os limiares sao escolhas de desenho, nao fatos sobre o ativo — mesma
    ressalva que `core.global_portfolio.roles.LIMIARES` registra; ficam
    como parametros explicitos para a interface poder exibi-los e ajusta-los
    (Task 6). Usa `concentration.por_dimensao` para o peso agregado por setor
    e por pais — nao reimplementa o agrupamento.

    So gera sinal para o ativo que de fato estoura algum limite: estar dentro
    do limite nao e "sinal fraco de aumentar", e ausencia de evidencia de
    estouro — sinal fabricado a mais inflaria a contagem sem nenhuma leitura
    nova.
    """
    if df_posicoes is None or df_posicoes.empty:
        return []

    linhas = df_posicoes.to_dict(orient="records")
    peso_ativo = {str(l["symbol"]): float(l.get("weight_global") or 0.0) for l in linhas}
    setor_por_symbol = {str(l["symbol"]): l.get("sector") for l in linhas}
    pais_por_symbol = {str(l["symbol"]): l.get("country") for l in linhas}

    por_setor = concentration.por_dimensao(df_posicoes, "sector")
    peso_setor = dict(zip(por_setor["sector"], por_setor["peso"])) if not por_setor.empty else {}
    por_pais = concentration.por_dimensao(df_posicoes, "country")
    peso_pais = dict(zip(por_pais["country"], por_pais["peso"])) if not por_pais.empty else {}

    saida: list[Sinal] = []
    for symbol in sorted(peso_ativo):
        candidatos: list[tuple[float, str]] = []

        pa = peso_ativo[symbol]
        if limite_ativo > 0 and pa > limite_ativo:
            excesso = (pa - limite_ativo) / limite_ativo
            candidatos.append((_clamp(-excesso),
                               f"peso do ativo {pa * 100:.2f}% acima do limite {limite_ativo * 100:.2f}%"))

        setor = setor_por_symbol.get(symbol)
        ps = float(peso_setor.get(setor, 0.0) or 0.0) if setor is not None else 0.0
        if limite_setor > 0 and ps > limite_setor:
            excesso = (ps - limite_setor) / limite_setor
            candidatos.append((_clamp(-excesso),
                               f"setor '{setor}' com {ps * 100:.2f}% acima do limite {limite_setor * 100:.2f}%"))

        pais = pais_por_symbol.get(symbol)
        pp = float(peso_pais.get(pais, 0.0) or 0.0) if pais is not None else 0.0
        if limite_pais > 0 and pp > limite_pais:
            excesso = (pp - limite_pais) / limite_pais
            candidatos.append((_clamp(-excesso),
                               f"pais '{pais}' com {pp * 100:.2f}% acima do limite {limite_pais * 100:.2f}%"))

        if not candidatos:
            continue
        valor_final, texto = min(candidatos, key=lambda t: t[0])  # o estouro mais severo
        saida.append(Sinal(nome="estouro_limite", symbol=symbol, valor=valor_final,
                           direcao=_direcao(valor_final), analisador="concentration", texto=texto))
    return saida


# ---------------------------------------------------------------------------
# 6. Ausencia de papel estrategico
# ---------------------------------------------------------------------------

def sinais_papel_ausente(papeis: list[PapelDoAtivo]) -> list[Sinal]:
    """Ativo sem nenhum papel estrategico CONQUISTADO empurra para reduzir.

    `papeis` e a saida de `roles.classificar` — este modulo nao reclassifica.
    So gera sinal quando ao menos um papel foi de fato AVALIADO e nao
    cumprido: se todos os sete papeis estao indeterminados (sem dado para
    julgar nenhum, ex.: ativo sem serie de precos e sem historico contabil),
    "ausencia" nao e uma afirmacao sustentada — e teria o mesmo problema que
    `roles.py` evita ao separar `indeterminados` de "nao cumpre".
    """
    if not papeis:
        return []
    saida: list[Sinal] = []
    for p in sorted(papeis, key=lambda x: x.symbol):
        if p.papeis:
            continue
        if len(p.indeterminados) >= len(PAPEIS):
            continue  # nenhum papel foi avaliado de fato: sem evidencia, sem sinal
        texto = "Nenhum papel estrategico conquistado: " + p.justificativa
        saida.append(Sinal(nome="papel_ausente", symbol=p.symbol, valor=-1.0,
                           direcao="reduzir", analisador="roles", texto=texto))
    return saida


# ---------------------------------------------------------------------------
# 7. Desvio em relacao ao alvo da classe
# ---------------------------------------------------------------------------

def sinais_desvio_alvo(df_posicoes: pd.DataFrame, alvos: dict[str, float]) -> list[Sinal]:
    """Classe acima do alvo empurra os seus ativos para reduzir; abaixo, para aumentar.

    `alvos` e a alocacao-alvo ativa (`core.portfolio.repository.load_allocation_targets`),
    ja normalizada para somar 1. Classe sem alvo (>0) definido nao gera sinal
    para os ativos dessa classe — sem alvo nao ha "desvio" para medir.
    """
    if df_posicoes is None or df_posicoes.empty or not alvos:
        return []

    linhas = df_posicoes.to_dict(orient="records")
    peso_por_classe: dict[str, float] = {}
    classe_por_symbol: dict[str, str] = {}
    for l in linhas:
        symbol = str(l["symbol"])
        classe = str(l.get("asset_class") or "").strip().lower()
        peso = float(l.get("weight_global") or 0.0)
        peso_por_classe[classe] = peso_por_classe.get(classe, 0.0) + peso
        classe_por_symbol[symbol] = classe

    saida: list[Sinal] = []
    for symbol in sorted(classe_por_symbol):
        classe = classe_por_symbol[symbol]
        alvo = alvos.get(classe)
        if alvo is None or alvo <= 0:
            continue
        atual = peso_por_classe.get(classe, 0.0)
        desvio_relativo = (atual - alvo) / alvo
        valor_final = _clamp(-desvio_relativo)
        texto = (f"Classe '{classe}' com {atual * 100:.2f}% do patrimonio contra "
                 f"alvo de {alvo * 100:.2f}% (desvio relativo {desvio_relativo * 100:+.1f}%)")
        saida.append(Sinal(nome="desvio_alvo", symbol=symbol, valor=valor_final,
                           direcao=_direcao(valor_final), analisador="targets", texto=texto))
    return saida


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def gerar_sinais(df_posicoes: pd.DataFrame, *,
                 contribuicoes: dict[str, ContribuicaoRisco] | None = None,
                 pares_redundantes: list[tuple[str, str, float]] | None = None,
                 papeis: list[PapelDoAtivo] | None = None,
                 alvos: dict[str, float] | None = None,
                 confianca: dict[str, float] | None = None,
                 limite_ativo: float = LIMITE_ATIVO_DEFAULT,
                 limite_setor: float = LIMITE_SETOR_DEFAULT,
                 limite_pais: float = LIMITE_PAIS_DEFAULT) -> list[Sinal]:
    """Combina os sete sinais. Saidas de analisador ausentes (None) simplesmente
    nao contribuem sinais daquele tipo — o quadro de posicoes e o unico
    argumento obrigatorio.

    Ordenacao final por (nome, symbol): deterministica, independente da ordem
    de insercao ou de qualquer dict/set intermediario.
    """
    saida: list[Sinal] = []
    saida += sinais_valuation(df_posicoes)
    saida += sinais_qualidade(df_posicoes, confianca=confianca)
    if contribuicoes:
        saida += sinais_risco(contribuicoes, df_posicoes)
    if pares_redundantes:
        saida += sinais_redundancia(pares_redundantes, df_posicoes)
    saida += sinais_concentracao(df_posicoes, limite_ativo=limite_ativo,
                                 limite_setor=limite_setor, limite_pais=limite_pais)
    if papeis:
        saida += sinais_papel_ausente(papeis)
    if alvos:
        saida += sinais_desvio_alvo(df_posicoes, alvos)
    return sorted(saida, key=lambda s: (s.nome, s.symbol))
