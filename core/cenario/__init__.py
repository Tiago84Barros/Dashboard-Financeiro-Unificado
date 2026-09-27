"""
core/cenario
Cenário de Investimentos: o ambiente econômico em que o usuário acredita
estar investindo. É premissa adicional das análises, ao lado da estratégia.

- ``modelo.py``       — os 12 itens, validação, versão, texto para a LLM (puro);
- ``divergencia.py``  — sinais de que o cenário pode estar desatualizado (puro);
- ``referencias.py``  — valores de referência dos insumos macro publicados;
- ``repositorio.py``  — leitura e gravação em ``user_settings.extra_settings``.

Estratégia responde "o que o investidor pretende alcançar?". Cenário responde
"em qual ambiente econômico estamos investindo?". Um não substitui o outro, e
nenhum dos dois substitui os fundamentos do ativo.
"""
