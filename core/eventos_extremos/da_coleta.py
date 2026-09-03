"""
core/eventos_extremos/da_coleta.py
==================================
A ponte entre o que a coleta apurou e o nível que governa a cadência.

Por que este módulo existe
--------------------------
``estado_coleta.definir_modo`` nasceu com o docstring *"quem chama é o motor de
eventos extremos, e só ele"* -- e **ninguém o chamava**. O efeito não era um
erro: era um silêncio. O job de notícias lia ``estado.modo`` do banco, o banco
guardava ``normal`` desde sempre, e a coleta seguia no ritmo de dia calmo
exatamente no dia em que deixaria de ser calmo. A infraestrutura de aceleração
estava inteira, testada, e desligada da tomada.

O que esta ponte pode e o que ela não pode
------------------------------------------
Ela avalia com **uma classe de evidência só** -- a informacional. O job de
coleta não tem preço nem carteira na mão: pedir isso a ele criaria uma segunda
leitura de mercado ao lado de ``eventos_extremos.mercado``, e duas leituras
divergentes do mesmo pregão são pior que uma ausente.

Isso não é uma limitação escondida; é uma limitação **que o próprio motor já
cobra**. Sem evidência de mercado, a regra R6 põe teto em
``NIVEL_MAXIMO_SEM_EVIDENCIA_DE_MERCADO``: a manchete sozinha acelera a coleta,
e não declara crise. É a ordem certa -- primeiro se olha melhor, depois se
conclui. Acelerar por manchete custa cota; concluir por manchete custa a
decisão.

Abrangência: ``macro`` na taxonomia vira ``pais``, e não ``global``
------------------------------------------------------------------
O escopo da taxonomia diz de que *tipo* de fato se trata, não que tamanho ele
teve. Traduzir ``macro`` para ``global`` deixaria o teto sistêmico ao alcance de
uma manchete, que é precisamente o alarme falso que o Prompt do motor mandou
evitar. ``pais`` mantém o teto em Crise e continua acelerando a coleta.
"""
from __future__ import annotations

import datetime as dt
import logging

from core.eventos_extremos import evidencias as ev
from core.eventos_extremos import niveis, transicao
from core.noticias import taxonomia

logger = logging.getLogger(__name__)

#: Escopo da taxonomia -> abrangência do motor. Ver o docstring do módulo.
ABRANGENCIA_POR_ESCOPO = {
    taxonomia.ESCOPO_ATIVO: niveis.ABRANGENCIA_ATIVO,
    taxonomia.ESCOPO_SETOR: niveis.ABRANGENCIA_SETOR,
    taxonomia.ESCOPO_MACRO: niveis.ABRANGENCIA_PAIS,
}

LIMITACAO_SEM_MERCADO = (
    "avaliação feita só com evidência informacional: a coleta não mede preço, "
    "e por isso o nível não passa do teto sem evidência de mercado")


def _horas(quando: dt.datetime | None, agora: dt.datetime) -> float | None:
    if quando is None:
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (agora - quando).total_seconds() / 3600.0)


def evidencia_do_evento(evento, *, agora: dt.datetime | None = None) -> ev.Evidencia:
    """Traduz um :class:`core.noticias.eventos.Evento` na classe informacional.

    Nada aqui é medido de novo: cada campo já foi apurado pela coleta, e
    recalcular confiabilidade ou contagem de fontes num segundo lugar criaria
    duas respostas para a mesma pergunta.
    """
    agora = agora or dt.datetime.now(dt.timezone.utc)
    tipo = taxonomia.tipo(getattr(evento, "tipo", None))
    return ev.informacional(
        fonte_oficial=bool(evento.confirmado_por_primaria),
        n_fontes_independentes=int(evento.n_fontes_independentes),
        confiabilidade_maxima=max(
            (n.confiabilidade for n in evento.noticias), default=None),
        horas_desde_publicacao=_horas(evento.ultimo_em, agora),
        materialidade=float(tipo.materialidade),
        abrangencia=ABRANGENCIA_POR_ESCOPO.get(tipo.escopo),
    )


def avaliar_coleta(eventos, *, agora: dt.datetime | None = None):
    """Nível imposto pelo evento mais severo da coleta. ``None`` se não houver.

    Devolver ``None`` para coleta vazia é deliberado: "nenhum evento apurado"
    não é "nível Normal apurado". Quem recebe ``None`` mantém o modo que já
    estava, e não rebaixa a cadência por uma coleta que não trouxe nada --
    inclusive porque uma coleta vazia pode ser uma coleta que falhou.
    """
    agora = agora or dt.datetime.now(dt.timezone.utc)
    melhor = None
    for evento in eventos or ():
        try:
            info = evidencia_do_evento(evento, agora=agora)
        except Exception:  # noqa: BLE001 - um evento torto não cala os outros
            logger.exception("evento sem evidência informacional utilizável")
            continue
        tipo = taxonomia.tipo(getattr(evento, "tipo", None))
        conjunto = ev.Conjunto(informacional=info)
        veredito = transicao.avaliar(
            conjunto,
            abrangencia=ABRANGENCIA_POR_ESCOPO.get(tipo.escopo),
            evento_id=getattr(evento, "id", None),
            agora=agora)
        if melhor is None or veredito.nivel.codigo > melhor.nivel.codigo:
            melhor = veredito
    return melhor


#: Nível a partir do qual a cadência acelera de fato.
#:
#: Não é uma regra a mais em cima das de ``transicao``: é o reconhecimento de
#: que o Nível 1 é o **piso alcançável** por qualquer manchete fresca, e não um
#: sinal. Medido em 03/09/2026 com os componentes da evidência informacional:
#:
#:   manchete banal, tipo ``indefinido``, veículo de confiabilidade 0,20,
#:   publicada há 2 h  ->  severidade **0,347**
#:
#: O limiar de Atenção é 0,22. Como ``recencia`` vale 1,0 para tudo que acabou
#: de sair e ``materialidade`` tem piso 0,25, nenhuma notícia recém-publicada
#: fica abaixo de 0,22. Ligar a cadência ao Nível 1 deixaria a coleta em
#: Vigilância para sempre -- 240 min viram 60 min todo ciclo, quatro vezes a
#: cota diária dos provedores gratuitos --, e um estado permanente não carrega
#: informação nenhuma. É o "portão que só podia dar True": critério que a
#: amostra inteira atende deixa de ser critério.
#:
#: A escolha também é coerente com a R1 do próprio motor: fonte isolada e fraca
#: tem teto em Atenção justamente por ainda não ser evidência. Acelerar por ela
#: seria gastar cota com aquilo que a regra acabou de declarar insuficiente.
PISO_PARA_ACELERAR = niveis.NIVEL_VIGILANCIA


def nivel_para_cadencia(veredito) -> int | None:
    """Nível que a cadência deve obedecer. ``None`` quando não há o que dizer.

    Rebaixa a Normal o que ficou abaixo do piso -- e rebaixar aqui é correto:
    o ciclo anterior pode ter acelerado por um evento que já passou, e manter a
    aceleração por inércia gastaria cota sem evidência que a sustente.
    """
    if veredito is None:
        return None
    codigo = int(veredito.nivel.codigo)
    return codigo if codigo >= PISO_PARA_ACELERAR else niveis.NIVEL_NORMAL
