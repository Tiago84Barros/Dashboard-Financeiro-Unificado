# Inteligência de Mercado — notícias, crise, antifragilidade e memória

Esta é a documentação da camada de apresentação (`views/inteligencia_mercado.py`,
`design/inteligencia.py`) e da camada de intermediação (`core/inteligencia/`) que
ligam os motores já existentes à tela e à LLM.

## O que cada camada pode fazer

| Camada | Pode | Não pode |
|---|---|---|
| `core/noticias`, `core/memoria_mercado`, `core/eventos_extremos` | calcular | apresentar |
| `core/inteligencia/painel.py` | reunir o que foi calculado, qualificar e declarar lacunas | recalcular |
| `core/inteligencia/llm.py` | explicar em texto o que está no painel | produzir número |
| `views/inteligencia_mercado.py`, `design/inteligencia.py` | apresentar | calcular |

A separação não é convenção de estilo: é o que torna verificável a regra "a LLM
não pode inventar números". `core/inteligencia/llm.py::check_grounding` roda a
resposta do modelo contra **o mesmo texto** que foi enviado como contexto
(`assert ctx in montar_prompt(pn)`), e qualquer número que não esteja lá reprova
a resposta inteira. Não há instrução de prompt encarregada disso — instrução de
prompt não é garantia.

"A LLM não pode alterar scores" não precisa de detector próprio: um score alterado
é, por construção, um número que não está no painel, e cai no mesmo portão.

## A tela

Rota `🧭 Inteligência de Mercado` no grupo Investimentos de `app.py`.

Ordem fixa: **barra de frescor primeiro**, abas depois. A barra publica a última
atualização pela **fonte mais antiga** — publicar a mais recente faria metade dos
dados velhos parecerem atuais.

Abas: Notícias · Crise · Antifragilidade · Memória de mercado · Fundamentos +
Cenário · Explicação · Alertas.

### Coleta é sob demanda, nunca na renderização

Rede dentro do `render()` transforma provedor lento em página travada. A coleta só
acontece no botão **"Atualizar notícias agora"** e o resultado vive em
`st.session_state[CHAVE_COLETA]`.

Consequência declarada na própria tela: ausência de notícia é publicada como
*"não coletada nesta sessão"*, nunca como *"sem notícias"*. Quando um provedor
está fora do ar, o painel acrescenta a limitação *"a ausência de notícias pode ser
falha de coleta, não calmaria"*.

### Fato, hipótese e estimativa

Todo valor na tela carrega selo de qualidade vindo de
`core.inteligencia.qualificacao.APARENCIA` — ícone **+** texto **+** cor, nunca só
cor. Quem não distingue verde de vermelho lê o mesmo conteúdo, e um print em
escala de cinza também.

Valores não medidos aparecem **na mesma grade** dos medidos, com o selo "Não
medido". Escondê-los numa gaveta faria a tela parecer completa. Ao lado de todo
bloco vai a `cobertura` — a fração dos componentes que foi possível medir.

### Nenhuma operação é executada automaticamente

A aba de Crise declara isso no texto, e o motor sustenta: o melhor desfecho
possível de `core/noticias/portoes.py` é `ACAO_SUGERIR_REVISAO`. Não existe
caminho de código que emita ordem.

## Alertas

Canal por nível (`core/inteligencia/alertas.py::canal_para`):

| Nível | Canal |
|---|---|
| 0–1 | painel |
| 2 | painel destacado, **apenas se afetar a carteira** |
| 3–4 | destacado; externo **somente** com infraestrutura configurada **e** autorização explícita |

Gravidade não substitui consentimento: nível 4 sem `autorizou_externo` continua no
painel.

### O que sai de casa

`redigir_externo` **reconstrói** a mensagem a partir de uma lista branca de 5
campos, em vez de filtrar a mensagem interna. Filtro falha aberto — campo novo
escapa por acidente; reconstrução falha fechada. Nunca saem: símbolo de ativo,
peso na carteira, valor em reais, prioridade de aporte.

Repetição é contida em `core/eventos_extremos/transicao.py::deve_notificar`
(`DELTA_MATERIAL = 0.15`), e **só lá** — `alertas.py` deliberadamente não
reimplementa a regra, para não haver duas verdades sobre "mudou o suficiente".

## Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `NOTICIAS_PROVEDORES` | `alphavantage,marketaux,rss` | provedores habilitados |
| `ALPHAVANTAGE_API_KEY` | — | 25 chamadas/dia no plano free |
| `MARKETAUX_API_KEY` | — | 100 chamadas/dia no plano free |
| `NOTICIAS_CACHE_TTL_MIN` | `15` | TTL do cache de coleta |
| `NOTICIAS_FREQ_NORMAL_MIN` | `240` | cadência normal |
| `NOTICIAS_FREQ_EMERGENCIA_MIN` | `30` | cadência sob evento |
| `NOTICIAS_IDADE_MAX_HORAS` | `72` | idade máxima da notícia aceita |
| `NOTICIAS_LIMITE_POR_CONSULTA` | `50` | teto por chamada |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | — | LLM; sem nenhuma, a explicação vem do backend |

Chaves saem exclusivamente de `core.config.settings`. Nenhuma é lida por
`os.getenv` fora de `core/config.py`, e a tela nunca exibe valor de chave — apenas
`tem_chave` booleano vindo de `registro.descrever()`.

O orçamento de chamadas é controlado por `core/noticias/rate_limit.py`, em arquivo
(`local_staging/noticias/rate_limit.json`) e **antes** da chamada, porque o job
agendado, o script manual e a sessão Streamlit são processos diferentes dividindo
a mesma cota.

## Validade dos dados

`core/inteligencia/painel.py::VALIDADE_PADRAO_HORAS`:

```
noticias 6h · mercado 24h · carteira 24h · memoria_mercado 168h · crise 6h
```

Vencido não é escondido: vira selo na barra de frescor, entra em `desatualizados`
e é promovido a limitação declarada do painel.

## Limitações conhecidas

1. **Coleta é por sessão.** Sem sessão aberta, nada é coletado. A infraestrutura
   de atualização contínua (job/worker desacoplado) é o próximo passo e ainda não
   existe.
2. **Não há trilha de auditoria persistida** para as decisões da camada — o
   histórico de alerta vive no objeto, não no banco.
3. **Os pesos não foram calibrados contra histórico.** Os valores atuais são
   sugestões iniciais, não resultados de validação.
4. **`core/eventos_extremos/plano.py`, `armazenamento.py` e `ponte.py` não
   existem** ainda; a taxonomia não cobre pandemia, quebra de banco e evento
   climático.
5. **Nada aqui prevê cisne negro.** O motor classifica evidência já observável e
   publica faixa, com amostra declarada; abaixo de
   `amostra.N_MINIMO_EXPERIMENTAL = 8` nenhuma faixa é publicada.

## Testes

- `tests/test_inteligencia_qualificacao.py`
- `tests/test_inteligencia_painel.py`
- `tests/test_inteligencia_llm.py` — inclui o gate de linguagem e a rejeição de número inventado
- `tests/test_inteligencia_alertas.py` — inclui a prova de que dado sensível não sai em notificação externa
- `tests/test_views_inteligencia_mercado.py`

Suíte completa em 02/09/2026: **3671 passed, 3 skipped**.
