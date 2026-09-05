"""A carteira real do usuário, no formato que os portões de notícia esperam.

O sexto portão — "respeita perfil, horizonte e limites da carteira" — existia,
tinha teste e nunca era exercido em produção: ``data_pipeline.jobs
.update_noticias`` chamava ``coletar`` sem ``perfil=``, e o valor por omissão é
:data:`core.noticias.portoes.PERFIL_VAZIO`. Com perfil vazio o portão devolve
``None`` ("sem carteira cadastrada"), e ``None`` não aprova. O portão só podia
não-aprovar, para sempre, e por isso a ação ``sugerir_revisao`` era inalcançável
no pipeline (``memoria: gate-que-so-dava-false``,
``diagnostico-precisa-porta-de-entrada``).

Isto não é só uma trava a menos. ``perfil.tickers`` também entra em
``relevancia.calcular`` como ``tickers_alvo``, e ``exposicao_por_ativo`` vira a
fração do patrimônio exposta à matéria. Sem perfil, notícia sobre um ativo que o
usuário tem e notícia sobre um ativo que ele nunca teve pontuavam igual.

Três estados, e eles não se confundem
-------------------------------------
A carteira pode estar **medida** (leitura real, com posições), **medida e
vazia** (leitura real, nenhuma posição) ou **não medida** (banco fora, sem
``OWNER_USER_ID``, MOCK_MODE). Só o primeiro produz perfil. Os outros dois
devolvem :data:`PERFIL_VAZIO` **com limitação escrita**, porque um perfil vazio
silencioso e um perfil vazio declarado levam à mesma decisão hoje e a
diagnósticos opostos amanhã.

MOCK_MODE é recusa explícita
----------------------------
``get_carteira()`` devolve posições sintéticas quando ``MOCK_MODE`` está ligado.
Deixá-las virar perfil faria a carteira de demonstração pontuar relevância e
abrir portão sobre notícia real — decisão de verdade tomada com entrada
inventada, que é o modo de falha registrado em ``memoria:
fallback-nunca-contradiz``.

O que este módulo deliberadamente não inventa
---------------------------------------------
``horizonte_meses`` e ``limite_por_ativo`` ficam ``None``. Não há, em lugar
nenhum do APP4, horizonte de investidor ou teto por ativo declarado pelo
usuário; escolher um número aqui seria fabricar a premissa que o portão depois
checaria contra si mesma. ``None`` desliga esses dois testes dentro do portão e
deixa valendo os que têm base medida: quais ativos a notícia toca e quanto do
patrimônio está neles.
"""
from __future__ import annotations

import logging

from core.noticias.portoes import PERFIL_VAZIO, Perfil

logger = logging.getLogger(__name__)


def _fracao(pct) -> float | None:
    """``pct_carteira`` vem em 0–100; ``exposicao_por_ativo`` é 0–1.

    A conversão parece detalhe e não é: ``relevancia`` satura a exposição em
    1,0, então esquecer a divisão faria qualquer posição acima de 1% do
    patrimônio virar "100% da carteira exposta" — e toda notícia da carteira
    receberia a pontuação máxima de exposição.
    """
    try:
        valor = float(pct)
    except (TypeError, ValueError):
        return None
    if valor != valor:  # NaN
        return None
    return max(0.0, min(1.0, valor / 100.0))


def carregar(*, carteira: dict | None = None) -> tuple[Perfil, tuple[str, ...]]:
    """Perfil do usuário e as limitações da leitura. Nunca levanta.

    ``carteira`` existe para o teste injetar o dicionário sem tocar no banco;
    em produção vem de :func:`core.investimentos.get_carteira`, que é a única
    fonte da carteira no projeto — reescrever o SQL aqui criaria uma segunda
    verdade sobre a mesma posição, com FX e snapshot resolvidos de outro jeito.
    """
    if carteira is None:
        try:
            from core.config import settings

            if settings.MOCK_MODE:
                return PERFIL_VAZIO, (
                    "perfil da carteira nao carregado: MOCK_MODE ligado, e "
                    "posicao sintetica nao decide sobre noticia real",)
        except Exception as exc:  # noqa: BLE001
            return PERFIL_VAZIO, (f"perfil da carteira: {exc}",)
        try:
            from core.investimentos import get_carteira

            carteira = get_carteira()
        except Exception as exc:  # noqa: BLE001 - coleta nao cai por isto
            causa = str(exc).splitlines()[0][:160]
            logger.warning("Perfil da carteira indisponivel: %s", causa)
            return PERFIL_VAZIO, (
                f"perfil da carteira nao lido ({causa}): o portao de carteira "
                f"fica em 'nao verificavel' e nenhuma noticia vira sugestao",)

    fonte = str(carteira.get("data_source") or "")
    if fonte == "mock":
        return PERFIL_VAZIO, (
            "perfil da carteira nao carregado: fonte 'mock'",)
    if fonte != "real":
        motivo = carteira.get("error_message") or f"fonte '{fonte or 'ausente'}'"
        return PERFIL_VAZIO, (
            f"perfil da carteira nao lido ({motivo}): o portao de carteira "
            f"fica em 'nao verificavel'",)

    exposicao: dict[str, float] = {}
    sem_peso: list[str] = []
    for posicao in carteira.get("posicoes") or []:
        ticker = str(posicao.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        fracao = _fracao(posicao.get("pct_carteira"))
        if fracao is None:
            # Ticker sem peso legível continua sendo um ativo da carteira: entra
            # em ``tickers`` para o portão de relação, e fica fora de
            # ``exposicao_por_ativo`` para não ser contado como exposição zero.
            sem_peso.append(ticker)
            exposicao.setdefault(ticker, 0.0)
            continue
        exposicao[ticker] = exposicao.get(ticker, 0.0) + fracao

    tickers = tuple(sorted(exposicao))
    limitacoes: list[str] = []
    if not tickers:
        return PERFIL_VAZIO, (
            "carteira lida e sem posicoes: perfil, horizonte e limites nao "
            "restringem nada",)
    if sem_peso:
        limitacoes.append(
            "peso na carteira ilegivel para " + ", ".join(sorted(sem_peso))
            + ": esses ativos contam para relacao, nao para exposicao")

    perfil = Perfil(exposicao_por_ativo=exposicao, tickers=tickers)
    logger.info("Perfil da carteira: %s ativos, exposicao somada %.1f%%",
                len(tickers), 100.0 * sum(exposicao.values()))
    return perfil, tuple(limitacoes)
