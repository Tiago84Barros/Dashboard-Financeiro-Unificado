# Dossiê por classe na aba Análise

## Problema

A aba **Investimentos → Análise** tem uma sub-aba por classe (Ações, FIIs,
Tesouro, Exterior) e, no fim de cada uma, um chat livre. O chat responde bem
quando o usuário sabe o que perguntar. Ele não produz o que a tela Empresas B3
produz: um texto estruturado, sempre com as mesmas seções, que atravessa
concentração, pontos fortes, pontos de atenção, substituições, rebalanceamento,
evidência documental, tabela de pesos e limitações.

O usuário quer aquele padrão em todas as sub-abas, adaptado à especificidade de
cada classe.

## O que se decidiu, e por quê

Seis decisões foram tomadas com o usuário antes de escrever código. Duas delas
revogam restrições deliberadas que já existiam — ficam registradas aqui porque
quem ler o código depois vai encontrar a restrição na história do git e precisa
saber que a remoção foi escolha, não descuido.

1. **Botão, não automático.** Cada sub-aba ganha `📑 Gerar dossiê da classe`. O
   chat livre continua existindo. Custo de LLM só ao clicar.
2. **Recomendação é permitida — no dossiê e no chat.** A regra 7 de
   `core/llm_carteira.py` proibia recomendação personalizada de compra, venda ou
   alocação e preço-alvo. Ela cai na aba Análise inteira. No lugar entra uma
   regra que *prende* a recomendação: ela tem que citar o que no contexto a
   sustenta, dizer o que falta de dado e declarar que a decisão é do usuário.
   Recomendação sem lastro declarado é pior que recomendação nenhuma.
3. **Reais são opt-in por classe.** Checkbox desmarcado por padrão. Marcado,
   vai o valor de mercado **por posição** — nunca o patrimônio total, nunca as
   outras classes. Desmarcado, o contexto segue só com percentuais, como hoje.
4. **Candidatos de fora vêm do universo do banco**, entre pares do mesmo
   setor (ações, exterior) ou do mesmo tipo (FIIs) — não da carteira modelo
   publicada. O universo já é pontuado por inteiro em
   `core/portfolio_db_analysis.py` e os não-carregados são descartados; buscar
   os pares ali custa zero consulta a mais.
5. **Tesouro tem estrutura própria.** Não é "seção não se aplica": indexador,
   prazo contra objetivo, juro real contra taxa carregada, efeito de vender
   antes do vencimento e a escolha do próximo aporte entre Selic, IPCA+ e
   Prefixado.
6. **Evidência documental onde houver corpus.** Ações via `core/rag_b3.py`
   (CVM/IPE); FIIs e Exterior via `core/noticias/vitrine.ler`. Tesouro não tem
   corpus e a seção declara isso em vez de sumir — o dicionário devolvido traz
   `nota`, e `_bloco_documentos` prefere a `nota` à frase "nada na janela",
   porque falar em janela para uma classe sem corpus sugere uma coleta que não
   existe.

## Arquitetura

A aba já separa interface, lógica e dado. O dossiê entra respeitando a mesma
divisão, sem módulo novo na camada de tela.

```
views/investimentos.py::_bloco_analise_classe
        │ passa posições, valuations, db, tesouro, macro, fundamentos
        ▼
design/chat_carteira.py::render_chat_carteira
        │ botão + checkbox de reais; decide QUANDO gastar LLM
        ▼
core/llm_context_carteira.py::build_carteira_classe_context
        │ contexto determinístico (+ pares, + reais opt-in, + documentos)
        ▼
core/llm_dossie_carteira.py::gerar_dossie_classe
        │ seções por classe → prompt → provedor
```

Três módulos de `core/` mudam e um nasce:

| Módulo | Mudança |
|---|---|
| `core/llm_dossie_carteira.py` | **novo.** Seções por classe e a chamada ao provedor. |
| `core/llm_carteira.py` | regra 7 substituída (decisão 2). |
| `core/llm_context_carteira.py` | parâmetros `valores_reais`, `pares`, `documentos`. |
| `core/portfolio_db_analysis.py` | cada `analise_*_db` passa a devolver `pares`. |
| `core/carteira_documentos.py` | **novo.** CVM/IPE e notícias por classe, com erro declarado. |

### Seções

Doze seções, com o título fixo por classe e a instrução adaptada. O esqueleto
é o mesmo em todas as classes para que o usuário reconheça o formato entre
sub-abas; o que muda é o conteúdo de cada instrução.

O contraste que importa está na 1, na 5 e na 7:

- **Concentração** é setorial em Ações e Exterior, por tipo/segmento em FIIs,
  por indexador e vencimento em Tesouro.
- **Substituições** compara com pares do mesmo setor em Ações/Exterior, do
  mesmo tipo em FIIs, e em Tesouro vira escolha de indexador — não existe "par"
  de um título público, o emissor é um só.
- **Rebalanceamento** carrega tributação, e as quatro classes têm regimes
  diferentes.

### Tributação: premissa, não fato

O exemplo que originou o pedido trazia "isenção de R$ 20.000,00". Esse número
não está em lugar nenhum do contexto — é conhecimento externo do modelo, e
escrevê-lo no prompt o congelaria numa constante que envelhece sem parecer
errada.

A instrução da seção 7 exige que o modelo **nomeie o regime que está
aplicando e o marque como premissa externa ao dado**, não como algo lido do
contexto. É a mesma disciplina que o projeto já aplica a default de widget e a
texto de metodologia.

### Privacidade

`_bloco_valores` só entra quando o checkbox está marcado, e lista valor de
mercado por ticker **daquela classe**. O total da classe é derivável da soma —
isso é aceito e está dito no texto do contexto. O que não sai em nenhuma
hipótese é o patrimônio consolidado nem as posições das outras classes.

## Defeito colateral corrigido

`views/investimentos.py` monta a lista `acoes` filtrando pela string da classe
(`"ação"`, `"ações"`, `"acoes"`), enquanto o Exterior é separado por país e
moeda. Uma ação estrangeira cuja classe contenha "Ações" cai nas **duas**
sub-abas. Para o dossiê isso não é cosmético: a concentração setorial sairia
sobre um universo misturado e os pares viriam da B3 para um papel americano.

A lista passa a excluir posição de exterior.

## Testes

- Seções: cada classe declara as 12, com títulos únicos, e Tesouro difere de
  Ações nas três seções que devem diferir.
- Contexto: sem o toggle, nenhum valor absoluto aparece; com o toggle,
  aparece por posição; o patrimônio consolidado nunca aparece.
- Pares: excluem o que já está na carteira e respeitam o grupo (setor/tipo).
- Documentos: falha de leitura vira erro declarado, não lista vazia — quadro
  vazio já foi confundido com "nada a relatar" neste projeto.
- Regra 7: teste que prende a ausência da proibição e a presença da regra que
  a substitui, para que uma reintrodução silenciosa apareça.

## O que ficou construído

| Arquivo | Papel |
|---|---|
| `core/llm_dossie_carteira.py` | 12 seções por classe, `prompt_do_dossie`, `gerar_dossie_classe`. |
| `core/carteira_documentos.py` | `documentos_da_classe`; teto de 8 ativos e 1.800 caracteres por ativo. |
| `core/llm_carteira.py` | `escopo_da_classe` e `regras_da_analise`, lidas pelo chat E pelo dossiê. |
| `core/llm_context_carteira.py` | `valores_reais`, `documentos`, `_bloco_pares`. |
| `core/portfolio_db_analysis.py` | `_pares_do_universo` nas três `analise_*_db`. |
| `design/portfolio_db_analysis.py` | `carregar_documentos`, cacheada por 30 min. |
| `design/chat_carteira.py` | botão do dossiê, checkbox de reais, texto do card conforme o toggle. |
| `views/investimentos.py` | `_contexto(pergunta, valores_reais=)`; filtro de `acoes` exclui exterior. |
| `tests/test_dossie_carteira.py` | 31 testes. |

### Sobra do PR anterior, fechada junto

A guarda do `escapar_cifrao` só conhecia os nomes `resposta`, `pergunta` e
`content`. Alargada para `answer`, `user_input` e `texto`, ela encontrou três
`st.markdown` de texto de chat ainda nus — um em `views/fiis.py`, um em
`views/analise_portfolio_b3.py` e dois em `views/controle_financeiro.py`. Todos
embrulhados. É a diferença entre uma guarda que passa e uma guarda que cobre:
a lista de nomes era o teto do que ela conseguia ver.
