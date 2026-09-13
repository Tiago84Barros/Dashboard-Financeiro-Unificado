"""Resolve de onde vem o macro: armazém local se houver, vitrine se não.

O problema que isto resolve
---------------------------
Sete telas fazem hoje a mesma sequência: pedem ``get_local_macro_engine()``,
recebem ``None`` porque não estão na máquina do Docker, e avisam que "a camada
macro do Docker local está indisponível". O aviso é honesto sobre o sintoma e
mudo sobre a causa — o dado **existe**, está calculado, e só não atravessou o
limite entre o armazém local e o app publicado.

:mod:`core.macro_data.vitrine` faz ele atravessar. Este módulo é o lado da
leitura: uma função única que as telas chamam no lugar do par
``get_local_macro_engine`` + ``load_portfolio_macro_snapshot``.

Ordem de preferência, e por quê
-------------------------------
1. **Armazém local**, quando alcançável. É a fonte viva: responde a qualquer
   ``as_of``, a qualquer conjunto de ativos, e não tem idade.
2. **Vitrine no Supabase**, quando não. É um retrato do que o armazém calculou
   na última publicação — serve o conjunto de ativos que foi publicado, não um
   ``as_of`` arbitrário, e chega com idade declarada.

O retorno diz sempre qual dos dois respondeu (``origem``) e quão velho está
(``idade_dias``), porque tratar os dois como intercambiáveis é exatamente o
erro que a vitrine existe para não cometer: um impacto publicado há duas
semanas não vale o que vale o impacto de hoje, e quem desenha a tela precisa
poder dizer isso ao investidor.

Um ``as_of`` histórico nunca é servido pela vitrine. A vitrine guarda **um**
retrato por classe; devolvê-lo para uma data que não é a dele seria olhar o
passado com o que só se soube depois — o mesmo *look-ahead* que
``knowledge_mode='strict'`` existe para impedir.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from core.macro_data.portfolio_context import PortfolioMacroSnapshot
from core.macro_data.vitrine import IDADE_MAXIMA_DIAS, VitrineMacroIlegivel

logger = logging.getLogger(__name__)

ORIGEM_LOCAL = "armazém local"
ORIGEM_VITRINE = "vitrine publicada"


@dataclass(frozen=True)
class MacroResolvido:
    """O snapshot e a procedência dele — nunca um sem o outro."""

    snapshot: PortfolioMacroSnapshot | None
    origem: str | None
    gerada_em: datetime | None = None
    idade_dias: float | None = None
    motivo: str | None = None

    @property
    def disponivel(self) -> bool:
        return self.snapshot is not None

    @property
    def envelhecida(self) -> bool:
        """Vitrine velha continua útil; ela só não pode passar por atual."""
        return self.idade_dias is not None and self.idade_dias > IDADE_MAXIMA_DIAS

    def rotulo(self) -> str:
        """Uma linha para a tela mostrar sem precisar remontar a frase."""
        if not self.disponivel:
            return self.motivo or "camada macro indisponível"
        if self.origem == ORIGEM_LOCAL:
            return "camada macro do armazém local"
        if self.idade_dias is None:
            return "camada macro da vitrine publicada"
        return (f"camada macro da vitrine publicada há {self.idade_dias:.0f} dia(s)"
                + (" — considere republicar" if self.envelhecida else ""))


def _idade_em_dias(gerada_em: datetime | None) -> float | None:
    if gerada_em is None:
        return None
    if gerada_em.tzinfo is None:
        gerada_em = gerada_em.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - gerada_em).total_seconds() / 86400.0, 0.0)


def snapshot_da_vitrine(linhas, meta, *, assets=None) -> PortfolioMacroSnapshot:
    """Remonta um ``PortfolioMacroSnapshot`` a partir das linhas publicadas.

    Quando a vitrine foi publicada por setor (o caso normal — ver
    :mod:`core.macro_data.vitrine`), cada linha é expandida para os ativos de
    ``assets`` que pertencem àquele setor. A expansão é exata, não uma
    atribuição: o impacto de um símbolo *é* o impacto do setor dele.

    Só o que a tela consome é remontado: impactos, fatores e a contagem de
    cobertura. As observações macro que produziram os números ficaram no
    armazém de propósito.
    """
    meta = meta or {}
    por_setor = str(meta.get("granularidade") or "ativo") == "setor"
    ativos = {str(k).strip().upper(): str(v or "").strip()
              for k, v in dict(assets or {}).items() if str(k).strip()}

    publicado: dict[str, tuple[float | None, list[dict]]] = {}
    for linha in linhas:
        chave = str(linha.get("simbolo") or "").strip()
        if not chave:
            continue
        fatores = linha.get("fatores") or []
        if isinstance(fatores, str):
            try:
                fatores = json.loads(fatores)
            except ValueError:
                fatores = []
        impacto = linha.get("impacto")
        # A chave é sempre normalizada: a vitrine grava em caixa alta e o
        # setor que a tela tem na mão veio do cadastro, com a caixa dele.
        publicado[chave.upper()] = (
            None if impacto is None else float(impacto), list(fatores))

    impacts: dict[str, float] = {}
    details: list[dict[str, object]] = []

    def _registrar(simbolo: str, setor: str | None, valor, fatores) -> None:
        if valor is not None:
            impacts[simbolo] = float(valor)
        for fator in fatores:
            details.append({
                "symbol": simbolo,
                "sector": setor,
                "provider": fator.get("provider"),
                "provider_code": fator.get("provider_code"),
                "factor": fator.get("fator"),
                "direction": fator.get("direcao"),
                "intensity": fator.get("intensidade"),
                "confidence": fator.get("confianca"),
                "channel": fator.get("canal"),
            })

    if por_setor:
        # Sem carteira na mão não há o que expandir: devolver os setores como se
        # fossem ativos faria a tela desenhar "SANEAMENTO" onde espera "SAPR11".
        for simbolo, setor in ativos.items():
            achado = publicado.get(setor.upper())
            if achado is None:
                continue
            _registrar(simbolo, setor, *achado)
    else:
        for simbolo, (valor, fatores) in publicado.items():
            if ativos and simbolo not in ativos:
                continue
            _registrar(simbolo, ativos.get(simbolo), valor, fatores)

    limitacoes = list(meta.get("limitacoes") or ())
    limitacoes.append(
        "impactos lidos da vitrine publicada; o armazém local não foi consultado"
    )
    # ``asset_count`` descreve a carteira que a tela tem na mão agora, não a que
    # foi publicada: cobertura é uma fração da pergunta atual, não da anterior.
    universo = set(ativos) or set(impacts)
    return PortfolioMacroSnapshot(
        impacts=impacts,
        details=tuple(details),
        as_of=meta.get("as_of") or datetime.now(timezone.utc),
        asset_count=len(universo),
        covered_assets=len(universo & set(impacts)),
        source_count=int(meta.get("fontes") or 0),
        limitations=tuple(limitacoes),
        knowledge_mode=str(meta.get("knowledge_mode") or "strict"),
    )


def resolver_macro(
    *,
    asset_class: str,
    assets,
    as_of: datetime | None = None,
    knowledge_mode: str = "strict",
    engine_local=None,
    engine_vitrine=None,
) -> MacroResolvido:
    """Devolve o macro desta carteira venha ele de onde vier.

    Nunca levanta por indisponibilidade: a tela precisa continuar desenhando o
    resto. Levantar aqui converteria "sem macro" em "sem tela".
    """
    from core.macro_data.database import get_local_macro_engine

    engine = engine_local if engine_local is not None else get_local_macro_engine()
    if engine is not None:
        try:
            from core.macro_data.portfolio_context import load_portfolio_macro_snapshot

            snapshot = load_portfolio_macro_snapshot(
                engine, asset_class=asset_class, assets=dict(assets),
                as_of=as_of, knowledge_mode=knowledge_mode,
            )
            return MacroResolvido(snapshot, ORIGEM_LOCAL)
        except Exception as exc:  # noqa: BLE001 - degrada para a vitrine
            logger.warning("armazém macro local falhou (%s); tentando vitrine", exc)

    if as_of is not None:
        return MacroResolvido(
            None, None,
            motivo=("camada macro indisponível para data histórica: a vitrine "
                    "guarda apenas o retrato mais recente"),
        )

    try:
        from core.database import get_engine
        from core.macro_data.vitrine import ler

        destino = engine_vitrine if engine_vitrine is not None else get_engine()
        linhas, meta = ler(destino, asset_class=asset_class)
    except VitrineMacroIlegivel as exc:
        return MacroResolvido(None, None, motivo=f"vitrine macro ilegível: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("leitura da vitrine macro falhou: %s", exc)
        return MacroResolvido(None, None, motivo="vitrine macro inacessível")

    if meta is None:
        return MacroResolvido(
            None, None,
            motivo=("camada macro nunca publicada; rode "
                    "scripts/publish_macro_vitrine.py no armazém local"),
        )
    if not linhas:
        return MacroResolvido(
            None, None, gerada_em=meta.get("gerada_em"),
            motivo="vitrine macro publicada sem nenhum ativo com impacto medido",
        )

    gerada_em = meta.get("gerada_em")
    reconstruido = snapshot_da_vitrine(linhas, meta, assets=dict(assets))
    if not reconstruido.impacts:
        return MacroResolvido(
            None, None, gerada_em=meta.get("gerada_em"),
            motivo=("vitrine macro publicada não cobre nenhum setor desta "
                    "carteira"),
        )
    return MacroResolvido(
        reconstruido,
        ORIGEM_VITRINE,
        gerada_em=gerada_em,
        idade_dias=_idade_em_dias(gerada_em),
    )
