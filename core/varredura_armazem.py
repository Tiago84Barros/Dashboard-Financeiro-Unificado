"""Varredura do armazém local antes de afirmar que não há dado sobre um ativo.

Ponto único. Regra do usuário de 08/11/2026: as análises de empresas do app e
das suas LLMs, quando encontram lacuna sobre um ativo, varrem o armazém local
antes de concluir que a informação não existe -- a vitrine do Supabase não é
o limite do que o projeto sabe.

Quem lê o inventário é :mod:`core.armazem_inventario`; este módulo decide
**onde** lê (armazém direto no desenvolvimento, túnel na produção, igual aos
``get_warehouse_detail_context`` de cada classe), guarda o resultado por alguns
minutos e devolve três formas do mesmo fato:

* :func:`bloco_varredura` -- seção de prompt, com a regra de leitura no
  cabeçalho; falha vira linha nomeada, nunca vazio;
* :func:`resumo_para_tela` -- uma linha para a tela que diz "ticker não
  encontrado", para o usuário saber o que o armazém tem;
* :func:`nota_para_lacuna` -- sufixo curto para a mensagem de uma lacuna, lido
  só da memória deste processo (sem ida nova ao armazém), para a triagem
  saber de antemão se o dado existe.

Nada aqui levanta para quem chama: o armazém fora do ar é o caso comum da
produção com o PC desligado, e a resposta ao usuário não pode esperar nem
quebrar por isso.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

from core.armazem_inventario import (
    CLASSES,
    TICKERS_MAX,
    conhece,
    normalizar_tickers,
    resumo_curto,
    resumo_para_prompt,
)
from core.contexto_mercado import REGRA_VARREDURA_ARMAZEM  # noqa: F401 - reexportada

__all__ = ["REGRA_VARREDURA_ARMAZEM", "anexar_varredura", "bloco_varredura",
           "inventario", "nota_para_lacuna", "resumo_para_tela"]

logger = logging.getLogger(__name__)

VALIDADE_S = 300

_trava = threading.Lock()
#: ``(classe, ticker) -> (quando, inventario_do_ticker, origem)``. O inventário
#: guardado é o da chamada inteira recortado para um ticker, para a lacuna
#: de um ativo não depender de quais outros foram lidos junto.
_memoria: dict[tuple[str, str], tuple[float, dict, str]] = {}



def _relogio() -> float:
    return time.monotonic()


def _limpar_memoria() -> None:
    """Esquece o que foi guardado (testes)."""
    with _trava:
        _memoria.clear()


def _engines_diretas() -> dict[str, object | None]:
    """Engines do armazém alcançado direto. Só vale quando o app aponta para
    ele (``_db_is_local``); fora disso, devolve tudo ``None``."""
    from core.us_read import _db_is_local, _engine

    if not (_db_is_local() and _engine() is not None):
        return {"mercado": None, "noticias": None, "memoria": None}
    engines: dict[str, object | None] = {"mercado": _engine(), "noticias": None, "memoria": None}
    try:
        from core.noticias.destino import engine_acervo

        engines["noticias"] = engine_acervo()
    except Exception as exc:  # noqa: BLE001 - a fonte fica "sem_banco"
        logger.info("acervo de notícias indisponível para a varredura: %s", exc)
    try:
        from core.memoria_mercado.destino import engine_memoria

        engines["memoria"] = engine_memoria()
    except Exception as exc:  # noqa: BLE001
        logger.info("safra da Memória de Mercado indisponível para a varredura: %s", exc)
    return engines


def _recorte(inventario: dict, ticker: str) -> dict:
    return {"classe": inventario.get("classe"), "tickers": [ticker],
            "fontes": inventario.get("fontes") or {},
            "ativos": {ticker: (inventario.get("ativos") or {}).get(ticker) or {}}}


def _assinatura(inv: dict) -> tuple:
    """O estado de cada fonte numa leitura. Duas leituras só se juntam quando
    as assinaturas são iguais: fonte que falhou numa e foi lida na outra não
    pode virar "lida" para o ticker da primeira -- o "nenhuma linha" daí
    autorizaria a LLM a afirmar uma ausência que ninguém mediu."""
    return tuple(sorted(
        (chave, str(estado.get("estado")), str(estado.get("motivo") or ""))
        for chave, estado in (inv.get("fontes") or {}).items()))


def _juntar(classe: str, tickers: list[str], partes: dict[str, tuple[dict, str]]) -> tuple[dict, str]:
    """Só para partes com a mesma assinatura (``inventario`` garante isso)."""
    ativos: dict = {}
    origens = []
    for tk in tickers:
        inv, origem = partes[tk]
        ativos[tk] = (inv.get("ativos") or {}).get(tk) or {}
        origens.append(origem)
    fontes = dict((partes[tickers[0]][0].get("fontes") or {})) if tickers else {}
    origem = origens[0] if len(set(origens)) == 1 else "lido em mais de um momento"
    return {"classe": classe, "tickers": tickers, "fontes": fontes, "ativos": ativos}, origem


def _ler(classe: str, tickers: list[str]) -> tuple[dict | None, str]:
    """Uma leitura: direto quando o app aponta para o armazém, túnel na
    produção; ``(None, motivo)`` quando nenhum dos dois está configurado."""
    engines = _engines_diretas()
    if engines.get("mercado") is not None:
        from core.armazem_inventario import ler_inventario

        try:
            return ler_inventario(engines, classe, tickers), "lido direto"
        finally:
            for banco in ("noticias", "memoria"):
                eng = engines.get(banco)
                if eng is not None and hasattr(eng, "dispose"):
                    eng.dispose()
    from core import armazem_remoto

    lido = armazem_remoto.inventario_ativos(classe, tickers)
    if lido is None:
        return None, "não configurado"
    return lido, "lido pelo túnel"


def _guardar(classe: str, lido: dict, tickers: list[str], origem: str,
             prontos: dict[str, tuple[dict, str]], agora: float) -> None:
    with _trava:
        for tk in tickers:
            recorte = _recorte(lido, tk)
            _memoria[(classe, tk)] = (_relogio(), recorte, origem)
            prontos[tk] = (recorte, origem)
        for chave in [c for c, (quando, _, _) in _memoria.items()
                      if agora - quando >= VALIDADE_S]:
            del _memoria[chave]


def inventario(classe: str, tickers: Iterable[str]) -> tuple[dict | None, str]:
    """``(inventário, origem)``. ``None`` quando nem o armazém direto nem o
    túnel estão configurados. Levanta quando o túnel está configurado e falhou.

    Reaproveita por :data:`VALIDADE_S` o que já leu de cada ticker: o chat do
    ativo, o relatório e a lacuna declarada pela LLM pedem o mesmo inventário
    em poucos segundos.
    """
    classe = str(classe or "").lower()
    if classe not in CLASSES:
        raise ValueError(f"classe desconhecida: {classe!r}")
    alvo = normalizar_tickers(tickers)[:TICKERS_MAX]
    if not alvo:
        return {"classe": classe, "tickers": [], "fontes": {}, "ativos": {}}, "vazio"
    agora = _relogio()
    prontos: dict[str, tuple[dict, str]] = {}
    with _trava:
        for tk in alvo:
            salvo = _memoria.get((classe, tk))
            if salvo is not None and agora - salvo[0] < VALIDADE_S:
                prontos[tk] = (salvo[1], salvo[2])
    faltam = [tk for tk in alvo if tk not in prontos]
    if faltam:
        lido, origem = _ler(classe, faltam)
        if lido is None:
            return None, origem
        _guardar(classe, lido, faltam, origem, prontos, agora)
    if len({_assinatura(inv) for inv, _ in prontos.values()}) > 1:
        # Leituras de momentos diferentes com fontes em estados diferentes
        # (uma falhou no pregão, a outra não): não se juntam. Lê-se tudo de
        # novo, junto, para um único estado valer para todos os tickers.
        lido, origem = _ler(classe, alvo)
        if lido is None:
            return None, origem
        _guardar(classe, lido, alvo, origem, prontos, agora)
    return _juntar(classe, alvo, prontos)


def _motivo(exc: BaseException) -> str:
    texto = str(exc).splitlines()[0][:140] if str(exc) else ""
    return texto or type(exc).__name__


def bloco_varredura(classe: str, tickers: Iterable[str]) -> str:
    """Seção VARREDURA DO ARMAZÉM LOCAL para o prompt. Nunca levanta; falha
    vira uma linha que diz que a varredura não foi feita -- sem ela, a LLM
    leria o silêncio como "o armazém não tem"."""
    alvo = normalizar_tickers(tickers)[:TICKERS_MAX]
    if not alvo:
        return ""
    try:
        inv, origem = inventario(classe, alvo)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - vira linha no prompt
        logger.warning("varredura do armazém (%s) indisponível: %s", classe, exc)
        return ("VARREDURA DO ARMAZÉM LOCAL: não feita nesta resposta "
                f"({_motivo(exc)}). A existência de dado sobre {', '.join(alvo)} no "
                "armazém está em aberto: diga \"não consultado\", não \"não existe\".")
    if inv is None:
        return ("VARREDURA DO ARMAZÉM LOCAL: não feita -- túnel não configurado neste "
                f"ambiente. A existência de dado sobre {', '.join(alvo)} no armazém "
                "está em aberto: diga \"não consultado\", não \"não existe\".")
    return resumo_para_prompt(inv, origem=origem)


def anexar_varredura(classe: str, tickers: Iterable[str], texto: str) -> str:
    """``texto`` (o detalhe do armazém já montado) seguido da varredura dos
    mesmos ativos. É o que os prompts que vetam recebem: o detalhe diz os
    números; a varredura diz o que mais o armazém tem e o que não tem. Nunca
    levanta; sem tickers devolve ``texto`` intacto."""
    bloco = bloco_varredura(classe, tickers)
    if not bloco:
        return texto
    return f"{texto}\n\n{bloco}" if texto else bloco


def resumo_para_tela(classe: str, ticker: str) -> str:
    """Uma frase para a tela: o que o armazém local tem deste ticker, ou por
    que não deu para olhar. Nunca levanta."""
    tk = normalizar_tickers([ticker])
    if not tk:
        return ""
    try:
        inv, _ = inventario(classe, tk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("varredura do armazém (%s/%s) indisponível: %s", classe, tk[0], exc)
        return (f"Armazém local não consultado ({_motivo(exc)}): a existência de dado "
                f"sobre {tk[0]} lá fica em aberto.")
    if inv is None:
        return ("Armazém local não consultado (túnel não configurado neste ambiente): "
                f"a existência de dado sobre {tk[0]} lá fica em aberto.")
    sabe = conhece(inv, tk[0])
    if sabe is False:
        return (f"Varredura feita: o armazém local também não tem nenhuma linha sobre "
                f"{tk[0]} nas fontes consultadas.")
    prefixo = ("Varredura feita: o armazém local conhece este ativo. "
               if sabe else "Varredura sem fonte consultada. ")
    curto = resumo_curto(inv, tk[0])
    return prefixo + curto[:1].upper() + curto[1:] + "."


def nota_para_lacuna(entidade: str | None) -> str:
    """Sufixo para a mensagem de uma lacuna, só da memória deste processo.

    Não vai ao armazém nem ao túnel: a lacuna é registrada no fim da resposta,
    e a varredura do ativo em questão, quando houve, foi feita ao montar o
    contexto segundos antes. Entidade que não foi varrida devolve vazio.
    """
    tk = normalizar_tickers([entidade or ""])
    if not tk:
        return ""
    agora = _relogio()
    with _trava:
        candidatos = [(quando, inv) for (classe, t), (quando, inv, _) in _memoria.items()
                      if t == tk[0] and agora - quando < VALIDADE_S]
    if not candidatos:
        return ""
    _, inv = max(candidatos, key=lambda c: c[0])
    return resumo_curto(inv, tk[0], limite=160)
