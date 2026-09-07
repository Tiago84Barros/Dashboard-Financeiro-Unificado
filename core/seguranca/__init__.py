"""Segurança de fronteira: o que entra no prompt, o que sai no log.

Três módulos, três fronteiras distintas:

``segredos``     -- nada de credencial ou dado pessoal sai daqui (log, prompt,
                    mensagem de erro).
``injecao``      -- conteúdo externo é dado, nunca instrução.
``procedencia``  -- as quatro camadas do prompt (instrução do sistema, dado
                    calculado, conteúdo recuperado, resposta) ficam separadas
                    por construção, e a separação é verificável.

Nenhum deles fala com rede, banco ou LLM: são funções puras sobre texto, o que
os torna testáveis sem infraestrutura -- e é o que permite que o teste de
injeção rode na suíte comum.
"""
