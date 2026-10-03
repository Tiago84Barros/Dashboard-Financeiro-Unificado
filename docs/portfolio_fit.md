# Portfolio Fit

A camada de Portfolio Fit da **Inteligência dos Ativos** responde se o ativo faz sentido para **este** investidor, dentro **desta** carteira. Ela não pergunta se o ativo é bom em abstrato.

- `core/inteligencia_ativos/portfolio_fit.py` é o módulo puro. Ele cuida da ordem de análise, da pré-leitura de fit por regras, do contexto estruturado, do prompt e da validação da resposta.
- `core/inteligencia_ativos/leitura_llm.py` é a camada de I/O: monta o bloco de contexto de mercado do ativo e chama o provedor (`_chat_complete`, JSON, temperatura 0,1).
- `views/inteligencia_ativos_fit.py` é a tela, no fim da análise de cada ativo.

## Ordem obrigatória

1. Objetivo do investidor
2. Horizonte
3. Liquidez
4. Perfil e capacidade de risco
5. Política
6. Alocação atual
7. Alocação-alvo
8. Concentração
9. Papel do ativo
10. Cenário
11. Fundamentos
12. Valuation
13. Pares
14. Notícias
15. Eventos
16. Alternativas

O investidor vem antes do ativo. A ordem vai no prompt e no próprio contexto (`ordem_de_analise`).

## Três dimensões independentes

| Dimensão | Níveis |
|---|---|
| `fundamental_quality` | forte · adequada · fraca · insuficiente |
| `valuation_attractiveness` | atrativo · neutro · esticado · insuficiente |
| `portfolio_fit` | alto · médio · baixo · insuficiente |

As três dimensões nunca são somadas, ordenadas ou transformadas em nota. O validador remove qualquer campo de nota geral ou ranking que a LLM devolva (`score`, `overall`, `ranking`, `nota_geral`, `recommendation`…). Fundamentos fortes com fit baixo por concentração é uma resposta válida, e a tela a mostra assim.

## Fit por regras (antes da LLM)

`fit_por_regras` lê só a política, a carteira e o papel do ativo. O resultado é mostrado sempre, mesmo sem LLM, e vai no contexto como dado.

- **Bloqueio** (fit baixo): tese fora da função original, ativo acima do limite por ativo, setor acima do limite por setor ou classe acima do limite máximo.
- **Contra**: classe acima do alvo além da tolerância de 5 pp, folga menor que 1 pp até o limite por ativo, ativo acima da faixa do usuário, ou nenhum papel identificado.
- **A favor**: classe abaixo do alvo ou dentro dele, folga até o limite, ativo abaixo do piso do usuário, ou papel principal no foco da estratégia.

O nível sai assim:

| Situação | Nível |
|---|---|
| Algum bloqueio | baixo |
| Só pontos contra | baixo |
| Pontos contra e a favor | médio |
| Só pontos a favor | alto |
| Nada | médio |

Com bloqueio, o fit não pode ser "alto" nem na leitura da LLM: o validador corrige e registra a correção. Uma simples divergência entre a LLM e as regras aparece na tela, sem correção.

## Contexto enviado

O banco inteiro não é enviado. O contexto tem as chaves:

- `portfolio`, `policy`, `allocation`, `concentration`, `scenario`;
- `asset`, `fundamentals`, `valuation`, `peers`, `peer_comparison`;
- `news`, `reports`, `events`, `alternatives`;
- `rules` (ação e fit pelas regras) e `data_gaps` (as lacunas que o código conhece).

O bloco de contexto de mercado (`bloco_contexto_mercado`) vai anexado como texto, com `REGRA_CONTEXTO_MERCADO` no prompt de sistema. O JSON não aparece mais na tela (o expansor "Contexto estruturado que a LLM recebe" saiu em 2026-10, a pedido); ele continua sendo montado e enviado igual.

## Resposta e validação

A resposta segue o schema pedido:

- `asset_role`, `thesis_status`, `fundamental_analysis`, `valuation_analysis`, `peer_analysis`;
- `scenario_impact`, `portfolio_impact`, `risks`, `opportunities`, `events_to_watch`;
- `action_to_consider`, `reasoning_summary`, `data_gaps`.

Soma a isso `dimensions`, com nível, fato e interpretação por dimensão, e `conclusions`, com fato, interpretação, impacto na carteira e ação a considerar.

`validar`:

- Um campo ausente vira "Dado não disponível.". Uma lista é limitada a 10 itens e um texto a 2.000 caracteres.
- Papel, status da tese e ação são conferidos contra as listas permitidas. Uma ação inválida volta à ação das regras.
- Dimensão sem dado no contexto (sem indicadores ou sem métricas) é forçada a "insuficiente", com a interpretação "Não existem informações suficientes para concluir.".
- Todo número do texto é conferido contra o contexto e o bloco de mercado (`check_grounding`). O que não tem âncora aparece em vermelho como possível invenção.
- Status: APROVADA, COM RESSALVAS (houve correção, problema ou número sem âncora) ou REJEITADA (sem resposta, ou resposta que não é JSON).

## Limitações

- O cenário é o Cenário de Investimentos do usuário (`scenario.cenario_do_investidor`), somado ao bloco de mercado. Ele é só leitura: a LLM não o altera e o validador descarta qualquer chave que tente reescrevê-lo. Ver `docs/cenario_investimentos.md`.
- A política não tem alvo por ativo, só por classe, e o contexto diz isso explicitamente.
- A leitura fica na sessão, com uma chave que depende do ativo, da versão da política e do hash do contexto. Mudou a carteira, é preciso pedir de novo.
