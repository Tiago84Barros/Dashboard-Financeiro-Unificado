# -*- coding: utf-8 -*-
"""Persistencia da ultima medicao do Grau de Confianca.

Existe porque medir custa caro e ``st.tabs`` nao adia nada: o Streamlit executa
o corpo de TODAS as abas em toda execucao do script -- trocar de aba e
client-side e nao gera rerun. Enquanto ``views/confianca.py`` chamava
``relatorio()`` direto, abrir Configuracoes para mexer em qualquer outra coisa
pagava as sete secoes. Quem adia e o portao explicito (o botao), nao a posicao.

Fica separado de ``core/confianca_secao.py`` de proposito: o motor de medicao
nao pode passar a depender de persistencia. Ele mede; este modulo guarda.

A serializacao e explicita, campo a campo, e carrega ``versao``. ``Componente``
e ``ConfiancaSecao`` sao dataclasses congeladas cujo significado mora nos
invariantes -- ``pct=None`` quer dizer NAO MEDIDO e nunca pode voltar do JSON
como ``0.0``, porque zero e punicao e ausencia nao e. Um ``asdict`` generico
sobreviveria a mudanca de campo em silencio; este nao.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, bindparam, text

from core.confianca_secao import Componente, ConfiancaSecao
from core.universo_decisao import Universo

logger = logging.getLogger(__name__)

VERSAO = 1

#: Acima disto a medicao aparece com aviso de que envelheceu. Nao e prazo de
#: validade tecnico: e o ponto a partir do qual o numero ja passou por
#: republicacao de vitrine, ingestao e safra suficientes para nao descrever
#: mais o app de hoje.
VALIDADE_DIAS = 7

#: Quantas medicoes ficam guardadas. O historico serve para ver o numero se
#: mover; guardar tudo cresceria sem teto num banco de 500 MB.
MANTER = 50

TABELA = "confianca_snapshots"
MIGRATION = "supabase_unificado/schema/071_confianca_snapshots.sql"


class TabelaAusente(RuntimeError):
    """A migration 071 ainda nao foi executada neste banco.

    Erro proprio porque o caminho de escrita NAO pode falhar calado: o usuario
    clicaria em "Recalcular agora", pagaria a medicao inteira e voltaria para a
    mesma tela vazia, sem nada que explicasse por que.
    """

    def __init__(self) -> None:
        super().__init__(
            f"A tabela {TABELA} nao existe neste banco. Execute {MIGRATION} "
            "no SQL Editor do Supabase."
        )


# -- serializacao -------------------------------------------------------------

def _componente_para_json(c: Componente) -> dict:
    return {"nome": c.nome, "pct": c.pct, "peso": c.peso,
            "evidencia": c.evidencia}


def _componente_de_json(d: dict) -> Componente:
    pct = d.get("pct")
    # None permanece None. Um componente nao medido que voltasse como 0.0
    # entraria na media ponderada punindo o app por uma falha que ninguem
    # observou -- o oposto do que o motor promete.
    return Componente(
        nome=str(d.get("nome", "")),
        pct=None if pct is None else float(pct),
        peso=float(d.get("peso", 0.0)),
        evidencia=str(d.get("evidencia", "")),
    )


def _universo_para_json(u: Universo | None) -> dict | None:
    if u is None:
        return None
    return {"modulo": u.modulo, "nominal": u.nominal,
            "investivel": u.investivel, "apto": u.apto,
            "exemplos_descartados": list(u.exemplos_descartados),
            "notas": list(u.notas), "minimo_absoluto": u.minimo_absoluto}


def _universo_de_json(d: dict | None) -> Universo | None:
    if not d:
        return None
    return Universo(
        modulo=str(d.get("modulo", "")),
        nominal=int(d.get("nominal", 0)),
        investivel=int(d.get("investivel", 0)),
        apto=int(d.get("apto", 0)),
        exemplos_descartados=tuple(d.get("exemplos_descartados") or ()),
        notas=tuple(d.get("notas") or ()),
        minimo_absoluto=int(d.get("minimo_absoluto", 0)),
    )


def _secao_para_json(s: ConfiancaSecao) -> dict:
    return {"secao": s.secao,
            "componentes": [_componente_para_json(c) for c in s.componentes],
            "universo": _universo_para_json(s.universo),
            "notas": list(s.notas),
            "aplicavel": bool(s.aplicavel)}


def _secao_de_json(d: dict) -> ConfiancaSecao:
    return ConfiancaSecao(
        secao=str(d.get("secao", "")),
        componentes=tuple(_componente_de_json(c)
                          for c in (d.get("componentes") or ())),
        universo=_universo_de_json(d.get("universo")),
        notas=tuple(d.get("notas") or ()),
        aplicavel=bool(d.get("aplicavel", True)),
    )


def serializar(secoes, rigor: dict | None) -> dict:
    """Payload gravavel. ``rigor`` e a tabela de ``views/confianca.py::_rigor``.

    O rigor entra no MESMO snapshot porque ele tambem consulta os tres motores
    e tambem custa. Guardar so metade da tela deixaria a outra metade medindo a
    cada abertura -- o portao teria sido construido pela metade.
    """
    return {"versao": VERSAO,
            "secoes": [_secao_para_json(s) for s in secoes],
            "rigor": rigor}


def desserializar(payload: dict) -> tuple[list[ConfiancaSecao], dict | None]:
    if not isinstance(payload, dict):
        raise ValueError("payload de snapshot invalido")
    secoes = [_secao_de_json(s) for s in (payload.get("secoes") or ())]
    return secoes, payload.get("rigor")


# -- idade --------------------------------------------------------------------

def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(momento) -> datetime:
    """Sempre com fuso. Um ``medido_em`` ingenuo subtraido de um consciente
    levanta ``TypeError`` -- e a conta da idade e justamente o que nao pode
    falhar, porque e dela que sai o aviso de medicao velha."""
    if isinstance(momento, str):
        momento = datetime.fromisoformat(momento.replace("Z", "+00:00"))
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def idade(medido_em: datetime, agora: datetime | None = None) -> timedelta:
    return (agora or _agora()) - _aware(medido_em)


def vencida(medido_em: datetime, agora: datetime | None = None) -> bool:
    return idade(medido_em, agora) > timedelta(days=VALIDADE_DIAS)


def rotulo_idade(medido_em: datetime, agora: datetime | None = None) -> str:
    """Quanto tempo faz, em texto DERIVADO de ``medido_em``.

    Texto de limitacao escrito a mao envelhece invertido: continua soando como
    rigor depois de ter virado falso. Este nao tem como discordar da medicao,
    porque sai dela.
    """
    delta = idade(medido_em, agora)
    segundos = max(0, int(delta.total_seconds()))
    if segundos < 90:
        return "agora ha pouco"
    if segundos < 5400:
        minutos = segundos // 60
        return "ha 1 minuto" if minutos == 1 else f"ha {minutos} minutos"
    if segundos < 86400:
        horas = segundos // 3600
        return "ha 1 hora" if horas == 1 else f"ha {horas} horas"
    dias = segundos // 86400
    return "ha 1 dia" if dias == 1 else f"ha {dias} dias"


# -- acesso ao banco ----------------------------------------------------------

def _engine(engine=None):
    from core.database import get_engine
    eng = engine or get_engine()
    if eng is None:
        raise RuntimeError("Banco indisponivel.")
    return eng


def _e_tabela_ausente(exc: Exception) -> bool:
    texto = str(exc).lower()
    return TABELA in texto and ("does not exist" in texto
                                or "undefinedtable" in texto
                                or "no such table" in texto)


def carregar_ultimo(
    engine=None,
) -> tuple[list[ConfiancaSecao], dict | None, datetime] | None:
    """Ultima medicao gravada, ou ``None`` quando nunca houve uma.

    ``None`` tambem e a resposta quando a migration ainda nao rodou. A LEITURA
    pode ser silenciosa porque o estado vazio e legitimo e a tela o desenha; a
    ESCRITA nao pode, e por isso ela levanta.
    """
    try:
        with _engine(engine).connect() as conn:
            linha = conn.execute(text(
                f"SELECT medido_em, payload FROM {TABELA} "
                "ORDER BY medido_em DESC, id DESC LIMIT 1"
            )).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("confianca_snapshot: leitura falhou: %s", exc)
        return None
    if linha is None:
        return None
    medido_em, payload = linha[0], linha[1]
    if isinstance(payload, str):
        payload = json.loads(payload)
    try:
        secoes, rigor = desserializar(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("confianca_snapshot: payload ilegivel: %s", exc)
        return None
    return secoes, rigor, _aware(medido_em)


def podar(conn, manter: int = MANTER) -> int:
    """Deixa apenas as ``manter`` medicoes mais recentes.

    O DELETE varre a TABELA INTEIRA. Um DELETE escopado pelo mesmo filtro da
    leitura so alcanca o que ja se enxerga, e o resto fica fora de alcance para
    sempre -- foi assim que 70% da vitrine dos EUA virou metodologia morta
    inalcancavel.
    """
    resultado = conn.execute(text(
        f"DELETE FROM {TABELA} WHERE id NOT IN ("
        f"SELECT id FROM {TABELA} ORDER BY medido_em DESC, id DESC LIMIT :n)"
    ), {"n": int(manter)})
    return resultado.rowcount or 0


def gravar(secoes, rigor: dict | None, engine=None, agora: datetime | None = None
           ) -> datetime:
    """Grava a medicao e devolve o instante gravado.

    ``medido_em`` vai explicito, e nao pelo ``DEFAULT NOW()`` da coluna: e esse
    instante que a tela imprime e do qual o aviso de idade e derivado, entao
    quem grava precisa devolver exatamente o que gravou, sem depender de
    ``RETURNING`` nem do relogio do banco.
    """
    payload = serializar(secoes, rigor)
    medido_em = agora or _agora()
    bruto = json.dumps(payload, ensure_ascii=False)
    try:
        with _engine(engine).begin() as conn:
            # JSONB nao aceita texto sem cast explicito; fora do Postgres o
            # mesmo cast mudaria a afinidade da coluna e guardaria numero.
            valor = "CAST(:p AS JSONB)" if conn.dialect.name == "postgresql" else ":p"
            conn.execute(
                text(f"INSERT INTO {TABELA} (medido_em, payload) "
                     f"VALUES (:m, {valor})").bindparams(
                         bindparam("m", type_=DateTime(timezone=True))),
                {"m": medido_em, "p": bruto})
            podar(conn)
    except Exception as exc:  # noqa: BLE001
        if _e_tabela_ausente(exc):
            raise TabelaAusente() from exc
        raise
    return medido_em
