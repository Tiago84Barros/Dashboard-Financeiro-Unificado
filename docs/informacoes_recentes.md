# Informações recentes dos ativos

A **Inteligência dos Ativos** tem três cartões com informação recente: 9 · Notícias, 10 · Relatórios e 11 · Próximos eventos.

- `core/inteligencia_ativos/informacoes.py`: filtro e classificador de manchetes, estruturas, prazo regulatório de resultado e o texto para a LLM. É puro.
- `core/inteligencia_ativos/fontes_informacoes.py`: lê o arquivo publicado e os proventos futuros, e monta os eventos por classe. É a camada de I/O.
- `scripts/publish_informacoes_recentes.py`: gera `data/public/informacoes_recentes.json.gz` a partir do armazém local. Grava por padrão; `--dry-run` só mede. Roda todo dia pelo alvo `informacoes_recentes` da agenda de publicação.

O Supabase passou dos 500 MB, e o acervo de notícias e os documentos da CVM e do FNET só existem no armazém local. Por isso o dado chega ao app publicado como arquivo, não como tabela nova.

## Regra de ouro

Nada é inventado. Sem fonte, a seção diz "Dado não disponível." e explica o motivo: sem arquivo, classe sem cobertura, nenhuma notícia passou no filtro ou base desatualizada. Todo dado externo carrega `source`, `source_url`, `retrieved_at` e `reference_date`.

## Notícias

Janela de 60 dias do acervo. No máximo 6 por ativo, as de impacto mais alto primeiro e exibidas da mais recente para a mais antiga.

A notícia só entra se o ativo é o **assunto da manchete**. Os descartes são contados por motivo e aparecem na tela:

| Descarte | Exemplo |
|---|---|
| O ativo não é o assunto | "Morgan Stanley upgrades Nvidia" não conta para MS; "price target" não conta para TGT |
| Posição de terceiro (13F, Form 4, participação) | "NVIDIA shares acquired by Acme Capital" |
| Manchete de mercado | "Ibovespa hoje", "stocks to buy", aviso de escritório de ação coletiva |
| Resumo com 3 ou mais empresas | "Vale, Petrobras e Itaú: lucro do trimestre" |
| Sem fato material | nenhuma categoria do léxico casou |
| Duplicada | a mesma manchete em outra fonte, com ou sem "(VALE3)" |

Ticker americano é casado respeitando a caixa, então "Post" não conta para POST. Ticker de 1–2 letras, ou manchete toda em maiúsculas, exige a forma marcada: `(X)`, `$X` ou `NYSE: X`.

**Impacto.** Sai de categorias por regra de palavra-chave sobre a manchete, não da leitura da matéria. O `tipo_evento` do acervo não é usado: ele marca a maior parte das manchetes como resultado.

| Categoria | Nível | Dimensões |
|---|---|---|
| Recuperação judicial, fraude, fusão/aquisição/venda de ativo | Alto | risco, dívida, governança, estratégia, valuation |
| Dívida e crédito, resultado, guidance, emissão/recompra, troca de gestão, litígio | Médio | dívida, fundamentos, receita, estratégia, governança, risco |
| Dividendos, operação, recomendação de analista | Baixo | dividendos, operação, valuation |

Ajustes:

- sobe um grau: rebaixamento por agência de rating, calote, vencimento antecipado, corte ou suspensão de dividendo;
- desce um grau:
  - manchete em forma de pergunta ou aposta ("Could Apple acquire…?");
  - manchete que divide o assunto com outra empresa;
- recomendação de analista manda sobre o resto (é opinião, não fato), salvo quando há fato de nível alto.

## Relatórios

A janela é de 180 dias, com até 8 documentos por ativo.

| Emissor | Fonte |
|---|---|
| Companhias da B3 | CVM/IPE (`docs_corporativos`) |
| FIIs | B3/FNET (`fii_documents`) |

Os documentos são tipados como balanço, release, apresentação, relatório gerencial, fato relevante, comunicado, guidance, rating, assembleia ou oferta. Não há base de filings da SEC no projeto, então ações americanas mostram "Dado não disponível.".

**Só metadados.** O conteúdo não é lido aqui. Para as sete perguntas (o que mudou, melhorou, piorou, novos riscos, oportunidades, estratégia, próximos movimentos), os documentos cujo **título** aponta para a pergunta seguem como indício no texto enviado à LLM. Melhorou, piorou e oportunidades exigem leitura e ficam como "Dado não disponível.". A tabela "Onde procurar" saiu da tela do investidor (2026-10); ela só lista a tabela de documentos.

Os achados extraídos de documento de FII (`fii_document_findings`) ficam fora. Em 26/09/2026 nenhum dos 751 estava validado, e os lidos eram boilerplate de regulamento ou o contrário do rótulo.

## Próximos eventos

Tabela Evento | Data | Relevância | Possível impacto, com a natureza da data e a fonte.

| Tipo | Fonte | Natureza |
|---|---|---|
| Provento (ação e FII) | `market.dividends` (Supabase, só leitura, safra canônica) | anunciado |
| Divulgação de resultado (ação da B3, não BDR) | prazo da Resolução CVM 80/2022: ITR até 45 dias após o trimestre, DFP até 3 meses após o exercício | prazo regulatório, **não** data anunciada |
| Vencimento (Tesouro Direto) | título da posição | contratual |

Não há fonte de data no projeto para guidance, assembleia, emissão de cotas, aquisição, venda de ativo, fim de contrato e evento regulatório. Esses tipos aparecem em "Sem fonte de data para: …", nunca estimados.

## Limitações conhecidas (26/09/2026)

- A última atualização de `docs_corporativos` é de 04/09/2026. ~~O texto dos documentos está vazio~~: desatualizado. Desde 03/10/2026 o texto vem do corpus RAG em Parquet (`data/public/rag/`, lido por `core/rag_store.py`), e a etapa 10 mostra trechos literais por tema (ver abaixo).
- Os documentos de FII no FNET param em 15/07/2026.
- O resolvedor de nomes não acha toda empresa citada. Alguns resumos com várias empresas passam como notícia de uma delas.

## Etapa 10 · o que os documentos dizem (03/10/2026)

A tabela "Dado · documentos publicados" saiu da tela. Pedido do usuário: o app
deve mostrar o que interessa nos relatórios, não a lista deles.

- `destaques_relatorios.ler_trechos(ticker)` lê até 12 documentos de fato dos
  últimos 12 meses no corpus RAG e escolhe até 4 frases com fato e número por
  documento. É a mesma regra da caixa "Relatórios relevantes" da resumida.
- `por_tema` reagrupa as frases em Resultado, Proventos e recompra, Caixa e
  dívida, Projeções e estratégia, Operação e crescimento e Outros fatos, com até
  3 por tema. O mesmo fato publicado em dois documentos aparece uma vez: a
  chave são os valores numéricos da frase.
- `provedor_relatorios` grava os trechos em `Relatorios.trechos`. A tela
  (`corpo_relatorios`) e o texto da LLM (`texto_relatorios`) leem dali. Antes a
  LLM recebia só metadados, embora o prompt falasse em "trechos recuperados".
- A frase é sempre do emissor, nunca paráfrase. O tema sai de palavra-chave,
  então uma frase pode cair no tema errado.
- Sem texto no acervo (FII, ou documento só com metadado), a tela diz isso em
  uma linha.
