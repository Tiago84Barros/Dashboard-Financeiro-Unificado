# Atualizações 2026-10 — Inteligência dos Ativos: notícias de cenário, tela mais enxuta e relatórios lidos

- **PRs:**
  - #454 (merge squash em 2026-10-03, commit `2efe60c`);
  - #456 (merge squash em 2026-10-03, commit `77c1ff0`);
  - #458 (merge squash em 2026-10-03, commit `8b2f649`);
  - #460 (merge squash em 2026-10-03, commit `dade9b9`).
- **Seção:** Investimentos → Inteligência dos Ativos

## 1. Notícias: o complemento passa a ser o cenário do país (PR #454)

**Problema.** Na página resumida, quando o ativo não tinha notícia própria
(DIRR3, BITH11, GMAT3), a caixa "Notícias" completava com o "Noticiário geral
do mercado". Eram as manchetes mais relevantes do acervo, de qualquer tema, e na
prática apareciam notícias de outras empresas (Sable Offshore) ou temas sem
relação (tecnologia para clima, TSE no Rio).

**O que mudou.**
- A seção se chama **"Cenário econômico e político"** e só aceita fato de
  cenário: juros, inflação, câmbio, fiscal/política, atividade/emprego,
  geopolítica, crise sistêmica e pandemia. Commodities e crédito entram quando
  pesam na classe do ativo.
- Matéria que cita ticker fica de fora. O complemento fala do país, não de outra
  empresa.
- Ordem das manchetes:
  1. tema que pesa na classe do ativo (os canais de
     `core/cenario/modelo.RELEVANCIA`, mapeados em `resumida.TIPOS_POR_CANAL`);
  2. país do ativo (BR; US para a classe exterior);
  3. nota de relevância.
- Cada manchete mostra "Tema · PAÍS · data · veículo". A nota da seção diz quais
  temas guiaram a escolha e de onde veio o dado.
- Sem nenhum fato de cenário no período, a caixa diz isso em vez de mostrar
  notícia de empresa.
- A seção "Como está o segmento" (notícias dos pares) foi mantida.

**De onde vêm os itens.** `core/contexto_mercado.itens_gerais()` devolve os itens
crus com `tipo_evento`. A fonte é tentada nesta ordem:
1. acervo local, com `ler_recentes(150, dias=3)`;
2. túnel (`armazem_remoto.noticias_recentes`);
3. vitrine do Supabase (`noticias_vitrine_meta.manchetes`).

A fonte que falha é nomeada. A vitrine com mais de 48 h vai marcada como VELHA.
Item da vitrine antiga, sem tipo, é classificado pelo título com
`core.noticias.eventos.classificar`.

**Vitrine.** `core/noticias/vitrine.manchetes_da_leitura` passa a publicar
`tipo_evento` e `com_ticker`. Também reserva até 20 vagas (`MANCHETES_MACRO`)
para fatos de cenário que ficaram fora das 40 manchetes mais relevantes. **Só vale
depois de rodar `scripts/publish_noticias_vitrine.py` localmente.**

**O que não mudou.** O bloco de mercado dos chats (`bloco_contexto_mercado`,
`manchetes_gerais`) é o mesmo. A premissa de que toda LLM recebe todo o dado
segue intacta.

## 2. Histórico e auditoria saem da tela (PR #456)

**Decisão.** A seção "Histórico e auditoria" da Análise detalhada foi retirada a
pedido: a tabela técnica (`analysis_timestamp`, `model_used`, `scenario_version`,
`investment_policy_version`, `sources_used`) não ajuda o investidor a decidir.

**O que saiu:**
- o título "Histórico e auditoria";
- o cartão "Histórico da análise · TICKER" com a tabela;
- o botão "Salvar esta análise no histórico".

**O que ficou:**
- as fotos continuam gravadas em `user_settings.extra_settings`: primeira
  análise, mudança material, leitura por LLM e 30 dias;
- a frase "Desde a última análise" da Visão geral da carteira continua usando o
  histórico;
- `render_historico` virou `registrar_leitura_llm`, que grava a foto da leitura
  por LLM sem exibir nada.

O toggle passou a se chamar "Análise detalhada (13 etapas e Portfolio Fit)".

## 3. "Onde procurar" e o JSON da LLM saem da tela (PR #458)

**Decisão.** A pedido, mais dois blocos técnicos saíram da Análise detalhada:
- **"Onde procurar · indício pelo título, não conclusão"** (etapa 10,
  Relatórios): a tabela Pergunta → Documentos, que na prática mostrava quase
  só "Dado não disponível.". Ficaram a tabela de documentos publicados e uma
  nota curta. **Ambas foram substituídas no PR #460 (seção 4).**
- **"Contexto estruturado que a LLM recebe"** (Portfolio Fit): o expander com o
  JSON (`ordem_de_analise`, `portfolio`, `scenario`, `news` …).

**O que não mudou.** A LLM recebe o mesmo de antes:
- os indícios por pergunta seguem no texto da análise
  (`core/inteligencia_ativos/informacoes.py`, `r.indicios()` + `PERGUNTAS`);
- o contexto do Portfolio Fit é montado igual (`pf.contexto`) e continua
  servindo de chave de sessão (`chave_sessao`);
- o bloco de mercado (`bloco_contexto_mercado`) segue anexado na hora da
  chamada. Ele nunca apareceu naquele JSON: `"news": []` ali significa só que o
  ativo não tinha notícia própria, não que a LLM ficou sem noticiário.

**Custo.** Sem o JSON na tela, auditar o que a LLM recebeu exige ler o código ou
gerar o contexto localmente (`pf.contexto(analise, ctx)`).

## 4. Etapa 10 passa a mostrar o que os relatórios dizem (PR #460)

**Problema.** A tabela "Documentos publicados" (data, tipo, título, link) era só
metadado e não ajudava o investidor. O pedido foi que o app lesse os relatórios
e mostrasse, de forma organizada, o que importa neles.

**O que mudou.**
- A tabela saiu. A etapa 10 agora mostra **frases literais** dos documentos
  oficiais (CVM), agrupadas por tema, nesta ordem:
  1. Resultado;
  2. Proventos e recompra;
  3. Caixa e dívida;
  4. Projeções e estratégia;
  5. Operação e crescimento;
  6. Outros fatos.
- Cada frase vem com "data · título curto do documento".
- A nota diz que as frases foram escolhidas por regra, sem LLM.
- Se o texto não está no acervo (FIIs, por enquanto), a etapa mostra só o resumo
  e o aviso "O texto destes documentos ainda não está no acervo…".

**Como funciona** (`core/inteligencia_ativos/destaques_relatorios.py`, tudo
determinístico):
- **Leitura.** `ler_trechos(ticker)` chama `ler(ticker, 12, 4)`: até 12
  documentos e 4 frases cada, tiradas do corpus RAG
  (`data/public/rag/chunks_*.parquet`, lido com DuckDB, sem embeddings). O corpus
  é publicado com o app e funciona na Streamlit Cloud.
- **Seleção.** `pontuar` escolhe as frases com prosa e números.
- **Tema.** `tema(frase)` soma pontos para cada termo de `TEMAS` encontrado na
  frase, e cada termo vale o seu número de palavras. Assim "custo de capital"
  vai para Projeções, não para Resultado. No empate vence o primeiro tema da
  lista; sem nenhum termo, a frase cai em "Outros fatos".
- **Agrupamento.** `por_tema` guarda até 3 frases por tema e não repete o mesmo
  fato. A chave do fato é o conjunto de números com separador decimal ou com 3+
  dígitos, quando a frase tem pelo menos dois; senão, os 80 primeiros caracteres
  normalizados.

**O que a LLM recebe.**
- Os trechos vão em `Relatorios.trechos`, persistido por `como_dict`/`de_dict`.
- `texto_relatorios` passa a enviar, nesta ordem:
  1. as linhas "Trechos · {tema}:";
  2. a lista de documentos publicados;
  3. os indícios por pergunta.
- A linha INTERPRETAÇÃO avisa que os trechos foram escolhidos por regra.
- Ou seja, a LLM recebe mais dado do que antes, e a lista de documentos continua
  no texto que ela lê.

**Testado com dados reais do corpus:** PETR4, TAEE11, WEGE3, ITUB4, BBAS3 e
MGLU3.

## 5. Limitações aceitas

- [Provável] A janela de 150 itens em 3 dias pode ter pouco assunto de cenário, e
  a caixa então mostra "Nenhuma manchete…". Se isso for comum, o ajuste é ampliar
  a janela em `itens_gerais()`.
- [Provável] A classificação por palavra-chave ainda deixa passar notícia política
  fraca (por exemplo, TSE no Rio).
- [Certo] Sem a trilha na tela, rever por que uma análise mudou exige ler o banco.
- [Certo] Sem o JSON na tela, conferir o que a LLM recebeu exige o código.
- [Certo] Etapa 10: o tema é decidido por palavra-chave e às vezes erra. Por
  exemplo, uma frase sobre dívida que cita "lucro" pode cair em Resultado.
- [Certo] Etapa 10: às vezes a regra escolhe uma frase de pouco valor.
- [Certo] Etapa 10: FIIs ainda não têm o texto dos documentos no corpus.
- [Certo] O resumo por IA dos trechos foi implementado em seguida, sob
  demanda (botão), em `core/inteligencia_ativos/leitura_relatorios.py`. Ver
  `docs/informacoes_recentes.md`, "Etapa 10 · resumo por IA". Não foi testado
  contra um provedor real nesta sessão.

## 6. Arquivos

- `core/contexto_mercado.py`: `itens_gerais`, `_normalizar_item`.
- `core/inteligencia_ativos/resumida.py`: `noticias_cenario`, `TIPOS_POR_CANAL`,
  `ItemCenario`.
- `views/inteligencia_ativos_resumida.py`: `cartao_noticias`, `_manchete_cenario`.
- `core/noticias/vitrine.py`: `MANCHETES_MACRO`, `TIPOS_CENARIO`.
- `views/inteligencia_ativos.py` e `views/inteligencia_ativos_painel.py`:
  histórico fora da tela, `registrar_leitura_llm`.
- `views/inteligencia_ativos.py::corpo_relatorios`: sem a tabela "Onde procurar".
- `views/inteligencia_ativos_fit.py::render`: sem o expander do JSON.
- `core/inteligencia_ativos/destaques_relatorios.py`: `TEMAS`, `ROTULO_TEMA`,
  `Trecho`, `tema`, `por_tema`, `titulo_curto`, `ler_trechos`.
- `core/inteligencia_ativos/informacoes.py`: `Relatorios.trechos`,
  `resumo_relatorios`, `texto_relatorios`.
- `core/inteligencia_ativos/secoes.py::provedor_relatorios`: lê os trechos.
- `views/inteligencia_ativos.py::corpo_relatorios`: trechos por tema, sem a
  tabela de documentos.
- `docs/informacoes_recentes.md` e `docs/portfolio_fit.md`: notas da remoção.
  `docs/informacoes_recentes.md` também ganhou a seção "Etapa 10 · o que os
  documentos dizem".

## 7. Verificação

- PR #454: 6874 passed, 115 skipped; CI verde (Python 3.11 e 3.12).
  **Não verificado contra o acervo real.**
- PR #456: 6881 passed, 115 skipped; CI verde (Python 3.11 e 3.12).
- PR #458: 6893 passed, 115 skipped; CI verde (Python 3.11 e 3.12).
- PR #460: 6898 passed, 115 skipped; CI verde.
- `ruff check .` limpo nos quatro. Merges feitos sem revisão humana.

## Relacionadas

- [Inteligência dos Ativos — painel](inteligencia_ativos_dashboard.md)
- [Referência do Portfólio Global](inteligencia_referencia_portfolio_global.md)
- Nota equivalente a colar no vault Obsidian, em `04_App_Dashboard_Financeiro_Unificado/`.
