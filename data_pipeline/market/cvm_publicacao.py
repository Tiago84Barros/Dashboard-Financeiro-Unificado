# -*- coding: utf-8 -*-
"""Data de entrega das DFP/ITR na CVM: o `published_at` que faltava ao PIT da B3.

A-155. `core.b3_validation.validation_readiness` reprova a validacao temporal
da B3 com o bloqueador "PIT estrito sem published_at/revisoes CVM", e ele nunca
teve como sair: `market.calculated_metric_vintages.availability_quality` so
sabia produzir dois valores. `migration_baseline` (97.236 linhas) e literalmente
"nao sei quando isso ficou disponivel"; `first_seen_proxy` (2.918) e "foi a
primeira vez que EU vi", que mede o dia em que o ETL rodou, nao o dia em que o
mercado soube. Nenhum dos dois sustenta backtest: se o ETL rodou hoje, o proxy
diz que o balanco de 2019 ficou disponivel hoje.

O dado real e publico e existe desde sempre. O arquivo-cabecalho anual da CVM
(`dfp_cia_aberta_YYYY.csv` dentro do ZIP) traz uma linha por documento
entregue, com `DT_RECEB` -- a data em que a companhia protocolou. E o instante
em que a informacao passou a ser conhecivel.

Duas decisoes de metodologia estao gravadas aqui, e as duas sao conservadoras:

**Reapresentacao manda.** Uma companhia pode entregar a DFP em marco e
reapresenta-la em agosto. O numero que o banco guarda hoje e o da versao mais
recente; portanto foi em agosto que ESTE numero ficou conhecivel, nao em marco.
`disponivel_em` usa o MAIOR `DT_RECEB` do exercicio. `primeira_entrega_em`
guarda o menor, para que a escolha seja auditavel e reversivel -- quem quiser a
leitura otimista tem o dado, e nao precisa rebaixar o conservador.

**Exercicio e `DT_REFER`, nao o ano do arquivo.** O ZIP de 2024 contem
documentos com `DT_REFER=2024-12-31` entregues em 2025. Amarrar ao ano do
arquivo produziria uma serie deslocada de um ano -- o erro exato que a
validacao PIT existe para pegar.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

logger = logging.getLogger(__name__)

DFP_ZIP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{year}.zip"
ITR_ZIP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{year}.zip"

_UA = {"User-Agent": "DashboardFinanceiro/1.0"}


@dataclass(frozen=True)
class Entrega:
    """Um exercicio de uma companhia, com quando ele ficou conhecivel."""

    codigo_cvm: int
    exercicio: int
    categoria: str
    disponivel_em: date
    primeira_entrega_em: date
    versoes: int

    @property
    def reapresentado(self) -> bool:
        return self.disponivel_em != self.primeira_entrega_em


def _data(valor) -> date | None:
    texto = str(valor or "").strip()
    if not texto or texto in {"0000-00-00", "nan"}:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _codigo(valor) -> int | None:
    """`CD_CVM` vem zero-preenchido ('001023'); o banco guarda o inteiro."""
    texto = str(valor or "").strip()
    if not texto.isdigit():
        return None
    codigo = int(texto)
    return codigo or None


def nome_cabecalho(categoria: str, year: int) -> str:
    return f"{categoria.lower()}_cia_aberta_{year}.csv"


def _linhas(conteudo: bytes, nome: str):
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        if nome not in z.namelist():
            raise KeyError(f"{nome} ausente no pacote da CVM")
        texto = z.read(nome).decode("latin-1")
    return csv.DictReader(io.StringIO(texto), delimiter=";")


def parse_cabecalho(conteudo: bytes, year: int, categoria: str = "DFP") -> list[Entrega]:
    """Le o cabecalho anual e consolida por (companhia, exercicio).

    Linhas sem `CD_CVM`, sem `DT_RECEB` ou sem `DT_REFER` sao descartadas em
    silencio: sem qualquer uma das tres nao ha o que afirmar sobre
    disponibilidade, e inventar uma data seria pior que nao ter.
    """
    por_chave: dict[tuple[int, int], list[date]] = {}
    for row in _linhas(conteudo, nome_cabecalho(categoria, year)):
        codigo = _codigo(row.get("CD_CVM"))
        referencia = _data(row.get("DT_REFER"))
        recebimento = _data(row.get("DT_RECEB"))
        if codigo is None or referencia is None or recebimento is None:
            continue
        # Entrega anterior a competencia e impossivel: seria conhecer o
        # exercicio antes de ele terminar. Linha corrompida, nao evidencia.
        if recebimento < referencia:
            logger.debug("cvm: %s exercicio %s recebido antes da competencia",
                         codigo, referencia)
            continue
        por_chave.setdefault((codigo, referencia.year), []).append(recebimento)
    return [
        Entrega(codigo_cvm=codigo, exercicio=exercicio,
                categoria=categoria.upper(),
                disponivel_em=max(datas), primeira_entrega_em=min(datas),
                versoes=len(datas))
        for (codigo, exercicio), datas in sorted(por_chave.items())
    ]


def baixar_cabecalho(year: int, categoria: str = "DFP", timeout: int = 300) -> bytes | None:
    """Baixa o pacote anual. Devolve ``None`` em falha: ausencia de fonte e
    pendencia declarada, nunca excecao que derruba um ETL inteiro."""
    import requests

    url = (DFP_ZIP if categoria.upper() == "DFP" else ITR_ZIP).format(year=year)
    try:
        resposta = requests.get(url, headers=_UA, timeout=timeout)
        if resposta.status_code != 200:
            logger.warning("cvm %s %s: HTTP %s", categoria, year, resposta.status_code)
            return None
        return resposta.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("cvm %s %s indisponivel: %s", categoria, year, exc)
        return None


def entregas_do_ano(year: int, categoria: str = "DFP") -> list[Entrega]:
    conteudo = baixar_cabecalho(year, categoria)
    if not conteudo:
        return []
    try:
        return parse_cabecalho(conteudo, year, categoria)
    except (KeyError, zipfile.BadZipFile) as exc:
        logger.warning("cvm %s %s ilegivel: %s", categoria, year, exc)
        return []
