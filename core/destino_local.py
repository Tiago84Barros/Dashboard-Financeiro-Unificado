"""Uma guarda só para "isto não pode ser gravado na nuvem".

Existia **três** vezes -- aqui, em ``core.memoria_mercado.repositorio`` e em
``data_pipeline.market.b3_precos`` -- e as cópias divergiram. Guarda duplicada
não fica igual: ela envelhece em direções diferentes, e a divergência só
aparece no dia em que alguém depende da metade errada.

A divergência foi medida em 05/09/2026, e ela tinha as **duas** direções:

============================  ==========  ===============
destino                       b3_precos   as outras duas
============================  ==========  ===============
``dfu_warehouse`` (Docker)    RECUSA      aceita
``localhost``                 aceita      aceita
Supabase, host na URL         RECUSA      RECUSA
Supabase, host na query       **aceita**  RECUSA
============================  ==========  ===============

A primeira linha é falso negativo: chato, seguro. A última é o problema --
``b3_precos`` só olhava ``url.host`` e desistia quando ele vinha vazio, então
uma URL como ``postgresql://u:s@/p?host=/x/db.a.supabase.co`` passava. São ~1 GB
de linhas apontados para uma instância com 23 MB de folga.

Desde 05/09/2026 as três usam esta implementação, e as outras duas apenas
reexportam os nomes para não quebrar quem já os importava de lá. Unificar
**apertou** a guarda do preço diário em vez de afrouxá-la, que é a única
direção em que valia fazer isso sem PR próprio.

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
