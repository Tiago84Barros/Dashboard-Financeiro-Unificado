"""Cache de respostas de provedor, com prazo configurável.

Duas leituras diferentes de propósito:

``obter``          -- só devolve o que ainda está dentro do prazo.
``obter_vencida``  -- devolve o conteúdo expirado, marcado como vencido.

A segunda existe porque, quando a API cai, ter a última coleta é melhor do que
não ter nada -- mas só se a tela disser que é de antes. Um cache que serve
conteúdo vencido em silêncio é a forma mais fácil de "apresentar notícia antiga
como se fosse atual", que é justamente o que o requisito proíbe. Por isso o
vencido nunca sai pela mesma porta do fresco: quem quiser usá-lo tem de pedir
por nome e recebe ``vencida=True`` junto.

O prazo é do chamador, não do arquivo. Guardar o TTL dentro da entrada
congelaria a configuração no momento da gravação; baixar a validade de 60 para
15 minutos não teria efeito nenhum sobre o que já está em disco.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DIRETORIO_PADRAO = Path("local_staging") / "noticias" / "cache"
TTL_PADRAO_S = 900.0          # 15 minutos


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Entrada:
    """Uma resposta guardada e a idade dela."""

    chave: str
    carga: object
    gravado_em: datetime
    idade_s: float
    vencida: bool = False


def chave_de(provedor: str, parametros: Mapping[str, object] | None = None) -> str:
    """Chave estável para (provedor, consulta).

    Ordena os parâmetros e usa ``hashlib``: a chave precisa ser a mesma em dois
    processos, e ``hash()`` embutido varia por PYTHONHASHSEED. Já custou
    carteiras diferentes para a mesma configuração neste projeto.
    """
    itens = sorted((str(k), str(v)) for k, v in (parametros or {}).items())
    bruto = provedor + "|" + "&".join(f"{k}={v}" for k, v in itens)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:32]


class Cache:
    """Cache em arquivos JSON, um por chave."""

    def __init__(self, diretorio: Path | str | None = DIRETORIO_PADRAO,
                 ttl_s: float = TTL_PADRAO_S,
                 agora: Callable[[], datetime] = _agora_utc):
        self._dir = Path(diretorio) if diretorio is not None else None
        self._ttl = float(ttl_s)
        self._agora = agora

    @property
    def ttl_s(self) -> float:
        return self._ttl

    def _arquivo(self, chave: str) -> Path | None:
        if self._dir is None:
            return None
        return self._dir / f"{chave}.json"

    def _ler(self, chave: str) -> Entrada | None:
        caminho = self._arquivo(chave)
        if caminho is None or not caminho.exists():
            return None
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            gravado = datetime.fromisoformat(str(dados["gravado_em"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("entrada de cache ilegivel (%s); ignorando", exc)
            return None
        if gravado.tzinfo is None:
            gravado = gravado.replace(tzinfo=timezone.utc)
        idade = (self._agora() - gravado).total_seconds()
        return Entrada(chave=chave, carga=dados.get("carga"),
                       gravado_em=gravado, idade_s=idade)

    def obter(self, chave: str, ttl_s: float | None = None) -> Entrada | None:
        """A entrada, se ainda estiver dentro do prazo. Senão ``None``."""
        entrada = self._ler(chave)
        if entrada is None:
            return None
        prazo = self._ttl if ttl_s is None else float(ttl_s)
        if entrada.idade_s > prazo:
            return None
        return entrada

    def obter_vencida(self, chave: str) -> Entrada | None:
        """A entrada mesmo fora do prazo, sempre marcada com ``vencida``.

        Só para modo degradado: provedor indisponível e a tela vai dizer, com o
        horário, que aquilo é a última coleta bem-sucedida.
        """
        entrada = self._ler(chave)
        if entrada is None:
            return None
        vencida = entrada.idade_s > self._ttl
        return Entrada(chave=entrada.chave, carga=entrada.carga,
                       gravado_em=entrada.gravado_em, idade_s=entrada.idade_s,
                       vencida=vencida)

    def guardar(self, chave: str, carga: object) -> None:
        caminho = self._arquivo(chave)
        if caminho is None:
            return
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            fd, temporario = tempfile.mkstemp(dir=str(caminho.parent),
                                              suffix=".parcial")
            with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
                json.dump({"gravado_em": self._agora().isoformat(),
                           "carga": carga}, arquivo, ensure_ascii=False)
            os.replace(temporario, caminho)
        except (OSError, TypeError, ValueError) as exc:
            # Falha de cache degrada desempenho, nunca correção: a coleta segue.
            logger.warning("nao foi possivel gravar o cache: %s", exc)

    def invalidar(self, chave: str) -> None:
        caminho = self._arquivo(chave)
        if caminho is not None and caminho.exists():
            try:
                caminho.unlink()
            except OSError as exc:
                logger.warning("nao foi possivel remover a entrada: %s", exc)


class CacheMemoria(Cache):
    """Cache sem disco, para teste e para execução em ambiente somente-leitura."""

    def __init__(self, ttl_s: float = TTL_PADRAO_S,
                 agora: Callable[[], datetime] = _agora_utc):
        super().__init__(diretorio=None, ttl_s=ttl_s, agora=agora)
        self._memoria: dict[str, tuple[datetime, object]] = {}

    def _ler(self, chave: str) -> Entrada | None:
        item = self._memoria.get(chave)
        if item is None:
            return None
        gravado, carga = item
        idade = (self._agora() - gravado).total_seconds()
        return Entrada(chave=chave, carga=carga, gravado_em=gravado,
                       idade_s=idade)

    def guardar(self, chave: str, carga: object) -> None:
        self._memoria[chave] = (self._agora(), carga)

    def invalidar(self, chave: str) -> None:
        self._memoria.pop(chave, None)
