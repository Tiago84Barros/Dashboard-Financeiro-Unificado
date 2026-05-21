# investment-imports

> Diretrizes para os parsers de importação de investimentos no app4.

## Objetivo

Importar dados de investimentos a partir de arquivos exportados pelo próprio
usuário, mantendo idempotência, mapeamento correto para o schema canônico do
app4 e compatibilidade com as visões já existentes.

## Quando usar

- Sempre que criar ou alterar um parser em
  `data_pipeline/importers/investments/`.
- Antes de mexer no fluxo "Importar dados de investimentos" da view
  `views/configuracoes.py`.

## Limites

- **Não fazer scraping** de B3, XP, Nomad, corretora ou banco.
- **Não automatizar login** em portal de investidor.
- **Não pedir, capturar ou armazenar senha pessoal**.
- Não copiar parser do Dashboard-Investimentos cegamente — adaptar ao schema
  do app4 (ver mapeamento abaixo).

## Fontes suportadas

| Origem               | Arquivo                                       | Aba/Formato          |
|----------------------|-----------------------------------------------|----------------------|
| B3 — Negociação      | `negociacao-*.xlsx`                           | aba "Negociação"     |
| B3 — Movimentação    | `movimentacao-*.xlsx`                         | aba "Movimentação"   |
| XP — Consolidado     | `Posição_*.xlsx`                              | múltiplas abas       |
| Nomad — Negociação   | PDFs de notas de corretagem                   | PDF                  |

## Mapeamento para o schema do app4

O app4 já tem o schema canônico carregado:

| Conceito                | Tabela no app4              | Colunas principais                                                              |
|-------------------------|-----------------------------|---------------------------------------------------------------------------------|
| Ativo                   | `assets`                    | `id, ticker, name, class, currency, country, sector`                            |
| Compra/venda            | `investment_transactions`   | `id, user_id, asset_id, type, quantity, unit_price, fees, transaction_date`     |
| Provento (div, JCP)     | `dividends`                 | `id, user_id, asset_id, type, amount_per_unit, quantity, total_amount, payment_date` |
| Snapshot de posição     | `portfolio_position_snapshots` | usado por XP/Nomad (não-transacional)                                        |
| Instituição financeira  | `financial_institutions`    | `id, name, type, active`                                                        |
| Conta                   | `accounts`                  | conta agregadora "B3 - Carteira Consolidada"                                    |

Convenções de mapeamento:

- B3 Negociação `Compra` → `investment_transactions.type = 'buy'`
- B3 Negociação `Venda` → `investment_transactions.type = 'sell'`
- B3 Movimentação `Dividendo` → `dividends.type = 'dividend'`
- B3 Movimentação `Juros sobre Capital Próprio` → `dividends.type = 'jcp'`
- B3 Movimentação `Rendimento` → `dividends.type = 'reit_income'`
- Classe do ativo a partir do sufixo do ticker:
  - termina em `11`, `11B` → `class = 'reit'` (FII)
  - termina em `3` ou `4` → `class = 'stock'`
  - prefixo TESOURO → `class = 'fixed_income'`

## Idempotência

Cada parser **deve** gerar um `external_id` determinístico (hash MD5 ou SHA1
dos campos identificadores). Antes de gravar:

1. Calcular `external_id`.
2. Tentar `SELECT 1 ... WHERE external_id = :ext`. Se existir → incrementar
   `duplicates_skipped`, pular.
3. Caso contrário, `INSERT ... RETURNING id` e registrar.

Se as tabelas `investment_transactions` / `dividends` ainda não tiverem coluna
`external_id`, o módulo `data_pipeline/importers/investments/common.py` cuida
de adicioná-la via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` em runtime, na
primeira execução.

Receita do `external_id`:

```python
raw = f"{source}|{data}|{tipo}|{ticker}|{qtd}|{preco}|{valor}"
ext_id = f"{prefix}-{hashlib.md5(raw.encode()).hexdigest()[:16]}"
```

`prefix` por fonte: `b3neg`, `b3mov`, `xpcsl`, `nomad`.

## Contrato de retorno (resumo padronizado)

Todos os parsers devolvem o mesmo `dict`:

```python
{
    "status": "success" | "partial_success" | "failed",
    "source": "b3_negociacao" | "b3_movimentacao" | "xp_consolidado" | "nomad_pdf",
    "records_imported": int,        # somatório de tudo gravado
    "transactions_imported": int,
    "incomes_imported": int,
    "positions_imported": int,
    "duplicates_skipped": int,
    "rows_skipped": int,
    "errors": list[str],
    "started_at": str (iso),
    "finished_at": str (iso),
}
```

## Checklist de implementação

- [ ] Recebe `file_bytes: bytes` e `engine: Engine` (não conexão aberta).
- [ ] Abre transação curta com `engine.begin()` para cada lote.
- [ ] Cria asset / institution / account `ON CONFLICT DO NOTHING` antes de
      inserir movimento.
- [ ] Calcula `external_id` antes do `INSERT`.
- [ ] Conta `transactions_imported`, `incomes_imported`, `duplicates_skipped`,
      `rows_skipped` e `errors`.
- [ ] Erros entram em `errors` com `"Linha N: <motivo curto>"`. Nunca expõe
      conteúdo bruto do arquivo (CPF, número de nota etc.).
- [ ] `OWNER_USER_ID` vem de `settings.OWNER_USER_ID`. Se ausente → retorna
      `status='failed'` com erro explicativo.
- [ ] Loga apenas: fonte, contadores, erro resumido, timestamp.
- [ ] Não escreve cópia do arquivo no disco.

## Critérios de aceite

- Reimportar o mesmo arquivo não duplica nenhum registro.
- Ticker novo aparece em `assets` com `class` correto.
- Resultado da importação aparece na visão de Investimentos
  (`views/investimentos.py`) e no Dashboard Geral, refletindo as novas
  operações.
- Erros não interrompem o lote: linhas válidas seguem, linhas inválidas vão
  para `errors`.

## Cuidados para não quebrar o app4

- Não altere as tabelas `assets`, `investment_transactions`, `dividends`,
  `accounts`, `financial_institutions` de forma destrutiva. Apenas
  `ADD COLUMN IF NOT EXISTS` para `external_id`.
- Não remova nem renomeie colunas existentes.
- Não substitua o pipeline automático: importadores manuais não rodam em
  `python run_data_updates.py --all`.

## Documentação de testes

Testes unitários em `tests/test_investment_imports.py` (criar pasta `tests/`
se não existir) cobrem helpers puros — sem banco:

- `_to_float_br("1.234,56") == 1234.56`
- `_parse_date_br("15/03/2025") == date(2025, 3, 15)`
- `_make_external_id(...)` é determinístico para os mesmos inputs.
- `_classify_ticker("MXRF11") == "reit"`
- `_classify_movement("Dividendo") == ("income", "dividend")`

Testes manuais documentados no PR:

1. Subir um `.xlsx` válido pequeno → confere contadores na UI.
2. Reimportar o mesmo `.xlsx` → todos vão para `duplicates_skipped`.
3. Subir arquivo corrompido → mensagem amigável, app não trava.
