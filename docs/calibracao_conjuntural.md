# Calibração quantitativa do Motor Conjuntural

Data da medição: 02/09/2026 · `calibracao_versao` 1.0.0 · `taxonomia_versao` 1.0.0
Fonte: armazém local (`dfu_warehouse`, Postgres 16, porta 5433). Nada foi gravado.

Reprodução:

```bash
python scripts/calibrar_motor_conjuntural.py --mercado b3 --horizonte 5 --limite 45000
```

---

## 1. O conjunto de validação pedido não existe, e não foi fabricado

A instrução pede um conjunto histórico cobrindo **15 tipos de evento**. Ele não
é obtível hoje:

- não há corpus histórico de notícias neste projeto — `noticias_itens` nasce
  vazia e é populada a partir de agora, pelo coletor do Prompt 2;
- nenhum provedor gratuito em uso serve arquivo retroativo de manchetes com
  carimbo de publicação.

Rotular notícia do passado a partir do que se sabe hoje seria look-ahead pela
porta da frente, e publicar cobertura que ninguém mediu repetiria
`memoria: declaracao-de-rigor-nao-verificada`.

Então a calibração foi ancorada no que existe com **carimbo de data verificável**
no armazém, declarado em `core/calibracao/catalogo.py`:

| tipo | fonte | carimbo | ressalva declarada |
|---|---|---|---|
| `dividendo` | `market.dividends` / `market_us.dividends` | `ex_date` | data-ex, não data do anúncio: mede a queda mecânica do provento; tipos misturados (DIVIDENDO, JCP, RENDIMENTO, AMORTIZAÇÃO) |
| `fato_relevante` | `market.fii_documents` | `source_published_at` | só FII; o título não é classificado |
| `deslistagem` | `market_us.delistings` | `delisted_date` | só EUA |

**Cobertura honesta: 3 de 25 tipos (12%).** O número sai na primeira linha da
execução, não no rodapé. `catalogo.cobertura()` levanta `RuntimeError` se alguém
acrescentar um tipo à taxonomia sem declarar a fonte **ou** o motivo da ausência
— é o remédio direto para `memoria: verificador-e-escritor-listas-diferentes`.

## 2. Ponto-no-tempo, em dois lugares

**Um — a amostra.** A estimativa do evento *i* usa apenas eventos anteriores
cuja **janela de medição já fechou** antes de *i*. Filtrar só por data de evento
deixaria entrar um evento de dez dias antes cujo retorno de 20 pregões ainda não
existia. A conversão pregão→dia corrido usa 1,55 e arredonda **para cima** o
tempo de espera: errar para o lado de descartar evidência é o único erro que não
vira look-ahead.

**Dois — o limiar.** "Movimento relevante" não é um número absoluto. Sai da
volatilidade dos **60 pregões anteriores** ao evento (`volatilidade_pre`), por
classe de ativo, em `core/calibracao/limiar.py`. Com o limiar único de 3% que
existia antes, o mesmo movimento era ~5 desvios num FII (o motor nunca falaria) e
menos de um desvio numa ação volátil (falaria todo dia).

## 3. Dois defeitos que só a execução mostrou

### 3.1 Os seis portões da instrução promoveriam um motor mudo

Na primeira rodada real, o motor apontou **2 eventos em 2.146** e deixou 283
movimentos relevantes passarem — e **passou** no portão de alarmes excessivos,
porque quem não fala nunca dá alarme falso.

Foi acrescentado um **sétimo portão**, `deteccao_util`
(`core/calibracao/pesos.py`, `TETO_NAO_DETECCAO = 0.50`). Ele não está na lista
da instrução; está aqui porque a lista, sozinha, não fecha. É a mesma família de
`memoria: quem-pergunta-menos-tira-nota-maior`.

### 3.2 Evento anterior à série virava medição confiante de outro dia

`SeriePrecos.indice_do_pregao` devolve o primeiro pregão *em ou após* a data — a
convenção certa para um fato de sábado. Faltava fundo: das 3.000 datas-ex mais
antigas de `market.dividends` (1995–2005), **2.067 recebiam
`data_pregao_zero = 2010-01-04`**, o primeiro pregão de
`market.b3_security_history`, e todas saíam medidas, completas e sem aviso.

Corrigido na origem, não no script: `TOLERANCIA_PREGAO_ZERO_DIAS = 11` em
`core/memoria_mercado/retornos.py`. Onze dias cobrem feriado emendado com fim de
semana; acima disso não é calendário, é ausência de série.
É `memoria: fallback-nunca-contradiz` — o preenchimento só tapa buraco, e por
isso nunca aparece como erro. Fixado por `tests/test_memoria_mercado_pregao_zero.py`.

### 3.3 Série de preços contaminada (contado, não apagado)

`TETO_PLAUSIBILIDADE_ANORMAL = 1.0` no script: retorno anormal acima de 100% é
defeito de série, não evento. O motivo é medido — em
`market.fii_b3_security_history` há 581 retornos diários acima de 50%, o maior
sendo +305.900% (BRCR12 em 25/02/2013), porque a série mistura cotas e recibos da
mesma família. É `memoria: preco-bilionario-e-retroajuste` visto do lado de quem
lê a série.

O descartado é **contado e publicado** (`n_defeito_de_serie` e
`simbolos_com_defeito` no relatório), nunca winsorizado em silêncio — o erro de
`memoria: faixa-de-validacao-apaga-evidencia`. **16 eventos de 2.146 no FII
moviam o erro médio de 2,9% para 13,1%.**

## 4. Resultado medido (horizonte 5 pregões)

| | B3 | FII | EUA |
|---|---|---|---|
| eventos avaliados | 9.503 | 1.871 | 2.987 |
| tipo | dividendo | fato_relevante | dividendo + deslistagem |
| VP / FP / VN / FN | 14 / 51 / 8.005 / 1.433 | 1 / 1 / 1.708 / 161 | 1 / 0 / 2.807 / 179 |
| precisão | 0,215 | 0,50 | 1,00 |
| recall | **0,010** | **0,006** | **0,006** |
| F1 | 0,019 | 0,012 | 0,011 |
| taxa de falso alarme | 0,6% | 0,06% | 0,0% |
| **não-detecção** | **99,0%** | **99,4%** | **99,4%** |
| Brier | 0,131 | 0,087 | 0,057 |
| erro de calibração | 3,6 pp | 2,3 pp | 1,4 pp |
| MAE | 5,22% | 2,64% | 2,40% |
| MAE da referência ingênua ("nada acontece") | 5,51% | 2,63% | 2,41% |
| **ganho sobre a referência — absoluto (pp de MAE)** | **+0,29 pp** | **−0,02 pp** | **+0,03 pp** |
| **ganho sobre a referência — relativo (`ganho_sobre_referencia`)** | **+5,28%** | ≈ 0 ¹ | ≈ 0 ¹ |
| cobertura da faixa (alvo 80%) | 78,8% | 78,8% | 77,0% |
| acerto de direção | 67,4% | **50,0%** | 52,6% |
| concentrado num período | não | **sim** | não |
| eventos excluídos por defeito de série | 52 | 16 | 0 |

Leitura sem suavizar:

- **O motor cala.** Detecta 1 de cada 100 movimentos relevantes nos três
  mercados. A precisão alta dos EUA (1,00) é de um único acerto.
- **A probabilidade está calibrada** (erro de 1,4 a 3,6 pontos) — mas está
  calibrada em torno de "quase nunca". Calibração sem recall é honestidade sobre
  o silêncio.
- **A magnitude não supera "nada acontece".** O ganho é de centésimos de ponto
  percentual em dois mercados e negativo no FII.
- **Duas linhas para o mesmo ganho, e elas não são intercambiáveis.** A primeira
  é a diferença de MAE em pontos percentuais (5,51 − 5,22 = 0,29 pp); a segunda
  é a fração do erro removida, que é o que o código publica em
  `Magnitude.ganho_sobre_referencia` — 0,29 / 5,51 = 5,28%, gravado no JSON como
  `0,05277`. Publicá-las sob o mesmo rótulo faria "0,29" e "0,05" parecerem
  divergência de medição quando são a mesma medição em duas unidades. **+5,28%
  soa maior que +0,29 pp e não é melhor notícia**: 5,28% de um erro de 5,51%
  ainda deixa 5,22% de erro, e a conclusão da linha acima não muda.
- **A direção é cara-ou-coroa no FII** (50,03%). Na B3, 67,4% — mas o evento é
  data-ex de provento, cuja direção é mecânica e conhecida sem modelo nenhum.
- **A faixa é estreita demais**: cobre 77–79% onde deveria cobrir 80%.

¹ Só a B3 tem o valor relativo publicado na saída da calibração. Para FII e EUA
a linha absoluta (−0,02 pp e +0,03 pp) foi apurada com MAE não arredondado e não
fecha com os MAE arredondados desta tabela na segunda casa, então dividir um
pelo outro produziria um número com aparência de medida e precisão de palpite.
Fica ``≈ 0`` até a calibração ser reexecutada — e ``≈ 0`` é a leitura correta
nos dois casos de qualquer forma.

## 5. Portões de promoção

| portão | B3 | FII | EUA |
|---|---|---|---|
| `alarmes_excessivos` | passou | passou | passou |
| `deteccao_util` | **reprovou** | **reprovou** | **reprovou** |
| `probabilidade_calibrada` | passou | passou | passou |
| `turnover` | não medido | não medido | não medido |
| `risco` | não medido | não medido | não medido |
| `disponivel_em_tempo_real` | passou | passou | passou |
| `estabilidade` | passou | **reprovou** | passou |

`pode_promover` = **False** nos três. `não medido` bloqueia sem reprovar:
reprovar manda ajustar o modelo, não medir manda arrumar a medição. Turnover e
risco pedem simulação de política de carteira, que não foi feita.

**Conclusão operacional: nenhum conjunto de pesos é promovível. `PRIOR` segue
ativo, com `calibrado=False` e a limitação declarada no próprio objeto.**
Nada foi ligado em produção por esta rodada.

## 6. O que a instrução pede e não foi medido — nomeado, não omitido

| pedido | situação |
|---|---|
| 15 tipos de evento | 3 (12%). Sem corpus histórico de notícias. |
| segmentação por setor | não medida: os eventos do catálogo não trazem setor point-in-time |
| segmentação por tamanho | não medida: não há capitalização histórica no armazém |
| segmentação por regime de mercado | não medida: fica para o Motor de Eventos Extremos |
| turnover, custos, impostos, spread, liquidez | não medidos: exigem simular a política de carteira |
| drawdown e tempo de recuperação | o código mede (`metricas.avaliar_politica`), a rodada não tem política para medir |
| pesos de materialidade/confiabilidade/novidade/… | testados por limite (`Conjunto.validar`), não ajustados por otimização — ajustar peso contra uma amostra que reprova em detecção seria ajustar ao ruído |
| fechamento de janela | aproximado por dias corridos (1,55/pregão), arredondado para cima |
| dentro x fora do pregão | não separado: as fontes trazem data, não hora |

## 7. Versionamento e volta atrás

`core/calibracao/pesos.py` guarda `Conjunto` versionado com `origem`,
`calibrado`, `limitacoes` e as duas versões de metodologia carimbadas
(`calibracao_versao` + `taxonomia_versao`). `Registro.promover` só ativa com
todos os portões em `True`; `Registro.reverter` volta ao anterior e o piso é
sempre o `PRIOR`, que existe sempre e nasce declarado como não calibrado.

Subir `*_VERSION` sem refazer a medição desliga a comparação em silêncio —
`memoria: versao-de-metodologia-sem-safra`.
