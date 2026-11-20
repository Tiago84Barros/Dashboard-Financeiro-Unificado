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

**Quando o armazém volta**, :func:`conferir_volta` (chamada pelo roteador do
``app.py`` antes de cada tela) percebe, limpa o ``st.cache_data`` e apaga o
registro: os loaders releem do armazém e o aviso sai. Sem isso, um corte de
um minuto deixava a cópia congelada no cache e o aviso na tela por 12 h
(20/11/2026). Enquanto o armazém não responde, o aviso fica.

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
#: Depois de uma leitura de reserva, de quanto em quanto tempo
#: :func:`conferir_volta` pergunta ao armazém se ele voltou. Igual à pausa do
#: cliente do túnel (``armazem_remoto.PAUSA_APOS_FALHA_S``): antes dela a
#: pergunta nem sairia.
INTERVALO_CONFERE_S = 60

_trava = threading.Lock()
_reserva: dict = {"quando": None, "motivo": None, "conferido": None}
_engine_papel: dict = {}
#: Sobe cada vez que :func:`conferir_volta` limpa o cache. Os caches de
#: sessão (:func:`cache_de_sessao`) guardam a geração em que nasceram e se
#: esvaziam quando ela muda -- o ``st.cache_data.clear()`` não os alcança.
_geracao = [0]


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
        _reserva.update(quando=None, motivo=None, conferido=None)
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


def _armazem_responde() -> bool:
    """O degrau que a produção usa (papel ou túnel) respondeu a um SELECT 1?"""
    url = _url_papel()
    if url:
        try:
            from sqlalchemy import text

            with _engine_do_papel(url).connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - qualquer falha é "ainda não voltou"
            return False
    from core.armazem_remoto import ArmazemRemotoIndisponivel

    try:
        return _consultar_tunel("SELECT 1 AS ok", None) is not None
    except ArmazemRemotoIndisponivel:
        return False


def _limpar_caches() -> None:
    """Esquece os quadros guardados: os da reserva estão entre eles."""
    import streamlit as st

    st.cache_data.clear()


def conferir_volta() -> bool:
    """Se houve reserva e o armazém voltou, limpa o cache e tira o aviso.

    Pergunta no máximo uma vez a cada :data:`INTERVALO_CONFERE_S`, e só
    depois de uma leitura de reserva -- sem reserva, não custa nada. Limpa
    o ``st.cache_data`` inteiro: os loaders da B3 estão espalhados por vários
    módulos, e um que ficasse de fora mostraria a cópia congelada sem aviso.
    Devolve ``True`` quando limpou.
    """
    agora = _agora()
    with _trava:
        quando, conferido = _reserva["quando"], _reserva["conferido"]
        if quando is None or agora - quando < INTERVALO_CONFERE_S:
            return False
        if conferido is not None and agora - conferido < INTERVALO_CONFERE_S:
            return False
        _reserva["conferido"] = agora
    if not _armazem_responde():
        return False
    _limpar_caches()
    with _trava:
        _geracao[0] += 1
        # Reserva nova durante a pergunta: o aviso fica, ela é mais recente.
        if _reserva["quando"] == quando:
            _reserva.update(quando=None, motivo=None, conferido=None)
    logger.info("Armazém respondeu de novo: cache limpo e aviso da reserva retirado.")
    return True


def geracao() -> int:
    """Quantas vezes o cache foi limpo pela volta do armazém neste processo."""
    with _trava:
        return _geracao[0]


def cache_de_sessao(chave: str) -> dict:
    """Cache por ativo em ``st.session_state`` que se esvazia na volta do armazém.

    Parecer guardado na sessão (portão da Inteligência dos Ativos, parecer de
    seleção) pode ter sido feito com a cópia congelada; o
    ``st.cache_data.clear()`` de :func:`conferir_volta` não chega ao
    ``session_state``, e gerar a carteira de novo reaproveitaria o parecer
    velho. Quem guarda parecer por ativo pede o dicionário aqui.

    A geração fica ao lado, em ``<chave>__geracao``; sem ela, o dicionário é
    tido como da geração atual.
    """
    import streamlit as st

    atual = geracao()
    marca = f"{chave}__geracao"
    caixa = st.session_state.get(chave)
    if not isinstance(caixa, dict) or st.session_state.get(marca, atual) != atual:
        caixa = {}
        st.session_state[chave] = caixa
    st.session_state[marca] = atual
    return caixa


class _Resultado:
    def __init__(self, linhas: list[tuple]):
        self._linhas = linhas

    def fetchall(self) -> list[tuple]:
        return self._linhas


class ConexaoB3:
    """Conexão de mentira que lê pela fonte da B3, para leitor que recebe ``conn``.

    ``core.b3_atualidade_trimestral`` (e quem mais seguir o padrão "o leitor
    recebe a conexão") chama ``conn.execute(text(sql), params).fetchall()``.
    Sem este adaptador, a medição do trimestre ia ao ``get_engine()`` -- a
    cópia congelada do Supabase -- enquanto os demonstrativos da mesma tela
    vinham do armazém: um trimestre novo no armazém ganhava aviso de
    "defasado" medido no Supabase.

    Erro de leitura levanta ``RuntimeError``: quem chama já trata falha como
    "não medido", e um quadro vazio aqui viraria "universo sem trimestre".
    """

    def execute(self, sql, params: dict | None = None) -> _Resultado:
        from core.market_read import _q_b3

        frame = _q_b3(str(sql), dict(params or {}))
        erro = frame.attrs.get("load_error")
        if erro:
            raise RuntimeError(
                f"leitura da B3 falhou ({frame.attrs.get('error_type') or erro})")
        frame = frame.astype(object).where(frame.notna(), None)
        return _Resultado([tuple(r) for r in frame.itertuples(index=False, name=None)])
