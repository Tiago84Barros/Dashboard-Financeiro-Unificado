"""Cliente do serviço só-leitura do armazém (``scripts/servir_armazem_leitura.py``).

É o caminho do meio entre o armazém alcançado direto (desenvolvimento) e a
vitrine do Supabase (produção sem túnel). Existe para a produção ler o acervo
de notícias e as séries macro quando o PC do usuário e o túnel estão ligados.

Duas regras de desenho:

* **Não configurado é ``None``; configurado e fora do ar é exceção.** São
  estados diferentes e o contexto das LLMs os descreve de jeitos diferentes:
  o primeiro é o normal de quem não montou o túnel, o segundo é uma falha que o
  modelo precisa saber para não tratar a vitrine como o noticiário inteiro.
* **Tempo curto.** O PC desligado é o caso comum, não o excepcional, e a tela
  espera por esta chamada antes de responder ao usuário.

E duas memórias de processo, porque um relatório de carteira com N empresas
monta o contexto N+1 vezes em poucos minutos:

* **Resposta boa vale ``VALIDADE_S``.** As notícias por ativo guardam-se por
  ticker e por corte (``as_of``): a leitura da carteira inteira, que vem depois
  das empresas, reaproveita o que cada empresa já trouxe. O corte entra na
  chave de propósito — reaproveitar a leitura de outro corte faria o carimbo
  "Corte:" do prompt mentir.
* **Falha de quem está fora do ar vale ``PAUSA_APOS_FALHA_S``.** Sem ela, o PC
  desligado custava um timeout por empresa; com ela, custa um por relatório, e
  as chamadas seguintes levantam na hora com o mesmo motivo e a idade dele.
  Erro 4xx não entra: é defeito do pedido, não sinal de que o serviço caiu.
"""
from __future__ import annotations

import copy
import threading
import time

TIMEOUT = (3.05, 10)
VALIDADE_S = 300
PAUSA_APOS_FALHA_S = 60

_trava = threading.Lock()
_respostas: dict[tuple, tuple[float, dict]] = {}
_por_ativo: dict[tuple, tuple[float, list[dict]]] = {}
_falhas: dict[str, tuple[float, str]] = {}


class ArmazemRemotoIndisponivel(RuntimeError):
    """O serviço está configurado, mas não entregou a leitura."""

    def __init__(self, motivo: str, *, fora_do_ar: bool = True) -> None:
        super().__init__(motivo)
        self.fora_do_ar = fora_do_ar


def _relogio() -> float:
    return time.monotonic()


def _limpar_memoria() -> None:
    """Esquece respostas e falhas guardadas (testes; troca de configuração)."""
    with _trava:
        _respostas.clear()
        _por_ativo.clear()
        _falhas.clear()


def _podar(memoria: dict, agora: float) -> None:
    for chave in [c for c, (quando, _) in memoria.items()
                  if agora - quando >= VALIDADE_S]:
        del memoria[chave]


def _config() -> tuple[str, str]:
    from core.config import _get_secret

    return (_get_secret("ARMAZEM_API_URL").rstrip("/"),
            _get_secret("ARMAZEM_API_TOKEN"))


def configurado() -> bool:
    url, token = _config()
    return bool(url and token)


def _ler(caminho: str, params: dict | None = None, *,
         guardar: bool = True) -> dict | None:
    url, token = _config()
    if not (url and token):
        return None
    chave = (url, caminho, tuple(sorted((params or {}).items())))
    agora = _relogio()
    with _trava:
        salvo = _respostas.get(chave) if guardar else None
        falha = _falhas.get(url)
    if salvo is not None and agora - salvo[0] < VALIDADE_S:
        return copy.deepcopy(salvo[1])
    if falha is not None and agora - falha[0] < PAUSA_APOS_FALHA_S:
        raise ArmazemRemotoIndisponivel(
            f"{falha[1]} (falhou há {int(agora - falha[0])} s; nova tentativa "
            f"depois de {PAUSA_APOS_FALHA_S} s)")
    try:
        corpo = _buscar(url, token, caminho, params)
    except ArmazemRemotoIndisponivel as exc:
        if exc.fora_do_ar:
            with _trava:
                _falhas[url] = (_relogio(), str(exc))
        raise
    with _trava:
        _falhas.pop(url, None)
        if guardar:
            _podar(_respostas, agora)
            _respostas[chave] = (_relogio(), corpo)
    return copy.deepcopy(corpo)


def _buscar(url: str, token: str, caminho: str, params: dict | None) -> dict:
    import requests

    try:
        resp = requests.get(f"{url}{caminho}", params=params or {},
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ArmazemRemotoIndisponivel(
            f"sem resposta ({type(exc).__name__}) — PC ou túnel desligado?") from exc
    if resp.status_code != 200:
        try:
            motivo = str(resp.json().get("erro") or "")
        except ValueError:
            motivo = ""
        raise ArmazemRemotoIndisponivel(
            f"HTTP {resp.status_code}" + (f": {motivo[:160]}" if motivo else ""),
            fora_do_ar=resp.status_code >= 500)
    try:
        corpo = resp.json()
    except ValueError as exc:
        # O túnel do Cloudflare devolve página HTML 200 em alguns erros de
        # origem; sem esta checagem, ela viraria "zero notícias".
        raise ArmazemRemotoIndisponivel("resposta não é JSON") from exc
    if not isinstance(corpo, dict):
        raise ArmazemRemotoIndisponivel("resposta em formato inesperado")
    return corpo


def noticias_recentes(limite: int = 150, dias: float = 3) -> list[dict] | None:
    """Itens avaliados do acervo, como ``ler_recentes``; ``None`` sem configuração."""
    corpo = _ler("/noticias/recentes", {"limite": int(limite), "dias": dias})
    if corpo is None:
        return None
    itens = corpo.get("itens")
    if not isinstance(itens, list):
        raise ArmazemRemotoIndisponivel("resposta sem a lista de itens")
    return [i for i in itens if isinstance(i, dict)]


def noticias_por_ativo(simbolos, *, as_of, janela_dias: int) -> list[dict] | None:
    """Linhas cruas do acervo por ticker, como ``ponte.linhas_do_acervo``.

    A agregação fica do lado do app, e não do servidor, para que a fórmula seja
    uma só: o PC servindo uma versão velha do código não muda a nota.
    """
    url, token = _config()
    if not (url and token):
        return None
    tickers = list(dict.fromkeys(
        str(s).strip().upper() for s in simbolos if str(s).strip()))
    corte, janela = as_of.isoformat(), int(janela_dias)
    agora = _relogio()
    guardadas: dict[str, list[dict]] = {}
    with _trava:
        for tk in tickers:
            salvo = _por_ativo.get((url, tk, corte, janela))
            if salvo is not None and agora - salvo[0] < VALIDADE_S:
                guardadas[tk] = salvo[1]
    faltam = [tk for tk in tickers if tk not in guardadas]
    avulsas: list[dict] = []
    if faltam:
        corpo = _ler("/noticias/ativos", {
            "tickers": ",".join(faltam),
            "as_of": corte,
            "janela_dias": janela,
        }, guardar=False)
        if corpo is None:
            return None
        linhas = corpo.get("linhas")
        if not isinstance(linhas, list):
            raise ArmazemRemotoIndisponivel("resposta sem a lista de linhas")
        # Ticker sem linha também é resposta: guardado vazio, não relido.
        novas: dict[str, list[dict]] = {tk: [] for tk in faltam}
        for linha in linhas:
            if not isinstance(linha, dict):
                continue
            destino = novas.get(str(linha.get("simbolo") or "").upper())
            (destino if destino is not None else avulsas).append(linha)
        with _trava:
            _podar(_por_ativo, agora)
            for tk, lista in novas.items():
                _por_ativo[(url, tk, corte, janela)] = (_relogio(), lista)
        guardadas.update(novas)
    saida = [linha for tk in tickers for linha in guardadas[tk]] + avulsas
    return copy.deepcopy(saida)


def macro_recente() -> list[dict] | None:
    """Última observação por série, como ``latest_macro_context``."""
    corpo = _ler("/macro/recente")
    if corpo is None:
        return None
    fatos = corpo.get("fatos")
    if not isinstance(fatos, list):
        raise ArmazemRemotoIndisponivel("resposta sem a lista de fatos")
    return [f for f in fatos if isinstance(f, dict)]
