# Analista Financeiro Pessoal IA

Seção integrada ao Dashboard Financeiro Unificado (App4). O módulo consolida dados já expostos por `core.controle`, `core.investimentos`, `core.metas` e `core.proventos`; não acessa o banco diretamente e não executa operações financeiras.

## Escopo da primeira versão

- resumo mensal e taxas determinísticas;
- identificação explicável de possíveis duplicidades, recorrências e pequenos gastos frequentes;
- alertas de concentração de carteira;
- leitura de metas e recomendações priorizadas;
- simulador nominal em três cenários;
- perguntas guiadas respondidas por regras locais.

Os achados são candidatos para revisão humana. O sistema não classifica automaticamente uma despesa como desperdício, não presume perfil de risco e não promete retorno.

## Proveniência e segurança

Cada serviço informa `data_source`. A interface diferencia `real`, `mock` e `mock_fallback`; dados de fallback nunca são apresentados como diagnóstico real. A seção é somente leitura e reutiliza o filtro de usuário dos serviços existentes.

## Fórmulas principais

- resultado mensal = receitas − despesas;
- taxa de poupança = resultado / receitas;
- taxa de investimento = aportes classificados / receitas;
- projeção = capitalização mensal do saldo anterior + aporte ao fim de cada mês.

Valores ausentes permanecem como “Dados insuficientes”. As categorias fixas e essenciais são heurísticas explícitas do motor e devem evoluir para classificação configurável pelo usuário.

## Validação

Execute:

```powershell
python -m pytest tests/test_analista_financeiro.py
python -m ruff check core/analista_financeiro.py views/analista_financeiro.py tests/test_analista_financeiro.py
```
