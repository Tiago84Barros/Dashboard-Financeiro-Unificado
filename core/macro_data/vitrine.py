"""Vitrine macro: a ponte entre o armazém local e o app publicado.

O defeito que ela corrige
-------------------------
``core.macro_data.portfolio_context.load_portfolio_macro_snapshot`` lê
``macro_observations`` e ``macro_sector_exposures`` no Postgres do Docker local.
São dezenas de milhares de observações, e é por isso que elas moram lá. Só que
``get_local_macro_engine`` devolve ``None`` fora daquela máquina, e as telas
respondiam a isso com um aviso — *"Camada macro do Docker local indisponível;
os pesos permanecem fundamentalistas"* — que descreve corretamente o sintoma e
esconde a causa: o dado existe, está calculado, e simplesmente não atravessou.

É o mesmo problema, com a mesma forma, que o noticiário já resolveu em
:mod:`core.noticias.vitrine`, e a solução aqui é deliberadamente a mesma: o
acervo fica no local, **o resultado** vai para o Supabase.

O que atravessa, e o que não
----------------------------
Atravessa o impacto agregado — um número por setor por classe, mais os fatores
que o compuseram e a direção, intensidade e confiança de cada um. Não atravessam
as observações macro que os produziram: são o acervo, elas somam MBs e o app não
faz nada com elas que o impacto já não responda.

=================  ============================  ============================
                   acervo (local)                vitrine (Supabase)
=================  ============================  ============================
grão               uma observação de indicador   um setor por classe
histórico          acumula                       **substituída** por classe
tamanho            dezenas de MB                 alguns KB
=================  ============================  ============================

Três regras herdadas do noticiário, pelos mesmos motivos
---------------------------------------------------------
1. **A substituição é por classe, sem filtro de versão.** Publicar a B3 não
   pode apagar a vitrine dos EUA, e republicar a B3 tem de apagar a B3 inteira:
   linha órfã de execução anterior é dado morto com cara de dado vivo.
2. **Vitrine vazia ≠ vitrine que nunca existiu.** ``macro_vitrine_meta`` guarda
   uma linha por classe justamente para separar "o publicador quebrou" de
   "nenhum ativo teve impacto medido".
3. **A idade viaja junto.** ``as_of`` e ``gerada_em`` são campos, não
   metadados implícitos: uma vitrine substituída sempre parece a leitura de
   agora, e quem lê precisa poder dizer que está velha.

Por que **setor** e não ativo
------------------------------
O impacto que ``load_portfolio_macro_snapshot`` calcula para um símbolo depende
exclusivamente do setor dele: as sensibilidades vivem em
``macro_sector_exposures``, indexadas por ``(setor, fator)``, e o símbolo só
aparece como rótulo. Publicar por setor é, portanto, **exato** — não é resumo
nem aproximação — e tem a propriedade de não depender de qual carteira estava
na tela quando o publicador rodou. Uma vitrine por ativo envelheceria a cada
ativo novo; esta só envelhece quando a economia se move.

``granularidade`` grava isso na meta em vez de deixar como convenção: o leitor
precisa saber se ``simbolo`` é um ticker ou um setor, e adivinhar pela forma da
string é o tipo de heurística que funciona até o dia em que não funciona.

``limitacoes`` e ``knowledge_mode`` viajam porque um impacto reconstruído *ex
post* não vale o mesmo que um impacto que estava na tela no dia — a distinção
já existe em :mod:`core.conjuntura.ponte` e sumiria se a vitrine a achatasse.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

CLASSES = ("b3", "us", "fii")

#: Acima disto a leitura é publicada com aviso de idade em vez de silêncio.
IDADE_MAXIMA_DIAS = 10

DDL_SQL = (
    """
    CREATE TABLE IF NOT EXISTS macro_vitrine (
        asset_class    TEXT        NOT NULL,
        simbolo        TEXT        NOT NULL,
        setor          TEXT,
        impacto        DOUBLE PRECISION,
        fatores        JSONB       NOT NULL DEFAULT '[]'::jsonb,
        PRIMARY KEY (asset_class, simbolo)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_vitrine_meta (
        asset_class      TEXT PRIMARY KEY,
        as_of            TIMESTAMPTZ NOT NULL,
        gerada_em        TIMESTAMPTZ NOT NULL,
        knowledge_mode   TEXT        NOT NULL,
        ativos           INTEGER     NOT NULL,
        ativos_cobertos  INTEGER     NOT NULL,
        fontes           INTEGER     NOT NULL,
        limitacoes       JSONB       NOT NULL DEFAULT '[]'::jsonb,
        snapshot_id      TEXT,
        origem           TEXT,
        granularidade    TEXT        NOT NULL DEFAULT 'ativo'
    )
    """,
    # Bancos que já criaram a tabela antes da granularidade existir.
    "ALTER TABLE macro_vitrine_meta ADD COLUMN IF NOT EXISTS "
    "granularidade TEXT NOT NULL DEFAULT 'ativo'",
)

_INSERT_LINHA = text("""
    INSERT INTO macro_vitrine (asset_class, simbolo, setor, impacto, fatores)
    VALUES (:asset_class, :simbolo, :setor, :impacto, CAST(:fatores AS jsonb))
""")

_UPSERT_META = text("""
    INSERT INTO macro_vitrine_meta (
        asset_class, as_of, gerada_em, knowledge_mode, ativos, ativos_cobertos,
        fontes, limitacoes, snapshot_id, origem, granularidade)
    VALUES (:asset_class, :as_of, :gerada_em, :knowledge_mode, :ativos,
            :ativos_cobertos, :fontes, CAST(:limitacoes AS jsonb), :snapshot_id,
            :origem, :granularidade)
    ON CONFLICT (asset_class) DO UPDATE SET
        as_of = EXCLUDED.as_of,
        gerada_em = EXCLUDED.gerada_em,
        knowledge_mode = EXCLUDED.knowledge_mode,
        ativos = EXCLUDED.ativos,
        ativos_cobertos = EXCLUDED.ativos_cobertos,
        fontes = EXCLUDED.fontes,
        limitacoes = EXCLUDED.limitacoes,
        snapshot_id = EXCLUDED.snapshot_id,
        origem = EXCLUDED.origem,
        granularidade = EXCLUDED.granularidade
""")

_SELECT_LINHAS = text("""
    SELECT simbolo, setor, impacto, fatores
      FROM macro_vitrine
     WHERE asset_class = :asset_class
""")

_SELECT_META = text("""
    SELECT as_of, gerada_em, knowledge_mode, ativos, ativos_cobertos, fontes,
           limitacoes, snapshot_id, origem, granularidade
      FROM macro_vitrine_meta
     WHERE asset_class = :asset_class
""")


class VitrineMacroIlegivel(RuntimeError):
    """A leitura falhou. Diferente de vitrine vazia — e pede outra providência."""


def garantir_schema(conn) -> None:
    """Cria as tabelas se faltarem. Só o publicador chama.

    O leitor não chama de propósito: criar tabela no caminho de leitura
    transforma "nunca publicada" em "vazia", e as duas pedem coisas opostas.
    """
    for ddl in DDL_SQL:
        conn.execute(text(ddl))


def linhas_do_snapshot(snapshot, *, asset_class: str, setores=None,
                      max_fatores: int | None = None) -> list[dict]:
    """Achata um ``PortfolioMacroSnapshot`` em linhas. Testável sem banco.

    ``max_fatores`` trunca os fatores por linha. O padrão é não truncar, e isso
    é deliberado: ``views/empresas_b3.py`` reagrega os fatores para separar o
    que é macro internacional do que é macro doméstico, e uma lista truncada
    devolveria um número plausível e errado, sem sinal nenhum de que foi
    truncada. Truncar só faz sentido para uma vitrine por ativo, onde o volume
    cresce com a carteira -- por setor, a lista inteira cabe em poucos KB.
    """
    setores = {str(k).upper(): v for k, v in (setores or {}).items()}
    fatores: dict[str, list[dict]] = {}
    for detail in getattr(snapshot, "details", ()) or ():
        simbolo = str(detail.get("symbol") or "").upper()
        if not simbolo:
            continue
        fatores.setdefault(simbolo, []).append({
            "fator": detail.get("factor"),
            # `provider` viaja porque a separação entre macro internacional e
            # doméstica em `views/empresas_b3.py` é feita por ele.
            "provider": detail.get("provider"),
            "provider_code": detail.get("provider_code"),
            "direcao": detail.get("direction"),
            "intensidade": detail.get("intensity"),
            "confianca": detail.get("confidence"),
            "canal": detail.get("channel"),
        })
    simbolos = set(snapshot.impacts) | set(fatores) | set(setores)
    return [{
        "asset_class": asset_class,
        "simbolo": simbolo,
        "setor": setores.get(simbolo),
        "impacto": (None if snapshot.impacts.get(simbolo) is None
                    else round(float(snapshot.impacts[simbolo]), 4)),
        "fatores": json.dumps(
            fatores.get(simbolo, [])[:max_fatores] if max_fatores
            else fatores.get(simbolo, []),
            ensure_ascii=False, default=str),
    } for simbolo in sorted(simbolos)]


def publicar(engine, snapshot, *, asset_class: str, setores=None,
             origem: str = "armazém local", granularidade: str = "ativo",
             max_fatores: int | None = None,
             gerada_em: datetime | None = None) -> dict:
    """Substitui a vitrine desta classe, numa transação só.

    O ``DELETE`` é escopado pela classe e por nada mais — ver o módulo.
    """
    if asset_class not in CLASSES:
        raise ValueError("classe de ativo macro inválida")
    momento = gerada_em or datetime.now(timezone.utc)
    linhas = linhas_do_snapshot(snapshot, asset_class=asset_class,
                                setores=setores, max_fatores=max_fatores)
    with engine.begin() as conn:
        garantir_schema(conn)
        conn.execute(text("DELETE FROM macro_vitrine WHERE asset_class = :c"),
                     {"c": asset_class})
        for linha in linhas:
            conn.execute(_INSERT_LINHA, linha)
        conn.execute(_UPSERT_META, {
            "asset_class": asset_class,
            "as_of": snapshot.as_of,
            "gerada_em": momento,
            "knowledge_mode": str(getattr(snapshot, "knowledge_mode", "strict")),
            "ativos": int(snapshot.asset_count),
            "ativos_cobertos": int(snapshot.covered_assets),
            "fontes": int(snapshot.source_count),
            "limitacoes": json.dumps(list(getattr(snapshot, "limitations", ())),
                                     ensure_ascii=False),
            "snapshot_id": getattr(snapshot, "snapshot_id", None),
            "origem": origem,
            "granularidade": granularidade,
        })
    return {"publicado": True, "classe": asset_class, "linhas": len(linhas),
            "granularidade": granularidade, "gerada_em": momento}


def ler(engine, *, asset_class: str, simbolos=None):
    """Devolve ``(linhas, meta)``. ``meta`` ``None`` significa nunca publicada.

    Os dois retornos respondem perguntas diferentes: as linhas dizem o que se
    sabe, a meta diz **quando** se soube. Levanta em falha de leitura, porque
    "o banco caiu" e "não há impacto macro" pedem providências opostas.
    """
    if asset_class not in CLASSES:
        raise ValueError("classe de ativo macro inválida")
    alvos = ([str(s).strip().upper() for s in simbolos if str(s).strip()]
             if simbolos is not None else None)
    try:
        with engine.connect() as conn:
            meta = conn.execute(_SELECT_META, {"asset_class": asset_class}) \
                       .mappings().first()
            linhas = conn.execute(_SELECT_LINHAS, {"asset_class": asset_class}) \
                         .mappings().all()
    except Exception as exc:  # noqa: BLE001 - falha declarada, não vitrine vazia
        causa = str(exc).splitlines()[0].strip()
        logger.warning("vitrine macro ilegivel: %s", causa)
        raise VitrineMacroIlegivel(causa) from exc
    saida = [dict(linha) for linha in linhas]
    if alvos is not None:
        alvo_set = set(alvos)
        saida = [linha for linha in saida
                 if str(linha["simbolo"]).upper() in alvo_set]
    return tuple(saida), (dict(meta) if meta is not None else None)
