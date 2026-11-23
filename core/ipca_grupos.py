"""IPCA por grupo e pesos do IBGE (SIDRA, tabela 7060).

A série mora num arquivo versionado, ``data/public/ipca_grupos.json.gz``,
publicado por ``scripts/publicar_ipca_grupos.py`` (agenda de publicação,
alvo ``ipca_grupos``). A tela só lê o arquivo; nunca chama o IBGE. Não há
tabela no Supabase: o plano free está no teto (497/500 MB em 23/11/2026).

Variáveis da tabela 7060: 63 = variação mensal (%), 66 = peso mensal (%).
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from core.categorias import normalizar

logger = logging.getLogger(__name__)

CAMINHO_IPCA_GRUPOS = (Path(__file__).resolve().parents[1]
                       / "data" / "public" / "ipca_grupos.json.gz")
FONTE = "ibge_sidra_7060"
SCHEMA = 1
INDICE_GERAL = "Índice geral"

GRUPOS_SIDRA: dict[str, str] = {
    "7169": INDICE_GERAL,
    "7170": "Alimentação e bebidas",
    "7445": "Habitação",
    "7486": "Artigos de residência",
    "7558": "Vestuário",
    "7625": "Transportes",
    "7660": "Saúde e cuidados pessoais",
    "7712": "Despesas pessoais",
    "7766": "Educação",
    "7786": "Comunicação",
}
VAR_VARIACAO = "63"
VAR_PESO = "66"
URL_SIDRA = ("https://apisidra.ibge.gov.br/values/t/7060/n1/all/v/63,66/p/all/c315/"
             + ",".join(GRUPOS_SIDRA))

# Palavra-chave (já normalizada) → grupo. Casa por palavra inteira, para
# "gas" não casar dentro de "gastos". A primeira regra que casa vence.
_PALAVRAS_GRUPO: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Habitação", ("moradia", "condominio", "luz", "agua", "gas", "aluguel",
                   "casa & construcao")),
    ("Alimentação e bebidas", ("alimentacao", "mercado", "supermercado")),
    ("Transportes", ("transporte", "combustivel", "automotivo")),
    ("Saúde e cuidados pessoais", ("saude", "bem estar", "cuidados pessoais")),
    ("Educação", ("educacao",)),
    ("Despesas pessoais", ("lazer", "pets", "seguros", "despesas domesticas",
                           "tarifas & anuidade")),
    ("Vestuário", ("vestuario",)),
    ("Comunicação", ("assinaturas", "internet", "telefone")),
)


@dataclass(frozen=True)
class SerieIpcaGrupos:
    """``variacao[(mes, grupo)]`` e ``peso[(mes, grupo)]`` em % (0.48 = 0,48%).

    ``mes`` é sempre o dia 1 do mês.
    """
    variacao: dict = field(default_factory=dict)
    peso: dict = field(default_factory=dict)
    gerado_em: datetime | None = None

    def meses(self) -> list[date]:
        """Meses com Índice geral publicado, em ordem."""
        return sorted({m for (m, g) in self.variacao if g == INDICE_GERAL})

    def ultimo_mes(self) -> date | None:
        meses = self.meses()
        return meses[-1] if meses else None


def _float(valor: Any) -> float | None:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_sidra(linhas: list) -> SerieIpcaGrupos:
    """Converte a resposta JSON do SIDRA. Ignora cabeçalho, grupo desconhecido
    e valor não numérico ("...", "-", "X")."""
    variacao: dict = {}
    peso: dict = {}
    for linha in linhas:
        if not isinstance(linha, dict):
            continue
        periodo = str(linha.get("D3C", ""))
        grupo = GRUPOS_SIDRA.get(str(linha.get("D4C", "")))
        valor = _float(linha.get("V"))
        if not re.fullmatch(r"\d{6}", periodo) or grupo is None or valor is None:
            continue
        try:
            mes = date(int(periodo[:4]), int(periodo[4:]), 1)
        except ValueError:
            continue
        variavel = str(linha.get("D2C", ""))
        if variavel == VAR_VARIACAO:
            variacao[(mes, grupo)] = valor
        elif variavel == VAR_PESO:
            peso[(mes, grupo)] = valor
    return SerieIpcaGrupos(variacao=variacao, peso=peso)


def serializar_ipca_grupos(serie: SerieIpcaGrupos, gerado_em: datetime) -> bytes:
    """Gzip determinístico (``mtime=0``, chaves ordenadas)."""
    chaves = sorted(set(serie.variacao) | set(serie.peso))
    linhas = [[f"{m:%Y-%m}", g, serie.variacao.get((m, g)), serie.peso.get((m, g))]
              for m, g in chaves]
    doc = {"schema": SCHEMA, "fonte": FONTE, "gerado_em": gerado_em.isoformat(),
           "linhas": linhas}
    texto = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return gzip.compress(texto.encode("utf-8"), mtime=0)


def desserializar_ipca_grupos(dados: bytes) -> SerieIpcaGrupos:
    doc = json.loads(gzip.decompress(dados).decode("utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"schema desconhecido: {doc.get('schema')!r}")
    variacao: dict = {}
    peso: dict = {}
    for mes_txt, grupo, var, pes in doc["linhas"]:
        mes = date(int(mes_txt[:4]), int(mes_txt[5:7]), 1)
        if var is not None:
            variacao[(mes, grupo)] = float(var)
        if pes is not None:
            peso[(mes, grupo)] = float(pes)
    gerado = doc.get("gerado_em")
    return SerieIpcaGrupos(variacao=variacao, peso=peso,
                           gerado_em=datetime.fromisoformat(gerado) if gerado else None)


def ler_ipca_grupos_publicado(caminho: Path | None = None) -> SerieIpcaGrupos:
    """Lê o arquivo publicado. Ausente ou ilegível → série vazia (quem chama
    nomeia a falha); nunca levanta."""
    alvo = Path(caminho) if caminho is not None else CAMINHO_IPCA_GRUPOS
    try:
        return desserializar_ipca_grupos(alvo.read_bytes())
    except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
        logger.info("ipca_grupos: arquivo %s ausente ou ilegível (%s)", alvo, exc)
        return SerieIpcaGrupos()


def _obter_requests(url: str, timeout: float) -> Any:
    import requests

    resposta = requests.get(url, timeout=timeout)
    resposta.raise_for_status()
    return resposta.json()


def baixar_sidra(*, timeout: float = 45.0,
                 obter: Callable[[str, float], Any] | None = None,
                 ) -> tuple[SerieIpcaGrupos, str | None]:
    """Baixa a tabela 7060. Devolve ``(serie, erro)``; ``erro`` nomeia a falha."""
    try:
        dados = (obter or _obter_requests)(URL_SIDRA, timeout)
    except Exception as exc:  # noqa: BLE001 -- a falha volta nomeada ao chamador
        return SerieIpcaGrupos(), f"{type(exc).__name__}: {exc}"
    if not isinstance(dados, list):
        return SerieIpcaGrupos(), (
            f"resposta do SIDRA não é uma lista ({type(dados).__name__})")
    serie = parse_sidra(dados)
    if not serie.variacao:
        return serie, "resposta do SIDRA sem nenhuma linha válida"
    return serie, None


def grupo_da_categoria(nome: str) -> tuple[str, bool]:
    """Grupo do IPCA para uma categoria do app: ``(grupo, definido)``.

    Sem palavra-chave conhecida → ``(INDICE_GERAL, False)``: a categoria usa o
    Índice geral e a tela marca "sem grupo definido".
    """
    texto = normalizar(nome)
    for grupo, palavras in _PALAVRAS_GRUPO:
        for palavra in palavras:
            if re.search(rf"(?<!\w){re.escape(palavra)}(?!\w)", texto):
                return grupo, True
    return INDICE_GERAL, False
