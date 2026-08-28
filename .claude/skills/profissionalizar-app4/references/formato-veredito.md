# Formato obrigatório do veredito

O auditor deve entregar ao líder um relatório Markdown e um resumo JSON. Como o
auditor é deliberadamente read-only, o líder persiste a mensagem sem alteração
substantiva em `artifacts/app4_professionalizacao/`. O relatório deve conter:

1. commit/base e timestamp da avaliação;
2. escopo efetivamente verificado;
3. matriz `módulo × G1..G8` com status e link/caminho da evidência;
4. comandos executados, exit codes e resumo dos resultados;
5. achados abertos por severidade;
6. verificações puladas ou indisponíveis e o risco correspondente;
7. riscos residuais e limites de uso;
8. veredito e justificativa.

O JSON deve seguir esta forma mínima:

```json
{
  "schema_version": "app4-professional-verdict.v1",
  "base_revision": "<git sha ou working-tree documentado>",
  "modules": {
    "empresas_b3": {"G1": "APROVADO"},
    "selecao_fiis": {"G1": "APROVADO"},
    "empresas_americanas": {"G1": "APROVADO"},
    "portfolio_global": {"G1": "APROVADO"}
  },
  "open_findings": {"critical": 0, "high": 0, "medium": 0, "low": 0},
  "skipped_required_checks": [],
  "quality_suite": {"command": "", "exit_code": 0},
  "streamlit_startup": {"command": "", "exit_code": 0},
  "browser_validation": {"status": "APROVADO", "evidence": ""},
  "verdict": "APROVADO",
  "completion_token": "APP4_PROFISSIONAL_APROVADO"
}
```

Preencha G1 a G8 para cada módulo; o exemplo abreviado não autoriza omissões.
O token de conclusão deve estar ausente ou vazio em qualquer outro veredito.

Uma aprovação não significa garantia de retorno, ausência absoluta de defeitos,
recomendação de investimento ou certificação regulatória. Significa apenas que
o escopo e a revisão identificados satisfizeram esta rubrica com as evidências
registradas.
