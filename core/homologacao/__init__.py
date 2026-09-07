"""Liberação gradual: fases, feature flags e critérios de avanço.

    "Não libere imediatamente todas as funcionalidades para decisões reais."

Quatro fases, e a diferença entre elas não é quanto o sistema calcula -- é
quanto ele **afirma**:

===== ============================ ==========================================
fase   nome                         o que o usuário vê
===== ============================ ==========================================
1      Observação silenciosa        nada. O sistema coleta e mede, e ninguém
                                    decide com base nisso.
2      Painel informativo           o que foi medido, sem recomendação.
3      Recomendações conjunturais   sugestões, com confirmação explícita.
4      Modo Crise                   o comportamento excepcional, completo.
===== ============================ ==========================================

A fase não é um enfeite de tela: ela é o teto do que as flags podem ligar. Uma
flag de recomendação emergencial ligada na Fase 2 não vale, e :func:`ativo`
devolve ``False`` sem discussão. Sem esse teto, "estamos na Fase 2" viraria uma
frase no README enquanto o código faria outra coisa -- o defeito de
``memoria: declaracao-de-rigor-nao-verificada``.

Dois módulos:

``flags``
    as nove chaves independentes, o teto por fase, e de onde vem o valor.

``criterios``
    o que precisa estar medido para a fase seguinte ser liberada. Critério
    objetivo, com número; "parece estar funcionando" não avança fase.
"""
