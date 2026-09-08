# A-135 — Correlação B3: janela comum e cobertura completa

Data: 2026-09-08
Base: `82f59d3` mais working tree documentada.

## Resultado

**ACEITO** por auditoria independente após uma reabertura.

A troca de diversificação B3 agora só é considerada quando a carteira-base
inteira e a candidata compartilham ao menos 18 retornos mensais observados na
mesma interseção temporal. Ticker ausente ou histórico insuficiente torna a
decisão indisponível; nenhuma ausência é preenchida e nenhuma troca é feita.

## Evidência

* Auditoria independente: reprodução do contraexemplo com uma posição sem
  série confirmou `log=[]`; a primeira versão foi reaberta por excluir essa
  posição silenciosamente, e a segunda recebeu `ACEITO`.
* Testes focados: `42 passed`.
* Suíte ampla: `4597 passed, 4 skipped, 20 warnings`; os testes passaram.
* `python -m ruff check .`: aprovado após ordenar um import.
* `git diff --check`: aprovado (avisos LF/CRLF sem erro).

## Limite residual

O controle é intencionalmente fail-closed: se não houver 18 retornos comuns
para a cesta completa, a diversificação por correlação não altera a carteira.
Isso é uma limitação explícita de cobertura, não uma estimativa de risco.
