"""Motor de Eventos Extraordinários e Índice de Antifragilidade.

Guerra, pandemia, quebra de banco, crise de liquidez, evento climático extremo,
choque político, fiscal, cambial ou regulatório não esperam o rebalanceamento
mensal. Este pacote é a resposta a isso -- e o que ele **não** promete é a parte
mais importante da definição:

**Ele não prevê cisne negro.** Não há aqui nenhuma função que estime a
probabilidade de um evento extraordinário acontecer. O que há é: preparar a
carteira antes, reconhecer depressa que o regime mudou, medir a exposição de
quem está exposto, alertar na proporção da evidência, recalcular cenários,
apresentar plano e sair do modo de crise quando a evidência sair.

**Ele não executa.** Nenhum caminho deste pacote produz ordem, peso ou
rebalanceamento. O teto de qualquer nível -- inclusive do Nível 4 -- é uma
proposta com justificativa, risco de agir, risco de não agir, custo, imposto,
liquidez e condição de desfazer, para confirmação humana explícita.

O pacote **orquestra**, não recalcula
------------------------------------
Boa parte do que a especificação pede já existe neste repositório, e duplicar
qualquer uma dessas peças seria criar uma segunda verdade:

``core.noticias``            evidência informacional: confiabilidade do veículo,
                             contagem de fontes independentes por cluster de
                             quase-duplicata, seis portões conjuntivos, impacto
                             separado em direção/probabilidade/faixa/horizonte.
``core.memoria_mercado``     o passo "compare com eventos históricos", com ``n``
                             declarado e retorno anormal contra benchmark.
``core.global_portfolio``    concentração (HHI por ativo, setor, país, moeda),
                             risco (VaR/CVaR históricos), papéis dos ativos.
``core.stress_tests``        "se acontecer crise tipo 2008, perde X e leva Y".
``core.copulas``             dependência de cauda -- correlação que sobe no
                             estresse, que é o que a especificação pede medir.
``core.liquidez``            liquidez que sustenta decisão quando as fontes
                             discordam.

Módulos
-------
``niveis``          vocabulário fechado dos cinco estados e do que cada um
                    autoriza.
``evidencias``      as três classes de evidência, cada uma com sua cobertura.
``transicao``       evidência -> nível, com as regras anti-alarme-falso
                    escritas como configuração e não como adjetivo.
"""
from __future__ import annotations

#: Sobe quando qualquer limiar, peso ou regra de transição muda. Sem isso, um
#: estado gravado sob a regra antiga fica indistinguível de um gravado sob a
#: nova, e a comparação histórica passa a somar maçãs com laranjas -- o mesmo
#: erro que ``TAXONOMIA_VERSAO`` existe para evitar em ``core.noticias``.
EVENTOS_EXTREMOS_VERSAO = "1.0.0"
