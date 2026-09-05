"""Uma guarda só para "isto não pode ser gravado na nuvem".

Existia duas vezes -- ``core.memoria_mercado.repositorio`` e
``data_pipeline.market.b3_precos`` -- e as duas cópias **divergiram**. Sobre o
host ``dfu_warehouse``, que é como se alcança o armazém de dentro do Docker,
uma diz "local" e a outra diz "recusado"; e só uma delas olha a URL inteira
atrás de fragmento de nuvem, então a outra deixa passar destino com host vazio.
Guarda duplicada não fica igual: ela envelhece em direções diferentes, e a
divergência só aparece no dia em que alguém depende da metade errada.

Este módulo carrega a versão estrita, que é a do ``memoria_mercado``. O
``b3_precos`` continua com a sua cópia por ora -- afrouxar a guarda do preço
diário é mudança de segurança que merece o seu próprio PR, não carona no de
notícias.

A regra é uma frase: **local por lista branca de host, remoto por qualquer
sinal de nuvem na URL**, e a URL nunca sai daqui com senha dentro.
"""
from __future__ import annotations

#: Hosts que são o armazém local. ``dfu_warehouse`` é o nome do contêiner.
HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0",
                          "host.docker.internal", "dfu_warehouse"})

#: Fragmentos que denunciam destino gerenciado na nuvem. A lista é deliberada:
#: um destino desconhecido que não bata a lista branca já é recusado pelo host,
#: e estes pegam o caso em que o host vem vazio ou disfarçado na URL.
FRAGMENTOS_REMOTOS = ("supabase.co", "supabase.com", "pooler.supabase",
                      "neon.tech", "amazonaws.com", "render.com",
                      "azure.com", "gcp.")


class DestinoRemotoRecusado(RuntimeError):
    """Gravação pedida em banco que não é o armazém local."""


def url_da_engine(engine) -> str:
    """URL da engine sem a senha. Nunca devolve credencial -- nem para log."""
    try:
        return str(engine.url.render_as_string(hide_password=True))
    except AttributeError:
        return str(getattr(engine, "url", ""))


def e_local(engine) -> bool:
    """``True`` apenas quando o destino é o armazém local.

    A ordem importa: o fragmento de nuvem é checado **antes** da lista branca,
    porque um destino remoto com host vazio na URL passaria pela lista branca
    por omissão. Só depois disso o host sem valor é tratado como local -- é o
    caso do SQLite em arquivo ou memória, local por construção.
    """
    url = getattr(engine, "url", None)
    host = (getattr(url, "host", None) or "").strip().lower()
    texto = url_da_engine(engine).lower()
    if any(fragmento in texto for fragmento in FRAGMENTOS_REMOTOS):
        return False
    if not host:
        return True
    return host in HOSTS_LOCAIS


def exigir_local(engine, *, o_que: str) -> None:
    """Levanta :class:`DestinoRemotoRecusado` se o destino não for local.

    ``o_que`` entra na mensagem porque um "destino recusado" sem dizer o que se
    tentava gravar manda quem lê o log procurar no código.
    """
    if engine is None:
        raise DestinoRemotoRecusado(f"nenhuma engine informada para {o_que}")
    if not e_local(engine):
        raise DestinoRemotoRecusado(
            f"{o_que} só pode ser gravado no armazém local; "
            f"destino recusado: {url_da_engine(engine)}")
