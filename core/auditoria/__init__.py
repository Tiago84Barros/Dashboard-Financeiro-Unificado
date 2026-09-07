"""Trilha de auditoria das recomendações.

O requisito faz uma pergunta e o pacote inteiro existe para respondê-la:

    "Por que o APP4 recomendou essa mudança naquele momento?"

Repare nas três partes da pergunta, porque cada uma exige um dado diferente e
faltar qualquer uma torna a resposta inútil:

- **por que** -- as evidências e o motor que as combinou;
- **essa mudança** -- a ação proposta, com número, e não uma frase vaga;
- **naquele momento** -- o carimbo, mais as versões de dado e de modelo que
  valiam naquele instante.

O projeto já tem quatro tabelas de auditoria (``market.fii_audit_events``,
``market_us.data_quality_audit``, ``market.b3_validation_runs``,
``market.b3_data_readiness_snapshots``) e nenhuma delas responde a essa
pergunta: todas auditam **qualidade de dado**, não **recomendação**. Auditar a
entrada e não a saída deixa o elo que o usuário questiona sem registro.

Dois módulos:

``trilha``
    grava e lê o registro. Nada de "gravou se der" -- falha de gravação é uma
    das seis travas (``core.seguranca.travas.AUDITORIA_FALHOU``) e bloqueia
    mudanças estratégicas. Sem registro não há como responder depois.

``confirmacao``
    a confirmação explícita das mudanças grandes, com os nove pontos que o
    requisito exige na tela antes de qualquer clique.
"""
