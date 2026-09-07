"""Orçamento local de chamadas por provedor.

Os planos gratuitos são pequenos e diários: Alpha Vantage dá 25 requisições por
dia, Marketaux 100. Descobrir o limite pelo 429 do servidor é caro -- a
requisição que leva o 429 já consumiu cota em vários provedores, e a janela de
reposição é de 24 horas. Por isso o freio é local e *anterior* à chamada.

O estado não pode viver em memória de processo: o job agendado, o script manual
e a sessão Streamlit são processos diferentes disputando a mesma cota, e um
contador em memória deixaria cada um achar que tem 25 chamadas só para si.

Por omissão o estado vai para arquivo, na doutrina de
``local_staging/estado_publicacao.json`` -- estado de MÁQUINA, fora do
versionamento. Isso basta na máquina do desenvolvedor e **não basta em
produção**: o runner do GitHub Actions começa cada execução com disco limpo, e
com o cron de meia em meia hora o teto diário nunca chegaria a ser atingido no
papel enquanto o provedor devolve 429 na prática. Para esse caso o orçamento
aceita um ``armazem`` compartilhado (ver ``estado_coleta.ConsumoBanco``).

O relógio é injetado. Sem isso, testar "a janela virou" custaria esperar a
janela virar.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.noticias.transporte import ErroTransporte

logger = logging.getLogger(__name__)

CAMINHO_PADRAO = Path("local_staging") / "noticias" / "rate_limit.json"

#: Motivos de :class:`LimiteExcedido`. São constantes, e não literais no ponto
#: de levantamento, porque o consumidor (``coleta._classificar_erro``) precisa
#: distinguir os dois para rotular a limitação na tela -- e comparar contra o
#: **texto** faria a reescrita de uma mensagem virar mudança silenciosa de
#: classificação: o rótulo pararia de separar "acabou" de "está racionado" sem
#: nenhum teste quebrar.
MOTIVO_COTA = "cota local esgotada"
MOTIVO_ESPACAMENTO = "espacado para o teto diario cobrir as 24h"


class LimiteExcedido(ErroTransporte):
    """Cota local esgotada. Não é retentável: esperar aqui é esperar horas."""

    def __init__(self, provedor: str, liberado_em: datetime | None = None,
                 motivo: str = "cota esgotada"):
        quando = (liberado_em.isoformat(timespec="minutes")
                  if liberado_em else "desconhecido")
        super().__init__(
            f"provedor {provedor}: {motivo} (libera em {quando})",
            status=429,
            retentavel=False,
        )
        self.provedor = provedor
        self.liberado_em = liberado_em
        self.motivo = motivo


@dataclass(frozen=True)
class Limite:
    """Teto de chamadas de um provedor. ``None`` significa sem teto conhecido."""

    por_minuto: int | None = None
    por_dia: int | None = None

    @classmethod
    def sem_limite(cls) -> "Limite":
        return cls(None, None)

    @property
    def intervalo_minimo_s(self) -> float | None:
        """Espaçamento mínimo entre chamadas, derivado do teto diário.

        Teto diário sozinho não distribui: ele autoriza gastar as 25 chamadas
        do Alpha Vantage nas primeiras horas e ficar mudo pelo resto do dia.
        Isso não é hipótese -- é a aritmética do modo crise, que roda de 30 em
        30 minutos e pede **48** ciclos por dia contra 25 disponíveis: o
        provedor morre por volta das 12h30 de crise, que é justamente quando
        ele importa mais.

        O piso é ``86400 / por_dia``: 57,6 min para o Alpha Vantage, 14,4 min
        para o Marketaux. Não morde em modo normal (240 min) nem em vigilância
        (60 min) -- morde só na crise, e o efeito é o provedor responder em
        ciclos alternados **o dia inteiro** em vez de meio dia seguido de
        silêncio. Sem teto diário não há piso: ``rss`` e ``finnhub`` passam
        direto.
        """
        if not self.por_dia:
            return None
        return 86400.0 / self.por_dia


# Tetos dos planos gratuitos, conferidos na documentação pública de cada API.
# Ficam aqui e não no config porque são propriedade do provedor, não do usuário;
# quem tem plano pago sobrescreve passando `limites=` ao construir o orçamento.
LIMITES_PADRAO: dict[str, Limite] = {
    "alphavantage": Limite(por_minuto=5, por_dia=25),
    "marketaux": Limite(por_minuto=None, por_dia=100),
    "finnhub": Limite(por_minuto=60, por_dia=None),
    "rss": Limite(por_minuto=30, por_dia=None),
}


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


class Orcamento:
    """Contador de chamadas com janelas de 60 segundos e de 24 horas."""

    def __init__(self, limites: dict[str, Limite] | None = None,
                 caminho: Path | str | None = CAMINHO_PADRAO,
                 agora: Callable[[], datetime] = _agora_utc,
                 persistir: bool = True, armazem=None):
        """``armazem`` substitui o arquivo por um meio compartilhado.

        O arquivo é estado de máquina e some com a máquina. Num runner de CI,
        que nasce com disco limpo a cada execução, o teto diário deixa de
        existir: cada execução se acha a primeira do dia. Quem roda em produção
        passa um armazém -- ``estado_coleta.ConsumoBanco`` -- e o contador passa
        a ser o mesmo para os três processos. O objeto precisa apenas de
        ``carregar() -> dict | None`` e ``salvar(dict)``.
        """
        self._limites = dict(limites or LIMITES_PADRAO)
        self._caminho = Path(caminho) if caminho is not None else None
        self._agora = agora
        self._armazem = armazem
        self._persistir = persistir and (
            armazem is not None or self._caminho is not None)
        self._registros: dict[str, list[float]] = {}
        self._carregar()

    # -- persistência -----------------------------------------------------
    def _carregar(self) -> None:
        if self._armazem is not None:
            try:
                dados = self._armazem.carregar()
            except Exception as exc:  # noqa: BLE001
                logger.warning("armazem de cota ilegivel (%s)", exc)
                dados = None
            # ``None`` é "não consegui ler", e aí o correto é NÃO concluir que
            # a cota está livre. Sem leitura, o orçamento em memória continua
            # zerado apenas para esta execução -- e o job declara a limitação.
            if isinstance(dados, dict):
                for provedor, marcas in dados.items():
                    self._registros[str(provedor)] = [
                        float(m) for m in (marcas or [])
                        if isinstance(m, (int, float))]
            return
        if not self._caminho or not self._caminho.exists():
            return
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Estado corrompido não pode travar a coleta, mas também não pode
            # passar despercebido: sem ele o orçamento reinicia zerado.
            logger.warning("estado de rate limit ilegivel (%s); reiniciando", exc)
            return
        if isinstance(dados, dict):
            for provedor, marcas in dados.items():
                if isinstance(marcas, list):
                    self._registros[str(provedor)] = [
                        float(m) for m in marcas
                        if isinstance(m, (int, float))
                    ]

    def _salvar(self) -> None:
        if not self._persistir:
            return
        if self._armazem is not None:
            try:
                self._armazem.salvar(self._registros)
            except Exception as exc:  # noqa: BLE001
                logger.warning("consumo de cota nao gravado (%s)", exc)
            return
        if self._caminho is None:
            return
        try:
            self._caminho.parent.mkdir(parents=True, exist_ok=True)
            # Escrita atômica: um processo lendo enquanto outro grava não pode
            # encontrar JSON pela metade e concluir que a cota está zerada.
            fd, temporario = tempfile.mkstemp(dir=str(self._caminho.parent),
                                              suffix=".parcial")
            with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
                json.dump(self._registros, arquivo)
            os.replace(temporario, self._caminho)
        except OSError as exc:
            logger.warning("nao foi possivel gravar o estado de rate limit: %s",
                           exc)

    # -- consulta ---------------------------------------------------------
    def _limpar(self, provedor: str) -> list[float]:
        corte = self._agora().timestamp() - 86400.0
        marcas = [m for m in self._registros.get(provedor, []) if m >= corte]
        self._registros[provedor] = marcas
        return marcas

    def limite(self, provedor: str) -> Limite:
        return self._limites.get(provedor, Limite.sem_limite())

    def restante(self, provedor: str) -> dict[str, int | None]:
        """Quanto sobra em cada janela. ``None`` onde não há teto."""
        marcas = self._limpar(provedor)
        agora = self._agora().timestamp()
        limite = self.limite(provedor)
        no_minuto = sum(1 for m in marcas if m >= agora - 60.0)
        return {
            "minuto": (None if limite.por_minuto is None
                       else max(0, limite.por_minuto - no_minuto)),
            "dia": (None if limite.por_dia is None
                    else max(0, limite.por_dia - len(marcas))),
        }

    def espera_de_espacamento(self, provedor: str) -> datetime | None:
        """Quando o piso de espaçamento libera. ``None`` se já liberou.

        Só olha a última chamada: o piso é sobre o intervalo, não sobre o
        acumulado -- disso já cuidam as janelas de minuto e de dia.
        """
        piso = self.limite(provedor).intervalo_minimo_s
        if piso is None:
            return None
        marcas = self._limpar(provedor)
        if not marcas:
            return None
        proxima = datetime.fromtimestamp(max(marcas), tz=timezone.utc) +             timedelta(seconds=piso)
        return proxima if proxima > self._agora() else None

    def permite(self, provedor: str) -> bool:
        sobra = self.restante(provedor)
        if any(v == 0 for v in sobra.values() if v is not None):
            return False
        return self.espera_de_espacamento(provedor) is None

    def liberado_em(self, provedor: str) -> datetime | None:
        """Quando a próxima chamada passa a ser permitida.

        ``None`` quando já está permitida agora. A janela que libera antes é a
        que manda, mas só entre as janelas que estão efetivamente estouradas.
        """
        marcas = self._limpar(provedor)
        if not marcas:
            return None
        limite = self.limite(provedor)
        agora = self._agora()
        candidatos: list[datetime] = []
        if limite.por_minuto is not None:
            no_minuto = sorted(m for m in marcas
                               if m >= agora.timestamp() - 60.0)
            if len(no_minuto) >= limite.por_minuto:
                mais_antiga = no_minuto[len(no_minuto) - limite.por_minuto]
                candidatos.append(
                    datetime.fromtimestamp(mais_antiga, tz=timezone.utc)
                    + timedelta(seconds=60)
                )
        if limite.por_dia is not None and len(marcas) >= limite.por_dia:
            ordenadas = sorted(marcas)
            mais_antiga = ordenadas[len(ordenadas) - limite.por_dia]
            candidatos.append(
                datetime.fromtimestamp(mais_antiga, tz=timezone.utc)
                + timedelta(days=1)
            )
        espaco = self.espera_de_espacamento(provedor)
        if espaco is not None:
            candidatos.append(espaco)
        return min(candidatos) if candidatos else None

    # -- registro ---------------------------------------------------------
    def registrar(self, provedor: str) -> None:
        """Marca uma chamada consumida. Chamar ANTES de sair para a rede.

        Registrar depois perderia a chamada que estourou o timeout -- e ela
        consumiu cota do mesmo jeito.
        """
        marcas = self._limpar(provedor)
        marcas.append(self._agora().timestamp())
        self._registros[provedor] = marcas
        self._salvar()

    def exigir(self, provedor: str) -> None:
        """Levanta ``LimiteExcedido`` quando a cota acabou ou é cedo demais.

        Os dois motivos viram a mesma exceção porque a decisão de quem chama é
        a mesma -- pular o provedor neste ciclo --, mas o texto os separa: quem
        lê a limitação na tela precisa distinguir "acabou a cota do dia" de
        "está sendo espaçado para durar o dia".
        """
        sobra = self.restante(provedor)
        if any(v == 0 for v in sobra.values() if v is not None):
            raise LimiteExcedido(provedor, self.liberado_em(provedor),
                                 motivo=MOTIVO_COTA)
        espera = self.espera_de_espacamento(provedor)
        if espera is not None:
            raise LimiteExcedido(
                provedor, espera,
                motivo=MOTIVO_ESPACAMENTO)
