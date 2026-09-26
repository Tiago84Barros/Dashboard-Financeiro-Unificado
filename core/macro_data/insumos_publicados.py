"""Insumos macro publicados: o que a produção lê quando o Docker não está ao alcance.

O contexto macro das carteiras (``load_portfolio_macro_snapshot``) move score,
ranking e peso em B3, EUA, FIIs e Portfólio Global. Até aqui ele só existia no
Docker local: em produção as quatro telas mostravam "macro local indisponível" e
a carteira publicada era outra, sem ajuste nenhum.

Servir o mesmo cálculo pelo túnel não resolve, e piora: a carteira passaria a
mudar conforme o PC estivesse ligado. O que sai daqui é o **insumo** --
exposições setoriais e as 24 últimas observações de cada série, exatamente o que
as duas consultas do cálculo devolvem --, gravado num arquivo do repositório pela
rotina de vitrines. O cálculo continua um só; muda de onde as linhas vêm.

**Ponto no tempo.** O arquivo é a resposta da consulta ``strict`` na hora da
publicação. Para ``as_of`` a partir dela, é a mesma resposta que o banco daria
(nada recuperado depois dela existe no arquivo, e nada anterior falta). Para
data anterior, ou para o modo ``reconstructed``, o arquivo não tem como
responder e recusa com ``ValueError`` -- que as telas já tratam como "sem
macro". Responder com o que tem seria vazar o futuro num backtest.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping

ESQUEMA = "macro_insumos.v1"
CAMINHO_PADRAO = Path(__file__).resolve().parents[2] / "data" / "public" / "macro_insumos.json.gz"
# Macro é mensal/trimestral: um arquivo de uma semana ainda descreve o cenário.
# Passado disto, a coleta ou a publicação parou, e ajustar carteira com isso
# seria afirmar um cenário que ninguém mais confere.
IDADE_MAXIMA_DIAS = 30

_DATAS = ("reference_period", "vintage_date")
_INSTANTES = ("retrieved_at", "released_at")


@dataclass(frozen=True)
class InsumosMacroPublicados:
    gerado_em: datetime
    exposicoes: tuple[Mapping[str, object], ...]
    observacoes: tuple[Mapping[str, object], ...]

    def ler(self, *, asset_class: str, sectors: list[str], as_of: datetime,
            knowledge_mode: str) -> tuple[list, list]:
        """Mesmo contrato das duas consultas de ``load_portfolio_macro_snapshot``."""
        if knowledge_mode != "strict":
            raise ValueError("insumos publicados só respondem ao modo strict")
        if as_of < self.gerado_em:
            raise ValueError("insumos publicados não cobrem data anterior à publicação")
        setores = set(sectors)
        exposicoes = [dict(e) for e in self.exposicoes
                      if e.get("asset_class") == asset_class and e.get("sector") in setores]
        corte = as_of.date()
        observacoes = [dict(o) for o in self.observacoes
                       if o["reference_period"] is not None and o["reference_period"] <= corte]
        return exposicoes, observacoes


_DECIMAL = "$decimal"


def _texto(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        # O Postgres devolve NUMERIC como Decimal, e ``snapshot_id`` serializa
        # Decimal como texto e float como número: virar float daria outro id
        # para os mesmos insumos, e a carteira salva deixaria de se reconhecer.
        return {_DECIMAL: str(valor)}
    return valor


def _valor(bruto):
    if isinstance(bruto, dict) and set(bruto) == {_DECIMAL}:
        return Decimal(bruto[_DECIMAL])
    return bruto


def serializar(gerado_em: datetime, exposicoes, observacoes) -> bytes:
    carga = {
        "schema": ESQUEMA,
        "generated_at": gerado_em.isoformat(),
        "exposures": [{k: _texto(v) for k, v in dict(e).items()} for e in exposicoes],
        "observations": [{k: _texto(v) for k, v in dict(o).items()} for o in observacoes],
    }
    # mtime=0: o mesmo conteúdo gera os mesmos bytes, e a rotina não commita
    # arquivo que não mudou.
    return gzip.compress(json.dumps(carga, ensure_ascii=False, sort_keys=True).encode(),
                         mtime=0)


def _instante(valor):
    if not valor:
        return None
    instante = datetime.fromisoformat(valor)
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


def desserializar(dados: bytes) -> InsumosMacroPublicados:
    carga = json.loads(gzip.decompress(dados))
    if carga.get("schema") != ESQUEMA:
        raise ValueError(f"esquema de insumos macro desconhecido: {carga.get('schema')!r}")
    observacoes = []
    for bruto in carga["observations"]:
        linha = {k: _valor(v) for k, v in bruto.items()}
        for chave in _DATAS:
            linha[chave] = date.fromisoformat(linha[chave]) if linha.get(chave) else None
        for chave in _INSTANTES:
            linha[chave] = _instante(linha.get(chave))
        observacoes.append(linha)
    return InsumosMacroPublicados(
        gerado_em=_instante(carga["generated_at"]),
        exposicoes=tuple({k: _valor(v) for k, v in e.items()} for e in carga["exposures"]),
        observacoes=tuple(observacoes),
    )


_cache: dict[tuple[str, int], InsumosMacroPublicados | None] = {}


def carregar_insumos_publicados(caminho: Path = CAMINHO_PADRAO, *,
                                agora: datetime | None = None) -> InsumosMacroPublicados | None:
    """O arquivo publicado, ou ``None`` se não existir, não ler ou estiver vencido."""
    try:
        chave = (str(caminho), caminho.stat().st_mtime_ns)
    except OSError:
        return None
    if chave not in _cache:
        try:
            _cache[chave] = desserializar(caminho.read_bytes())
        except (OSError, ValueError, KeyError, TypeError):
            _cache[chave] = None
    insumos = _cache[chave]
    if insumos is None:
        return None
    agora = agora or datetime.now(timezone.utc)
    if (agora - insumos.gerado_em).days > IDADE_MAXIMA_DIAS:
        return None
    return insumos
