# frontend-design

> Padrões visuais e de UX para a interface Streamlit do app4.

## Objetivo

Manter coerência visual ao adicionar novas seções em `views/`. O app usa tema
escuro próprio definido em `design/tema.py` e componentes reutilizáveis em
`design/componentes.py`.

## Quando usar

- Toda vez que criar ou alterar `views/<pagina>.py`.
- Antes de escrever HTML/CSS inline novo: verificar se já existe componente
  pronto.

## Limites

- Não trocar a paleta escura por outra (cores em `design/tema.py`).
- Não usar emojis pesados em texto técnico que vá para arquivos não-UI.
- Não exibir texto técnico bruto (traceback, query, URL) para o usuário final.

## Padrão dos cards

- Cards usam grid CSS já presente em `views/configuracoes.py` (`.upd-card`,
  `.upd-card-grid`) e variações por tom (`ok`, `warn`, `info`, `neutral`).
- Cores de acento:
  - sucesso: `#00D09C`
  - alerta: `#FFB020`
  - info: `#4DA3FF`
  - neutro: `#E5E7EB`
- Fundo dos cards: `linear-gradient(145deg, var(--bg), rgba(17,24,39,.72))`.

## Padrão das tabs

- Use `st.tabs([...])` com emoji + nome curto.
- Conteúdo dentro de `with tab_x:` chama uma função helper `_render_x()`.

## Padrão de upload + resultado

Para uploads de arquivo:

1. `st.file_uploader(...)` com `type=[...]` restrito.
2. Botão `st.button(...)` separado para "Importar".
3. Resultado em quatro blocos visuais sempre presentes:
   - Cards com contadores: importados / duplicados / ignorados / erros.
   - Linha pequena com a data/hora da execução.
   - `st.expander("Erros e linhas ignoradas")` colapsado por padrão.
   - Aviso de segurança visível e fixo, sempre antes do uploader:
     *"A importação usa apenas arquivos exportados. O app não solicita senha
     da B3, XP ou banco."*

## Padrão de mensagens

- Sucesso: `st.success("✅ N operações importadas")` — frase curta e ativa.
- Aviso: `st.warning("…")` — explica próximo passo do usuário.
- Erro de usuário: `st.error("…")` — termo simples ("arquivo vazio",
  "formato inesperado").
- Erro técnico: dentro do expander, em `st.code(...)`.

## Checklist de implementação

- [ ] Importou `container_pagina` de `design.componentes` (quando aplicável).
- [ ] Usou os mesmos nomes de classes CSS já existentes — não criou
      `.import-card-X` quando `.upd-card` resolve.
- [ ] Botões primários só na ação destrutiva/principal; secundários no
      restante.
- [ ] Nada de senha, URL de banco ou OWNER_USER_ID visível na tela.
- [ ] Seção de importação de **investimentos** fica visualmente separada das
      importações financeiras existentes (subtítulo + divider, não misturada
      dentro do mesmo expander).

## Critérios de aceite

- Estilo idêntico ao restante do app — mesma altura de card, mesma família de
  cores, mesmo padding.
- Mobile: layout colapsa para uma coluna (já coberto pelos
  `@media (max-width: 900px)` existentes — não criar novo media query).
- Acessibilidade básica: contraste preservado; `aria-label` quando o botão
  for apenas ícone.

## Cuidados para não quebrar o app4

- HTML inline com `unsafe_allow_html=True` apenas para componentes visuais
  reutilizados — nunca para dados do usuário (XSS).
- Quaisquer dados do usuário renderizados em HTML inline devem passar por
  `html.escape()`.
- Não trocar o nome das `st.tabs(...)` existentes — outros pontos do app
  podem referenciar via deep link.

## Documentação de testes

- Smoke test manual:
  1. `streamlit run app.py`
  2. Navegar até **Configurações → Banco & Importação**.
  3. Conferir que a nova seção 📈 Importar dados de investimentos aparece
     separada dos uploads CSV/Excel existentes.
  4. Confirmar que aviso de segurança aparece **antes** dos uploaders.
  5. Confirmar que o tema escuro é preservado (sem fundo branco).
