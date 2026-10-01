"""Avaliação multicritério do ativo: qualidade, valuation, mercado e riscos.

Pedido do usuário (01/10/2026): "Coloque fundamentos e valuation na decisão
também... comparar com outras empresas, avaliar setores críticos, justificar
o argumento... não só do ponto de vista fundamentalista, mas também de
sentimento do mercado".

Lê só o que a análise já carrega (seções ``fundamentos``, ``valuation``,
``pares`` e ``noticias``) e devolve, por dimensão, uma leitura e os critérios
com número e referência que a sustentam. Puro: nenhuma consulta a banco.

Três decisões de método, porque já custaram caro neste projeto:

* **Alerta eliminatório elimina.** Fraude, recuperação judicial, patrimônio
  negativo, juros maiores que o resultado operacional, dividendo pago sem
  lucro nem caixa: qualquer um deles põe a qualidade em "frágil", e nenhuma
  quantidade de critério bom compensa (a média ponderada que compensa defeito
  eliminatório já foi um erro aqui).
* **Barato exige as duas réguas.** Um múltiplo só conta como barato (ou caro)
  quando o histórico do próprio ativo e os pares concordam; com uma régua só
  ele conta pela metade. Múltiplo abaixo do normal não é vantagem por si só
  (ordenar não é superar).
* **Setor muda a régua.** Banco e seguradora não têm dívida/EBITDA nem
  EV/EBIT (dívida é matéria-prima); elétricas e saneamento convivem com
  alavancagem maior, regulada; em commodity e siderurgia o P/L baixo costuma
  marcar o pico do ciclo, e não conta como barato.

O sentimento das notícias é o léxico do próprio app
(``core.noticias.sentimento``), não um modelo: serve de termômetro, e o texto
diz isso. Momento de preço não entra porque o app não publica série de preço
por ativo para a tela.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from core.inteligencia_ativos import fundamentos as fnd
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import valuation as val

# -- leituras ------------------------------------------------------------------------

FORTE, ADEQUADA, FRAGIL, INSUFICIENTE = "forte", "adequada", "fragil", "insuficiente"
BARATO, JUSTO, CARO = "barato", "justo", "caro"
POSITIVO, NEUTRO, NEGATIVO, MISTO = "positivo", "neutro", "negativo", "misto"
SEM_LEITURA = "sem_leitura"

ROTULO_LEITURA = {
    FORTE: "Forte", ADEQUADA: "Adequada", FRAGIL: "Frágil",
    INSUFICIENTE: "Dado insuficiente",
    BARATO: "Abaixo do normal (barato)", JUSTO: "Em linha / misto",
    CARO: "Acima do normal (caro)",
    POSITIVO: "Positivo", NEUTRO: "Neutro", NEGATIVO: "Negativo",
    MISTO: "Misto", SEM_LEITURA: "Sem leitura",
}

QUALIDADE, VALUATION, MERCADO = "qualidade", "valuation", "mercado"
ROTULO_DIMENSAO = {QUALIDADE: "Qualidade e fundamentos",
                   VALUATION: "Valuation (histórico e pares)",
                   MERCADO: "Mercado e notícias"}

# Perfis setoriais (a régua muda com o negócio).
GERAL, FINANCEIRA, REGULADA, CICLICA, FII = (
    "geral", "financeira", "regulada", "ciclica", "fii")
ROTULO_PERFIL = {
    GERAL: "régua geral",
    FINANCEIRA: "banco/seguradora: sem dívida/EBITDA nem EV/EBIT; pesa ROE e P/VP",
    REGULADA: "setor regulado (energia, saneamento): tolera alavancagem maior",
    CICLICA: "commodity/cíclica: P/L baixo pode ser pico de ciclo",
    FII: "fundo imobiliário: vacância, inadimplência, alavancagem e P/VP",
}
# Dívida líquida/EBITDA: (bom até, alto acima de, crítico acima de).
LIMITES_ALAVANCAGEM = {GERAL: (1.5, 3.0, 4.5), CICLICA: (1.0, 2.5, 4.0),
                       REGULADA: (3.0, 4.0, 5.5)}

_TERMOS_FINANCEIRA = ("financ", "banco", "bancos", "segur", "previdencia",
                      "intermediarios", "bank", "insurance", "credit")
_TERMOS_CICLICA = ("materiais basicos", "petroleo", "refino", "exploracao",
                   "combustiveis", "mineracao", "minerais",
                   "siderurgia", "metalurgia", "papel e celulose", "quimic",
                   "madeira", "oil", "mining", "steel", "metal", "chemical")
_TERMOS_REGULADA = ("utilidade publica", "energia eletrica", "saneamento",
                    "agua", "distribuicao de gas", "transmissao",
                    "electric", "utilit", "water")

# Categorias de notícia que, sobre o próprio ativo, eliminam.
CATEGORIAS_CRITICAS = ("recuperacao_judicial", "fraude")
CATEGORIAS_NEGATIVAS = ("litigio",)
# Notícia de recuperação judicial que fala da SAÍDA dela não é fato novo de
# insolvência (AMER3, 09/2026: "Recuperação judicial é página virada?").
_TERMOS_SAIDA = ("pagina virada", "sai da", "saida da", "deixa a", "encerra",
                 "encerramento", "fim da", "termino", "emerge",
                 "exits", "emerges")

AVISO = ("Avaliação por regras sobre os dados do app, não recomendação: cada "
         "critério traz o número e a referência. Sentimento é léxico do app "
         "(termômetro, não modelo); momento de preço não entra.")


@dataclass(frozen=True)
class Criterio:
    texto: str
    sinal: int          # +1 favorável, -1 desfavorável, 0 contexto


@dataclass(frozen=True)
class Alerta:
    texto: str
    critico: bool       # eliminatório: põe a qualidade em frágil


@dataclass(frozen=True)
class Dimensao:
    chave: str
    leitura: str
    criterios: tuple[Criterio, ...] = ()
    nota: str | None = None

    @property
    def rotulo(self) -> str:
        return ROTULO_DIMENSAO[self.chave]

    @property
    def rotulo_leitura(self) -> str:
        return ROTULO_LEITURA[self.leitura]

    @property
    def favoraveis(self) -> int:
        return sum(1 for c in self.criterios if c.sinal > 0)

    @property
    def desfavoraveis(self) -> int:
        return sum(1 for c in self.criterios if c.sinal < 0)


@dataclass(frozen=True)
class Avaliacao:
    perfil: str
    dimensoes: tuple[Dimensao, ...]
    alertas: tuple[Alerta, ...] = ()

    @property
    def rotulo_perfil(self) -> str:
        return ROTULO_PERFIL[self.perfil]

    def dimensao(self, chave: str) -> Dimensao:
        return next(d for d in self.dimensoes if d.chave == chave)

    @property
    def qualidade(self) -> str:
        return self.dimensao(QUALIDADE).leitura

    @property
    def preco(self) -> str:
        return self.dimensao(VALUATION).leitura

    @property
    def mercado(self) -> str:
        return self.dimensao(MERCADO).leitura

    @property
    def criticos(self) -> tuple[Alerta, ...]:
        return tuple(a for a in self.alertas if a.critico)

    @property
    def avaliou_algo(self) -> bool:
        return any(d.criterios for d in self.dimensoes)

    def resumo(self) -> str:
        """Uma linha: as três leituras e o pior alerta."""
        partes = [f"qualidade {ROTULO_LEITURA[self.qualidade].lower()}",
                  f"preço {ROTULO_LEITURA[self.preco].lower()}",
                  f"mercado {ROTULO_LEITURA[self.mercado].lower()}"]
        if self.criticos:
            partes.append(f"alerta: {self.criticos[0].texto}")
        return "; ".join(partes)


# -- utilidades ----------------------------------------------------------------------

def _norm(t: str | None) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _br(x: float, casas: int = 1) -> str:
    return f"{x:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _pct(x: float, casas: int = 1) -> str:
    return f"{_br(x, casas)}%"


def _x(x: float) -> str:
    return f"{_br(x, 2 if abs(x) < 10 else 1)}x"


def _fmt(x: float, unidade: str) -> str:
    return _pct(x) if unidade == fnd.PCT else _x(x)


def _ref(ind: fnd.Indicador | None) -> str:
    return f" ({ind.referencia})" if ind is not None and ind.referencia else ""


def _secao(sec: m.Secao | None) -> dict:
    return (sec.dados if sec is not None and isinstance(sec.dados, dict)
            else {}) or {}


# -- perfil setorial -----------------------------------------------------------------

def perfil(a: m.AnaliseAtivo, f: fnd.Fundamentos, v: val.Valuation,
           c: prs.ComparacaoPares | None) -> str:
    tipo = f.tipo or v.tipo or (c.tipo if c else None)
    if tipo == fnd.FII:
        return FII
    ev = v.linha("ev_ebit")
    if ev is not None and ev.motivo == val.NAO_SE_APLICA["financeira"]:
        return FINANCEIRA
    textos = [a.ativo.setor, a.ativo.subclasse]
    if c is not None:
        textos.append(c.grupo.valor)
    texto = " ".join(_norm(t) for t in textos if t)
    if any(t in texto for t in _TERMOS_FINANCEIRA):
        return FINANCEIRA
    # Cíclica antes de regulada: "Petróleo, Gás e Biocombustíveis" é commodity.
    if any(t in texto for t in _TERMOS_CICLICA):
        return CICLICA
    if any(t in texto for t in _TERMOS_REGULADA):
        return REGULADA
    return GERAL


# -- qualidade -----------------------------------------------------------------------

def _contra_pares(c: prs.ComparacaoPares | None, chave: str, rotulo: str,
                  maior_melhor: bool = True) -> Criterio | None:
    ln = c.linha(chave) if c is not None else None
    if ln is None or ln.posicao not in (val.ACIMA, val.ABAIXO) \
            or ln.valor is None or ln.mediana_pares is None:
        return None
    melhor = (ln.posicao == val.ACIMA) == maior_melhor
    rel = "acima" if ln.posicao == val.ACIMA else "abaixo"
    return Criterio(
        f"{rotulo} {_pct(ln.valor)} {rel} da mediana de {ln.n_pares} pares "
        f"({_pct(ln.mediana_pares)})", 1 if melhor else -1)


def _qualidade_acao(f: fnd.Fundamentos, c, perfil_: str,
                    crits: list[Criterio], alertas: list[Alerta]) -> None:
    def ind(chave):
        i = f.indicador(chave)
        return i if i is not None and _num(i.valor) is not None else None

    def val_(chave):
        i = ind(chave)
        return _num(i.valor) if i else None

    # Rentabilidade: ROE contra os pares; sem pares, régua absoluta.
    roe_par = _contra_pares(c, "roe", "ROE")
    roe = ind("roe")
    if roe_par is not None:
        crits.append(roe_par)
    elif roe is not None:
        r = _num(roe.valor)
        bom, fraco = (15.0, 10.0) if perfil_ == FINANCEIRA else (15.0, 8.0)
        if r >= bom:
            crits.append(Criterio(f"ROE de {_pct(r)}{_ref(roe)}: alto", 1))
        elif r < fraco:
            crits.append(Criterio(f"ROE de {_pct(r)}{_ref(roe)}: baixo "
                                  f"(abaixo de {_pct(fraco, 0)})", -1))
        else:
            crits.append(Criterio(f"ROE de {_pct(r)}{_ref(roe)}: médio", 0))
    margem_par = _contra_pares(c, "margem_liquida", "Margem líquida")
    if margem_par is not None:
        crits.append(margem_par)

    lucro = val_("lucro_liquido")
    if lucro is not None and lucro <= 0:
        crits.append(Criterio("Prejuízo no último exercício"
                              f"{_ref(ind('lucro_liquido'))}", -1))
        alertas.append(Alerta("prejuízo no último exercício", False))

    cresc_l = val_("crescimento_lucro")
    if cresc_l is not None:
        if cresc_l <= -20:
            crits.append(Criterio(f"Lucro caiu {_pct(-cresc_l)}"
                                  f"{_ref(ind('crescimento_lucro'))}", -1))
        elif cresc_l >= 10:
            crits.append(Criterio(f"Lucro cresceu {_pct(cresc_l)}"
                                  f"{_ref(ind('crescimento_lucro'))}", 1))

    payout = val_("payout")
    if perfil_ == FINANCEIRA:
        if payout is not None and payout > 100:
            crits.append(Criterio(f"Payout de {_pct(payout)}: distribui mais "
                                  "que o lucro", -1))
        return

    roic = ind("roic")
    if roic is not None:
        r = _num(roic.valor)
        if r >= 12:
            crits.append(Criterio(f"ROIC de {_pct(r)}{_ref(roic)}: retorno "
                                  "sobre o capital alto", 1))
        elif r < 6:
            crits.append(Criterio(f"ROIC de {_pct(r)}{_ref(roic)}: abaixo do "
                                  "custo de capital típico", -1))
    if margem_par is None:
        ml = val_("margem_liquida")
        if ml is not None and ml <= 0:
            crits.append(Criterio(f"Margem líquida de {_pct(ml)}", -1))

    cresc = val_("crescimento_receita")
    if cresc is not None:
        if cresc >= 5:
            crits.append(Criterio(f"Receita cresceu {_pct(cresc)}"
                                  f"{_ref(ind('crescimento_receita'))}", 1))
        elif cresc < 0:
            crits.append(Criterio(f"Receita encolheu {_pct(-cresc)}"
                                  f"{_ref(ind('crescimento_receita'))}", -1))

    # O lucro vira caixa?
    fco = val_("fluxo_caixa_operacional")
    if fco is not None and lucro is not None and lucro > 0:
        conv = fco / lucro
        if fco <= 0:
            crits.append(Criterio("Caixa operacional negativo com lucro "
                                  "positivo: lucro que não vira caixa", -1))
        elif conv > 5:
            # Lucro perto de zero: a razão explode e não mede conversão.
            crits.append(Criterio("Lucro pequeno demais diante do caixa "
                                  "operacional para medir conversão", 0))
        elif conv >= 0.8:
            crits.append(Criterio(f"Caixa operacional de {_br(conv, 2)}x o "
                                  "lucro: o lucro vira caixa", 1))
        elif conv < 0.5:
            crits.append(Criterio(f"Caixa operacional de só {_br(conv, 2)}x o "
                                  "lucro: lucro que não vira caixa", -1))
    fcl = val_("fluxo_caixa_livre")
    if fcl is not None:
        crits.append(Criterio("Fluxo de caixa livre positivo", 1) if fcl > 0
                     else Criterio("Fluxo de caixa livre negativo: consome "
                                   "caixa", -1))

    # Endividamento, com a régua do setor.
    dl_ebitda = ind("divida_liquida_ebitda")
    if dl_ebitda is not None:
        x = _num(dl_ebitda.valor)
        bom, alto, critico = LIMITES_ALAVANCAGEM.get(perfil_,
                                                     LIMITES_ALAVANCAGEM[GERAL])
        regua = (" (régua de setor regulado)" if perfil_ == REGULADA else
                 " (régua de cíclica)" if perfil_ == CICLICA else "")
        if x <= 0:
            crits.append(Criterio("Caixa líquido (dívida líquida negativa)", 1))
        elif x <= bom:
            crits.append(Criterio(f"Dívida líquida/EBITDA de {_x(x)}"
                                  f"{_ref(dl_ebitda)}: confortável{regua}", 1))
        elif x > alto:
            crits.append(Criterio(f"Dívida líquida/EBITDA de {_x(x)}"
                                  f"{_ref(dl_ebitda)}: alta (acima de "
                                  f"{_x(alto)}){regua}", -1))
            if x > critico:
                alertas.append(Alerta(f"alavancagem de {_x(x)} o EBITDA, acima "
                                      f"do limite de {_x(critico)}{regua}",
                                      True))
    cob = ind("cobertura_juros")
    if cob is not None:
        x = _num(cob.valor)
        if x >= 3:
            crits.append(Criterio(f"Resultado operacional cobre os juros "
                                  f"{_x(x)}", 1))
        elif x < 1.5:
            crits.append(Criterio(f"Cobertura de juros de {_x(x)}: o resultado "
                                  "operacional mal paga os juros", -1))
            if x < 1.0:
                alertas.append(Alerta("o resultado operacional não paga os "
                                      f"juros (cobertura de {_x(x)})", True))

    if payout is not None and payout > 100:
        if fcl is not None and fcl < 0:
            # Em setor regulado, caixa livre negativo costuma ser ciclo de
            # investimento com retorno contratado: alerta, não eliminação.
            regulada = perfil_ == REGULADA
            alertas.append(Alerta(
                f"payout de {_pct(payout)} com caixa livre negativo: "
                + ("dividendo pago com dívida durante o ciclo de investimento"
                   if regulada else "dividendo insustentável"),
                not regulada))
        crits.append(Criterio(f"Payout de {_pct(payout)}: distribui mais que o "
                              "lucro", -1))


def _qualidade_fii(f: fnd.Fundamentos, c, crits: list[Criterio],
                   alertas: list[Alerta]) -> None:
    def val_(chave):
        i = f.indicador(chave)
        return _num(i.valor) if i is not None else None

    vac = val_("vacancia_financeira")
    rot_vac = "Vacância financeira"
    if vac is None:
        vac, rot_vac = val_("vacancia_fisica"), "Vacância física"
    if vac is not None:
        if vac <= 5:
            crits.append(Criterio(f"{rot_vac} de {_pct(vac)}: baixa", 1))
        elif vac > 15:
            crits.append(Criterio(f"{rot_vac} de {_pct(vac)}: alta", -1))
            if vac > 30:
                alertas.append(Alerta(f"{rot_vac.lower()} de {_pct(vac)}",
                                      True))
    inad = val_("inadimplencia")
    if inad is not None:
        if inad > 5:
            crits.append(Criterio(f"Inadimplência de {_pct(inad)}", -1))
            if inad > 10:
                alertas.append(Alerta(f"inadimplência de {_pct(inad)}", True))
        elif inad <= 1:
            crits.append(Criterio(f"Inadimplência de {_pct(inad)}: baixa", 1))
    alav = val_("alavancagem")
    if alav is None and c is not None:
        ln = c.linha("alavancagem")
        alav = ln.valor if ln is not None else None
    if alav is not None:
        if alav > 30:
            crits.append(Criterio(f"Alavancagem de {_pct(alav)} do patrimônio",
                                  -1))
        elif alav <= 10:
            crits.append(Criterio(f"Alavancagem de {_pct(alav)}: baixa", 1))
    wault = val_("wault")
    if wault is not None:
        crits.append(Criterio(f"WAULT de {_br(wault)} anos", 1 if wault >= 5
                              else -1 if wault < 2 else 0))
    # Dividend yield e P/VP ficam no valuation, para não contar duas vezes.
    liq = c.linha("liquidez_diaria") if c is not None else None
    if liq is not None and liq.posicao == val.ABAIXO:
        crits.append(Criterio(f"Liquidez diária abaixo da mediana de "
                              f"{liq.n_pares} pares", -1))


def _leitura_qualidade(crits, alertas) -> str:
    if any(a.critico for a in alertas):
        return FRAGIL
    assinados = [c.sinal for c in crits if c.sinal]
    if len(assinados) < 3:
        return INSUFICIENTE
    saldo = sum(assinados) / len(assinados)
    if saldo >= 0.4:
        return FORTE
    if saldo <= -0.2:
        return FRAGIL
    return ADEQUADA


# -- valuation -----------------------------------------------------------------------

_MENOR_E_BARATO = {"p_l", "p_vp", "ev_ebit"}


def _valuation(v: val.Valuation, perfil_: str, crits: list[Criterio],
               alertas: list[Alerta]) -> tuple[str, str | None]:
    nota = None
    saldo, medidos = 0.0, 0
    for ln in v.linhas:
        if ln.motivo == val.NAO_SE_APLICA["patrimonio_negativo"]:
            alertas.append(Alerta("patrimônio líquido negativo", True))
        if not ln.aplicavel or ln.atual is None:
            continue
        sinais = []
        partes = []
        for pos, est, nome in ((ln.posicao_historica, ln.historico,
                                "do histórico"),
                               (ln.posicao_pares, ln.pares, "dos pares")):
            if pos not in (val.ACIMA, val.ABAIXO) or est is None:
                continue
            barato = (pos == val.ABAIXO) == (ln.chave in _MENOR_E_BARATO)
            sinais.append(1 if barato else -1)
            rel = "acima" if pos == val.ACIMA else "abaixo"
            extra = (f", percentil {ln.percentil_historico:.0f}"
                     if nome == "do histórico"
                     and ln.percentil_historico is not None else "")
            partes.append(f"{rel} da mediana {nome} "
                          f"({_fmt(est.mediana, ln.unidade)}{extra})")
        if not sinais:
            continue
        medidos += 1
        atual = _fmt(ln.atual, ln.unidade)
        # As duas réguas concordando valem 1; uma só, meia.
        s = (sum(sinais) / 2.0) if len(sinais) == 2 else sinais[0] / 2.0
        if ln.chave == "p_l" and s > 0 and ln.atual < 3:
            nota = ("P/L abaixo de 3x costuma vir de lucro não recorrente "
                    "(venda de ativo, crédito fiscal); não contou como barato.")
            s = 0.0
        elif ln.chave == "p_l" and perfil_ == CICLICA and s > 0:
            nota = ("P/L baixo em empresa cíclica costuma marcar lucro no pico "
                    "do ciclo; não contou como barato.")
            s = 0.0
        saldo += s
        crits.append(Criterio(f"{ln.rotulo} de {atual}: " + " e ".join(partes),
                              0 if s == 0 else 1 if s > 0 else -1))
    if medidos == 0:
        return SEM_LEITURA, nota
    media = saldo / medidos
    leitura = BARATO if media >= 0.4 else CARO if media <= -0.4 else JUSTO
    return leitura, nota


# -- mercado e notícias --------------------------------------------------------------

def _sentimento(n: inf.Noticia, moeda: str) -> float | None:
    from core.noticias import normalizacao, sentimento
    texto = f"{n.headline}. {n.summary or ''}"
    idioma = normalizacao.detectar_idioma(texto) or (
        "pt" if moeda == "BRL" else "en")
    return sentimento.calcular(texto, idioma)


def _mercado(a: m.AnaliseAtivo, c, crits: list[Criterio],
             alertas: list[Alerta]) -> tuple[str, str | None]:
    nots = inf.Noticias.de_dict(_secao(a.noticias))
    escores = []
    for n in nots.itens:
        if n.categoria in CATEGORIAS_CRITICAS and any(
                t in _norm(n.headline) for t in _TERMOS_SAIDA):
            alertas.append(Alerta(f"{n.rotulo_categoria.lower()} citada no "
                                  f"noticiário ({(n.date or '')[:10]}), mas a "
                                  f"notícia fala da saída: {n.headline}",
                                  False))
            crits.append(Criterio(f"{n.rotulo_categoria} em pauta, sem fato "
                                  f"novo: {n.headline}", 0))
            continue
        if n.categoria in CATEGORIAS_CRITICAS:
            alertas.append(Alerta(f"{n.rotulo_categoria.lower()} no noticiário"
                                  f" ({(n.date or '')[:10]}): {n.headline}",
                                  True))
            crits.append(Criterio(f"{n.rotulo_categoria}: {n.headline}", -1))
            escores.append(-1.0)
            continue
        s = _sentimento(n, a.ativo.moeda)
        if n.categoria in CATEGORIAS_NEGATIVAS and (s is None or s > 0):
            s = -0.5
        if s is None:
            continue
        escores.append(s)
    nota = None
    if escores:
        pos = sum(1 for s in escores if s > 0.15)
        neg = sum(1 for s in escores if s < -0.15)
        media = sum(escores) / len(escores)
        crits.append(Criterio(
            f"{len(escores)} notícia(s) própria(s) com tom medido em "
            f"{nots.janela_dias or 60} dias: {pos} positiva(s), {neg} "
            f"negativa(s), tom médio {_br(media, 2)}",
            1 if media > 0.15 else -1 if media < -0.15 else 0))
    elif nots.itens:
        nota = "Notícias próprias sem termo que o léxico meça."
    else:
        nota = (f"Sem notícia própria nos últimos {nots.janela_dias or 60} "
                "dias.")
    setor = [s for s in (_sentimento(n, a.ativo.moeda) for n in nots.setor)
             if s is not None]
    if setor:
        ms = sum(setor) / len(setor)
        crits.append(Criterio(f"Tom do noticiário do segmento "
                              f"({len(setor)} notícia(s) de pares): "
                              f"{_br(ms, 2)}", 0))

    # Risco de mercado contra os pares.
    for chave, rotulo in (("volatilidade", "Volatilidade"),
                          ("max_drawdown", "Queda máxima")):
        ln = c.linha(chave) if c is not None else None
        if ln is None or ln.posicao not in (val.ACIMA, val.ABAIXO) \
                or ln.valor is None or ln.mediana_pares is None:
            continue
        maior = ln.posicao == val.ACIMA
        crits.append(Criterio(
            f"{rotulo} de {_pct(abs(ln.valor))} "
            f"{'acima' if maior else 'abaixo'} da mediana de {ln.n_pares} "
            f"pares ({_pct(abs(ln.mediana_pares))})", -1 if maior else 1))

    assinados = [x.sinal for x in crits if x.sinal]
    if not assinados:
        return SEM_LEITURA, nota
    if any(s > 0 for s in assinados) and any(s < 0 for s in assinados) \
            and abs(sum(assinados)) <= 1 and len(assinados) >= 2:
        return MISTO, nota
    saldo = sum(assinados)
    return (POSITIVO if saldo > 0 else NEGATIVO if saldo < 0 else NEUTRO), nota


# -- avaliação -----------------------------------------------------------------------

def avaliar(a: m.AnaliseAtivo) -> Avaliacao:
    """Avalia o ativo em três dimensões e levanta os alertas. Puro."""
    f = fnd.Fundamentos.de_dict(_secao(a.fundamentos))
    v = val.Valuation.de_dict(_secao(a.valuation))
    dp = _secao(a.pares)
    c = prs.ComparacaoPares.de_dict(dp) if dp else None
    perfil_ = perfil(a, f, v, c)
    alertas: list[Alerta] = []

    q: list[Criterio] = []
    if perfil_ == FII:
        _qualidade_fii(f, c, q, alertas)
    elif (f.tipo or v.tipo) == fnd.ACAO:
        _qualidade_acao(f, c, perfil_, q, alertas)
    vc: list[Criterio] = []
    leitura_v, nota_v = _valuation(v, perfil_, vc, alertas)
    mc: list[Criterio] = []
    leitura_m, nota_m = _mercado(a, c, mc, alertas)

    leitura_q = _leitura_qualidade(q, alertas)
    nota_q = None
    if leitura_q == FRAGIL and leitura_v == BARATO:
        nota_q = ("Preço baixo com qualidade frágil: pode ser armadilha de "
                  "valor, o mercado pode estar certo.")
    if leitura_q == INSUFICIENTE and not q:
        nota_q = (f.motivo or "Sem fundamentos publicados para este ativo.")
    return Avaliacao(perfil_, (
        Dimensao(QUALIDADE, leitura_q, tuple(q), nota_q),
        Dimensao(VALUATION, leitura_v, tuple(vc), nota_v),
        Dimensao(MERCADO, leitura_m, tuple(mc), nota_m),
    ), tuple(alertas))


def argumentos(av: Avaliacao, n: int = 2) -> tuple[str, ...]:
    """Uma linha por dimensão: a leitura e os critérios que a puxaram.

    Os critérios escolhidos são os do mesmo lado da leitura (os contra numa
    leitura ruim, os a favor numa boa); leitura mista mostra um de cada.
    """
    saida = []
    for d in av.dimensoes:
        if d.leitura in (INSUFICIENTE, SEM_LEITURA):
            saida.append(f"{d.rotulo}: {d.rotulo_leitura.lower()}"
                         + (f" ({d.nota})" if d.nota else ""))
            continue
        bons = [c.texto for c in d.criterios if c.sinal > 0]
        ruins = [c.texto for c in d.criterios if c.sinal < 0]
        if d.leitura in (FORTE, BARATO, POSITIVO):
            esc = bons[:n]
        elif d.leitura in (FRAGIL, CARO, NEGATIVO):
            esc = ruins[:n]
        else:
            esc = bons[:1] + ruins[:1]
        saida.append(f"{d.rotulo}: {d.rotulo_leitura.lower()}"
                     + (f" — {'; '.join(esc)}" if esc else ""))
    for al in av.alertas:
        saida.append(f"Alerta{' eliminatório' if al.critico else ''}: "
                     f"{al.texto}")
    return tuple(saida)


def texto(av: Avaliacao, ticker: str) -> str:
    """Bloco para a LLM: leituras, critérios e alertas, com a régua usada."""
    linhas = [f"[Avaliação multicritério (regras) — {ticker}]",
              f"Régua setorial: {av.rotulo_perfil}."]
    for d in av.dimensoes:
        linhas.append(f"{d.rotulo}: {d.rotulo_leitura}"
                      f" ({d.favoraveis} a favor, {d.desfavoraveis} contra)")
        for c in d.criterios:
            sinal = "+" if c.sinal > 0 else "−" if c.sinal < 0 else "·"
            linhas.append(f"  {sinal} {c.texto}")
        if d.nota:
            linhas.append(f"  nota: {d.nota}")
    for al in av.alertas:
        linhas.append(f"ALERTA{' ELIMINATÓRIO' if al.critico else ''}: "
                      f"{al.texto}")
    linhas.append(AVISO)
    return "\n".join(linhas)
