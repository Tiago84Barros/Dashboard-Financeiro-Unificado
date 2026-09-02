"""Política de alerta: quem é avisado, por onde, e o que sai de casa.

Divisão de trabalho
-------------------
*Se* alerta é ``core.eventos_extremos.transicao.deve_notificar`` -- ele já
implementa a regra de não repetir sem mudança material, e duplicá-la aqui
criaria duas verdades sobre a mesma pergunta. Este módulo decide o resto:
**por onde** o alerta sai, **o que** o texto pode conter e **o que fica
registrado**.

Por que o canal externo tem regra própria
-----------------------------------------
Notificação externa sai do computador do usuário. O requisito é textual --
"nunca expor informações sensíveis em notificações externas" -- e a forma de
garantir isso não é lembrar de escrever com cuidado: é
:func:`redigir_externo` reconstruir o texto do zero a partir de campos de uma
lista curta, em vez de filtrar o texto interno. Filtro de saída falha em
silêncio na primeira frase que ninguém previu; reconstrução falha fechada,
porque o que não está na lista simplesmente não é escrito.

Por isso a mensagem externa não carrega valor em reais, nem símbolo de ativo,
nem peso de carteira, nem prioridade de aporte. Ela carrega nível, tipo de
evento e o convite para abrir o painel.

Nível decide canal, não urgência de linguagem
---------------------------------------------
- Nível 1: só o painel. Nada é enviado.
- Nível 2: notificação normal, e **somente** se o evento tocar a carteira.
- Níveis 3 e 4: alerta destacado no painel; canal externo apenas quando há
  infraestrutura configurada *e* autorização explícita do usuário. Ausência de
  autorização não vira envio "porque é grave".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass, field, replace

from core.eventos_extremos import niveis

logger = logging.getLogger(__name__)

ALERTAS_VERSAO = "1.0.0"

# ── Canais ───────────────────────────────────────────────────────────────────
CANAL_PAINEL = "painel"
CANAL_DESTAQUE = "painel_destacado"
CANAL_EXTERNO = "externo"
CANAIS: tuple[str, ...] = (CANAL_PAINEL, CANAL_DESTAQUE, CANAL_EXTERNO)

APARENCIA_CANAL: dict[str, dict[str, str]] = {
    CANAL_PAINEL: {"rotulo": "Somente no painel", "icone": "○"},
    CANAL_DESTAQUE: {"rotulo": "Destacado no painel", "icone": "▲"},
    CANAL_EXTERNO: {"rotulo": "Canal externo", "icone": "✉"},
}

# ── Estados de entrega ───────────────────────────────────────────────────────
ENTREGUE = "entregue"
NAO_ENVIADO = "nao_enviado"
BLOQUEADO_SEM_AUTORIZACAO = "bloqueado_sem_autorizacao"
BLOQUEADO_SEM_INFRA = "bloqueado_sem_infraestrutura"
SUPRIMIDO_SEM_MUDANCA = "suprimido_sem_mudanca_material"
SUPRIMIDO_ABAIXO_DA_SEVERIDADE = "suprimido_abaixo_da_severidade_escolhida"
SUPRIMIDO_FORA_DA_CARTEIRA = "suprimido_evento_nao_toca_a_carteira"
FALHOU = "falhou"

#: Campos que a mensagem externa pode conter. Nada fora desta lista sai.
CAMPOS_EXTERNOS_PERMITIDOS: frozenset[str] = frozenset(
    {"nivel_codigo", "nivel_rotulo", "tipo_evento", "abrangencia", "quando"})


@dataclass(frozen=True)
class Preferencias:
    """O que o usuário escolheu. O padrão é o mais silencioso que funciona."""

    severidade_minima: int = niveis.NIVEL_ATENCAO
    canais_externos: tuple[str, ...] = ()
    autorizou_externo: bool = False
    so_se_afetar_carteira: bool = True

    def aceita(self, codigo: int) -> bool:
        return codigo >= self.severidade_minima


@dataclass(frozen=True)
class Alerta:
    """Um alerta pronto para ser exibido e, talvez, enviado."""

    id: str
    nivel_codigo: int
    nivel_rotulo: str
    canal: str
    titulo: str
    corpo: str
    criado_em: dt.datetime
    tipo_evento: str = ""
    abrangencia: str | None = None
    afeta_carteira: bool = False
    severidade: float | None = None
    motivo_canal: str = ""
    entregue_em: dt.datetime | None = None
    lido_em: dt.datetime | None = None
    estado_entrega: str = NAO_ENVIADO
    detalhe_entrega: str = ""
    historico: tuple[str, ...] = field(default_factory=tuple)

    @property
    def destacado(self) -> bool:
        return self.canal in (CANAL_DESTAQUE, CANAL_EXTERNO)

    @property
    def aparencia(self) -> dict[str, str]:
        return APARENCIA_CANAL[self.canal]

    def texto_externo(self) -> str:
        """A mensagem que pode sair do computador -- e só ela."""
        return redigir_externo(self)


def _identificador(evento_id: str, codigo: int, severidade: float | None,
                   quando: dt.datetime) -> str:
    """Chave estável do alerta, com a severidade dentro.

    A severidade entra de propósito: dois alertas do mesmo evento e mesmo nível
    com severidades diferentes são fatos diferentes, e uma chave que os funde
    faria o registro dizer "já avisei" sobre um agravamento que ninguém viu.
    """
    sev = "na" if severidade is None else f"{severidade:.3f}"
    bruto = f"{evento_id}|{codigo}|{sev}|{quando.date().isoformat()}"
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def canal_para(codigo: int, *, afeta_carteira: bool, prefs: Preferencias,
               infraestrutura: bool) -> tuple[str, str]:
    """Traduz nível em canal, devolvendo também o porquê.

    O motivo viaja junto porque um alerta que não saiu precisa poder explicar
    por que não saiu -- silêncio sem motivo é indistinguível de falha.
    """
    if codigo <= niveis.NIVEL_ATENCAO:
        return CANAL_PAINEL, "Nível 1 fica no painel, sem notificação."

    if codigo == niveis.NIVEL_VIGILANCIA:
        if prefs.so_se_afetar_carteira and not afeta_carteira:
            return CANAL_PAINEL, ("Nível 2 sem exposição da carteira: "
                                  "notificação não se justifica.")
        return CANAL_DESTAQUE, "Nível 2 com exposição da carteira."

    if not infraestrutura:
        return CANAL_DESTAQUE, ("Nível alto sem canal externo configurado: "
                                "o alerta fica destacado no painel.")
    if not prefs.autorizou_externo or not prefs.canais_externos:
        return CANAL_DESTAQUE, ("Nível alto sem autorização para canal externo: "
                                "o alerta fica destacado no painel.")
    return CANAL_EXTERNO, "Nível alto com infraestrutura e autorização."


def _titulo(nivel: niveis.Nivel, tipo_evento: str) -> str:
    """Título factual. Sem ponto de exclamação, sem 'urgente', sem 'pânico'."""
    assunto = tipo_evento.replace("_", " ") if tipo_evento else "evento de mercado"
    return f"Nível {nivel.codigo} — {nivel.rotulo}: {assunto}"


def montar(veredito, *, tipo_evento: str = "", evento_id: str = "",
           afeta_carteira: bool = False, prefs: Preferencias | None = None,
           infraestrutura: bool = False, resumo: str = "",
           agora: dt.datetime | None = None) -> Alerta | None:
    """Constrói o alerta de um veredito, ou devolve ``None`` se não há alerta.

    ``None`` sai apenas quando ``transicao`` já decidiu que não há mudança
    material a comunicar -- a regra de não repetir mora lá, não aqui.
    """
    quando = agora or dt.datetime.now(dt.timezone.utc)
    prefs = prefs or Preferencias()
    nivel = veredito.nivel
    codigo = int(nivel.codigo)

    if not veredito.notificar and codigo <= niveis.NIVEL_ATENCAO:
        return None

    canal, motivo = canal_para(codigo, afeta_carteira=afeta_carteira,
                               prefs=prefs, infraestrutura=infraestrutura)

    corpo = resumo or nivel.resumo
    if veredito.teto_aplicado:
        corpo += (f" A avaliação bruta era Nível {veredito.nivel_bruto} e foi "
                  "barrada por regra de contenção.")
    if not veredito.notificar:
        motivo += " Sem mudança material desde o último aviso."

    return Alerta(
        id=_identificador(evento_id or tipo_evento or "sem_evento", codigo,
                          veredito.severidade, quando),
        nivel_codigo=codigo, nivel_rotulo=nivel.rotulo, canal=canal,
        titulo=_titulo(nivel, tipo_evento), corpo=corpo, criado_em=quando,
        tipo_evento=tipo_evento, abrangencia=veredito.abrangencia,
        afeta_carteira=afeta_carteira, severidade=veredito.severidade,
        motivo_canal=motivo,
        estado_entrega=NAO_ENVIADO if canal == CANAL_EXTERNO else ENTREGUE,
        historico=(f"{quando.isoformat()} · criado · {canal} · {motivo}",))


def redigir_externo(alerta: Alerta) -> str:
    """Reconstrói a mensagem externa a partir dos campos permitidos.

    Reconstruir em vez de filtrar é a diferença entre falhar fechado e falhar
    aberto. Nenhum valor de carteira, símbolo de ativo, peso, prioridade de
    aporte ou nome de fonte entra aqui -- e não entra porque não é lido, não
    porque foi removido depois.
    """
    campos = {
        "nivel_codigo": alerta.nivel_codigo,
        "nivel_rotulo": alerta.nivel_rotulo,
        "tipo_evento": (alerta.tipo_evento or "evento de mercado").replace("_", " "),
        "abrangencia": alerta.abrangencia or "não classificada",
        "quando": alerta.criado_em.strftime("%d/%m/%Y %H:%M UTC"),
    }
    assert set(campos) <= CAMPOS_EXTERNOS_PERMITIDOS  # guarda de manutenção
    return (f"Nível {campos['nivel_codigo']} ({campos['nivel_rotulo']}) em "
            f"{campos['quando']}. Tipo: {campos['tipo_evento']}. "
            f"Abrangência: {campos['abrangencia']}. "
            "Abra o painel para ver os detalhes da carteira.")


def enviar(alerta: Alerta, *, prefs: Preferencias, infraestrutura: bool,
           transportar=None, agora: dt.datetime | None = None) -> Alerta:
    """Tenta a entrega externa e devolve o alerta com o registro atualizado.

    Args:
        transportar: ``(canal, texto) -> None``. Sem ela, nada é enviado --
            o módulo não conhece nenhum transporte concreto de propósito.
    """
    quando = agora or dt.datetime.now(dt.timezone.utc)

    if alerta.canal != CANAL_EXTERNO:
        return _registrar(alerta, alerta.estado_entrega,
                          "alerta de painel: nada é enviado para fora", quando)
    if not infraestrutura:
        return _registrar(alerta, BLOQUEADO_SEM_INFRA,
                          "nenhum canal externo configurado", quando)
    if not prefs.autorizou_externo:
        return _registrar(alerta, BLOQUEADO_SEM_AUTORIZACAO,
                          "o usuário não autorizou envio externo", quando)
    if not prefs.aceita(alerta.nivel_codigo):
        return _registrar(alerta, SUPRIMIDO_ABAIXO_DA_SEVERIDADE,
                          f"abaixo da severidade escolhida "
                          f"({prefs.severidade_minima})", quando)
    if transportar is None:
        return _registrar(alerta, BLOQUEADO_SEM_INFRA,
                          "nenhum transporte disponível nesta execução", quando)

    texto = redigir_externo(alerta)
    falhas: list[str] = []
    entregues: list[str] = []
    for canal in prefs.canais_externos:
        try:
            transportar(canal, texto)
            entregues.append(canal)
        except Exception as erro:  # noqa: BLE001 — a falha vira registro
            logger.warning("falha ao enviar alerta por %s: %s", canal, erro)
            falhas.append(f"{canal}: {erro}")

    if not entregues:
        return _registrar(alerta, FALHOU, "; ".join(falhas) or "sem canal", quando)
    detalhe = "enviado por " + ", ".join(entregues)
    if falhas:
        detalhe += " · falhou em " + "; ".join(falhas)
    novo = _registrar(alerta, ENTREGUE, detalhe, quando)
    return replace(novo, entregue_em=quando)


def marcar_lido(alerta: Alerta, *, agora: dt.datetime | None = None) -> Alerta:
    quando = agora or dt.datetime.now(dt.timezone.utc)
    if alerta.lido_em is not None:
        return alerta
    novo = _registrar(alerta, alerta.estado_entrega, "lido pelo usuário", quando)
    return replace(novo, lido_em=quando)


def atualizar(alerta: Alerta, *, corpo: str, motivo: str,
              agora: dt.datetime | None = None) -> Alerta:
    """Atualiza o alerta preservando o registro do que ele dizia antes."""
    quando = agora or dt.datetime.now(dt.timezone.utc)
    novo = _registrar(alerta, alerta.estado_entrega,
                      f"atualizado: {motivo}", quando)
    return replace(novo, corpo=corpo)


def _registrar(alerta: Alerta, estado: str, detalhe: str,
               quando: dt.datetime) -> Alerta:
    linha = f"{quando.isoformat()} · {estado} · {detalhe}"
    return replace(alerta, estado_entrega=estado, detalhe_entrega=detalhe,
                   historico=alerta.historico + (linha,))
