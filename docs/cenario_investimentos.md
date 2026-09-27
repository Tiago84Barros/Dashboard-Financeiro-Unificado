# Cenário de Investimentos

- **Estratégia:** o que o investidor pretende alcançar.
- **Cenário:** em que ambiente econômico ele acredita estar investindo.

O cenário é uma premissa adicional da análise dos ativos. Não substitui os fundamentos nem a estratégia.

## Onde fica

- **Tela:** Investimentos → Inteligência dos Ativos → "Meu cenário", no fim da aba liberada (`views/configuracoes_cenario.py`). Até 27/09/2026 ficava em Configurações → Geral. Cada conta tem o seu, admin ou não.
- **Armazenamento:** `user_settings.extra_settings["investment_scenario"]`, esquema `cenario.v1`. Não usa tabela nova nem migration, porque o Supabase está acima de 500 MB.
- **Regras:** `core/cenario/`:
  - `modelo.py` é puro;
  - `divergencia.py` gera os sinais de revisão;
  - `referencias.py` lê os insumos macro publicados;
  - `repositorio.py` faz a leitura e a gravação.

## Itens

São 12 itens:

- taxa de juros e expectativa de juros;
- inflação e expectativa de inflação;
- atividade econômica;
- câmbio;
- política fiscal;
- crédito;
- commodities;
- economia internacional;
- riscos geopolíticos;
- mercado de capitais.

Cada item tem `current_value`, `expected_direction` (alta / estável / queda / incerta), `confidence` (baixa / média / alta), `source` e `last_updated`.

Um item preenchido exige direção, confiança e fonte, porque premissa sem procedência vira "fato" de origem desconhecida. Um item vazio significa premissa ausente, não neutra.

## Regra: a LLM nunca altera o cenário

- **Só a tela grava.** Grava por `repositorio.salvar`, com a versão lida e uma origem da lista branca:
  - `manual`;
  - `atualizacao_solicitada`, quando o usuário clicou em "Sugerir valores a partir dos dados publicados" e salvou.

  Não existe origem `llm`. Nenhum caminho de LLM importa o repositório, e um teste garante que `leitura_llm.gerar` não chama `salvar`.
- **Conflito de versão.** Se duas abas salvarem, a segunda recebe `ConflitoDeVersao` em vez de sobrescrever a primeira.
- **Datas e histórico.** Salvar sem mudança não cria versão. Só os itens alterados ganham `last_updated` novo. O histórico guarda as últimas 20 revisões.
- **Sugestão não grava.** A sugestão só preenche o formulário, e direção e confiança continuam sendo decisão do usuário.
- **Prompt.** O prompt leva `REGRA_CENARIO`: usar como premissa, nunca alterar e, se os fatos contradisserem o cenário, escrever exatamente *"Existem mudanças relevantes que podem justificar revisão do cenário."*.
- **Validador.** O validador do Portfolio Fit descarta chaves como `updated_scenario`/`new_scenario` e marca `revisao_cenario` quando a frase aparece. A tela então mostra o aviso e aponta para "Meu cenário", no fim da aba.

## Sinais de revisão (calculados pelo código)

`divergencia.sinais` compara juros, inflação e câmbio com a última observação publicada em `data/public/macro_insumos.json.gz`. As tolerâncias são:

- juros: 0,25 pp;
- inflação: 0,5 pp;
- câmbio: 5% relativo.

O sinal só nasce quando a referência é **mais nova** que a última revisão do item. Referência mais antiga não contradiz o que o usuário escreveu depois dela.

Hoje as séries do Brasil publicadas são anuais, fechadas em 31/12. Por isso o sinal é raro até sair a série de 2026, e isso é esperado.

Itens revistos há mais de 90 dias aparecem como "revisão antiga". Eles não geram alteração, só aviso.

## Uso na análise

- `ContextoInvestidor.cenario` e `sinais_cenario` são carregados em `analisar_ativo`/`analisar_carteira`. Uma falha de leitura vira "sem cenário" e não derruba a análise.
- A seção "Cenário" de cada ativo (`secoes.provedor_cenario`) resume a versão, os itens mais relevantes para a classe da política (`RELEVANCIA`), os itens antigos e a frase de revisão, se houver sinal.
- `analise.texto_para_llm` inclui o bloco "CENÁRIO DE INVESTIMENTOS". Sem cadastro, o bloco diz para não presumir um cenário.
- No Portfolio Fit, o cenário vai em `scenario.cenario_do_investidor`, e a LLM o usa em `scenario_impact`.

Testes: `tests/test_cenario_investimentos.py`.
