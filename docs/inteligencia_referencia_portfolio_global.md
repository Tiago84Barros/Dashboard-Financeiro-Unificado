# Atualizações 2026-10 — Inteligência dos Ativos: carteira do Portfólio Global como referência comparativa

- **PR:** #437 (merge squash em 2026-10-02, commit `e002a11`)
- **Seção:** Investimentos → Inteligência dos Ativos

## 1. O que mudou

A aba Inteligência dos Ativos passou a mostrar a carteira recomendada do Portfólio
Global **apenas como referência comparativa**. Antes, a Inteligência não tinha
acesso a ela. A dependência só existia no sentido inverso: o Portfólio Global
consome o veredito da Inteligência.

Na tela:
- **Cartão por ativo** (borda tracejada), logo antes do Portfolio Fit. Mostra o peso
  no modelo, o peso real e a diferença (real − modelo, em pontos percentuais),
  mais a base usada nos pesos.
- **Tabela por classe** dentro do expander "Visão geral" (Classe / Modelo / Real /
  Real − modelo). Abaixo dela vêm a lista "Do modelo, fora da sua carteira" (até
  10 ativos, em ordem de peso no modelo) e os avisos.
- Os dois cartões dizem que o modelo **não entra** na "Ação a considerar", no
  Portfolio Fit por regras nem no veredito, e que **não é evidência independente**.

Na LLM:
- O Portfolio Fit LLM recebe a referência como dado declarado, na chave
  `app_model_reference` do contexto JSON.
- O system prompt ganhou a regra `REGRA_REFERENCIA`. A LLM pode citar a referência
  em `portfolio_impact`, como comparação, e não pode usá-la para justificar
  `action_to_consider` nem o nível de portfolio_fit.

## 2. Decisão: por que fora do veredito

O Portfólio Global já usa o veredito da Inteligência para montar a carteira
recomendada. Se essa carteira voltasse à decisão da Inteligência, o mesmo sinal
seria contado duas vezes: **confirmação circular**. Um ativo "comprar" entraria no
modelo, e o modelo reforçaria o "comprar".

Como isso é garantido:
- Ficaram intactos `fit_por_regras`, `analise`, `adequacao`, `veredito`,
  `calculos`, `contexto` e `painel`.
- Um teste (`test_modulos_de_decisao_nao_importam_a_referencia`) falha se algum
  desses módulos passar a citar `referencia_modelo`.
- Outro teste mostra que o contexto do Portfolio Fit é **idêntico** com ou sem a
  referência, exceto pela chave `app_model_reference`.
- Limite conhecido: a barreira na LLM é uma instrução de prompt, não uma garantia.
  A barreira dura está no código das regras, que não recebe a referência.

## 3. Regra de base dos pesos

Os alvos do Portfólio Global dividem só a **parcela de risco** (B3, FII, US). Os
pesos da Inteligência são valor de mercado ÷ patrimônio total. A comparação
escolhe a base assim:

| Situação | Base | Peso no modelo |
|---|---|---|
| Fatia de renda fixa salva na alocação-alvo | Patrimônio inteiro | alvo da classe × (1 − renda fixa) × peso do ativo na classe |
| Sem fatia de renda fixa | Carteira sem renda fixa | alvo da classe × peso do ativo na classe |

A base usada aparece no cartão e no payload da LLM (`base_dos_pesos`). Um mesmo
"+12 pp" significa coisas diferentes em cada base.

## 4. Quando aparece "indisponível"

A referência nunca some: ela fica indisponível e diz o motivo quando:
- não há carteira-modelo ativa em nenhuma classe;
- não há alocação-alvo entre as classes salva;
- a carteira real não tem valor de mercado positivo;
- a leitura falha (o motivo nomeia a exceção, por exemplo `ConnectionError`).

Quando uma classe tem alvo mas não tem carteira-modelo, isso vira aviso, por
exemplo "Internacional: alvo de 20,0% sem carteira-modelo ativa".

**Esperado em produção:** enquanto a alocação-alvo (e a fatia de renda fixa) não
for salva no Portfólio Global, o cartão mostra "indisponível". Não é defeito.

## 5. Limitações aceitas

- **BDRs:** AAPL34 na carteira não casa com AAPL no modelo americano; os dois
  aparecem como posições distintas. Não foi criada tabela de equivalência.
- Tickers com `.SA` são normalizados (`PETR4.SA` → `PETR4`).
- Um ticker presente em duas classes do modelo tem os pesos somados; vale a
  primeira classe.
- Cache em `st.session_state` com TTL de 900 s, a chave é o hash das posições.
  Um modelo republicado no Portfólio Global pode levar até 15 min para aparecer.

## 6. Arquivos

| Arquivo | Papel |
|---|---|
| `core/inteligencia_ativos/referencia_modelo.py` | **Novo.** Módulo puro: `montar()` calcula, `carregar()` lê o banco (único ponto de I/O em `_ler()`, via `core.portfolio.repository`), `ReferenciaModelo.para_llm()` gera o payload |
| `core/inteligencia_ativos/portfolio_fit.py` | `contexto(..., referencia_modelo=)` adiciona `app_model_reference`; `sistema()` inclui `REGRA_REFERENCIA` |
| `core/inteligencia_ativos/leitura_llm.py` | `gerar(..., referencia_modelo=)` repassa o payload ao contexto |
| `views/inteligencia_ativos.py` | Cartões `cartao_referencia_ativo` / `cartao_referencia_carteira` e memo `_referencia_memo` |
| `views/inteligencia_ativos_fit.py` | `render(..., referencia_modelo=)` |
| `tests/conftest.py` | Fixture autouse `_referencia_modelo_sem_banco` (testes sem banco) |
| `tests/test_inteligencia_ativos_referencia_modelo.py` | **Novo.** Pesos nas duas bases, indisponibilidade, avisos, falha nomeada, isolamento da decisão, escape de HTML, AppTest |

Reaproveita `core/global_portfolio/aggregate.montar_posicoes` e
`core/global_portfolio/carteira_real` (`classe_global`, `alvos_globais`,
`CLASSES_REAIS`). A conexão de banco não foi duplicada.

## 7. Verificação

- Suíte completa: 6810 passed, 115 skipped; `ruff check .` limpo; CI verde (Python 3.11 e 3.12).
- **Não verificado contra o Supabase real.** A tela só foi testada sem banco, com dados simulados.
- Merge feito sem revisão humana.

## Relacionadas

- [Inteligência dos Ativos — painel](inteligencia_ativos_dashboard.md)
- Nota equivalente a colar no vault Obsidian, em `04_App_Dashboard_Financeiro_Unificado/`.
