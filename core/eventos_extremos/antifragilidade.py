"""Índice de Antifragilidade da Carteira -- e o que ele é proibido de esconder.

A especificação foi explícita: o índice precisa **explicar seus componentes** e
**não esconder riscos dentro de uma nota única**. Isso não é um pedido de
formatação; é um requisito que muda o cálculo, e este módulo o trata assim.

Três defesas contra a nota única
--------------------------------
**Todo componente sai publicado, medido ou não.** :class:`Indice` devolve os
doze com valor, medição bruta, evidência textual e peso. Quem só quiser o número
tem o número; quem quiser saber de onde ele veio não precisa recalcular nada.

**A cobertura viaja junto e a nota nunca a esconde.** Uma carteira com dois
componentes medidos em 0,9 não é "0,90 de antifragilidade": é 0,90 sobre 20% da
pergunta. Sem a cobertura ao lado, quem mede menos tira nota maior -- defeito
que este projeto já publicou e registrou.

**Componente crítico rebaixa o índice inteiro.** Média ponderada compensa: uma
carteira sem nenhuma liquidez pode sair com nota alta se estiver bem diversificada
em tudo o mais. Mas ficar sem caixa numa crise não é compensável por diversificação
-- é eliminatório. Por isso :data:`NOTA_CRITICA` aplica um teto ao índice, e o
componente que causou o teto sai nomeado em ``alertas``. Sem esse teto, o índice
seria exatamente o que a especificação proibiu: um lugar para esconder risco.

Uma medição que não é monotônica
--------------------------------
Exposição cambial é **banda**, não escada. Zero por cento em moeda estrangeira é
tão frágil quanto oitenta: o primeiro é viés doméstico total e o segundo é risco
cambial descoberto. Tratá-la como "menos é melhor" -- ou como "mais é melhor" --
daria nota máxima para uma das duas fragilidades. É o único componente aqui com
duas bordas, e por isso ele tem função própria.

Módulo puro: recebe quadro de posições e medições já calculadas; não abre banco,
não chama rede, não importa Streamlit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from core.eventos_extremos import EVENTOS_EXTREMOS_VERSAO
from core.global_portfolio import concentration as conc

logger = logging.getLogger(__name__)

# ── Vocabulário fechado dos doze componentes ──────────────────────────────────
C_LIQUIDEZ = "liquidez"
C_CONC_ATIVO = "concentracao_ativo"
C_CONC_SETOR = "concentracao_setor"
C_CONC_PAIS = "concentracao_pais"
C_CONC_MOEDA = "concentracao_moeda"
C_CORRELACAO = "correlacao_estresse"
C_CREDITO = "qualidade_credito"
C_CAMBIAL = "exposicao_cambial"
C_BRASIL = "dependencia_brasil"
C_PERDA = "capacidade_de_perda"
C_DEFENSIVOS = "ativos_defensivos"
C_BENEFICIARIOS = "beneficiarios_de_choque"

COMPONENTES: tuple[str, ...] = (
    C_LIQUIDEZ, C_CONC_ATIVO, C_CONC_SETOR, C_CONC_PAIS, C_CONC_MOEDA,
    C_CORRELACAO, C_CREDITO, C_CAMBIAL, C_BRASIL, C_PERDA, C_DEFENSIVOS,
    C_BENEFICIARIOS,
)

ROTULOS: dict[str, str] = {
    C_LIQUIDEZ: "Liquidez disponível",
    C_CONC_ATIVO: "Concentração por ativo",
    C_CONC_SETOR: "Concentração por setor",
    C_CONC_PAIS: "Concentração por país",
    C_CONC_MOEDA: "Concentração por moeda",
    C_CORRELACAO: "Correlação durante estresse",
    C_CREDITO: "Qualidade de crédito",
    C_CAMBIAL: "Exposição cambial",
    C_BRASIL: "Dependência do Brasil",
    C_PERDA: "Capacidade de suportar perdas",
    C_DEFENSIVOS: "Ativos defensivos",
    C_BENEFICIARIOS: "Ativos que se beneficiam de choques",
}

#: Cortes ``(bom, ruim)`` na unidade natural de cada componente. Nota 1,0 em
#: ``bom`` ou melhor; 0,0 em ``ruim`` ou pior; linear entre os dois. A direção
#: sai do par: quando ``bom > ruim``, mais é melhor.
CORTES: dict[str, tuple[float, float]] = {
    C_LIQUIDEZ: (0.20, 0.02),        # fração em caixa e equivalentes
    C_CONC_ATIVO: (0.08, 0.30),      # HHI por símbolo
    C_CONC_SETOR: (0.15, 0.45),      # HHI por setor
    C_CONC_PAIS: (0.30, 0.85),       # HHI por país
    C_CONC_MOEDA: (0.35, 0.90),      # HHI por moeda
    C_CORRELACAO: (0.30, 0.85),      # correlação média sob estresse
    C_CREDITO: (0.85, 0.40),         # fração da carteira em crédito bom
    C_BRASIL: (0.40, 0.95),          # fração dependente do Brasil
    C_PERDA: (0.10, 0.45),           # perda simulada no cenário aplicável
    C_DEFENSIVOS: (0.25, 0.02),      # peso com papel defensivo
    C_BENEFICIARIOS: (0.12, 0.0),    # peso que sobe no choque
}

#: A exposição cambial é banda, não escada: as duas bordas são frágeis.
CAMBIAL_IDEAL = (0.15, 0.45)
CAMBIAL_BORDA = (0.0, 0.85)

PESOS: dict[str, float] = {
    C_LIQUIDEZ: 1.4, C_CONC_ATIVO: 1.2, C_CONC_SETOR: 1.0, C_CONC_PAIS: 0.9,
    C_CONC_MOEDA: 0.9, C_CORRELACAO: 1.1, C_CREDITO: 0.9, C_CAMBIAL: 0.8,
    C_BRASIL: 0.9, C_PERDA: 1.3, C_DEFENSIVOS: 1.0, C_BENEFICIARIOS: 0.7,
}

#: Abaixo disto o componente é fragilidade eliminatória e limita o índice.
NOTA_CRITICA = 0.20

#: Teto imposto ao índice quando algum componente está abaixo do crítico.
TETO_COM_COMPONENTE_CRITICO = 0.45

#: Cobertura abaixo da qual o índice não é um índice, e sai ``None``.
COBERTURA_MINIMA = 0.40

#: Componentes que respondem "esta carteira aguenta o choque?".
#:
#: Cobertura global não basta como portão. As quatro concentrações, a exposição
#: cambial e a dependência do Brasil saem de graça do próprio quadro de posições
#: -- juntas passam de 40% do peso, e sozinhas publicariam "0,81 de
#: antifragilidade" para uma carteira cuja liquidez, perda simulada e correlação
#: sob estresse ninguém mediu. Seria "quem pergunta menos tira nota maior"
#: entrando por outra porta: quem não tem stress test tiraria nota melhor que
#: quem tem. Diversificação é condição necessária e não suficiente, e o índice
#: não pode dizer o contrário por omissão.
NUCLEO = (C_LIQUIDEZ, C_PERDA, C_CORRELACAO)

#: Quantos do núcleo precisam estar medidos para o índice ser publicável.
NUCLEO_MINIMO = 2

#: Papéis de :mod:`core.global_portfolio.roles` que sustentam cada componente.
PAPEIS_DEFENSIVOS = ("baixa_volatilidade", "renda")
PAPEIS_BENEFICIARIOS = ("hedge_cambial", "protecao_inflacao", "reserva_valor")

#: Classes de ativo tratadas como caixa quando a liquidez não vem declarada.
CLASSES_LIQUIDAS = frozenset({"caixa", "cash", "renda_fixa", "liquidez"})

#: Rótulos de país que significam Brasil.
BRASIL = frozenset({"BR", "BRA", "BRASIL", "BRAZIL"})
MOEDA_LOCAL = frozenset({"BRL", "R$"})


@dataclass(frozen=True)
class Parte:
    """Um dos doze componentes, com o número que o justificou."""

    chave: str
    rotulo: str
    nota: float | None
    peso: float
    bruto: float | None
    evidencia: str

    @property
    def medido(self) -> bool:
        return self.nota is not None

    @property
    def critico(self) -> bool:
        return self.nota is not None and self.nota < NOTA_CRITICA

    def descrever(self) -> str:
        nota = "não medido" if self.nota is None else f"{self.nota:.2f}"
        return f"{self.rotulo}: {nota} ({self.evidencia})"


@dataclass(frozen=True)
class Indice:
    """O índice e tudo o que ele seria capaz de esconder."""

    valor: float | None
    bruto: float | None
    cobertura: float
    partes: tuple[Parte, ...]
    alertas: tuple[str, ...]
    limitacoes: tuple[str, ...]
    teto_aplicado: bool = False
    versao: str = EVENTOS_EXTREMOS_VERSAO

    def parte(self, chave: str) -> Parte | None:
        for p in self.partes:
            if p.chave == chave:
                return p
        return None

    def nota_de(self, chave: str) -> float | None:
        p = self.parte(chave)
        return None if p is None else p.nota

    @property
    def nao_medidos(self) -> tuple[str, ...]:
        return tuple(p.chave for p in self.partes if not p.medido)

    @property
    def criticos(self) -> tuple[str, ...]:
        return tuple(p.chave for p in self.partes if p.critico)

    @property
    def piores(self) -> tuple[Parte, ...]:
        """Componentes medidos, do pior para o melhor -- a leitura honesta."""
        return tuple(sorted((p for p in self.partes if p.medido),
                            key=lambda p: (p.nota, p.chave)))

    def descrever(self) -> tuple[str, ...]:
        linhas = []
        if self.valor is None:
            linhas.append(f"índice não calculado (cobertura {self.cobertura:.0%}, "
                          f"mínimo {COBERTURA_MINIMA:.0%})")
        else:
            linhas.append(f"antifragilidade {self.valor:.2f} sobre "
                          f"{self.cobertura:.0%} da pergunta")
            if self.teto_aplicado:
                linhas.append(f"  teto de {TETO_COM_COMPONENTE_CRITICO:.2f} "
                              f"aplicado: média ponderada seria {self.bruto:.2f}")
        linhas.extend("  " + p.descrever() for p in self.partes)
        linhas.extend(f"alerta: {a}" for a in self.alertas)
        linhas.extend(f"limitação: {lim}" for lim in self.limitacoes)
        return tuple(linhas)


def _num(valor) -> float | None:
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _nota(valor, bom: float, ruim: float) -> float | None:
    """Mapeia a medição para 0..1 entre dois cortes declarados."""
    v = _num(valor)
    if v is None:
        return None
    if bom > ruim:                       # mais é melhor
        if v >= bom:
            return 1.0
        if v <= ruim:
            return 0.0
        return (v - ruim) / (bom - ruim)
    if v <= bom:                         # menos é melhor
        return 1.0
    if v >= ruim:
        return 0.0
    return (ruim - v) / (ruim - bom)


def _nota_em_banda(valor) -> float | None:
    """Nota da exposição cambial: as duas bordas são frágeis.

    Dentro de :data:`CAMBIAL_IDEAL` a nota é 1,0. Fora, cai linearmente até 0,0
    nas bordas de :data:`CAMBIAL_BORDA`. Tratar isto como monotônico daria nota
    máxima ou para o viés doméstico total ou para o risco cambial descoberto.
    """
    v = _num(valor)
    if v is None:
        return None
    piso, teto = CAMBIAL_IDEAL
    borda_baixa, borda_alta = CAMBIAL_BORDA
    if piso <= v <= teto:
        return 1.0
    if v < piso:
        if v <= borda_baixa:
            return 0.0
        return (v - borda_baixa) / (piso - borda_baixa)
    if v >= borda_alta:
        return 0.0
    return (borda_alta - v) / (borda_alta - teto)


def _peso_por_valor(posicoes: pd.DataFrame, coluna: str,
                    valores: frozenset[str]) -> float | None:
    """Fração do peso cujas linhas casam com ``valores`` em ``coluna``.

    ``None`` quando a coluna não existe ou está vazia -- e nunca 0,0. Uma
    carteira cujo país nunca foi preenchido não é uma carteira sem dependência
    do Brasil.
    """
    if coluna not in posicoes.columns or not posicoes[coluna].notna().any():
        return None
    pesos = pd.to_numeric(posicoes["weight_global"], errors="coerce").fillna(0.0)
    total = float(pesos.sum())
    if total <= 0:
        return None
    casa = posicoes[coluna].astype(str).str.strip().str.upper().isin(valores)
    return float(pesos[casa].sum()) / total


def _peso_por_papel(posicoes: pd.DataFrame, papeis, alvos) -> tuple[float, float] | None:
    """``(peso com o papel, cobertura)`` entre os ativos que puderam ser avaliados.

    O ativo cujos papéis-alvo saíram todos **indeterminados** fica fora do
    denominador em vez de contar como "não é defensivo". Contá-lo como zero
    puniria justamente a carteira com pior cobertura de dados -- é o defeito
    registrado em ``memoria: medicao-que-pune-a-evidencia``, e
    :mod:`core.global_portfolio.roles` já declara que papel indeterminado nunca
    é negado por omissão.
    """
    if not papeis:
        return None
    pesos = pd.to_numeric(posicoes["weight_global"], errors="coerce").fillna(0.0)
    por_simbolo = dict(zip(
        posicoes["symbol"].astype(str).str.strip().str.upper(), pesos))
    total = float(pesos.sum())
    if total <= 0:
        return None

    alvo = set(alvos)
    peso_avaliado = 0.0
    peso_com_papel = 0.0
    for p in papeis:
        simbolo = str(getattr(p, "symbol", "")).strip().upper()
        w = float(por_simbolo.get(simbolo, 0.0))
        if w <= 0:
            continue
        indeterminados = set(getattr(p, "indeterminados", ()) or ())
        if alvo <= indeterminados:
            continue                       # nada dizível sobre este ativo
        peso_avaliado += w
        if alvo & set(getattr(p, "papeis", ()) or ()):
            peso_com_papel += w

    if peso_avaliado <= 0:
        return None
    return peso_com_papel / peso_avaliado, peso_avaliado / total


def _hhi(posicoes: pd.DataFrame, dimensao: str) -> float | None:
    if dimensao not in posicoes.columns or not posicoes[dimensao].notna().any():
        return None
    try:
        tabela = conc.por_dimensao(posicoes, dimensao)
    except (KeyError, ValueError) as erro:
        logger.warning("HHI por %s não calculado: %s", dimensao, erro)
        return None
    if tabela is None or tabela.empty:
        return None
    pesos = pd.to_numeric(tabela["peso"], errors="coerce").fillna(0.0)
    total = float(pesos.sum())
    if total <= 0:
        return None
    # HHI só é comparável com os cortes se os pesos somarem 1. Quadro com pesos
    # em percentual ou em reais devolveria um índice de outra escala, sem erro.
    return conc.hhi(pesos / total)


def calcular(
    posicoes: pd.DataFrame,
    *,
    papeis=None,
    liquidez: float | None = None,
    correlacao_estresse: float | None = None,
    qualidade_credito: float | None = None,
    perda_simulada: float | None = None,
) -> Indice:
    """Calcula o índice publicando os doze componentes.

    Args:
        posicoes: quadro de ``global_portfolio.aggregate.montar_posicoes``.
        papeis: saída de ``global_portfolio.roles.classificar``, se houver.
        liquidez: fração em caixa. ``None`` tenta derivar de ``asset_class``.
        correlacao_estresse: correlação média sob estresse (``core.copulas`` ou
            :mod:`core.eventos_extremos.mercado`).
        qualidade_credito: fração da carteira em crédito de boa qualidade.
        perda_simulada: perda no pior cenário aplicável (``core.stress_tests``).

    Returns:
        Um :class:`Indice`. Componente sem fonte sai ``None`` e entra na
        cobertura; nenhum deles vira 0,0 por falta de dado.
    """
    limitacoes: list[str] = []
    brutos: dict[str, float | None] = {}
    evidencias: dict[str, str] = {}

    if posicoes is None or posicoes.empty:
        limitacoes.append("carteira sem posições: índice não calculado")
        partes = tuple(Parte(c, ROTULOS[c], None, PESOS[c], None, "não medido")
                       for c in COMPONENTES)
        return Indice(None, None, 0.0, partes, (), tuple(limitacoes))

    faltando = [c for c in ("symbol", "weight_global") if c not in posicoes.columns]
    if faltando:
        limitacoes.append(f"quadro de posições sem {', '.join(faltando)}: "
                          "índice não calculado")
        partes = tuple(Parte(c, ROTULOS[c], None, PESOS[c], None, "não medido")
                       for c in COMPONENTES)
        return Indice(None, None, 0.0, partes, (), tuple(limitacoes))

    # ── Liquidez ──────────────────────────────────────────────────────────────
    liq = _num(liquidez)
    if liq is None:
        liq = _peso_por_valor(posicoes, "asset_class", CLASSES_LIQUIDAS)
        if liq is None:
            limitacoes.append("liquidez não declarada e sem 'asset_class': "
                              "componente não medido")
        else:
            evidencias[C_LIQUIDEZ] = f"{liq:.1%} em classes líquidas (derivado)"
    if liq is not None and C_LIQUIDEZ not in evidencias:
        evidencias[C_LIQUIDEZ] = f"{liq:.1%} do patrimônio em caixa"
    brutos[C_LIQUIDEZ] = liq

    # ── Concentrações ─────────────────────────────────────────────────────────
    for chave, dimensao in ((C_CONC_ATIVO, "symbol"), (C_CONC_SETOR, "sector"),
                            (C_CONC_PAIS, "country"), (C_CONC_MOEDA, "currency")):
        h = _hhi(posicoes, dimensao)
        brutos[chave] = h
        if h is None:
            limitacoes.append(f"coluna '{dimensao}' ausente ou vazia: "
                              f"{ROTULOS[chave].lower()} não medida")
        else:
            efetivo = 1.0 / h if h > 0 else float("inf")
            evidencias[chave] = f"HHI {h:.3f} (equivale a {efetivo:.1f} posições)"

    # ── Medições que chegam de fora ───────────────────────────────────────────
    for chave, valor, texto in (
        (C_CORRELACAO, correlacao_estresse, "correlação média sob estresse"),
        (C_CREDITO, qualidade_credito, "da carteira em crédito de boa qualidade"),
        (C_PERDA, perda_simulada, "de perda no cenário simulado"),
    ):
        v = _num(valor)
        brutos[chave] = v
        if v is None:
            limitacoes.append(f"{ROTULOS[chave].lower()} sem fonte: não medida")
        elif chave == C_CORRELACAO:
            evidencias[chave] = f"{texto} {v:.2f}"
        else:
            evidencias[chave] = f"{v:.1%} {texto}"

    # ── Câmbio e dependência do Brasil ────────────────────────────────────────
    local = _peso_por_valor(posicoes, "currency", MOEDA_LOCAL)
    cambial = None if local is None else 1.0 - local
    brutos[C_CAMBIAL] = cambial
    if cambial is None:
        limitacoes.append("coluna 'currency' ausente ou vazia: "
                          "exposição cambial não medida")
    else:
        evidencias[C_CAMBIAL] = (
            f"{cambial:.1%} fora do real (banda ideal "
            f"{CAMBIAL_IDEAL[0]:.0%}-{CAMBIAL_IDEAL[1]:.0%})")

    brasil = _peso_por_valor(posicoes, "country", BRASIL)
    brutos[C_BRASIL] = brasil
    if brasil is None:
        limitacoes.append("coluna 'country' ausente ou vazia: "
                          "dependência do Brasil não medida")
    else:
        evidencias[C_BRASIL] = f"{brasil:.1%} do patrimônio depende do Brasil"

    # ── Papéis dos ativos ─────────────────────────────────────────────────────
    for chave, alvos in ((C_DEFENSIVOS, PAPEIS_DEFENSIVOS),
                         (C_BENEFICIARIOS, PAPEIS_BENEFICIARIOS)):
        medida = _peso_por_papel(posicoes, papeis, alvos)
        if medida is None:
            brutos[chave] = None
            limitacoes.append(f"papéis dos ativos indisponíveis: "
                              f"{ROTULOS[chave].lower()} não medidos")
        else:
            peso, cobertura_papel = medida
            brutos[chave] = peso
            evidencias[chave] = (f"{peso:.1%} dos ativos avaliáveis "
                                 f"(cobertura {cobertura_papel:.0%})")
            if cobertura_papel < 0.5:
                limitacoes.append(
                    f"{ROTULOS[chave].lower()}: apenas {cobertura_papel:.0%} do "
                    "patrimônio pôde ser avaliado")

    # ── Notas ─────────────────────────────────────────────────────────────────
    partes: list[Parte] = []
    for chave in COMPONENTES:
        bruto = brutos.get(chave)
        if chave == C_CAMBIAL:
            nota = _nota_em_banda(bruto)
        else:
            bom, ruim = CORTES[chave]
            nota = _nota(bruto, bom, ruim)
        partes.append(Parte(chave, ROTULOS[chave], nota, PESOS[chave], bruto,
                            evidencias.get(chave, "não medido")))

    peso_medido = sum(p.peso for p in partes if p.medido)
    peso_total = sum(p.peso for p in partes)
    cobertura = peso_medido / peso_total if peso_total > 0 else 0.0

    if cobertura < COBERTURA_MINIMA:
        limitacoes.append(
            f"cobertura {cobertura:.0%} abaixo do mínimo "
            f"{COBERTURA_MINIMA:.0%}: índice não é publicável como índice")
        return Indice(None, None, cobertura, tuple(partes), (), tuple(limitacoes))

    medidos = {p.chave for p in partes if p.medido}
    nucleo_medido = [c for c in NUCLEO if c in medidos]
    if len(nucleo_medido) < NUCLEO_MINIMO:
        faltam = ", ".join(ROTULOS[c].lower() for c in NUCLEO if c not in medidos)
        limitacoes.append(
            f"apenas {len(nucleo_medido)} de {len(NUCLEO)} componentes de "
            f"resistência a choque foram medidos (faltam: {faltam}): "
            "diversificação sozinha não responde antifragilidade")
        return Indice(None, None, cobertura, tuple(partes), (), tuple(limitacoes))

    bruto_indice = sum(p.nota * p.peso for p in partes if p.medido) / peso_medido

    # Componente crítico não é compensável por média: ficar sem caixa numa crise
    # não vira aceitável porque a carteira está bem diversificada.
    criticos = [p for p in partes if p.critico]
    teto = bool(criticos)
    valor = min(bruto_indice, TETO_COM_COMPONENTE_CRITICO) if teto else bruto_indice

    alertas = tuple(
        f"{p.rotulo} em {p.nota:.2f} ({p.evidencia}): fragilidade eliminatória, "
        "não compensável por diversificação"
        for p in sorted(criticos, key=lambda p: (p.nota, p.chave)))

    return Indice(valor, bruto_indice, cobertura, tuple(partes), alertas,
                  tuple(limitacoes), teto_aplicado=teto and valor < bruto_indice)
