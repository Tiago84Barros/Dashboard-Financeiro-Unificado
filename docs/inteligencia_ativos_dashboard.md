# Inteligência dos Ativos — painel

Investimentos → Inteligência dos Ativos, com a estratégia concluída. A aba
virou um painel. De cima para baixo, ela mostra:

1. **Resumo da carteira**, um cartão só.
2. **Ativos**, com um cartão por posição. O botão *Abrir análise completa*
   seleciona o ativo.
3. **Estratégia, cálculos e tabela da carteira**, recolhido num expander. Traz
   a premissa, os cálculos objetivos e a tabela de adequação.
4. **Análise completa** do ativo escolhido: as questões e o fluxo das 13 seções.
5. **Portfolio Fit**, primeiro pelas regras e depois pela LLM, sob demanda.
6. **Histórico e auditoria** do ativo escolhido.

## Resumo da carteira

| Campo | Origem |
|---|---|
| Patrimônio total | `ctx.total_mercado` (carteira consolidada) |
| Rentabilidade | `rentabilidade_total_pct`, só quando `rentabilidade_total_disponivel` |
| Renda gerada | proventos `total_12m` / `total_historico`; some se `data_source == "error"` |
| Alocação atual, alvo e desvio | `calculos.alocacao` (por classe da política) |
| Concentrações | maior ativo, setor e emissor (`calculos`) |
| Objetivo e estratégia | política de investimentos (versão exibida) |
| Cenário | Cenário de Investimentos; avisa quando os sinais pedem revisão |
| Alertas relevantes | alertas de severidade ALTA e MÉDIA (até 6) |
| Próximos eventos | eventos futuros das análises (até 6) |
| Ativos que merecem revisão | ação em reavaliar tese, reduzir concentração, comparar alternativas ou reavaliar aportes, por prioridade |

Nada é inventado. Um número sem base aparece como indisponível, nunca como
zero.

## Cartão do ativo

Cada cartão mostra ticker, valor, peso na carteira, target/faixa, papel,
status da tese (válida, com sinal contra ou sem veredito), valuation, principal
risco, próximo evento e ação a considerar.

- **Valuation** mostra uma métrica com comparação ao próprio histórico
  ("acima da média histórica"). Nunca diz "barato" ou "caro".
- **Principal risco** segue esta ordem: primeiro o gatilho da tese disparado,
  depois o alerta do próprio ativo, e por fim o do setor ou do emissor, do
  mais grave para o menos grave.
- **Ordem dos cartões:** os ativos que pedem revisão vêm primeiro; os demais
  seguem do maior peso para o menor.

## Histórico

`core/inteligencia_ativos/historico.py` (puro) e `historico_repo.py` (I/O).

Uma foto guarda peso, valor, status da tese, ação, papel, sinais de risco e até
seis métricas de fundamentos e de valuation. A foto da carteira guarda
patrimônio, rentabilidade, alocação por classe e número de alertas.

**Quando salvar.** O histórico não grava a cada visita. Numa foto automática,
só grava:

- quando é a primeira foto;
- quando mudou a versão da estratégia ou do cenário;
- quando mudou o status da tese, a ação ou a contagem de sinais de risco;
- quando o peso variou 1 pp ou mais;
- quando a alocação por classe variou 1 pp ou mais (na foto da carteira);
- quando a última foto tem mais de 30 dias.

Há duas outras formas de gravar:

- **leitura por LLM gerada:** grava sempre, com o modelo que respondeu;
- **botão *Salvar esta análise no histórico*:** grava sempre, como manual.

A aba tenta a gravação automática uma vez por sessão. A decisão acontece
dentro da transação, com a linha travada (`FOR UPDATE`), então duas abas
abertas não duplicam a foto.

**Comparação.** A comparação é feita com a foto anterior, e não com a gravada
agora. Ela gera frases como:

- "Na análise anterior (dd/mm/aaaa), o peso era 8,0%; agora é 12,0%."
- "Desde a última análise, a tese permaneceu válida."
- "O risco aumentou: os sinais objetivos passaram de 1 para 2."
- "Dívida líquida/EBITDA aumentou de 1,50x para 2,40x (fundamentos)."

Variações de métrica abaixo de 2% são tratadas como ruído e não viram frase.

## Auditoria

Cada foto registra os seguintes campos:

| Campo | Conteúdo |
|---|---|
| `analysis_timestamp` | momento da análise (UTC, ISO) |
| `model_used` | `provedor/modelo` que de fato respondeu (com fallback, é o do fallback). Fica vazio quando não houve LLM |
| `data_timestamp` | a data mais recente dos dados usados, nunca no futuro |
| `scenario_version` | versão do Cenário de Investimentos, ou vazio |
| `investment_policy_version` | versão da estratégia |
| `sources_used` | fontes das seções disponíveis e dos fundamentos |

`model_used` vem de `core.llm_b3.ultimo_modelo()`, que é por thread e é zerado
a cada chamada.

## Armazenamento

O histórico fica em `user_settings.extra_settings`, na chave
`asset_analysis_history` (esquema `historico_analises.v1`). O limite é de 8
fotos por ativo e 8 da carteira.

O motivo de não usar uma tabela é que o Supabase está acima do teto de 500 MB.
Algumas fotos compactas por usuário não justificam uma tabela nova nem uma
migration.

Se o histórico precisar crescer (mais fotos, consulta entre usuários, retenção
longa), a alternativa é uma tabela `asset_analysis_snapshots`, com as colunas
de auditoria promovidas a físicas. Essa mudança depende de uma migration
aprovada. O formato JSON já usa os nomes em inglês dessas colunas.

## Testes

- `tests/test_inteligencia_ativos_painel.py` cobre o resumo, os cartões, a
  ordem dos riscos, o HTML só com tokens e o AppTest completo da aba: abrir a
  análise pelo cartão, a gravação automática, o botão de salvar e a falha do
  banco.
- `tests/test_inteligencia_ativos_historico.py` cobre a regra de quando salvar,
  o limite, a comparação, a auditoria, o modelo que respondeu e o repositório
  com engine falso.
- `tests/conftest.py` (`_historico_em_memoria`) troca o repositório por um
  dicionário em memória, para que nenhum teste chegue ao banco.
