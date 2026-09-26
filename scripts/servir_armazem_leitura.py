"""Serviço HTTP só de leitura sobre o armazém local, para a produção alcançar.

O app publicado na Streamlit Cloud não enxerga o Docker desta máquina: por isso
o contexto das LLMs em produção só via a vitrine de notícias do Supabase (um
recorte por ativo) e nunca o acervo inteiro nem as séries do ``macro_staging``.
Este serviço fecha essa lacuna sem abrir o Postgres para a internet.

O desenho é deliberadamente estreito:

* **Só dado público.** Notícias avaliadas e séries macro. Nada de finanças
  pessoais, carteira ou espelho do Supabase -- esses já estão no Supabase e,
  se vazassem por aqui, vazariam dado de uma pessoa.
* **Só leitura, em duas camadas.** Não há rota que escreva, e a sessão do
  Postgres abre com ``default_transaction_read_only=on``: um defeito futuro
  numa rota nova recebe erro do banco em vez de gravar.
* **Só localhost.** O servidor escuta em ``127.0.0.1``; quem o expõe é o túnel
  (``cloudflared``), que o usuário instala e controla. Desligar o túnel ou o PC
  desliga o acesso, e o app cai para o Supabase dizendo que caiu.
* **Token obrigatório.** Sem ``ARMAZEM_API_TOKEN`` com 32+ caracteres o serviço
  não sobe. Comparação em tempo constante.

Uso:
    python scripts/servir_armazem_leitura.py            # 127.0.0.1:8787
    python scripts/servir_armazem_leitura.py --porta 9000

Rotas (todas GET, todas exigem ``Authorization: Bearer <token>``):
    /saude                              -- o banco responde?
    /noticias/recentes?limite=150&dias=3
    /noticias/ativos?tickers=PETR4,VALE3&janela_dias=30&as_of=<ISO>
    /macro/recente
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("armazem_leitura")

PORTA_PADRAO = 8787
TOKEN_MINIMO = 32
#: Tetos das rotas. A tela pede 150 itens de 3 dias; o teto evita que um
#: token vazado vire um dump do acervo inteiro numa chamada só.
LIMITE_MAX_NOTICIAS = 500
DIAS_MAX_NOTICIAS = 30.0
#: Tetos da rota por ativo. A carteira-modelo mais larga pede ~40 tickers; a
#: janela da conjuntura é de 30 dias.
TICKERS_MAX = 80
JANELA_MAX_ATIVOS = 30

_ENGINES: dict[str, object] = {}


def _json_padrao(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    return str(valor)


def engine_leitura(url: str):
    """Engine com a sessão travada em somente leitura, uma por URL."""
    if url not in _ENGINES:
        from sqlalchemy import create_engine

        _ENGINES[url] = create_engine(
            url, pool_pre_ping=True, pool_size=2, max_overflow=2,
            connect_args={"connect_timeout": 10,
                          "options": "-c default_transaction_read_only=on"})
    return _ENGINES[url]


def _url_noticias() -> str:
    from core.noticias.destino import url_acervo

    return url_acervo()


def _url_macro() -> str:
    from core.config import settings

    return settings.MACRO_LOCAL_DB_URL or ""


def _numero(params: dict, chave: str, padrao: float, teto: float) -> float:
    try:
        valor = float(params.get(chave, [padrao])[0])
    except (TypeError, ValueError):
        return padrao
    return max(0.0, min(valor, teto)) if valor == valor else padrao


def rota_saude(_params) -> tuple[int, dict]:
    from sqlalchemy import text

    url = _url_noticias()
    if not url:
        return 503, {"ok": False, "erro": "armazém não configurado nesta máquina"}
    with engine_leitura(url).connect() as conn:
        conn.execute(text("SELECT 1"))
    return 200, {"ok": True}


def rota_noticias(params) -> tuple[int, dict]:
    from core.noticias.armazenamento import ler_recentes

    url = _url_noticias()
    if not url:
        return 503, {"erro": "acervo de notícias não configurado nesta máquina"}
    limite = int(_numero(params, "limite", 150, LIMITE_MAX_NOTICIAS))
    dias = _numero(params, "dias", 3, DIAS_MAX_NOTICIAS)
    itens = ler_recentes(limite, dias=dias, engine=engine_leitura(url))
    return 200, {"itens": list(itens), "limite": limite, "dias": dias}


def rota_noticias_ativos(params) -> tuple[int, dict]:
    from core.conjuntura.ponte import JANELA_NOTICIAS_DIAS, linhas_do_acervo

    url = _url_noticias()
    if not url:
        return 503, {"erro": "acervo de notícias não configurado nesta máquina"}
    tickers = [t.strip().upper()
               for t in ",".join(params.get("tickers", [])).split(",") if t.strip()]
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return 400, {"erro": "informe tickers"}
    if len(tickers) > TICKERS_MAX:
        return 400, {"erro": f"no máximo {TICKERS_MAX} tickers por chamada"}
    janela = int(_numero(params, "janela_dias", JANELA_NOTICIAS_DIAS, JANELA_MAX_ATIVOS))
    agora = datetime.now(timezone.utc)
    try:
        as_of = datetime.fromisoformat(params.get("as_of", [""])[0])
    except ValueError:
        as_of = agora
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    # Corte no futuro não traz nada a mais, mas o teto deixa a regra explícita.
    as_of = min(as_of, agora)
    linhas = linhas_do_acervo(engine_leitura(url), simbolos=tickers,
                              as_of=as_of, janela_dias=janela)
    return 200, {"linhas": linhas, "janela_dias": janela,
                 "as_of": as_of.isoformat(), "tickers": len(tickers)}


def rota_macro(_params) -> tuple[int, dict]:
    from core.macro_data.context import latest_macro_context

    url = _url_macro()
    if not url:
        return 503, {"erro": "armazém macro não configurado nesta máquina"}
    return 200, {"fatos": list(latest_macro_context(engine_leitura(url)))}


ROTAS = {
    "/saude": rota_saude,
    "/noticias/recentes": rota_noticias,
    "/noticias/ativos": rota_noticias_ativos,
    "/macro/recente": rota_macro,
}


def token_confere(cabecalho: str | None, token: str) -> bool:
    if not token or not cabecalho or not cabecalho.startswith("Bearer "):
        return False
    return hmac.compare_digest(cabecalho[len("Bearer "):].encode(), token.encode())


def fabricar_handler(token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ArmazemLeitura/1"
        sys_version = ""

        def _responder(self, status: int, corpo: dict) -> None:
            dados = json.dumps(corpo, ensure_ascii=False, default=_json_padrao).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self):  # noqa: N802 - nome da API do http.server
            if not token_confere(self.headers.get("Authorization"), token):
                self._responder(401, {"erro": "não autorizado"})
                return
            partes = urlsplit(self.path)
            rota = ROTAS.get(partes.path.rstrip("/") or "/")
            if rota is None:
                self._responder(404, {"erro": "rota inexistente"})
                return
            try:
                status, corpo = rota(parse_qs(partes.query))
            except Exception as exc:  # noqa: BLE001 - vira 503 nomeado
                logger.warning("rota %s falhou: %s", partes.path, exc)
                status, corpo = 503, {"erro": f"{type(exc).__name__}: "
                                              f"{str(exc).splitlines()[0][:200] if str(exc) else ''}"}
            self._responder(status, corpo)

        def _recusar(self):
            self._responder(405, {"erro": "somente GET"})

        do_POST = do_PUT = do_DELETE = do_PATCH = _recusar  # noqa: N815

        def log_message(self, formato, *args):
            # O log padrão imprime a linha da requisição; o token vai no
            # cabeçalho, não na URL, então nada sensível sai aqui.
            logger.info("%s " + formato, self.address_string(), *args)

    return Handler


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--porta", type=int, default=PORTA_PADRAO)
    p.add_argument("--host", default="127.0.0.1",
                   help="Endereço de escuta. Mantenha 127.0.0.1: o túnel expõe.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    from core.config import _get_secret

    token = _get_secret("ARMAZEM_API_TOKEN")
    if len(token) < TOKEN_MINIMO:
        print(f"ARMAZEM_API_TOKEN ausente ou com menos de {TOKEN_MINIMO} caracteres. "
              "Gere um com: python -c \"import secrets; print(secrets.token_urlsafe(48))\"",
              file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Aviso: escutando em {args.host}, fora do localhost.", file=sys.stderr)

    servidor = ThreadingHTTPServer((args.host, args.porta), fabricar_handler(token))
    logger.info("armazém só-leitura em http://%s:%s", args.host, args.porta)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
