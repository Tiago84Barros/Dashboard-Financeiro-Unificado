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
"""
from __future__ import annotations

TIMEOUT = (3.05, 10)


class ArmazemRemotoIndisponivel(RuntimeError):
    """O serviço está configurado, mas não entregou a leitura."""


def _config() -> tuple[str, str]:
    from core.config import _get_secret

    return (_get_secret("ARMAZEM_API_URL").rstrip("/"),
            _get_secret("ARMAZEM_API_TOKEN"))


def configurado() -> bool:
    url, token = _config()
    return bool(url and token)


def _ler(caminho: str, params: dict | None = None) -> dict | None:
    url, token = _config()
    if not (url and token):
        return None
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
            f"HTTP {resp.status_code}" + (f": {motivo[:160]}" if motivo else ""))
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


def macro_recente() -> list[dict] | None:
    """Última observação por série, como ``latest_macro_context``."""
    corpo = _ler("/macro/recente")
    if corpo is None:
        return None
    fatos = corpo.get("fatos")
    if not isinstance(fatos, list):
        raise ArmazemRemotoIndisponivel("resposta sem a lista de fatos")
    return [f for f in fatos if isinstance(f, dict)]
