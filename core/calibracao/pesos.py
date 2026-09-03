"""Pesos versionados, com portões de promoção e com volta atrás.

Duas instruções desta entrega moram aqui. A do fim -- *"Versione pesos e
modelos, permitindo rollback"* -- e a lista de condições em que **não** se
coloca em produção, que é o que os portões deste módulo verificam.

Prior é uma hipótese com data
-----------------------------
:data:`PRIOR` congela o que hoje está escrito à mão em
:mod:`core.noticias.relevancia` e :mod:`core.noticias.taxonomia`. Ele entra no
registro como qualquer outro conjunto, com ``calibrado=False``, e é o alvo do
rollback: voltar atrás significa voltar a ele, não voltar a um estado que
ninguém sabe descrever.

Onde os pesos moram
-------------------
Numa tabela do Supabase, e isso é deliberado contra a intuição de tamanho. A
regra do projeto é separar por mutabilidade e não por volume
(``memoria: separar-por-mutabilidade-nao-tamanho``): pesos são poucos bytes,
mudam em runtime e precisam ser lidos pelo app publicado -- que não alcança o
armazém local. O conjunto histórico de eventos, que é pesado e imutável, fica no
armazém; os pesos, que são leves e vivos, ficam onde a produção lê.

Portões que só podem reprovar por evidência
-------------------------------------------
Cada portão devolve ``ok`` em três estados. ``False`` é reprovação medida;
``None`` é "não medido" -- nunca ``False`` disfarçado, que é lei do projeto. Um
conjunto só é promovido com todos os portões em ``True``: não medido **bloqueia**
a promoção, e bloqueia dizendo que não mediu, em vez de dizer que reprovou. A
diferença importa porque uma reprovação manda ajustar o modelo e uma
não-medição manda arrumar a medição.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.calibracao import CALIBRACAO_VERSAO
from core.noticias import relevancia as rel
from core.noticias import taxonomia as tax

logger = logging.getLogger(__name__)

TABELA = "noticias_pesos_versoes"

#: Teto de falso alarme. Acima disto o painel vira ruído e o usuário aprende a
#: ignorá-lo -- e um alerta ignorado é pior que nenhum alerta, porque custa
#: atenção e dá sensação de cobertura.
TETO_FALSO_ALARME = 0.20

#: Teto de nao-deteccao. A instrucao manda medir "crises nao detectadas", e
#: metrica medida que nao barra nada e decoracao
#: (``memoria: diagnostico-precisa-porta-de-entrada``). Este portao existe por
#: uma medicao real: na primeira rodada contra o armazem, o motor apontou 2 de
#: 2.146 eventos, deixou 283 movimentos relevantes passarem -- e **passou** no
#: portao de alarme excessivo, porque quem nao fala nunca da alarme falso. Os
#: seis portoes da instrucao, sozinhos, promoveriam um motor mudo.
TETO_NAO_DETECCAO = 0.50

#: Quanto o turnover pode crescer em relação a não agir. "Muito maior" da
#: instrução vira número: 50% a mais de giro precisa de ganho líquido para se
#: justificar, e o portão do risco cobra esse ganho separadamente.
TETO_TURNOVER_EXTRA = 0.50

#: Piora tolerada no drawdown. Zero: a instrução diz "piora o risco da carteira"
#: sem faixa, e uma tolerância inventada aqui seria exatamente o peso arbitrário
#: que a entrega inteira existe para não ter.
TOLERANCIA_DRAWDOWN = 0.0


def _agora() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Conjunto:
    """Um conjunto de pesos completo, identificado e com procedência."""

    versao: str
    pesos_relevancia: dict[str, float]
    #: ``chave do tipo -> (materialidade, persistencia)``. São as duas notas
    #: que ``core.noticias.taxonomia`` fixa por categoria, e as duas que a
    #: calibração pode mover.
    notas_tipo: dict[str, tuple[float, float]]
    calibrado: bool = False
    origem: str = "prior_declarado"
    criado_em: datetime = field(default_factory=_agora)
    evidencia: dict = field(default_factory=dict)
    limitacoes: tuple[str, ...] = ()

    def como_pesos(self) -> rel.Pesos:
        """Converte para o objeto que ``core.noticias.relevancia`` consome."""
        return rel.Pesos(
            materialidade=self.pesos_relevancia.get(
                rel.MATERIALIDADE, rel.PESOS_PADRAO.materialidade),
            relacao_ativo=self.pesos_relevancia.get(
                rel.RELACAO_ATIVO, rel.PESOS_PADRAO.relacao_ativo),
            confiabilidade=self.pesos_relevancia.get(
                rel.CONFIABILIDADE, rel.PESOS_PADRAO.confiabilidade),
            novidade=self.pesos_relevancia.get(
                rel.NOVIDADE, rel.PESOS_PADRAO.novidade),
            confirmacao=self.pesos_relevancia.get(
                rel.CONFIRMACAO, rel.PESOS_PADRAO.confirmacao),
            persistencia=self.pesos_relevancia.get(
                rel.PERSISTENCIA, rel.PESOS_PADRAO.persistencia),
            exposicao=self.pesos_relevancia.get(
                rel.EXPOSICAO, rel.PESOS_PADRAO.exposicao),
        )

    def validar(self) -> list[str]:
        """Avisos estruturais. Inclui o teto que impede notícia de virar tese."""
        avisos = list(self.como_pesos().validar())
        materialidade = self.pesos_relevancia.get(rel.MATERIALIDADE, 0.0)
        if materialidade > TETO_MATERIALIDADE:
            avisos.append(
                f"peso de materialidade {materialidade:.2f} acima do teto "
                f"{TETO_MATERIALIDADE:.2f}: uma noticia do dia passaria a "
                "dominar a avaliacao estrutural")
        for chave, (materialidade_tipo, persistencia) in self.notas_tipo.items():
            if chave not in tax.POR_CHAVE:
                avisos.append(f"tipo desconhecido no conjunto: {chave}")
            if not (0.0 <= materialidade_tipo <= 1.0) or not (0.0 <= persistencia <= 1.0):
                avisos.append(f"nota fora de [0,1] em {chave}")
        return avisos

    def como_dict(self) -> dict:
        return {
            "versao": self.versao,
            "calibracao_versao": CALIBRACAO_VERSAO,
            "taxonomia_versao": tax.TAXONOMIA_VERSAO,
            "pesos_relevancia": dict(self.pesos_relevancia),
            "notas_tipo": {k: list(v) for k, v in self.notas_tipo.items()},
            "calibrado": self.calibrado,
            "origem": self.origem,
            "criado_em": self.criado_em.isoformat(),
            "evidencia": dict(self.evidencia),
            "limitacoes": list(self.limitacoes),
        }


#: Teto do peso de materialidade. A instrução pede limites "para impedir que
#: notícias cotidianas dominem decisões fundamentais", e o componente que carrega
#: a notícia do dia é a materialidade. O número é o prior (0,25) com folga de um
#: terço: acima disso, o Score Conjuntural deixa de ser um ajuste sobre o Score
#: Estrutural e passa a ser a decisão.
TETO_MATERIALIDADE = 0.35


PRIOR = Conjunto(
    versao="1.0.0-prior",
    pesos_relevancia=dict(rel.PESOS_PADRAO.como_dicionario()),
    notas_tipo={t.chave: (t.materialidade, t.persistencia) for t in tax.TIPOS},
    calibrado=False,
    origem="prior_declarado",
    limitacoes=(
        "pesos escritos com o motivo ao lado, nunca medidos contra historia",
        "notas por tipo herdadas de core.noticias.taxonomia v"
        + tax.TAXONOMIA_VERSAO,
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Portões
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Portao:
    nome: str
    ok: bool | None
    motivo: str

    def descrever(self) -> str:
        marca = {True: "PASSOU", False: "REPROVOU", None: "NAO MEDIDO"}[self.ok]
        return f"[{marca}] {self.nome}: {self.motivo}"


def _portao_alarme(confusao) -> Portao:
    taxa = getattr(confusao, "taxa_falso_alarme", None)
    if taxa is None:
        return Portao("alarmes_excessivos", None,
                      "matriz de confusao sem quadrante negativo contado")
    ok = taxa <= TETO_FALSO_ALARME
    return Portao("alarmes_excessivos", ok,
                  f"falso alarme em {taxa * 100:.1f}% dos casos sem movimento "
                  f"(teto {TETO_FALSO_ALARME * 100:.0f}%)")


def _portao_calibracao(calibracao) -> Portao:
    calibrada = getattr(calibracao, "calibrada", None)
    erro = getattr(calibracao, "erro_calibracao", None)
    if calibrada is None:
        return Portao("probabilidade_calibrada", None,
                      "nenhum balde de probabilidade com observacao suficiente")
    return Portao("probabilidade_calibrada", bool(calibrada),
                  f"desvio medio de {erro * 100:.1f} pontos entre probabilidade "
                  "declarada e frequencia observada")


def _portao_deteccao(confusao) -> Portao:
    taxa = getattr(confusao, "taxa_nao_deteccao", None)
    if taxa is None:
        return Portao("deteccao_util", None,
                      "nenhum movimento relevante na amostra para detectar")
    ok = taxa <= TETO_NAO_DETECCAO
    return Portao("deteccao_util", ok,
                  f"{taxa * 100:.1f}% dos movimentos relevantes passaram sem "
                  f"aviso (teto {TETO_NAO_DETECCAO * 100:.0f}%)")


def _portao_turnover(comparacao: dict | None) -> Portao:
    if not comparacao:
        return Portao("turnover", None, "politica nao simulada")
    extra = comparacao.get("turnover_extra")
    if extra is None:
        return Portao("turnover", None, "giro nao medido")
    ok = extra <= TETO_TURNOVER_EXTRA
    return Portao("turnover", ok,
                  f"giro {extra:+.2f} acima de nao agir "
                  f"(teto {TETO_TURNOVER_EXTRA:+.2f})")


def _portao_risco(comparacao: dict | None) -> Portao:
    if not comparacao:
        return Portao("risco", None, "politica nao simulada")
    delta = comparacao.get("drawdown")
    if delta is None:
        return Portao("risco", None, "drawdown nao medido nos dois lados")
    # Drawdown é negativo; agir com drawdown mais negativo piora o risco.
    ok = delta >= -TOLERANCIA_DRAWDOWN
    return Portao("risco", ok,
                  f"drawdown de agir {delta * 100:+.2f} pontos em relacao a "
                  "nao agir")


def _portao_tempo_real(variaveis) -> Portao:
    """Toda variável do modelo precisa existir no instante da decisão.

    Uma variável que só fica pronta no fechamento -- ou que vem de um relatório
    publicado depois -- produz backtest excelente e motor inútil. É a forma mais
    comum de look-ahead sobreviver a uma revisão: ela não está na data, está na
    disponibilidade.
    """
    if variaveis is None:
        return Portao("disponivel_em_tempo_real", None,
                      "lista de variaveis do modelo nao declarada")
    faltando = sorted(nome for nome, disponivel in dict(variaveis).items()
                      if not disponivel)
    if faltando:
        return Portao("disponivel_em_tempo_real", False,
                      "variaveis indisponiveis no instante da decisao: "
                      + ", ".join(faltando))
    return Portao("disponivel_em_tempo_real", True,
                  f"as {len(dict(variaveis))} variaveis do modelo existem no "
                  "instante da decisao")


def _portao_estabilidade(estabilidade) -> Portao:
    concentrado = getattr(estabilidade, "concentrado", None)
    if concentrado is None:
        return Portao("estabilidade", None,
                      "menos de dois segmentos ou periodos medidos")
    medidos = getattr(estabilidade, "medidos", {})
    positivos = getattr(estabilidade, "positivos", 0)
    return Portao("estabilidade", not concentrado,
                  f"resultado positivo em {positivos} de {len(medidos)} "
                  "segmentos medidos")


def avaliar_portoes(*, confusao=None, calibracao=None, comparacao=None,
                    variaveis=None, estabilidade=None) -> tuple[Portao, ...]:
    """Os portões, na ordem em que a instrução os lista.

    O sétimo -- ``deteccao_util`` -- não está na lista dela, e está aqui porque
    a lista sozinha não fecha: um motor que nunca dispara passa em todos os
    seis. Ele cobra a outra métrica que a instrução manda medir, a das crises
    não detectadas.
    """
    return (
        _portao_alarme(confusao),
        _portao_deteccao(confusao),
        _portao_calibracao(calibracao),
        _portao_turnover(comparacao),
        _portao_risco(comparacao),
        _portao_tempo_real(variaveis),
        _portao_estabilidade(estabilidade),
    )


def pode_promover(portoes) -> tuple[bool, tuple[str, ...]]:
    """Promoção exige todos os portões em ``True``.

    Devolve também os impedimentos separados por natureza, porque reprovar e não
    medir pedem trabalhos diferentes.
    """
    impedimentos = []
    for portao in portoes:
        if portao.ok is False:
            impedimentos.append(f"reprovou: {portao.nome} -- {portao.motivo}")
        elif portao.ok is None:
            impedimentos.append(f"nao medido: {portao.nome} -- {portao.motivo}")
    return (not impedimentos), tuple(impedimentos)


# ─────────────────────────────────────────────────────────────────────────────
# Registro versionado
# ─────────────────────────────────────────────────────────────────────────────
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    versao        TEXT PRIMARY KEY,
    conjunto      JSONB NOT NULL,
    ativo         BOOLEAN NOT NULL DEFAULT FALSE,
    promovido_em  TIMESTAMPTZ,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


class Registro:
    """Histórico de conjuntos, com um ativo por vez e volta atrás barata.

    Sem engine, opera em memória: os testes exercitam a decisão de promover e de
    reverter, que é o que importa, sem depender de um Postgres de mentira. A
    mesma escolha de ``tests/test_noticias_infraestrutura.py``, pelo mesmo
    motivo.
    """

    def __init__(self, engine=None):
        self._engine = engine
        self._memoria: dict[str, Conjunto] = {PRIOR.versao: PRIOR}
        self._ativo = PRIOR.versao
        self._historico: list[str] = [PRIOR.versao]

    # -- leitura ------------------------------------------------------------
    def ativo(self) -> Conjunto:
        if self._engine is None:
            return self._memoria[self._ativo]
        lido = self._ler_do_banco()
        return lido if lido is not None else PRIOR

    def versoes(self) -> tuple[str, ...]:
        return tuple(sorted(self._memoria))

    @property
    def historico(self) -> tuple[str, ...]:
        """Ordem em que os conjuntos estiveram ativos. O rollback usa isto."""
        return tuple(self._historico)

    # -- escrita ------------------------------------------------------------
    def registrar(self, conjunto: Conjunto) -> None:
        avisos = conjunto.validar()
        if avisos:
            logger.warning("conjunto %s registrado com avisos: %s",
                           conjunto.versao, "; ".join(avisos))
        self._memoria[conjunto.versao] = conjunto
        if self._engine is not None:
            self._gravar(conjunto, ativo=False)

    def promover(self, conjunto: Conjunto, portoes) -> dict:
        """Ativa um conjunto se, e só se, todos os portões passarem."""
        pode, impedimentos = pode_promover(portoes)
        self.registrar(conjunto)
        if not pode:
            return {"promovido": False, "versao_ativa": self._ativo,
                    "impedimentos": impedimentos}
        self._ativo = conjunto.versao
        self._historico.append(conjunto.versao)
        if self._engine is not None:
            self._ativar(conjunto.versao)
        return {"promovido": True, "versao_ativa": self._ativo,
                "impedimentos": ()}

    def reverter(self) -> dict:
        """Volta ao conjunto anterior. Sem anterior, volta ao prior.

        Rollback que depende de alguém lembrar qual era a versão boa não é
        rollback. O anterior está no histórico e o prior é o piso: existe sempre.
        """
        anterior = None
        if len(self._historico) >= 2:
            self._historico.pop()
            anterior = self._historico[-1]
        else:
            anterior = PRIOR.versao
            if self._historico[-1] != PRIOR.versao:
                self._historico.append(PRIOR.versao)
        self._ativo = anterior
        if self._engine is not None:
            self._ativar(anterior)
        return {"versao_ativa": self._ativo,
                "calibrado": self._memoria[self._ativo].calibrado}

    # -- banco --------------------------------------------------------------
    def garantir_schema(self) -> None:
        if self._engine is None:
            return
        from sqlalchemy import text as _text
        with self._engine.begin() as conn:
            conn.execute(_text(DDL))

    def _gravar(self, conjunto: Conjunto, *, ativo: bool) -> None:
        from sqlalchemy import text as _text
        sql = _text(f"""
            INSERT INTO {TABELA} (versao, conjunto, ativo)
            VALUES (:versao, CAST(:conjunto AS jsonb), :ativo)
            ON CONFLICT (versao) DO UPDATE SET conjunto = EXCLUDED.conjunto
        """)
        with self._engine.begin() as conn:
            conn.execute(sql, {"versao": conjunto.versao,
                               "conjunto": json.dumps(conjunto.como_dict(),
                                                      ensure_ascii=False),
                               "ativo": ativo})

    def _ativar(self, versao: str) -> None:
        from sqlalchemy import text as _text
        with self._engine.begin() as conn:
            conn.execute(_text(f"UPDATE {TABELA} SET ativo = FALSE "
                               "WHERE ativo IS TRUE"))
            conn.execute(_text(f"UPDATE {TABELA} SET ativo = TRUE, "
                               "promovido_em = NOW() WHERE versao = :v"),
                         {"v": versao})

    def _ler_do_banco(self) -> Conjunto | None:
        from sqlalchemy import text as _text
        try:
            with self._engine.begin() as conn:
                linha = conn.execute(_text(
                    f"SELECT conjunto FROM {TABELA} WHERE ativo IS TRUE "
                    "LIMIT 1")).scalar()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pesos: leitura do registro falhou (%s); "
                           "usando o prior", exc)
            return None
        if not linha:
            return None
        dados = json.loads(linha) if isinstance(linha, str) else linha
        return Conjunto(
            versao=dados["versao"],
            pesos_relevancia=dict(dados.get("pesos_relevancia") or {}),
            notas_tipo={k: tuple(v) for k, v in
                        (dados.get("notas_tipo") or {}).items()},
            calibrado=bool(dados.get("calibrado")),
            origem=str(dados.get("origem") or "banco"),
            evidencia=dict(dados.get("evidencia") or {}),
            limitacoes=tuple(dados.get("limitacoes") or ()),
        )
