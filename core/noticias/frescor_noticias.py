"""Frescor das notícias, em minutos, e registro da última coleta bem-sucedida.

Existe separado de ``core/frescor.py`` por uma razão de unidade, não de gosto:
aquele módulo mede publicação de vitrine em **dias** e trabalha com ``date``.
Notícia vence em minutos, e arredondar minutos para dias transformaria "coletado
há 40 minutos" e "coletado há 20 horas" no mesmo "hoje". A doutrina é a mesma e
está mantida aqui: **idade indeterminável devolve ``None``, nunca ``0``.** Não
saber há quanto tempo foi a última coleta não é o mesmo que ter coletado agora.

Duas coisas distintas são medidas, e confundi-las é o erro clássico:

``idade da coleta``    -- há quanto tempo o APP4 falou com o provedor.
``idade da noticia``   -- há quanto tempo o fato foi publicado.

Uma coleta de dois minutos atrás pode trazer matéria de três semanas. Por isso
``rotular_idade`` marca a matéria velha como velha mesmo quando o painel acabou
de atualizar -- é literalmente o requisito de "nunca apresentar notícia antiga
como se fosse atual".
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.noticias.modelos import Noticia

logger = logging.getLogger(__name__)

CAMINHO_PADRAO = Path("local_staging") / "noticias" / "coleta.json"

ESTADO_FRESCO = "fresco"
ESTADO_VENCIDO = "vencido"
ESTADO_SEM_COLETA = "sem_coleta"
ESTADO_DESCONHECIDO = "desconhecido"

ROTULO_ESTADO = {
    ESTADO_FRESCO: "Atualizado",
    ESTADO_VENCIDO: "Desatualizado",
    ESTADO_SEM_COLETA: "Nunca coletado",
    ESTADO_DESCONHECIDO: "Idade desconhecida",
}

#: Multiplicador sobre a cadência antes de declarar vencido. Uma coleta que
#: atrasou meio ciclo não é dado velho; duas cadências sem sucesso, sim.
FOLGA_CADENCIA = 2.0


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _para_utc(valor) -> datetime | None:
    if isinstance(valor, datetime):
        return (valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None
                else valor.astimezone(timezone.utc))
    if isinstance(valor, str) and valor.strip():
        try:
            return _para_utc(datetime.fromisoformat(valor.strip()))
        except ValueError:
            return None
    return None


def idade_em_minutos(quando: datetime | None,
                     agora: datetime | None = None) -> float | None:
    """Minutos desde ``quando``. ``None`` quando não dá para saber.

    Nunca negativa: relógio adiantado do provedor viraria "idade negativa" e
    depois "notícia do futuro" na tela.
    """
    referencia = _para_utc(quando)
    if referencia is None:
        return None
    delta = (agora or agora_utc()) - referencia
    return max(0.0, delta.total_seconds() / 60.0)


@dataclass(frozen=True)
class EstadoFrescor:
    """Situação de frescor de um provedor (ou do motor todo)."""

    estado: str
    idade_minutos: float | None = None
    limite_minutos: float | None = None
    ultimo_sucesso: datetime | None = None
    ultima_tentativa: datetime | None = None
    emergencia: bool = False
    ultimo_erro: str | None = None
    itens_no_ultimo_sucesso: int | None = None

    @property
    def fresco(self) -> bool:
        return self.estado == ESTADO_FRESCO

    @property
    def vencido(self) -> bool:
        """Só ``True`` quando há medição dizendo isso.

        Estado desconhecido não é vencido nem fresco -- e não deve virar
        nenhum dos dois por conveniência de quem consome.
        """
        return self.estado == ESTADO_VENCIDO

    @property
    def rotulo(self) -> str:
        return ROTULO_ESTADO.get(self.estado, self.estado)

    def texto(self) -> str:
        if self.estado == ESTADO_SEM_COLETA:
            return "Nenhuma coleta bem-sucedida registrada ate agora."
        if self.idade_minutos is None:
            return ("Nao foi possivel determinar quando foi a ultima coleta "
                    "bem-sucedida.")
        return (f"{self.rotulo}: ultima coleta bem-sucedida ha "
                f"{formatar_idade(self.idade_minutos)}.")


def formatar_idade(minutos: float | None) -> str:
    """Idade legível. ``None`` vira texto de desconhecimento, não "agora"."""
    if minutos is None:
        return "tempo desconhecido"
    if minutos < 1:
        return "menos de 1 minuto"
    if minutos < 60:
        return f"{int(minutos)} min"
    horas = minutos / 60.0
    if horas < 48:
        return f"{horas:.1f} h"
    return f"{horas / 24.0:.1f} dias"


def rotular_idade(noticia: Noticia, *, agora: datetime | None = None,
                  limite_horas: float = 72.0) -> tuple[str, bool | None]:
    """Rótulo de idade da matéria e se ela ainda conta como atual.

    Devolve ``(texto, atual)`` onde ``atual`` é ``None`` quando a matéria não
    trouxe data de publicação. Sem data, o correto é dizer que não se sabe --
    tratar como atual seria apresentar notícia possivelmente antiga como se
    fosse de agora, e tratar como antiga descartaria matéria boa por defeito
    do provedor.
    """
    minutos = noticia.idade_em_minutos(agora)
    if minutos is None:
        return ("sem data de publicacao informada pela fonte", None)
    atual = minutos <= limite_horas * 60.0
    quando = noticia.publicado_em
    carimbo = quando.strftime("%d/%m/%Y %H:%M UTC") if quando else ""
    texto = f"publicada ha {formatar_idade(minutos)} ({carimbo})"
    if not atual:
        texto += " - ANTIGA para o limite configurado"
    return (texto, atual)


class RegistroColeta:
    """Última coleta bem-sucedida e última tentativa, por provedor.

    Persistido em disco porque o job agendado, o script manual e a sessão do
    Streamlit são processos diferentes que precisam enxergar o mesmo carimbo.
    Gravação atômica: um ``kill`` no meio do job não pode deixar o arquivo pela
    metade e apagar o histórico de sucesso.
    """

    def __init__(self, caminho: Path | str | None = None, *,
                 agora=None, persistir: bool = True) -> None:
        self.caminho = Path(caminho) if caminho else CAMINHO_PADRAO
        self._agora = agora or agora_utc
        self._persistir = persistir
        self._estado: dict[str, dict] = self._carregar()

    def _carregar(self) -> dict[str, dict]:
        if not self._persistir:
            return {}
        try:
            with open(self.caminho, "r", encoding="utf-8") as fh:
                dados = json.load(fh)
            return dados if isinstance(dados, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Registro de coleta ilegivel (%s): recomecando", exc)
            return {}

    def _salvar(self) -> None:
        if not self._persistir:
            return
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            fd, temporario = tempfile.mkstemp(
                dir=str(self.caminho.parent), suffix=".parcial")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._estado, fh, ensure_ascii=False, indent=2)
            os.replace(temporario, self.caminho)
        except OSError as exc:
            logger.warning("Nao foi possivel gravar o registro de coleta: %s",
                           exc)

    def registrar_sucesso(self, provedor: str, *, itens: int = 0,
                          quando: datetime | None = None) -> None:
        carimbo = _para_utc(quando) or self._agora()
        entrada = self._estado.setdefault(provedor, {})
        entrada["ultimo_sucesso"] = carimbo.isoformat()
        entrada["ultima_tentativa"] = carimbo.isoformat()
        entrada["itens"] = int(itens)
        entrada["ultimo_erro"] = None
        self._salvar()

    def registrar_falha(self, provedor: str, motivo: str, *,
                        quando: datetime | None = None) -> None:
        """Falha atualiza a tentativa, **nunca** o último sucesso.

        Essa é a linha que impede o painel de dizer "atualizado agora" depois
        de uma coleta que não trouxe nada.
        """
        carimbo = _para_utc(quando) or self._agora()
        entrada = self._estado.setdefault(provedor, {})
        entrada["ultima_tentativa"] = carimbo.isoformat()
        entrada["ultimo_erro"] = str(motivo)[:500]
        self._salvar()

    def ultimo_sucesso(self, provedor: str) -> datetime | None:
        return _para_utc(self._estado.get(provedor, {}).get("ultimo_sucesso"))

    def ultima_tentativa(self, provedor: str) -> datetime | None:
        return _para_utc(self._estado.get(provedor, {}).get("ultima_tentativa"))

    def provedores(self) -> tuple[str, ...]:
        return tuple(sorted(self._estado))

    def estado(self, provedor: str, *, cadencia_minutos: float,
               emergencia: bool = False) -> EstadoFrescor:
        entrada = self._estado.get(provedor, {})
        sucesso = self.ultimo_sucesso(provedor)
        tentativa = self.ultima_tentativa(provedor)
        limite = max(1.0, float(cadencia_minutos) * FOLGA_CADENCIA)

        if sucesso is None:
            estado = (ESTADO_DESCONHECIDO if entrada else ESTADO_SEM_COLETA)
            return EstadoFrescor(
                estado=estado,
                limite_minutos=limite,
                ultima_tentativa=tentativa,
                emergencia=emergencia,
                ultimo_erro=entrada.get("ultimo_erro"),
            )

        idade = idade_em_minutos(sucesso, self._agora())
        return EstadoFrescor(
            estado=(ESTADO_FRESCO if idade is not None and idade <= limite
                    else ESTADO_VENCIDO if idade is not None
                    else ESTADO_DESCONHECIDO),
            idade_minutos=idade,
            limite_minutos=limite,
            ultimo_sucesso=sucesso,
            ultima_tentativa=tentativa,
            emergencia=emergencia,
            ultimo_erro=entrada.get("ultimo_erro"),
            itens_no_ultimo_sucesso=entrada.get("itens"),
        )

    def precisa_coletar(self, provedor: str, *, cadencia_minutos: float,
                        forcar: bool = False) -> bool:
        """Se já passou a cadência desde o último **sucesso**.

        Medir contra a última tentativa faria uma sequência de falhas parecer
        trabalho feito e adiar a coleta indefinidamente.
        """
        if forcar:
            return True
        sucesso = self.ultimo_sucesso(provedor)
        if sucesso is None:
            return True
        idade = idade_em_minutos(sucesso, self._agora())
        return idade is None or idade >= max(0.0, float(cadencia_minutos))

    def proxima_coleta(self, provedor: str, *,
                       cadencia_minutos: float) -> datetime | None:
        sucesso = self.ultimo_sucesso(provedor)
        if sucesso is None:
            return None
        return sucesso + timedelta(minutes=max(0.0, float(cadencia_minutos)))
