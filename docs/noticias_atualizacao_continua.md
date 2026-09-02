# Atualização contínua das notícias

Como o APP4 mantém as notícias frescas **sem ninguém com uma página do Streamlit
aberta**, o que isso custa em cota de plano gratuito, e o que precisa ser
configurado para valer em produção.

---

## 1. Onde o APP4 roda, e o que isso permite

Levantado antes de projetar qualquer coisa, porque a resposta elimina metade das
arquiteturas possíveis:

| Pergunta | Resposta | Consequência |
|---|---|---|
| Onde está publicado? | Streamlit Cloud, a partir da `main` | deploy é `git push` |
| Há processo persistente? | **Não** | o container só executa enquanto responde a uma requisição |
| Há worker? | **Não**, e não há onde hospedar um sem custo | nada de fila, nada de daemon |
| Há banco? | Sim, Supabase (plano free, teto de 500 MB) | é o único estado que os processos compartilham |
| Há agendador? | **Sim, e já existia**: cron do GitHub Actions | `data_pipeline.yml`, `market-refresh.yml`, `update-data.yml` |

Duas consequências mandam no desenho:

1. **Laço dentro da interface está descartado.** Um `while` numa página do
   Streamlit morre quando a aba fecha e não roda para mais ninguém. Também é o
   que o requisito proíbe explicitamente.
2. **`st.session_state` não é estado do sistema.** É memória de uma aba de um
   usuário. O estado da coleta precisa estar onde o runner do Actions, o
   container do Streamlit e a máquina do desenvolvedor cheguem: o banco.

O agendador não precisou ser inventado — precisou ser **usado**. O que faltava
era o estado compartilhado.

---

## 2. O defeito que isto corrigiu

`frescor_noticias.RegistroColeta` gravava o carimbo da última coleta em
`local_staging/noticias/coleta.json`. O docstring acertava o requisito ("três
processos precisam enxergar o mesmo carimbo") e errava o meio: os três processos
**não compartilham disco**.

Na prática:

* o runner do Actions nasce com disco limpo → o freio de cadência nunca freava,
  toda execução se via como a primeira;
* a sessão do Streamlit nunca via a coleta do job → a tela dizia "nenhuma coleta
  nesta sessão" com o acervo cheio;
* o mesmo valia para o **orçamento de requisições** (`rate_limit.json`): com o
  cron de meia em meia hora, seriam 48 orçamentos diários completos contra um
  teto de 25 chamadas/dia. O teto existia no código e não existia na prática.

Os dois contadores passaram para o banco (`noticias_coleta_estado`,
`noticias_coleta_ciclos`, `noticias_consumo_provedor`). O arquivo continua
existindo como degrau de desenvolvimento: sem `DATABASE_URL` tudo funciona em
disco e em memória, como sempre funcionou nos testes.

---

## 3. Arquitetura

```
GitHub Actions (cron :17 e :47)          Streamlit Cloud (container efêmero)
        │                                          │
        ▼                                          ▼
data_pipeline.cli_noticias          views/inteligencia_mercado.py
        │                                   │            │
        └──────► update_noticias.run() ◄────┘      ler_recentes()
                        │                                │
                        ▼                                ▼
        ┌───────────────────────────────────────────────────────┐
        │  Supabase — estado compartilhado                       │
        │  noticias_coleta_estado    carimbos, modo, status      │
        │  noticias_coleta_ciclos    auditoria de cada execução  │
        │  noticias_consumo_provedor cota consumida por provedor │
        │  noticias_itens/avaliacoes o acervo                    │
        └───────────────────────────────────────────────────────┘
```

O botão manual da tela chama **o mesmo** `run()` que o cron chama. Não existe um
segundo caminho de coleta — dois caminhos divergiriam, e o da tela seria o menos
testado.

---

## 4. Os três modos

O modo **não é escolhido pela coleta**. Sai do nível do Motor de Eventos
Extremos por tabela fixa (`cadencia.MODO_POR_NIVEL`), para não existir um
segundo juiz de crise com regras que ninguém auditou. O encerramento automático
da vigilância vem de graça: a descida de nível já é governada por
`eventos_extremos.transicao` (mínimo de 12 h no nível, um degrau por avaliação),
e o modo herda essa histerese.

| Modo | Nível | Frequência (padrão) | Universo | SLA de frescor |
|---|---|---|---|---|
| Normal | 0 | 240 min | carteira → candidatos → mercado amplo | 480 min |
| Vigilância | 1–2 | 60 min | carteira → candidatos | 120 min |
| Crise | 3–4 | 30 min | só a carteira | 60 min |

**Frequência maior, universo menor.** Cota de provedor gratuito é finita. Subir
a frequência sem encolher o universo não aumenta frescor: esgota a cota antes do
meio-dia e o resto do dia fica sem coleta nenhuma.

O SLA é `frequência × 2`. `NOTICIAS_MAX_SEM_ATUALIZACAO_MIN`, quando definido,
só pode **apertar** esse limite, nunca afrouxá-lo — um teto global de 24 h não
deveria transformar a crise, que vence em 30 min, em algo que só fica atrasado
no dia seguinte.

---

## 5. Periodicidade real (não a prometida)

| Item | Valor |
|---|---|
| Cron declarado | `17,47 * * * *` — 48 disparos/dia |
| Pontualidade | **O cron do GitHub Actions não é pontual** e pode atrasar minutos em fila; em janelas de pico, mais. Não há SLA público. |
| Cron desligado por inatividade | o GitHub desabilita cron de repositório sem commits por ~60 dias |
| Execuções que viram coleta | só as que o modo corrente pede: no modo Normal, ~6 das 48 (freio de cadência no banco) |
| Custo das demais | um runner ocioso por ~40 s, **zero** cota de provedor |

O freio tem tolerância de 15% do intervalo (`cadencia.TOLERANCIA_FRACAO`). Sem
ela, um agendador de horário fixo medido contra um portão de tempo decorrido
pula um ciclo inteiro sempre que chega alguns segundos adiantado — e pular fica
"certo" pela regra.

A verificação de saúde do agendador **deduz do carimbo**, não da existência do
arquivo: workflow desabilitado, cota de Actions estourada ou cron desligado pelo
GitHub continuam existindo no `.yml`. Só o carimbo prova execução. O alarme só
dispara depois de 3 ciclos previstos perdidos, para não confundir fila com
parada.

---

## 6. Limites dos planos gratuitos

| Provedor | Por minuto | Por dia | Observação |
|---|---|---|---|
| Alpha Vantage | 5 | **25** | é o teto que aperta |
| Marketaux | — | 100 | |
| Finnhub | 60 | — | |
| RSS | 30 | — | sem chave |

Tetos conferidos na documentação pública de cada API. Ficam em
`rate_limit.LIMITES_PADRAO` e não no config: são propriedade do provedor, não
preferência do usuário. Quem tem plano pago sobrescreve passando `limites=`.

O freio é **anterior à chamada**. Descobrir o limite pelo 429 é caro: a
requisição que leva o 429 já consumiu cota, e a janela de reposição é de 24 h.

Com 25 chamadas/dia e uma chamada por ciclo, um dia inteiro em modo Crise
(48 ciclos) não cabe na cota do Alpha Vantage. É por isso que o modo Crise
encolhe o universo para a carteira e que existe fallback para os outros
provedores: **o orçamento é a restrição de projeto, não um detalhe.**

---

## 7. Configuração

### Variáveis de ambiente (todas com padrão; nenhuma obrigatória)

| Variável | Padrão | O que faz |
|---|---|---|
| `NOTICIAS_FREQ_NORMAL_MIN` | 240 | intervalo do modo Normal |
| `NOTICIAS_FREQ_VIGILANCIA_MIN` | 60 | intervalo do modo Vigilância |
| `NOTICIAS_FREQ_CRISE_MIN` | *(herda `NOTICIAS_FREQ_EMERGENCIA_MIN`)* | intervalo do modo Crise |
| `NOTICIAS_MAX_SEM_ATUALIZACAO_MIN` | 0 (sem teto) | teto global de tempo sem atualização; só aperta |
| `NOTICIAS_MAX_RETENTATIVAS` | 2 | retentativas quando **nenhum** provedor respondeu |
| `NOTICIAS_BACKOFF_S` | 5 | espera base; dobra a cada tentativa |
| `NOTICIAS_TIMEOUT_S` | 12 | timeout por requisição |
| `NOTICIAS_PROVEDORES` | *(vazio)* | quais provedores habilitar |
| `NOTICIAS_LIMITES_DIARIOS` | *(vazio)* | sobrescreve os tetos por provedor |
| `NOTICIAS_RETENCAO_DIAS` | 180 | expurgo de notícias e ciclos |
| `NOTICIAS_TIMEZONE` | `America/Sao_Paulo` | **só apresentação** |

Sobre a última linha: carimbo é gravado e comparado **em UTC, sem exceção**.
Converter na gravação já produziu neste projeto série que muda de dia conforme o
horário de verão de quem gravou. Trocar `NOTICIAS_TIMEZONE` não move gatilho
nenhum, e há teste que fixa isso.

### Para ligar em produção

O workflow **nasce desligado**. O `if:` do job checa uma variável de repositório
que não existe até alguém criá-la — o cron dispara, o job é pulado, nenhuma cota
é gasta. É o mesmo espírito do `is_active: False` no registro do pipeline.

1. `Settings > Secrets and variables > Actions > Secrets`:
   `SUPABASE_UNIFICADO_URL`, e a chave de ao menos um provedor
   (`NOTICIAS_ALPHAVANTAGE_KEY` e/ou `NOTICIAS_MARKETAUX_KEY`).
2. `... > Variables`: `NOTICIAS_PROVEDORES` (ex.: `rss,marketaux`) e as demais
   da tabela acima que quiser mudar.
3. `... > Variables`: **`NOTICIAS_COLETA_ATIVA = true`**. É esta que liga.
4. Confira em Actions → *Coleta de noticias* → *Run workflow* (o disparo manual
   aceita `force` e `nivel`).

O passo de saúde roda com `if: always()` — é justamente quando a coleta falha
que a saúde interessa.

`update_registry` continua com `is_active: False` e **assim deve ficar**: o
pipeline noturno roda uma vez por dia, frequência que não serve a nenhum dos
três modos. Ativar nos dois lugares faria a coleta noturna disputar cota com o
ciclo do modo corrente sem trazer frescor.

---

## 8. Estado gravado a cada ciclo

`noticias_coleta_ciclos` guarda, por execução: modo, origem (`job`/`manual`),
se foi forçada, início, conclusão, duração, status, próximo ciclo previsto,
provedores que responderam, provedores que falharam, notícias coletadas, novas,
duplicadas, eventos, erros e limitações.

Duas decisões que valem registro:

* **`novas` é contado antes da gravação.** Depois do upsert todo `id_dedup` já
  existe no acervo e a resposta seria zero em qualquer cenário — um número
  estável, plausível e sempre errado.
* **`ultimo_sucesso` só avança quando alguém respondeu.** No SQL é
  `COALESCE(EXCLUDED.ultimo_sucesso, tabela.ultimo_sucesso)`: é a linha que
  impede o painel de dizer "atualizado agora" depois de uma coleta que não
  trouxe nada.

Quando algo não pôde ser apurado, o ciclo diz isso por escrito (*"quantas
notícias eram inéditas: não apurado"*) em vez de gravar zero.

---

## 9. Status, e o que cada um bloqueia

Quatro palavras, na precedência do pior para o melhor:

| Status | Quando | Recomendação emergencial |
|---|---|---|
| `indisponivel` | nunca houve sucesso, ou nenhum provedor respondeu agora | **bloqueada** |
| `atrasado` | último sucesso mais velho que o SLA do modo | **bloqueada** |
| `degradado` | dentro do prazo, mas parcial ou vindo de cache vencido | **bloqueada** |
| `atualizado` | dentro do prazo e completo | permitida |

Dado velho **coletado por provedor degradado é `atrasado`**, não `degradado`: a
idade é o defeito maior.

Bloquear não é esconder. O painel continua exibindo o que foi coletado, com o
carimbo de atraso e a confiança reduzida. O que fica bloqueado é a recomendação
que se apresentaria como urgente apoiada em dado vencido.

---

## 10. Proteções

| Risco nomeado no requisito | Como está protegido |
|---|---|
| Execução simultânea do mesmo job | `pg_try_advisory_lock` + `concurrency` no workflow. A segunda sai `skipped` **declarando** o motivo |
| Duplicação de registros | dedup por `id_dedup`/simhash + upsert |
| Corrida entre processos | o lock; e o carimbo de sucesso avança antes da gravação, então um Supabase fora do ar não desfaz o fato da coleta |
| Consumo excessivo de requisições | orçamento no banco, anterior à chamada; universo encolhe conforme a frequência sobe |
| Bloqueio permanente do agendador | lock é `try` (nunca espera) e é solto no `finally`; `timeout-minutes: 15` no job |
| Falhas silenciosas | todo ciclo é gravado, inclusive o que morreu no meio. Silêncio de job é indistinguível de job que nunca rodou |
| Crescimento ilimitado do banco | `expurgar()` a cada ciclo, por `NOTICIAS_RETENCAO_DIAS`; o expurgo vira limitação escrita |
| Mudança incorreta de timezone | tudo em UTC; `NOTICIAS_TIMEZONE` é só apresentação |
| Atualização parcial como completa | status `degradado`, limitações escritas, universo truncado declarado |

**Botão manual.** Respeita o rate limit e o lock (`forcar` ignora só o freio de
cadência — as outras duas travas protegem terceiros e a cota, e um botão não
deveria poder desligá-las). Fica **desabilitado com o motivo escrito** em vez de
sumir: um controle que desaparece deixa o usuário sem saber se a função existe.
Falha na atualização **não apaga a última coleta válida** — a tela diz que
continua exibindo os dados da última coleta bem-sucedida.

---

## 11. Saúde dos serviços

Sete verificações, nenhuma delas na rede: banco, provedores, agendador, worker,
cache, serviço de preços, serviço de LLM.

Um health check que gasta cota transforma o painel de saúde num consumidor de
requisições — abrir a tela cinco vezes gastaria um quinto da cota diária do
Alpha Vantage. O que se verifica é configuração, carimbo e conectividade com o
próprio banco.

`ok` é ternário: `True` / `False` / **`None` = não verificado**. Serviço que não
pôde ser verificado não é serviço com defeito. Marcar desconhecido como falho
encheria a tela de alarme falso; marcar como saudável esconderia risco.

Na linha de comando: `python -m data_pipeline.cli_noticias --saude`.

---

## 12. Demonstração de que não depende do Streamlit

Três evidências, todas verificáveis:

1. `python -m data_pipeline.cli_noticias` é um processo headless. O teste
   `test_o_job_roda_sem_streamlit` roda o ciclo com `sys.modules["streamlit"]`
   anulado — qualquer `import streamlit` no caminho quebraria.
2. `test_o_job_nao_depende_de_session_state` inspeciona o fonte do job: nem
   `streamlit`, nem `session_state`.
3. A tela lê o acervo do banco (`armazenamento.ler_recentes`) quando a sessão
   nunca coletou, e rotula: *"notícias vindas do acervo da coleta automática;
   esta sessão não consultou os provedores"*. Sem essa leitura, a tela diria
   "nenhuma coleta nesta sessão" com o acervo cheio — apresentando trabalho
   feito como trabalho ausente.

---

## 13. Testes

`tests/test_noticias_infraestrutura.py` cobre os treze cenários do requisito:
ciclo normal, ativação de Vigilância, ativação de Crise, rebaixamento, execução
concorrente, provedor indisponível, todos indisponíveis, rate limit esgotado,
dados vencidos, atualização parcial, reinicialização do servidor, frontend
fechado e mudança de timezone — mais universo por modo, truncagem declarada,
carteira ilegível, cota compartilhada e as verificações de saúde.

Nada toca rede ou banco: o estado compartilhado é substituído por um duplo em
memória e os provedores são os falsos de `tests/apoio_noticias.py`. Não se usa
SQLite: o DDL é Postgres (`JSONB`, `BIGSERIAL`, `pg_try_advisory_lock`), e um
SQLite provaria coisa diferente da que roda em produção. O que se verifica é a
**decisão** do job — qual modo, qual universo, qual status, o que avança e o que
não avança — e essa decisão não é do banco.

---

## 14. O que esta camada não faz

* **Não decide o nível de crise.** Recebe. Quem avalia é
  `core/eventos_extremos/`, que ainda não tem chamador em produção — enquanto
  isso o modo gravado é sempre Normal, e `estado_coleta.definir_modo()` é a
  costura que espera esse chamador.
* **Não notifica ninguém.** Canal externo de alerta é assunto do Prompt 5, atrás
  de feature flag.
* **Não garante pontualidade.** Garante cadência média e, quando ela não é
  cumprida, **diz** — em vez de exibir dado vencido como fresco.
