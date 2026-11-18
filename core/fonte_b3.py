"""De onde o app lê a B3: o armazém local, com a cópia do Supabase como reserva.

Decisão do usuário de 15/11/2026: o Supabase não recebe mais atualização da
B3 (só os arquivos que o próprio usuário sobe), e o app4 lê do armazém. Desde
o PR #755 a ingestão grava só no armazém; o que ficou no Supabase é uma cópia
**congelada** em 15/11.

:func:`consultar` é o ponto único dessa escolha, em quatro degraus:

1. **Banco do app já é o armazém** (``core.us_read._db_is_local``: a URL do
   banco aponta para localhost) -- lê direto, como sempre.
2. **Papel ``app4_leitura`` neste ambiente** (``ARMAZEM_SQL_DB_URL``) -- lê
   direto no armazém com o papel só-leitura. É o caso do PC de
   desenvolvimento cujo ``.env`` aponta o app para o Supabase.
3. **Túnel configurado** (``ARMAZEM_API_URL`` + token) -- o mesmo SELECT vai
   pela rota ``POST /sql`` (``core.armazem_remoto.consultar_sql``). É o caso
   da produção.
4. **Nada disso, ou o degrau escolhido falhou** -- lê a cópia congelada do
   Supabase e **registra** o motivo. A tela mostra o aviso de
   :func:`aviso_reserva` enquanto o registro for recente: o
   ``st.cache_data`` dos loaders guarda o quadro velho por até 12 h, e uma
   tela que mostra a cópia congelada sem dizer engana quem confia nela.

O degrau que falha **não** tenta o seguinte (túnel depois de papel local):
a reserva é uma só, e é nomeada. O quadro devolvido carrega
``attrs["fonte_b3"]`` (``armazem`` | ``armazem_tunel`` | ``supabase_congelado``).
"""
from __future__ import annotations

import logging
import threading
import time

import pandas as pd

logger = logging.getLogger(__name__)

FONTE_ARMAZEM = "armazem"
FONTE_TUNEL = "armazem_tunel"
FONTE_RESERVA = "supabase_congelado"

#: Quanto tempo o aviso fica de pé depois da última leitura de reserva. É o
#: maior TTL dos loaders da B3 em ``core.market_read`` (43200 s): até lá, um
#: quadro lido da cópia congelada ainda pode estar no cache.
JANELA_AVISO_S = 43_200

_trava = threading.Lock()
_reserva: dict = {"quando": None, "motivo": None}
_engine_papel: dict = {}


def _agora() -> float:
    return time.time()


def _url_papel() -> str:
    from core.config import _get_secret

    return _get_secret("ARMAZEM_SQL_DB_URL")


def _consultar_tunel(sql: str, params: dict | None):
    """``None`` sem túnel configurado; levanta ``ArmazemRemotoIndisponivel``."""
    from core import armazem_remoto

    return armazem_remoto.consultar_sql(sql, params)


def _engine_do_papel(url: str):
    """Engine do papel só-leitura, uma por URL e por processo."""
    with _trava:
        eng = _engine_papel.get(url)
        if eng is None:
            from sqlalchemy import create_engine

            eng = create_engine(
                url, pool_size=2, max_overflow=1, pool_pre_ping=True,
                connect_args={"options": "-c default_transaction_read_only=on",
                              "connect_timeout": 5})
            _engine_papel[url] = eng
        return eng


def _registrar_reserva(motivo: str) -> None:
    with _trava:
        _reserva["quando"] = _agora()
        _reserva["motivo"] = motivo
    logger.warning("B3 lida da cópia congelada do Supabase: %s", motivo)


def _limpar_estado() -> None:
    """Para testes."""
    with _trava:
        _reserva.update(quando=None, motivo=None)
        _engine_papel.clear()


def _marcar(frame: pd.DataFrame, fonte: str) -> pd.DataFrame:
    frame.attrs["fonte_b3"] = fonte
    return frame


def consultar(sql: str, params: dict | None = None, *, ler_direto) -> pd.DataFrame:
    """Roda o SELECT da B3 na fonte certa (ver o docstring do módulo).

    ``ler_direto(sql, params, engine=None)`` é o leitor que o chamador já
    usa (``core.market_read._q``): devolve quadro vazio com
    ``attrs["load_error"]`` em vez de levantar. Com ``engine=None`` lê o
    banco do app.
    """
    from core.us_read import _db_is_local

    if _db_is_local():
        return _marcar(ler_direto(sql, params), FONTE_ARMAZEM)

    url = _url_papel()
    if url:
        frame = ler_direto(sql, params, engine=_engine_do_papel(url))
        if not frame.attrs.get("load_error"):
            return _marcar(frame, FONTE_ARMAZEM)
        motivo = f"leitura direta no armazém falhou ({frame.attrs.get('error_type') or frame.attrs['load_error']})"
    else:
        from core.armazem_remoto import ArmazemRemotoIndisponivel

        try:
            frame = _consultar_tunel(sql, params)
        except ArmazemRemotoIndisponivel as exc:
            motivo = f"túnel do armazém: {str(exc).splitlines()[0][:160]}"
        else:
            if frame is not None:
                return _marcar(frame, FONTE_TUNEL)
            motivo = "túnel do armazém não configurado neste ambiente"

    _registrar_reserva(motivo)
    return _marcar(ler_direto(sql, params), FONTE_RESERVA)


def aviso_reserva() -> str | None:
    """Texto do aviso se alguma leitura recente caiu na cópia congelada."""
    with _trava:
        quando, motivo = _reserva["quando"], _reserva["motivo"]
    if quando is None or _agora() - quando > JANELA_AVISO_S:
        return None
    return motivo
