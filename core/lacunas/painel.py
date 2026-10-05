"""
core/lacunas/painel.py
O que a aba Configuracoes -> Restricoes mostra ao administrador, e a decisao que
ele toma sobre cada item.

A aba existe desde 05/10/2026, quando restricoes e detalhes tecnicos sairam da
tela de uso (``design/lacunas.py``). Ela le o MESMO log que o corretor diario
le, pelas mesmas funcoes (``core.lacunas.leitura`` + ``fila.fundir``): um
painel com leitura propria acabaria mostrando uma fila e o corretor
trabalhando outra.

Onde a decisao e gravada segue onde o log mora:

* na Cloud (``app_lacunas``), com a nota marcada ``[admin AAAA-MM-DD]``; o
  sincronizador local reconhece a marca e traz a decisao para o estado local
  (``fila.importar_decisoes_admin``) em vez de sobrescreve-la;
* na maquina local, tambem no ``estado.json`` -- e ele que vence na fusao.

Nada aqui levanta para a view: falha de fonte vira texto em ``fontes``, como no
``abertas.json`` do sincronizador.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.lacunas import destino, fila, leitura
from core.lacunas.evento import e_detalhe

_log = logging.getLogger(__name__)

PASTA_LOCAL = destino.ARQUIVO_LOCAL.parent

NATUREZAS = ("Restrição", "Erro", "Detalhe técnico")

#: O que cada status significa para quem decide. ``em_pr`` so o corretor poe.
STATUS_ROTULO = {
    "aberta": "Pendente",
    "incerta": "Em análise",
    "em_pr": "Correção em PR",
    "legitima": "Aceita",
    "resolvida": "Resolvida",
}
STATUS_DECIDIVEIS = ("aberta", "incerta", "legitima", "resolvida")

#: Arquivo de view -> nome da tela como aparece na barra lateral.
_TELAS = {
    "views/dashboard_geral.py": "Dashboard Geral",
    "views/controle_financeiro.py": "Controle Financeiro",
    "views/investimentos": "Investimentos",
    "views/inteligencia_ativos": "Investimentos · Inteligência dos Ativos",
    "views/inteligencia_mercado.py": "Investimentos · Inteligência de Mercado",
    "views/carteira.py": "Investimentos · Carteira",
    "views/proventos.py": "Investimentos · Proventos",
    "views/ir_renda_variavel.py": "Investimentos · IR",
    "views/metas.py": "Investimentos · Metas",
    "views/alertas.py": "Alertas",
    "views/empresas_b3.py": "Empresas B3",
    "views/portfolio_b3": "Empresas B3 · Portfólio",
    "views/analise_portfolio_b3.py": "Empresas B3 · Portfólio",
    "views/empresas_americanas.py": "Empresas Americanas",
    "views/analise_portfolio_us.py": "Empresas Americanas · Portfólio",
    "views/fiis.py": "Seleção de FIIs",
    "views/portfolio_global.py": "Portfólio Global",
    "views/macro_internacional.py": "Portfólio Global · Macro",
    "views/configuracoes": "Configurações",
    "design/inteligencia.py": "Investimentos · Inteligência dos Ativos",
    "design/chat_": "Chats com a LLM",
    "core/llm": "Chats com a LLM",
}


def tela_do_modulo(modulo: str | None) -> str:
    """Nome legivel da tela a partir de ``caminho.py:funcao``; o prefixo mais
    longo que casa vence (``views/portfolio_b3_safras.py`` e Portfolio)."""
    caminho = (modulo or "").split(":", 1)[0].replace("\\", "/")
    if not caminho:
        return "—"
    casados = [p for p in _TELAS if caminho.startswith(p)]
    if casados:
        return _TELAS[max(casados, key=len)]
    if caminho.startswith(("core/", "data_pipeline/", "etl/")):
        return "Motor de cálculo"
    return caminho


def natureza(item: dict) -> str:
    if item.get("fonte") == "excecao":
        return "Erro"
    if e_detalhe(item.get("codigo")):
        return "Detalhe técnico"
    return "Restrição"


@dataclass
class Painel:
    itens: list[dict] = field(default_factory=list)
    fontes: dict[str, str] = field(default_factory=dict)
    gerado_em: str = ""


def carregar(engine=None, *, pasta: Path | None = None,
             agora: datetime | None = None, ler_nuvem: bool = True) -> Painel:
    """Funde log local e ``app_lacunas`` do jeito que o sincronizador funde,
    sem gravar nada. ``engine=None`` com ``ler_nuvem`` usa ``get_engine()``."""
    agora = agora or datetime.now(timezone.utc)
    pasta = PASTA_LOCAL if pasta is None else Path(pasta)
    fontes: dict[str, str] = {}

    try:
        eventos = leitura.eventos_locais(pasta) if pasta.exists() else []
        local = fila.agregar_eventos(eventos, agora)
        fontes["local"] = f"ok ({len(local)} registros)" if pasta.exists() else "sem log local"
    except Exception as exc:  # noqa: BLE001 - fonte que falha e nomeada
        local = {}
        fontes["local"] = f"indisponivel: {type(exc).__name__}"
        _log.warning("log local de lacunas nao lido", exc_info=True)

    guardado = leitura.ler_json(pasta / "estado.json", {}) if pasta.exists() else {}
    estado = guardado.get("lacunas", {}) if isinstance(guardado, dict) else {}
    fotos = guardado.get("fotos_cloud", {}) if isinstance(guardado, dict) else {}

    cloud: list[dict] = []
    if ler_nuvem:
        try:
            if engine is None:
                from core.database import get_engine

                engine = get_engine()
            if engine is None:
                fontes["cloud"] = "indisponivel: banco nao configurado"
            else:
                cloud = leitura.ler_cloud(engine)
                fontes["cloud"] = f"ok ({len(cloud)} registros)"
        except Exception as exc:  # noqa: BLE001 - sem nuvem o painel sai so com o local
            fontes["cloud"] = f"indisponivel: {type(exc).__name__}"
            _log.warning("app_lacunas nao lida para o painel", exc_info=True)

    itens = fila.fundir(local, cloud, estado, fotos, agora)
    for item in itens:
        item["natureza"] = natureza(item)
        item["tela"] = tela_do_modulo(item.get("modulo"))
    # Mais recente primeiro; depois, estavel: restricao antes de detalhe, e a
    # de maior prioridade na fila do corretor no topo.
    itens.sort(key=lambda i: i.get("ultima_vez") or "", reverse=True)
    itens.sort(key=lambda i: (i["natureza"] == "Detalhe técnico", -i.get("prioridade", 0)))
    return Painel(itens=itens, fontes=fontes, gerado_em=agora.isoformat())


def nota_admin(texto: str | None, agora: datetime) -> str:
    corpo = (texto or "").strip() or "sem observacao"
    return f"{fila.PREFIXO_NOTA_ADMIN}{agora.date().isoformat()}] {corpo}"[:500]


_SQL_DECIDIR = """
UPDATE app_lacunas
SET status = :status, nota_triagem = :nota_triagem
WHERE impressao = :impressao
"""


def decidir(impressao: str, status: str, observacao: str | None = None, *,
            engine=None, pasta: Path | None = None,
            agora: datetime | None = None, na_nuvem: bool = True) -> dict[str, str]:
    """Grava a decisao do administrador. Devolve ``{destino: resultado}``.

    Levanta ``ValueError`` so para status que o administrador nao pode dar
    (``em_pr`` e do corretor, que anexa o PR).
    """
    if status not in STATUS_DECIDIVEIS:
        raise ValueError(f"status nao decidivel pela tela: {status!r}")
    agora = agora or datetime.now(timezone.utc)
    pasta = PASTA_LOCAL if pasta is None else Path(pasta)
    nota = nota_admin(observacao, agora)
    resultado: dict[str, str] = {}

    if na_nuvem:
        try:
            if engine is None:
                from core.database import get_engine

                engine = get_engine()
            if engine is None:
                resultado["cloud"] = "indisponivel"
            else:
                from sqlalchemy import text

                with engine.begin() as con:
                    n = con.execute(text(_SQL_DECIDIR), {
                        "status": status, "nota_triagem": nota, "impressao": impressao,
                    }).rowcount
                resultado["cloud"] = "ok" if n else "sem linha na nuvem"
        except Exception as exc:  # noqa: BLE001
            resultado["cloud"] = f"falhou: {type(exc).__name__}"
            _log.warning("decisao nao gravada em app_lacunas", exc_info=True)

    # O estado local so existe onde o sincronizador roda (a maquina com o
    # armazem). Na Cloud a pasta nao existe e nada e criado.
    arquivo = pasta / "estado.json"
    if arquivo.exists():
        try:
            guardado = leitura.ler_json(arquivo, {})
            estado = guardado.setdefault("lacunas", {})
            fila.marcar(estado, impressao, status, nota=nota, agora=agora)
            if status != "resolvida":
                estado[impressao].pop("resolvida_em", None)
            leitura.gravar_json(arquivo, guardado)
            resultado["local"] = "ok"
        except Exception as exc:  # noqa: BLE001
            resultado["local"] = f"falhou: {type(exc).__name__}"
            _log.warning("decisao nao gravada no estado local", exc_info=True)
    return resultado
