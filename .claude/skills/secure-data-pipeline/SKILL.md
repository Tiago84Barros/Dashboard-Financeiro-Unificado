# secure-data-pipeline

> Regras de segurança para qualquer ingestão de dados financeiros no app4.

## Objetivo

Garantir que ingestão de dados pessoais e operacionais respeite o princípio do
mínimo privilégio, sem scraping autenticado, sem captura de senha e sem
vazamento de informação sensível.

## Quando usar

- Antes de implementar qualquer importador, conector, agendador ou job que
  toque dados pessoais (operações, proventos, posições, transações, contas).
- Antes de adicionar logs em qualquer parte do pipeline.

## Limites — proibições explícitas

- **Proibido** scraping autenticado da Área do Investidor B3, XP, Nomad,
  internet banking ou painel de corretora.
- **Proibido** automatizar login com credenciais reais do usuário.
- **Proibido** pedir senha pessoal na UI ou em prompt.
- **Proibido** armazenar senha pessoal, mesmo "criptografada".
- **Proibido** imprimir conteúdo integral de arquivo, mesmo em DEBUG.
- **Proibido** expor `DATABASE_URL`, `SUPABASE_*`, `OWNER_USER_ID` ou tokens
  na UI ou em mensagens de erro.
- **Proibido** persistir cópia bruta de arquivo do usuário em disco. Trabalhar
  em memória (`io.BytesIO`).

## Caminho autorizado

O único modo autorizado de ingestão é:

1. Usuário exporta o arquivo no próprio portal (B3, XP, Nomad).
2. Usuário envia o arquivo via uploader Streamlit.
3. Parser processa o arquivo em memória.
4. Importador grava no banco unificado via `engine.begin()` com idempotência.

## Logs

Em `data_update_logs` e `print/logger`, gravar apenas:

- nome da fonte (`b3_negociacao`, `xp_consolidado`, …);
- status (`success`, `partial_success`, `failed`, `skipped`);
- contadores (registros importados, duplicados, ignorados, com erro);
- mensagem de erro **resumida** (até 500 caracteres), sem connection string
  e sem trecho cru do arquivo;
- timestamps.

Pipeline já tem `_sanitize(msg)` em
`data_pipeline/utils/logging_utils.py`. Reaproveitar quando aplicável.

## Checklist de implementação

- [ ] Não há `getpass`, `input("senha")`, `st.text_input("senha")` no parser.
- [ ] Não há `requests.post(login_url, ...)` ou similar.
- [ ] Não há `open(<arquivo do usuário>, "wb")` salvando o upload.
- [ ] Mensagem de erro não contém: connection string, JWT, CPF, número de
      cartão, número de conta completo.
- [ ] Logs passam por `_sanitize()` ou função equivalente.
- [ ] Funções que recebem credenciais (raras) recebem por
      `core.config.settings`, nunca como argumento explícito da view.

## Critérios de aceite

- Procura por padrões proibidos não encontra ocorrência:
  - `re.search(r"senha\\s*=\\s*['\"]", source)` → 0 matches.
  - `re.search(r"requests\\.(get|post)\\(['\"]https?://(www\\.)?investidor\\.b3\\.com\\.br", source)` → 0 matches.
- Aviso de segurança visível na UI de importação:
  *"A importação usa apenas arquivos exportados. O app não solicita senha da
  B3, XP, Nomad ou banco."*

## Cuidados para não quebrar o app4

- Não desabilitar logs existentes em nome de "menos exposição". Sanitizar é
  melhor que silenciar.
- Não trocar `pool_pre_ping=True` em `core/database.py` por nenhuma
  configuração que desabilite SSL.

## Documentação de testes

- Teste manual: subir um arquivo .xlsx adulterado com URL bizarra em uma
  célula. Confirmar que logs não trazem essa URL para fora.
- Teste de regressão de logs: depois da importação, conferir
  `data_update_logs.error_message` no banco — não pode conter `postgresql://`
  ou similar.
