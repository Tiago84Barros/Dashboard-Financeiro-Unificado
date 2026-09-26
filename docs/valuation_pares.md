# Valuation e comparação com pares

Estas são duas camadas separadas da **Inteligência dos Ativos**, os cartões 6 e 7 da análise.

- `core/inteligencia_ativos/valuation.py`: catálogo por classe, estatística do histórico, faixas, frases e o texto para a LLM. É puro.
- `core/inteligencia_ativos/pares.py`: seleção do grupo de pares e a tabela de comparação. É puro.
- `core/inteligencia_ativos/fontes_valuation.py`: lê as fontes e monta os candidatos e as entradas por classe. É a camada de I/O.
- `scripts/publish_valuation_historico.py`: gera `data/public/valuation_historico.json.gz` a partir do armazém local.

## Regras

1. **Nunca "barato" ou "caro".** Cada comparação diz a posição e a referência, por exemplo "O P/VP atual (1,80x) está abaixo da média histórica dos últimos 10 anos (2,10x; …) e no percentil 20 do próprio histórico." Toda seção termina com o aviso de que diferença de múltiplo pode refletir risco, crescimento, ciclo ou juros. Os testes rejeitam as palavras barato e caro nas saídas.
2. **DADO / INTERPRETAÇÃO.** A tela mostra primeiro a tabela de dados (atual, média e mediana do histórico, mediana dos pares, referência), depois as faixas e premissas, e só então as frases de comparação. No texto para a LLM, o bloco `[DADO]` traz os números e o bloco `[INTERPRETAÇÃO]` traz as perguntas próprias da classe.
3. **Métricas por classe:**

   | Classe | Métricas |
   |---|---|
   | Ação | P/L, P/VP, Dividend yield, EV/EBIT |
   | FII | P/VP, DY 12 meses, Cap rate implícito |
   | Tesouro Direto | Taxa de mercado (venda) |
   | ETF, renda fixa fora do Tesouro | nenhuma (motivo explícito) |

   Uma métrica que não se aplica sai como "Não se aplica: …" com o motivo:
   - lucro ≤ 0 (P/L);
   - patrimônio ≤ 0;
   - banco ou seguradora (EV/EBIT);
   - FII que não é de tijolo (cap rate).
4. **Histórico mínimo:**

   | Frequência | Janela | Mínimo para comparar |
   |---|---|---|
   | Anual | 10 observações | 5 |
   | Mensal | 60 observações | 12 |
   | Diária | 756 pregões | 60 |

   Abaixo do mínimo, a frase diz "Histórico insuficiente" e informa a contagem.
5. **"Em linha"** significa uma diferença de até 5% da referência. Na taxa do Tesouro o limite é 1%, porque 5% de 7,4% são 0,37 p.p.
6. **Faixas de referência** são o interquartil e o mínimo–máximo do histórico, mais o interquartil dos pares quando há pelo menos 3. São intervalos observados, não preço-alvo.

## Pares

A seleção (`pares.selecionar`) é determinística:

1. **Mesma classe e mesmo mercado:** B3, EUA ou Tesouro Direto.
2. **Uma classe por empresa**, pela raiz (4 letras na B3, CIK nos EUA). O próprio ativo e as outras classes dele ficam de fora.
3. **Dado contemporâneo.** Fica fora o par cujo exercício é anterior ao ano do ativo menos 1. Sem essa regra, a Fitbit, com balanço de 2019, entrava no grupo da Apple de 2025.
4. **Modelo de negócio.** A taxonomia vai do nível mais estreito para o mais largo:

   | Mercado | Taxonomia |
   |---|---|
   | B3 | segmento → subsetor → setor |
   | FII | tipo + segmento declarado → tipo + mandato → tipo |
   | EUA | SIC de 4 → 3 → 2 dígitos |
   | Tesouro | papel → família |

   O grupo sobe de nível só quando faltam 3 pares, e cada subida fica registrada.
5. **Tamanho e risco.** O porte fica entre 1/10x e 10x o do ativo. O risco fica entre 1/2x e 2x, medido assim:
   - volatilidade anualizada de 36 meses em ações;
   - maior queda em FIIs;
   - prazo no Tesouro.

   As bandas afrouxam nesta ordem: porte + risco → só porte → só risco → nenhuma. Cada afrouxamento vira linha em "Como o grupo foi escolhido".
6. **Ordem.** Os pares vêm pela menor distância log de porte e risco, com o ticker como desempate, até 10 pares. Com menos de 3 pares mesmo no nível mais largo, não há grupo. A seção diz isso em vez de comparar com outro negócio.

A tabela segue o formato **Ativo | Métrica | Valor | Mediana dos pares (n) | Diferença | Interpretação**. A interpretação diz a posição e o que a métrica mede. Ela nunca conclui que o indicador melhor é o investimento melhor.

`Secao.dados` guarda o `como_dict()` das duas camadas. É o insumo estruturado para o futuro Portfolio Fit.

## Fontes

| Mercado | Valor atual | Histórico |
|---|---|---|
| B3 | `market_read.load_multiplos_todos` (Supabase) | arquivo publicado: P/L = fechamento de fim de ano ÷ LPA; P/VP = valor de mercado ÷ PL; DY = proventos com data-ex no ano ÷ fechamento |
| FII | vitrine de FIIs (`load_fii_methodology_inputs`) | arquivo publicado, mensal |
| EUA | arquivo publicado (retrato SEC + preço) | arquivo publicado, anual por exercício fiscal |
| Tesouro | `tesouro_curva.taxas_mais_recentes` | `tesouro_curva.serie_titulo` (3 anos) |

O arquivo é necessário porque o histórico vem de tabelas que só existem no armazém local (fita da B3, `market_us.prices_monthly`), e o Supabase está acima do limite do plano free. A rotina noturna (`scripts/atualizar_vitrines.py`, alvo `valuation_historico`) o republica semanalmente e commita o artefato.

O publicador tem estas guardas:

- **B3.** Um P/L que foge 20x da mediana do próprio ticker é descartado, porque é LPA de outra escala. O valor de mercado implícito (lucro ÷ LPA × preço) passa pela mesma guarda.
- **EUA.** Os meses em que o valor de mercado não acompanha o preço são descartados. Isso acontece porque as ações do ponto-no-tempo chegam atrasadas depois de um desdobramento. Também sai todo o trecho anterior ao último degrau: a AAPL até set/2020 aparecia com 1/4 do valor.

## Limitações conhecidas

- **Bancos na B3** não têm lucro absoluto na base local. Ficam sem P/VP histórico e sem porte, e a seleção de pares roda só por risco.
- **Segmento de FII.** O segmento declarado no cadastro pode divergir do portfólio: o HGLG11, que é logístico, aparece como Varejo. "Multicategoria" e "Outros" não contam como segmento. Isso está nas premissas.
- **EUA.** Cerca de mil símbolos têm último exercício ≤ 2023 (empresas que saíram da bolsa). O filtro de dado contemporâneo os tira dos grupos.
- **Tesouro.** O Supabase guarda poucas semanas de taxa diária por título. Até a série crescer, a comparação histórica sai como "Histórico insuficiente".
- **BDR** em BRL segue o caminho da B3 e normalmente cai em "fora do universo".
