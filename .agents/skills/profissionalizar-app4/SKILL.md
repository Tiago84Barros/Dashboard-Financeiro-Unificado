---
name: profissionalizar-app4
description: Conduz a profissionalização ampla e verificável de Empresas B3, Seleção de FIIs, Empresas Americanas e Portfólio Global por rodadas econômicas de implementador e auditor independente. Use quando o usuário pedir para retomar o checkpoint, executar o goal do App 4 ou obter o veredito APP4_PROFISSIONAL_APROVADO.
---

# Profissionalizar o App 4

Atuar como líder do ciclo. Buscar evidência profissional; nunca declarar
qualidade por intenção, aparência ou quantidade de testes.

## Preparar

1. Ler `AGENTS.md`, `graphify-out/GRAPH_REPORT.md` e as skills obrigatórias.
2. Ler integralmente:
   - `../ProjetoIA/04_App_Dashboard_Financeiro_Unificado/profissionalizacao_app4_checkpoint_2026-08-17.md`;
   - `.claude/skills/profissionalizar-app4/references/rubrica-profissional.md`;
   - `.claude/skills/profissionalizar-app4/references/formato-veredito.md`.
3. Inspecionar Git e preservar todas as alterações do usuário.

## Controlar custo

- Manter `gpt-5.6-terra` com esforço médio no líder e implementador.
- Usar `app4_auditor` em Terra/high somente para a revisão independente.
- Executar um único subagente por vez; nunca manter implementador e auditor
  ativos simultaneamente.
- Passar ao subagente somente achado, arquivos, critério de aceite e checkpoint
  curto. Não encaminhar a conversa completa nem logs extensos.
- Não criar agentes adicionais nem usar Sol, Max ou Ultra sem autorização
  humana explícita.
- Fazer testes focados por rodada e reservar a suíte ampla para checkpoints de
  módulo e para o gate final.

## Executar o ciclo

1. Retomar o primeiro achado aberto no checkpoint.
2. Iniciar `app4_implementador` com uma única fatia e exigir teste de reprodução,
   correção da causa-raiz e evidência focada.
3. Encerrar o implementador antes de iniciar `app4_auditor` em contexto limpo.
4. Exigir do auditor reprodução independente e `ACEITO`, `REABERTO` ou
   `NOVO ACHADO`.
5. Se reaberto, iniciar nova rodada curta do implementador com o feedback exato.
6. Registrar checkpoint e avançar somente após aceite independente.

Não alterar migrations, bancos persistentes, dados remotos, deploy ou Git remoto
sem autorização humana. Banco/rede/navegador indisponível permanece pendência;
nunca converter ausência de verificação em aprovação.

## Concluir

Rodar as verificações exigidas pela rubrica, incluindo suíte relevante,
qualidade, inicialização do Streamlit e validação funcional/visual sintética.
Concluir somente quando o auditor emitir literalmente
`APP4_PROFISSIONAL_APROVADO`, sem achados críticos/altos e sem gate obrigatório
pulado. Se o mesmo bloqueio externo persistir por três rodadas, registrar o
checkpoint e solicitar decisão humana em vez de consumir créditos em loop.
