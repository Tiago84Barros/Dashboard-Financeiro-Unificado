"""Motor Conjuntural: coleta, normalizacao e avaliacao de noticias.

O pacote e deliberadamente sem efeito colateral no import: nada de rede, nada de
banco, nada de leitura de ambiente no nivel de modulo. Quem precisa de HTTP
recebe um `Transporte` injetado; quem precisa de relogio recebe um `agora`.
Isso e o que permite a suite offline (`tests/conftest.py` bloqueia socket) rodar
o motor inteiro sem tocar a rede.
"""
