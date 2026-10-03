# Atualizações 2026-10 — Inteligência dos Ativos: notícias de cenário e histórico fora da tela

- **PRs:**
  - #454 (merge squash em 2026-10-03, commit `2efe60c`);
  - #456 (merge squash em 2026-10-03, commit `77c1ff0`).
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

## 3. Limitações aceitas

- [Provável] A janela de 150 itens em 3 dias pode ter pouco assunto de cenário, e
  a caixa então mostra "Nenhuma manchete…". Se isso for comum, o ajuste é ampliar
  a janela em `itens_gerais()`.
- [Provável] A classificação por palavra-chave ainda deixa passar notícia política
  fraca (por exemplo, TSE no Rio).
- [Certo] Sem a trilha na tela, rever por que uma análise mudou exige ler o banco.

## 4. Arquivos

- `core/contexto_mercado.py`: `itens_gerais`, `_normalizar_item`.
- `core/inteligencia_ativos/resumida.py`: `noticias_cenario`, `TIPOS_POR_CANAL`,
  `ItemCenario`.
- `views/inteligencia_ativos_resumida.py`: `cartao_noticias`, `_manchete_cenario`.
- `core/noticias/vitrine.py`: `MANCHETES_MACRO`, `TIPOS_CENARIO`.
- `views/inteligencia_ativos.py` e `views/inteligencia_ativos_painel.py`:
  histórico fora da tela, `registrar_leitura_llm`.

## 5. Verificação

- PR #454: 6874 passed, 115 skipped; CI verde (Python 3.11 e 3.12).
  **Não verificado contra o acervo real.**
- PR #456: 6881 passed, 115 skipped; CI verde (Python 3.11 e 3.12).
- `ruff check .` limpo nos dois. Merges feitos sem revisão humana.

## Relacionadas

- [Inteligência dos Ativos — painel](inteligencia_ativos_dashboard.md)
- [Referência do Portfólio Global](inteligencia_referencia_portfolio_global.md)
- Nota equivalente a colar no vault Obsidian, em `04_App_Dashboard_Financeiro_Unificado/`.
