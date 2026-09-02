"""Memória de Mercado: o que aconteceu das outras vezes, e o quanto isso vale hoje.

O Motor Conjuntural (``core.noticias``) responde "o que aconteceu e quão
relevante é". Este pacote responde a pergunta seguinte, que é outra: **quando um
fato desse tipo aconteceu antes, o que os preços fizeram?** -- e depois desconta
essa referência pela distância entre o cenário de então e o de agora.

Três recusas estruturam o pacote inteiro, e as três já foram aprendidas caro
neste repositório:

1. **Um evento não é uma amostra.** O caso mais lembrado é o mais enviesado: é
   lembrado justamente por ter sido extremo. Toda saída aqui carrega ``n``, e
   abaixo de :data:`amostra.N_MINIMO_EXPERIMENTAL` não sai faixa nenhuma.
2. **Movimento do ativo não é reação ao evento.** Sem separar o que o mercado
   inteiro fez no mesmo dia, "a ação caiu 6%" pode ser uma notícia ou pode ser
   uma quarta-feira de queda geral. Daí o retorno anormal ser o número que a
   estimativa usa, e o retorno bruto ficar ao lado apenas como evidência.
3. **Série esparsa não é série diária.** O armazém local tem preço diário de
   verdade para EUA e FIIs, e **não** tem para ações da B3 (1.542 datas
   distintas em 26 anos). Pedir "retorno em 1 pregão" a essa série devolveria um
   número que na prática mede semanas. :mod:`core.memoria_mercado.serie` mede a
   densidade da janela e devolve ``None`` em vez de um número que mente.

Nada aqui decide compra ou venda. A saída máxima do pacote é *reduzir
prioridade de aporte*, *observar*, *suspender aporte novo* ou *pedir
reavaliação fundamentalista* -- e :mod:`core.memoria_mercado.scores` mantém isso
como invariante testada, não como promessa em comentário.

Camadas
-------
``serie``        calendário de pregões, janelas e o portão de densidade
``benchmark``    índice de referência por ativo e modelos de retorno anormal
``retornos``     métricas de um evento único (a "reação observada")
``amostra``      estatística sobre vários eventos comparáveis
``similaridade`` Fator de Similaridade do Cenário (0-100)
``estimativa``   junta amostra + similaridade e devolve faixa, nunca ponto
``scores``       Score Estrutural x Score Conjuntural e as ações permitidas
``calibracao``   backtest da faixa e ajuste dos pesos por evidência
``repositorio``  persistência **no armazém local**, nunca no Supabase
``ponte_noticias`` conversão para ``core.noticias.impacto.BaseHistorica``
"""

MEMORIA_MERCADO_VERSAO = "1.0.0"
