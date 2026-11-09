# Alertas de Investimentos — design

Data: 2026-11-09 · Estado: aguardando revisão do usuário · Nada implementado

## Objetivo

Uma aba **Alertas** em Investimentos que sinaliza, de forma rara e explicável,
oportunidades de compra e riscos de tese em ações B3, FII e ações dos EUA, da
carteira e de uma lista de observação.

A aba não decide. Ela diz o que aconteceu, por que importa, com que evidência,
em que horizonte e com que confiança, e o que separaria distorção temporária de
deterioração real. A ação continua na Inteligência dos Ativos, com as sete
decisões e a regra de vender com cautela.

O que a aba **não** é: central de notificação de oscilação diária, sinal de
"preço caiu, compre", nem um segundo motor de veredito.

## Decisões tomadas

| Pergunta | Decisão |
|---|---|
| Função | Camada de **eventos** sobre a Inteligência. A Inteligência descreve estado; o alerta é evento com início, evidência e validade |
| Veredito | Nenhum motor novo de qualidade ou valuation. O alerta lê `core.inteligencia_ativos.avaliacao.avaliar` e a decisão de `resumida` |
| Universo | Ações B3, FII e ações dos EUA, da carteira **e** de uma lista de observação por usuário |
| Níveis | **Atenção**, frequente e solto; **Risco de tese** e **Oportunidade**, raros e exigentes |
| Lugar | Aba própria em Investimentos + resumo no Dashboard Geral + selo no ativo na Inteligência |
| Teste | Ponto no tempo, calibração até 2021, período guardado 2022–2025 rodado uma vez, custos de negociação, controles |
| Ordem | Risco primeiro; oportunidade só depois de passar no período guardado |
| Fora do MVP | Saída por exagero, notificação externa, LLM redigindo, consenso de analistas |

Descartadas:

- **Só um selo dentro da Inteligência.** A página foi enxugada em 30/09 por
  excesso visual, e o alerta precisa de ciclo de vida, histórico e placar.
- **Tela própria no menu lateral.** Afastaria o alerta da carteira que ele
  qualifica.
- **Reaproveitar a aba "🔔 Alertas" de `views/inteligencia_mercado.py`.** A tela
  saiu da navegação em 06/09 (commit `3fafac37`) por ser retaguarda, e a aba
  trata de crise sistêmica, guardada só na sessão.

## 1. O que já existe e é reaproveitado

O motor novo **orquestra**. Cada pergunta abaixo já tem dono no repositório, e
responder de novo criaria uma segunda verdade.

| Pergunta | Dono atual | Uso pelos Alertas |
|---|---|---|
| Qualidade, valuation, mercado e alertas eliminatórios do ativo | `core/inteligencia_ativos/avaliacao.py::avaliar` | Estado de cada data; transição de estado é gatilho de risco |
| Decisão por ativo | `core/inteligencia_ativos/resumida.py` | Destino do link do cartão; nunca recalculada |
| Choque sistêmico, macro e de mercado, com cinco níveis, histerese e trilha | `core/eventos_extremos/transicao.py` | Gatilho macro do nível Atenção. Nenhum detector macro novo |
| Notícia confiável: seis portões conjuntivos, fontes independentes | `core/noticias/portoes.py::avaliar` | Gatilho "notícia forte" do nível Atenção |
| "O que aconteceu das outras vezes", com `n` e retorno anormal | `core/memoria_mercado/estimativa.py::estimar` | Taxa-base exibida no cartão |
| Canal e redação externa sem dado sensível | `core/inteligencia/alertas.py` | Fora do MVP; é o caminho quando houver notificação externa |
| Contexto de mercado para LLM | `core/contexto_mercado.py::bloco_contexto_mercado` | Fora do MVP; nenhuma LLM cria ou agrava alerta |

Nomes já ocupados que **não** são reutilizados:

- `views/alertas.py` e `core/alertas.py`: alertas de orçamento e metas, sem rota, mantidos por decisão.
- `core/inteligencia/alertas.py`: política de canal dos alertas de crise.

O pacote novo é `core/alertas_ativos/` e a tela é `views/investimentos_alertas.py`.

## 2. Famílias de evidência

Cada alerta conta famílias **independentes**. Duas evidências da mesma família
contam uma.

| Família | Fonte | Frequência |
|---|---|---|
| Preço | B3: `market.b3_security_history` (COTAHIST, 2010+), ajustado por evento de capital antes de medir queda. EUA: `market_us.prices_daily`. FII: COTAHIST diário e `fii_metrics_monthly` | diária |
| Fundamento | B3: `market.income_statements`, `balance_sheets` e `cash_flow_statements`, com data de publicação de `market.cvm_filing_publications` (2011+). FII: `market.fii_metric_observations` (2016+). EUA: demonstrações em `market_us` | trimestral ou mensal |
| Documento oficial | B3: `public.docs_corporativos` (IPE). FII: `market.fii_documents` (FNET). EUA: `market_us.sec_filings` (8-K com item) | diária |
| Notícia | `core.noticias`, só quando o veredito dos portões é `sugerir_revisao` | diária |
| Macro | Nível de `core.eventos_extremos` | diária |

Regras:

- **Notícia nunca é a evidência principal** de Risco de tese ou Oportunidade.
- **Ausência de dado é `None`, nunca contra nem a favor.** Família sem dado não
  conta e aparece no cartão como "sem dado".
- **A data que vale é a de disponibilidade**, nunca a de referência. Um ITR de
  setembro publicado em novembro só existe a partir de novembro.

## 3. Tipos de alerta

| Tipo | Nível | Gatilho | Bloqueio | Fatia |
|---|---|---|---|---|
| Evento oficial grave | Risco de tese | Um documento oficial de categoria grave basta: recuperação judicial, reapresentação de demonstrações, ressalva ou abstenção do auditor, questionamento da CVM ou da B3, aumento de capital com diluição, troca de controle, corte ou suspensão de proventos. EUA: itens 1.03, 2.06, 3.01, 4.01, 4.02 e 5.01 do 8-K | Documento de categoria rotineira | 1 |
| Deterioração persistente | Risco de tese | Transição de estado em `avaliar`: qualidade de sólida para frágil, ou alerta eliminatório novo; **ou** piora em duas famílias por dois trimestres seguidos | Piora de um trimestre que reverte | 1 |
| FII: corte de rendimento ou VPA em queda | Risco de tese | Corte de rendimento confirmado pelo FNET, ou VPA em queda por dois meses com vacância ou inadimplência subindo | Amortização declarada | 1 |
| Choque macro | Atenção | `eventos_extremos` em nível 2 ou mais. Um cartão por evento, com os ativos mais sensíveis ao fator listados | Nível que volta antes de 3 pregões | 2 |
| Notícia forte | Atenção | Veredito `sugerir_revisao` dos portões, com o ativo como **sujeito** da notícia | Ativo só citado | 2 |
| Queda idiossincrática | Atenção | Queda contra pares e índice no extremo do próprio histórico, ajustada por evento de capital | Queda na data-ex ou em grupamento | 2 |
| Queda com deterioração | Atenção | A mesma queda, com piora de fundamento | Nenhum | 2 |
| Desconto sem deterioração | Oportunidade | **Três famílias a favor e nenhuma contra**: queda idiossincrática; múltiplo no decil baixo do próprio histórico; fundamento do último período sem queda proporcional; qualidade sólida antes da queda | Alerta eliminatório; documento grave; dívida líquida sobre EBITDA acima do teto setorial; cíclica com lucro no pico do próprio ciclo | 3 |
| FII com P/VP no fundo do histórico | Oportunidade | P/VP no decil baixo do próprio fundo, com VPA, rendimento, vacância e inadimplência estáveis. Só FII de tijolo | FII de papel; qualquer Risco de tese ativo | 3 |

**Sensibilidade a fator macro** é medida pelo histórico de retorno de cada ativo
contra o fator (Selic, curva, câmbio) em janela de 5 anos, não presumida pelo
setor.

**FII de papel** fica restrito a evento oficial e corte de rendimento. A leitura
de crédito continua na Seleção de FIIs.

## 4. Ruído e confiança

- **Orçamento de raridade escrito.** Cada tipo de nível alto tem frequência alvo
  por ativo-ano, calibrada no período até 2021 e versionada com a regra. Tipo
  que excede o orçamento no período guardado não é lançado.
- **Um alerta por episódio.** Chave `(ativo, tipo, data_inicio)`. Evidência nova
  entra no alerta aberto.
- **Histerese.** Limiar de entrada e de saída diferentes, e prazo mínimo antes de
  resolver.
- **Gravidade sobe só com outra família.** Mais evidência da mesma família não
  agrava.
- **Confiança por regra.** Calculada pelas famílias que concordam, pelo frescor
  das fontes e pela taxa-base. A LLM não cria, não agrava e não pontua alerta.
- **Taxa-base com os casos ruins.** Mediana, quartis e a fração de episódios em
  que o preço caiu mais, com `n`. Abaixo do `n` mínimo de
  `core.memoria_mercado.amostra`, o cartão diz "amostra insuficiente".
- **Fonte velha suspende.** O alerta fica Suspenso, com a fonte e a data no
  cartão. Nunca some sem aviso.

## 5. Ciclo de vida

| Estado | Entra quando | O que acontece |
|---|---|---|
| Criado | Condições cumpridas com dado fresco | Grava registro imutável: preço, evidências, versão da regra, hash do registro anterior |
| Atualizado | Chega evidência nova | Evidência anexada; `data_inicio` não muda; gravidade só sobe com outra família |
| Suspenso | Uma fonte que sustenta o alerta vence | Volta ao estado anterior quando a fonte volta |
| Resolvido | Condição desfeita pela histerese, ou horizonte vencido | Resultado medido no horizonte contra o índice e contra o controle |
| Invalidado | Evidência contraria a própria tese | Ex.: o ITR seguinte mostra a deterioração. Pode abrir um Risco de tese ligado ao alerta invalidado |
| Expirado | Atenção sem confirmação no prazo do tipo | Conta como alarme falso no placar |

O registro segue o padrão da Carteira 20: tabela só de inserção no armazém
local, com cadeia de hashes. Mudança de estado é linha nova, nunca `UPDATE`.

## 6. Arquitetura e fluxo de dados

```
armazém local ──► core/alertas_ativos (motor, rotina noturna)
                    │  lê: avaliar(), eventos_extremos, noticias.portoes,
                    │      memoria_mercado, preço, fundamento, documentos
                    ├─► alertas_ativos.registro   (armazém, imutável)
                    └─► data/public/alertas_ativos.json.gz  (vitrine)
                                   │
Streamlit Cloud ◄──────────────────┘
  views/investimentos_alertas.py  filtra por carteira + lista de observação
  views/dashboard_geral.py        resumo do nível alto
  Inteligência dos Ativos         selo "alerta ativo" no ativo
```

- **O motor calcula o universo inteiro.** A vitrine não carrega dado pessoal; o
  filtro pela carteira e pela lista de observação acontece na renderização.
- **O motor roda localmente.** O Supabase está acima do limite gratuito; o
  registro e o histórico ficam no armazém.
- **Lista de observação** é a única escrita nova no Supabase:
  `087_lista_observacao.sql`, com `(user_id, ticker, mercado, criado_em)`,
  chave `(user_id, ticker)` e RLS por usuário. A escrita no Supabase já foi
  barrada pelo classificador de segurança mesmo autorizada; se for barrada de
  novo, a migration é aplicada pelo usuário.
- **Vitrine com mais de 48 h** aparece marcada como velha, como as outras.
- **Publicador:** `scripts/publish_alertas_ativos.py`, alvo novo em
  `core/publicacao_agenda.py`, idempotente.

## 7. Interface

**Aba "🚨 Alertas" em Investimentos**, entre "🔍 Análise" e a aba da
Inteligência. O rótulo leva a contagem de alertas de nível alto ativos.

- **Topo:** contagem dos tipos de Risco de tese e de Oportunidade, e o placar
  dos alertas resolvidos.
- **Estado vazio:** o placar e os últimos alertas resolvidos, com o que
  aconteceu depois de cada um.

Cartão, nesta ordem:

1. O fato numa frase, com número e data.
2. A hipótese: oportunidade ou ameaça.
3. Evidências por família, com fonte, data de disponibilidade e link.
4. As seis perguntas, cada uma com sim, não ou sem dado:
   1. O lucro e o caixa caíram na mesma proporção do preço?
   2. A queda foi do ativo ou do setor e do mercado?
   3. Há documento oficial que explique?
   4. O balanço aguenta esperar?
   5. Os proventos foram cortados?
   6. A piora durou mais de um trimestre?
5. Horizonte, validade e o próximo documento decisivo ("próximo ITR em DD/MM").
6. O que invalidaria o alerta.
7. Taxa-base com `n` e os casos ruins.
8. Link para o ativo na Inteligência dos Ativos.

Regras visuais:

- Três tipos com cor própria (risco, oportunidade, atenção) por token de tema,
  no máximo dois níveis de gravidade.
- Atenção fica numa seção recolhida abaixo do nível alto e não entra em
  contagem nenhuma.
- Nada intradiário, nenhum botão de compra ou venda.

**Dashboard Geral:** um card com a contagem de Risco de tese e Oportunidade
ativos na carteira e na lista de observação, com atalho para a aba.

**Inteligência dos Ativos:** selo "alerta ativo" no ativo, com o tipo.

## 8. Teste antes de lançar

Teste ponto no tempo, com o que se sabia em cada data, incluindo empresas que
saíram da bolsa.

| Mercado | Calibração | Período guardado | Observação |
|---|---|---|---|
| Ações B3 | 2012–2021 | 2022–2025 | O IPE no armazém começa em 2023. A fatia 1 faz o backfill só de metadados (categoria, data, código CVM) do IPE pelos dados abertos da CVM desde 2011 |
| FII | 2016–2021 | 2022–2025 | `fii_metric_observations` começa em 2016 |
| Ações EUA | 2012–2021 | 2022–2025 | `market_us.sec_filings` não existe no armazém em 09/11, embora a 085 diga que mora lá. A fatia 1 aplica a 085 localmente e mede o histórico coletável |

Regras do teste:

- **O período guardado roda uma vez**, no fim, com as regras congeladas e
  versionadas. Ajuste depois dele reabre a calibração com outra janela guardada.
- **Custos de negociação** entram no retorno: emolumentos e spread estimado pela
  liquidez do ativo.
- **Mercado sem histórico suficiente** para um tipo: o tipo sai como
  experimental, com o motivo no cartão, ou não sai.

Critérios de aprovação por tipo, no período guardado:

| Tipo | Passa se |
|---|---|
| Oportunidade | Retorno excedente em 12 meses, líquido de custos, acima do controle "queda do mesmo tamanho com deterioração", com intervalo de 95% que não cruza zero, dentro do orçamento de raridade |
| Risco de tese | Fração seguida de queda acima de 20% em 12 meses maior que a taxa-base do universo, com intervalo que não cruza zero; antecedência mediana reportada |
| Atenção | Fração confirmada por evidência de outra família no prazo e fração de alarme falso, reportadas. Sem limiar de aprovação; o nível existe para ser frequente |

Depois do lançamento, cada alerta é julgado no próprio horizonte e entra no
placar da aba. As metas são rotuladas como convenção até haver amostra.

## 9. Fatias de entrega

Cada fatia é um PR e é entregável sozinha.

1. **Motor e risco.** `core/alertas_ativos`, registro imutável, publicador,
   vitrine, tipos de Risco de tese para B3, FII e EUA, backfill de metadados do
   IPE, 085 no armazém, teste do risco, aba, card no Dashboard Geral e selo na
   Inteligência. Tipo de risco que não passar no período guardado sai como
   experimental, com o resultado no cartão, e não entra nas contagens.
2. **Atenção.** Choque macro, notícia forte, queda idiossincrática e queda com
   deterioração, com medição de confirmação e alarme falso.
3. **Oportunidade.** Os dois tipos, lançados só se passarem no período guardado.
4. **Lista de observação.** Migration 087, tela de manutenção da lista e filtro
   na aba. Entra junto com a oportunidade, onde ela mais importa.

Pronto, em cada fatia:

- testes unitários do motor com casos de criação, atualização, histerese,
  suspensão, resolução, invalidação e expiração;
- teste de equivalência: o estado que o alerta lê é o mesmo que a Inteligência
  mostra para o ativo na mesma data;
- relatório do teste ponto no tempo em `docs/`, com números e `n`;
- as três abas de Configurações atualizadas no mesmo PR (seção 10);
- verificado na Streamlit Cloud, não só localmente.

## 10. Configurações no mesmo PR

| Aba | O que muda |
|---|---|
| 🗺️ Mapa do App | `docs/mapa_app/areas/` de Investimentos ganha a aba Alertas e a do Dashboard Geral ganha o card; depois `python scripts/montar_mapa_app.py` |
| 📚 Documentação | `views/documentacao.py`: nó da aba Alertas no `FlowSpec` de Investimentos, card no Dashboard Geral, selo na Inteligência; contagens por extenso das abas de Investimentos |
| 🎯 Grau de Confiança | `core/confianca_secao.py::_SECOES`: seção da aba, medindo o frescor de `alertas_ativos.json.gz` e das fontes que ela cita |

`tests/test_configuracoes_sincronizadas.py` barra o que não estiver sincronizado.

## 11. Riscos e limitações

- **Sem estimativas de analistas, "o fundamento não piorou" mede o passado.** O
  mercado cai pelo lucro esperado. O tipo Oportunidade vai pegar armadilhas de
  valor, sobretudo em cíclicas; o teste contra o controle é a defesa.
- **O ITR sai até 45 dias depois do trimestre.** A janela que mais importa é a
  que tem menos dado. O cartão mostra a data do próximo documento decisivo.
- **COTAHIST é bruto.** Queda medida sem ajuste por evento de capital confunde
  grupamento e data-ex com queda.
- **Viés de sobrevivência.** Sem as empresas que saíram da bolsa, comprar no
  desconto parece sempre recompensado.
- **Amostra pequena por carteira.** A confiança se apoia na taxa-base do
  universo, não no placar de um usuário.
- **Notícia como gatilho.** A identificação do sujeito da notícia é o ponto mais
  frágil. Ela usa a regra de entidades de `core.noticias`, nunca a LLM.
- **Frescor das fontes oficiais.** Em 09/11, o IPE no armazém vai até 30/10.
  Antes da fatia 1, confirmar que a coleta não parou.
- **Escopo.** Três mercados e três níveis multiplicam regras e testes. As fatias
  existem para cada uma ser entregável e verificável sozinha.

## Fora do escopo

Saída por exagero, notificação externa, LLM redigindo o cartão, consenso e
revisão de estimativas, negociação de administradores (CVM 44), aluguel de
ações, rating estruturado e REIT dos EUA.
