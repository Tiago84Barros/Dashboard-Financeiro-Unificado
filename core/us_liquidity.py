"""Piso de negociabilidade do módulo EUA.

Por que NÃO tem troca de classe irmã, ao contrário do módulo B3. Lá o app
trocava BRAP3 por BRAP4 — mesma empresa, mesma exposição econômica, 72× mais
giro. Transplantar isso para os EUA seria perigoso, e a checagem de 03/08/2026
mostrou por quê: o vínculo ``company_id`` do banco americano agrupa instrumentos
que não são classes de ação.

    JPM (US$ 309) e VYLD (US$ 28)  → VYLD não é classe do JPMorgan, é outro papel
    ACON e ACONW (US$ 0,02)        → warrant, com UM pregão em seis meses
    T e TBB                        → baby bond, que é dívida
    AACI / AACIU / AACIW           → SPAC com unit e warrant

Das 448 empresas "multiclasse", a maioria são **ações preferenciais**
(``JPM-PD``, ``BAC-PE``, ``WFC-PC``) — e nos EUA preferred é quase-dívida:
dividendo fixo, sem voto, comportamento de bond. Não é o par ON/PN brasileiro,
onde as duas são capital de risco da mesma empresa. Pior: ``security_type`` só
distingue ``common`` de ``reit``, então o banco marca as preferenciais como
"common" e não há campo confiável para separá-las.

Trocar uma ação por um warrant, um ETF ou uma debênture causaria dano muito
maior que a iliquidez que a troca pretendia corrigir. Então aqui o módulo só
FILTRA — e quem consome sabe que a decisão de classe continua com o usuário.

Puro (sem banco, sem rede). Coberto por tests/test_us_liquidity.py.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

# Changelog da metodologia (docs/empresas_americanas.md registra o racional):
#
# 2.1.0 (2026-08-17, achado A-004) — giro sem data de referência atual,
#   inválida ou ausente deixa de ser evidência de liquidez. O cálculo usa 180
#   pregões, mas a última observação precisa ter no máximo 7 dias corridos;
#   fim de semana, feriado e atraso operacional curto cabem nessa tolerância.
# 2.0.0 (2026-08-17, achado A-004) — a elegibilidade passa a ser TRI-ESTADO.
#   Até a 1.0.0 o símbolo sem giro medido entrava em ``aprovados`` e só saía num
#   aviso: quem consumisse a primeira posição da tupla montava carteira com
#   papel cuja negociabilidade nunca foi medida. Agora "não verificado" é um
#   conjunto próprio e, com piso > 0, NÃO é investível. Contrato quebrado de
#   propósito (tupla → LiquidityScreen) para que nenhum chamador continue lendo
#   o desconhecido como aprovado por omissão.
# 1.0.0 (2026-08-03) — piso de US$ 1 mi/dia calibrado no universo americano.
VERSION = "us-liquidity-2.1.0"

# Escolhido com o usuário em 03/08/2026, medido sobre 2.752 empresas com giro:
# mediana de US$ 7,05 mi/dia, p25 em US$ 0,42 mi, p75 em US$ 60,6 mi. O piso de
# US$ 1 mi mantém 1.864 empresas (68%); US$ 5 mi cairia para 1.481 e US$ 20 mi
# para 1.062.
#
# A ordem de grandeza é outra: no B3 o piso equivalente é R$ 500 mil/dia, porque
# a mediana brasileira fica perto disso. Copiar o número de lá para cá não
# filtraria nada — 1 milhão de reais é ruído no mercado americano.
PISO_PADRAO_USD = 1_000_000.0
LIQUIDITY_MAX_AGE_DAYS = 7
PISO_INVALIDO_MESSAGE = (
    "Piso de negociabilidade inválido: informe um valor finito maior ou igual a zero."
)


def normalizar_piso_diario_usd(valor: object) -> float | None:
    """Piso finito não negativo, ou ``None`` para uma configuração inválida.

    Zero é uma escolha explícita de exploração; NaN, infinitos e negativos não
    são configurações de política e, portanto, jamais podem desativar o gate.
    """
    try:
        piso = float(valor)
    except (TypeError, ValueError):
        return None
    return piso if math.isfinite(piso) and piso >= 0 else None


@dataclass(frozen=True)
class LiquidityPolicy:
    piso_diario_usd: float = PISO_PADRAO_USD

    @property
    def piso_normalizado(self) -> float | None:
        return normalizar_piso_diario_usd(self.piso_diario_usd)

    @property
    def exploratorio(self) -> bool:
        """Somente piso zero finito desliga o gate: modo de EXPLORAÇÃO.

        É o único modo em que ativo não verificado pode aparecer, e a tela que o
        usa precisa dizer isso — ver ``aplicar_piso``.
        """
        return self.piso_normalizado == 0


class EstadoLiquidez(str, Enum):
    """Os três estados possíveis de um ativo diante do piso de negociabilidade.

    ``str`` na base para que o valor viaje em JSON/params sem conversão manual.
    """

    MEDIDA_APROVADA = "MEDIDA_APROVADA"
    MEDIDA_REPROVADA = "MEDIDA_REPROVADA"
    NAO_VERIFICADA = "NAO_VERIFICADA"


def _num(valor: object) -> float:
    """Medição em US$/dia, ou NaN quando não HÁ medição.

    NaN aqui significa exatamente uma coisa: "ninguém mediu isto". Por isso
    ``None``, string vazia, texto não numérico e — principalmente — ±infinito
    caem todos no mesmo balde. Infinito não é um giro altíssimo, é resultado de
    divisão por zero ou de overflow a montante; a versão 1.0.0 devolvia ``inf``,
    que passava por ``>= piso`` e aprovava lixo como se fosse o ativo mais
    líquido do mercado.
    """
    if valor is None:
        return float("nan")
    if isinstance(valor, str) and not valor.strip():
        return float("nan")
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return float("nan")
    return numero if math.isfinite(numero) else float("nan")


def _timestamp_atual(timestamp: object, *, now: datetime) -> bool:
    """Se a medição chegou até uma data dentro da tolerância metodológica."""
    if timestamp is None:
        return False
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        # Datas sem fuso são ambíguas para um mercado americano; não se presume
        # UTC e, portanto, não se usa para autorizar uma posição.
        return False
    reference = now.astimezone(timezone.utc)
    measured_at = parsed.astimezone(timezone.utc)
    return (measured_at <= reference and
            measured_at >= reference - timedelta(days=LIQUIDITY_MAX_AGE_DAYS))


def classificar(
    valor: object,
    piso_usd: float,
    timestamp: object | None = None,
    *,
    now: datetime | None = None,
) -> EstadoLiquidez:
    """Estado de UM ativo. Fonte única da regra para o motor e para as telas.

    Fail-closed com piso > 0: só valor finito, medido e no mínimo igual ao piso
    é ``MEDIDA_APROVADA``. Zero é medição legítima de "não negociou" e reprova;
    ausência de medição não reprova — ela simplesmente não autoriza.
    """
    piso = normalizar_piso_diario_usd(piso_usd)
    if piso is None:
        return EstadoLiquidez.NAO_VERIFICADA
    giro = _num(valor)
    if giro != giro:                           # NaN: ninguém mediu
        return EstadoLiquidez.NAO_VERIFICADA
    # Piso zero só libera a EXPLORAÇÃO no resultado do lote; não converte uma
    # data ausente em evidência. Assim a UI ainda emite o aviso explícito.
    if not _timestamp_atual(timestamp, now=now or datetime.now(timezone.utc)):
        return EstadoLiquidez.NAO_VERIFICADA
    if giro >= piso:
        return EstadoLiquidez.MEDIDA_APROVADA
    return EstadoLiquidez.MEDIDA_REPROVADA


def formata_usd(valor: float) -> str:
    """US$ com separador de milhar no padrão americano."""
    return f"{valor:,.0f}"


def formata_usd_curto(valor: float) -> str:
    """Ordem de grandeza por extenso — "1 milhão", "20 milhões", "750 mil".

    A interface é em português e o valor é em dólar: "US$ 1,000,000" mistura as
    duas convenções e um leitor brasileiro pode ler "1,000000". Escrever a
    ordem de grandeza remove a ambiguidade sem precisar escolher um dos dois
    padrões de separador.
    """
    def _pt(n: float) -> str:
        """Decimal com VÍRGULA — o texto é lido em português."""
        return (f"{int(n)}" if n == int(n) else f"{n:.1f}".replace(".", ","))

    v = float(valor)
    for corte, singular, plural in ((1e9, "bilhão", "bilhões"),
                                    (1e6, "milhão", "milhões")):
        if v >= corte:
            n = v / corte
            return f"{_pt(n)} {singular if n == 1 else plural}"
    if v >= 1e3:
        return f"{_pt(v / 1e3)} mil"
    return f"{v:.0f}"


@dataclass(frozen=True)
class LiquidityScreen:
    """Resultado tri-estado da triagem. Substitui a tupla de três posições.

    Os três conjuntos são disjuntos e a união reconcilia com os símbolos
    recebidos. ``aprovados`` contém SOMENTE quem foi medido e passou — quem
    consumir esse campo por engano nunca receberá um não verificado de brinde.
    """

    aprovados: list[str] = field(default_factory=list)
    removidos: list[dict] = field(default_factory=list)
    nao_verificados: list[str] = field(default_factory=list)
    timestamps: dict[str, object] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    piso_diario_usd: float = PISO_PADRAO_USD
    versao: str = VERSION

    @property
    def exploratorio(self) -> bool:
        return normalizar_piso_diario_usd(self.piso_diario_usd) == 0

    @property
    def elegiveis(self) -> list[str]:
        """Quem pode seguir para a construção de carteira.

        Com piso > 0 é só o medido e aprovado: comprar um papel cuja
        negociabilidade nunca foi medida é assumir risco não verificado, e
        publicação de carteira não aceita holding não verificada. Só no modo
        exploratório (piso zerado pelo usuário) o não verificado entra — e
        nesse modo não existe piso a ser respeitado.
        """
        if self.exploratorio:
            return list(self.aprovados) + list(self.nao_verificados)
        return list(self.aprovados)

    @property
    def estados(self) -> dict[str, EstadoLiquidez]:
        return {
            **{s: EstadoLiquidez.MEDIDA_APROVADA for s in self.aprovados},
            **{r["symbol"]: EstadoLiquidez.MEDIDA_REPROVADA for r in self.removidos},
            **{s: EstadoLiquidez.NAO_VERIFICADA for s in self.nao_verificados},
        }


def aplicar_piso(
    symbols: Sequence[str],
    giro: Mapping[str, float],
    *,
    timestamps: Mapping[str, object] | None = None,
    policy: LiquidityPolicy | None = None,
    now: datetime | None = None,
) -> LiquidityScreen:
    """Separa quem negocia o bastante, quem não negocia e quem ninguém mediu.

    Returns:
        LiquidityScreen — três conjuntos disjuntos, não dois.

    Por que "não verificado" é um estado próprio, e não um aprovado com aviso.
    O universo americano tem 3.759 ativos contra 2.752 com série de volume, e
    tratar os 1.007 sem dado como ilíquidos cortaria empresa boa por lacuna de
    coleta — ausência de medição não é prova de iliquidez. Esse argumento vale
    para EXPLORAR o universo e não vale para CONSTRUIR CARTEIRA: no primeiro
    caso o custo do erro é uma empresa a menos na tela; no segundo é dinheiro
    numa posição que talvez não se consiga desmontar.

    Por isso a decisão fica com o piso, não com o chamador: com piso > 0 o não
    verificado não é investível (``elegiveis``); com o piso zerado pelo usuário
    ele aparece, e o aviso diz explicitamente que a liquidez não foi validada.
    """
    policy = policy or LiquidityPolicy()
    timestamps = timestamps or {}
    piso = policy.piso_normalizado
    if piso is None:
        return LiquidityScreen(
            nao_verificados=[str(s).upper() for s in symbols],
            timestamps={str(s).upper(): timestamps.get(str(s).upper()) for s in symbols},
            avisos=[PISO_INVALIDO_MESSAGE],
            piso_diario_usd=policy.piso_diario_usd,
        )
    aprovados: list[str] = []
    removidos: list[dict] = []
    sem_medicao: list[str] = []

    for s in symbols:
        symbol = str(s).upper()
        estado = classificar(giro.get(symbol), piso, timestamps.get(symbol), now=now)
        if estado is EstadoLiquidez.MEDIDA_APROVADA:
            aprovados.append(symbol)
        elif estado is EstadoLiquidez.MEDIDA_REPROVADA:
            removidos.append({"symbol": symbol, "giro_usd": _num(giro.get(symbol))})
        else:
            sem_medicao.append(symbol)

    avisos: list[str] = []
    if removidos:
        exemplos = ", ".join(r["symbol"] for r in sorted(
            removidos, key=lambda r: r["symbol"])[:12])
        reticencias = "…" if len(removidos) > 12 else ""
        avisos.append(
            f"{len(removidos)} empresa(s) com volume medido abaixo de US$ "
            f"{formata_usd_curto(piso)}/dia — {exemplos}{reticencias}")
    if sem_medicao and policy.exploratorio:
        avisos.append(
            f"{len(sem_medicao)} empresa(s) com negociabilidade **não "
            "verificada** seguem no universo porque o piso está zerado (modo "
            "exploratório). A liquidez delas NÃO foi validada.")
    elif sem_medicao:
        avisos.append(
            f"{len(sem_medicao)} empresa(s) sem série de volume: negociabilidade "
            f"**não verificada** e, com piso de US$ {formata_usd_curto(piso)}/dia, "
            "fora da carteira — comprar sem medir negociabilidade é assumir risco "
            "não verificado. Ausência de medição também não é prova de iliquidez: "
            "para examiná-las, zere o piso e explore sem montar carteira.")
    return LiquidityScreen(
        aprovados=aprovados, removidos=removidos, nao_verificados=sem_medicao,
        timestamps={s: timestamps.get(s) for s in symbols}, avisos=avisos,
        piso_diario_usd=piso,
    )
