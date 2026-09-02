"""
core/aporte.py — convergencia por aporte, sem venda.

Responde a pergunta que o resto do app nao respondia: **dado R$ X para
aportar este mes, para onde vai o dinheiro?**

`core.rebalancing` responde outra coisa — SE hoje e dia de mexer — e
`core.global_portfolio.advisor` transforma isso em ordens que incluem
"reduzir" e "vender". As duas sao corretas para quem rebalanceia
negociando. Nenhuma das duas serve para quem so aporta, que e o caso real
de quem esta formando patrimonio: esse investidor converge para o alvo
comprando o que falta, nunca vendendo o que sobra.

A diferenca nao e cosmetica. Uma carteira teorica com rebalanceamento
periodico e uma carteira alimentada por aporte sao carteiras diferentes,
com retornos diferentes, e publicar o retorno de uma ao lado da instrucao
de operar a outra e o defeito registrado em
`memoria: backtest-teorico-nao-e-alcancavel`.

Identidade que sustenta o algoritmo
-----------------------------------
Com patrimonio P, aporte A e pesos-alvo somando 1, definindo

    deficit_i = alvo_i * (P + A) - valor_i

vale, por construcao, `soma(todos os deficit_i) = A`. Logo

    soma(deficit_i positivos) = A + soma(|deficit_i negativos|) >= A

ou seja: **a soma dos deficits positivos nunca e menor que o aporte**, com
igualdade exata so quando nenhuma posicao esta acima do alvo. O aporte
portanto sempre e integralmente distribuivel entre quem esta abaixo do
alvo, e nunca ha "sobra por falta de destino" — sobra so aparece por lote
que nao fecha ou por teto de preco, e nesses casos e reportada, nunca
silenciada.

Denominador
-----------
`deficit` usa o patrimonio **depois** do aporte, nao antes. Usar `P` faria
o alvo se mover a cada aporte e a carteira perseguir um numero que ela
mesma desloca.

Camada pura: sem SQL, sem Streamlit, sem I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import floor, isfinite

# Distancia L1/2 (`sum|w - alvo| / 2`) abaixo da qual a carteira e
# considerada convergida. 0,5 p.p. e escolha de desenho: abaixo disso o
# desvio e menor que o proprio arredondamento de lote na maioria das
# carteiras de varejo.
TOLERANCIA_CONVERGENCIA_DEFAULT = 0.005

# Teto de iteracoes de `meses_para_convergir`. Nao e um limite fisico: e o
# ponto a partir do qual a resposta "50 anos" deixa de ser informacao util
# e passa a ser ruido — o chamador recebe None e diz "nao converge", em vez
# de imprimir um numero grande que parece uma previsao.
HORIZONTE_MAXIMO_MESES = 600

MOTIVO_ACIMA_DO_TETO = "preco acima do teto de compra"
MOTIVO_LOTE_NAO_FECHA = "aporte insuficiente para um lote"

# Motivo padrao quando o Score Conjuntural suspende aporte novo e o chamador
# nao informa um texto proprio. Suspender aporte NAO e vender: a posicao
# existente fica intacta e so o dinheiro novo e desviado para os demais.
MOTIVO_SUSPENSAO_CONJUNTURAL = "aporte suspenso pelo score conjuntural"

# Faixa em que uma prioridade e aceita. Prioridade nunca bloqueia: um ativo com
# prioridade baixissima ainda recebe alguma coisa se houver deficit e dinheiro.
# Bloquear e outra operacao, com outro parametro e outro motivo declarado —
# manter as duas distinguiveis e o que impede "prioridade zero" de virar uma
# suspensao silenciosa que ninguem consegue ler no plano.
PRIORIDADE_MINIMA_APORTE = 0.01
PRIORIDADE_MAXIMA_APORTE = 100.0


@dataclass(frozen=True)
class Alocacao:
    """Para onde vai (ou por que nao vai) uma fatia do aporte, por ativo.

    `valor_aportado` e sempre >= 0 — este modulo nunca vende. Um ativo
    acima do alvo aparece com `deficit` negativo e `valor_aportado` zero:
    ele nao e omitido do plano, porque "esta acima do alvo e por isso nao
    recebe" e informacao, e some se a linha some.

    `motivo_bloqueio` e "" quando o ativo simplesmente nao precisava de
    aporte. Ele so e preenchido quando o ativo PRECISAVA e mesmo assim nao
    recebeu — teto de preco ou lote que nao fecha. A distincao importa: as
    duas situacoes zeram `valor_aportado` por razoes opostas.
    """

    symbol: str
    valor_atual: float
    peso_atual: float
    peso_alvo: float
    deficit: float
    valor_aportado: float
    peso_depois: float
    cotas: int | None = None
    preco: float | None = None
    motivo_bloqueio: str = ""

    @property
    def bloqueado(self) -> bool:
        return bool(self.motivo_bloqueio)


@dataclass(frozen=True)
class PlanoAporte:
    """Plano completo de um aporte.

    `sobra` e o dinheiro que o plano NAO conseguiu alocar (lote que nao
    fecha, teto de preco). Ela existe como campo de primeira classe de
    proposito: um plano que devolve menos do que recebeu sem dizer quanto e
    onde e exatamente o tipo de defeito silencioso que
    `memoria: defeito-silencioso-vs-erro` descreve.
    """

    aporte: float
    patrimonio_antes: float
    patrimonio_depois: float
    alocacoes: tuple[Alocacao, ...]
    sobra: float
    desvio_antes: float
    desvio_depois: float
    meses_para_convergir: int | None = None
    convergencia_avaliada: bool = False

    @property
    def bloqueadas(self) -> tuple[Alocacao, ...]:
        return tuple(a for a in self.alocacoes if a.bloqueado)

    @property
    def recebem(self) -> tuple[Alocacao, ...]:
        """Alocacoes com dinheiro de fato direcionado, da maior para a menor."""
        com_valor = [a for a in self.alocacoes if a.valor_aportado > 0]
        return tuple(sorted(com_valor, key=lambda a: (-a.valor_aportado, a.symbol)))


def _num(valor) -> float:
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return 0.0
    return f if isfinite(f) else 0.0


def desvio_l1(pesos: dict[str, float], alvos: dict[str, float]) -> float:
    """Distancia `sum|w - alvo| / 2` sobre a UNIAO dos dois conjuntos.

    A uniao, e nao so `alvos`: um ativo que a carteira tem e o alvo nao
    quer (alvo 0) e o maior desvio possivel, e iterar so as metas o tornaria
    invisivel — mesmo defeito ja corrigido em `core.rebalancing._maior_desvio`.

    Dividido por 2 para a leitura ser "fracao do patrimonio no lugar
    errado": uma carteira 50/50 com alvo 100/0 devolve 0,50, nao 1,00.
    """
    return sum(
        abs(pesos.get(tk, 0.0) - alvos.get(tk, 0.0))
        for tk in set(pesos) | set(alvos)
    ) / 2.0


def _normalizar_alvos(alvos: dict[str, float]) -> dict[str, float]:
    """Alvos como fracoes somando 1. Aceita entrada em % (soma ~100)."""
    limpos = {tk: _num(v) for tk, v in alvos.items() if _num(v) > 0}
    total = sum(limpos.values())
    if total <= 0:
        return {}
    return {tk: v / total for tk, v in limpos.items()}


def plano_de_aporte(
    valores_atuais: dict[str, float],
    alvos: dict[str, float],
    aporte: float,
    *,
    precos: dict[str, float] | None = None,
    tetos_preco: dict[str, float] | None = None,
    lote: dict[str, int] | int = 1,
    bloqueios_conjunturais: dict[str, str] | None = None,
    prioridades: dict[str, float] | None = None,
) -> PlanoAporte:
    """Distribui `aporte` entre os ativos abaixo do peso-alvo, sem vender.

    valores_atuais: {ticker: valor em R$ hoje}. Ticker ausente = posicao zero.
    alvos:          {ticker: peso}. Aceita fracao (soma 1) ou % (soma 100).
    aporte:         R$ a distribuir. Zero ou negativo devolve plano vazio
                    com o diagnostico de desvio ainda calculado.
    precos:         {ticker: preco unitario}. Sem isto o plano sai em R$
                    (`cotas is None`) — util para renda fixa e fundos.
    tetos_preco:    {ticker: preco maximo de compra}. Ativo cujo preco
                    excede o teto e BLOQUEADO e seu deficit e redistribuido
                    entre os demais. Ausente = sem teto (nunca bloqueia).
    lote:           tamanho do lote por ticker, ou int unico para todos.
    bloqueios_conjunturais: {ticker: motivo}. Vem de
                    `core.memoria_mercado.scores.para_aporte`. Trata o ativo
                    exatamente como o teto de preco trata: ele sai do rateio de
                    dinheiro NOVO e seu deficit vai para os demais. A posicao ja
                    existente nao e tocada — este modulo continua sem vender.
    prioridades:    {ticker: multiplicador}. Reordena quem recebe mais dentro de
                    quem continua elegivel, multiplicando o peso do deficit no
                    rateio. Ausente = 1,0. Nao cria nem destroi dinheiro: o que
                    um ativo deixa de receber outro recebe.

    Ordem das operacoes, e ela importa: teto de preco bloqueia ANTES da
    distribuicao (o deficit do bloqueado vai para os outros, nao vira
    sobra), e o arredondamento por lote acontece DEPOIS (o que sobra do
    arredondamento e sobra de verdade, e fica declarado).
    """
    atuais = {tk: _num(v) for tk, v in (valores_atuais or {}).items()}
    metas = _normalizar_alvos(alvos or {})
    precos = {tk: _num(p) for tk, p in (precos or {}).items()}
    tetos_preco = {tk: _num(t) for tk, t in (tetos_preco or {}).items() if _num(t) > 0}
    conjunturais = {tk: (str(m) or MOTIVO_SUSPENSAO_CONJUNTURAL)
                    for tk, m in (bloqueios_conjunturais or {}).items()}
    pesos_prioridade = {
        tk: min(PRIORIDADE_MAXIMA_APORTE, max(PRIORIDADE_MINIMA_APORTE, _num(v)))
        for tk, v in (prioridades or {}).items() if _num(v) > 0
    }

    universo = sorted(set(atuais) | set(metas))
    patrimonio = sum(atuais.get(tk, 0.0) for tk in universo)
    aporte = max(0.0, _num(aporte))
    patrimonio_depois = patrimonio + aporte

    pesos_antes = (
        {tk: atuais.get(tk, 0.0) / patrimonio for tk in universo}
        if patrimonio > 0
        else {tk: 0.0 for tk in universo}
    )
    desvio_antes = desvio_l1(pesos_antes, metas)

    if not universo or patrimonio_depois <= 0:
        return PlanoAporte(
            aporte=aporte, patrimonio_antes=patrimonio,
            patrimonio_depois=patrimonio_depois, alocacoes=(), sobra=aporte,
            desvio_antes=desvio_antes, desvio_depois=desvio_antes,
        )

    # Deficit contra o patrimonio DEPOIS do aporte (ver docstring do modulo).
    deficits = {
        tk: metas.get(tk, 0.0) * patrimonio_depois - atuais.get(tk, 0.0)
        for tk in universo
    }

    def _lote_de(tk: str) -> int:
        if isinstance(lote, dict):
            return max(1, int(lote.get(tk, 1) or 1))
        return max(1, int(lote or 1))

    bloqueios: dict[str, str] = {}
    for tk in universo:
        if deficits[tk] <= 0:
            continue
        motivo_conjuntural = conjunturais.get(tk)
        if motivo_conjuntural:
            bloqueios[tk] = motivo_conjuntural
            continue
        teto = tetos_preco.get(tk)
        preco = precos.get(tk, 0.0)
        if teto is not None and preco > 0 and preco > teto:
            bloqueios[tk] = MOTIVO_ACIMA_DO_TETO

    elegiveis = [tk for tk in universo if deficits[tk] > 0 and tk not in bloqueios]
    soma_deficits = sum(deficits[tk] for tk in elegiveis)

    bruto: dict[str, float] = dict.fromkeys(universo, 0.0)
    if aporte > 0 and soma_deficits > 0:
        # Cascata: reparte proporcionalmente ao deficit ponderado pela
        # prioridade, devolve ao bolo o que exceder o deficit de cada um e
        # reparte de novo entre quem ainda cabe. Sem a cascata, elevar a
        # prioridade de um ativo o faria estourar o proprio deficit, o `min`
        # cortaria a diferenca e o troco viraria `sobra` — dinheiro sumindo do
        # plano por causa de um parametro que so deveria ter reordenado quem
        # recebe primeiro. Com todas as prioridades em 1,0 a cascata converge na
        # primeira rodada e o resultado e identico ao anterior.
        restante = aporte
        capacidade = {tk: deficits[tk] for tk in elegiveis}
        abertos = [tk for tk in elegiveis if capacidade[tk] > 0]
        for _ in range(len(elegiveis) + 1):
            if restante <= 1e-9 or not abertos:
                break
            pesos_rodada = {tk: capacidade[tk] * pesos_prioridade.get(tk, 1.0)
                            for tk in abertos}
            soma_rodada = sum(pesos_rodada.values())
            if soma_rodada <= 0:
                break
            distribuido = 0.0
            for tk in abertos:
                fatia = min(capacidade[tk],
                            restante * pesos_rodada[tk] / soma_rodada)
                bruto[tk] += fatia
                capacidade[tk] -= fatia
                distribuido += fatia
            if distribuido <= 1e-12:
                break
            restante -= distribuido
            abertos = [tk for tk in abertos if capacidade[tk] > 1e-9]

    # Arredondamento por lote, depois da distribuicao.
    alocado: dict[str, float] = {}
    cotas: dict[str, int | None] = {}
    for tk in universo:
        valor = bruto[tk]
        preco = precos.get(tk, 0.0)
        if valor <= 0:
            alocado[tk] = 0.0
            cotas[tk] = None
            continue
        if preco <= 0:
            # Sem preco nao ha como converter em cotas, mas o valor em R$
            # continua valido (renda fixa, fundo, Tesouro). Nao e bloqueio.
            alocado[tk] = valor
            cotas[tk] = None
            continue
        tamanho = _lote_de(tk)
        n = int(floor(valor / (preco * tamanho))) * tamanho
        if n <= 0:
            bloqueios.setdefault(tk, MOTIVO_LOTE_NAO_FECHA)
            alocado[tk] = 0.0
            cotas[tk] = 0
            continue
        alocado[tk] = n * preco
        cotas[tk] = n

    sobra = aporte - sum(alocado.values())
    patrimonio_final = patrimonio + sum(alocado.values())

    linhas: list[Alocacao] = []
    for tk in universo:
        valor_final = atuais.get(tk, 0.0) + alocado[tk]
        linhas.append(Alocacao(
            symbol=tk,
            valor_atual=atuais.get(tk, 0.0),
            peso_atual=pesos_antes.get(tk, 0.0),
            peso_alvo=metas.get(tk, 0.0),
            deficit=deficits[tk],
            valor_aportado=alocado[tk],
            peso_depois=(valor_final / patrimonio_final) if patrimonio_final > 0 else 0.0,
            cotas=cotas[tk],
            preco=precos.get(tk) or None,
            motivo_bloqueio=bloqueios.get(tk, ""),
        ))

    pesos_depois = {a.symbol: a.peso_depois for a in linhas}
    return PlanoAporte(
        aporte=aporte,
        patrimonio_antes=patrimonio,
        patrimonio_depois=patrimonio_final,
        alocacoes=tuple(linhas),
        sobra=max(0.0, sobra),
        desvio_antes=desvio_antes,
        desvio_depois=desvio_l1(pesos_depois, metas),
    )


def meses_para_convergir(
    valores_atuais: dict[str, float],
    alvos: dict[str, float],
    aporte_mensal: float,
    *,
    tolerancia: float = TOLERANCIA_CONVERGENCIA_DEFAULT,
    horizonte: int = HORIZONTE_MAXIMO_MESES,
    precos: dict[str, float] | None = None,
    tetos_preco: dict[str, float] | None = None,
    lote: dict[str, int] | int = 1,
) -> int | None:
    """Quantos aportes iguais bastam para o desvio cair abaixo de `tolerancia`.

    Devolve `None` quando nao converge dentro de `horizonte` — inclusive
    quando o aporte e zero ou quando todo o dinheiro fica bloqueado. Nunca
    devolve um numero grande fingindo previsao: "nao converge no horizonte"
    e uma resposta, "487 meses" nao e.

    **Premissa declarada, e ela e forte: precos constantes.** O calculo
    responde "quantos aportes de R$ X, se nada se mover, fecham a diferenca"
    — nao e projecao de mercado. Precos reais mudam os pesos entre um aporte
    e outro, e nao ha aqui nenhuma tentativa de modelar isso. Quem exibir
    este numero precisa exibir a premissa junto.
    """
    atuais = {tk: _num(v) for tk, v in (valores_atuais or {}).items()}
    metas = _normalizar_alvos(alvos or {})
    if not metas:
        return None

    patrimonio = sum(atuais.values())
    pesos = (
        {tk: v / patrimonio for tk, v in atuais.items()} if patrimonio > 0 else {}
    )
    if desvio_l1(pesos, metas) <= tolerancia:
        return 0
    if _num(aporte_mensal) <= 0:
        return None

    estado = dict(atuais)
    for mes in range(1, max(1, int(horizonte)) + 1):
        plano = plano_de_aporte(
            estado, metas, aporte_mensal,
            precos=precos, tetos_preco=tetos_preco, lote=lote,
        )
        if plano.sobra >= plano.aporte:
            # Nenhum centavo entrou: teto de preco, lote ou alvo impossivel.
            # Continuar iterando so gastaria ciclos repetindo o mesmo estado.
            return None
        estado = {a.symbol: a.valor_atual + a.valor_aportado for a in plano.alocacoes}
        if plano.desvio_depois <= tolerancia:
            return mes
    return None


def com_convergencia(
    plano: PlanoAporte,
    valores_atuais: dict[str, float],
    alvos: dict[str, float],
    aporte_mensal: float,
    **kwargs,
) -> PlanoAporte:
    """Devolve o mesmo plano com `meses_para_convergir` preenchido.

    Separado de `plano_de_aporte` porque a simulacao custa ate `horizonte`
    iteracoes e nem todo chamador precisa dela — quem so quer saber para
    onde mandar o dinheiro deste mes nao deve pagar por isso.

    `convergencia_avaliada` fica True mesmo quando o resultado e `None`,
    para a interface distinguir "nao converge" de "nao foi calculado".
    """
    meses = meses_para_convergir(valores_atuais, alvos, aporte_mensal, **kwargs)
    return replace(plano, meses_para_convergir=meses, convergencia_avaliada=True)
