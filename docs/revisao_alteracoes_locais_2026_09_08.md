# Revisão das alterações locais — 2026-09-08

## Decisão

Revisão inicial: não aprovado para commit em bloco. Achados M1 e M2 corrigidos
na continuação autorizada; resultados finais abaixo. O histórico da avaliação
inicial é preservado para rastreabilidade.
Base local: 6fa07e3. Integração experimental: origin/main em 7cee411, em
worktree separado, com aplicação dos patches locais e inclusão dos nove novos
arquivos de implementação, documentação e testes macro. Logs de local_staging
e cmp_regra_div.json não foram tratados como código de produto.

## Benefícios demonstrados

- Snapshots macro serializáveis preservam fontes e data da criação nos contextos LLM.
- Deduplicação temporal seleciona uma versão por período antes de limitar a janela.
- Histórico reutiliza os motores de carteira e invalida resultados após mudanças de configuração.
- Limites de turnover e de desvio relativo são aplicados após projeção dos pesos.
- Implementações locais de contexto macro global completam dependências já chamadas pela versão remota.
- Corpus RAG ampliado: 162282 hashes únicos, 292 raízes, 74454 âncoras e 2600 stubs.
  Contagens e tamanhos físicos conferem com o manifesto. Isso demonstra integridade
  estrutural, não ganho de qualidade de respostas da LLM.

## Achados

### M1 — Ajuste exibido no cenário FII diverge da regra compartilhada

core/fii_portfolio_v4.py:731 calcula macro_score_adjustment com fator 1 tanto
em moderate quanto em scenario. core/macro_data/portfolio_tilt.py:108 usa
fator 1,5 para scenario. Reprodução sintética com impacto 50: FII retorna
2,0 nos dois modos; o motor compartilhado retorna 3,0 no cenário. O otimizador
FII distingue os modos por coeficientes 0,08/0,12, portanto a exibição não
representa essa diferença. Harmonizar a convenção ou documentar explicitamente
uma métrica distinta antes de aprovar a alteração de exibição/persistência.

### M2 — Universo da reconstrução histórica FII não coincide com o dos impactos

views/fiis.py:1966 define rebuild_fii(base, impacts, mode), mas ignora base e
reotimiza scored inteiro. core/macro_data/portfolio_context.py:347 monta os
ativos para a consulta macro somente a partir de holdings (carteira exibida).
Assim, candidatos fora da carteira participam da reotimização sem que seus
impactos históricos tenham sido solicitados. É uma inconsistência de contrato
confirmada por inspeção; a magnitude do efeito nos pesos não foi quantificada.
Definir um único universo para consulta e reconstrução, preservando a promessa
da interface de analisar a composição atual.

## Compatibilidade e verificações

Aplicação em três vias sobre a main atual: sem conflitos. A correção posterior
de P/VP dos FIIs foi preservada; os hunks locais dessa tela alteram apenas a
camada macro. Testes do chat global, P/VP, macro, persistência FII e RAG:

196 passed, 3 skipped em 36,73 segundos.

python -m ruff check .: aprovado.
python scripts/run_quality_checks.py: aprovado (skills, exemplos de fórmulas,
varredura de segredos e testes de ambiente; não foi usado --full nesta rodada).

Os três skips são testes condicionais de persistência FII. A suíte integral,
validação visual real e teste PostgreSQL macro opt-in não foram repetidos nesta
revisão. Resultados históricos não foram promovidos a nova aprovação desses gates.

## Próximo passo

Resolver M1 e M2, testar seus contraexemplos e revalidar o conjunto integrado
antes do commit. Manter arquivos de staging fora do commit de produto.

## Fechamento autorizado

- M1 corrigido: FII reutiliza apply_macro_scores, com ajuste de 0/2/3 pontos
  para impacto 50 nos modos fundamental/moderado/cenário.
- M2 corrigido: rebuild_fii_macro_history recebe somente a composição exibida.
  O motor histórico recusa inclusão, exclusão ou duplicação de símbolos na saída.
- Regressões novas: falharam antes da correção (5 falhas, 2 aprovações).
  Incluída também reconstrução com otimizador real, nove ativos sintéticos,
  soma dos pesos 1 e limites por ativo/desvio relativo respeitados.
- Integração final sobre 5b0ebdc: 228 testes passaram, 3 testes condicionais
  de persistência FII ignorados. Inclui chat Global, P/VP, criação US e A-135.
- Ruff global e run_quality_checks.py sem --full aprovados. O lint encontrou
  um import fora de ordem em portfolio_global.py vindo da main; ordenação corrigida.
- Streamlit em APP_TEST_MODE=true iniciou em 127.0.0.1:8514, health HTTP 200,
  encerrado em seguida. AppTest valida o estado da interface macro; navegador
  real/mobile e suíte integral não foram repetidos nesta continuação.
- RAG: 68784 hashes adicionados, nenhum removido, schema preservado,
  162282 hashes únicos e tamanhos reconciliados. Os 28 textos vazios já existiam;
  nenhum vazio novo. Stubs não entram na busca de âncoras; diversidade e paridade
  foram exercitadas na revisão anterior. Não se alega ganho de respostas da LLM.
- Logs de local_staging e cmp_regra_div.json permanecem fora do commit.
- Falta de espaço interrompeu uma cópia temporária; três worktrees temporários
  desta tarefa foram removidos, todos reproduzíveis a partir do Git e dos arquivos
  originais preservados. A revisão final usou checkout esparso para economizar disco.
