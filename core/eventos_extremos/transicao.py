"""Evidência entra, nível sai -- e a regra que decidiu sai junto.

Este é o módulo em que a especificação vira número. Cada regra anti-alarme-falso
que ela enuncia em português tem aqui uma constante nomeada e um
:class:`RegraAplicada` no resultado, para que "por que estamos no Nível 3?" tenha
resposta sem ninguém precisar reler o código.

Três decisões de projeto que mudam o comportamento
---------------------------------------------------
**A carteira não origina evento; ela o dimensiona.** Se a severidade fosse a
média das três classes, uma carteira concentrada geraria Nível 2 permanente sem
que nada tivesse acontecido no mundo -- uma fábrica de alarme falso movida a
característica estrutural. Aqui o evento nasce das evidências informacional e de
mercado, e a de carteira multiplica dentro de uma faixa declarada
(:data:`AMPLITUDE_CARTEIRA`). Exposição zero amortece; nunca inventa.

**Mercado fechado não é mercado calmo.** A regra "divergência entre manchete e
mercado reduz a confiança" só pode disparar quando o mercado foi *medido*. Com a
bolsa fechada a evidência de mercado é ``None``, e ``None`` não contradiz nada --
ele apenas segura o teto no Nível 3, porque o Nível 4 exige confirmação de preço.
Confundir os dois faria o motor rebaixar um evento grave de madrugada.

**Subir é rápido; descer é devagar.** Escalar acontece na mesma avaliação em que
a evidência aparece. Rebaixar exige que o estado anterior tenha durado
:data:`PERMANENCIA_MINIMA_HORAS` e desce **um nível por avaliação**. Sem isso o
estado oscila entre 2 e 3 a cada coleta, e um painel que pisca é um painel que
ninguém lê. O caminho rápido para baixo existe, mas é explícito e humano:
:func:`encerrar`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

from core.eventos_extremos import EVENTOS_EXTREMOS_VERSAO, niveis
from core.eventos_extremos import evidencias as ev

# ── Configuração: severidade -> nível ─────────────────────────────────────────
#: Piso de severidade combinada para cada nível, do mais grave para o mais leve.
#: Abaixo do último, Nível 0.
LIMIARES_DE_NIVEL: tuple[tuple[int, float], ...] = (
    (niveis.NIVEL_SISTEMICO, 0.80),
    (niveis.NIVEL_CRISE, 0.62),
    (niveis.NIVEL_VIGILANCIA, 0.42),
    (niveis.NIVEL_ATENCAO, 0.22),
)

#: Peso de cada classe na severidade do evento. A carteira não aparece aqui de
#: propósito -- ver o cabeçalho.
PESO_INFORMACIONAL = 1.0
PESO_MERCADO = 1.2

#: A evidência de carteira multiplica a severidade do evento dentro de
#: ``[1 - AMPLITUDE, 1 + AMPLITUDE]``. Carteira não medida vale 1,0 (neutro).
AMPLITUDE_CARTEIRA = 0.25

# ── Configuração: regras anti-alarme-falso ────────────────────────────────────
#: Abaixo disto, um veículo isolado não sustenta nada além de monitoramento.
PISO_CONFIABILIDADE_ISOLADA = 0.60

#: Duas fontes independentes é o degrau em que ``fontes_independentes`` deixa de
#: ser "fonte única". Casado com ``ev.SEVERIDADE_POR_N_FONTES``.
SEVERIDADE_DUAS_FONTES = ev.SEVERIDADE_POR_N_FONTES[2]

#: Materialidade a partir da qual fonte oficial gera alerta imediato.
MATERIALIDADE_ALERTA_IMEDIATO = 0.85

#: Divergência: manchete forte contra mercado medido e calmo.
DIVERGENCIA_INFORMACIONAL_ALTA = 0.60
DIVERGENCIA_MERCADO_CALMO = 0.25
FATOR_CONFIANCA_NA_DIVERGENCIA = 0.60

#: Cobertura mínima do conjunto para o motor poder declarar Crise ou Sistêmico.
COBERTURA_MINIMA_PARA_CRISE = 0.45

#: Exposição (direta e indireta) abaixo disto é "não alcança esta carteira".
EXPOSICAO_IRRELEVANTE = 0.02

# ── Configuração: histerese e notificação ─────────────────────────────────────
#: Tempo mínimo num nível antes de poder descer dele.
PERMANENCIA_MINIMA_HORAS = 12.0

#: Variação de severidade que conta como mudança material para reemitir alerta.
DELTA_MATERIAL = 0.15

# ── Chaves das regras ─────────────────────────────────────────────────────────
R_FONTE_FRACA = "fonte_fraca_nao_ativa_crise"
R_FONTE_OFICIAL = "fonte_oficial_alerta_imediato"
R_DUAS_FONTES = "duas_fontes_elevam_severidade"
R_DIVERGENCIA = "divergencia_manchete_mercado"
R_LOCALIZADA = "crise_localizada_nao_e_sistemica"
R_SEM_MERCADO = "sem_evidencia_de_mercado"
R_COBERTURA = "cobertura_insuficiente_para_escalar"
R_SEM_EXPOSICAO = "evento_nao_alcanca_a_carteira"
R_PERMANENCIA = "permanencia_minima_antes_de_rebaixar"
R_DESCIDA_GRADUAL = "rebaixamento_de_um_nivel_por_avaliacao"
R_ENCERRAMENTO = "encerramento_explicito"

EFEITO_TETO = "teto"
EFEITO_PISO = "piso"
EFEITO_CONFIANCA = "confianca"
EFEITO_REGISTRO = "registro"


@dataclass(frozen=True)
class RegraAplicada:
    """Uma regra que efetivamente mexeu (ou constatou) algo, com o motivo.

    Regras que não se aplicaram não entram: uma lista de trinta "não incidiu"
    esconde as três que incidiram. As que constatam sem mexer entram com efeito
    ``registro``, porque "duas fontes independentes confirmaram" é parte da
    justificativa mesmo não alterando número nenhum.
    """

    chave: str
    efeito: str
    motivo: str
    de: int | None = None
    para: int | None = None

    def descrever(self) -> str:
        if self.de is not None and self.para is not None and self.de != self.para:
            return f"[{self.efeito}] {self.chave}: {self.de} -> {self.para} ({self.motivo})"
        return f"[{self.efeito}] {self.chave}: {self.motivo}"


@dataclass(frozen=True)
class Estado:
    """O que fica gravado entre uma avaliação e a próxima.

    ``versao_metodologia`` viaja no estado pelo mesmo motivo que viaja na chave
    das avaliações de notícia: um Nível 3 declarado sob limiares antigos e outro
    sob os novos não são o mesmo fato, e o histórico precisa poder distingui-los.
    """

    nivel: int = niveis.NIVEL_NORMAL
    severidade: float = 0.0
    confianca: float = 0.0
    evento_id: str | None = None
    abrangencia: str | None = None
    desde: dt.datetime | None = None
    atualizado_em: dt.datetime | None = None
    notificado_em: dt.datetime | None = None
    encerrado: bool = False
    motivo_encerramento: str = ""
    versao_metodologia: str = EVENTOS_EXTREMOS_VERSAO

    @property
    def objeto_nivel(self) -> niveis.Nivel:
        return niveis.de_codigo(self.nivel)

    def horas_no_nivel(self, agora: dt.datetime) -> float | None:
        if self.desde is None:
            return None
        return (agora - self.desde).total_seconds() / 3600.0


@dataclass(frozen=True)
class Veredito:
    """A decisão, com tudo que a sustentou.

    ``nivel_bruto`` é o que a severidade sozinha pediria; ``nivel`` é o que
    sobrou depois dos tetos. Publicar os dois é o que permite dizer "o 4 foi
    avaliado e barrado pela cobertura" em vez de deixar parecer que ele nunca
    esteve na mesa.
    """

    nivel: niveis.Nivel
    nivel_bruto: int
    severidade: float
    confianca: float
    severidade_evento: float | None
    severidade_carteira: float | None
    cobertura: dict[str, float]
    regras: tuple[RegraAplicada, ...]
    limitacoes: tuple[str, ...]
    abrangencia: str | None
    notificar: bool
    estado: Estado
    anterior: Estado | None = None

    @property
    def suspende_recomendacao(self) -> bool:
        return self.nivel.suspende_recomendacao

    @property
    def teto_aplicado(self) -> int:
        tetos = [r.para for r in self.regras
                 if r.efeito == EFEITO_TETO and r.para is not None]
        return min(tetos) if tetos else niveis.NIVEL_SISTEMICO

    def justificativa(self) -> tuple[str, ...]:
        return tuple(r.descrever() for r in self.regras)


def _agora(valor: dt.datetime | None) -> dt.datetime:
    return valor or dt.datetime.now(dt.timezone.utc)


def _nivel_da_severidade(severidade: float) -> int:
    for codigo, piso in LIMIARES_DE_NIVEL:
        if severidade >= piso:
            return codigo
    return niveis.NIVEL_NORMAL


def _severidade_do_evento(conjunto: ev.Conjunto) -> float | None:
    """Média ponderada das evidências informacional e de mercado.

    Classe não medida sai da média (não vira zero) -- e a cobertura, publicada
    separadamente, é o que impede que sair da média vire vantagem.
    """
    partes = ((conjunto.informacional.severidade, PESO_INFORMACIONAL),
              (conjunto.mercado.severidade, PESO_MERCADO))
    peso = sum(p for v, p in partes if v is not None)
    if peso <= 0:
        return None
    return sum(v * p for v, p in partes if v is not None) / peso


def _fator_carteira(severidade_carteira: float | None) -> float:
    if severidade_carteira is None:
        return 1.0
    return 1.0 - AMPLITUDE_CARTEIRA + 2.0 * AMPLITUDE_CARTEIRA * severidade_carteira


def _confianca(conjunto: ev.Conjunto) -> float:
    """Cobertura ponderada das classes que participaram da severidade.

    A carteira entra com peso menor porque ela não decide *se* houve evento;
    ela decide o tamanho. Uma carteira bem medida não pode fazer parecer que o
    evento está bem apurado.
    """
    pares = ((conjunto.informacional, PESO_INFORMACIONAL),
             (conjunto.mercado, PESO_MERCADO),
             (conjunto.carteira, 0.6))
    total = sum(p for _, p in pares)
    return sum(e.cobertura * p for e, p in pares) / total


def _fonte_oficial(info: ev.Evidencia) -> bool | None:
    valor = info.valor_de("fonte_oficial")
    return None if valor is None else valor >= 1.0


def _tem_duas_fontes(info: ev.Evidencia) -> bool | None:
    valor = info.valor_de("fontes_independentes")
    return None if valor is None else valor >= SEVERIDADE_DUAS_FONTES


def avaliar(
    conjunto: ev.Conjunto,
    *,
    abrangencia: str | None = None,
    anterior: Estado | None = None,
    evento_id: str | None = None,
    agora: dt.datetime | None = None,
) -> Veredito:
    """Decide o nível a partir das três classes de evidência.

    Args:
        conjunto: as evidências informacional, de mercado e de carteira.
        abrangencia: uma das chaves de :data:`core.eventos_extremos.niveis.ABRANGENCIAS`.
            Ausente recebe o teto mais restritivo, não o mais permissivo.
        anterior: estado gravado da avaliação anterior do mesmo evento. Sem ele,
            não há histerese nem janela de silêncio -- toda avaliação é a
            primeira.
        evento_id: identificador do evento agrupado (``core.noticias.eventos``).
        agora: instante da avaliação, em UTC. Injetável para teste.

    Returns:
        Um :class:`Veredito` com o nível, o nível bruto, as regras aplicadas e o
        estado novo pronto para gravar.
    """
    instante = _agora(agora)
    regras: list[RegraAplicada] = []

    sev_evento = _severidade_do_evento(conjunto)
    sev_carteira = conjunto.carteira.severidade
    severidade = 0.0 if sev_evento is None else min(
        1.0, max(0.0, sev_evento * _fator_carteira(sev_carteira)))
    confianca = _confianca(conjunto)
    bruto = _nivel_da_severidade(severidade)

    info = conjunto.informacional
    teto = niveis.NIVEL_SISTEMICO
    piso = niveis.NIVEL_NORMAL

    def _aplicar_teto(chave: str, novo_teto: int, motivo: str) -> None:
        nonlocal teto
        if novo_teto < teto:
            regras.append(RegraAplicada(chave, EFEITO_TETO, motivo,
                                        de=teto, para=novo_teto))
            teto = novo_teto

    # R5 — crise localizada não é sistêmica automaticamente.
    teto_abr = niveis.teto_por_abrangencia(abrangencia)
    if teto_abr < niveis.NIVEL_SISTEMICO:
        _aplicar_teto(
            R_LOCALIZADA, teto_abr,
            f"abrangência {abrangencia or 'não declarada'} não sustenta sistêmico")

    # R1 — fonte isolada e fraca inicia monitoramento, não crise.
    oficial = _fonte_oficial(info)
    duas = _tem_duas_fontes(info)
    conf_veiculo = info.valor_de("confiabilidade")
    if (oficial is not True and duas is not True
            and (conf_veiculo is None or conf_veiculo < PISO_CONFIABILIDADE_ISOLADA)):
        _aplicar_teto(
            R_FONTE_FRACA, niveis.NIVEL_ATENCAO,
            "sem fonte oficial, sem confirmação independente e sem veículo "
            "confiável: só monitoramento")
    elif duas is True and (conf_veiculo or 0.0) >= PISO_CONFIABILIDADE_ISOLADA:
        regras.append(RegraAplicada(
            R_DUAS_FONTES, EFEITO_REGISTRO,
            "duas ou mais fontes independentes confiáveis sustentam a severidade"))

    # R6 — sem evidência de mercado, o Nível 4 fica fora de alcance.
    if conjunto.mercado.severidade is None:
        _aplicar_teto(
            R_SEM_MERCADO, niveis.NIVEL_MAXIMO_SEM_EVIDENCIA_DE_MERCADO,
            "nenhum indicador de mercado medido: o preço não confirmou nem "
            "desmentiu a informação")
    else:
        # R4 — divergência entre manchete e mercado reduz a confiança.
        sev_info = info.severidade
        sev_mkt = conjunto.mercado.severidade
        if (sev_info is not None and sev_info >= DIVERGENCIA_INFORMACIONAL_ALTA
                and sev_mkt <= DIVERGENCIA_MERCADO_CALMO):
            confianca *= FATOR_CONFIANCA_NA_DIVERGENCIA
            regras.append(RegraAplicada(
                R_DIVERGENCIA, EFEITO_CONFIANCA,
                f"informação forte ({sev_info:.2f}) contra mercado medido e "
                f"calmo ({sev_mkt:.2f}): confiança reduzida a "
                f"{FATOR_CONFIANCA_NA_DIVERGENCIA:.0%}"))
            _aplicar_teto(R_DIVERGENCIA, niveis.NIVEL_VIGILANCIA,
                          "manchete não confirmada pelos preços")

    # R7 — cobertura insuficiente não escala para Crise.
    if confianca < COBERTURA_MINIMA_PARA_CRISE:
        _aplicar_teto(
            R_COBERTURA, niveis.NIVEL_VIGILANCIA,
            f"cobertura ponderada de {confianca:.0%} abaixo do mínimo de "
            f"{COBERTURA_MINIMA_PARA_CRISE:.0%} para declarar crise")

    # R8 — evento medido que não alcança esta carteira.
    # Pergunta pela exposição BRUTA, não pela severidade normalizada: tudo
    # abaixo do corte brando normaliza para 0,0, e perguntar ao normalizado
    # declararia "não alcança" para 4,9% do patrimônio num banco que quebrou.
    direta = conjunto.carteira.componente("exposicao_direta")
    indireta = conjunto.carteira.componente("exposicao_indireta")
    localizado = niveis.teto_por_abrangencia(abrangencia) < niveis.NIVEL_SISTEMICO
    if (localizado and direta is not None and indireta is not None
            and direta.medido and indireta.medido
            and direta.bruto is not None and indireta.bruto is not None
            and abs(direta.bruto) <= EXPOSICAO_IRRELEVANTE
            and abs(indireta.bruto) <= EXPOSICAO_IRRELEVANTE):
        _aplicar_teto(
            R_SEM_EXPOSICAO, niveis.NIVEL_ATENCAO,
            f"exposição direta ({direta.bruto:.1%}) e indireta "
            f"({indireta.bruto:.1%}) medidas e desprezíveis: o evento não "
            f"alcança esta carteira")

    # R2 — fonte oficial em evento de alta materialidade gera alerta imediato.
    # Só entra na trilha quando efetivamente levanta o nível: um piso que não
    # levantou nada, registrado como "4 -> 2", faz a auditoria ler rebaixamento
    # onde não houve nenhum.
    materialidade = info.valor_de("materialidade")
    sem_piso = min(bruto, teto)
    if oficial is True and materialidade is not None and \
            materialidade >= MATERIALIDADE_ALERTA_IMEDIATO:
        candidato = min(niveis.NIVEL_VIGILANCIA, teto)
        if candidato > sem_piso:
            piso = candidato
            regras.append(RegraAplicada(
                R_FONTE_OFICIAL, EFEITO_PISO,
                f"fonte oficial em evento de materialidade {materialidade:.2f}: "
                f"alerta imediato, sem esperar confirmação de mercado",
                de=sem_piso, para=piso))

    nivel_codigo = max(piso, sem_piso)

    # R9/R10 — histerese na descida.
    if anterior is not None and not anterior.encerrado:
        nivel_codigo = _aplicar_histerese(anterior, nivel_codigo, instante, regras)

    nivel = niveis.de_codigo(nivel_codigo)
    estado = _novo_estado(anterior, nivel_codigo, severidade, confianca,
                          evento_id, abrangencia, instante)
    notificar = deve_notificar(anterior, estado, nivel, instante)
    if notificar:
        estado = replace(estado, notificado_em=instante)

    limitacoes = tuple(dict.fromkeys(
        list(info.limitacoes) + list(conjunto.mercado.limitacoes)
        + list(conjunto.carteira.limitacoes)))

    return Veredito(
        nivel=nivel,
        nivel_bruto=bruto,
        severidade=severidade,
        confianca=confianca,
        severidade_evento=sev_evento,
        severidade_carteira=sev_carteira,
        cobertura={
            ev.CLASSE_INFORMACIONAL: info.cobertura,
            ev.CLASSE_MERCADO: conjunto.mercado.cobertura,
            ev.CLASSE_CARTEIRA: conjunto.carteira.cobertura,
        },
        regras=tuple(regras),
        limitacoes=limitacoes,
        abrangencia=abrangencia,
        notificar=notificar,
        estado=estado,
        anterior=anterior,
    )


def _aplicar_histerese(anterior: Estado, proposto: int, instante: dt.datetime,
                       regras: list[RegraAplicada]) -> int:
    """Escalar é imediato; rebaixar exige permanência e desce um degrau."""
    if proposto >= anterior.nivel:
        return proposto

    horas = anterior.horas_no_nivel(instante)
    if horas is not None and horas < PERMANENCIA_MINIMA_HORAS:
        regras.append(RegraAplicada(
            R_PERMANENCIA, EFEITO_PISO,
            f"apenas {horas:.1f}h no nível atual, abaixo do mínimo de "
            f"{PERMANENCIA_MINIMA_HORAS:g}h para rebaixar",
            de=proposto, para=anterior.nivel))
        return anterior.nivel

    if anterior.nivel - proposto > 1:
        alvo = anterior.nivel - 1
        regras.append(RegraAplicada(
            R_DESCIDA_GRADUAL, EFEITO_PISO,
            "rebaixamento limitado a um nível por avaliação",
            de=proposto, para=alvo))
        return alvo

    return proposto


def _novo_estado(anterior: Estado | None, nivel: int, severidade: float,
                 confianca: float, evento_id: str | None,
                 abrangencia: str | None, instante: dt.datetime) -> Estado:
    mudou = anterior is None or anterior.nivel != nivel or anterior.encerrado
    return Estado(
        nivel=nivel,
        severidade=severidade,
        confianca=confianca,
        evento_id=evento_id or (anterior.evento_id if anterior else None),
        abrangencia=abrangencia,
        desde=instante if mudou else anterior.desde,
        atualizado_em=instante,
        notificado_em=None if anterior is None else anterior.notificado_em,
        encerrado=False,
        motivo_encerramento="",
    )


def deve_notificar(anterior: Estado | None, novo: Estado, nivel: niveis.Nivel,
                   instante: dt.datetime) -> bool:
    """Alerta repetido só sai de novo se algo mudou de forma material.

    A regra é a da especificação, e o custo de errá-la é conhecido: alerta que
    repete todo dia sem novidade treina quem lê a ignorá-lo, e aí o alerta que
    importa chega num canal já morto.

    Nível 0 e 1 não notificam por si -- só a *mudança* para eles notifica, que é
    como o encerramento de uma crise chega a quem estava acompanhando.
    """
    if anterior is None:
        return nivel.codigo >= niveis.NIVEL_VIGILANCIA
    if anterior.nivel != novo.nivel:
        return True
    if nivel.codigo < niveis.NIVEL_VIGILANCIA:
        return False
    if abs(novo.severidade - anterior.severidade) >= DELTA_MATERIAL:
        return True
    if anterior.notificado_em is None:
        return True
    horas = (instante - anterior.notificado_em).total_seconds() / 3600.0
    return horas >= nivel.silencio_horas


def encerrar(anterior: Estado, motivo: str, *,
             agora: dt.datetime | None = None) -> Veredito:
    """Encerramento explícito: volta ao Nível 0 sem passar pela histerese.

    É o caminho humano de saída, e ele existe justamente porque a descida
    automática é lenta de propósito. Sem ele, encerrar uma crise que acabou
    levaria dias de avaliações -- e o requisito é textual: "o sistema deve
    permitir rebaixamento e encerramento explícito de crise".

    O motivo é obrigatório e não pode ser vazio: um encerramento sem motivo é
    exatamente o registro que ninguém consegue auditar depois.
    """
    texto = str(motivo or "").strip()
    if not texto:
        raise ValueError("encerramento exige motivo")

    instante = _agora(agora)
    estado = Estado(
        nivel=niveis.NIVEL_NORMAL,
        severidade=0.0,
        confianca=anterior.confianca,
        evento_id=anterior.evento_id,
        abrangencia=anterior.abrangencia,
        desde=instante,
        atualizado_em=instante,
        notificado_em=instante,
        encerrado=True,
        motivo_encerramento=texto,
    )
    regra = RegraAplicada(R_ENCERRAMENTO, EFEITO_PISO, texto,
                          de=anterior.nivel, para=niveis.NIVEL_NORMAL)
    return Veredito(
        nivel=niveis.de_codigo(niveis.NIVEL_NORMAL),
        nivel_bruto=niveis.NIVEL_NORMAL,
        severidade=0.0,
        confianca=anterior.confianca,
        severidade_evento=None,
        severidade_carteira=None,
        cobertura={c: 0.0 for c in ev.CLASSES},
        regras=(regra,),
        limitacoes=(),
        abrangencia=anterior.abrangencia,
        notificar=True,
        estado=estado,
        anterior=anterior,
    )
