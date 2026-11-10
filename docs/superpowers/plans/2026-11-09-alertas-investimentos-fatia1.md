# Alertas de Investimentos — Fatia 1 (motor e risco) — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a aba 🚨 Alertas em Investimentos com os três tipos de risco da spec (evento grave, deterioração fundamental confirmada, FII com deterioração de renda), registro imutável no armazém local, vitrine publicada, teste PIT com período guardado e o resumo no Dashboard Geral.

**Architecture:** Um motor puro (`core/alertas_ativos/`) recebe séries PIT já lidas, gera observações por ativo e as passa por uma máquina de estados única (`ciclo_vida.avancar`). O mesmo replay (`motor.reproduzir`) alimenta o backtest e a produção, sem cópia gêmea. Cada transição vai para `alertas_ativos.registro` no armazém local, só de inserção e com cadeia de hashes. O estado corrente sai para a vitrine `data/public/alertas_ativos.json.gz`, sem dado pessoal, e a tela filtra pela carteira do usuário.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy, Postgres 16 (armazém `dfu_warehouse`, porta 5433), Streamlit (`st.tabs`, AppTest), pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-alertas-investimentos-design.md` (commit 112a1ac2).

## Global Constraints

- Nada desta fatia escreve no Supabase. Toda escrita passa por `core.destino_local.exigir_local(engine, o_que=...)`.
- O SQL novo mora no armazém local: `supabase_unificado/schema/088_*.sql`, `089_*.sql`, `090_*.sql`. O 087 fica reservado à fatia 4. A primeira linha de cada arquivo é `-- Mora no ARMAZEM LOCAL (dfu_warehouse). Nao aplicar no Supabase.`
- A URL do armazém sai de `scripts.publish_fii_selection_from_local._warehouse_url()`.
- Dado ausente é `None`, nunca zero. `or 0` em valor medido é defeito.
- A data que vale é a de **disponibilidade**:
  - DFP: `market.cvm_filing_publications.disponivel_em`;
  - ITR: `period_end_date + 45 dias`;
  - EUA: `available_at`;
  - FII: `available_at`;
  - IPE: `data_entrega`.
- O repositório é **público**. Teste, commit e PR não levam ticker nem valor do usuário. Use tickers fictícios (`AAAA3`, `BBBB11`, `ZZZ`) ou as fixtures que já existem (`tests/test_inteligencia_avaliacao.py`).
- Pytest: `PY="/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe"` e depois `PYTHONIOENCODING=utf-8 "$PY" -m pytest ...`.
- Commit termina com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. O corpo do PR termina com `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- O merge para a `main` só acontece depois que a revisão do Codex do **último** commit fechar.
- Regra dos gêmeos (CLAUDE.md): uma régua tem uma função só, e o backtest e a produção chamam o mesmo `reproduzir`.
- Mudou a estrutura, mudam no **mesmo PR** as três abas de ⚙️ Configurações: Mapa do App, Documentação e Grau de Confiança. Quem barra é `tests/test_configuracoes_sincronizadas.py`.
- Textos de tela, docstrings e mensagens ficam em português do Brasil. Cor só por token de tema. Não há botão de compra nem de venda. Há no máximo dois níveis de gravidade, e Atenção fica recolhido e fora da contagem.
- Uma vitrine com mais de 48 h aparece marcada como VELHA.
- Bases de calibração: B3 2012–2021, FII 2016–2021, EUA 2012–2021. O período guardado, 2022–2025, roda **uma vez**.

---

## Desvios da spec, declarados

Estes desvios saíram de medir o armazém em 09/11/2026. Cada um vai também para `docs/alertas_ativos/teste_pit_fatia1.md` e para a aba 🎯 Grau de Confiança.

1. **"Qualidade passou de sólida para frágil" não é PIT.** A avaliação completa depende de indicadores que o armazém não guarda por data de disponibilidade. Usam-se só os eliminatórios que dá para reconstruir na data:
   - patrimônio líquido ≤ 0 (`market.balance_sheets.equity`);
   - vacância > 30% (`vacancia_fisica` com `source = 'cvm_informe_trimestral'`, 8.330 linhas, trimestral). No armazém ela é fração de 0 a 1 (mediana 0; p90 0,30), então o leitor da Tarefa 6 multiplica por 100 antes de chamar `el.vacancia_critica`. A fonte `brapi_fii_v2` fica de fora porque não é PIT.

   Dívida líquida/EBITDA e cobertura de juros ficam fora porque o EBITDA PIT da B3 vem nulo.
2. **Inadimplência de FII é trimestral.** Ela vem de `market.fii_metric_observations` com `metric_name = 'delinquency'` e `source = 'cvm_informe_trimestral'`: 8.330 linhas, de 31/12/2016 em diante, defasagem de cerca de 46 dias. É fração de 0 a 1, e o leitor multiplica por 100 antes de comparar com `INADIMPLENCIA_FII` (em %). As outras fontes da métrica (`cvm_cri_lookthrough` e `public_fii_report_provisional_v1`) ficam de fora porque medem outra coisa ou não são oficiais. Por ser trimestral, "inadimplência subindo" compara o último trimestre disponível na data com o anterior, não mês a mês.
3. **Reapresentação de balanço** só é detectada pelo assunto do IPE. A comparação entre versões da DFP fica para depois.
4. **Ofício de questionamento da CVM/B3** entra como **candidato**. Ele aparece como evidência, mas não abre alerta sozinho.
5. **Amortização de FII não está ingerida e não há confirmação pelo FNET.** O rendimento sai de `dy_patrimonial_mes × nav_per_share`, de modo que uma queda por amortização pode parecer corte. O card diz isso no campo "o que invalidaria".
6. **ITR:** a disponibilidade vale `period_end_date + 45 dias`, o prazo legal, porque a data real de entrega do ITR não está ingerida.
7. **Preço do backtest B3 é mensal.** `market.historical_prices` tem uma linha por mês (mediana medida = 1). A queda em 12 meses é medida em fim de mês, e o tempo de antecedência é contado em meses.
8. **Proventos B3 não têm série PIT.** A foto da brapi não é PIT. Por isso a pergunta "Os proventos foram cortados?" responde "sem dado" para ações, a menos que exista documento IPE de corte.
9. **Caixa trimestral PIT não entra na pergunta 1.** "O lucro e o caixa caíram na mesma proporção do preço?" é respondida só com o lucro, e o card diz isso.
10. **Fronteira da calibração:** entradas até 31/12/2020, para que o desfecho de 12 meses termine até 2021. As entradas do período guardado vão de 01/2022 a 10/2025, com desfecho até 10/2026. Série que termina antes dos 12 meses usa o último preço e é contada como **censurada**.
11. **Teste do rótulo das abas.** `tests/test_configuracoes_sincronizadas.py::_abas_de_investimentos` hoje trata todo rótulo calculado como o da Inteligência. A Tarefa 9 adapta o teste para mapear cada `ast.Name` ao seu `ROTULO`.
12. **Tipo do FII (tijolo, papel, FoF) não é PIT.** Vem de `market.fiis.tipo`, que guarda só o tipo atual. Um fundo que mudou de tipo é classificado pelo tipo de hoje em todo o histórico. O relatório PIT diz quantos fundos não têm tipo (esses são tratados como tijolo, que tem mais testes).
13. **Uma safra por mês de FII.** `fii_metric_observations` guarda várias versões de cada (fundo, métrica, referência). O leitor usa a **primeira** (menor `available_at`), que é o que se sabia na data. Correção posterior não reescreve o passado.
14. **Evento grave de FII fica para depois.** O FATO RELEV do FNET só está ingerido a partir de 2022, curto demais para calibrar. Na fatia 1, FII de papel recebe apenas o tipo "renda do FII" pelo corte de rendimento, e FII de tijolo recebe o mesmo tipo com VPA, vacância e inadimplência.
15. **A pergunta 2 não responde sim ou não.** "A queda foi do ativo ou do setor e do mercado?" responde "do ativo", "do mercado" ou "sem dado". A conta compara o retorno de 12 meses com a mediana do universo na data. A mediana por setor fica para depois, porque o setor de hoje não é PIT, pelo mesmo motivo do desvio 12.
16. **O ícone da aba nova é 🚨, e não 🔔.** O 🔔 já é a subaba Análise › 🔔 Alertas, que aponta concentração e falta de cotação. Ícone igual com assunto diferente confundiria. A aba nova cita a antiga no texto de ajuda.
17. **A lista de observação fica para a fatia 4.** Ela pede tabela por usuário (a migration 087, reservada). Na fatia 1, a tela filtra só pela carteira. A vitrine já cobre o universo inteiro, então a lista entra depois sem mudar o motor.
18. **O Grau de Confiança não ganha seção nova.** A subaba mora em Investimentos, e a nota de Investimentos ganha o componente `_alertas_publicados` (peso 0,05; fresca até 2 dias, velha depois de 7). O Dashboard herda a nota pelo mínimo. A Inteligência dos Ativos segue em `SUBABAS_FORA_DA_NOTA`, e o comentário diz que o selo lê a vitrine já medida.
19. **A série de preço é mista e sai reduzida a fim de mês.** `market.historical_prices` mistura linhas mensais e diárias conforme o ativo. O leitor fica com o último preço de cada mês. Preço com mais de 40 dias na data conta como ausente. O mês corrente é medido no próprio fim de mês.
20. **A rotina noturna entra na Tarefa 11, e não antes.** O Alvo `alertas_ativos` só é declarado depois da primeira publicação real, porque o teste `test_artefato_declarado_e_rastreavel_pelo_git` exige o artefato no git. Os três passos escrevem por padrão, sem `--apply`, porque só tocam o armazém local e `data/public`.
21. **Caso censurado sai do numerador e do denominador.** Série que termina antes dos 12 meses não conta nem como acerto nem como erro, tanto nos alertas quanto na taxa-base. O relatório diz quantos casos foram censurados.
22. **Antes do guardado, todo tipo é experimental.** A taxa-base do cartão vem da calibração. Um tipo só deixa de ser experimental se passar no período guardado. Se nenhum passar, a vitrine publica do mesmo jeito e a contagem do rótulo fica zero.
23. **O selo da Inteligência dos Ativos não é botão nem link.** O Streamlit não abre uma subaba de outra tela por link. O selo é texto no título da caixa do ativo ("· 🚨 <tipo>"), e o cartão do Dashboard diz onde está o detalhe.

---

## Mapa de arquivos

### Novos

| Arquivo | Responsabilidade |
|---|---|
| `core/inteligencia_ativos/eliminatorios.py` | Réguas eliminatórias e predicados puros, usados pela avaliação e pelos alertas |
| `core/alertas_ativos/__init__.py` | Marca o pacote |
| `core/alertas_ativos/modelos.py` | Dataclasses `Evidencia`, `Observacao`, `RegraTipo`, `Alerta`, `Transicao` e as constantes de estado |
| `core/alertas_ativos/ciclo_vida.py` | `avancar()`, a máquina de estados única |
| `core/alertas_ativos/registro.py` | Registro imutável no armazém (ler, acrescentar, verificar a cadeia) |
| `core/alertas_ativos/categorias.py` | Classifica o IPE, o parecer do auditor e o item do 8-K em códigos de evento |
| `core/alertas_ativos/dados_pit.py` | Leitores do armazém que devolvem DataFrames com data de disponibilidade |
| `core/alertas_ativos/regras_risco.py` | Geradores de observação dos três tipos, a grade, as seis perguntas, o próximo documento e o "o que invalidaria" |
| `core/alertas_ativos/backtest.py` | Custos, queda em 12 meses, taxa-base, bootstrap e `ResultadoTipo` |
| `core/alertas_ativos/motor.py` | `reproduzir()`, usado pelo backtest e pela produção, e `rodar()` |
| `core/alertas_ativos/vitrine.py` | Carrega a vitrine e calcula idade, VELHA, filtro pela carteira e contagem |
| `supabase_unificado/schema/088_alertas_ativos_registro.sql` | Schema `alertas_ativos`, a tabela `registro` e o gatilho que recusa alteração |
| `supabase_unificado/schema/089_cvm_ipe_metadados.sql` | `market.cvm_ipe_metadados` |
| `supabase_unificado/schema/090_cvm_parecer_auditor.sql` | `market.cvm_parecer_auditor` |
| `scripts/backfill_ipe_metadados.py` | Ingestão histórica dos metadados do IPE (2011 em diante) |
| `scripts/ingerir_parecer_auditor.py` | Parecer do auditor tirado dos ZIPs da DFP |
| `scripts/testar_alertas_pit.py` | Calibração e período guardado (`--fase calibracao\|guardado`) |
| `scripts/publish_alertas_ativos.py` | Roda o motor e grava a vitrine |
| `views/investimentos_alertas.py` | A aba 🚨 Alertas |
| `docs/alertas_ativos/regras_congeladas.json` | Parâmetros escolhidos na calibração e hash da fonte das regras |
| `docs/alertas_ativos/resultado_pit_fatia1.json` | Números do período guardado |
| `docs/alertas_ativos/teste_pit_fatia1.md` | Relatório PIT com números e n |
| `docs/alertas_ativos/guardado_executado.json` | Trava que impede rodar o período guardado de novo |
| `tests/test_alertas_eliminatorios.py`, `tests/test_alertas_ciclo_vida.py`, `tests/test_alertas_registro.py`, `tests/test_alertas_categorias.py`, `tests/test_alertas_ingestao.py`, `tests/test_alertas_sec_historico.py`, `tests/test_alertas_regras_risco.py`, `tests/test_alertas_backtest.py`, `tests/test_alertas_motor.py`, `tests/test_alertas_vitrine.py`, `tests/test_investimentos_alertas.py` | Testes |

### Modificados

| Arquivo | O quê |
|---|---|
| `core/inteligencia_ativos/avaliacao.py:91-109, 484, 495, 512, 516-525, 545, 552, 601` | Constantes e comparações passam a vir de `eliminatorios` |
| `core/inteligencia_ativos/fontes_valuation.py:285` | `val <= 0` vira `el.nao_positivo(val)` |
| `data_pipeline/us/edgar.py` (perto de `submissions`, linha ~309) | `submissions_arquivo(nome)` |
| `data_pipeline/us/sec_filings.py:199-244` | `blocos_de_submissions()` e `coletar(..., historico=False)` |
| `run_us_ingest.py` (argparse e o ramo `filings`, perto da linha 370) | Flag `--historico` |
| `core/publicacao_agenda.py` e `scripts/atualizar_vitrines.py` | Alvo `alertas_ativos` |
| `core/confianca_secao.py` | Componente `_alertas_publicados()` dentro de `confianca_investimentos()` (a seção Investimentos já cobre a subaba; `_SECOES` não muda) e o comentário de `SUBABAS_FORA_DA_NOTA` sobre o selo da IA |
| `views/investimentos.py:5211-5245` | Aba nova entre Análise e Inteligência |
| `views/dashboard_geral.py:2128` | Card de contagem dos alertas |
| `views/inteligencia_ativos_resumida.py:121-132` | Selo "🚨 <tipo>" no `rotulo_expander` |
| `views/documentacao.py` | Nós da aba Alertas, com "cinco subabas" passando a "seis" onde houver |
| `docs/mapa_app/areas/investimentos.json`, `docs/mapa_app/areas/dashboard_geral.json`, `docs/mapa_app/areas/configuracoes.json`, `docs/mapa_app/mapa.json` (regenerado por `scripts/montar_mapa_app.py`) | Mapa do App |
| `tests/conftest.py` | Fixture autouse que esconde a vitrine publicada dos testes |
| `tests/test_publicacao_agenda.py` | Ordem e passos do Alvo `alertas_ativos` |
| `tests/test_dashboard_geral_modernization.py` | Cartão 🚨 Alertas da carteira |
| `tests/test_inteligencia_ativos_resumida.py` | Selo no rótulo da caixa do ativo |
| `tests/test_investimentos_abas_sob_demanda.py` | A aba nova entre Análise e Inteligência |
| `tests/test_configuracoes_sincronizadas.py:91-103` | Rótulo calculado por nome |

---

### Task 1: Réguas eliminatórias numa função única

Hoje `avaliacao.py` compara direto (`x > critico`, `vac > VACANCIA_FII[1]` etc.), e `fontes_valuation.py:285` repete `val <= 0`. Os alertas vão precisar das mesmas réguas. Pela regra dos gêmeos, elas saem para um módulo-folha, e os dois caminhos passam a chamá-lo.

**Files:**
- Create: `core/inteligencia_ativos/eliminatorios.py`
- Modify: `core/inteligencia_ativos/avaliacao.py:91-109` (as constantes viram reexportação), `:484`, `:495`, `:512`, `:516-525`, `:545`, `:552`, `:601`
- Modify: `core/inteligencia_ativos/fontes_valuation.py:285`
- Test: `tests/test_alertas_eliminatorios.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `eliminatorios.LIMITES_ALAVANCAGEM: dict[str, tuple[float,float,float]]`
  - `FATOR_CRITICO_DIVIDA_PL`, `COBERTURA_JUROS`, `PAYOUT_ACIMA_DO_LUCRO`, `VACANCIA_FII`, `INADIMPLENCIA_FII`, `ALAVANCAGEM_FII_ALTA`
  - predicados `(x, ...) -> bool`, em que `None`/NaN sempre dá `False`: `alavancagem_critica(x, perfil)`, `divida_pl_critica(x, teto)`, `cobertura_critica(x)`, `payout_insustentavel(payout, fcl)`, `vacancia_critica(x)`, `inadimplencia_critica(x)`, `nao_positivo(x)`, `patrimonio_nao_positivo(x)`

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_alertas_eliminatorios.py
"""As réguas eliminatórias moram num lugar só e os dois caminhos concordam."""
import math

import pytest

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import eliminatorios as el
from core.inteligencia_ativos import fundamentos as f
from tests.test_inteligencia_avaliacao import BOA, _com, _fund, an  # noqa: F401


def test_ausente_nunca_dispara():
    for pred in (el.cobertura_critica, el.vacancia_critica,
                 el.inadimplencia_critica, el.nao_positivo,
                 el.patrimonio_nao_positivo):
        assert pred(None) is False
        assert pred(math.nan) is False
    assert el.alavancagem_critica(None, "geral") is False
    assert el.divida_pl_critica(None, 1.0) is False
    assert el.divida_pl_critica(5.0, None) is False
    assert el.payout_insustentavel(None, -1.0) is False
    assert el.payout_insustentavel(150.0, None) is False


@pytest.mark.parametrize("x,esperado", [(29.9, False), (30.0, False), (30.1, True)])
def test_vacancia_fronteira(x, esperado):
    assert el.vacancia_critica(x) is esperado


@pytest.mark.parametrize("x,esperado", [(10.0, False), (10.1, True)])
def test_inadimplencia_fronteira(x, esperado):
    assert el.inadimplencia_critica(x) is esperado


@pytest.mark.parametrize("x,esperado", [(1.0, False), (0.99, True)])
def test_cobertura_fronteira(x, esperado):
    assert el.cobertura_critica(x) is esperado


def test_alavancagem_usa_regua_do_perfil():
    assert el.alavancagem_critica(5.0, "geral") is True      # crítico 4,5
    assert el.alavancagem_critica(5.0, "regulada") is False  # crítico 5,5
    assert el.alavancagem_critica(5.0, "perfil_desconhecido") is True  # cai em geral


def test_nao_positivo_e_patrimonio():
    assert el.nao_positivo(0.0) is True
    assert el.nao_positivo(-0.01) is True
    assert el.nao_positivo(0.01) is False
    assert el.patrimonio_nao_positivo(0.0) is True


def test_avaliacao_reexporta_as_mesmas_constantes():
    assert av.VACANCIA_FII is el.VACANCIA_FII
    assert av.INADIMPLENCIA_FII is el.INADIMPLENCIA_FII
    assert av.COBERTURA_JUROS is el.COBERTURA_JUROS
    assert av.LIMITES_ALAVANCAGEM is el.LIMITES_ALAVANCAGEM


@pytest.mark.parametrize("vac", [29.0, 30.0, 31.0, 45.0])
def test_equivalencia_vacancia_fii(an, vac):
    fund = _fund(f.FII, vacancia_financeira=vac, inadimplencia=2.0,
                 alavancagem=5.0, wault=6.0)
    r = av.avaliar(_com(an["HGLG11"], fund))
    tem_critico = any("vacância" in a.texto and a.critico for a in r.alertas)
    assert tem_critico is el.vacancia_critica(vac)


@pytest.mark.parametrize("inad", [9.0, 10.0, 10.5])
def test_equivalencia_inadimplencia_fii(an, inad):
    fund = _fund(f.FII, vacancia_financeira=5.0, inadimplencia=inad,
                 alavancagem=5.0, wault=6.0)
    r = av.avaliar(_com(an["HGLG11"], fund))
    tem_critico = any("inadimplência" in a.texto and a.critico for a in r.alertas)
    assert tem_critico is el.inadimplencia_critica(inad)


@pytest.mark.parametrize("cob", [0.5, 0.99, 1.0, 1.2])
def test_equivalencia_cobertura(an, cob):
    fund = _fund(**{**BOA, "cobertura_juros": cob})
    r = av.avaliar(_com(an["WEGE3"], fund))
    tem_critico = any("não paga os juros" in a.texto and a.critico for a in r.alertas)
    assert tem_critico is el.cobertura_critica(cob)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_eliminatorios.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.inteligencia_ativos.eliminatorios'`.

- [ ] **Step 3: Escrever o módulo**

```python
# core/inteligencia_ativos/eliminatorios.py
"""Réguas eliminatórias: um lugar só para a avaliação e para os alertas.

Os perfis são as strings de ``avaliacao`` ("geral", "regulada", "ciclica"...);
o módulo não importa ``avaliacao`` para continuar folha e barato.
Valor ausente (``None`` ou NaN) nunca dispara uma régua: dado que falta é
lacuna, não defeito do ativo.
"""
from __future__ import annotations

# Dívida líquida/EBITDA: (bom até, alto acima de, crítico acima de).
LIMITES_ALAVANCAGEM = {"geral": (1.5, 3.0, 4.5), "ciclica": (1.0, 2.5, 4.0),
                       "regulada": (3.0, 4.0, 5.5)}
# Dívida bruta/patrimônio acima deste múltiplo do teto do segmento elimina a
# incorporadora (nela, é a régua que substitui a de dívida/EBITDA).
FATOR_CRITICO_DIVIDA_PL = 2.0
# Cobertura de juros (resultado operacional/juros): (fraca abaixo de,
# eliminatória abaixo de).
COBERTURA_JUROS = (1.5, 1.0)
# Payout (%) acima do qual a empresa distribui mais que o lucro.
PAYOUT_ACIMA_DO_LUCRO = 100.0
# Réguas do FII, em %: (desfavorável acima de, eliminatório acima de).
VACANCIA_FII = (15.0, 30.0)
INADIMPLENCIA_FII = (5.0, 10.0)
# Alavancagem do FII (% do patrimônio) acima da qual pesa contra.
ALAVANCAGEM_FII_ALTA = 30.0


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def alavancagem_critica(x, perfil: str) -> bool:
    x = _num(x)
    if x is None:
        return False
    critico = LIMITES_ALAVANCAGEM.get(perfil, LIMITES_ALAVANCAGEM["geral"])[2]
    return x > critico


def divida_pl_critica(x, teto) -> bool:
    x, teto = _num(x), _num(teto)
    if x is None or teto is None:
        return False
    return x > FATOR_CRITICO_DIVIDA_PL * teto


def cobertura_critica(x) -> bool:
    x = _num(x)
    return x is not None and x < COBERTURA_JUROS[1]


def payout_insustentavel(payout, fcl) -> bool:
    """Distribui mais que o lucro com caixa livre negativo."""
    payout, fcl = _num(payout), _num(fcl)
    if payout is None or fcl is None:
        return False
    return payout > PAYOUT_ACIMA_DO_LUCRO and fcl < 0


def vacancia_critica(x) -> bool:
    x = _num(x)
    return x is not None and x > VACANCIA_FII[1]


def inadimplencia_critica(x) -> bool:
    x = _num(x)
    return x is not None and x > INADIMPLENCIA_FII[1]


def nao_positivo(x) -> bool:
    x = _num(x)
    return x is not None and x <= 0


def patrimonio_nao_positivo(x) -> bool:
    return nao_positivo(x)
```

- [ ] **Step 4: Ligar a avaliação ao módulo**

Em `core/inteligencia_ativos/avaliacao.py`:

1. Acrescente `from core.inteligencia_ativos import eliminatorios as el` ao bloco de imports (linhas 51–56).
2. Troque o bloco das linhas 91–109, de `# Dívida líquida/EBITDA:` até `ALAVANCAGEM_FII_ALTA = 30.0`, por:

```python
# Réguas eliminatórias: moram em ``eliminatorios`` (avaliação e alertas usam
# a mesma função; ver CLAUDE.md, "procure os gêmeos").
LIMITES_ALAVANCAGEM = el.LIMITES_ALAVANCAGEM
FATOR_CRITICO_DIVIDA_PL = el.FATOR_CRITICO_DIVIDA_PL
COBERTURA_JUROS = el.COBERTURA_JUROS
PAYOUT_ACIMA_DO_LUCRO = el.PAYOUT_ACIMA_DO_LUCRO
VACANCIA_FII = el.VACANCIA_FII
INADIMPLENCIA_FII = el.INADIMPLENCIA_FII
ALAVANCAGEM_FII_ALTA = el.ALAVANCAGEM_FII_ALTA
```

3. Troque as comparações eliminatórias, mantendo o texto e a estrutura:

| Linha | Antes | Depois |
|---|---|---|
| 484 | `if x > critico:` | `if el.alavancagem_critica(x, perfil_):` |
| 495 | `if perfil_ == INCORPORADORA and x > FATOR_CRITICO_DIVIDA_PL * teto:` | `if perfil_ == INCORPORADORA and el.divida_pl_critica(x, teto):` |
| 512 | `if x < COBERTURA_JUROS[1]:` | `if el.cobertura_critica(x):` |
| 516–517 | `if payout is not None and payout > PAYOUT_ACIMA_DO_LUCRO:` seguido de `if fcl is not None and fcl < 0:` | o externo fica igual; o interno passa a `if el.payout_insustentavel(payout, fcl):` |
| 545 | `if vac > VACANCIA_FII[1]:` | `if el.vacancia_critica(vac):` |
| 552 | `if inad > INADIMPLENCIA_FII[1]:` | `if el.inadimplencia_critica(inad):` |

A linha 601 (`patrimonio_negativo`) já vem decidida pelo motivo do valuation e fica como está. O motivo nasce em `fontes_valuation.py:285`, que é o passo seguinte.

- [ ] **Step 5: Ligar o valuation**

Em `core/inteligencia_ativos/fontes_valuation.py`, acrescente `from core.inteligencia_ativos import eliminatorios as el` aos imports e troque a linha 285:

```python
        if excl_nao_positivo and el.nao_positivo(val):
```

- [ ] **Step 6: Rodar os testes, os novos e os da avaliação**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_eliminatorios.py tests/test_inteligencia_avaliacao.py tests/test_inteligencia_valuation.py -q`
Expected: PASS. Se `tests/test_inteligencia_valuation.py` não existir, rode `-k "valuation or avaliacao"` sobre `tests/`.

- [ ] **Step 7: Procurar gêmeos que ficaram para trás**

Run: `grep -rn "VACANCIA_FII\[1\]\|INADIMPLENCIA_FII\[1\]\|COBERTURA_JUROS\[1\]\|FATOR_CRITICO_DIVIDA_PL \*" --include=*.py core views scripts`
Expected: nenhuma ocorrência fora de `eliminatorios.py`. Cada ocorrência que aparecer é trocada pelo predicado ou ganha, no PR, o motivo de ficar.

- [ ] **Step 8: Commit**

```bash
git add core/inteligencia_ativos/eliminatorios.py core/inteligencia_ativos/avaliacao.py core/inteligencia_ativos/fontes_valuation.py tests/test_alertas_eliminatorios.py
git commit -m "Inteligência: réguas eliminatórias numa função única (alertas e avaliação)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Modelos e máquina de estados

**Files:**
- Create: `core/alertas_ativos/__init__.py`, `core/alertas_ativos/modelos.py`, `core/alertas_ativos/ciclo_vida.py`
- Test: `tests/test_alertas_ciclo_vida.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - Estados: `NOVO`, `ATUALIZADO`, `RESOLVIDO`, `INVALIDADO`, `EXPIRADO`, `SUSPENSO`. Conjunto `ABERTOS = {NOVO, ATUALIZADO, SUSPENSO}`. Gravidade dentro do nível: `GRAVIDADE_BASE = 1`, `GRAVIDADE_REFORCADA = 2`. Nível vem do tipo: `NIVEL_RISCO = "risco_de_tese"`, `NIVEL_ATENCAO = "atencao"`, `NIVEL_OPORTUNIDADE = "oportunidade"`, `NIVEL_DO_TIPO: dict[str, str]` (os três tipos da fatia 1 → `NIVEL_RISCO`).
  - `Evidencia(familia: str, codigo: str, descricao: str, data: date, fonte: str, url: str | None = None)`, frozen.
  - `Observacao(data: date, presente: bool, desfeita: bool | None = None, evidencias: tuple[Evidencia, ...] = (), fontes_frescas: bool = True, contraria: bool = False, confirmada: bool = False, preco: float | None = None)`, frozen.
  - `RegraTipo(tipo: str, versao: str, saidas: int, prazo_minimo_dias: int, horizonte_dias: int, validade_confirmacao_dias: int | None = None, resolve_so_no_horizonte: bool = False)`, frozen.
  - `Alerta(ativo, tipo, versao_regra, estado, gravidade, aberto_em: date, atualizado_em: date, evidencias: tuple[Evidencia,...], saidas_consecutivas: int = 0, estado_antes_suspensao: str | None = None, preco_abertura: float | None = None, confirmado: bool = False)`, frozen, com `chave` (a string `"ativo|tipo|aberto_em"`), `familias` (frozenset), `para_dict()` e `Alerta.de_dict(d)`.
  - `Transicao(alerta: Alerta, estado: str, data_evento: date, motivo: str)`, frozen.
  - `ciclo_vida.avancar(alerta: Alerta | None, obs: Observacao, regra: RegraTipo, *, ativo: str) -> tuple[Alerta | None, list[Transicao]]`.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_alertas_ciclo_vida.py
"""Cada transição da spec (seção 5) tem um teste."""
from datetime import date, timedelta

from core.alertas_ativos import ciclo_vida as cv
from core.alertas_ativos import modelos as m

R = m.RegraTipo(tipo="deterioracao", versao="1.0.0", saidas=2,
                prazo_minimo_dias=90, horizonte_dias=365)
D0 = date(2020, 3, 31)
EV_F = m.Evidencia("fundamento", "RECEITA_LUCRO_CAEM", "receita e lucro caem", D0, "CVM")
EV_P = m.Evidencia("preco", "PRECO_RELATIVO", "pior que o mercado", D0, "B3")
EV_D = m.Evidencia("documento", "REAPRESENTACAO", "reapresentação", D0, "CVM/IPE")


def _obs(d, **kw):
    return m.Observacao(data=d, **kw)


def _novo():
    a, ts = cv.avancar(None, _obs(D0, presente=True, evidencias=(EV_F, EV_P)), R, ativo="AAAA3")
    return a, ts


def test_nada_presente_nao_cria():
    a, ts = cv.avancar(None, _obs(D0, presente=False), R, ativo="AAAA3")
    assert a is None and ts == []


def test_novo_com_duas_familias_e_reforcada():
    a, ts = _novo()
    assert a.estado == m.NOVO and a.gravidade == m.GRAVIDADE_REFORCADA
    assert [t.estado for t in ts] == [m.NOVO]
    assert a.chave == "AAAA3|deterioracao|2020-03-31"


def test_novo_com_uma_familia_e_base():
    a, _ = cv.avancar(None, _obs(D0, presente=True, evidencias=(EV_F,)), R, ativo="AAAA3")
    assert a.gravidade == m.GRAVIDADE_BASE


def test_evidencia_nova_atualiza_e_sobe_gravidade():
    a, _ = cv.avancar(None, _obs(D0, presente=True, evidencias=(EV_F,)), R, ativo="AAAA3")
    d1 = D0 + timedelta(days=30)
    ev = m.Evidencia("preco", "PRECO_RELATIVO", "pior", d1, "B3")
    a, ts = cv.avancar(a, _obs(d1, presente=True, evidencias=(ev,)), R, ativo="AAAA3")
    assert a.estado == m.ATUALIZADO and a.gravidade == m.GRAVIDADE_REFORCADA
    assert [t.estado for t in ts] == [m.ATUALIZADO]


def test_mesma_evidencia_nao_atualiza():
    a, _ = _novo()
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=30), presente=True, evidencias=(EV_F,)), R, ativo="AAAA3")
    assert ts == [] and a.estado == m.NOVO


def test_histerese_pede_saidas_consecutivas_e_prazo_minimo():
    a, _ = _novo()
    d = D0 + timedelta(days=95)
    a, ts = cv.avancar(a, _obs(d, presente=False, desfeita=True), R, ativo="AAAA3")
    assert ts == [] and a.saidas_consecutivas == 1
    a, ts = cv.avancar(a, _obs(d + timedelta(days=90), presente=False, desfeita=True), R, ativo="AAAA3")
    assert [t.estado for t in ts] == [m.RESOLVIDO]


def test_saida_interrompida_zera_contagem():
    a, _ = _novo()
    d = D0 + timedelta(days=95)
    a, _ = cv.avancar(a, _obs(d, presente=False, desfeita=True), R, ativo="AAAA3")
    a, _ = cv.avancar(a, _obs(d + timedelta(days=30), presente=False, desfeita=False), R, ativo="AAAA3")
    assert a.saidas_consecutivas == 0


def test_saida_antes_do_prazo_minimo_nao_resolve():
    a, _ = _novo()
    a, _ = cv.avancar(a, _obs(D0 + timedelta(days=10), presente=False, desfeita=True), R, ativo="AAAA3")
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=20), presente=False, desfeita=True), R, ativo="AAAA3")
    assert ts == [] and a.estado == m.NOVO


def test_desfeita_none_nao_conta_nem_zera():
    a, _ = _novo()
    d = D0 + timedelta(days=95)
    a, _ = cv.avancar(a, _obs(d, presente=False, desfeita=True), R, ativo="AAAA3")
    a, ts = cv.avancar(a, _obs(d + timedelta(days=30), presente=False, desfeita=None), R, ativo="AAAA3")
    assert ts == [] and a.saidas_consecutivas == 1


def test_contraria_invalida():
    a, _ = _novo()
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=40), presente=False, contraria=True), R, ativo="AAAA3")
    assert a.estado == m.INVALIDADO and [t.estado for t in ts] == [m.INVALIDADO]


def test_horizonte_resolve():
    a, _ = _novo()
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=366), presente=True), R, ativo="AAAA3")
    assert [t.estado for t in ts] == [m.RESOLVIDO]
    assert "horizonte" in ts[0].motivo


def test_fonte_velha_suspende_e_volta_ao_estado_anterior():
    a, _ = _novo()
    d = D0 + timedelta(days=30)
    a, ts = cv.avancar(a, _obs(d, presente=True, fontes_frescas=False), R, ativo="AAAA3")
    assert a.estado == m.SUSPENSO and [t.estado for t in ts] == [m.SUSPENSO]
    a, ts = cv.avancar(a, _obs(d + timedelta(days=5), presente=True), R, ativo="AAAA3")
    assert a.estado == m.NOVO and [t.estado for t in ts] == [m.NOVO]
    assert "fontes voltaram" in ts[0].motivo


def test_confirmacao_expira():
    rc = m.RegraTipo(tipo="evento_grave", versao="1.0.0", saidas=1, prazo_minimo_dias=0,
                     horizonte_dias=365, validade_confirmacao_dias=60,
                     resolve_so_no_horizonte=True)
    a, _ = cv.avancar(None, _obs(D0, presente=True, evidencias=(EV_D,)), rc, ativo="AAAA3")
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=61), presente=False), rc, ativo="AAAA3")
    assert a.estado == m.EXPIRADO and [t.estado for t in ts] == [m.EXPIRADO]


def test_confirmada_nao_expira_e_resolve_so_no_horizonte():
    rc = m.RegraTipo(tipo="evento_grave", versao="1.0.0", saidas=1, prazo_minimo_dias=0,
                     horizonte_dias=365, validade_confirmacao_dias=60,
                     resolve_so_no_horizonte=True)
    a, _ = cv.avancar(None, _obs(D0, presente=True, evidencias=(EV_D,), confirmada=True), rc, ativo="AAAA3")
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=100), presente=False, desfeita=True), rc, ativo="AAAA3")
    assert ts == [] and a.estado == m.NOVO
    a, ts = cv.avancar(a, _obs(D0 + timedelta(days=366), presente=False), rc, ativo="AAAA3")
    assert [t.estado for t in ts] == [m.RESOLVIDO]


def test_ida_e_volta_pelo_dict():
    a, _ = _novo()
    assert m.Alerta.de_dict(a.para_dict()) == a


def test_fechado_nao_avanca():
    a, _ = _novo()
    a, _ = cv.avancar(a, _obs(D0 + timedelta(days=40), presente=False, contraria=True), R, ativo="AAAA3")
    b, ts = cv.avancar(a, _obs(D0 + timedelta(days=50), presente=True, evidencias=(EV_F,)), R, ativo="AAAA3")
    # Fechado não reabre: um alerta novo nasce com outra chave.
    assert b is not None and b.estado == m.NOVO and b.aberto_em == D0 + timedelta(days=50)
    assert [t.estado for t in ts] == [m.NOVO]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_ciclo_vida.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.alertas_ativos'`.

- [ ] **Step 3: Escrever `modelos.py`**

```python
# core/alertas_ativos/__init__.py
"""Alertas de Investimentos: motor, regras, registro e vitrine."""
```

```python
# core/alertas_ativos/modelos.py
"""Tipos do motor de alertas. Tudo frozen: o estado anda por substituição."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date

NOVO, ATUALIZADO, RESOLVIDO = "novo", "atualizado", "resolvido"
INVALIDADO, EXPIRADO, SUSPENSO = "invalidado", "expirado", "suspenso"
ABERTOS = frozenset({NOVO, ATUALIZADO, SUSPENSO})
FECHADOS = frozenset({RESOLVIDO, INVALIDADO, EXPIRADO})
# gravidade dentro do nível; o nível vem do tipo (NIVEL_DO_TIPO)
GRAVIDADE_BASE, GRAVIDADE_REFORCADA = 1, 2
NIVEL_RISCO, NIVEL_ATENCAO, NIVEL_OPORTUNIDADE = "risco_de_tese", "atencao", "oportunidade"

TIPO_EVENTO_GRAVE = "evento_grave"
TIPO_DETERIORACAO = "deterioracao"
TIPO_FII_RENDA = "fii_renda"
NIVEL_DO_TIPO = {
    TIPO_EVENTO_GRAVE: NIVEL_RISCO,
    TIPO_DETERIORACAO: NIVEL_RISCO,
    TIPO_FII_RENDA: NIVEL_RISCO,
}
ROTULO_TIPO = {
    TIPO_EVENTO_GRAVE: "Evento grave",
    TIPO_DETERIORACAO: "Deterioração fundamental confirmada",
    TIPO_FII_RENDA: "FII com deterioração de renda",
}


@dataclass(frozen=True)
class Evidencia:
    familia: str          # "documento" | "fundamento" | "preco" | "renda"
    codigo: str
    descricao: str
    data: date            # data de disponibilidade
    fonte: str
    url: str | None = None


@dataclass(frozen=True)
class Observacao:
    data: date
    presente: bool
    desfeita: bool | None = None   # None = sem dado para julgar a saída
    evidencias: tuple[Evidencia, ...] = ()
    fontes_frescas: bool = True
    contraria: bool = False
    confirmada: bool = False
    preco: float | None = None


@dataclass(frozen=True)
class RegraTipo:
    tipo: str
    versao: str
    saidas: int
    prazo_minimo_dias: int
    horizonte_dias: int
    validade_confirmacao_dias: int | None = None
    resolve_so_no_horizonte: bool = False


@dataclass(frozen=True)
class Alerta:
    ativo: str
    tipo: str
    versao_regra: str
    estado: str
    gravidade: int
    aberto_em: date
    atualizado_em: date
    evidencias: tuple[Evidencia, ...] = field(default_factory=tuple)
    saidas_consecutivas: int = 0
    estado_antes_suspensao: str | None = None
    preco_abertura: float | None = None
    confirmado: bool = False

    @property
    def chave(self) -> str:
        return f"{self.ativo}|{self.tipo}|{self.aberto_em.isoformat()}"

    @property
    def familias(self) -> frozenset[str]:
        return frozenset(e.familia for e in self.evidencias)

    def com(self, **kw) -> "Alerta":
        return replace(self, **kw)

    def para_dict(self) -> dict:
        d = asdict(self)
        d["aberto_em"] = self.aberto_em.isoformat()
        d["atualizado_em"] = self.atualizado_em.isoformat()
        d["evidencias"] = [{**asdict(e), "data": e.data.isoformat()}
                           for e in self.evidencias]
        d["chave"] = self.chave
        return d

    @classmethod
    def de_dict(cls, d: dict) -> "Alerta":
        evs = tuple(Evidencia(**{**e, "data": date.fromisoformat(e["data"])})
                    for e in d.get("evidencias") or ())
        campos = {k: v for k, v in d.items() if k not in ("chave", "evidencias")}
        campos["aberto_em"] = date.fromisoformat(d["aberto_em"])
        campos["atualizado_em"] = date.fromisoformat(d["atualizado_em"])
        return cls(**campos, evidencias=evs)


@dataclass(frozen=True)
class Transicao:
    alerta: Alerta
    estado: str
    data_evento: date
    motivo: str
```

- [ ] **Step 4: Escrever `ciclo_vida.py`**

```python
# core/alertas_ativos/ciclo_vida.py
"""A máquina de estados única (spec, seção 5).

Ordem dos testes, por observação:
1. fonte velha → SUSPENSO (guarda o estado anterior);
2. volta de suspensão → estado anterior;
3. contrária → INVALIDADO;
4. horizonte vencido → RESOLVIDO;
5. confirmação vencida sem confirmar → EXPIRADO;
6. histerese de saída → RESOLVIDO;
7. evidência nova → ATUALIZADO.
Alerta fechado não reabre: se a condição volta, nasce outro, com outra chave.
"""
from __future__ import annotations

from core.alertas_ativos import modelos as m


def _gravidade(familias: frozenset[str]) -> int:
    return m.GRAVIDADE_REFORCADA if len(familias) >= 2 else m.GRAVIDADE_BASE


def _chaves(evs) -> set[tuple[str, str]]:
    return {(e.familia, e.codigo) for e in evs}


def _abrir(obs: m.Observacao, regra: m.RegraTipo, ativo: str):
    fam = frozenset(e.familia for e in obs.evidencias)
    a = m.Alerta(ativo=ativo, tipo=regra.tipo, versao_regra=regra.versao,
                 estado=m.NOVO, gravidade=_gravidade(fam), aberto_em=obs.data,
                 atualizado_em=obs.data, evidencias=tuple(obs.evidencias),
                 preco_abertura=obs.preco, confirmado=obs.confirmada)
    return a, [m.Transicao(a, m.NOVO, obs.data, "condição presente")]


def avancar(alerta: m.Alerta | None, obs: m.Observacao, regra: m.RegraTipo,
            *, ativo: str) -> tuple[m.Alerta | None, list[m.Transicao]]:
    if alerta is None or alerta.estado in m.FECHADOS:
        if obs.presente and obs.fontes_frescas:
            return _abrir(obs, regra, ativo)
        return alerta, []

    # 1-2. Suspensão por fonte velha e volta.
    if not obs.fontes_frescas:
        if alerta.estado == m.SUSPENSO:
            return alerta, []
        a = alerta.com(estado=m.SUSPENSO, estado_antes_suspensao=alerta.estado,
                       atualizado_em=obs.data)
        return a, [m.Transicao(a, m.SUSPENSO, obs.data, "fonte desatualizada")]
    trans: list[m.Transicao] = []
    if alerta.estado == m.SUSPENSO:
        volta = alerta.estado_antes_suspensao or m.NOVO
        alerta = alerta.com(estado=volta, estado_antes_suspensao=None,
                            atualizado_em=obs.data)
        trans.append(m.Transicao(alerta, volta, obs.data, "fontes voltaram"))

    idade = (obs.data - alerta.aberto_em).days

    # 3. Evidência contrária.
    if obs.contraria:
        a = alerta.com(estado=m.INVALIDADO, atualizado_em=obs.data)
        return a, trans + [m.Transicao(a, m.INVALIDADO, obs.data, "evidência contrária")]

    # 4. Horizonte.
    if idade >= regra.horizonte_dias:
        a = alerta.com(estado=m.RESOLVIDO, atualizado_em=obs.data)
        return a, trans + [m.Transicao(a, m.RESOLVIDO, obs.data, "horizonte encerrado")]

    # 5. Confirmação que não veio. A confirmação é um campo, não uma
    # evidência: marcá-la como evidência inflaria a contagem de famílias.
    if obs.confirmada and not alerta.confirmado:
        alerta = alerta.com(confirmado=True)
    if (regra.validade_confirmacao_dias is not None and not alerta.confirmado
            and idade > regra.validade_confirmacao_dias):
        a = alerta.com(estado=m.EXPIRADO, atualizado_em=obs.data)
        return a, trans + [m.Transicao(a, m.EXPIRADO, obs.data, "confirmação não veio no prazo")]

    # 6. Histerese.
    if obs.desfeita is True:
        saidas = alerta.saidas_consecutivas + 1
    elif obs.desfeita is False or obs.presente:
        saidas = 0
    else:
        saidas = alerta.saidas_consecutivas
    alerta = alerta.com(saidas_consecutivas=saidas)
    if (not regra.resolve_so_no_horizonte and saidas >= regra.saidas
            and idade >= regra.prazo_minimo_dias):
        a = alerta.com(estado=m.RESOLVIDO, atualizado_em=obs.data)
        return a, trans + [m.Transicao(a, m.RESOLVIDO, obs.data,
                                       f"condição desfeita em {saidas} leituras seguidas")]

    # 7. Evidência nova.
    novas = [e for e in obs.evidencias if (e.familia, e.codigo) not in _chaves(alerta.evidencias)]
    if novas:
        evs = alerta.evidencias + tuple(novas)
        fam = frozenset(e.familia for e in evs)
        a = alerta.com(estado=m.ATUALIZADO, evidencias=evs, atualizado_em=obs.data,
                       gravidade=max(alerta.gravidade, _gravidade(fam)))
        return a, trans + [m.Transicao(a, m.ATUALIZADO, obs.data, "evidência nova")]
    return alerta, trans
```

- [ ] **Step 5: Rodar os testes**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_ciclo_vida.py -q`
Expected: PASS (16 testes).

- [ ] **Step 6: Commit**

```bash
git add core/alertas_ativos/__init__.py core/alertas_ativos/modelos.py core/alertas_ativos/ciclo_vida.py tests/test_alertas_ciclo_vida.py
git commit -m "Alertas: modelos e máquina de estados com histerese, suspensão e horizonte

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Registro imutável no armazém local

**Files:**
- Create: `supabase_unificado/schema/088_alertas_ativos_registro.sql`, `core/alertas_ativos/registro.py`
- Test: `tests/test_alertas_registro.py`

**Interfaces:**
- Consumes:
  - `modelos.Transicao` e `modelos.Alerta.para_dict()` (Task 2);
  - `core.carteira20.canonico(obj) -> str`, `core.carteira20.calcular_hash(registro: dict) -> str` (sha256 da forma canônica sem a chave `"hash"`) e `core.carteira20.StatusCadeia(integra: bool, n: int, ultimo_hash: str | None, erros: tuple[str, ...])`;
  - `core.destino_local.exigir_local(engine, o_que=...)`.
- Produces:
  - `registro.montar(transicao, hash_anterior: str | None, registrado_em: datetime) -> dict`
  - `registro.verificar(linhas: list[dict]) -> c20.StatusCadeia`
  - `registro.aplicar_migracao(engine, schema="alertas_ativos") -> None`
  - `registro.ler(engine, schema="alertas_ativos") -> list[dict]`, em ordem de `seq`
  - `registro.acrescentar(engine, transicoes, *, agora: datetime, schema="alertas_ativos") -> int`, que devolve quantas linhas entraram
  - `registro.abertos(linhas) -> dict[str, dict]`, que vai da chave ao último `alerta` aberto
  - `registro.inicio_do_registro(linhas) -> date | None`, o fim do mês da primeira gravação (`registrado_em`)

- [ ] **Step 1: Escrever o SQL**

```sql
-- supabase_unificado/schema/088_alertas_ativos_registro.sql
-- Mora no ARMAZEM LOCAL (dfu_warehouse). Nao aplicar no Supabase.
-- Registro dos alertas de investimentos: so de insercao, com cadeia de hashes.
CREATE SCHEMA IF NOT EXISTS alertas_ativos;

CREATE TABLE IF NOT EXISTS alertas_ativos.registro (
    seq            BIGSERIAL PRIMARY KEY,
    chave          TEXT        NOT NULL,
    ativo          TEXT        NOT NULL,
    tipo           TEXT        NOT NULL,
    versao_regra   TEXT        NOT NULL,
    estado         TEXT        NOT NULL,
    data_evento    DATE        NOT NULL,
    motivo         TEXT        NOT NULL,
    alerta         JSONB       NOT NULL,
    registrado_em  TIMESTAMPTZ NOT NULL,
    hash_anterior  TEXT,
    hash           TEXT        NOT NULL UNIQUE,
    CONSTRAINT uq_alertas_transicao UNIQUE (chave, estado, data_evento, versao_regra)
);
CREATE INDEX IF NOT EXISTS ix_alertas_registro_ativo
    ON alertas_ativos.registro (ativo, data_evento DESC);

CREATE OR REPLACE FUNCTION alertas_ativos.recusa_alteracao() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'alertas_ativos.registro é só de inserção';
END $$;

DROP TRIGGER IF EXISTS tg_registro_imutavel ON alertas_ativos.registro;
CREATE TRIGGER tg_registro_imutavel
    BEFORE UPDATE OR DELETE ON alertas_ativos.registro
    FOR EACH ROW EXECUTE FUNCTION alertas_ativos.recusa_alteracao();

DROP TRIGGER IF EXISTS tg_registro_sem_truncate ON alertas_ativos.registro;
CREATE TRIGGER tg_registro_sem_truncate
    BEFORE TRUNCATE ON alertas_ativos.registro
    FOR EACH STATEMENT EXECUTE FUNCTION alertas_ativos.recusa_alteracao();
```

- [ ] **Step 2: Escrever os testes que falham**

```python
# tests/test_alertas_registro.py
"""Registro só de inserção: cadeia íntegra, dedup e gatilho que recusa alteração."""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from core.alertas_ativos import modelos as m
from core.alertas_ativos import registro as rg

AGORA = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
EV = m.Evidencia("documento", "RJ", "pedido de recuperação judicial", date(2026, 1, 5), "CVM/IPE")


def _transicao(estado=m.NOVO, d=date(2026, 1, 5), ativo="AAAA3"):
    a = m.Alerta(ativo=ativo, tipo=m.TIPO_EVENTO_GRAVE, versao_regra="1.0.0", estado=estado,
                 gravidade=m.GRAVIDADE_REFORCADA, aberto_em=date(2026, 1, 5), atualizado_em=d, evidencias=(EV,))
    return m.Transicao(a, estado, d, "teste")


def test_cadeia_pura_detecta_adulteracao():
    l1 = rg.montar(_transicao(), None, AGORA)
    l2 = rg.montar(_transicao(m.RESOLVIDO, date(2026, 12, 31)), l1["hash"], AGORA)
    assert rg.verificar([l1, l2]).integra
    l2_mexida = {**l2, "motivo": "outro"}
    st = rg.verificar([l1, l2_mexida])
    assert not st.integra and st.erros


def test_abertos_e_inicio():
    l1 = rg.montar(_transicao(), None, AGORA)
    l2 = rg.montar(_transicao(ativo="BBBB3"), l1["hash"], AGORA)
    l3 = rg.montar(_transicao(m.RESOLVIDO, date(2026, 12, 31)), l2["hash"], AGORA)
    ab = rg.abertos([l1, l2, l3])
    assert list(ab) == ["BBBB3|evento_grave|2026-01-05"]
    # o marco é o fim do mês da primeira gravação, não a data do evento mais velho
    assert rg.inicio_do_registro([l1, l2, l3]) == date(2026, 11, 30)
    assert rg.inicio_do_registro([]) is None


@pytest.fixture
def engine_schema():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        eng = create_engine(_warehouse_url())
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"armazém local indisponível: {exc}")
    schema = f"teste_alertas_{uuid.uuid4().hex[:8]}"
    rg.aplicar_migracao(eng, schema=schema)
    yield eng, schema
    with eng.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_acrescentar_le_e_deduplica(engine_schema):
    eng, schema = engine_schema
    assert rg.acrescentar(eng, [_transicao()], agora=AGORA, schema=schema) == 1
    assert rg.acrescentar(eng, [_transicao()], agora=AGORA, schema=schema) == 0
    linhas = rg.ler(eng, schema=schema)
    assert len(linhas) == 1 and rg.verificar(linhas).integra
    assert linhas[0]["registrado_em"].tzinfo is not None


def test_gatilho_recusa_update_e_delete(engine_schema):
    eng, schema = engine_schema
    rg.acrescentar(eng, [_transicao()], agora=AGORA, schema=schema)
    with pytest.raises(Exception, match="só de inserção"):
        with eng.begin() as c:
            c.execute(text(f'UPDATE "{schema}".registro SET motivo = \'x\''))
    with pytest.raises(Exception, match="só de inserção"):
        with eng.begin() as c:
            c.execute(text(f'DELETE FROM "{schema}".registro'))
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `docker ps --filter name=dfu_warehouse` (se o container não aparecer, suba-o: `docker start dfu_warehouse`; a autorização é permanente) e depois `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_registro.py -q`
Expected: FAIL com `ImportError: cannot import name 'registro'`. Os testes com banco **não** podem terminar em skip: com o armazém fora do ar, a tarefa não está pronta.

- [ ] **Step 4: Escrever `registro.py`**

```python
# core/alertas_ativos/registro.py
"""Registro imutável das transições, no armazém local (088).

A cadeia reusa a forma canônica e o hash da Carteira 20: ``hash`` é o
sha256 da linha sem a chave ``hash``; ``hash_anterior`` liga à linha
anterior. ``registrado_em`` sempre em UTC, no ``montar`` e na leitura,
para o hash recomputado bater.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core import carteira20 as c20
from core.alertas_ativos import modelos as m
from core.destino_local import exigir_local

_SQL = Path(__file__).resolve().parents[2] / "supabase_unificado" / "schema" / "088_alertas_ativos_registro.sql"
_LOCK = 880_088
_CAMPOS = ("chave", "ativo", "tipo", "versao_regra", "estado", "data_evento",
           "motivo", "alerta", "registrado_em", "hash_anterior", "hash")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _para_hash(linha: dict) -> dict:
    d = {k: linha[k] for k in _CAMPOS if k in linha}
    d["data_evento"] = (d["data_evento"].isoformat()
                        if isinstance(d["data_evento"], date) else d["data_evento"])
    d["registrado_em"] = _utc(d["registrado_em"]).isoformat()
    return d


def montar(t: m.Transicao, hash_anterior: str | None, registrado_em: datetime) -> dict:
    linha = {
        "chave": t.alerta.chave, "ativo": t.alerta.ativo, "tipo": t.alerta.tipo,
        "versao_regra": t.alerta.versao_regra, "estado": t.estado,
        "data_evento": t.data_evento, "motivo": t.motivo,
        "alerta": t.alerta.para_dict(), "registrado_em": _utc(registrado_em),
        "hash_anterior": hash_anterior,
    }
    linha["hash"] = c20.calcular_hash(_para_hash(linha))
    return linha


def verificar(linhas: list[dict]) -> c20.StatusCadeia:
    erros, anterior = [], None
    for i, ln in enumerate(linhas):
        if ln.get("hash_anterior") != anterior:
            erros.append(f"linha {i}: hash_anterior não liga à linha anterior")
        if c20.calcular_hash(_para_hash(ln)) != ln.get("hash"):
            erros.append(f"linha {i}: hash não confere com o conteúdo")
        anterior = ln.get("hash")
    return c20.StatusCadeia(integra=not erros, n=len(linhas),
                            ultimo_hash=anterior, erros=tuple(erros))


def aplicar_migracao(engine, schema: str = "alertas_ativos") -> None:
    exigir_local(engine, o_que="alertas_ativos.registro (088)")
    sql = _SQL.read_text(encoding="utf-8")
    if schema != "alertas_ativos":
        sql = sql.replace("alertas_ativos.", f'"{schema}".').replace(
            "CREATE SCHEMA IF NOT EXISTS alertas_ativos", f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    with engine.begin() as c:
        c.exec_driver_sql(sql)


def ler(engine, schema: str = "alertas_ativos") -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(text(
            f'SELECT {", ".join(_CAMPOS)} FROM "{schema}".registro ORDER BY seq')).mappings().all()
    saida = []
    for r in rows:
        d = dict(r)
        d["registrado_em"] = _utc(d["registrado_em"])
        if isinstance(d["alerta"], str):
            d["alerta"] = json.loads(d["alerta"])
        saida.append(d)
    return saida


def acrescentar(engine, transicoes: list[m.Transicao], *, agora: datetime,
                schema: str = "alertas_ativos") -> int:
    if not transicoes:
        return 0
    exigir_local(engine, o_que="alertas_ativos.registro")
    n = 0
    with engine.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK})
        ult = c.execute(text(f'SELECT hash FROM "{schema}".registro ORDER BY seq DESC LIMIT 1')).scalar()
        existentes = {tuple(r) for r in c.execute(text(
            f'SELECT chave, estado, data_evento, versao_regra FROM "{schema}".registro'))}
        for t in transicoes:
            k = (t.alerta.chave, t.estado, t.data_evento, t.alerta.versao_regra)
            if k in existentes:
                continue
            ln = montar(t, ult, agora)
            c.execute(text(
                f'INSERT INTO "{schema}".registro ({", ".join(_CAMPOS)}) VALUES '
                "(:chave, :ativo, :tipo, :versao_regra, :estado, :data_evento, :motivo, "
                "CAST(:alerta AS jsonb), :registrado_em, :hash_anterior, :hash)"),
                {**ln, "alerta": c20.canonico(ln["alerta"])})
            existentes.add(k)
            ult = ln["hash"]
            n += 1
    return n


def abertos(linhas: list[dict]) -> dict[str, dict]:
    ultimo: dict[str, dict] = {}
    for ln in linhas:
        ultimo[ln["chave"]] = ln
    return {k: ln["alerta"] for k, ln in ultimo.items() if ln["estado"] in m.ABERTOS}


def inicio_do_registro(linhas: list[dict]) -> date | None:
    """Fim do mês da primeira gravação: o marco a partir do qual o motor grava a reprodução.

    As observações caem no fim do mês (o mês corrente inclusive), então toda
    transição com ``data_evento`` neste marco ou depois foi vista ao vivo. O que
    é anterior é história reconstruída e não entra no registro, a não ser pelo
    acerto de estado de ``motor.rodar``.
    """
    if not linhas:
        return None
    primeira = min(_utc(ln["registrado_em"]) for ln in linhas).date()
    return primeira.replace(day=calendar.monthrange(primeira.year, primeira.month)[1])
```

Atenção: `alerta` volta do JSONB como dict. `c20.calcular_hash` usa `canonico`, que ordena as chaves, então o dict lido e o montado geram o mesmo hash.

- [ ] **Step 5: Rodar os testes com o armazém no ar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_registro.py -q -rs`
Expected: 4 passed, 0 skipped.

- [ ] **Step 6: Aplicar a 088 no armazém de verdade**

Run: `PYTHONIOENCODING=utf-8 "$PY" -c "from sqlalchemy import create_engine; from scripts.publish_fii_selection_from_local import _warehouse_url; from core.alertas_ativos import registro as rg; rg.aplicar_migracao(create_engine(_warehouse_url())); print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add supabase_unificado/schema/088_alertas_ativos_registro.sql core/alertas_ativos/registro.py tests/test_alertas_registro.py
git commit -m "Alertas: registro só de inserção no armazém local, com cadeia de hashes (088)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---



---

### Task 4: Categorias de evento, metadados do IPE desde 2011 e parecer do auditor

O IPE do armazém começa em 2023 e o backtest B3 precisa de 2011 em diante (spec §8). Esta tarefa:

- ingere só os **metadados** do IPE (categoria, tipo, espécie, assunto, datas e link) a partir dos dados abertos da CVM;
- tira a opinião do auditor dos ZIPs da DFP que já estão em `data/cache/cvm/dfp/`;
- classifica tudo num conjunto único de códigos de evento, que o 8-K dos EUA também usa.

A classificação acontece na leitura, não na gravação. Assim, mudar uma regex não exige reingerir.

**Files:**
- Create: `core/alertas_ativos/categorias.py`
- Create: `supabase_unificado/schema/089_cvm_ipe_metadados.sql`, `supabase_unificado/schema/090_cvm_parecer_auditor.sql`
- Create: `scripts/backfill_ipe_metadados.py`, `scripts/ingerir_parecer_auditor.py`
- Test: `tests/test_alertas_categorias.py`, `tests/test_alertas_ingestao.py`

**Interfaces:**
- Consumes:
  - `core.cvm_ipe.fetch_ipe_csv(year, timeout=60) -> bytes | None`;
  - `core.cvm_ipe.parse_ipe_csv(content) -> list[dict]`, com as chaves `codigo_cvm` (int|None), `categoria`, `tipo`, `especie`, `assunto`, `versao` (str), `data_referencia` (date|None), `data_entrega` (date|None) e `url`;
  - `core.destino_local.exigir_local`.
- Produces:
  - `categorias.RJ`, `REAPRESENTACAO`, `TROCA_CONTROLE`, `CORTE_PROVENTOS`, `AUMENTO_CAPITAL`, `QUESTIONAMENTO`, `PARECER_RESSALVA`, `PARECER_ABSTENCAO`, `PARECER_ADVERSO`, `IMPAIRMENT`, `DESLISTAGEM`, `TROCA_AUDITOR` (str);
  - `categorias.DESCRICAO: dict[str, str]`, `GRAVES: frozenset[str]` e `CANDIDATOS: frozenset[str]`;
  - `categorias.normalizar(s) -> str`;
  - `categorias.eventos_do_ipe(categoria, tipo, especie, assunto) -> tuple[str, ...]`;
  - `categorias.eventos_do_8k(forma, itens) -> tuple[str, ...]`;
  - `categorias.evento_do_parecer(opiniao) -> str | None`;
  - tabelas `market.cvm_ipe_metadados` e `market.cvm_parecer_auditor` no armazém;
  - `backfill_ipe_metadados.anos_pedidos(arg: str, hoje: date) -> list[int]`, `linhas_validas(registros, ano) -> list[dict]` e `gravar_ano(engine, ano, linhas) -> int`;
  - `ingerir_parecer_auditor.pareceres_do_zip(caminho: Path) -> list[dict]`, com as chaves `codigo_cvm`, `dt_refer`, `versao`, `opiniao`, `data_entrega` e `ano_arquivo`.

- [ ] **Step 1: Escrever os testes das categorias**

```python
# tests/test_alertas_categorias.py
"""Classificação de documentos em códigos de evento: B3 (IPE), auditor e 8-K."""
import pytest

from core.alertas_ativos import categorias as cat


@pytest.mark.parametrize("assunto,esperado", [
    ("Pedido de Recuperação Judicial", cat.RJ),
    ("Ajuizamento de pedido de recuperação extrajudicial", cat.RJ),
    ("Decretação de falência da controlada", cat.RJ),
    ("Reapresentação das Demonstrações Financeiras de 2019", cat.REAPRESENTACAO),
    ("Republicação das demonstrações financeiras", cat.REAPRESENTACAO),
    ("Alienação de controle acionário", None),
    ("Alienação do controle da Companhia", cat.TROCA_CONTROLE),
    ("Suspensão do pagamento de dividendos", cat.CORTE_PROVENTOS),
    ("Redução dos juros sobre capital próprio", cat.CORTE_PROVENTOS),
    ("Aumento de capital por subscrição privada", cat.AUMENTO_CAPITAL),
    ("Aumento de capital mediante bonificação", None),
    ("Aumento de capital por capitalização de reservas", None),
    ("Resposta ao Ofício CVM/SEP 123/2020", cat.QUESTIONAMENTO),
    ("Esclarecimentos sobre questionamento da B3", cat.QUESTIONAMENTO),
    ("Pagamento de dividendos intermediários", None),
    ("Calendário de eventos corporativos", None),
])
def test_eventos_do_ipe_pelo_assunto(assunto, esperado):
    evs = cat.eventos_do_ipe("Fato Relevante", "", "", assunto)
    assert (esperado in evs) if esperado else evs == ()


def test_ipe_olha_tipo_e_especie_tambem():
    evs = cat.eventos_do_ipe("Comunicado ao Mercado", "Recuperação Judicial", "", "Atualização")
    assert evs == (cat.RJ,)


def test_ipe_nao_repete_codigo():
    evs = cat.eventos_do_ipe("Fato Relevante", "Recuperação Judicial", "",
                             "Pedido de recuperação judicial")
    assert evs == (cat.RJ,)


def test_questionamento_e_candidato_e_nao_grave():
    assert cat.QUESTIONAMENTO in cat.CANDIDATOS
    assert cat.QUESTIONAMENTO not in cat.GRAVES
    assert cat.RJ in cat.GRAVES and cat.PARECER_ADVERSO in cat.GRAVES


@pytest.mark.parametrize("forma,itens,esperado", [
    ("8-K", "1.03,9.01", (cat.RJ,)),
    ("8-K", "4.01", (cat.TROCA_AUDITOR,)),
    ("8-K/A", "4.02", (cat.REAPRESENTACAO,)),
    ("8-K", "2.06, 5.01", (cat.IMPAIRMENT, cat.TROCA_CONTROLE)),
    ("8-K", "3.01", (cat.DESLISTAGEM,)),
    ("8-K", "2.02,9.01", ()),
    ("8-K", "", ()),
    ("10-K", "1.03", ()),
    (None, None, ()),
])
def test_eventos_do_8k(forma, itens, esperado):
    assert cat.eventos_do_8k(forma, itens) == esperado


@pytest.mark.parametrize("opiniao,esperado", [
    ("adverso", cat.PARECER_ADVERSO),
    ("Negativa de Opinião", cat.PARECER_ABSTENCAO),
    ("abstenção de opinião", cat.PARECER_ABSTENCAO),
    ("com ressalva", cat.PARECER_RESSALVA),
    ("sem ressalva", None),
    ("", None),
    (None, None),
])
def test_evento_do_parecer(opiniao, esperado):
    assert cat.evento_do_parecer(opiniao) == esperado


def test_toda_categoria_tem_descricao():
    assert cat.GRAVES | cat.CANDIDATOS <= set(cat.DESCRICAO)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_categorias.py -q`
Expected: FAIL com `ImportError: cannot import name 'categorias'`.

- [ ] **Step 3: Escrever `categorias.py`**

```python
# core/alertas_ativos/categorias.py
"""Documento oficial → código de evento. Puro e sem I/O.

Um vocabulário só para os três mercados: o IPE da CVM (pelo texto de
categoria, tipo, espécie e assunto), o parecer do auditor da DFP e o item
do 8-K da SEC. QUESTIONAMENTO é candidato: entra como evidência, mas não
abre alerta sozinho (desvio 4 do plano).
"""
from __future__ import annotations

import re
import unicodedata

RJ = "RJ"
REAPRESENTACAO = "REAPRESENTACAO"
TROCA_CONTROLE = "TROCA_CONTROLE"
CORTE_PROVENTOS = "CORTE_PROVENTOS"
AUMENTO_CAPITAL = "AUMENTO_CAPITAL"
QUESTIONAMENTO = "QUESTIONAMENTO"
PARECER_RESSALVA = "PARECER_RESSALVA"
PARECER_ABSTENCAO = "PARECER_ABSTENCAO"
PARECER_ADVERSO = "PARECER_ADVERSO"
IMPAIRMENT = "IMPAIRMENT"
DESLISTAGEM = "DESLISTAGEM"
TROCA_AUDITOR = "TROCA_AUDITOR"

DESCRICAO = {
    RJ: "recuperação judicial, extrajudicial ou falência",
    REAPRESENTACAO: "reapresentação das demonstrações financeiras",
    TROCA_CONTROLE: "troca de controle",
    CORTE_PROVENTOS: "corte ou suspensão de proventos",
    AUMENTO_CAPITAL: "aumento de capital com emissão de ações",
    QUESTIONAMENTO: "questionamento da CVM ou da B3",
    PARECER_RESSALVA: "parecer do auditor com ressalva",
    PARECER_ABSTENCAO: "auditor se absteve de opinar",
    PARECER_ADVERSO: "parecer adverso do auditor",
    IMPAIRMENT: "baixa contábil relevante (impairment)",
    DESLISTAGEM: "aviso de deslistagem",
    TROCA_AUDITOR: "troca de auditor",
}
CANDIDATOS = frozenset({QUESTIONAMENTO})
GRAVES = frozenset(DESCRICAO) - CANDIDATOS


def normalizar(s) -> str:
    """Minúsculas, sem acento e com espaço simples."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", s).strip()


_REGRAS_IPE: tuple[tuple[str, re.Pattern, re.Pattern | None], ...] = (
    (RJ, re.compile(r"recuperacao (judicial|extrajudicial)|falencia"), None),
    (REAPRESENTACAO, re.compile(
        r"reapresenta|refazimento|republica\w* (das |de )?demonstra"), None),
    (TROCA_CONTROLE, re.compile(
        r"(alienacao|transferencia|mudanca|troca) d[eo] controle(?! acionario)"), None),
    (CORTE_PROVENTOS, re.compile(
        r"(suspensao|cancelamento|reducao|corte) d[eao]s? (pagamento d[eo]s? )?"
        r"(dividendos|proventos|juros sobre (o )?capital)"), None),
    (AUMENTO_CAPITAL, re.compile(r"aumento de capital"),
     re.compile(r"bonifica|capitalizacao de (reservas|lucros)|sem emissao")),
    (QUESTIONAMENTO, re.compile(r"oficio.*(cvm|b3|bovespa)|questionamento"), None),
)


def eventos_do_ipe(categoria, tipo, especie, assunto) -> tuple[str, ...]:
    """Códigos de evento de uma entrega do IPE, na ordem de ``_REGRAS_IPE``."""
    texto = " | ".join(normalizar(x) for x in (categoria, tipo, especie, assunto))
    saida = []
    for codigo, sim, nao in _REGRAS_IPE:
        if sim.search(texto) and not (nao and nao.search(texto)):
            saida.append(codigo)
    return tuple(saida)


ITENS_GRAVES_8K = {
    "1.03": RJ,
    "2.06": IMPAIRMENT,
    "3.01": DESLISTAGEM,
    "4.01": TROCA_AUDITOR,
    "4.02": REAPRESENTACAO,
    "5.01": TROCA_CONTROLE,
}


def eventos_do_8k(forma, itens) -> tuple[str, ...]:
    """Itens graves de um 8-K (ou 8-K/A); outras formas não contam."""
    if str(forma or "").strip().upper() not in ("8-K", "8-K/A"):
        return ()
    saida = []
    for item in str(itens or "").split(","):
        codigo = ITENS_GRAVES_8K.get(item.strip())
        if codigo and codigo not in saida:
            saida.append(codigo)
    return tuple(saida)


def evento_do_parecer(opiniao) -> str | None:
    o = normalizar(opiniao)
    if "advers" in o:
        return PARECER_ADVERSO
    if "negativa" in o or "abstenc" in o:
        return PARECER_ABSTENCAO
    if "com ressalva" in o:
        return PARECER_RESSALVA
    return None
```

O lookahead `(?! acionario)` existe porque, no IPE, "Alienação de controle acionário" costuma ser o nome de uma subcategoria genérica usada para participação relevante, e não a venda do controle da companhia. O teste da linha `"Alienação de controle acionário" → None` guarda essa decisão. Se a medição do Step 11 mostrar que isso esconde casos reais, troque a regra e o teste juntos.

- [ ] **Step 4: Rodar os testes das categorias**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_categorias.py -q`
Expected: PASS (34 testes).

- [ ] **Step 5: Escrever o SQL 089 e o 090**

```sql
-- supabase_unificado/schema/089_cvm_ipe_metadados.sql
-- Mora no ARMAZEM LOCAL (dfu_warehouse). Nao aplicar no Supabase.
-- Metadados do IPE da CVM (dados abertos, 2011 em diante), sem o texto do
-- documento. A classificacao em evento acontece na leitura
-- (core.alertas_ativos.categorias), nao aqui.
CREATE TABLE IF NOT EXISTS market.cvm_ipe_metadados (
    codigo_cvm      INTEGER     NOT NULL,
    categoria       TEXT        NOT NULL DEFAULT '',
    tipo            TEXT        NOT NULL DEFAULT '',
    especie         TEXT        NOT NULL DEFAULT '',
    assunto         TEXT        NOT NULL DEFAULT '',
    versao          INTEGER     NOT NULL DEFAULT 1,
    data_referencia DATE,
    data_entrega    DATE        NOT NULL,
    url             TEXT        NOT NULL,
    ano_arquivo     INTEGER     NOT NULL,
    ingerido_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (codigo_cvm, url, versao)
);
CREATE INDEX IF NOT EXISTS ix_cvm_ipe_metadados_entrega
    ON market.cvm_ipe_metadados (codigo_cvm, data_entrega);
CREATE INDEX IF NOT EXISTS ix_cvm_ipe_metadados_ano
    ON market.cvm_ipe_metadados (ano_arquivo);
```

```sql
-- supabase_unificado/schema/090_cvm_parecer_auditor.sql
-- Mora no ARMAZEM LOCAL (dfu_warehouse). Nao aplicar no Supabase.
-- Opiniao do auditor por DFP, tirada de dfp_cia_aberta_parecer_AAAA.csv.
-- data_entrega e o DT_RECEB da versao guardada no ZIP (a CVM guarda so a
-- ultima versao), portanto conservadora.
CREATE TABLE IF NOT EXISTS market.cvm_parecer_auditor (
    codigo_cvm   INTEGER     NOT NULL,
    dt_refer     DATE        NOT NULL,
    versao       INTEGER     NOT NULL,
    opiniao      TEXT        NOT NULL,
    data_entrega DATE        NOT NULL,
    ano_arquivo  INTEGER     NOT NULL,
    ingerido_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (codigo_cvm, dt_refer, versao)
);
CREATE INDEX IF NOT EXISTS ix_cvm_parecer_auditor_entrega
    ON market.cvm_parecer_auditor (codigo_cvm, data_entrega);
```

- [ ] **Step 6: Escrever os testes da ingestão**

```python
# tests/test_alertas_ingestao.py
"""Backfill do IPE e parecer do auditor: funções puras e gravação no armazém."""
import io
import uuid
import zipfile
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from scripts import backfill_ipe_metadados as bf
from scripts import ingerir_parecer_auditor as pa


def test_anos_pedidos():
    hoje = date(2026, 11, 9)
    assert bf.anos_pedidos("recentes", hoje) == [2025, 2026]
    assert bf.anos_pedidos("todos", hoje) == list(range(2011, 2027))
    assert bf.anos_pedidos("2012,2014", hoje) == [2012, 2014]
    with pytest.raises(ValueError):
        bf.anos_pedidos("2010", hoje)


def test_linhas_validas_descarta_sem_chave_e_converte_versao():
    regs = [
        {"codigo_cvm": 1, "url": "u1", "data_entrega": date(2012, 3, 1), "versao": "2",
         "categoria": "Fato Relevante", "tipo": "", "especie": "", "assunto": "x",
         "data_referencia": None},
        {"codigo_cvm": None, "url": "u2", "data_entrega": date(2012, 3, 1), "versao": "1"},
        {"codigo_cvm": 2, "url": "", "data_entrega": date(2012, 3, 1), "versao": "1"},
        {"codigo_cvm": 3, "url": "u3", "data_entrega": None, "versao": "1"},
        {"codigo_cvm": 4, "url": "u4", "data_entrega": date(2012, 3, 1), "versao": ""},
        {"codigo_cvm": 4, "url": "u4", "data_entrega": date(2012, 3, 1), "versao": ""},
    ]
    out = bf.linhas_validas(regs, 2012)
    assert [(r["codigo_cvm"], r["versao"]) for r in out] == [(1, 2), (4, 1)]
    assert all(r["ano_arquivo"] == 2012 for r in out)
    assert out[1]["categoria"] == ""


def _zip_dfp(tmp_path, ano, parecer_linhas, cia_linhas):
    cab_p = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;TP_RELAT_AUD;TP_PARECER_DECL;NUM_ITEM_PARECER_DECL;TXT_PARECER_DECL"
    cab_c = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC"
    caminho = tmp_path / f"dfp_cia_aberta_{ano}.zip"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr(f"dfp_cia_aberta_parecer_{ano}.csv",
                   "\n".join([cab_p, *parecer_linhas]).encode("latin-1"))
        z.writestr(f"dfp_cia_aberta_{ano}.csv",
                   "\n".join([cab_c, *cia_linhas]).encode("latin-1"))
    return caminho


def test_pareceres_do_zip_acha_a_opiniao_nas_duas_colunas(tmp_path):
    caminho = _zip_dfp(tmp_path, 2020, [
        # opinião em TP_RELAT_AUD (layout de 2020)
        "11;2020-12-31;1;Cia A;com ressalva;Relatório do auditor;1;texto",
        "11;2020-12-31;1;Cia A;sem ressalva;Relatório do auditor;2;texto",
        # opinião em TP_PARECER_DECL (layout trocado)
        "22;2020-12-31;2;Cia B;Relatório do auditor;Adverso;1;texto",
        # sem opinião reconhecível: some
        "33;2020-12-31;1;Cia C;Relatório do auditor;Parecer do conselho;1;texto",
        # sem par no arquivo da companhia: some
        "44;2020-12-31;1;Cia D;adverso;;1;texto",
    ], [
        "11;2020-12-31;1;Cia A;901;DFP;1;2021-03-30;l",
        "22;2020-12-31;2;Cia B;902;DFP;2;2021-04-15;l",
        "33;2020-12-31;1;Cia C;903;DFP;3;2021-03-31;l",
    ])
    out = sorted(pa.pareceres_do_zip(caminho), key=lambda r: r["codigo_cvm"])
    assert [(r["codigo_cvm"], r["opiniao"], r["data_entrega"]) for r in out] == [
        (901, "com ressalva", date(2021, 3, 30)),
        (902, "adverso", date(2021, 4, 15)),
    ]
    assert out[0]["dt_refer"] == date(2020, 12, 31) and out[1]["versao"] == 2
    assert all(r["ano_arquivo"] == 2020 for r in out)


def test_zip_sem_arquivo_de_parecer_devolve_vazio(tmp_path):
    caminho = tmp_path / "dfp_cia_aberta_2011.zip"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr("dfp_cia_aberta_2011.csv", "CNPJ_CIA;DT_REFER;VERSAO;CD_CVM;DT_RECEB\n")
    assert pa.pareceres_do_zip(caminho) == []


@pytest.fixture
def engine_schema():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        eng = create_engine(_warehouse_url())
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"armazém local indisponível: {exc}")
    schema = f"teste_ipe_{uuid.uuid4().hex[:8]}"
    yield eng, schema
    with eng.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_gravar_ano_substitui_o_ano_e_recusa_vazio(engine_schema):
    eng, schema = engine_schema
    bf.aplicar_migracao(eng, schema=schema)
    base = {"categoria": "Fato Relevante", "tipo": "", "especie": "", "assunto": "a",
            "data_referencia": None, "data_entrega": date(2012, 3, 1), "ano_arquivo": 2012}
    assert bf.gravar_ano(eng, 2012, [{**base, "codigo_cvm": 1, "url": "u1", "versao": 1},
                                     {**base, "codigo_cvm": 2, "url": "u2", "versao": 1}],
                         schema=schema) == 2
    assert bf.gravar_ano(eng, 2012, [{**base, "codigo_cvm": 3, "url": "u3", "versao": 1}],
                         schema=schema) == 1
    with eng.connect() as c:
        assert c.execute(text(f'SELECT codigo_cvm FROM "{schema}".cvm_ipe_metadados')).scalars().all() == [3]
    with pytest.raises(ValueError, match="vazio"):
        bf.gravar_ano(eng, 2012, [], schema=schema)
    with eng.connect() as c:
        assert c.execute(text(f'SELECT count(*) FROM "{schema}".cvm_ipe_metadados')).scalar() == 1
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_ingestao.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.backfill_ipe_metadados'`.

- [ ] **Step 8: Escrever `scripts/backfill_ipe_metadados.py`**

```python
# scripts/backfill_ipe_metadados.py
"""Metadados do IPE da CVM (2011 em diante) para o armazém local.

Uso:
    python scripts/backfill_ipe_metadados.py --anos todos
    python scripts/backfill_ipe_metadados.py --anos recentes     # rotina noturna
    python scripts/backfill_ipe_metadados.py --anos 2012,2013

Cada ano é substituído numa transação. Download vazio é falha nomeada e não
apaga o que já estava gravado.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine, text  # noqa: E402

from core.destino_local import exigir_local  # noqa: E402

PRIMEIRO_ANO = 2011
_SQL = RAIZ / "supabase_unificado" / "schema" / "089_cvm_ipe_metadados.sql"
_CAMPOS = ("codigo_cvm", "categoria", "tipo", "especie", "assunto", "versao",
           "data_referencia", "data_entrega", "url", "ano_arquivo")


def anos_pedidos(arg: str, hoje: date) -> list[int]:
    arg = str(arg).strip().lower()
    if arg == "todos":
        return list(range(PRIMEIRO_ANO, hoje.year + 1))
    if arg == "recentes":
        return [hoje.year - 1, hoje.year]
    anos = sorted({int(x) for x in arg.split(",") if x.strip()})
    if not anos or anos[0] < PRIMEIRO_ANO or anos[-1] > hoje.year:
        raise ValueError(f"anos fora de {PRIMEIRO_ANO}..{hoje.year}: {arg}")
    return anos


def linhas_validas(registros: list[dict], ano: int) -> list[dict]:
    saida: dict[tuple, dict] = {}
    for r in registros:
        if r.get("codigo_cvm") is None or not r.get("url") or r.get("data_entrega") is None:
            continue
        try:
            versao = int(str(r.get("versao") or "1").strip())
        except ValueError:
            versao = 1
        ln = {
            "codigo_cvm": int(r["codigo_cvm"]),
            "categoria": r.get("categoria") or "",
            "tipo": r.get("tipo") or "",
            "especie": r.get("especie") or "",
            "assunto": r.get("assunto") or "",
            "versao": versao,
            "data_referencia": r.get("data_referencia"),
            "data_entrega": r["data_entrega"],
            "url": r["url"],
            "ano_arquivo": int(ano),
        }
        saida.setdefault((ln["codigo_cvm"], ln["url"], versao), ln)
    return list(saida.values())


def aplicar_migracao(engine, schema: str = "market") -> None:
    exigir_local(engine, o_que="market.cvm_ipe_metadados (089)")
    sql = _SQL.read_text(encoding="utf-8")
    if schema != "market":
        sql = sql.replace("market.", f'"{schema}".')
    with engine.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        c.exec_driver_sql(sql)


def gravar_ano(engine, ano: int, linhas: list[dict], *, schema: str = "market") -> int:
    if not linhas:
        raise ValueError(f"IPE {ano}: download vazio; nada foi apagado")
    exigir_local(engine, o_que="market.cvm_ipe_metadados")
    with engine.begin() as c:
        c.execute(text(f'DELETE FROM "{schema}".cvm_ipe_metadados WHERE ano_arquivo = :a'),
                  {"a": int(ano)})
        c.execute(text(
            f'INSERT INTO "{schema}".cvm_ipe_metadados ({", ".join(_CAMPOS)}) '
            f'VALUES ({", ".join(":" + k for k in _CAMPOS)}) '
            "ON CONFLICT (codigo_cvm, url, versao) DO NOTHING"), linhas)
    return len(linhas)


def main(argv: list[str] | None = None) -> int:
    from core.cvm_ipe import fetch_ipe_csv, parse_ipe_csv
    from scripts.publish_fii_selection_from_local import _warehouse_url

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--anos", default="recentes")
    args = ap.parse_args(argv)
    engine = create_engine(_warehouse_url())
    aplicar_migracao(engine)
    falhas = []
    for ano in anos_pedidos(args.anos, date.today()):
        bruto = fetch_ipe_csv(ano)
        linhas = linhas_validas(parse_ipe_csv(bruto or b""), ano)
        try:
            n = gravar_ano(engine, ano, linhas)
        except ValueError as exc:
            falhas.append(ano)
            print(f"[FALHA] {exc}")
            continue
        print(f"IPE {ano}: {n} entregas")
    if falhas:
        print(f"anos com falha: {falhas}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 9: Escrever `scripts/ingerir_parecer_auditor.py`**

```python
# scripts/ingerir_parecer_auditor.py
"""Opinião do auditor por DFP, tirada dos ZIPs em data/cache/cvm/dfp/.

A coluna da opinião mudou de lugar com os anos: em 2020 ela está em
TP_RELAT_AUD; noutros anos, em TP_PARECER_DECL; nos mais antigos, em
nenhuma. Vale a primeira das duas cujo valor normalizado é uma opinião
conhecida. Ano sem opinião em coluna nenhuma não gera linha, e a cobertura
por ano é impressa.

Uso: python scripts/ingerir_parecer_auditor.py [--dir data/cache/cvm/dfp]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from sqlalchemy import create_engine, text  # noqa: E402

from core.alertas_ativos.categorias import normalizar  # noqa: E402
from core.destino_local import exigir_local  # noqa: E402

_SQL = RAIZ / "supabase_unificado" / "schema" / "090_cvm_parecer_auditor.sql"
_OPINIOES = {"sem ressalva", "com ressalva", "negativa de opiniao", "abstencao de opiniao",
             "abstencao", "adverso", "adversa"}
_SEVERIDADE = {"adverso": 4, "adversa": 4, "negativa de opiniao": 3,
               "abstencao de opiniao": 3, "abstencao": 3, "com ressalva": 2, "sem ressalva": 1}
_CAMPOS = ("codigo_cvm", "dt_refer", "versao", "opiniao", "data_entrega", "ano_arquivo")


def _csv(z: zipfile.ZipFile, nome: str) -> list[dict]:
    with z.open(nome) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";"))


def _data(s) -> date | None:
    try:
        return date.fromisoformat(str(s or "").strip()[:10])
    except ValueError:
        return None


def pareceres_do_zip(caminho: Path) -> list[dict]:
    ano = int(re.search(r"(\d{4})", Path(caminho).name).group(1))
    with zipfile.ZipFile(caminho) as z:
        nomes = z.namelist()
        n_par = next((n for n in nomes if "parecer" in n.lower()), None)
        n_cia = next((n for n in nomes if n.lower() == f"dfp_cia_aberta_{ano}.csv"), None)
        if not n_par or not n_cia:
            return []
        par, cia = _csv(z, n_par), _csv(z, n_cia)
    chave = lambda r: (r.get("CNPJ_CIA"), r.get("DT_REFER"), r.get("VERSAO"))  # noqa: E731
    entrega = {chave(r): r for r in cia}
    melhor: dict[tuple, dict] = {}
    for r in par:
        opiniao = next((normalizar(r.get(c)) for c in ("TP_RELAT_AUD", "TP_PARECER_DECL")
                        if normalizar(r.get(c)) in _OPINIOES), None)
        base = entrega.get(chave(r))
        if opiniao is None or base is None:
            continue
        try:
            cd = int(str(base.get("CD_CVM")).strip())
            versao = int(str(r.get("VERSAO")).strip())
        except (TypeError, ValueError):
            continue
        dt_refer, receb = _data(r.get("DT_REFER")), _data(base.get("DT_RECEB"))
        if dt_refer is None or receb is None:
            continue
        k = (cd, dt_refer, versao)
        atual = melhor.get(k)
        if atual is None or _SEVERIDADE[opiniao] > _SEVERIDADE[atual["opiniao"]]:
            melhor[k] = {"codigo_cvm": cd, "dt_refer": dt_refer, "versao": versao,
                         "opiniao": opiniao, "data_entrega": receb, "ano_arquivo": ano}
    return list(melhor.values())


def aplicar_migracao(engine) -> None:
    exigir_local(engine, o_que="market.cvm_parecer_auditor (090)")
    with engine.begin() as c:
        c.exec_driver_sql(_SQL.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    from scripts.publish_fii_selection_from_local import _warehouse_url

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=str(RAIZ / "data" / "cache" / "cvm" / "dfp"))
    args = ap.parse_args(argv)
    engine = create_engine(_warehouse_url())
    aplicar_migracao(engine)
    zips = sorted(Path(args.dir).glob("dfp_cia_aberta_*.zip"))
    if not zips:
        print(f"[FALHA] nenhum ZIP da DFP em {args.dir}")
        return 1
    for zp in zips:
        linhas = pareceres_do_zip(zp)
        ano = int(re.search(r"(\d{4})", zp.name).group(1))
        with engine.begin() as c:
            if linhas:
                c.execute(text("DELETE FROM market.cvm_parecer_auditor WHERE ano_arquivo = :a"),
                          {"a": ano})
                c.execute(text(
                    f"INSERT INTO market.cvm_parecer_auditor ({', '.join(_CAMPOS)}) "
                    f"VALUES ({', '.join(':' + k for k in _CAMPOS)})"), linhas)
        graves = sum(1 for ln in linhas if ln["opiniao"] != "sem ressalva")
        print(f"DFP {ano}: {len(linhas)} pareceres com opinião, {graves} fora de 'sem ressalva'"
              + ("" if linhas else " (ano sem campo de opinião)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 10: Rodar os testes da ingestão**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_ingestao.py tests/test_alertas_categorias.py -q -rs`
Expected: PASS, 0 skipped. Com o armazém fora do ar, `docker start dfu_warehouse` e rode de novo.

- [ ] **Step 11: Rodar os dois backfills de verdade e medir**

```bash
PYTHONIOENCODING=utf-8 "$PY" scripts/backfill_ipe_metadados.py --anos todos
PYTHONIOENCODING=utf-8 "$PY" scripts/ingerir_parecer_auditor.py
```

Expected: uma linha por ano, de 2011 a 2026, com milhares de entregas e nenhum `[FALHA]`. O parecer sai com contagem nos anos que têm o campo; nos outros, a mensagem "ano sem campo de opinião".

Depois, meça a cobertura dos eventos por ano. Os números entram em `docs/alertas_ativos/teste_pit_fatia1.md` na Task 7:

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
from collections import Counter
from sqlalchemy import create_engine, text
from scripts.publish_fii_selection_from_local import _warehouse_url
from core.alertas_ativos import categorias as cat
e = create_engine(_warehouse_url())
with e.connect() as c:
    rows = c.execute(text("SELECT ano_arquivo, categoria, tipo, especie, assunto FROM market.cvm_ipe_metadados")).all()
    par = c.execute(text("SELECT ano_arquivo, opiniao, count(*) FROM market.cvm_parecer_auditor GROUP BY 1, 2 ORDER BY 1, 2")).all()
cont = Counter((r[0], ev) for r in rows for ev in cat.eventos_do_ipe(*r[1:]))
for ano in sorted({a for a, _ in cont}):
    print(ano, {ev: n for (a, ev), n in sorted(cont.items()) if a == ano})
print(par)
EOF
```

Critério: todo ano de 2012 a 2021 precisa ter pelo menos um RJ e uma REAPRESENTACAO. Se um ano vier sem, olhe uma amostra de `assunto` desse ano (`SELECT assunto FROM market.cvm_ipe_metadados WHERE ano_arquivo = <ano> AND assunto ILIKE '%recupera%' LIMIT 20`). Corrija a regex e acrescente o caso real (sem nome de empresa) ao `parametrize` do Step 1 antes de seguir.

- [ ] **Step 12: Commit**

```bash
git add core/alertas_ativos/categorias.py supabase_unificado/schema/089_cvm_ipe_metadados.sql supabase_unificado/schema/090_cvm_parecer_auditor.sql scripts/backfill_ipe_metadados.py scripts/ingerir_parecer_auditor.py tests/test_alertas_categorias.py tests/test_alertas_ingestao.py
git commit -m "Alertas: categorias de evento, metadados do IPE desde 2011 (089) e parecer do auditor (090)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Histórico da SEC (8-K) no armazém, com as empresas que saíram da bolsa

`market_us.sec_filings` não existe no armazém (spec §8), e o coletor atual só lê `filings.recent`. Isso dá cerca de mil entregas por CIK, e para empresa grande cobre menos de três anos. O histórico mais antigo fica nas páginas de `filings.files`, no mesmo formato colunar. O universo do coletor (`company_snapshots` com `is_active`) também deixa de fora quem saiu da bolsa. Para o backtest, o modo histórico junta `market_us.delistings`.

**Files:**
- Modify: `data_pipeline/us/edgar.py` (constante perto da linha 42 e método logo depois de `submissions`, linha ~314)
- Modify: `data_pipeline/us/sec_filings.py:163-166` (universo) e `:199-244` (`coletar`)
- Modify: `run_us_ingest.py` (argparse do subcomando `filings` e ramo da linha ~370)
- Test: `tests/test_alertas_sec_historico.py`

**Interfaces:**
- Consumes: `sec_filings.linhas_de_recentes(recentes, cik, *, desde) -> list[dict]`, `sec_filings.simbolos_por_cik(pares, somente)` e `provider.submissions(cik) -> dict | None`.
- Produces:
  - `EdgarProvider.submissions_arquivo(nome: str) -> dict | None`, que levanta `ValueError` para nome fora de `CIK\d{10}-submissions-\d{3}\.json`;
  - `sec_filings.blocos_de_submissions(sub: dict, provider, *, historico: bool) -> list[dict]`;
  - `sec_filings.coletar(..., historico: bool = False)`. Com `historico=True`, `desde = date(2001, 1, 1)` e o universo inclui `market_us.delistings`;
  - `python run_us_ingest.py filings --historico`.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_alertas_sec_historico.py
"""Histórico da SEC: páginas de filings.files e universo com deslistadas."""
from datetime import date

import pytest

from data_pipeline.us import sec_filings as sf


def _bloco(acessos, formas, datas, itens):
    return {"accessionNumber": acessos, "form": formas, "filingDate": datas,
            "reportDate": datas, "items": itens,
            "primaryDocument": ["d.htm"] * len(acessos),
            "primaryDocDescription": [""] * len(acessos)}


class _Prov:
    def __init__(self, paginas):
        self.paginas, self.pedidos = paginas, []

    def submissions_arquivo(self, nome):
        self.pedidos.append(nome)
        return self.paginas.get(nome)


SUB = {"filings": {
    "recent": _bloco(["0001-24-1"], ["8-K"], ["2024-05-01"], ["1.03"]),
    "files": [{"name": "CIK0000000001-submissions-001.json"},
              {"name": "CIK0000000001-submissions-002.json"}],
}}
PAGINAS = {
    "CIK0000000001-submissions-001.json": _bloco(["0001-15-1"], ["8-K"], ["2015-02-01"], ["4.02"]),
    "CIK0000000001-submissions-002.json": None,
}


def test_sem_historico_so_le_recent():
    prov = _Prov(PAGINAS)
    blocos = sf.blocos_de_submissions(SUB, prov, historico=False)
    assert len(blocos) == 1 and prov.pedidos == []


def test_historico_le_as_paginas_e_ignora_pagina_vazia():
    prov = _Prov(PAGINAS)
    blocos = sf.blocos_de_submissions(SUB, prov, historico=True)
    assert len(blocos) == 2
    assert prov.pedidos == list(PAGINAS)
    linhas = [ln for b in blocos for ln in sf.linhas_de_recentes(b, "1", desde=date(2001, 1, 1))]
    assert sorted(ln["filing_date"] for ln in linhas) == ["2015-02-01", "2024-05-01"]


def test_submissions_arquivo_recusa_nome_fora_do_padrao():
    from data_pipeline.us.edgar import EdgarProvider
    prov = EdgarProvider.__new__(EdgarProvider)
    prov._get = lambda url: {"url": url}
    assert prov.submissions_arquivo("CIK0000000001-submissions-001.json") == {
        "url": "https://data.sec.gov/submissions/CIK0000000001-submissions-001.json"}
    for ruim in ("../x.json", "CIK1-submissions-001.json", "https://evil/CIK0000000001-submissions-001.json"):
        with pytest.raises(ValueError):
            prov.submissions_arquivo(ruim)


def test_universo_historico_inclui_deslistadas():
    assert "market_us.delistings" in sf.sql_universo(historico=True)
    assert "market_us.delistings" not in sf.sql_universo(historico=False)
```

Antes de rodar, confira o formato real de `linhas_de_recentes` (`data_pipeline/us/sec_filings.py:117`). Se `filing_date` vier como `date` e não como string, troque o `assert` do segundo teste para comparar com `date(2015, 2, 1)` e `date(2024, 5, 1)`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_sec_historico.py -q`
Expected: FAIL com `AttributeError: module ... has no attribute 'blocos_de_submissions'`.

- [ ] **Step 3: `submissions_arquivo` no provedor**

Em `data_pipeline/us/edgar.py`, acrescente `import re` aos imports do topo, junto de `import logging`. Logo abaixo de `SUBMISSIONS_URL = ...` (linha 42), acrescente:

```python
SUBMISSIONS_ARQUIVO_URL = "https://data.sec.gov/submissions/{nome}"
_NOME_ARQUIVO = re.compile(r"CIK\d{10}-submissions-\d{3}\.json")
```

Logo depois do método `submissions` (termina na linha ~314), acrescente:

```python
    def submissions_arquivo(self, nome: str) -> dict | None:
        """Página antiga de ``filings.files`` (mesmo formato colunar do ``recent``).

        O nome vem da resposta da SEC; validá-lo impede que um campo inesperado
        vire requisição para outro caminho ou outro host."""
        if not _NOME_ARQUIVO.fullmatch(str(nome or "")):
            raise ValueError(f"nome de página da SEC fora do padrão: {nome!r}")
        return self._get(SUBMISSIONS_ARQUIVO_URL.format(nome=nome))
```

- [ ] **Step 4: Blocos, universo e `coletar(historico=...)`**

Em `data_pipeline/us/sec_filings.py`, troque o bloco de `_SQL_UNIVERSO` (linhas 163–166) por:

```python
_SQL_UNIVERSO = """
    SELECT symbol, cik FROM market_us.company_snapshots
     WHERE is_active IS TRUE AND cik IS NOT NULL AND symbol IS NOT NULL
"""
#: Para o backtest: quem saiu da bolsa também entra, senão o teste só vê
#: sobreviventes. ``delistings.cik`` é numérico; o resto do código espera texto.
_SQL_UNIVERSO_DESLISTADAS = """
    UNION
    SELECT symbol, cik::text FROM market_us.delistings
     WHERE cik IS NOT NULL AND symbol IS NOT NULL AND delisted_date >= DATE '2011-01-01'
"""


def sql_universo(*, historico: bool) -> str:
    return _SQL_UNIVERSO + (_SQL_UNIVERSO_DESLISTADAS if historico else "")


def blocos_de_submissions(sub: dict, provider, *, historico: bool) -> list[dict]:
    """``filings.recent`` e, no modo histórico, cada página de ``filings.files``.

    Página que volta vazia (404) é pulada; erro de rede sobe, para o CIK
    entrar na contagem de falhas de ``coletar``."""
    filings = (sub or {}).get("filings") or {}
    blocos = [filings.get("recent") or {}]
    if historico:
        for arq in filings.get("files") or []:
            pagina = provider.submissions_arquivo(arq.get("name"))
            if pagina:
                blocos.append(pagina)
    return blocos
```

Em `coletar`, mude a assinatura e três trechos:

```python
def coletar(provider, engine, *, tickers: Iterable[str] | None = None,
            hoje: date | None = None, janela_dias: int = JANELA_DIAS,
            aplicar_migracao: bool = True, historico: bool = False) -> dict:
```

```python
    hoje = hoje or date.today()
    desde = date(2001, 1, 1) if historico else hoje - timedelta(days=int(janela_dias))
```

```python
        pares = [(r[0], r[1]) for r in conn.execute(text(sql_universo(historico=historico)))]
```

```python
        entregas = [e for bloco in blocos_de_submissions(sub, provider, historico=historico)
                    for e in linhas_de_recentes(bloco, cik, desde=desde)]
```

O último trecho substitui as duas linhas `recentes = ...` e `entregas = linhas_de_recentes(recentes, ...)`. Ponha a chamada a `blocos_de_submissions` dentro do `try` que já protege `provider.submissions(cik)`, para que uma página com erro conte como falha do CIK e não derrube a coleta:

```python
        try:
            sub = provider.submissions(cik)
            blocos = blocos_de_submissions(sub, provider, historico=historico) if sub else []
        except Exception as exc:  # noqa: BLE001 - um CIK não derruba os outros
            falhas += 1
            logger.warning("filings %s (%s) falhou: %s", cik, ",".join(simbolos), exc)
            continue
        if not sub:
            sem_resposta += 1
            continue
        entregas = [e for bloco in blocos for e in linhas_de_recentes(bloco, cik, desde=desde)]
```

- [ ] **Step 5: Flag no `run_us_ingest.py`**

No subparser do comando `filings` (perto das linhas 130–162), acrescente:

```python
    p_filings.add_argument("--historico", action="store_true",
                           help="lê também as páginas antigas da SEC e as empresas deslistadas (backtest)")
```

Use o nome real da variável do subparser: procure com `grep -n '"filings"' run_us_ingest.py`. No ramo da linha ~370, passe a flag:

```python
        return out(sec_filings.coletar(build_edgar_provider(args.budget), engine,
                                       tickers=args.tickers, historico=args.historico))
```

- [ ] **Step 6: Rodar os testes novos e os antigos da SEC**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_sec_historico.py tests/test_us_sec_filings.py tests/test_us_edgar_provider.py -q`
Expected: PASS. Os testes antigos de `coletar` usam provedor falso sem `submissions_arquivo`, e continuam passando porque `historico=False` não chama o método.

- [ ] **Step 7: Coletar o histórico no armazém**

A flag `--warehouse` (`run_us_ingest.py:160`) aponta a gravação para o armazém local; sem ela o comando grava no Supabase, que está acima do limite. A coleta leva cerca de 16 mil CIKs a 10 requisições por segundo, então rode em segundo plano (`run_in_background`), sem `| tail`:

```bash
PYTHONIOENCODING=utf-8 "$PY" run_us_ingest.py filings --warehouse --historico --budget 200000 > "$TMPDIR/sec_historico.log" 2>&1
```

Expected: JSON final com `"ok": true` e `"desde": "2001-01-01"`. Depois, meça:

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
from sqlalchemy import create_engine, text
from scripts.publish_fii_selection_from_local import _warehouse_url
from core.alertas_ativos import categorias as cat
e = create_engine(_warehouse_url())
with e.connect() as c:
    rows = c.execute(text("SELECT extract(year FROM filing_date::date)::int, form, items FROM market_us.sec_filings WHERE form IN ('8-K','8-K/A')")).all()
from collections import Counter
cont = Counter((a, ev) for a, f, i in rows for ev in cat.eventos_do_8k(f, i))
for ano in sorted({a for a, _ in cont}):
    print(ano, {ev: n for (a, ev), n in sorted(cont.items()) if a == ano})
EOF
```

Critério: de 2012 a 2021, todo ano tem 8-K com item 1.03 e 4.02. Anote a contagem por ano para o relatório da Task 7.

- [ ] **Step 8: Commit**

```bash
git add data_pipeline/us/edgar.py data_pipeline/us/sec_filings.py run_us_ingest.py tests/test_alertas_sec_historico.py
git commit -m "SEC: coleta histórica das páginas de filings.files e das empresas deslistadas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 6: Leitores PIT e regras dos três tipos de risco

**Files:**
- Create: `core/alertas_ativos/dados_pit.py`, `core/alertas_ativos/regras_risco.py`
- Test: `tests/test_alertas_dados_pit.py`, `tests/test_alertas_regras_risco.py`

**Interfaces:**
- Consumes:
  - `core.alertas_ativos.modelos`: `Evidencia`, `Observacao`, `RegraTipo`, `Alerta`, `TIPO_EVENTO_GRAVE`, `TIPO_DETERIORACAO`, `TIPO_FII_RENDA`, `ROTULO_TIPO` (Tarefa 2).
  - `core.alertas_ativos.categorias`: `GRAVES`, `CANDIDATOS`, `DESCRICAO`, `CORTE_PROVENTOS`, `eventos_do_ipe`, `eventos_do_8k`, `evento_do_parecer` (Tarefa 4).
  - `core.inteligencia_ativos.eliminatorios as el`: `vacancia_critica`, `inadimplencia_critica`, `patrimonio_nao_positivo` (Tarefa 1).
  - Tabelas `market.cvm_ipe_metadados`, `market.cvm_parecer_auditor` (Tarefa 4) e `market_us.sec_filings` (Tarefa 5).
- Produces:
  - `dados_pit`:
    - colunas `COLS_PRECOS = ["ativo", "mes", "preco", "volume_fin"]`, `COLS_FUND`, `COLS_DOCS`, `COLS_FII_M`, `COLS_FII_T`;
    - `UNIVERSOS = ("b3_acoes", "b3_fii", "us")`;
    - `Painel(universo, precos, fundamentos, documentos, fii_mensal, fii_trimestral, tipo_fii)`, uma dataclass `frozen=True, eq=False`;
    - funções puras: `fim_de_mes(df)`, `descartar_saltos(df, teto=10.0)`, `disponibilidade_b3(df) -> pd.Series`, `ttm(df)`, `q4_derivado(trimestrais, anuais)`, `primeira_safra(obs)`, `fii_mensal_de(obs)`, `fii_trimestral_de(obs)`, `documentos_do_ipe(df)`, `documentos_do_parecer(df)`, `documentos_do_8k(df)`;
    - leitor: `carregar_painel(engine, universo) -> Painel`.
  - `regras_risco`:
    - `Parametros(limiar_preco=0.10, limiar_corte=0.15)`, uma dataclass frozen;
    - `PADRAO = Parametros()`, `GRADE: dict[str, tuple[float, ...]]` e `grade() -> list[Parametros]`;
    - `REGRAS: dict[str, RegraTipo]`, `TIPOS_DO_UNIVERSO: dict[str, tuple[str, ...]]`, `ORCAMENTO_RARIDADE = 0.15`;
    - `Contexto` e `contexto(painel, params) -> Contexto`;
    - `observacoes(tipo, ativo, ctx) -> list[Observacao]`;
    - `seis_perguntas(alerta, ctx, d) -> tuple[tuple[str, str], ...]`;
    - `proximo_documento(universo, d) -> tuple[str, date]`, `texto_proximo_documento(universo, d) -> str`;
    - `HIPOTESE: dict[str, str]`, `invalidaria(tipo, params) -> str`.

As funções puras recebem DataFrames montados no teste, de modo que nada nesta tarefa precisa do banco, a não ser `carregar_painel`. O passo 9 o exercita contra o armazém real.

- [ ] **Step 1: Escrever os testes dos leitores (funções puras)**

```python
# tests/test_alertas_dados_pit.py
"""Funções puras dos leitores PIT. Tickers fictícios, valores sintéticos."""
from datetime import date

import pandas as pd
import pytest

from core.alertas_ativos import categorias as cat
from core.alertas_ativos import dados_pit as dp


def test_fim_de_mes_leva_ao_ultimo_dia_e_fica_com_a_ultima_linha():
    df = pd.DataFrame({
        "ativo": ["AAAA3", "AAAA3", "AAAA3"],
        "data": [date(2020, 3, 1), date(2020, 3, 20), date(2020, 4, 1)],
        "preco": [10.0, 11.0, 12.0],
        "volume_fin": [1.0, 2.0, 3.0],
    })
    out = dp.fim_de_mes(df)
    assert list(out["mes"]) == [date(2020, 3, 31), date(2020, 4, 30)]
    assert list(out["preco"]) == [11.0, 12.0]
    assert list(out.columns) == dp.COLS_PRECOS


def test_descartar_saltos_tira_o_mes_que_vai_e_volta():
    meses = [date(2020, m, 28) for m in range(1, 6)]
    df = pd.DataFrame({"ativo": "AAAA3", "mes": meses,
                       "preco": [10.0, 10.5, 2_000.0, 10.4, 10.6], "volume_fin": 1.0})
    out = dp.descartar_saltos(df)
    assert 2_000.0 not in set(out["preco"])
    assert len(out) == 4


def test_descartar_saltos_mantem_grupamento_que_nao_volta():
    meses = [date(2020, m, 28) for m in range(1, 5)]
    df = pd.DataFrame({"ativo": "AAAA3", "mes": meses,
                       "preco": [1.0, 1.1, 50.0, 51.0], "volume_fin": 1.0})
    assert len(dp.descartar_saltos(df)) == 4


def test_disponibilidade_b3_itr_45_dias_e_dfp_pela_publicacao():
    df = pd.DataFrame({
        "trimestre": [1, 4, 4],
        "fim_periodo": [date(2020, 3, 31), date(2020, 12, 31), date(2021, 12, 31)],
        "dfp_disponivel_em": [None, date(2021, 3, 10), None],
    })
    out = list(dp.disponibilidade_b3(df).dt.date)
    assert out == [date(2020, 5, 15), date(2021, 3, 10), date(2022, 3, 31)]


def _trimestres(ativo, inicio_ano, n, receita, lucro, patrimonio=100.0, pular=None):
    linhas = []
    ano, tri = inicio_ano, 1
    for i in range(n):
        if pular != (ano, tri):
            fim = pd.Timestamp(year=ano, month=3 * tri, day=1) + pd.offsets.MonthEnd(0)
            linhas.append({"ativo": ativo, "ano": ano, "trimestre": tri,
                           "fim_periodo": fim.date(),
                           "disponivel_em": (fim + pd.Timedelta(days=45)).date(),
                           "receita": receita[i], "lucro": lucro[i],
                           "patrimonio": patrimonio})
        tri += 1
        if tri == 5:
            ano, tri = ano + 1, 1
    return pd.DataFrame(linhas)


def test_ttm_soma_quatro_consecutivos_e_traz_o_ano_anterior():
    df = _trimestres("AAAA3", 2019, 8, receita=[10] * 4 + [8] * 4, lucro=[2] * 4 + [1] * 4)
    out = dp.ttm(df).set_index("fim_periodo")
    ult = out.loc[date(2020, 12, 31)]
    assert ult["receita_ttm"] == 32 and ult["receita_ttm_aa"] == 40
    assert ult["lucro_ttm"] == 4 and ult["lucro_ttm_aa"] == 8
    assert pd.isna(out.loc[date(2019, 9, 30), "receita_ttm"])
    assert ult["disponivel_em"] == date(2021, 2, 14)
    assert list(dp.ttm(df).columns) == dp.COLS_FUND


def test_ttm_com_buraco_nao_soma():
    df = _trimestres("AAAA3", 2019, 6, receita=[10] * 6, lucro=[1] * 6, pular=(2019, 3))
    out = dp.ttm(df).set_index("fim_periodo")
    assert pd.isna(out.loc[date(2020, 3, 31), "receita_ttm"])
    assert out.loc[date(2020, 3, 31), "patrimonio"] == 100.0


def test_ttm_valor_ausente_nao_vira_zero():
    df = _trimestres("AAAA3", 2019, 4, receita=[10, None, 10, 10], lucro=[1, 1, 1, 1])
    out = dp.ttm(df)
    assert out["receita_ttm"].isna().all()
    assert out["lucro_ttm"].iloc[-1] == 4


def test_q4_derivado_subtrai_os_tres_trimestres_e_herda_o_10k():
    tri = _trimestres("ZZZ", 2020, 3, receita=[10, 20, 30], lucro=[1, 2, 3])
    anu = pd.DataFrame([{"ativo": "ZZZ", "ano": 2020, "trimestre": 0,
                         "fim_periodo": date(2020, 12, 31), "disponivel_em": date(2021, 2, 20),
                         "receita": 100.0, "lucro": 10.0, "patrimonio": 55.0}])
    out = dp.q4_derivado(tri, anu)
    q4 = out[out["trimestre"] == 4].iloc[0]
    assert q4["receita"] == 40 and q4["lucro"] == 4 and q4["patrimonio"] == 55.0
    assert q4["disponivel_em"] == date(2021, 2, 20)
    assert len(out) == 4


def test_q4_derivado_sem_os_tres_trimestres_nao_deriva():
    tri = _trimestres("ZZZ", 2020, 2, receita=[10, 20], lucro=[1, 2])
    anu = pd.DataFrame([{"ativo": "ZZZ", "ano": 2020, "trimestre": 0,
                         "fim_periodo": date(2020, 12, 31), "disponivel_em": date(2021, 2, 20),
                         "receita": 100.0, "lucro": 10.0, "patrimonio": 55.0}])
    assert (dp.q4_derivado(tri, anu)["trimestre"] == 4).sum() == 0


def _obs_fii(linhas):
    return pd.DataFrame(linhas, columns=["ativo", "metrica", "referencia", "disponivel_em", "valor"])


def test_primeira_safra_fica_com_o_que_se_sabia_primeiro():
    obs = _obs_fii([
        ("BBBB11", "nav_per_share", date(2020, 1, 31), date(2020, 2, 15), 100.0),
        ("BBBB11", "nav_per_share", date(2020, 1, 31), date(2020, 6, 1), 90.0),
    ])
    assert list(dp.primeira_safra(obs)["valor"]) == [100.0]


def test_fii_mensal_rendimento_e_dy_vezes_vpa():
    obs = _obs_fii([
        ("BBBB11", "nav_per_share", date(2020, 1, 31), date(2020, 2, 15), 100.0),
        ("BBBB11", "dy_patrimonial_mes", date(2020, 1, 31), date(2020, 2, 20), 0.008),
    ])
    out = dp.fii_mensal_de(obs).iloc[0]
    assert out["rendimento"] == pytest.approx(0.8)
    assert out["vpa"] == 100.0
    assert out["disponivel_em"] == date(2020, 2, 20)


def test_fii_mensal_sem_dy_deixa_rendimento_ausente():
    obs = _obs_fii([("BBBB11", "nav_per_share", date(2020, 1, 31), date(2020, 2, 15), 100.0)])
    assert pd.isna(dp.fii_mensal_de(obs).iloc[0]["rendimento"])


def test_fii_trimestral_converte_fracao_em_porcentagem():
    obs = _obs_fii([
        ("BBBB11", "vacancia_fisica", date(2020, 3, 31), date(2020, 5, 15), 0.12),
        ("BBBB11", "delinquency", date(2020, 3, 31), date(2020, 5, 15), 0.03),
    ])
    out = dp.fii_trimestral_de(obs).iloc[0]
    assert out["vacancia"] == pytest.approx(12.0)
    assert out["inadimplencia"] == pytest.approx(3.0)


def test_documentos_do_ipe_classifica_e_fica_com_a_primeira_versao():
    df = pd.DataFrame([
        {"ativo": "AAAA3", "disponivel_em": date(2020, 5, 2), "categoria": "Fato Relevante",
         "tipo": "", "especie": "", "assunto": "Pedido de Recuperação Judicial", "url": "u1"},
        {"ativo": "AAAA3", "disponivel_em": date(2020, 5, 9), "categoria": "Fato Relevante",
         "tipo": "", "especie": "", "assunto": "Pedido de Recuperação Judicial", "url": "u1"},
        {"ativo": "AAAA3", "disponivel_em": date(2020, 6, 1), "categoria": "Comunicado",
         "tipo": "", "especie": "", "assunto": "Agenda de eventos", "url": "u2"},
    ])
    out = dp.documentos_do_ipe(df)
    assert list(out["evento"]) == [cat.RJ]
    assert out.iloc[0]["disponivel_em"] == date(2020, 5, 2)
    assert out.iloc[0]["fonte"] == "CVM/IPE"
    assert list(out.columns) == dp.COLS_DOCS


def test_documentos_do_parecer_ignora_sem_ressalva():
    df = pd.DataFrame([
        {"ativo": "AAAA3", "dt_refer": date(2019, 12, 31), "disponivel_em": date(2020, 3, 30),
         "opiniao": "Com Ressalva"},
        {"ativo": "AAAA3", "dt_refer": date(2019, 12, 31), "disponivel_em": date(2020, 7, 1),
         "opiniao": "Com Ressalva"},
        {"ativo": "BBBB3", "dt_refer": date(2019, 12, 31), "disponivel_em": date(2020, 3, 30),
         "opiniao": "Sem Ressalva"},
    ])
    out = dp.documentos_do_parecer(df)
    assert list(out["evento"]) == [cat.PARECER_RESSALVA]
    assert out.iloc[0]["disponivel_em"] == date(2020, 3, 30)


def test_documentos_do_8k_so_itens_graves():
    df = pd.DataFrame([
        {"ativo": "ZZZ", "disponivel_em": date(2020, 5, 2), "form": "8-K", "items": "1.03,9.01",
         "url": "https://www.sec.gov/x"},
        {"ativo": "ZZZ", "disponivel_em": date(2020, 6, 2), "form": "8-K", "items": "2.02",
         "url": "https://www.sec.gov/y"},
        {"ativo": "ZZZ", "disponivel_em": date(2020, 7, 2), "form": "10-Q", "items": "1.03",
         "url": "https://www.sec.gov/z"},
    ])
    out = dp.documentos_do_8k(df)
    assert list(out["evento"]) == [cat.RJ]
    assert out.iloc[0]["fonte"] == "SEC/8-K"


def test_universo_desconhecido_recusa():
    with pytest.raises(ValueError, match="universo"):
        dp.carregar_painel(None, "marte")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_dados_pit.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.alertas_ativos.dados_pit'`.

- [ ] **Step 3: Escrever `dados_pit.py`**

```python
# core/alertas_ativos/dados_pit.py
"""Leitores PIT do armazém local para os alertas.

Toda linha devolvida traz a data em que o dado ficou disponível
(``disponivel_em``). O motor só olha, numa data ``d``, as linhas com
``disponivel_em <= d``. As funções puras (sem banco) ficam separadas para que
os testes as exercitem com DataFrames sintéticos. ``carregar_painel`` é o único
ponto que lê o armazém.

Ausência é ``None``/``NaN``, nunca zero: uma soma TTM com um trimestre ausente
fica ausente.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import text

from core.alertas_ativos import categorias as cat

PRAZO_ITR_DIAS = 45   # prazo legal; a data real de entrega do ITR não está ingerida
PRAZO_DFP_DIAS = 90   # reserva quando a DFP não está em cvm_filing_publications
SALTO_MAXIMO = 10.0

COLS_PRECOS = ["ativo", "mes", "preco", "volume_fin"]
COLS_FUND = ["ativo", "fim_periodo", "disponivel_em", "receita_ttm", "lucro_ttm",
             "receita_ttm_aa", "lucro_ttm_aa", "patrimonio", "patrimonio_ant"]
COLS_DOCS = ["ativo", "disponivel_em", "evento", "descricao", "fonte", "url"]
COLS_FII_M = ["ativo", "referencia", "disponivel_em", "rendimento", "vpa"]
COLS_FII_T = ["ativo", "referencia", "disponivel_em", "vacancia", "inadimplencia"]
_COLS_TRIM = ["ativo", "ano", "trimestre", "fim_periodo", "disponivel_em",
              "receita", "lucro", "patrimonio"]

UNIVERSOS = ("b3_acoes", "b3_fii", "us")
METRICAS_FII_MENSAL = ("nav_per_share", "dy_patrimonial_mes")
METRICAS_FII_TRIMESTRAL = ("vacancia_fisica", "delinquency")
FONTE_FII_MENSAL = "cvm_informe_mensal"
FONTE_FII_TRIMESTRAL = "cvm_informe_trimestral"


def _vazio(cols) -> pd.DataFrame:
    return pd.DataFrame(columns=cols)


@dataclass(frozen=True, eq=False)
class Painel:
    """Tudo o que as regras de um universo leem. ``eq=False`` mantém o hash por identidade."""
    universo: str
    precos: pd.DataFrame
    fundamentos: pd.DataFrame = field(default_factory=lambda: _vazio(COLS_FUND))
    documentos: pd.DataFrame = field(default_factory=lambda: _vazio(COLS_DOCS))
    fii_mensal: pd.DataFrame = field(default_factory=lambda: _vazio(COLS_FII_M))
    fii_trimestral: pd.DataFrame = field(default_factory=lambda: _vazio(COLS_FII_T))
    tipo_fii: dict = field(default_factory=dict)


# ---------------------------------------------------------------- preços

def fim_de_mes(df: pd.DataFrame) -> pd.DataFrame:
    """[ativo, data, preco, volume_fin] -> uma linha por (ativo, mês), a última do mês.

    ``mes`` é o último dia do mês. A B3 grava o mês no dia 1; levar ao fim do
    mês nunca antecipa o dado (se o dia 1 fosse o fechamento do próprio dia 1,
    a troca só o atrasaria).
    """
    if df.empty:
        return _vazio(COLS_PRECOS)
    d = df.copy()
    d["data"] = pd.to_datetime(d["data"])
    d["mes"] = (d["data"] + pd.offsets.MonthEnd(0)).dt.date
    d = d.sort_values(["ativo", "data"]).drop_duplicates(["ativo", "mes"], keep="last")
    return d[COLS_PRECOS].reset_index(drop=True)


def descartar_saltos(df: pd.DataFrame, teto: float = SALTO_MAXIMO) -> pd.DataFrame:
    """Tira o mês que multiplica (ou divide) o preço por mais de ``teto`` e volta no mês seguinte.

    É retroajuste mal feito da fonte (preço bilionário de um mês só), não
    movimento de mercado. Um salto que não volta fica: pode ser grupamento.
    """
    if df.empty:
        return df
    d = df.sort_values(["ativo", "mes"]).reset_index(drop=True)
    g = d.groupby("ativo")["preco"]
    ant, prox = g.shift(1), g.shift(-1)
    sobe = (d["preco"] / ant > teto) & (prox / d["preco"] < 1 / teto)
    desce = (d["preco"] / ant < 1 / teto) & (prox / d["preco"] > teto)
    return d.loc[~(sobe | desce)].reset_index(drop=True)


# ---------------------------------------------------------------- fundamentos

def disponibilidade_b3(df: pd.DataFrame) -> pd.Series:
    """Data em que o trimestre da B3 ficou disponível.

    Q1–Q3: fim do período + 45 dias (ITR). Q4: ``disponivel_em`` da DFP em
    ``cvm_filing_publications``, porque o Q4 discreto é DFP menos nove meses.
    Sem a DFP, fim do período + 90 dias. ``disponivel_em`` é a data da versão
    guardada, que pode ser a de uma reapresentação: isso atrasa, nunca antecipa.
    """
    fim = pd.to_datetime(df["fim_periodo"])
    itr = fim + pd.Timedelta(days=PRAZO_ITR_DIAS)
    dfp = pd.to_datetime(df["dfp_disponivel_em"]).fillna(fim + pd.Timedelta(days=PRAZO_DFP_DIAS))
    return dfp.where(df["trimestre"].astype(int) == 4, itr)


def ttm(df: pd.DataFrame) -> pd.DataFrame:
    """Trimestres discretos -> soma móvel de quatro trimestres consecutivos.

    Entrada: ``[ativo, ano, trimestre, fim_periodo, disponivel_em, receita, lucro, patrimonio]``.
    A linha TTM fica disponível quando o último dos quatro fica: ``disponivel_em``
    é o máximo da janela. Janela com buraco não soma, e valor ausente deixa a
    soma ausente. ``*_aa`` é o TTM de quatro trimestres antes e ``patrimonio_ant``
    é o do trimestre anterior. A linha sem TTM continua, para o patrimônio.
    """
    if df.empty:
        return _vazio(COLS_FUND)
    d = (df.sort_values(["ativo", "ano", "trimestre", "disponivel_em"])
           .drop_duplicates(["ativo", "ano", "trimestre"], keep="last").copy())
    d["idx"] = d["ano"].astype(int) * 4 + d["trimestre"].astype(int)
    d["disponivel_em"] = pd.to_datetime(d["disponivel_em"])
    d["receita"] = pd.to_numeric(d["receita"], errors="coerce")
    d["lucro"] = pd.to_numeric(d["lucro"], errors="coerce")
    d["patrimonio"] = pd.to_numeric(d["patrimonio"], errors="coerce")
    partes = []
    for ativo, g in d.groupby("ativo", sort=False):
        g = g.reset_index(drop=True)
        consecutivo = g["idx"].diff(3) == 3
        ns = g["disponivel_em"].astype("int64")
        disp_janela = pd.to_datetime(ns.rolling(4, min_periods=4).max())
        t = pd.DataFrame({
            "ativo": ativo,
            "idx": g["idx"],
            "fim_periodo": g["fim_periodo"],
            "disponivel_em": disp_janela.where(consecutivo, g["disponivel_em"]),
            "receita_ttm": g["receita"].rolling(4, min_periods=4).sum().where(consecutivo),
            "lucro_ttm": g["lucro"].rolling(4, min_periods=4).sum().where(consecutivo),
            "patrimonio": g["patrimonio"],
        })
        por_idx = t.set_index("idx")
        t["receita_ttm_aa"] = (t["idx"] - 4).map(por_idx["receita_ttm"])
        t["lucro_ttm_aa"] = (t["idx"] - 4).map(por_idx["lucro_ttm"])
        t["patrimonio_ant"] = (t["idx"] - 1).map(por_idx["patrimonio"])
        partes.append(t)
    out = pd.concat(partes, ignore_index=True)
    out["disponivel_em"] = out["disponivel_em"].dt.date
    return out[COLS_FUND]


def q4_derivado(trimestrais: pd.DataFrame, anuais: pd.DataFrame) -> pd.DataFrame:
    """EUA: o armazém só tem Q1–Q3 discretos. Q4 = anual − (Q1+Q2+Q3).

    O Q4 fica disponível quando o 10-K fica (``disponivel_em`` do anual) e usa o
    patrimônio do balanço anual. Só deriva quando os três trimestres existem.
    """
    tri = trimestrais[_COLS_TRIM].copy()
    if tri.empty or anuais.empty:
        return tri.reset_index(drop=True)
    soma = (tri.groupby(["ativo", "ano"])
               .agg(n=("trimestre", "nunique"),
                    receita=("receita", lambda s: s.sum(min_count=3)),
                    lucro=("lucro", lambda s: s.sum(min_count=3)))
               .reset_index())
    soma = soma[soma["n"] == 3]
    a = anuais.merge(soma, on=["ativo", "ano"], suffixes=("", "_9m"))
    q4 = pd.DataFrame({
        "ativo": a["ativo"], "ano": a["ano"], "trimestre": 4,
        "fim_periodo": a["fim_periodo"], "disponivel_em": a["disponivel_em"],
        "receita": a["receita"] - a["receita_9m"], "lucro": a["lucro"] - a["lucro_9m"],
        "patrimonio": a["patrimonio"],
    })
    return pd.concat([tri, q4], ignore_index=True)


# ---------------------------------------------------------------- FII

def primeira_safra(obs: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (ativo, métrica, referência): a de menor ``disponivel_em``.

    É o que se sabia na data. Correção posterior não reescreve o passado.
    """
    if obs.empty:
        return obs
    return (obs.dropna(subset=["valor"])
               .sort_values(["ativo", "metrica", "referencia", "disponivel_em"])
               .drop_duplicates(["ativo", "metrica", "referencia"], keep="first")
               .reset_index(drop=True))


def _pivo(obs: pd.DataFrame, metricas) -> pd.DataFrame:
    o = primeira_safra(obs)
    o = o[o["metrica"].isin(metricas)]
    if o.empty:
        return pd.DataFrame(columns=["ativo", "referencia", "disponivel_em", *metricas])
    w = (o.pivot_table(index=["ativo", "referencia"], columns="metrica",
                       values="valor", aggfunc="first")
          .reindex(columns=list(metricas)))
    w["disponivel_em"] = o.groupby(["ativo", "referencia"])["disponivel_em"].max()
    return w.reset_index()


def fii_mensal_de(obs: pd.DataFrame) -> pd.DataFrame:
    """Rendimento por cota = DY patrimonial do mês (fração) × VPA (R$).

    Amortização não está ingerida e pode parecer corte; o card avisa.
    """
    w = _pivo(obs, METRICAS_FII_MENSAL)
    w["rendimento"] = w["dy_patrimonial_mes"] * w["nav_per_share"]
    w["vpa"] = w["nav_per_share"]
    return w[COLS_FII_M].reset_index(drop=True)


def fii_trimestral_de(obs: pd.DataFrame) -> pd.DataFrame:
    """Vacância e inadimplência do informe trimestral, de fração para %."""
    w = _pivo(obs, METRICAS_FII_TRIMESTRAL)
    w["vacancia"] = w["vacancia_fisica"] * 100
    w["inadimplencia"] = w["delinquency"] * 100
    return w[COLS_FII_T].reset_index(drop=True)


# ---------------------------------------------------------------- documentos

def _docs(linhas: list[dict]) -> pd.DataFrame:
    if not linhas:
        return _vazio(COLS_DOCS)
    out = pd.DataFrame(linhas)
    out = (out.sort_values(["ativo", "chave", "evento", "disponivel_em"])
              .drop_duplicates(["ativo", "chave", "evento"], keep="first"))
    return out.sort_values(["ativo", "disponivel_em"])[COLS_DOCS].reset_index(drop=True)


def documentos_do_ipe(df: pd.DataFrame) -> pd.DataFrame:
    """Entregas do IPE -> um documento por (ativo, url, evento), na primeira entrega."""
    linhas = []
    for r in df.itertuples(index=False):
        for ev in cat.eventos_do_ipe(r.categoria, r.tipo, r.especie, r.assunto):
            linhas.append({"ativo": r.ativo, "chave": r.url, "disponivel_em": r.disponivel_em,
                           "evento": ev, "descricao": cat.DESCRICAO[ev],
                           "fonte": "CVM/IPE", "url": r.url})
    return _docs(linhas)


def documentos_do_parecer(df: pd.DataFrame) -> pd.DataFrame:
    """Parecer do auditor -> documento na primeira entrega de cada exercício."""
    linhas = []
    for r in df.itertuples(index=False):
        ev = cat.evento_do_parecer(r.opiniao)
        if ev:
            linhas.append({"ativo": r.ativo, "chave": str(r.dt_refer),
                           "disponivel_em": r.disponivel_em, "evento": ev,
                           "descricao": cat.DESCRICAO[ev], "fonte": "CVM/DFP parecer",
                           "url": None})
    return _docs(linhas)


def documentos_do_8k(df: pd.DataFrame) -> pd.DataFrame:
    """8-K da SEC -> um documento por item grave."""
    linhas = []
    for r in df.itertuples(index=False):
        for ev in cat.eventos_do_8k(r.form, r.items):
            linhas.append({"ativo": r.ativo, "chave": r.url, "disponivel_em": r.disponivel_em,
                           "evento": ev, "descricao": cat.DESCRICAO[ev],
                           "fonte": "SEC/8-K", "url": r.url})
    return _docs(linhas)


# ---------------------------------------------------------------- leitura

_SQL_PRECOS_B3 = """
SELECT h.ticker AS ativo, h.date AS data, h.adjusted_close AS preco,
       h.close * h.volume AS volume_fin
FROM market.historical_prices h
WHERE h.adjusted_close > 0
  AND {filtro} EXISTS (SELECT 1 FROM market.fiis f WHERE f.ticker = h.ticker)
"""
_SQL_PRECOS_US = """
SELECT symbol AS ativo, month_end AS data, adjusted_close AS preco, close * volume AS volume_fin
FROM market_us.prices_monthly WHERE adjusted_close > 0
"""
_SQL_FUND_B3 = """
SELECT i.ticker AS ativo, i.year AS ano, i.quarter AS trimestre, i.period_end_date AS fim_periodo,
       i.revenue AS receita, i.net_income AS lucro, b.equity AS patrimonio,
       p.disponivel_em AS dfp_disponivel_em
FROM market.income_statements i
LEFT JOIN market.balance_sheets b
       ON b.ticker = i.ticker AND b.period = i.period AND b.year = i.year AND b.quarter = i.quarter
LEFT JOIN market.ticker_cvm t ON t.ticker = i.ticker
LEFT JOIN market.cvm_filing_publications p
       ON p.codigo_cvm = t.codigo_cvm AND p.exercicio = i.year AND p.categoria = 'DFP'
WHERE i.period = 'quarterly'
"""
_SQL_FUND_US = """
SELECT i.symbol AS ativo, i.fiscal_year AS ano, i.fiscal_quarter AS trimestre,
       i.period AS periodo, i.reference_date AS fim_periodo, i.available_at::date AS disponivel_em,
       i.revenue AS receita, i.net_income AS lucro, b.total_equity AS patrimonio
FROM market_us.income_statements i
LEFT JOIN market_us.balance_sheets b
       ON b.symbol = i.symbol AND b.period = i.period
      AND b.fiscal_year = i.fiscal_year AND b.fiscal_quarter = i.fiscal_quarter
WHERE i.period IN ('quarterly', 'annual') AND i.available_at IS NOT NULL
"""
_SQL_FII = """
SELECT ticker AS ativo, metric_name AS metrica, reference_date AS referencia,
       available_at::date AS disponivel_em, value_numeric AS valor
FROM market.fii_metric_observations
WHERE source = :fonte AND metric_name = ANY(:metricas) AND value_numeric IS NOT NULL
"""
_SQL_IPE = """
SELECT t.ticker AS ativo, m.data_entrega AS disponivel_em, m.categoria, m.tipo, m.especie,
       m.assunto, m.url
FROM market.cvm_ipe_metadados m JOIN market.ticker_cvm t ON t.codigo_cvm = m.codigo_cvm
"""
_SQL_PARECER = """
SELECT t.ticker AS ativo, p.dt_refer, p.data_entrega AS disponivel_em, p.opiniao
FROM market.cvm_parecer_auditor p JOIN market.ticker_cvm t ON t.codigo_cvm = p.codigo_cvm
"""
_SQL_8K = """
SELECT symbol AS ativo, filing_date AS disponivel_em, form, items, url
FROM market_us.sec_filings WHERE form IN ('8-K', '8-K/A')
"""


def _existe(engine, tabela: str) -> bool:
    with engine.connect() as c:
        return c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": tabela}).scalar()


def _ler(engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(text(sql), c, params=params)


def _precos(engine, sql: str) -> pd.DataFrame:
    return descartar_saltos(fim_de_mes(_ler(engine, sql)))


def _fundamentos_b3(engine) -> pd.DataFrame:
    df = _ler(engine, _SQL_FUND_B3)
    if df.empty:
        return _vazio(COLS_FUND)
    df["disponivel_em"] = disponibilidade_b3(df).dt.date
    return ttm(df[_COLS_TRIM])


def _fundamentos_us(engine) -> pd.DataFrame:
    df = _ler(engine, _SQL_FUND_US)
    if df.empty:
        return _vazio(COLS_FUND)
    tri = df[(df["periodo"] == "quarterly") & df["trimestre"].isin([1, 2, 3])]
    anu = df[df["periodo"] == "annual"]
    return ttm(q4_derivado(tri, anu))


def _documentos_b3(engine) -> pd.DataFrame:
    partes = []
    if _existe(engine, "market.cvm_ipe_metadados"):
        partes.append(documentos_do_ipe(_ler(engine, _SQL_IPE)))
    if _existe(engine, "market.cvm_parecer_auditor"):
        partes.append(documentos_do_parecer(_ler(engine, _SQL_PARECER)))
    partes = [p for p in partes if not p.empty]
    if not partes:
        return _vazio(COLS_DOCS)
    return pd.concat(partes, ignore_index=True).sort_values(["ativo", "disponivel_em"])


def carregar_painel(engine, universo: str) -> Painel:
    """Lê do armazém local tudo o que as regras do universo usam.

    Tabela que ainda não existe devolve quadro vazio; o motor marca a fonte
    como velha pelo frescor, e o alerta que depende dela fica suspenso.
    """
    if universo not in UNIVERSOS:
        raise ValueError(f"universo desconhecido: {universo!r}; use um de {UNIVERSOS}")
    if universo == "b3_acoes":
        return Painel(universo, precos=_precos(engine, _SQL_PRECOS_B3.format(filtro="NOT")),
                      fundamentos=_fundamentos_b3(engine), documentos=_documentos_b3(engine))
    if universo == "us":
        docs = (documentos_do_8k(_ler(engine, _SQL_8K))
                if _existe(engine, "market_us.sec_filings") else _vazio(COLS_DOCS))
        return Painel(universo, precos=_precos(engine, _SQL_PRECOS_US),
                      fundamentos=_fundamentos_us(engine), documentos=docs)
    mensal = _ler(engine, _SQL_FII, fonte=FONTE_FII_MENSAL, metricas=list(METRICAS_FII_MENSAL))
    trimestral = _ler(engine, _SQL_FII, fonte=FONTE_FII_TRIMESTRAL,
                      metricas=list(METRICAS_FII_TRIMESTRAL))
    tipos = _ler(engine, "SELECT ticker, tipo FROM market.fiis")
    return Painel(universo, precos=_precos(engine, _SQL_PRECOS_B3.format(filtro="")),
                  fii_mensal=fii_mensal_de(mensal), fii_trimestral=fii_trimestral_de(trimestral),
                  tipo_fii={r.ticker: r.tipo for r in tipos.itertuples(index=False)})
```

- [ ] **Step 4: Rodar os testes dos leitores**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_dados_pit.py -q`
Expected: PASS (17 testes).

- [ ] **Step 5: Escrever os testes das regras**

```python
# tests/test_alertas_regras_risco.py
"""Regras dos três tipos de risco sobre painéis sintéticos. Tickers fictícios."""
from datetime import date

import pandas as pd
import pytest

from core.alertas_ativos import categorias as cat
from core.alertas_ativos import dados_pit as dp
from core.alertas_ativos import modelos as m
from core.alertas_ativos import regras_risco as rr


def _meses(inicio: date, n: int) -> list[date]:
    return [(pd.Timestamp(inicio) + pd.offsets.MonthEnd(i)).date() for i in range(n)]


MESES = _meses(date(2019, 1, 31), 36)


def _precos(series: dict[str, list[float]]) -> pd.DataFrame:
    linhas = []
    for ativo, valores in series.items():
        for mes, p in zip(MESES, valores):
            linhas.append({"ativo": ativo, "mes": mes, "preco": p, "volume_fin": 1e6})
    return pd.DataFrame(linhas, columns=dp.COLS_PRECOS)


PLANO = [100.0] * 36
QUEDA = [100.0] * 18 + [70.0] * 18           # cai 30% no 19º mês e fica
UNIVERSO = {"BBBB3": PLANO, "CCCC3": PLANO, "DDDD3": PLANO}


def _fund(ativo, linhas):
    return pd.DataFrame([{"ativo": ativo, **x} for x in linhas], columns=dp.COLS_FUND)


def _f(fim, disp, rec, luc, rec_aa, luc_aa, pl=100.0, pl_ant=100.0):
    return {"fim_periodo": fim, "disponivel_em": disp, "receita_ttm": rec, "lucro_ttm": luc,
            "receita_ttm_aa": rec_aa, "lucro_ttm_aa": luc_aa,
            "patrimonio": pl, "patrimonio_ant": pl_ant}


PIORA = [
    _f(date(2020, 3, 31), date(2020, 5, 15), 90, 9, 100, 10),
    _f(date(2020, 6, 30), date(2020, 8, 14), 85, 8, 100, 10),
]


def _ctx(painel, params=rr.PADRAO):
    return rr.contexto(painel, params)


def _obs_em(obs, d):
    return next(o for o in obs if o.data == d)


# ------------------------------------------------------------- constantes

def test_regras_dos_tres_tipos():
    assert set(rr.REGRAS) == {m.TIPO_EVENTO_GRAVE, m.TIPO_DETERIORACAO, m.TIPO_FII_RENDA}
    assert rr.REGRAS[m.TIPO_EVENTO_GRAVE].resolve_so_no_horizonte is True
    assert all(r.horizonte_dias == 365 for r in rr.REGRAS.values())
    assert rr.TIPOS_DO_UNIVERSO["b3_fii"] == (m.TIPO_FII_RENDA,)


def test_grade_e_o_produto_e_contem_o_padrao():
    g = rr.grade()
    assert len(g) == len(rr.GRADE["limiar_preco"]) * len(rr.GRADE["limiar_corte"])
    assert rr.PADRAO in g


# ------------------------------------------------------------- deterioração

def test_deterioracao_exige_dois_trimestres_ruins_e_preco_pior_que_o_mercado():
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": QUEDA, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", PIORA))
    obs = rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel))
    antes = _obs_em(obs, date(2020, 7, 31))      # só um trimestre ruim conhecido
    depois = _obs_em(obs, date(2020, 8, 31))     # o segundo ficou disponível em 14/08
    assert antes.presente is False
    assert depois.presente is True
    assert {e.familia for e in depois.evidencias} == {"fundamento", "preco"}
    assert depois.preco == 70.0


def test_deterioracao_sem_queda_relativa_nao_dispara():
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": PLANO, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", PIORA))
    obs = rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel))
    assert not any(o.presente for o in obs)


def test_deterioracao_nao_olha_o_futuro():
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": QUEDA, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", PIORA))
    obs = rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel))
    for o in obs:
        assert all(e.data <= o.data for e in o.evidencias)


def test_pl_que_fica_negativo_dispara_sozinho():
    linhas = [_f(date(2020, 3, 31), date(2020, 5, 15), 100, 10, 100, 10, pl=-5.0, pl_ant=20.0)]
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": PLANO, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", linhas))
    o = _obs_em(rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel)), date(2020, 5, 31))
    assert o.presente is True
    assert [e.codigo for e in o.evidencias] == ["PL_NAO_POSITIVO"]


def test_desfeita_e_none_sem_fundamento_e_true_quando_o_trimestre_melhora():
    linhas = PIORA + [_f(date(2020, 9, 30), date(2020, 11, 14), 105, 11, 100, 10)]
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": QUEDA, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", linhas))
    obs = rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel))
    assert _obs_em(obs, date(2020, 1, 31)).desfeita is None
    assert _obs_em(obs, date(2020, 8, 31)).desfeita is False
    assert _obs_em(obs, date(2020, 11, 30)).desfeita is True


def test_contraria_quando_lucro_sobe_mais_de_10_e_preco_acompanha():
    linhas = [_f(date(2020, 3, 31), date(2020, 5, 15), 120, 12, 100, 10)]
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": PLANO, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", linhas))
    o = _obs_em(rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel)), date(2020, 5, 31))
    assert o.contraria is True


def test_limiar_preco_da_grade_muda_o_disparo():
    leve = [100.0] * 18 + [93.0] * 18          # cai 7%
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": leve, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", PIORA))
    frouxo = rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel, rr.Parametros(0.05, 0.15)))
    duro = rr.observacoes(m.TIPO_DETERIORACAO, "AAAA3", _ctx(painel, rr.Parametros(0.10, 0.15)))
    assert any(o.presente for o in frouxo)
    assert not any(o.presente for o in duro)


# ------------------------------------------------------------- evento grave

def _docs(linhas):
    return pd.DataFrame(linhas, columns=dp.COLS_DOCS)


def test_evento_grave_abre_no_mes_da_entrega_e_so_nele():
    docs = _docs([("AAAA3", date(2020, 5, 12), cat.RJ, cat.DESCRICAO[cat.RJ], "CVM/IPE", "u")])
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": PLANO, **UNIVERSO}), documentos=docs)
    obs = rr.observacoes(m.TIPO_EVENTO_GRAVE, "AAAA3", _ctx(painel))
    presentes = [o.data for o in obs if o.presente]
    assert presentes == [date(2020, 5, 31)]
    ev = _obs_em(obs, date(2020, 5, 31)).evidencias[0]
    assert (ev.familia, ev.codigo, ev.data, ev.url) == ("documento", cat.RJ, date(2020, 5, 12), "u")


def test_questionamento_e_so_evidencia():
    docs = _docs([("AAAA3", date(2020, 5, 12), cat.QUESTIONAMENTO,
                   cat.DESCRICAO[cat.QUESTIONAMENTO], "CVM/IPE", "u")])
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": PLANO, **UNIVERSO}), documentos=docs)
    o = _obs_em(rr.observacoes(m.TIPO_EVENTO_GRAVE, "AAAA3", _ctx(painel)), date(2020, 5, 31))
    assert o.presente is False
    assert [e.codigo for e in o.evidencias] == [cat.QUESTIONAMENTO]


def test_evento_grave_ganha_evidencia_de_preco_na_queda_de_3_meses():
    docs = _docs([("AAAA3", date(2020, 6, 12), cat.RJ, cat.DESCRICAO[cat.RJ], "CVM/IPE", "u")])
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": QUEDA, **UNIVERSO}), documentos=docs)
    o = _obs_em(rr.observacoes(m.TIPO_EVENTO_GRAVE, "AAAA3", _ctx(painel)), date(2020, 7, 31))
    assert "preco" in {e.familia for e in o.evidencias}


# ------------------------------------------------------------- FII

def _fii_m(ativo, rendimentos, vpas):
    linhas = []
    for mes, r, v in zip(MESES, rendimentos, vpas):
        disp = (pd.Timestamp(mes) + pd.Timedelta(days=15)).date()
        linhas.append({"ativo": ativo, "referencia": mes, "disponivel_em": disp,
                       "rendimento": r, "vpa": v})
    return pd.DataFrame(linhas, columns=dp.COLS_FII_M)


def _fii_t(ativo, linhas):
    return pd.DataFrame([{"ativo": ativo, **x} for x in linhas], columns=dp.COLS_FII_T)


def _painel_fii(rend, vpa, tipo="tijolo", trimestral=None):
    return dp.Painel("b3_fii", precos=_precos({"BBBB11": PLANO, "CCCC11": PLANO}),
                     fii_mensal=_fii_m("BBBB11", rend, vpa),
                     fii_trimestral=trimestral if trimestral is not None else dp._vazio(dp.COLS_FII_T),
                     tipo_fii={"BBBB11": tipo})


def test_corte_de_rendimento_dispara_quando_o_mes_fica_disponivel():
    rend = [1.0] * 12 + [0.80] + [0.80] * 23
    painel = _painel_fii(rend, [100.0] * 36)
    obs = rr.observacoes(m.TIPO_FII_RENDA, "BBBB11", _ctx(painel))
    # referência 31/12/2019 fica disponível em 15/01/2020
    assert _obs_em(obs, date(2019, 12, 31)).presente is False
    o = _obs_em(obs, date(2020, 1, 31))
    assert o.presente is True
    assert [e.codigo for e in o.evidencias] == ["CORTE_RENDIMENTO"]


def test_corte_menor_que_o_limiar_nao_dispara():
    rend = [1.0] * 12 + [0.90] * 24
    obs = rr.observacoes(m.TIPO_FII_RENDA, "BBBB11", _ctx(_painel_fii(rend, [100.0] * 36)))
    assert not any(o.presente for o in obs)


def test_vpa_caindo_com_vacancia_subindo_dispara_no_tijolo_e_nao_no_papel():
    vpa = [100.0] * 12 + [99.0, 98.0, 97.0] + [97.0] * 21
    tri = _fii_t("BBBB11", [
        {"referencia": date(2019, 9, 30), "disponivel_em": date(2019, 11, 15),
         "vacancia": 5.0, "inadimplencia": 0.0},
        {"referencia": date(2019, 12, 31), "disponivel_em": date(2020, 2, 14),
         "vacancia": 9.0, "inadimplencia": 0.0},
    ])
    tijolo = rr.observacoes(m.TIPO_FII_RENDA, "BBBB11",
                            _ctx(_painel_fii([1.0] * 36, vpa, "tijolo", tri)))
    papel = rr.observacoes(m.TIPO_FII_RENDA, "BBBB11",
                           _ctx(_painel_fii([1.0] * 36, vpa, "papel", tri)))
    o = _obs_em(tijolo, date(2020, 3, 31))
    assert o.presente is True
    assert {e.codigo for e in o.evidencias} == {"VPA_CAI", "VACANCIA_SOBE"}
    assert not any(x.presente for x in papel)


def test_fii_sem_tipo_e_tratado_como_tijolo():
    painel = _painel_fii([1.0] * 36, [100.0] * 36, tipo=None)
    assert rr._tipo_fii(painel, "BBBB11") == "tijolo"


def test_fii_desfeita_quando_rendimento_volta():
    rend = [1.0] * 12 + [0.80] * 3 + [1.0] * 21
    obs = rr.observacoes(m.TIPO_FII_RENDA, "BBBB11", _ctx(_painel_fii(rend, [100.0] * 36)))
    assert _obs_em(obs, date(2020, 1, 31)).desfeita is False
    assert _obs_em(obs, date(2020, 4, 30)).desfeita is True


# ------------------------------------------------------------- card

def _alerta(tipo, ativo="AAAA3", aberto=date(2020, 8, 31), evs=()):
    return m.Alerta(ativo=ativo, tipo=tipo, versao_regra="1.0.0", estado=m.NOVO,
                    gravidade=m.GRAVIDADE_BASE, aberto_em=aberto, atualizado_em=aberto,
                    evidencias=tuple(evs))


def test_seis_perguntas_na_ordem_da_spec_e_com_respostas_validas():
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": QUEDA, **UNIVERSO}),
                       fundamentos=_fund("AAAA3", PIORA))
    resp = rr.seis_perguntas(_alerta(m.TIPO_DETERIORACAO), _ctx(painel), date(2020, 8, 31))
    assert [p for p, _ in resp] == list(rr.PERGUNTAS)
    assert all(r in rr.RESPOSTAS[i] for i, (_, r) in enumerate(resp))
    d = dict(resp)
    assert d[rr.PERGUNTAS[0]] == "não"           # lucro −20% contra preço −30%
    assert d[rr.PERGUNTAS[1]] == "do ativo"
    assert d[rr.PERGUNTAS[2]] == "não"
    assert d[rr.PERGUNTAS[3]] == "sim"           # PL positivo
    assert d[rr.PERGUNTAS[4]] == "sem dado"      # ação sem série PIT de proventos
    assert d[rr.PERGUNTAS[5]] == "sim"           # deterioração exige dois trimestres


def test_seis_perguntas_sem_dado_nunca_vira_nao():
    painel = dp.Painel("b3_acoes", precos=_precos({"AAAA3": PLANO, **UNIVERSO}))
    d = dict(rr.seis_perguntas(_alerta(m.TIPO_EVENTO_GRAVE), _ctx(painel), date(2020, 5, 31)))
    assert d[rr.PERGUNTAS[0]] == "sem dado"
    assert d[rr.PERGUNTAS[2]] == "sem dado"      # painel sem fonte de documentos
    assert d[rr.PERGUNTAS[3]] == "sem dado"


def test_pergunta_de_proventos_do_fii_responde_pelo_corte():
    rend = [1.0] * 12 + [0.80] * 24
    painel = _painel_fii(rend, [100.0] * 36)
    d = dict(rr.seis_perguntas(_alerta(m.TIPO_FII_RENDA, "BBBB11", date(2020, 1, 31)),
                               _ctx(painel), date(2020, 1, 31)))
    assert d[rr.PERGUNTAS[4]] == "sim"


@pytest.mark.parametrize("universo,d,esperado", [
    ("b3_acoes", date(2026, 1, 10), ("DFP", date(2026, 3, 31))),
    ("b3_acoes", date(2026, 4, 1), ("ITR", date(2026, 5, 15))),
    ("b3_acoes", date(2026, 11, 14), ("DFP", date(2027, 3, 31))),
    ("b3_fii", date(2026, 11, 9), ("informe mensal", date(2026, 12, 15))),
    ("us", date(2026, 11, 20), ("10-K", date(2027, 3, 31))),
    ("us", date(2026, 4, 1), ("10-Q", date(2026, 5, 15))),
])
def test_proximo_documento(universo, d, esperado):
    assert rr.proximo_documento(universo, d) == esperado


def test_texto_proximo_documento():
    assert rr.texto_proximo_documento("b3_acoes", date(2026, 4, 1)) == "próximo ITR em 15/05"


def test_todo_tipo_tem_hipotese_e_invalidacao():
    for tipo in rr.REGRAS:
        assert rr.HIPOTESE[tipo]
        assert rr.invalidaria(tipo, rr.PADRAO)
    assert "92,5%" in rr.invalidaria(m.TIPO_FII_RENDA, rr.PADRAO)
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_regras_risco.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.alertas_ativos.regras_risco'`.

- [ ] **Step 7: Escrever `regras_risco.py`**

```python
# core/alertas_ativos/regras_risco.py
"""Regras dos três tipos de risco da fatia 1.

Cada regra transforma o painel PIT de um universo numa sequência mensal de
``Observacao`` por ativo. A máquina de estados (``ciclo_vida.avancar``) decide
o que vira alerta. Aqui não há estado: só o que era verdade em cada data,
olhando apenas linhas com ``disponivel_em <= d``.

Os limiares que a calibração pode escolher estão em ``GRADE``; o resto é
constante declarada. A LLM não participa de nada disto.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from core.alertas_ativos import categorias as cat
from core.alertas_ativos import modelos as m
from core.alertas_ativos.dados_pit import Painel
from core.inteligencia_ativos import eliminatorios as el

REGRAS: dict[str, m.RegraTipo] = {
    m.TIPO_EVENTO_GRAVE: m.RegraTipo(m.TIPO_EVENTO_GRAVE, "1.0.0", saidas=1,
                                     prazo_minimo_dias=0, horizonte_dias=365,
                                     resolve_so_no_horizonte=True),
    m.TIPO_DETERIORACAO: m.RegraTipo(m.TIPO_DETERIORACAO, "1.0.0", saidas=2,
                                     prazo_minimo_dias=90, horizonte_dias=365),
    m.TIPO_FII_RENDA: m.RegraTipo(m.TIPO_FII_RENDA, "1.0.0", saidas=2,
                                  prazo_minimo_dias=60, horizonte_dias=365),
}
TIPOS_DO_UNIVERSO: dict[str, tuple[str, ...]] = {
    "b3_acoes": (m.TIPO_EVENTO_GRAVE, m.TIPO_DETERIORACAO),
    "us": (m.TIPO_EVENTO_GRAVE, m.TIPO_DETERIORACAO),
    "b3_fii": (m.TIPO_FII_RENDA,),
}
ORCAMENTO_RARIDADE = 0.15     # fração máxima de ativos com alerta aberto, por tipo, na média

ALTA_LUCRO_CONTRARIA = 0.10   # lucro TTM sobe mais que isto e o preço acompanha: tese contrária
MESES_MEDIA_RENDIMENTO = 6
SUBIDA_MINIMA_PP = 1.0        # vacância/inadimplência "subindo" = +1 p.p. ou mais no trimestre
JANELA_DOCUMENTO_DIAS = 180   # pergunta 3: documento oficial nos últimos seis meses
JANELA_PROVENTOS_DIAS = 365


@dataclass(frozen=True)
class Parametros:
    limiar_preco: float = 0.10   # queda relativa ao mercado que conta como "preço confirma"
    limiar_corte: float = 0.15   # queda do rendimento contra a média de 6 meses


PADRAO = Parametros()
GRADE: dict[str, tuple[float, ...]] = {
    "limiar_preco": (0.05, 0.10, 0.15),
    "limiar_corte": (0.10, 0.15, 0.20),
}


def grade() -> list[Parametros]:
    return [Parametros(p, c) for p, c in itertools.product(GRADE["limiar_preco"],
                                                           GRADE["limiar_corte"])]


# ---------------------------------------------------------------- contexto

@dataclass(frozen=True, eq=False)
class Contexto:
    painel: Painel
    params: Parametros
    meses: tuple[date, ...]
    ret3: pd.DataFrame      # mes × ativo, retorno de 3 meses
    rel3: pd.DataFrame      # idem, menos a mediana do universo no mês
    ret12: pd.DataFrame
    rel12: pd.DataFrame
    por_ativo: dict         # nome do quadro -> {ativo: DataFrame ordenado}


def _retornos(wide: pd.DataFrame, meses: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    ret = wide / wide.shift(meses) - 1
    rel = ret.sub(ret.median(axis=1, skipna=True), axis=0)
    return ret, rel


def _agrupar(df: pd.DataFrame, ordem: str) -> dict:
    if df.empty:
        return {}
    return {a: g.sort_values(ordem).reset_index(drop=True) for a, g in df.groupby("ativo")}


def contexto(painel: Painel, params: Parametros = PADRAO) -> Contexto:
    """Pré-calcula retornos e agrupa os quadros por ativo, uma vez por painel e parâmetro."""
    wide = (painel.precos.pivot_table(index="mes", columns="ativo", values="preco", aggfunc="last")
            .sort_index())
    ret3, rel3 = _retornos(wide, 3)
    ret12, rel12 = _retornos(wide, 12)
    por_ativo = {
        "precos": _agrupar(painel.precos, "mes"),
        "fundamentos": _agrupar(painel.fundamentos, "fim_periodo"),
        "documentos": _agrupar(painel.documentos, "disponivel_em"),
        "fii_mensal": _agrupar(painel.fii_mensal, "referencia"),
        "fii_trimestral": _agrupar(painel.fii_trimestral, "referencia"),
    }
    return Contexto(painel, params, tuple(wide.index), ret3, rel3, ret12, rel12, por_ativo)


def _valor(quadro: pd.DataFrame, d: date, ativo: str) -> float | None:
    try:
        v = quadro.at[d, ativo]
    except KeyError:
        return None
    return None if pd.isna(v) else float(v)


def _ate(ctx: Contexto, nome: str, ativo: str, d: date) -> pd.DataFrame:
    g = ctx.por_ativo[nome].get(ativo)
    if g is None:
        return pd.DataFrame()
    return g[g["disponivel_em"] <= d]


def _datas_do_ativo(ctx: Contexto, ativo: str) -> list[date]:
    g = ctx.por_ativo["precos"].get(ativo)
    return [] if g is None else list(g["mes"])


def _preco(ctx: Contexto, ativo: str, d: date) -> float | None:
    g = ctx.por_ativo["precos"].get(ativo)
    if g is None:
        return None
    linha = g[g["mes"] == d]
    return None if linha.empty else float(linha["preco"].iloc[0])


# ---------------------------------------------------------------- deterioração

def _piora(linha) -> bool | None:
    vals = (linha["receita_ttm"], linha["lucro_ttm"], linha["receita_ttm_aa"], linha["lucro_ttm_aa"])
    if any(pd.isna(v) for v in vals):
        return None
    return bool(linha["receita_ttm"] < linha["receita_ttm_aa"]
                and linha["lucro_ttm"] < linha["lucro_ttm_aa"])


def _deterioracao(ativo: str, ctx: Contexto, d: date) -> m.Observacao:
    p = ctx.params
    f = _ate(ctx, "fundamentos", ativo, d)
    rel12 = _valor(ctx.rel12, d, ativo)
    evs: list[m.Evidencia] = []
    ult = f.iloc[-1] if len(f) else None
    pen = f.iloc[-2] if len(f) >= 2 else None

    piora_ult = _piora(ult) if ult is not None else None
    piora_pen = _piora(pen) if pen is not None else None
    fundamento_ruim = bool(piora_ult and piora_pen)
    preco_ruim = rel12 is not None and rel12 <= -p.limiar_preco
    pl_novo = bool(ult is not None and el.patrimonio_nao_positivo(ult["patrimonio"])
                   and not pd.isna(ult["patrimonio_ant"]) and ult["patrimonio_ant"] > 0)

    if fundamento_ruim:
        evs.append(m.Evidencia("fundamento", "RECEITA_LUCRO_CAEM",
                               "receita e lucro TTM abaixo do ano anterior em dois trimestres seguidos",
                               ult["disponivel_em"], "CVM (ITR/DFP)"))
    if fundamento_ruim and preco_ruim:
        evs.append(m.Evidencia("preco", "PRECO_RELATIVO_12M",
                               f"preço {rel12:+.0%} contra a mediana do mercado em 12 meses",
                               d, "preços mensais"))
    if pl_novo:
        evs.append(m.Evidencia("fundamento", "PL_NAO_POSITIVO",
                               "patrimônio líquido ficou zero ou negativo",
                               ult["disponivel_em"], "CVM (ITR/DFP)"))

    lucro_aa = None if ult is None else ult["lucro_ttm_aa"]
    contraria = bool(ult is not None and not pd.isna(lucro_aa) and lucro_aa > 0
                     and not pd.isna(ult["lucro_ttm"])
                     and ult["lucro_ttm"] > (1 + ALTA_LUCRO_CONTRARIA) * lucro_aa
                     and rel12 is not None and rel12 >= 0)
    return m.Observacao(
        data=d,
        presente=(fundamento_ruim and preco_ruim) or pl_novo,
        desfeita=None if piora_ult is None else (not piora_ult),
        evidencias=tuple(evs),
        contraria=contraria,
        preco=_preco(ctx, ativo, d),
    )


# ---------------------------------------------------------------- evento grave

def _evento_grave(ativo: str, ctx: Contexto, d: date, d_ant: date) -> m.Observacao:
    docs = _ate(ctx, "documentos", ativo, d)
    novos = docs[docs["disponivel_em"] > d_ant] if len(docs) else docs
    evs: list[m.Evidencia] = []
    presente = False
    for r in novos.itertuples(index=False):
        if r.evento in cat.GRAVES:
            presente = True
        if r.evento in cat.GRAVES or r.evento in cat.CANDIDATOS:
            evs.append(m.Evidencia("documento", r.evento, r.descricao, r.disponivel_em,
                                   r.fonte, r.url if isinstance(r.url, str) else None))
    rel3 = _valor(ctx.rel3, d, ativo)
    if rel3 is not None and rel3 <= -ctx.params.limiar_preco:
        evs.append(m.Evidencia("preco", "QUEDA_RELATIVA_3M",
                               f"preço {rel3:+.0%} contra a mediana do mercado em 3 meses",
                               d, "preços mensais"))
    return m.Observacao(data=d, presente=presente, desfeita=None, evidencias=tuple(evs),
                        preco=_preco(ctx, ativo, d))


# ---------------------------------------------------------------- FII

def _tipo_fii(painel: Painel, ativo: str) -> str:
    """Tipo atual de ``market.fiis`` (não é PIT, desvio 12). Sem tipo, híbrido e FoF contam como tijolo."""
    return "papel" if painel.tipo_fii.get(ativo) == "papel" else "tijolo"


def _corte(mensal: pd.DataFrame, limiar: float) -> tuple[bool | None, float | None, float | None]:
    """(houve corte, último rendimento, média dos 6 anteriores). ``None`` sem dado suficiente."""
    r = mensal["rendimento"].dropna() if len(mensal) else pd.Series(dtype=float)
    if len(r) < MESES_MEDIA_RENDIMENTO + 1:
        return None, None, None
    ultimo = float(r.iloc[-1])
    media = float(r.iloc[-1 - MESES_MEDIA_RENDIMENTO:-1].mean())
    if media <= 0:
        return None, ultimo, media
    return ultimo <= (1 - limiar) * media, ultimo, media


def _subindo(t: pd.DataFrame, coluna: str, critica) -> bool:
    v = t[coluna].dropna() if len(t) else pd.Series(dtype=float)
    if v.empty:
        return False
    if critica(float(v.iloc[-1])):
        return True
    return len(v) >= 2 and float(v.iloc[-1]) - float(v.iloc[-2]) >= SUBIDA_MINIMA_PP


def _fii_renda(ativo: str, ctx: Contexto, d: date) -> m.Observacao:
    p = ctx.params
    mensal = _ate(ctx, "fii_mensal", ativo, d)
    tri = _ate(ctx, "fii_trimestral", ativo, d)
    corte, ultimo, media = _corte(mensal, p.limiar_corte)
    v = mensal["vpa"].dropna() if len(mensal) else pd.Series(dtype=float)
    vpa_caindo = len(v) >= 3 and v.iloc[-1] < v.iloc[-2] < v.iloc[-3]
    tijolo = _tipo_fii(ctx.painel, ativo) == "tijolo"
    vac = tijolo and _subindo(tri, "vacancia", el.vacancia_critica)
    inad = tijolo and _subindo(tri, "inadimplencia", el.inadimplencia_critica)
    estrutural = tijolo and vpa_caindo and (vac or inad)

    evs: list[m.Evidencia] = []
    disp_m = mensal["disponivel_em"].iloc[-1] if len(mensal) else d
    disp_t = tri["disponivel_em"].iloc[-1] if len(tri) else d
    if corte:
        evs.append(m.Evidencia("renda", "CORTE_RENDIMENTO",
                               f"rendimento {ultimo / media - 1:+.0%} contra a média de 6 meses",
                               disp_m, "CVM (informe mensal)"))
    if estrutural:
        evs.append(m.Evidencia("fundamento", "VPA_CAI", "VPA caiu três meses seguidos",
                               disp_m, "CVM (informe mensal)"))
        if vac:
            evs.append(m.Evidencia("fundamento", "VACANCIA_SOBE", "vacância subindo ou crítica",
                                   disp_t, "CVM (informe trimestral)"))
        if inad:
            evs.append(m.Evidencia("fundamento", "INADIMPLENCIA_SOBE",
                                   "inadimplência subindo ou crítica",
                                   disp_t, "CVM (informe trimestral)"))
    desfeita = None
    if corte is not None:
        desfeita = ultimo >= (1 - p.limiar_corte / 2) * media and not vpa_caindo
    return m.Observacao(data=d, presente=bool(corte) or estrutural, desfeita=desfeita,
                        evidencias=tuple(evs), preco=_preco(ctx, ativo, d))


# ---------------------------------------------------------------- entrada única

def observacoes(tipo: str, ativo: str, ctx: Contexto) -> list[m.Observacao]:
    """Uma observação por fim de mês em que o ativo tem preço, em ordem."""
    if tipo not in REGRAS:
        raise ValueError(f"tipo desconhecido: {tipo!r}")
    if tipo not in TIPOS_DO_UNIVERSO[ctx.painel.universo]:
        raise ValueError(f"tipo {tipo!r} não se aplica ao universo {ctx.painel.universo!r}")
    saida = []
    d_ant = None
    for d in _datas_do_ativo(ctx, ativo):
        if tipo == m.TIPO_DETERIORACAO:
            saida.append(_deterioracao(ativo, ctx, d))
        elif tipo == m.TIPO_EVENTO_GRAVE:
            inicio = d_ant if d_ant is not None else (pd.Timestamp(d) - pd.offsets.MonthEnd(1)).date()
            saida.append(_evento_grave(ativo, ctx, d, inicio))
        else:
            saida.append(_fii_renda(ativo, ctx, d))
        d_ant = d
    return saida


# ---------------------------------------------------------------- card

PERGUNTAS = (
    "O lucro e o caixa caíram na mesma proporção do preço?",
    "A queda foi do ativo ou do setor e do mercado?",
    "Há documento oficial que explique?",
    "O balanço aguenta esperar?",
    "Os proventos foram cortados?",
    "A piora durou mais de um trimestre?",
)
_SND = ("sim", "não", "sem dado")
RESPOSTAS = (_SND, ("do ativo", "do mercado", "sem dado"), _SND, _SND, _SND, _SND)
SEM_DADO = "sem dado"


def _q1_lucro_acompanha(alerta, ctx, d) -> str:
    """Só o lucro (o caixa trimestral PIT não está no armazém, desvio 9); no FII, o rendimento."""
    ret12 = _valor(ctx.ret12, d, alerta.ativo)
    if ret12 is None:
        return SEM_DADO
    if alerta.tipo == m.TIPO_FII_RENDA:
        _, ultimo, media = _corte(_ate(ctx, "fii_mensal", alerta.ativo, d), ctx.params.limiar_corte)
        var = None if not media else ultimo / media - 1
    else:
        f = _ate(ctx, "fundamentos", alerta.ativo, d)
        if f.empty or pd.isna(f.iloc[-1]["lucro_ttm"]) or pd.isna(f.iloc[-1]["lucro_ttm_aa"]) \
                or f.iloc[-1]["lucro_ttm_aa"] <= 0:
            return SEM_DADO
        var = f.iloc[-1]["lucro_ttm"] / f.iloc[-1]["lucro_ttm_aa"] - 1
    if var is None:
        return SEM_DADO
    if ret12 >= 0:
        return "não"
    return "sim" if var <= ret12 else "não"


def _q2_do_ativo(alerta, ctx, d) -> str:
    rel12 = _valor(ctx.rel12, d, alerta.ativo)
    if rel12 is None:
        return SEM_DADO
    return "do ativo" if rel12 <= -ctx.params.limiar_preco else "do mercado"


def _q3_documento(alerta, ctx, d) -> str:
    if any(e.familia == "documento" for e in alerta.evidencias):
        return "sim"
    if ctx.painel.documentos.empty:
        return SEM_DADO
    docs = _ate(ctx, "documentos", alerta.ativo, d)
    recentes = docs[docs["disponivel_em"] > d - timedelta(days=JANELA_DOCUMENTO_DIAS)] if len(docs) else docs
    return "sim" if len(recentes) else "não"


def _q4_balanco(alerta, ctx, d) -> str:
    """Ações: PL positivo (dívida/EBITDA não é PIT, desvio 1). FII: VPA positivo e sem régua crítica."""
    if alerta.tipo == m.TIPO_FII_RENDA:
        mensal = _ate(ctx, "fii_mensal", alerta.ativo, d)
        v = mensal["vpa"].dropna() if len(mensal) else pd.Series(dtype=float)
        if v.empty:
            return SEM_DADO
        tri = _ate(ctx, "fii_trimestral", alerta.ativo, d)
        critico = len(tri) and (el.vacancia_critica(tri["vacancia"].iloc[-1])
                                or el.inadimplencia_critica(tri["inadimplencia"].iloc[-1]))
        return "sim" if v.iloc[-1] > 0 and not critico else "não"
    f = _ate(ctx, "fundamentos", alerta.ativo, d)
    if f.empty or pd.isna(f.iloc[-1]["patrimonio"]):
        return SEM_DADO
    return "não" if el.patrimonio_nao_positivo(f.iloc[-1]["patrimonio"]) else "sim"


def _q5_proventos(alerta, ctx, d) -> str:
    """FII pelo corte de rendimento. Ação só com documento de corte (sem série PIT, desvio 8)."""
    if alerta.tipo == m.TIPO_FII_RENDA:
        corte, _, _ = _corte(_ate(ctx, "fii_mensal", alerta.ativo, d), ctx.params.limiar_corte)
        return SEM_DADO if corte is None else ("sim" if corte else "não")
    docs = _ate(ctx, "documentos", alerta.ativo, d)
    if len(docs):
        janela = docs[docs["disponivel_em"] > d - timedelta(days=JANELA_PROVENTOS_DIAS)]
        if (janela["evento"] == cat.CORTE_PROVENTOS).any():
            return "sim"
    return SEM_DADO


def _q6_durou(alerta, ctx, d) -> str:
    if alerta.tipo == m.TIPO_DETERIORACAO and any(
            e.codigo == "RECEITA_LUCRO_CAEM" for e in alerta.evidencias):
        return "sim"
    return "sim" if (d - alerta.aberto_em).days >= 90 else "não"


def seis_perguntas(alerta: m.Alerta, ctx: Contexto, d: date) -> tuple[tuple[str, str], ...]:
    """As seis perguntas da spec (seção 7), na ordem, com a resposta na data ``d``."""
    funcs = (_q1_lucro_acompanha, _q2_do_ativo, _q3_documento, _q4_balanco,
             _q5_proventos, _q6_durou)
    return tuple((p, f(alerta, ctx, d)) for p, f in zip(PERGUNTAS, funcs))


_ITR_B3 = ((5, 15), (8, 14), (11, 14))
_DFP_B3 = (3, 31)


def proximo_documento(universo: str, d: date) -> tuple[str, date]:
    """Próximo documento decisivo depois de ``d`` (prazo legal, não a data real)."""
    if universo == "b3_fii":
        prox = (pd.Timestamp(d) + pd.offsets.MonthBegin(1)).date()
        return "informe mensal", prox.replace(day=15)
    candidatos: list[tuple[date, str]] = []
    for ano in (d.year, d.year + 1):
        if universo == "b3_acoes":
            candidatos += [(date(ano, mm, dd), "ITR") for mm, dd in _ITR_B3]
            candidatos.append((date(ano, *_DFP_B3), "DFP"))
        else:
            for mm in (3, 6, 9):
                fim = (pd.Timestamp(date(ano, mm, 1)) + pd.offsets.MonthEnd(0)).date()
                candidatos.append((fim + timedelta(days=45), "10-Q"))
            candidatos.append((date(ano, 12, 31) + timedelta(days=90), "10-K"))
    data, nome = min(c for c in candidatos if c[0] > d)
    return nome, data


def texto_proximo_documento(universo: str, d: date) -> str:
    nome, data = proximo_documento(universo, d)
    return f"próximo {nome} em {data:%d/%m}"


HIPOTESE = {
    m.TIPO_EVENTO_GRAVE: "Um documento oficial registrou um evento que costuma mudar a tese "
                         "do ativo; o preço pode ainda não ter refletido tudo.",
    m.TIPO_DETERIORACAO: "Receita e lucro pioram há dois trimestres e o preço confirma, "
                         "caindo mais que o mercado: a tese original pode ter mudado.",
    m.TIPO_FII_RENDA: "O fundo distribui menos ou perde valor patrimonial com piora de "
                      "vacância ou inadimplência: a renda pode não voltar ao nível anterior.",
}


def _pct(x: float) -> str:
    return f"{x:.1%}".replace(".", ",")


def invalidaria(tipo: str, params: Parametros = PADRAO) -> str:
    """O que, se acontecer, desfaz o alerta. Texto do card, com os limiares em vigor."""
    if tipo == m.TIPO_EVENTO_GRAVE:
        return ("A própria companhia retificar ou cancelar o documento. Ofício de "
                "questionamento sozinho não sustenta o alerta.")
    if tipo == m.TIPO_DETERIORACAO:
        return (f"Dois trimestres seguidos com receita e lucro TTM acima do ano anterior, "
                f"ou lucro TTM subindo mais de {ALTA_LUCRO_CONTRARIA:.0%} com o preço "
                f"acompanhando o mercado.")
    if tipo == m.TIPO_FII_RENDA:
        volta = 1 - params.limiar_corte / 2
        return (f"Rendimento voltar a pelo menos {_pct(volta)} da média dos 6 meses "
                f"anteriores, por dois meses, com o VPA parando de cair. Amortização de "
                f"capital pode parecer corte: ela não está ingerida e, se for o caso, "
                f"invalida o alerta.")
    raise ValueError(f"tipo desconhecido: {tipo!r}")
```

- [ ] **Step 8: Rodar os testes das regras**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_regras_risco.py tests/test_alertas_dados_pit.py -q`
Expected: PASS. Se `test_deterioracao_exige_dois_trimestres...` falhar no `antes`, confira se o segundo trimestre (`disponivel_em` 14/08/2020) está sendo filtrado por `<= d`: em 31/07 só um trimestre ruim é conhecido.

- [ ] **Step 9: Exercitar `carregar_painel` no armazém real**

O armazém precisa estar de pé (`docker ps --filter name=dfu_warehouse`; a autorização para subir o Docker é permanente). Rode:

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
import time
from sqlalchemy import create_engine
from scripts.publish_fii_selection_from_local import _warehouse_url
from core.alertas_ativos import dados_pit as dp, regras_risco as rr
e = create_engine(_warehouse_url())
for u in dp.UNIVERSOS:
    t = time.time(); p = dp.carregar_painel(e, u)
    print(u, f"{time.time()-t:.0f}s", "precos", len(p.precos), p.precos["ativo"].nunique(),
          "fund", len(p.fundamentos), "docs", len(p.documentos),
          "fii_m", len(p.fii_mensal), "fii_t", len(p.fii_trimestral))
    ctx = rr.contexto(p)
    a = p.precos["ativo"].iloc[0]
    for tipo in rr.TIPOS_DO_UNIVERSO[u]:
        obs = rr.observacoes(tipo, a, ctx)
        print("  ", tipo, len(obs), sum(o.presente for o in obs))
EOF
```

Expected:
- `b3_acoes` com cerca de 400 ativos e fundamentos não vazios;
- `b3_fii` com `fii_m` na casa de 50 mil e `fii_t` na casa de 8 mil;
- `us` com preços de milhares de símbolos;
- `docs` igual a 0 enquanto as Tarefas 4 e 5 não tiverem rodado a ingestão real, o que é esperado.

Anote os tempos no corpo do commit. Se `us` passar de 5 minutos, troque o laço por ativo de `ttm` por `groupby(...).rolling(4)` antes de seguir, e mantenha os testes passando.

- [ ] **Step 10: Commit**

```bash
git add core/alertas_ativos/dados_pit.py core/alertas_ativos/regras_risco.py \
        tests/test_alertas_dados_pit.py tests/test_alertas_regras_risco.py
git commit -m "$(cat <<'EOF'
alertas: leitores PIT do armazém e regras dos três tipos de risco

Deterioração exige dois trimestres de receita e lucro TTM em queda com o
preço pior que a mediana do mercado; evento grave abre no mês da entrega do
documento; renda do FII olha corte contra a média de 6 meses e, no tijolo,
VPA caindo com vacância ou inadimplência subindo. Só linhas com
disponivel_em <= d entram em cada data.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```


### Task 7: Replay único, desfechos e teste PIT (calibração e período guardado)

**Files:**
- Create: `core/alertas_ativos/motor.py` (parte 1: constantes de arquivo, `ler_json`, `reproduzir`)
- Create: `core/alertas_ativos/desfechos.py`
- Create: `core/alertas_ativos/backtest.py`
- Create: `scripts/testar_alertas_pit.py`
- Test: `tests/test_alertas_motor.py`, `tests/test_alertas_backtest.py`

**Interfaces:**
- Consumes:
  - `ciclo_vida.avancar(alerta, obs, regra, *, ativo) -> (Alerta | None, list[Transicao])` (Tarefa 2)
  - `modelos.Observacao`, `modelos.Transicao`, `modelos.NOVO`, `modelos.FECHADOS`, `modelos.TIPO_*` (Tarefa 2)
  - `dados_pit.Painel`, `dados_pit.COLS_*`, `dados_pit._vazio`, `dados_pit.UNIVERSOS`, `dados_pit.carregar_painel(engine, universo)` (Tarefa 6)
  - `regras_risco.REGRAS`, `TIPOS_DO_UNIVERSO`, `ORCAMENTO_RARIDADE`, `Parametros`, `PADRAO`, `GRADE`, `contexto(painel, params)`, `observacoes(tipo, ativo, ctx)`, `_datas_do_ativo(ctx, ativo)`, `_preco(ctx, ativo, d)` (Tarefa 6)
  - `core.memoria_mercado.amostra.N_MINIMO_EXPERIMENTAL` (8) e `N_MINIMO_ROBUSTO` (30)
- Produces:
  - `motor.DOCS_PADRAO: Path` (`<repo>/docs/alertas_ativos`) e os nomes `ARQ_CONGELADAS = "regras_congeladas.json"`, `ARQ_RESULTADO = "resultado_pit_fatia1.json"`, `ARQ_TRAVA = "guardado_executado.json"`, `ARQ_RELATORIO = "teste_pit_fatia1.md"`
  - `motor.ler_json(caminho) -> dict` (`{}` se o arquivo não existe; JSON corrompido levanta)
  - `motor.reproduzir(observacoes, regra, ativo) -> (Alerta | None, list[Transicao])`
  - `desfechos.QUEDA = -0.20`, `desfechos.HORIZONTE_MESES = 12`
  - `desfechos.matriz_precos(precos) -> DataFrame` (índice: todo fim de mês entre o primeiro e o último, como `date`; colunas: ativos)
  - `desfechos.Desfechos(caiu, antecedencia, ret12, censurado)` e `desfechos.desfechos(wide) -> Desfechos`
  - `desfechos.celula(quadro, d, ativo) -> float | None`
  - `backtest.Episodio(ativo, aberto_em, fechado_em)`, `backtest.episodios(transicoes)`
  - `backtest.ResultadoTipo` com `.para_dict()` e `.robusto`
  - `backtest.avaliar(painel, tipo, params, de, ate, fase, *, wide=None, des=None, tercis=None) -> ResultadoTipo`
  - `backtest.calibrar(painel, tipo, de, ate) -> (Parametros, list[ResultadoTipo])`
  - `backtest.PERIODOS: dict[fase, dict[universo, (de, ate)]]`
  - `backtest.impressao_das_regras() -> str` (sha256 hex)
  - `backtest.pode_rodar_guardado(docs_dir, impressao) -> (bool, str)`
  - `backtest.DESVIOS_PIT: tuple[str, ...]` e `backtest.relatorio_md(guardado: list[dict], calibracao: list[dict]) -> str`
  - `scripts/testar_alertas_pit.py::main(argv=None) -> int`

A ordem de importação evita ciclo: `motor` não importa `backtest`. `backtest` importa `motor` e `desfechos`. Na Tarefa 8, `motor` passa a importar `desfechos`, mas continua sem importar `backtest`.

- [ ] **Step 1: Escrever os testes do replay**

```python
# tests/test_alertas_motor.py
"""Motor dos alertas: o replay único que backtest e produção chamam."""
from __future__ import annotations

from datetime import date

from core.alertas_ativos import ciclo_vida as cv
from core.alertas_ativos import modelos as m
from core.alertas_ativos import motor
from core.alertas_ativos import regras_risco as rr

MESES = [date(2020, 1, 31), date(2020, 2, 29), date(2020, 3, 31), date(2020, 4, 30),
         date(2020, 5, 31), date(2020, 6, 30), date(2020, 7, 31)]


def _obs(presentes, desfeitas=()):
    return [m.Observacao(data=d, presente=i in presentes,
                         desfeita=True if i in desfeitas else None, preco=100.0)
            for i, d in enumerate(MESES)]


def test_reproduzir_e_o_laco_do_avancar():
    regra = rr.REGRAS[m.TIPO_DETERIORACAO]
    obs = _obs({1})
    alerta, trans = motor.reproduzir(obs, regra, "AAAA3")
    manual, esperado = None, []
    for o in obs:
        manual, t = cv.avancar(manual, o, regra, ativo="AAAA3")
        esperado.extend(t)
    assert alerta == manual and trans == esperado
    assert [t.estado for t in trans] == [m.NOVO]
    assert alerta.chave == "AAAA3|deterioracao|2020-02-29"


def test_reproduzir_sem_observacao_nao_abre_nada():
    assert motor.reproduzir([], rr.REGRAS[m.TIPO_DETERIORACAO], "AAAA3") == (None, [])


def test_reproduzir_fecha_e_reabre_com_chave_nova():
    regra = rr.REGRAS[m.TIPO_DETERIORACAO]        # duas saídas, prazo de 90 dias
    _, trans = motor.reproduzir(_obs({0, 6}, desfeitas={4, 5}), regra, "AAAA3")
    assert [(t.estado, t.data_evento) for t in trans] == [
        (m.NOVO, date(2020, 1, 31)),
        (m.RESOLVIDO, date(2020, 6, 30)),
        (m.NOVO, date(2020, 7, 31)),
    ]
    assert trans[0].alerta.chave != trans[-1].alerta.chave


def test_ler_json_ausente_e_vazio(tmp_path):
    assert motor.ler_json(tmp_path / "nao_existe.json") == {}
    (tmp_path / "x.json").write_text('{"a": 1}', encoding="utf-8")
    assert motor.ler_json(tmp_path / "x.json") == {"a": 1}


def test_docs_padrao_aponta_para_docs_alertas_ativos():
    assert motor.DOCS_PADRAO.parts[-2:] == ("docs", "alertas_ativos")
```

O terceiro teste depende da histerese da Tarefa 2: as leituras desfeitas nos índices 4 e 5 somam duas saídas, e a idade em 30/06 (151 dias) passa do prazo mínimo de 90. A observação do índice 6 reabre o alerta, porque um alerta fechado abre de novo quando a condição volta.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_motor.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.alertas_ativos.motor'`.

- [ ] **Step 3: Escrever `motor.py` (parte 1)**

```python
# core/alertas_ativos/motor.py
"""Motor dos alertas: um replay só, para o backtest e para a produção.

``reproduzir`` passa as observações de um ativo, em ordem, pela máquina de
estados. O teste PIT (``backtest``) e o publicador (``rodar``, Tarefa 8)
chamam esta mesma função: não existe uma segunda cópia da régua. Este módulo
não importa ``backtest``; é ``backtest`` que importa este.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.alertas_ativos import ciclo_vida as cv
from core.alertas_ativos import modelos as m

DOCS_PADRAO = Path(__file__).resolve().parents[2] / "docs" / "alertas_ativos"
ARQ_CONGELADAS = "regras_congeladas.json"
ARQ_RESULTADO = "resultado_pit_fatia1.json"
ARQ_TRAVA = "guardado_executado.json"
ARQ_RELATORIO = "teste_pit_fatia1.md"


def ler_json(caminho) -> dict:
    """Conteúdo do JSON; ``{}`` se o arquivo não existe.

    JSON corrompido levanta: um arquivo de regras congeladas ilegível não pode
    virar, em silêncio, os parâmetros padrão.
    """
    p = Path(caminho)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def reproduzir(observacoes: list[m.Observacao], regra: m.RegraTipo,
               ativo: str) -> tuple[m.Alerta | None, list[m.Transicao]]:
    """Estado final e todas as transições de um ativo, na ordem das observações."""
    alerta: m.Alerta | None = None
    transicoes: list[m.Transicao] = []
    for obs in observacoes:
        alerta, t = cv.avancar(alerta, obs, regra, ativo=ativo)
        transicoes.extend(t)
    return alerta, transicoes
```

- [ ] **Step 4: Rodar os testes do replay**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_motor.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Escrever os testes de desfechos e do backtest**

```python
# tests/test_alertas_backtest.py
"""Desfechos e teste PIT dos alertas de risco, com séries sintéticas."""
from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest

from core.alertas_ativos import backtest as bt
from core.alertas_ativos import dados_pit as dp
from core.alertas_ativos import desfechos as dz
from core.alertas_ativos import modelos as m
from core.alertas_ativos import motor
from core.alertas_ativos import regras_risco as rr


def _meses(inicio: date, n: int) -> list[date]:
    return [d.date() for d in pd.date_range(inicio, periods=n, freq=pd.offsets.MonthEnd())]


MESES = _meses(date(2019, 1, 31), 24)


def _precos(series: dict[str, list[float | None]], meses=MESES, volume=1e6) -> pd.DataFrame:
    linhas = [{"ativo": a, "mes": d, "preco": p, "volume_fin": volume}
              for a, ps in series.items() for d, p in zip(meses, ps) if p is not None]
    return pd.DataFrame(linhas, columns=dp.COLS_PRECOS)


def _painel(precos: pd.DataFrame, universo: str = "b3_acoes") -> dp.Painel:
    return dp.Painel(universo, precos, dp._vazio(dp.COLS_FUND), dp._vazio(dp.COLS_DOCS),
                     dp._vazio(dp.COLS_FII_M), dp._vazio(dp.COLS_FII_T), {})


def _wide(series: dict[str, list[float | None]], n: int) -> pd.DataFrame:
    return dz.matriz_precos(_precos(series, meses=_meses(date(2019, 1, 31), n)))


# ------------------------------------------------------------------ desfechos

def test_matriz_precos_preenche_o_mes_que_falta():
    precos = _precos({"AAAA3": [100.0, None, 90.0]}, meses=MESES[:3])
    w = dz.matriz_precos(precos)
    assert list(w.index) == MESES[:3]
    assert np.isnan(w.at[MESES[1], "AAAA3"]) and w.at[MESES[2], "AAAA3"] == 90.0


def test_matriz_precos_vazia():
    assert dz.matriz_precos(dp._vazio(dp.COLS_PRECOS)).empty


def test_desfechos_queda_antecedencia_e_censura():
    a = [100.0, 100.0, 100.0] + [75.0] * 12          # cai 25% no 4º mês e fica
    b = [100.0] * 15
    c = [100.0] * 5 + [None] * 10                     # some antes dos 12 meses
    w = _wide({"AAAA3": a, "BBBB3": b, "CCCC3": c}, 15)
    des = dz.desfechos(w)
    d0, d2, d3 = w.index[0], w.index[2], w.index[3]
    assert des.caiu.at[d0, "AAAA3"] == 1.0 and des.antecedencia.at[d0, "AAAA3"] == 3
    assert des.caiu.at[d2, "AAAA3"] == 1.0 and des.antecedencia.at[d2, "AAAA3"] == 1
    assert np.isnan(des.caiu.at[d3, "AAAA3"])         # sem 12 meses à frente
    assert des.caiu.at[d0, "BBBB3"] == 0.0 and des.ret12.at[d0, "BBBB3"] == 0.0
    assert des.ret12.at[d0, "AAAA3"] == pytest.approx(-0.25)
    assert np.isnan(des.caiu.at[d0, "CCCC3"]) and bool(des.censurado.at[d0, "CCCC3"])
    assert not bool(des.censurado.at[d0, "BBBB3"])


def test_queda_seguida_de_saida_conta_como_queda():
    a = [100.0, 70.0] + [None] * 13                   # cai 30% e sai da bolsa
    des = dz.desfechos(_wide({"AAAA3": a, "BBBB3": [100.0] * 15}, 15))
    d0 = des.caiu.index[0]
    assert des.caiu.at[d0, "AAAA3"] == 1.0 and not bool(des.censurado.at[d0, "AAAA3"])


def test_celula():
    w = _wide({"AAAA3": [100.0, None]}, 2)
    assert dz.celula(w, w.index[0], "AAAA3") == 100.0
    assert dz.celula(w, w.index[1], "AAAA3") is None
    assert dz.celula(w, date(1990, 1, 31), "AAAA3") is None
    assert dz.celula(w, w.index[0], "ZZZZ3") is None


# ------------------------------------------------------------------ peças do backtest

def test_tercis_de_liquidez():
    precos = pd.concat([
        _precos({"AAAA3": [1.0]}, meses=MESES[:1], volume=1.0),
        _precos({"BBBB3": [1.0]}, meses=MESES[:1], volume=2.0),
        _precos({"CCCC3": [1.0]}, meses=MESES[:1], volume=3.0),
    ])
    assert bt.tercis_de_liquidez(precos) == {"AAAA3": 3, "BBBB3": 2, "CCCC3": 1}
    iguais = _precos({"AAAA3": [1.0], "BBBB3": [1.0]}, meses=MESES[:1])
    assert set(bt.tercis_de_liquidez(iguais).values()) == {2}
    sem_volume = _precos({"AAAA3": [1.0]}, meses=MESES[:1], volume=np.nan)
    assert bt.tercis_de_liquidez(sem_volume) == {"AAAA3": 3}


def test_custo_ida_volta():
    assert bt.custo_ida_volta(1) == pytest.approx(2 * 0.0003 + 0.001)
    assert bt.custo_ida_volta(3) == pytest.approx(2 * 0.0003 + 0.008)


def test_episodios_pareiam_abertura_e_fechamento():
    regra = rr.REGRAS[m.TIPO_DETERIORACAO]
    obs = [m.Observacao(data=d, presente=i in (0, 8), desfeita=True if i in (4, 5) else None,
                        preco=100.0) for i, d in enumerate(MESES[:10])]
    _, trans = motor.reproduzir(obs, regra, "AAAA3")
    eps = bt.episodios(trans)
    assert eps == [bt.Episodio("AAAA3", MESES[0], MESES[5]),
                   bt.Episodio("AAAA3", MESES[8], None)]


def test_raridade_media_mensal():
    w = _wide({"AAAA3": [1.0] * 3, "BBBB3": [1.0] * 3, "CCCC3": [1.0] * 3}, 3)
    eps = [bt.Episodio("AAAA3", w.index[0], None)]
    assert bt.raridade(eps, w, w.index[0], w.index[2]) == pytest.approx(1 / 3)
    assert bt.raridade(eps, w, date(2030, 1, 1), date(2030, 12, 31)) is None


def test_bootstrap_deterministico_e_vazio():
    qa = np.array([1.0, 1.0, 0.0, 1.0] * 5)
    na = np.ones(20)
    qb = np.array([1.0, 0.0, 0.0, 0.0] * 5) * 10
    nb = np.ones(20) * 40
    um = bt.excesso_bootstrap(qa, na, qb, nb)
    assert um == bt.excesso_bootstrap(qa, na, qb, nb)
    assert um[0] is not None and 0 < um[0] < um[1]
    assert bt.excesso_bootstrap(np.array([]), np.array([]), np.array([]), np.array([])) == (None, None)
    assert bt.excesso_bootstrap(qa * 0, na * 0, qb, nb) == (None, None)


@pytest.mark.parametrize("n, rar, ic, esperado", [
    (5, 0.05, 0.10, ("insuficiente", "amostra insuficiente")),
    (10, None, 0.10, ("reprovado", "sem meses no período")),
    (10, 0.20, 0.10, ("reprovado", "acima do orçamento de raridade")),
    (10, 0.05, None, ("reprovado", "intervalo cruza zero")),
    (10, 0.05, 0.0, ("reprovado", "intervalo cruza zero")),
    (10, 0.05, 0.01, ("aprovado", "excesso positivo com intervalo acima de zero")),
])
def test_veredito(n, rar, ic, esperado):
    assert bt.veredito(n, rar, ic) == esperado


# ------------------------------------------------------------------ avaliar

PLANO = [100.0] * 24
CAI = [100.0] * 7 + [70.0] * 17          # cai 30% no 8º mês (índice 7) e fica


def _fake_observacoes(abre: dict[str, int]):
    def observacoes(tipo, ativo, ctx):
        datas = rr._datas_do_ativo(ctx, ativo)
        return [m.Observacao(data=d, presente=abre.get(ativo) == i,
                             preco=rr._preco(ctx, ativo, d))
                for i, d in enumerate(datas)]
    return observacoes


def _universo(extra: dict[str, list[float | None]]) -> dict[str, list[float | None]]:
    base = {f"P{i:03d}3": PLANO for i in range(10 - len(extra))}
    return {**base, **extra}


def test_avaliar_mede_queda_antecedencia_base_e_ganho(monkeypatch):
    painel = _painel(_precos(_universo({"AAAA3": CAI})))
    monkeypatch.setattr(bt.rr, "observacoes", _fake_observacoes({"AAAA3": 5}))
    r = bt.avaliar(painel, m.TIPO_DETERIORACAO, rr.PADRAO, date(2019, 1, 1),
                   date(2020, 12, 31), "calibracao")
    assert (r.n_episodios, r.n_validos, r.n_ativos, r.n_censurados) == (1, 1, 1, 0)
    assert r.frac_queda == 1.0
    assert r.frac_base == pytest.approx(7 / 120)
    assert r.n_base == 120
    assert r.excesso == pytest.approx(1 - 7 / 120)
    assert r.antecedencia_mediana_meses == 2
    assert r.ret_mediana == pytest.approx(-0.30)
    assert r.ganho_sair_mediana == pytest.approx(0.30 - bt.custo_ida_volta(2))
    assert r.raridade == pytest.approx(0.05)        # 1 de 10 aberto em 12 dos 24 meses
    assert (r.status, r.motivo) == ("insuficiente", "amostra insuficiente")
    assert r.robusto is False
    d = r.para_dict()
    assert d["de"] == "2019-01-01" and d["params"] == {"limiar_preco": 0.10, "limiar_corte": 0.15}
    json.dumps(d)                                    # serializável


def test_censurado_sai_do_numerador_e_do_denominador(monkeypatch):
    some = [100.0] * 10 + [None] * 14
    painel = _painel(_precos(_universo({"AAAA3": CAI, "ZZZZ3": some})))
    monkeypatch.setattr(bt.rr, "observacoes", _fake_observacoes({"AAAA3": 5, "ZZZZ3": 5}))
    r = bt.avaliar(painel, m.TIPO_DETERIORACAO, rr.PADRAO, date(2019, 1, 1),
                   date(2020, 12, 31), "calibracao")
    assert (r.n_episodios, r.n_validos, r.n_censurados) == (2, 1, 1)
    assert r.frac_queda == 1.0
    assert r.frac_base == pytest.approx(7 / 108)     # o ZZZZ3 não entra na base
    assert r.n_base == 108


def test_avaliar_respeita_a_janela(monkeypatch):
    painel = _painel(_precos(_universo({"AAAA3": CAI})))
    monkeypatch.setattr(bt.rr, "observacoes", _fake_observacoes({"AAAA3": 5}))
    r = bt.avaliar(painel, m.TIPO_DETERIORACAO, rr.PADRAO, date(2019, 7, 1),
                   date(2020, 12, 31), "calibracao")
    assert r.n_episodios == 0 and r.frac_queda is None and r.excesso is None
    assert (r.ic_inf, r.ic_sup) == (None, None)
    assert r.status == "insuficiente"


# ------------------------------------------------------------------ calibrar

def _res(params, ic, n=10, rar=0.05):
    return bt.ResultadoTipo(universo="b3_acoes", tipo=m.TIPO_DETERIORACAO, fase="calibracao",
                            de=date(2012, 1, 1), ate=date(2020, 12, 31), params=params,
                            n_validos=n, raridade=rar, ic_inf=ic)


def test_calibrar_empate_fica_no_padrao(monkeypatch):
    painel = _painel(_precos({"AAAA3": PLANO}))
    monkeypatch.setattr(bt, "avaliar", lambda p, t, params, *a, **k: _res(params, 0.02))
    escolhido, rs = bt.calibrar(painel, m.TIPO_DETERIORACAO, date(2012, 1, 1), date(2020, 12, 31))
    assert escolhido == rr.PADRAO and len(rs) == len(rr.GRADE["limiar_preco"])


def test_calibrar_escolhe_o_maior_limite_inferior_entre_os_elegiveis(monkeypatch):
    painel = _painel(_precos({"AAAA3": PLANO}))
    tabela = {0.05: _res(None, 0.09, rar=0.30),       # fora do orçamento de raridade
              0.10: _res(None, 0.01),
              0.15: _res(None, 0.03)}
    monkeypatch.setattr(bt, "avaliar",
                        lambda p, t, params, *a, **k: tabela[params.limiar_preco])
    escolhido, _ = bt.calibrar(painel, m.TIPO_DETERIORACAO, date(2012, 1, 1), date(2020, 12, 31))
    assert escolhido == rr.Parametros(limiar_preco=0.15)


def test_calibrar_sem_elegivel_fica_no_padrao(monkeypatch):
    painel = _painel(_precos({"AAAA3": PLANO}))
    monkeypatch.setattr(bt, "avaliar", lambda p, t, params, *a, **k: _res(params, 0.5, n=3))
    escolhido, _ = bt.calibrar(painel, m.TIPO_FII_RENDA, date(2016, 1, 1), date(2020, 12, 31))
    assert escolhido == rr.PADRAO


def test_periodos_respeitam_a_fronteira():
    for u in dp.UNIVERSOS:
        de_c, ate_c = bt.PERIODOS["calibracao"][u]
        de_g, ate_g = bt.PERIODOS["guardado"][u]
        assert ate_c == date(2020, 12, 31) and de_g == date(2022, 1, 1) and ate_g == date(2025, 10, 31)
    assert bt.PERIODOS["calibracao"]["b3_fii"][0] == date(2016, 1, 1)


# ------------------------------------------------------------------ congelamento

def test_impressao_e_sha256_e_ignora_fim_de_linha(monkeypatch, tmp_path):
    assert re.fullmatch(r"[0-9a-f]{64}", bt.impressao_das_regras())
    lf, crlf = tmp_path / "lf" / "r.py", tmp_path / "crlf" / "r.py"
    lf.parent.mkdir(); crlf.parent.mkdir()
    lf.write_bytes(b"x = 1\ny = 2\n")
    crlf.write_bytes(b"x = 1\r\ny = 2\r\n")
    monkeypatch.setattr(bt, "_ARQUIVOS_DAS_REGRAS", (lf,))
    a = bt.impressao_das_regras()
    monkeypatch.setattr(bt, "_ARQUIVOS_DAS_REGRAS", (crlf,))
    assert bt.impressao_das_regras() == a
    crlf.write_bytes(b"x = 2\r\n")
    assert bt.impressao_das_regras() != a


def test_pode_rodar_guardado(tmp_path):
    assert bt.pode_rodar_guardado(tmp_path, "abc") == (
        False, "regras não congeladas: rode a calibração antes")
    (tmp_path / motor.ARQ_CONGELADAS).write_text(json.dumps({"impressao": "xyz"}), encoding="utf-8")
    assert bt.pode_rodar_guardado(tmp_path, "abc") == (
        False, "as regras mudaram depois do congelamento")
    assert bt.pode_rodar_guardado(tmp_path, "xyz") == (True, "ok")
    (tmp_path / motor.ARQ_TRAVA).write_text("{}", encoding="utf-8")
    assert bt.pode_rodar_guardado(tmp_path, "xyz") == (
        False, "o período guardado já rodou (trava presente)")


def test_relatorio_md_tem_as_tabelas_e_os_desvios(monkeypatch):
    painel = _painel(_precos(_universo({"AAAA3": CAI})))
    monkeypatch.setattr(bt.rr, "observacoes", _fake_observacoes({"AAAA3": 5}))
    r = bt.avaliar(painel, m.TIPO_DETERIORACAO, rr.PADRAO, date(2019, 1, 1),
                   date(2020, 12, 31), "calibracao").para_dict()
    md = bt.relatorio_md([], [r])
    assert "## Calibração" in md and "## Período guardado" in md
    assert "Ainda não rodou" in md
    assert "| b3_acoes | deterioracao |" in md and "amostra insuficiente" in md
    for desvio in bt.DESVIOS_PIT:
        assert desvio in md
    md2 = bt.relatorio_md([r], [r])
    assert "Ainda não rodou" not in md2


# ------------------------------------------------------------------ script

def _script():
    import scripts.testar_alertas_pit as s
    return s


def _engine_proibido():
    raise AssertionError("não podia abrir o banco")


def test_script_guardado_recusa_com_trava_sem_tocar_o_banco(monkeypatch, tmp_path, capsys):
    s = _script()
    monkeypatch.setattr(s, "_engine", _engine_proibido)
    (tmp_path / motor.ARQ_CONGELADAS).write_text(
        json.dumps({"impressao": bt.impressao_das_regras()}), encoding="utf-8")
    (tmp_path / motor.ARQ_TRAVA).write_text("{}", encoding="utf-8")
    assert s.main(["--fase", "guardado", "--docs-dir", str(tmp_path)]) == 2
    assert "já rodou" in capsys.readouterr().err


def test_script_guardado_recusa_sem_congelamento(monkeypatch, tmp_path):
    s = _script()
    monkeypatch.setattr(s, "_engine", _engine_proibido)
    assert s.main(["--fase", "guardado", "--docs-dir", str(tmp_path)]) == 2
    assert not (tmp_path / motor.ARQ_TRAVA).exists()


def test_script_calibracao_recusa_depois_do_guardado(monkeypatch, tmp_path):
    s = _script()
    monkeypatch.setattr(s, "_engine", _engine_proibido)
    (tmp_path / motor.ARQ_TRAVA).write_text("{}", encoding="utf-8")
    assert s.main(["--fase", "calibracao", "--docs-dir", str(tmp_path)]) == 2


def test_script_calibracao_escreve_congelamento_e_relatorio(monkeypatch, tmp_path):
    s = _script()
    painel = _painel(_precos(_universo({"AAAA3": CAI})))
    monkeypatch.setattr(s, "_engine", lambda: object())
    monkeypatch.setattr(s.dp, "carregar_painel",
                        lambda e, u: dp.Painel(u, *[getattr(painel, c) for c in
                                                    ("precos", "fundamentos", "documentos",
                                                     "fii_mensal", "fii_trimestral", "tipo_fii")]))
    monkeypatch.setattr(bt.rr, "observacoes", _fake_observacoes({"AAAA3": 5}))
    assert s.main(["--fase", "calibracao", "--docs-dir", str(tmp_path)]) == 0
    cong = json.loads((tmp_path / motor.ARQ_CONGELADAS).read_text(encoding="utf-8"))
    assert cong["impressao"] == bt.impressao_das_regras()
    assert set(cong["parametros"]) == {f"{u}/{t}" for u in dp.UNIVERSOS
                                       for t in rr.TIPOS_DO_UNIVERSO[u]}
    assert (tmp_path / motor.ARQ_RELATORIO).read_text(encoding="utf-8").startswith("# Teste PIT")
    assert not (tmp_path / motor.ARQ_TRAVA).exists()
```

A falsificação de `rr.observacoes` no último teste devolve observação para todo universo e todo tipo, inclusive `fii_renda` e `evento_grave`. O teste mede o fluxo do script (o que ele escreve e onde), não as regras, que a Tarefa 6 já cobre.

- [ ] **Step 6: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_backtest.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.alertas_ativos.backtest'`.

- [ ] **Step 7: Escrever `desfechos.py`**

```python
# core/alertas_ativos/desfechos.py
"""O que aconteceu com o preço nos 12 meses seguintes a cada fim de mês.

Uma matriz só, mês × ativo, alimenta duas coisas: o teste PIT (``backtest``) e
o placar da aba (``motor.rodar``). As duas leem a mesma régua de queda.

Regras:
- **caiu** = 1 se, em algum dos 12 fins de mês seguintes, o preço ficou 20% ou
  mais abaixo do preço da data; 0 se a série chegou aos 12 meses sem isso; e
  ausente (censurado) se a série acabou antes dos 12 meses sem queda. Queda
  seguida de saída da bolsa conta como queda.
- **antecedência** = o menor número de meses até a primeira queda.
- **ret12** = retorno até o 12º fim de mês. Se faltar o preço exato, usa o
  último dos 12 meses (``ffill``). Entra só onde ``caiu`` não é censurado.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

QUEDA = -0.20
HORIZONTE_MESES = 12


def matriz_precos(precos: pd.DataFrame) -> pd.DataFrame:
    """Pivô mês × ativo com todo fim de mês entre o primeiro e o último, como ``date``."""
    if precos.empty:
        return pd.DataFrame()
    wide = precos.pivot_table(index="mes", columns="ativo", values="preco", aggfunc="last")
    idx = pd.to_datetime(pd.Index(wide.index))
    wide.index = idx
    cheio = pd.date_range(idx.min(), idx.max(), freq=pd.offsets.MonthEnd())
    wide = wide.reindex(cheio).sort_index()
    wide.index = pd.Index([d.date() for d in wide.index])
    return wide


@dataclass(frozen=True, eq=False)
class Desfechos:
    caiu: pd.DataFrame          # 1.0, 0.0 ou NaN (censurado ou sem preço na data)
    antecedencia: pd.DataFrame  # meses até a primeira queda, NaN se não caiu
    ret12: pd.DataFrame
    censurado: pd.DataFrame     # bool: tinha preço na data, mas não há desfecho


def desfechos(wide: pd.DataFrame) -> Desfechos:
    ante = pd.DataFrame(np.nan, index=wide.index, columns=wide.columns)
    for k in range(HORIZONTE_MESES, 0, -1):          # do maior para o menor: fica o menor k
        queda = (wide.shift(-k) / wide - 1) <= QUEDA
        ante = ante.mask(queda, float(k))
    viu = ante.notna()
    chegou = (wide.notna().iloc[::-1].cummax().iloc[::-1]
              .shift(-HORIZONTE_MESES, fill_value=False).astype(bool))
    caiu = pd.DataFrame(np.where(viu, 1.0, np.where(chegou, 0.0, np.nan)),
                        index=wide.index, columns=wide.columns).where(wide.notna())
    ret12 = wide.ffill(limit=HORIZONTE_MESES).shift(-HORIZONTE_MESES) / wide - 1
    censurado = wide.notna() & caiu.isna()
    return Desfechos(caiu, ante, ret12, censurado)


def celula(quadro: pd.DataFrame, d: date, ativo: str) -> float | None:
    """Valor da matriz na data e no ativo; ``None`` se ausente."""
    try:
        v = quadro.at[d, ativo]
    except KeyError:
        return None
    return None if pd.isna(v) else float(v)
```

- [ ] **Step 8: Escrever `backtest.py`**

```python
# core/alertas_ativos/backtest.py
"""Teste ponto no tempo (PIT) dos alertas de risco da fatia 1.

Critério da spec (seção 8), por tipo, no período guardado: a fração de alertas
seguidos de queda acima de 20% em 12 meses tem de ser maior que a taxa-base do
universo, com intervalo de 95% que não cruza zero, dentro do orçamento de
raridade. A antecedência mediana é reportada.

- A taxa-base é a fração de todos os ativo-meses do universo, na mesma janela,
  seguidos da mesma queda.
- Censurado (série que acaba antes dos 12 meses sem queda) sai do numerador e
  do denominador, nos alertas e na base, e é contado à parte.
- O intervalo sai de bootstrap por ativo (cluster), com semente fixa.
- O ganho de sair é ``-ret12`` menos o custo de ida e volta pelo tercil de
  liquidez: o que se evitaria vendendo no fim do mês do alerta.

O replay é ``motor.reproduzir``, o mesmo da produção.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from core.alertas_ativos import desfechos as dz
from core.alertas_ativos import modelos as m
from core.alertas_ativos import motor
from core.alertas_ativos import regras_risco as rr
from core.alertas_ativos.dados_pit import Painel
from core.memoria_mercado.amostra import N_MINIMO_EXPERIMENTAL, N_MINIMO_ROBUSTO

CUSTO_CORRETAGEM = 0.0003                       # emolumentos por perna
SPREAD_TERCIL = {1: 0.001, 2: 0.003, 3: 0.008}  # ida e volta, pelo tercil de liquidez
SEMENTE = 20261109
B = 2000

PERIODOS: dict[str, dict[str, tuple[date, date]]] = {
    "calibracao": {
        "b3_acoes": (date(2012, 1, 1), date(2020, 12, 31)),
        "b3_fii": (date(2016, 1, 1), date(2020, 12, 31)),
        "us": (date(2012, 1, 1), date(2020, 12, 31)),
    },
    "guardado": {u: (date(2022, 1, 1), date(2025, 10, 31))
                 for u in ("b3_acoes", "b3_fii", "us")},
}

PARAMETRO_DO_TIPO = {
    m.TIPO_EVENTO_GRAVE: "limiar_preco",
    m.TIPO_DETERIORACAO: "limiar_preco",
    m.TIPO_FII_RENDA: "limiar_corte",
}

_PACOTE = Path(__file__).resolve().parent
_ARQUIVOS_DAS_REGRAS: tuple[Path, ...] = tuple(
    [_PACOTE / f for f in ("regras_risco.py", "ciclo_vida.py", "dados_pit.py", "categorias.py",
                           "modelos.py", "desfechos.py", "motor.py", "backtest.py")]
    + [_PACOTE.parent / "inteligencia_ativos" / "eliminatorios.py"])

DESVIOS_PIT: tuple[str, ...] = (
    "1. Qualidade sólida→frágil não é PIT: só PL ≤ 0 e vacância > 30% (informe trimestral) entram.",
    "2. Inadimplência de FII é trimestral (cvm_informe_trimestral): compara trimestre com trimestre.",
    "3. Reapresentação de balanço só pelo assunto do IPE.",
    "4. Ofício de questionamento da CVM/B3 é evidência, não abre alerta sozinho.",
    "5. Amortização de FII não está ingerida: queda por amortização pode parecer corte.",
    "6. ITR disponível em fim do trimestre + 45 dias (prazo legal).",
    "7. Preço reduzido a fim de mês; antecedência contada em meses.",
    "8. Proventos de ações sem série PIT: a pergunta responde 'sem dado' sem documento de corte.",
    "9. Caixa trimestral PIT não entra: a pergunta 1 usa só o lucro.",
    "10. Calibração com entradas até 31/12/2020; guardado com entradas de 01/2022 a 10/2025.",
    "11. Teste do rótulo das abas mapeia cada nome ao seu ROTULO.",
    "12. Tipo do FII é o de hoje em todo o histórico; sem tipo vira tijolo.",
    "13. FII usa a primeira safra de cada referência.",
    "14. Evento grave de FII fica para depois (FNET só desde 2022).",
    "19. Série de preço mista reduzida a fim de mês; frescor de 40 dias; mês corrente no próprio fim de mês.",
    "21. Censurado sai do numerador e do denominador, nos alertas e na base.",
    "22. Antes do guardado, a taxa-base do cartão vem da calibração e todo tipo é experimental.",
)


# ------------------------------------------------------------------ custos

def tercis_de_liquidez(precos: pd.DataFrame) -> dict[str, int]:
    """1 = mais líquido. Pela mediana do volume financeiro; sem volume vai para 3."""
    if precos.empty:
        return {}
    r = precos.groupby("ativo")["volume_fin"].median().rank(pct=True)
    saida = {}
    for ativo, v in r.items():
        if pd.isna(v):
            saida[ativo] = 3
        else:
            saida[ativo] = 1 if v > 2 / 3 else (2 if v > 1 / 3 else 3)
    return saida


def custo_ida_volta(tercil: int) -> float:
    return 2 * CUSTO_CORRETAGEM + SPREAD_TERCIL[tercil]


# ------------------------------------------------------------------ episódios

@dataclass(frozen=True)
class Episodio:
    ativo: str
    aberto_em: date
    fechado_em: date | None


def episodios(transicoes: list[m.Transicao]) -> list[Episodio]:
    abertos: dict[str, m.Alerta] = {}
    saida: list[Episodio] = []
    for t in transicoes:
        ch = t.alerta.chave
        if t.estado == m.NOVO and ch not in abertos:
            abertos[ch] = t.alerta
        elif t.estado in m.FECHADOS and ch in abertos:
            a = abertos.pop(ch)
            saida.append(Episodio(a.ativo, a.aberto_em, t.data_evento))
    saida.extend(Episodio(a.ativo, a.aberto_em, None) for a in abertos.values())
    return sorted(saida, key=lambda e: (e.ativo, e.aberto_em))


def raridade(eps: list[Episodio], wide: pd.DataFrame, de: date, ate: date) -> float | None:
    """Média mensal de (ativos com alerta aberto / ativos com preço) nos meses da janela."""
    fracs = []
    for d in wide.index:
        if not (de <= d <= ate):
            continue
        precificados = int(wide.loc[d].notna().sum())
        if precificados == 0:
            continue
        abertos = sum(1 for e in eps
                      if e.aberto_em <= d and (e.fechado_em is None or e.fechado_em > d))
        fracs.append(abertos / precificados)
    return float(np.mean(fracs)) if fracs else None


# ------------------------------------------------------------------ estatística

def excesso_bootstrap(qa, na, qb, nb, *, seed: int = SEMENTE, b: int = B
                      ) -> tuple[float | None, float | None]:
    """IC 95% de (fração dos alertas − taxa-base), reamostrando ativos inteiros."""
    qa, na, qb, nb = (np.asarray(x, dtype=float) for x in (qa, na, qb, nb))
    if len(na) == 0 or na.sum() == 0 or nb.sum() == 0:
        return None, None
    rng = np.random.default_rng(seed)
    n = len(na)
    blocos = []
    for ini in range(0, b, 250):
        idx = rng.integers(0, n, size=(min(250, b - ini), n))
        with np.errstate(invalid="ignore", divide="ignore"):
            blocos.append(qa[idx].sum(1) / na[idx].sum(1) - qb[idx].sum(1) / nb[idx].sum(1))
    r = np.concatenate(blocos)
    if np.isnan(r).all():
        return None, None
    lo, hi = np.nanpercentile(r, [2.5, 97.5])
    return float(lo), float(hi)


def veredito(n_validos: int, rar: float | None, ic_inf: float | None) -> tuple[str, str]:
    if n_validos < N_MINIMO_EXPERIMENTAL:
        return "insuficiente", "amostra insuficiente"
    if rar is None:
        return "reprovado", "sem meses no período"
    if rar > rr.ORCAMENTO_RARIDADE:
        return "reprovado", "acima do orçamento de raridade"
    if ic_inf is None or ic_inf <= 0:
        return "reprovado", "intervalo cruza zero"
    return "aprovado", "excesso positivo com intervalo acima de zero"


@dataclass(frozen=True)
class ResultadoTipo:
    universo: str
    tipo: str
    fase: str
    de: date
    ate: date
    params: rr.Parametros | None
    n_episodios: int = 0
    n_validos: int = 0
    n_ativos: int = 0
    n_censurados: int = 0
    frac_queda: float | None = None
    frac_base: float | None = None
    n_base: int = 0
    excesso: float | None = None
    ic_inf: float | None = None
    ic_sup: float | None = None
    ret_mediana: float | None = None
    ret_q1: float | None = None
    ret_q3: float | None = None
    ganho_sair_mediana: float | None = None
    antecedencia_mediana_meses: float | None = None
    raridade: float | None = None
    status: str = "insuficiente"
    motivo: str = "amostra insuficiente"

    @property
    def robusto(self) -> bool:
        return self.n_validos >= N_MINIMO_ROBUSTO

    def para_dict(self) -> dict:
        d = asdict(self)
        d["de"], d["ate"] = self.de.isoformat(), self.ate.isoformat()
        d["params"] = asdict(self.params) if self.params is not None else None
        d["robusto"] = self.robusto
        return d


def _quantis(xs: list[float]) -> tuple[float | None, float | None, float | None]:
    if not xs:
        return None, None, None
    q1, q2, q3 = np.percentile(xs, [25, 50, 75])
    return float(q1), float(q2), float(q3)


def avaliar(painel: Painel, tipo: str, params: rr.Parametros, de: date, ate: date, fase: str,
            *, wide: pd.DataFrame | None = None, des: dz.Desfechos | None = None,
            tercis: dict[str, int] | None = None) -> ResultadoTipo:
    wide = dz.matriz_precos(painel.precos) if wide is None else wide
    des = dz.desfechos(wide) if des is None else des
    tercis = tercis_de_liquidez(painel.precos) if tercis is None else tercis
    ctx = rr.contexto(painel, params)
    regra = rr.REGRAS[tipo]

    todos: list[Episodio] = []
    for ativo in sorted(painel.precos["ativo"].unique()):
        _, trans = motor.reproduzir(rr.observacoes(tipo, ativo, ctx), regra, ativo)
        todos.extend(episodios(trans))
    janela = [e for e in todos if de <= e.aberto_em <= ate]

    qa: dict[str, float] = {}
    na: dict[str, float] = {}
    rets, ganhos, antes = [], [], []
    censurados = 0
    for e in janela:
        caiu = dz.celula(des.caiu, e.aberto_em, e.ativo)
        if caiu is None:
            censurados += 1
            continue
        qa[e.ativo] = qa.get(e.ativo, 0.0) + caiu
        na[e.ativo] = na.get(e.ativo, 0.0) + 1.0
        if caiu == 1.0:
            antes.append(dz.celula(des.antecedencia, e.aberto_em, e.ativo))
        r12 = dz.celula(des.ret12, e.aberto_em, e.ativo)
        if r12 is not None:
            rets.append(r12)
            ganhos.append(-r12 - custo_ida_volta(tercis.get(e.ativo, 3)))

    meses = [d for d in des.caiu.index if de <= d <= ate]
    base = des.caiu.loc[meses]
    qb, nb = base.sum(skipna=True), base.notna().sum()
    n_base = int(nb.sum())
    frac_base = float(qb.sum() / n_base) if n_base else None

    n_validos = int(sum(na.values()))
    frac_queda = float(sum(qa.values()) / n_validos) if n_validos else None
    excesso = (frac_queda - frac_base) if frac_queda is not None and frac_base is not None else None
    if n_validos:
        qa_s = pd.Series(qa).reindex(qb.index, fill_value=0.0)
        na_s = pd.Series(na).reindex(qb.index, fill_value=0.0)
        ic_inf, ic_sup = excesso_bootstrap(qa_s.to_numpy(), na_s.to_numpy(),
                                           qb.to_numpy(), nb.to_numpy())
    else:
        ic_inf, ic_sup = None, None
    r_q1, r_med, r_q3 = _quantis(rets)
    rar = raridade(todos, wide, de, ate)
    status, motivo = veredito(n_validos, rar, ic_inf)
    return ResultadoTipo(
        universo=painel.universo, tipo=tipo, fase=fase, de=de, ate=ate, params=params,
        n_episodios=len(janela), n_validos=n_validos, n_ativos=len({e.ativo for e in janela}),
        n_censurados=censurados, frac_queda=frac_queda, frac_base=frac_base, n_base=n_base,
        excesso=excesso, ic_inf=ic_inf, ic_sup=ic_sup, ret_mediana=r_med, ret_q1=r_q1,
        ret_q3=r_q3, ganho_sair_mediana=float(np.median(ganhos)) if ganhos else None,
        antecedencia_mediana_meses=float(np.median(antes)) if antes else None,
        raridade=rar, status=status, motivo=motivo)


def calibrar(painel: Painel, tipo: str, de: date, ate: date
             ) -> tuple[rr.Parametros, list[ResultadoTipo]]:
    """Varia só o parâmetro do tipo na grade. Empate ou nenhum elegível: fica o padrão."""
    wide = dz.matriz_precos(painel.precos)
    des = dz.desfechos(wide)
    tercis = tercis_de_liquidez(painel.precos)
    nome = PARAMETRO_DO_TIPO[tipo]
    candidatos = [replace(rr.PADRAO, **{nome: v}) for v in rr.GRADE[nome]]
    resultados = [avaliar(painel, tipo, p, de, ate, "calibracao", wide=wide, des=des, tercis=tercis)
                  for p in candidatos]
    elegiveis = [(r.ic_inf, p) for p, r in zip(candidatos, resultados)
                 if r.n_validos >= N_MINIMO_EXPERIMENTAL and r.raridade is not None
                 and r.raridade <= rr.ORCAMENTO_RARIDADE and r.ic_inf is not None]
    if not elegiveis:
        return rr.PADRAO, resultados
    melhor = max(v for v, _ in elegiveis)
    vencedores = [p for v, p in elegiveis if v == melhor]
    return (rr.PADRAO if rr.PADRAO in vencedores else vencedores[0]), resultados


# ------------------------------------------------------------------ congelamento

def impressao_das_regras() -> str:
    """sha256 do código que decide os alertas, com CRLF trocado por LF."""
    h = hashlib.sha256()
    for p in _ARQUIVOS_DAS_REGRAS:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def pode_rodar_guardado(docs_dir, impressao: str) -> tuple[bool, str]:
    docs = Path(docs_dir)
    if (docs / motor.ARQ_TRAVA).exists():
        return False, "o período guardado já rodou (trava presente)"
    cong = motor.ler_json(docs / motor.ARQ_CONGELADAS)
    if not cong:
        return False, "regras não congeladas: rode a calibração antes"
    if cong.get("impressao") != impressao:
        return False, "as regras mudaram depois do congelamento"
    return True, "ok"


# ------------------------------------------------------------------ relatório

def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x, casas: int = 1) -> str:
    return "—" if x is None else f"{x:.{casas}f}"


_CAB = ("| universo | tipo | parâmetros | episódios | válidos | censurados | queda > 20% "
        "| taxa-base | excesso | IC 95% | ganho de sair (mediana) | antecedência (meses) "
        "| raridade | status |")
_SEP = "|" + "---|" * 14


def _linha(r: dict) -> str:
    params = ", ".join(f"{k}={v}" for k, v in (r.get("params") or {}).items())
    status = r["status"] if r.get("robusto") else f"{r['status']} (experimental)"
    return (f"| {r['universo']} | {r['tipo']} | {params} | {r['n_episodios']} | {r['n_validos']} "
            f"| {r['n_censurados']} | {_pct(r['frac_queda'])} | {_pct(r['frac_base'])} "
            f"| {_pct(r['excesso'])} | {_pct(r['ic_inf'])} a {_pct(r['ic_sup'])} "
            f"| {_pct(r['ganho_sair_mediana'])} | {_num(r['antecedencia_mediana_meses'])} "
            f"| {_pct(r['raridade'])} | {status}: {r['motivo']} |")


def _tabela(rs: list[dict]) -> str:
    return "\n".join([_CAB, _SEP] + [_linha(r) for r in rs])


def relatorio_md(guardado: list[dict], calibracao: list[dict]) -> str:
    partes = [
        "# Teste PIT dos alertas de risco — fatia 1",
        "",
        "Gerado por `scripts/testar_alertas_pit.py`. Não editar à mão.",
        "",
        "## Critério (spec, seção 8)",
        "",
        "- Fração de alertas seguidos de queda acima de 20% em 12 meses maior que a taxa-base "
        "do universo, com IC 95% (bootstrap por ativo) que não cruza zero.",
        f"- Raridade média até {rr.ORCAMENTO_RARIDADE:.0%} dos ativos com alerta aberto.",
        f"- Menos de {N_MINIMO_EXPERIMENTAL} alertas válidos: amostra insuficiente. "
        f"Menos de {N_MINIMO_ROBUSTO}: experimental.",
        "- O ganho de sair desconta emolumentos e spread pelo tercil de liquidez.",
        "",
        "## Calibração",
        "",
        _tabela(calibracao) if calibracao else "Sem resultado.",
        "",
        "## Período guardado",
        "",
        _tabela(guardado) if guardado else
        "Ainda não rodou. Roda uma vez, depois que as regras forem congeladas.",
        "",
        "## Desvios declarados",
        "",
    ]
    partes += [f"- {d}" for d in DESVIOS_PIT]
    return "\n".join(partes) + "\n"
```

- [ ] **Step 9: Escrever o script**

```python
# scripts/testar_alertas_pit.py
"""Teste PIT dos alertas de risco: calibração e período guardado.

    python scripts/testar_alertas_pit.py --fase calibracao
    python scripts/testar_alertas_pit.py --fase guardado

A calibração escolhe, na grade, o parâmetro de cada tipo e grava
``docs/alertas_ativos/regras_congeladas.json`` com a impressão do código. O
guardado roda UMA vez: recusa se a trava existir, se as regras não estiverem
congeladas ou se o código mudou depois do congelamento. A trava é gravada
ANTES de ler o banco, de modo que uma execução interrompida também conta como
executada; reabrir exige decisão humana registrada no relatório.
Lê só o armazém local.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.alertas_ativos import backtest as bt  # noqa: E402
from core.alertas_ativos import dados_pit as dp  # noqa: E402
from core.alertas_ativos import desfechos as dz  # noqa: E402
from core.alertas_ativos import motor  # noqa: E402
from core.alertas_ativos import regras_risco as rr  # noqa: E402


def _engine():
    from sqlalchemy import create_engine

    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(_warehouse_url())


def _escrever(caminho: Path, dado: dict) -> None:
    caminho.write_text(json.dumps(dado, ensure_ascii=False, indent=2, default=str) + "\n",
                       encoding="utf-8")


def _calibracao(docs: Path, agora: datetime) -> int:
    if (docs / motor.ARQ_TRAVA).exists():
        print("recusado: o período guardado já rodou; recalibrar exige outra janela guardada",
              file=sys.stderr)
        return 2
    eng = _engine()
    parametros, todos = {}, []
    for u in dp.UNIVERSOS:
        painel = dp.carregar_painel(eng, u)
        de, ate = bt.PERIODOS["calibracao"][u]
        for tipo in rr.TIPOS_DO_UNIVERSO[u]:
            escolhido, rs = bt.calibrar(painel, tipo, de, ate)
            parametros[f"{u}/{tipo}"] = asdict(escolhido)
            todos.extend(r.para_dict() for r in rs)
            print(f"{u}/{tipo}: {asdict(escolhido)}")
    _escrever(docs / motor.ARQ_CONGELADAS, {
        "versao": 1, "gerado_em": agora.isoformat(), "impressao": bt.impressao_das_regras(),
        "parametros": parametros, "calibracao": todos})
    (docs / motor.ARQ_RELATORIO).write_text(bt.relatorio_md([], todos), encoding="utf-8")
    return 0


def _guardado(docs: Path, agora: datetime) -> int:
    impressao = bt.impressao_das_regras()
    ok, motivo = bt.pode_rodar_guardado(docs, impressao)
    if not ok:
        print(f"recusado: {motivo}", file=sys.stderr)
        return 2
    _escrever(docs / motor.ARQ_TRAVA,
              {"status": "iniciado", "em": agora.isoformat(), "impressao": impressao})
    cong = motor.ler_json(docs / motor.ARQ_CONGELADAS)
    eng = _engine()
    resultados = []
    for u in dp.UNIVERSOS:
        painel = dp.carregar_painel(eng, u)
        de, ate = bt.PERIODOS["guardado"][u]
        wide = dz.matriz_precos(painel.precos)
        des = dz.desfechos(wide)
        tercis = bt.tercis_de_liquidez(painel.precos)
        for tipo in rr.TIPOS_DO_UNIVERSO[u]:
            params = rr.Parametros(**cong["parametros"][f"{u}/{tipo}"])
            r = bt.avaliar(painel, tipo, params, de, ate, "guardado",
                           wide=wide, des=des, tercis=tercis)
            resultados.append(r.para_dict())
            print(f"{u}/{tipo}: {r.status} ({r.motivo}), n={r.n_validos}")
    _escrever(docs / motor.ARQ_RESULTADO, {
        "versao": 1, "gerado_em": agora.isoformat(), "impressao": impressao,
        "resultados": resultados})
    (docs / motor.ARQ_RELATORIO).write_text(
        bt.relatorio_md(resultados, cong.get("calibracao") or []), encoding="utf-8")
    _escrever(docs / motor.ARQ_TRAVA, {
        "status": "concluido", "em": agora.isoformat(), "impressao": impressao,
        "terminado_em": datetime.now(timezone.utc).isoformat()})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fase", choices=("calibracao", "guardado"), required=True)
    ap.add_argument("--docs-dir", type=Path, default=motor.DOCS_PADRAO)
    a = ap.parse_args(argv)
    a.docs_dir.mkdir(parents=True, exist_ok=True)
    agora = datetime.now(timezone.utc)
    if a.fase == "calibracao":
        return _calibracao(a.docs_dir, agora)
    return _guardado(a.docs_dir, agora)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 10: Rodar os testes**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_motor.py tests/test_alertas_backtest.py -q`
Expected: todos passam (`5 passed` no motor; no backtest, todos os casos, com os seis do `veredito` parametrizado).

Se `test_avaliar_mede_queda_antecedencia_base_e_ganho` falhar em `raridade`, confira se o episódio fecha em 30/06/2020: a regra de deterioração tem horizonte de 365 dias, e o alerta aberto em 30/06/2019 chega a 366 dias em 30/06/2020. O mês do fechamento não conta como aberto (`fechado_em > d`).

- [ ] **Step 11: Medir o tempo no armazém real (sem gravar nada)**

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
import time
from sqlalchemy import create_engine
from scripts.publish_fii_selection_from_local import _warehouse_url
from core.alertas_ativos import backtest as bt, dados_pit as dp, regras_risco as rr
e = create_engine(_warehouse_url())
for u in dp.UNIVERSOS:
    p = dp.carregar_painel(e, u)
    de, ate = bt.PERIODOS["calibracao"][u]
    for tipo in rr.TIPOS_DO_UNIVERSO[u]:
        t = time.time()
        r = bt.avaliar(p, tipo, rr.PADRAO, de, ate, "calibracao")
        print(u, tipo, f"{time.time()-t:.0f}s", r.n_episodios, r.n_validos,
              r.frac_queda, r.frac_base, r.raridade, r.status)
EOF
```

Expected:
- cada `avaliar` em menos de 5 minutos (a calibração roda três por tipo);
- `evento_grave` com `n_episodios = 0` enquanto a ingestão real das Tarefas 4 e 5 não rodou, o que é esperado;
- nenhum arquivo novo em `docs/alertas_ativos/`.

Anote os tempos e os números no corpo do commit, sem ticker. Se `us` passar de 5 minutos por `avaliar`, pré-calcule `rr.contexto` uma vez por parâmetro em `calibrar` (ele já é por parâmetro) e meça de novo antes de seguir. Não mexa nas regras para ganhar tempo.

- [ ] **Step 12: Commit**

```bash
git add core/alertas_ativos/motor.py core/alertas_ativos/desfechos.py \
        core/alertas_ativos/backtest.py scripts/testar_alertas_pit.py \
        tests/test_alertas_motor.py tests/test_alertas_backtest.py
git commit -m "$(cat <<'EOF'
alertas: replay único, desfechos de 12 meses e teste PIT com período guardado

motor.reproduzir é o laço do ciclo de vida que backtest e produção chamam.
O teste mede, por tipo, a fração de alertas seguidos de queda acima de 20%
em 12 meses contra a taxa-base do universo, com IC por bootstrap de ativos,
orçamento de raridade e custos por tercil de liquidez. Censurado sai dos dois
lados. O guardado grava a trava antes de ler o banco e recusa rodar de novo
ou com o código mudado depois do congelamento.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Motor de produção, registro reconciliado, vitrine e Grau de Confiança

**Files:**
- Modify: `core/alertas_ativos/motor.py` (parte 2: `frescor`, `fontes_frescas`, `parametros_congelados`, `status_dos_tipos`, `rodar`)
- Create: `core/alertas_ativos/vitrine.py`
- Create: `scripts/publish_alertas_ativos.py`
- Modify: `core/confianca_secao.py` (constantes perto da linha 106; `_alertas_publicados` depois de `_proventos_do_chat`, perto da linha 425; `confianca_investimentos`, linha 1089)
- Test: `tests/test_alertas_motor.py` (acrescentar), `tests/test_alertas_vitrine.py`

**Interfaces:**
- Consumes:
  - tudo da Tarefa 7 (`motor.reproduzir`, `motor.ler_json`, `motor.ARQ_*`, `desfechos.*`)
  - `registro.ler`, `registro.acrescentar`, `registro.abertos`, `registro.inicio_do_registro`, `registro.verificar`, `registro.aplicar_migracao` (Tarefa 3)
  - `regras_risco.seis_perguntas(alerta, ctx, d)`, `texto_proximo_documento(universo, d)`, `HIPOTESE`, `invalidaria(tipo, params)` (Tarefa 6)
  - `modelos.ROTULO_TIPO`, `NIVEL_DO_TIPO`, `NIVEL_RISCO`, `ABERTOS`, `FECHADOS`, `INVALIDADO`, `EXPIRADO`, `Alerta.de_dict` (Tarefa 2)
  - `core.inteligencia_ativos.arquivo_publicado.ler(caminho: str, rotulo: str) -> dict`
  - `scripts.publish_pregoes_recentes_b3.serializar(payload, caminho)`
  - `core.confianca_secao.Componente`, `_frescor_pct(dias, fresco, velho)`
- Produces:
  - `motor.LIMITES_DIAS`, `motor.FONTES_DO_TIPO`, `motor.VERSAO_VITRINE = 1`, `motor.JANELA_RESOLVIDOS_DIAS = 90`
  - `motor.MOTIVO_REVISTO = "dado revisto: a condição não aparece mais na reprodução"` e `motor.MOTIVO_RECONCILIADO = "estado reconciliado com a reprodução"`
  - `motor.frescor(engine, hoje) -> dict[fonte, {ultimo, dias, limite_dias, fresca, erro}]`
  - `motor.fontes_frescas(frescor, universo, tipo) -> bool`
  - `motor.parametros_congelados(docs_dir) -> dict["universo/tipo", Parametros]`
  - `motor.status_dos_tipos(docs_dir) -> dict["universo/tipo", {origem, experimental, status, motivo, taxa_base}]`
  - `motor.rodar(engine, hoje, *, agora, schema="alertas_ativos", docs_dir=DOCS_PADRAO) -> dict` (o payload da vitrine)
  - vitrine: `{versao, gerado_em, hoje, inicio_registro, cadeia, fontes, tipos, abertos, resolvidos_recentes, placar}`. Cada item de `abertos` é `Alerta.para_dict()` mais `universo`, `rotulo_tipo`, `nivel`, `perguntas` (lista de `[pergunta, resposta]`), `proximo_documento`, `invalidaria`, `hipotese`, `taxa_base`, `experimental`, `mes_referencia`, `detectado_em`, `preco_atual`, `horizonte_ate` (ISO, abertura + `horizonte_dias`) e `confirmar_ate` (ISO, abertura + `validade_confirmacao_dias`; `None` quando a regra não exige confirmação ou o alerta já foi confirmado).
  - `vitrine.CAMINHO`, `vitrine.LIMITE_VELHA_HORAS = 48`, `vitrine.carregar(caminho=CAMINHO) -> dict`, `vitrine.idade_horas(payload, agora=None) -> float | None`, `vitrine.velha(payload, agora=None) -> bool`, `vitrine.filtrar_carteira(payload, tickers) -> list[dict]`, `vitrine.contagem_alta(alertas) -> int`
  - `core.confianca_secao.ALERTAS_PUBLICADOS`, `PESO_ALERTAS = 0.05`, `ALERTAS_FRESCO_DIAS = 2`, `ALERTAS_VELHO_DIAS = 7`, `_alertas_publicados(hoje=None) -> Componente`
  - `scripts/publish_alertas_ativos.py::main(argv=None) -> int`, com `--saida`, `--docs-dir` e `--schema`

**Como o registro acompanha a reprodução.** A reprodução é refeita inteira a cada execução, sobre o dado de hoje. O registro só cresce. Os dois ficam iguais assim:

1. Se o registro já tem um marco (`inicio_do_registro`), toda transição da reprodução com `data_evento` no marco ou depois é acrescentada. O `acrescentar` deduplica por (chave, estado, data_evento, versão).
2. Chave aberta no registro e ausente entre as abertas da reprodução vira `INVALIDADO` com data de hoje e o motivo `MOTIVO_REVISTO`. É o caso do dado revisto que desfaz a condição.
3. Chave aberta na reprodução e ausente do registro entra com a última transição da reprodução. Isso semeia o registro vazio e também cobre o alerta que nasce de uma revisão em data antiga.
4. Chave aberta na reprodução cujo último estado no registro é outro recebe uma transição para o estado da reprodução, com data de hoje e o motivo `MOTIVO_RECONCILIADO`. É o caso da suspensão: a fonte volta, a reprodução não tem mais o `SUSPENSO`, e o registro precisa sair dele. O mesmo vale para o caso inverso: o `SUSPENSO` do fim de mês já gravado colide na deduplicação, e a reconciliação o grava com a data de hoje.

Depois disso, as chaves abertas do registro são exatamente as abertas da reprodução. Um teste verifica isso (regra 3 dos gêmeos).

- [ ] **Step 1: Acrescentar os testes de produção ao `tests/test_alertas_motor.py`**

Acrescente no topo do arquivo os imports que faltam e, no fim, os testes:

```python
# imports a acrescentar no topo de tests/test_alertas_motor.py
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from core.alertas_ativos import dados_pit as dp
from core.alertas_ativos import registro as rg
```

```python
# ------------------------------------------------------------------ frescor e status

def test_frescor_tabela_ausente_e_fonte_velha(monkeypatch):
    monkeypatch.setattr(motor.dp, "_existe", lambda e, t: False)
    fr = motor.frescor(None, date(2026, 11, 9))
    assert set(fr) == set(motor.LIMITES_DIAS)
    assert all(v["fresca"] is False and v["ultimo"] is None and v["dias"] is None
               for v in fr.values())


def test_frescor_que_falha_e_nomeada(monkeypatch):
    def explode(e, t):
        raise RuntimeError("sem banco")
    monkeypatch.setattr(motor.dp, "_existe", explode)
    fr = motor.frescor(None, date(2026, 11, 9))
    assert fr["ipe"]["fresca"] is False and fr["ipe"]["erro"] == "RuntimeError"


def _fr(fresca: bool, **excecoes) -> dict:
    return {f: {"ultimo": None, "dias": None, "limite_dias": lim,
                "fresca": excecoes.get(f, fresca), "erro": None}
            for f, lim in motor.LIMITES_DIAS.items()}


def test_fontes_frescas_exige_todas_as_fontes_do_tipo():
    assert motor.fontes_frescas(_fr(True), "b3_acoes", m.TIPO_EVENTO_GRAVE)
    assert not motor.fontes_frescas(_fr(True, ipe=False), "b3_acoes", m.TIPO_EVENTO_GRAVE)
    assert motor.fontes_frescas(_fr(True, ipe=False), "b3_acoes", m.TIPO_DETERIORACAO)
    assert not motor.fontes_frescas(_fr(True, fii_informe=False), "b3_fii", m.TIPO_FII_RENDA)


def test_parametros_congelados(tmp_path):
    todos = motor.parametros_congelados(tmp_path)
    assert set(todos) == {f"{u}/{t}" for u in dp.UNIVERSOS for t in rr.TIPOS_DO_UNIVERSO[u]}
    assert all(p == rr.PADRAO for p in todos.values())
    (tmp_path / motor.ARQ_CONGELADAS).write_text(json.dumps({"parametros": {
        "b3_fii/fii_renda": {"limiar_preco": 0.10, "limiar_corte": 0.20}}}), encoding="utf-8")
    assert motor.parametros_congelados(tmp_path)["b3_fii/fii_renda"].limiar_corte == 0.20


def _resultado(u, t, status, n, params=None):
    return {"universo": u, "tipo": t, "status": status, "motivo": f"motivo {status}",
            "n_validos": n, "n_censurados": 1, "frac_queda": 0.4, "frac_base": 0.1,
            "ret_q1": -0.3, "ret_mediana": -0.2, "ret_q3": -0.05,
            "params": params or {"limiar_preco": 0.10, "limiar_corte": 0.15}}


def test_status_dos_tipos_convencao_calibracao_e_guardado(tmp_path):
    st = motor.status_dos_tipos(tmp_path)
    assert st["us/deterioracao"]["origem"] == "convenção"
    assert st["us/deterioracao"]["experimental"] and st["us/deterioracao"]["taxa_base"] is None

    (tmp_path / motor.ARQ_CONGELADAS).write_text(json.dumps({
        "parametros": {"us/deterioracao": {"limiar_preco": 0.10, "limiar_corte": 0.15}},
        "calibracao": [_resultado("us", "deterioracao", "aprovado", 40),
                       _resultado("us", "deterioracao", "aprovado", 99,
                                  params={"limiar_preco": 0.05, "limiar_corte": 0.15})]}),
        encoding="utf-8")
    st = motor.status_dos_tipos(tmp_path)["us/deterioracao"]
    assert st["origem"] == "calibração" and st["experimental"]
    assert st["motivo"] == "período guardado ainda não rodou"
    assert st["taxa_base"]["n"] == 40 and st["taxa_base"]["origem"] == "calibração"

    (tmp_path / motor.ARQ_RESULTADO).write_text(json.dumps({"resultados": [
        _resultado("us", "deterioracao", "aprovado", 40),
        _resultado("b3_fii", "fii_renda", "reprovado", 50)]}), encoding="utf-8")
    st = motor.status_dos_tipos(tmp_path)
    assert st["us/deterioracao"]["origem"] == "guardado"
    assert st["us/deterioracao"]["experimental"] is False
    assert st["b3_fii/fii_renda"]["experimental"] is True
    assert st["b3_fii/fii_renda"]["motivo"] == "motivo reprovado"


# ------------------------------------------------------------------ rodar no armazém

AGORA = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
HOJE = date(2026, 11, 9)
MESES_26 = [date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30), date(2026, 7, 31),
            date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30)]
CHAVE = "AAAA3|deterioracao|2026-09-30"


@pytest.fixture
def engine_schema():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        eng = create_engine(_warehouse_url())
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"armazém local indisponível: {exc}")
    schema = f"teste_alertas_{uuid.uuid4().hex[:8]}"
    rg.aplicar_migracao(eng, schema=schema)
    yield eng, schema
    with eng.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def cena(monkeypatch):
    """Um ativo fictício, um tipo, observações controladas; o resto do universo vazio."""
    estado = {"meses": list(MESES_26), "abre": 5, "desfeita_de": None, "fresca": True}

    def painel(engine, u):
        if u != "b3_acoes":
            precos = dp._vazio(dp.COLS_PRECOS)
        else:
            precos = pd.DataFrame([{"ativo": "AAAA3", "mes": d, "preco": 100.0,
                                    "volume_fin": 1e6} for d in estado["meses"]],
                                  columns=dp.COLS_PRECOS)
        return dp.Painel(u, precos, dp._vazio(dp.COLS_FUND), dp._vazio(dp.COLS_DOCS),
                         dp._vazio(dp.COLS_FII_M), dp._vazio(dp.COLS_FII_T), {})

    def observacoes(tipo, ativo, ctx):
        if tipo != m.TIPO_DETERIORACAO:
            return []
        dd = estado["desfeita_de"]
        return [m.Observacao(data=d, presente=estado["abre"] == i,
                             desfeita=True if dd is not None and i >= dd else None,
                             preco=100.0)
                for i, d in enumerate(estado["meses"])]

    monkeypatch.setattr(motor.dp, "carregar_painel", painel)
    monkeypatch.setattr(motor.rr, "observacoes", observacoes)
    monkeypatch.setattr(motor, "frescor", lambda e, h: _fr(estado["fresca"]))
    return estado


def _rodar(eng, schema, tmp_path, hoje=HOJE, agora=AGORA):
    return motor.rodar(eng, hoje, agora=agora, schema=schema, docs_dir=tmp_path)


def test_rodar_semeia_e_e_idempotente(engine_schema, cena, tmp_path):
    eng, schema = engine_schema
    v = _rodar(eng, schema, tmp_path)
    assert [a["chave"] for a in v["abertos"]] == [CHAVE]
    a = v["abertos"][0]
    assert a["estado"] == m.NOVO and a["universo"] == "b3_acoes" and a["experimental"]
    assert len(a["perguntas"]) == 6 and a["mes_referencia"] == "2026-09-30"
    assert a["detectado_em"].startswith("2026-11-09") and a["preco_atual"] == 100.0
    assert a["horizonte_ate"] == "2027-09-30"  # mês de referência + 365 dias
    assert v["cadeia"]["integra"] and v["inicio_registro"] == "2026-11-30"
    assert v["tipos"]["b3_acoes/deterioracao"]["origem"] == "convenção"
    linhas = rg.ler(eng, schema=schema)
    assert [(ln["estado"], ln["data_evento"]) for ln in linhas] == [(m.NOVO, date(2026, 9, 30))]
    # equivalência: o que a vitrine mostra aberto é o que o registro tem aberto
    assert set(rg.abertos(linhas)) == {x["chave"] for x in v["abertos"]}
    _rodar(eng, schema, tmp_path)
    assert len(rg.ler(eng, schema=schema)) == 1
    json.dumps(v)


def test_dado_revisto_invalida_no_registro(engine_schema, cena, tmp_path):
    eng, schema = engine_schema
    _rodar(eng, schema, tmp_path)
    cena["abre"] = None                       # a revisão desfaz a condição
    v = _rodar(eng, schema, tmp_path, hoje=date(2026, 11, 10))
    ultima = rg.ler(eng, schema=schema)[-1]
    assert (ultima["estado"], ultima["data_evento"], ultima["motivo"]) == (
        m.INVALIDADO, date(2026, 11, 10), motor.MOTIVO_REVISTO)
    assert v["abertos"] == []
    assert [r["estado"] for r in v["resolvidos_recentes"]] == [m.INVALIDADO]
    assert v["placar"][m.TIPO_DETERIORACAO]["invalidados"] == 1


def test_suspensao_e_volta_ficam_no_registro(engine_schema, cena, tmp_path):
    eng, schema = engine_schema
    _rodar(eng, schema, tmp_path)
    cena["fresca"] = False
    v = _rodar(eng, schema, tmp_path, hoje=date(2026, 11, 10))
    assert v["abertos"][0]["estado"] == m.SUSPENSO
    assert rg.ler(eng, schema=schema)[-1]["estado"] == m.SUSPENSO
    cena["fresca"] = True
    _rodar(eng, schema, tmp_path, hoje=date(2026, 11, 11))
    ultima = rg.ler(eng, schema=schema)[-1]
    assert (ultima["estado"], ultima["motivo"]) == (m.NOVO, motor.MOTIVO_RECONCILIADO)
    cena["fresca"] = False                    # o SUSPENSO de 30/11 colide na deduplicação
    _rodar(eng, schema, tmp_path, hoje=date(2026, 11, 12))
    linhas = rg.ler(eng, schema=schema)
    assert (linhas[-1]["estado"], linhas[-1]["data_evento"]) == (m.SUSPENSO, date(2026, 11, 12))
    assert rg.verificar(linhas).integra


def test_resolucao_depois_do_marco_entra_pela_reproducao(engine_schema, cena, tmp_path):
    eng, schema = engine_schema
    _rodar(eng, schema, tmp_path)
    cena["meses"] = MESES_26 + [date(2026, 12, 31), date(2027, 1, 31)]
    cena["desfeita_de"] = 8
    depois = datetime(2027, 2, 5, 12, 0, tzinfo=timezone.utc)
    v = _rodar(eng, schema, tmp_path, hoje=date(2027, 2, 5), agora=depois)
    linhas = rg.ler(eng, schema=schema)
    assert [(ln["estado"], ln["data_evento"]) for ln in linhas] == [
        (m.NOVO, date(2026, 9, 30)), (m.RESOLVIDO, date(2027, 1, 31))]
    assert v["abertos"] == []
    r = v["resolvidos_recentes"][0]
    assert r["estado"] == m.RESOLVIDO and r["variacao_desde_abertura"] == 0.0
    p = v["placar"][m.TIPO_DETERIORACAO]
    assert (p["fechados"], p["avaliados"], p["status"]) == (1, 0, "convenção")
```

- [ ] **Step 2: Escrever os testes da vitrine, do publicador e do componente**

```python
# tests/test_alertas_vitrine.py
"""Vitrine dos alertas, publicador e componente do Grau de Confiança."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from core.alertas_ativos import modelos as m
from core.alertas_ativos import vitrine as va

AGORA = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _alerta(ativo, estado=m.NOVO, experimental=False, nivel=m.NIVEL_RISCO):
    return {"ativo": ativo, "estado": estado, "experimental": experimental, "nivel": nivel}


def test_carregar_ausente_devolve_vazio(tmp_path):
    assert va.carregar(tmp_path / "nao_existe.json.gz") == {}


def test_publicar_e_carregar_ida_e_volta(monkeypatch, tmp_path):
    import scripts.publish_alertas_ativos as pub
    payload = {"versao": 1, "gerado_em": AGORA.isoformat(), "abertos": [_alerta("AAAA3")]}
    chamadas = []
    monkeypatch.setattr(pub, "_engine", lambda: "eng")
    monkeypatch.setattr(pub.rg, "aplicar_migracao",
                        lambda e, schema="alertas_ativos": chamadas.append(("mig", schema)))
    monkeypatch.setattr(pub.motor, "rodar",
                        lambda e, hoje, *, agora, schema, docs_dir: chamadas.append(
                            ("rodar", schema)) or payload)
    saida = tmp_path / "alertas_ativos.json.gz"
    assert pub.main(["--saida", str(saida), "--schema", "teste_x"]) == 0
    assert chamadas == [("mig", "teste_x"), ("rodar", "teste_x")]
    assert va.carregar(saida) == payload


def test_idade_e_velha():
    assert va.idade_horas({"gerado_em": (AGORA - timedelta(hours=3)).isoformat()},
                          agora=AGORA) == 3.0
    assert not va.velha({"gerado_em": (AGORA - timedelta(hours=47)).isoformat()}, agora=AGORA)
    assert va.velha({"gerado_em": (AGORA - timedelta(hours=49)).isoformat()}, agora=AGORA)
    assert va.velha({}, agora=AGORA) and va.idade_horas({}, agora=AGORA) is None
    assert va.velha({"gerado_em": "ontem"}, agora=AGORA)


def test_filtrar_carteira_normaliza_o_ticker():
    payload = {"abertos": [_alerta("AAAA3"), _alerta("BBBB11"), _alerta("ZZZ")]}
    assert [a["ativo"] for a in va.filtrar_carteira(payload, [" aaaa3.SA", "zzz"])] == ["AAAA3", "ZZZ"]
    assert va.filtrar_carteira(payload, []) == []
    assert va.filtrar_carteira({}, ["AAAA3"]) == []


def test_contagem_alta_tira_experimental_suspenso_e_atencao():
    alertas = [_alerta("A", m.NOVO), _alerta("B", m.ATUALIZADO),
               _alerta("C", m.SUSPENSO), _alerta("D", m.NOVO, experimental=True),
               _alerta("E", m.NOVO, nivel=m.NIVEL_ATENCAO), {"ativo": "F", "estado": m.NOVO}]
    assert va.contagem_alta(alertas) == 2


def _vitrine_confianca(**kw):
    base = {"gerado_em": "2026-11-09T10:00:00+00:00",
            "cadeia": {"integra": True, "n": 4, "erros": []},
            "abertos": [_alerta("A", experimental=True), _alerta("B")],
            "fontes": {"ipe": {"fresca": False}, "precos_b3": {"fresca": True}}}
    return {**base, **kw}


def test_componente_alertas_publicados(monkeypatch):
    import core.confianca_secao as cs
    monkeypatch.setattr(va, "carregar", lambda caminho=va.CAMINHO: _vitrine_confianca())
    c = cs._alertas_publicados(hoje=date(2026, 11, 10))
    assert (c.nome, c.pct, c.peso) == (cs.ALERTAS_PUBLICADOS, 100.0, cs.PESO_ALERTAS)
    assert "2 alertas abertos (1 experimentais)" in c.evidencia
    assert "fontes velhas: ipe" in c.evidencia
    assert cs._alertas_publicados(hoje=date(2026, 11, 16)).pct == 0.0

    monkeypatch.setattr(va, "carregar", lambda caminho=va.CAMINHO: _vitrine_confianca(
        cadeia={"integra": False, "n": 4, "erros": ["linha 2: hash"]}))
    quebrada = cs._alertas_publicados(hoje=date(2026, 11, 10))
    assert quebrada.pct == 0.0 and "não íntegra" in quebrada.evidencia

    monkeypatch.setattr(va, "carregar", lambda caminho=va.CAMINHO: _vitrine_confianca(
        gerado_em="ontem"))
    assert cs._alertas_publicados(hoje=date(2026, 11, 10)).pct == 0.0

    monkeypatch.setattr(va, "carregar", lambda caminho=va.CAMINHO: {})
    ausente = cs._alertas_publicados(hoje=date(2026, 11, 10))
    assert ausente.pct == 0.0 and "ausente" in ausente.evidencia


def test_investimentos_mede_os_alertas_publicados(monkeypatch):
    import core.confianca_secao as cs

    class _Morta:
        def connect(self):
            raise RuntimeError("sem banco")

        begin = connect

    monkeypatch.setattr("core.database.get_engine", lambda *a, **k: _Morta())
    monkeypatch.setattr(va, "carregar", lambda caminho=va.CAMINHO: _vitrine_confianca())
    s = cs.confianca_investimentos(_Morta(), user_id="dono")
    comp = next(c for c in s.componentes if c.nome == cs.ALERTAS_PUBLICADOS)
    # arquivo publicado: segue medido com o banco fora
    assert comp.medido and comp.peso == cs.PESO_ALERTAS
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_motor.py tests/test_alertas_vitrine.py -q`
Expected: FAIL com `AttributeError: module 'core.alertas_ativos.motor' has no attribute 'frescor'` e `ModuleNotFoundError: No module named 'core.alertas_ativos.vitrine'`.

- [ ] **Step 4: Escrever `motor.py` (parte 2)**

Troque o bloco de imports da parte 1 por este e acrescente o resto no fim do arquivo:

```python
# imports de core/alertas_ativos/motor.py (substituem os da parte 1)
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from core.alertas_ativos import ciclo_vida as cv
from core.alertas_ativos import dados_pit as dp
from core.alertas_ativos import desfechos as dz
from core.alertas_ativos import modelos as m
from core.alertas_ativos import registro as rg
from core.alertas_ativos import regras_risco as rr
from core.memoria_mercado.amostra import N_MINIMO_EXPERIMENTAL, N_MINIMO_ROBUSTO
```

```python
# fim de core/alertas_ativos/motor.py

VERSAO_VITRINE = 1
JANELA_RESOLVIDOS_DIAS = 90
MOTIVO_REVISTO = "dado revisto: a condição não aparece mais na reprodução"
MOTIVO_RECONCILIADO = "estado reconciliado com a reprodução"

#: Idade máxima, em dias, para a fonte contar como fresca. Preço em 40 porque a
#: série é reduzida a fim de mês (desvio 19).
LIMITES_DIAS = {"precos_b3": 40, "precos_us": 40, "ipe": 7, "sec_8k": 7, "fii_informe": 45}

FONTES_DO_TIPO: dict[tuple[str, str], tuple[str, ...]] = {
    ("b3_acoes", m.TIPO_EVENTO_GRAVE): ("precos_b3", "ipe"),
    ("us", m.TIPO_EVENTO_GRAVE): ("precos_us", "sec_8k"),
    ("b3_acoes", m.TIPO_DETERIORACAO): ("precos_b3",),
    ("us", m.TIPO_DETERIORACAO): ("precos_us",),
    ("b3_fii", m.TIPO_FII_RENDA): ("precos_b3", "fii_informe"),
}

_SQL_FRESCOR: dict[str, tuple[str, str]] = {
    "precos_b3": ("market.historical_prices",
                  "SELECT max(date) FROM market.historical_prices"),
    "precos_us": ("market_us.prices_monthly",
                  "SELECT max(month_end) FROM market_us.prices_monthly"),
    "ipe": ("market.cvm_ipe_metadados",
            "SELECT max(data_entrega) FROM market.cvm_ipe_metadados"),
    "sec_8k": ("market_us.sec_filings",
               "SELECT max(filing_date) FROM market_us.sec_filings WHERE form = '8-K'"),
    "fii_informe": ("market.fii_metric_observations",
                    "SELECT max(available_at) FROM market.fii_metric_observations "
                    "WHERE source = 'cvm_informe_mensal'"),
}


def frescor(engine, hoje: date) -> dict[str, dict]:
    """Última data de cada fonte. Tabela ausente ou consulta que falha: fonte velha, nomeada."""
    saida = {}
    for fonte, (tabela, sql) in _SQL_FRESCOR.items():
        ultimo, erro = None, None
        try:
            if dp._existe(engine, tabela):
                with engine.connect() as c:
                    v = c.execute(text(sql)).scalar()
                ultimo = pd.Timestamp(v).date() if v is not None else None
        except Exception as exc:  # noqa: BLE001 - a fonte que falha é nomeada, não some
            erro = type(exc).__name__
        dias = (hoje - ultimo).days if ultimo is not None else None
        saida[fonte] = {"ultimo": ultimo.isoformat() if ultimo else None, "dias": dias,
                        "limite_dias": LIMITES_DIAS[fonte],
                        "fresca": dias is not None and dias <= LIMITES_DIAS[fonte],
                        "erro": erro}
    return saida


def fontes_frescas(fr: dict, universo: str, tipo: str) -> bool:
    return all(fr[f]["fresca"] for f in FONTES_DO_TIPO[(universo, tipo)])


def _chaves_dos_tipos() -> list[str]:
    return [f"{u}/{t}" for u in dp.UNIVERSOS for t in rr.TIPOS_DO_UNIVERSO[u]]


def parametros_congelados(docs_dir=DOCS_PADRAO) -> dict[str, rr.Parametros]:
    cong = ler_json(Path(docs_dir) / ARQ_CONGELADAS).get("parametros") or {}
    return {k: rr.Parametros(**cong[k]) if k in cong else rr.PADRAO for k in _chaves_dos_tipos()}


def _taxa_base(r: dict, origem: str) -> dict:
    return {"frac_queda": r.get("frac_queda"), "frac_base": r.get("frac_base"),
            "n": r.get("n_validos"), "n_censurados": r.get("n_censurados"),
            "ret_q1": r.get("ret_q1"), "ret_mediana": r.get("ret_mediana"),
            "ret_q3": r.get("ret_q3"), "origem": origem}


def status_dos_tipos(docs_dir=DOCS_PADRAO) -> dict[str, dict]:
    """De onde vem o número de cada tipo: guardado, calibração ou convenção.

    Só sai de experimental o tipo aprovado no guardado com ``n`` robusto.
    Antes do guardado, a taxa-base mostrada é a da calibração com os parâmetros
    congelados (desvio 22).
    """
    docs = Path(docs_dir)
    guardado = {f"{r['universo']}/{r['tipo']}": r
                for r in ler_json(docs / ARQ_RESULTADO).get("resultados") or []}
    cong = ler_json(docs / ARQ_CONGELADAS)
    params = cong.get("parametros") or {}
    calib = {}
    for r in cong.get("calibracao") or []:
        k = f"{r['universo']}/{r['tipo']}"
        if k in params and r.get("params") == params[k]:
            calib[k] = r
    saida = {}
    for k in _chaves_dos_tipos():
        if k in guardado:
            r = guardado[k]
            experimental = not (r["status"] == "aprovado" and r["n_validos"] >= N_MINIMO_ROBUSTO)
            saida[k] = {"origem": "guardado", "experimental": experimental, "status": r["status"],
                        "motivo": r["motivo"], "taxa_base": _taxa_base(r, "guardado")}
        elif k in calib:
            r = calib[k]
            saida[k] = {"origem": "calibração", "experimental": True, "status": r["status"],
                        "motivo": "período guardado ainda não rodou",
                        "taxa_base": _taxa_base(r, "calibração")}
        else:
            saida[k] = {"origem": "convenção", "experimental": True, "status": "convenção",
                        "motivo": "sem teste PIT", "taxa_base": None}
    return saida


def _reconciliar(linhas: list[dict], abertos_replay: dict, ultimas: dict,
                 hoje: date) -> list[m.Transicao]:
    ultimo_estado = {}
    for ln in linhas:
        ultimo_estado[ln["chave"]] = ln["estado"]
    saida = []
    for ch, d in rg.abertos(linhas).items():
        if ch not in abertos_replay:
            a = m.Alerta.de_dict(d).com(estado=m.INVALIDADO, atualizado_em=hoje)
            saida.append(m.Transicao(a, m.INVALIDADO, hoje, MOTIVO_REVISTO))
    for ch, (a, *_resto) in abertos_replay.items():
        if ch not in ultimo_estado:
            saida.append(ultimas[ch])
        elif ultimo_estado[ch] != a.estado:
            saida.append(m.Transicao(a, a.estado, hoje, MOTIVO_RECONCILIADO))
    return saida


def _placar(linhas: list[dict], universo_do: dict[str, str],
            caiu: dict[str, pd.DataFrame]) -> dict[str, dict]:
    ultimo = {}
    for ln in linhas:
        ultimo[ln["chave"]] = ln
    saida = {}
    for tipo, rotulo in m.ROTULO_TIPO.items():
        fechados = [ln for ln in ultimo.values()
                    if ln["tipo"] == tipo and ln["estado"] in m.FECHADOS]
        avaliados = quedas = 0
        for ln in fechados:
            u = universo_do.get(ln["ativo"])
            if u is None:
                continue
            v = dz.celula(caiu[u], date.fromisoformat(ln["alerta"]["aberto_em"]), ln["ativo"])
            if v is not None:
                avaliados += 1
                quedas += int(v == 1.0)
        saida[tipo] = {
            "rotulo": rotulo, "fechados": len(fechados), "avaliados": avaliados,
            "quedas_acima_20": quedas,
            "invalidados": sum(ln["estado"] == m.INVALIDADO for ln in fechados),
            "expirados": sum(ln["estado"] == m.EXPIRADO for ln in fechados),
            "status": "convenção" if avaliados < N_MINIMO_EXPERIMENTAL else "medido"}
    return saida


def _somar_dias(d: date, dias: int | None) -> str | None:
    return None if dias is None else (d + timedelta(days=dias)).isoformat()


def rodar(engine, hoje: date, *, agora: datetime, schema: str = "alertas_ativos",
          docs_dir=DOCS_PADRAO) -> dict:
    """Reproduz os três tipos, reconcilia o registro e devolve o payload da vitrine."""
    fr = frescor(engine, hoje)
    params = parametros_congelados(docs_dir)
    status = status_dos_tipos(docs_dir)

    transicoes: list[m.Transicao] = []
    abertos_replay: dict[str, tuple] = {}
    preco_atual: dict[str, float | None] = {}
    universo_do: dict[str, str] = {}
    caiu: dict[str, pd.DataFrame] = {}
    for u in dp.UNIVERSOS:
        painel = dp.carregar_painel(engine, u)
        ativos = sorted(painel.precos["ativo"].unique())
        if not ativos:  # universo sem preço no armazém: nada a observar
            caiu[u] = pd.DataFrame()
            continue
        caiu[u] = dz.desfechos(dz.matriz_precos(painel.precos)).caiu
        universo_do.update({a: u for a in ativos})
        for tipo in rr.TIPOS_DO_UNIVERSO[u]:
            ctx = rr.contexto(painel, params[f"{u}/{tipo}"])
            frescas = fontes_frescas(fr, u, tipo)
            regra = rr.REGRAS[tipo]
            for ativo in ativos:
                obs = rr.observacoes(tipo, ativo, ctx)
                if not obs:
                    continue
                if not frescas:
                    obs[-1] = replace(obs[-1], fontes_frescas=False)
                alerta, trans = reproduzir(obs, regra, ativo)
                transicoes.extend(trans)
                preco_atual[ativo] = obs[-1].preco
                if alerta is not None and alerta.estado in m.ABERTOS:
                    abertos_replay[alerta.chave] = (alerta, u, ctx, obs[-1].data)

    linhas = rg.ler(engine, schema=schema)
    inicio = rg.inicio_do_registro(linhas)
    if inicio is not None:
        rg.acrescentar(engine, [t for t in transicoes if t.data_evento >= inicio],
                       agora=agora, schema=schema)
        linhas = rg.ler(engine, schema=schema)
    ultimas = {}
    for t in transicoes:
        ultimas[t.alerta.chave] = t
    rg.acrescentar(engine, _reconciliar(linhas, abertos_replay, ultimas, hoje),
                   agora=agora, schema=schema)
    linhas = rg.ler(engine, schema=schema)

    detectado: dict[str, datetime] = {}
    for ln in linhas:
        detectado.setdefault(ln["chave"], ln["registrado_em"])
    abertos = []
    for ch, (a, u, ctx, d) in sorted(abertos_replay.items()):
        st = status[f"{u}/{a.tipo}"]
        abertos.append({
            **a.para_dict(), "universo": u, "rotulo_tipo": m.ROTULO_TIPO[a.tipo],
            "nivel": m.NIVEL_DO_TIPO[a.tipo],
            "perguntas": [list(p) for p in rr.seis_perguntas(a, ctx, d)],
            "proximo_documento": rr.texto_proximo_documento(u, hoje),
            "invalidaria": rr.invalidaria(a.tipo, ctx.params),
            "hipotese": rr.HIPOTESE[a.tipo],
            "taxa_base": st["taxa_base"], "experimental": st["experimental"],
            "mes_referencia": a.aberto_em.isoformat(),
            "detectado_em": detectado[ch].isoformat() if ch in detectado else None,
            "preco_atual": preco_atual.get(a.ativo),
            "horizonte_ate": _somar_dias(a.aberto_em, rr.REGRAS[a.tipo].horizonte_dias),
            "confirmar_ate": (None if a.confirmado
                              else _somar_dias(a.aberto_em,
                                               rr.REGRAS[a.tipo].validade_confirmacao_dias))})

    limite = agora - timedelta(days=JANELA_RESOLVIDOS_DIAS)
    resolvidos = []
    for ln in linhas:
        if ln["estado"] not in m.FECHADOS or ln["registrado_em"] < limite:
            continue
        p0, p1 = ln["alerta"].get("preco_abertura"), preco_atual.get(ln["ativo"])
        var = (p1 / p0 - 1) if p0 is not None and p0 > 0 and p1 is not None else None
        resolvidos.append({
            "chave": ln["chave"], "ativo": ln["ativo"], "tipo": ln["tipo"],
            "rotulo_tipo": m.ROTULO_TIPO.get(ln["tipo"], ln["tipo"]), "estado": ln["estado"],
            "data_evento": ln["data_evento"].isoformat(), "motivo": ln["motivo"],
            "mes_referencia": ln["alerta"]["aberto_em"], "variacao_desde_abertura": var})

    cadeia = rg.verificar(linhas)
    inicio = rg.inicio_do_registro(linhas)
    return {
        "versao": VERSAO_VITRINE, "gerado_em": agora.isoformat(), "hoje": hoje.isoformat(),
        "inicio_registro": inicio.isoformat() if inicio else None,
        "cadeia": {"integra": cadeia.integra, "n": cadeia.n, "ultimo_hash": cadeia.ultimo_hash,
                   "erros": list(cadeia.erros)[:20]},
        "fontes": fr, "tipos": status, "abertos": abertos,
        "resolvidos_recentes": resolvidos, "placar": _placar(linhas, universo_do, caiu)}
```

O `json` da parte 1 continua em uso por `ler_json`. O `cv` continua em uso por `reproduzir`.

- [ ] **Step 5: Escrever `vitrine.py`**

```python
# core/alertas_ativos/vitrine.py
"""Leitura da vitrine dos alertas (``data/public/alertas_ativos.json.gz``).

A vitrine não tem dado pessoal: lista os alertas abertos de todo o universo.
O filtro pela carteira acontece aqui, na tela, com os tickers do usuário.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.alertas_ativos import modelos as m
from core.inteligencia_ativos.arquivo_publicado import ler

CAMINHO = Path(__file__).resolve().parents[2] / "data" / "public" / "alertas_ativos.json.gz"
LIMITE_VELHA_HORAS = 48


def carregar(caminho=CAMINHO) -> dict:
    """Payload publicado; ``{}`` se ausente ou ilegível."""
    return ler(str(caminho), "alertas_ativos")


def idade_horas(payload: dict, agora: datetime | None = None) -> float | None:
    try:
        gerado = datetime.fromisoformat(str(payload["gerado_em"]))
    except (KeyError, ValueError):
        return None
    if gerado.tzinfo is None:
        gerado = gerado.replace(tzinfo=timezone.utc)
    agora = agora or datetime.now(timezone.utc)
    return (agora - gerado).total_seconds() / 3600


def velha(payload: dict, agora: datetime | None = None) -> bool:
    h = idade_horas(payload, agora)
    return h is None or h > LIMITE_VELHA_HORAS


def _normalizar(ticker) -> str:
    return str(ticker).strip().upper().removesuffix(".SA")


def filtrar_carteira(payload: dict, tickers) -> list[dict]:
    alvo = {_normalizar(t) for t in tickers}
    return [a for a in payload.get("abertos") or [] if _normalizar(a["ativo"]) in alvo]


def contagem_alta(alertas: list[dict]) -> int:
    """Alertas de Risco de tese, novos ou atualizados, de tipo não experimental."""
    return sum(1 for a in alertas
               if a.get("nivel") == m.NIVEL_RISCO and a.get("estado") in (m.NOVO, m.ATUALIZADO)
               and a.get("experimental") is False)
```

- [ ] **Step 6: Escrever o publicador**

```python
# scripts/publish_alertas_ativos.py
"""Publica a vitrine dos alertas a partir do armazém local.

    python scripts/publish_alertas_ativos.py
    python scripts/publish_alertas_ativos.py --schema teste_alertas_x --saida /tmp/a.json.gz

Reproduz os três tipos de risco, reconcilia o registro imutável
(``alertas_ativos.registro``, só no armazém) e grava
``data/public/alertas_ativos.json.gz``. Não escreve no Supabase.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.alertas_ativos import motor  # noqa: E402
from core.alertas_ativos import registro as rg  # noqa: E402
from core.alertas_ativos import vitrine  # noqa: E402
from scripts.publish_pregoes_recentes_b3 import serializar  # noqa: E402


def _engine():
    from sqlalchemy import create_engine

    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(_warehouse_url())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", type=Path, default=vitrine.CAMINHO)
    ap.add_argument("--docs-dir", type=Path, default=motor.DOCS_PADRAO)
    ap.add_argument("--schema", default="alertas_ativos")
    a = ap.parse_args(argv)
    eng = _engine()
    rg.aplicar_migracao(eng, schema=a.schema)
    agora = datetime.now(timezone.utc)
    hoje = agora.astimezone(ZoneInfo("America/Sao_Paulo")).date()
    payload = motor.rodar(eng, hoje, agora=agora, schema=a.schema, docs_dir=a.docs_dir)
    serializar(payload, a.saida)
    print(f"{len(payload['abertos'])} alertas abertos; cadeia íntegra: "
          f"{payload['cadeia']['integra']}; gravado em {a.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Acrescentar o componente ao Grau de Confiança**

Em `core/confianca_secao.py`, logo depois de `PROVENTOS_FRESCO_DIAS = 3` (perto da linha 111):

```python
ALERTAS_PUBLICADOS = "Alertas publicados"
PESO_ALERTAS = 0.05
ALERTAS_FRESCO_DIAS = 2
ALERTAS_VELHO_DIAS = 7
```

Logo depois do fim de `_proventos_do_chat` (antes de `def _scalar`, perto da linha 425):

```python
def _alertas_publicados(hoje: date | None = None) -> Componente:
    """Idade e integridade da vitrine dos alertas (aba 🚨 Alertas).

    Zero quando a vitrine falta, quando a cadeia de hashes do registro não
    fecha ou quando ela passa de ``ALERTAS_VELHO_DIAS``. A evidência diz
    quantos alertas são experimentais e quais fontes estavam velhas.
    """
    from core.alertas_ativos import vitrine as va
    peso = PESO_ALERTAS
    p = va.carregar()
    if not p:
        return Componente(ALERTAS_PUBLICADOS, 0.0, peso,
                          "vitrine alertas_ativos ausente ou ilegivel: a aba fica sem alertas")
    cadeia = p.get("cadeia") or {}
    if not cadeia.get("integra"):
        return Componente(ALERTAS_PUBLICADOS, 0.0, peso,
                          f"cadeia de hashes do registro não íntegra "
                          f"({len(cadeia.get('erros') or [])} erros)")
    try:
        gerado = datetime.fromisoformat(str(p["gerado_em"])).date()
    except (KeyError, ValueError):
        return Componente(ALERTAS_PUBLICADOS, 0.0, peso,
                          f"data da vitrine ilegivel ({p.get('gerado_em')!r})")
    dias = ((hoje or date.today()) - gerado).days
    abertos = p.get("abertos") or []
    exp = sum(1 for a in abertos if a.get("experimental"))
    velhas = sorted(f for f, v in (p.get("fontes") or {}).items() if not v.get("fresca"))
    evid = (f"vitrine de {gerado:%d/%m}, {len(abertos)} alertas abertos ({exp} experimentais); "
            f"registro com {cadeia.get('n')} linhas íntegras")
    if velhas:
        evid += f"; fontes velhas: {', '.join(velhas)}"
    return Componente(ALERTAS_PUBLICADOS,
                      _frescor_pct(dias, ALERTAS_FRESCO_DIAS, ALERTAS_VELHO_DIAS), peso, evid)
```

Confira se `datetime` já está importado no topo de `core/confianca_secao.py`:

Run: `grep -n "^from datetime import" core/confianca_secao.py`
Expected: uma linha. Se ela não trouxer `datetime`, acrescente-o à mesma linha (por exemplo, `from datetime import date, datetime, timedelta`).

Em `confianca_investimentos`, logo depois de `comps.append(_proventos_do_chat())` (linha 1089):

```python
    # A aba 🚨 Alertas lê a vitrine publicada, não o banco.
    comps.append(_alertas_publicados())
```

- [ ] **Step 8: Rodar os testes**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_motor.py tests/test_alertas_vitrine.py tests/test_pregoes_recentes.py -q`
Expected: todos passam. Com o armazém desligado, os quatro testes de `rodar` aparecem como `skipped` (`armazém local indisponível`). Antes de seguir, ligue o Docker (`docker start dfu_warehouse`) e rode de novo até os quatro passarem: eles são a única prova da reconciliação.

- [ ] **Step 9: Exercitar no armazém real, num schema de teste**

```bash
PYTHONIOENCODING=utf-8 "$PY" scripts/publish_alertas_ativos.py --schema teste_alertas_smoke \
  --saida "/c/Users/TIAGOB~1/AppData/Local/Temp/claude/3229ec419e62e14f/067f7df2-c824-4c00-98ae-5e1106794f6e/scratchpad/alertas_smoke.json.gz"
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
from sqlalchemy import create_engine, text
from scripts.publish_fii_selection_from_local import _warehouse_url
from core.alertas_ativos import vitrine as va
p = va.carregar("/c/Users/TIAGOB~1/AppData/Local/Temp/claude/3229ec419e62e14f/067f7df2-c824-4c00-98ae-5e1106794f6e/scratchpad/alertas_smoke.json.gz")
print({k: (v["fresca"], v["dias"]) for k, v in p["fontes"].items()})
print(len(p["abertos"]), "abertos;", {k: v["origem"] for k, v in p["tipos"].items()})
print("cadeia", p["cadeia"]["integra"], p["cadeia"]["n"])
e = create_engine(_warehouse_url())
with e.begin() as c:
    c.execute(text('DROP SCHEMA "teste_alertas_smoke" CASCADE'))
EOF
```

Expected:
- `precos_b3` e `fii_informe` frescos; `ipe` e `sec_8k` velhos até a Tarefa 11 rodar a ingestão real, o que é esperado e deixa `evento_grave` suspenso ou fechado;
- todos os tipos com origem `convenção`, porque a calibração ainda não rodou;
- cadeia íntegra;
- o schema de teste apagado no fim.

Não grave em `data/public/` nem no schema `alertas_ativos`. A primeira publicação real fica na Tarefa 11. Anote no corpo do commit o tempo e a contagem de abertos por tipo, sem ticker.

- [ ] **Step 10: Commit**

```bash
git add core/alertas_ativos/motor.py core/alertas_ativos/vitrine.py \
        scripts/publish_alertas_ativos.py core/confianca_secao.py \
        tests/test_alertas_motor.py tests/test_alertas_vitrine.py
git commit -m "$(cat <<'EOF'
alertas: motor de produção, registro reconciliado com a reprodução e vitrine

rodar refaz a reprodução sobre o dado de hoje e reconcilia o registro: grava
o que mudou depois do marco, invalida o que a revisão desfez, semeia o que
nunca entrou e tira o registro da suspensão quando a fonte volta. Fonte velha
suspende o tipo e é nomeada no payload. O Grau de Confiança de Investimentos
ganha o componente Alertas publicados (idade e cadeia de hashes).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---


### Task 9: Aba 🚨 Alertas em Investimentos

A aba lê só a vitrine. Ela filtra pela carteira na hora de desenhar e não tem botão de comprar nem de vender. A estrutura de Investimentos muda (cinco abas viram seis), por isso as três abas de ⚙️ Configurações mudam neste mesmo commit.

**Files:**
- Create: `views/investimentos_alertas.py`
- Create: `tests/test_investimentos_alertas.py`
- Modify: `views/investimentos.py:5113-5133` (`_aba_mantida`) e `views/investimentos.py:5196-5246` (abas em `render`)
- Modify: `tests/test_investimentos_abas_sob_demanda.py`
- Modify: `tests/test_configuracoes_sincronizadas.py:91-101` (`_abas_de_investimentos`)
- Modify: `views/documentacao.py` (novo `FLOW_ALERTAS` depois de `FLOW_INVESTIMENTOS`, nó `alertas_ai`, `cfg_documentacao`, subtítulo de `FLOW_INTELIGENCIA_IR`, `FLOWS`, `tab_labels` e `fluxos` de `render_corpo`, `impacto` em `_generic_flow_detail`)
- Modify: `docs/mapa_app/areas/investimentos.json`, `docs/mapa_app/areas/configuracoes.json`
- Regenerate: `docs/mapa_app/mapa.json` (`python scripts/montar_mapa_app.py`)

**Interfaces:**
- Consumes:
  - `core.alertas_ativos.vitrine`: `CAMINHO`, `LIMITE_VELHA_HORAS`, `carregar(caminho)`, `idade_horas(payload, agora=None)`, `velha(payload, agora=None)`, `filtrar_carteira(payload, tickers) -> list[dict]` e `contagem_alta(alertas) -> int` (Task 8).
  - `core.alertas_ativos.modelos`: os estados, os níveis, os tipos, `ROTULO_TIPO` e `GRAVIDADE_BASE` (Task 1).
  - `core.memoria_mercado.amostra.N_MINIMO_EXPERIMENTAL` (já existe, vale 8).
  - `core.proventos.tickers_em_carteira(carteira) -> set` (já existe).
- Produces (usado pela Task 10):
  - `ROTULO = "Alertas"` e `ICONE = "🚨"`.
  - `rotulo_aba(n: int) -> str` devolve `"🚨  Alertas"` ou `"🚨  Alertas (n)"`.
  - `contagem_da_carteira(tickers, payload=None) -> int`.
  - `selo_do_ativo(ticker, payload=None) -> str | None`.
  - `resumo_da_carteira(tickers, payload=None, agora=None) -> dict` com as chaves `publicada`, `velha`, `alta`, `experimentais` e `ativos`.
  - `cartao_alerta(a: dict, tipos: dict | None = None) -> str`, uma linha de HTML.
  - `render(tickers, *, payload=None, rotulo_ia=None, chave_aba=None, agora=None) -> None`.

Quando `payload is None`, as funções leem `vitrine.carregar(vitrine.CAMINHO)`. O caminho é passado explícito porque o padrão de `carregar` foi fixado na definição e um teste que troca `vitrine.CAMINHO` não chegaria a ele. A view não usa `st.cache_data`: `arquivo_publicado.ler` já memoiza pela assinatura do arquivo, e um cache do Streamlit vazaria entre testes.

- [ ] **Step 1: Escrever os testes da aba**

```python
# tests/test_investimentos_alertas.py
"""Subaba 🚨 Alertas de Investimentos.

O repositório é público: só tickers fictícios (AAAA3, BBBB11, ZZZZ3) e
nenhum valor do usuário.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from core.alertas_ativos import modelos as m
from views import investimentos_alertas as al

AGORA = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _ev(descricao="Fato relevante: renúncia do diretor financeiro", *, familia="documento",
        data="2026-09-30", url="https://exemplo.invalid/doc"):
    return {"familia": familia, "codigo": "EVENTO", "descricao": descricao, "data": data,
            "fonte": "CVM/IPE", "url": url}


def _alerta(ativo, *, estado=m.NOVO, experimental=False, nivel=m.NIVEL_RISCO,
            tipo=m.TIPO_EVENTO_GRAVE, universo="b3_acoes", evidencias=None,
            taxa_base=None, confirmar_ate=None, aberto_em="2026-09-30"):
    return {
        "ativo": ativo, "tipo": tipo, "versao_regra": "1.0.0", "estado": estado,
        "gravidade": m.GRAVIDADE_BASE, "aberto_em": aberto_em, "atualizado_em": aberto_em,
        "evidencias": list(evidencias) if evidencias is not None else [_ev()],
        "saidas_consecutivas": 0, "estado_antes_suspensao": None, "preco_abertura": 10.0,
        "confirmado": confirmar_ate is None, "chave": f"{ativo}|{tipo}|{aberto_em}",
        "universo": universo, "rotulo_tipo": m.ROTULO_TIPO[tipo], "nivel": nivel,
        "perguntas": [[f"Pergunta {i}?", f"Resposta {i}."] for i in range(1, 7)],
        "proximo_documento": "Próximo ITR até 14/11/2026.",
        "invalidaria": "Retificação do fato relevante.",
        "hipotese": "O fato pode quebrar a tese.",
        "taxa_base": taxa_base, "experimental": experimental,
        "mes_referencia": aberto_em, "detectado_em": "2026-11-09T06:00:00+00:00",
        "preco_atual": 9.0, "horizonte_ate": "2027-09-30", "confirmar_ate": confirmar_ate,
    }


def _payload(abertos, *, gerado_em=AGORA, resolvidos=(), fontes=None):
    return {
        "versao": 1, "gerado_em": gerado_em.isoformat(), "hoje": "2026-11-09",
        "inicio_registro": "2026-11-09",
        "cadeia": {"integra": True, "n": len(abertos), "ultimo_hash": "x", "erros": []},
        "fontes": fontes or {"ipe": {"ultimo": "2026-11-08", "dias": 1, "limite_dias": 3,
                                      "fresca": True, "erro": None}},
        "tipos": {"b3_acoes/evento_grave": {
            "origem": "convenção", "experimental": True, "status": "convenção",
            "motivo": "sem teste PIT", "taxa_base": None}},
        "abertos": list(abertos), "resolvidos_recentes": list(resolvidos),
        "placar": {m.TIPO_EVENTO_GRAVE: {
            "rotulo": m.ROTULO_TIPO[m.TIPO_EVENTO_GRAVE], "fechados": 1, "avaliados": 0,
            "quedas_acima_20": 0, "invalidados": 1, "expirados": 0, "status": "convenção"}},
    }


# ── funções puras ────────────────────────────────────────────────────────────

def test_rotulo_da_aba_leva_a_contagem_so_quando_ha_alerta():
    assert al.rotulo_aba(0) == "🚨  Alertas"
    assert al.rotulo_aba(3) == "🚨  Alertas (3)"


def test_contagem_e_da_carteira_e_so_de_risco_nao_experimental():
    p = _payload([
        _alerta("AAAA3"),
        _alerta("BBBB11", universo="b3_fii", tipo=m.TIPO_FII_RENDA),
        _alerta("AAAA3", experimental=True, tipo=m.TIPO_DETERIORACAO),
        _alerta("BBBB11", nivel=m.NIVEL_ATENCAO),
        _alerta("BBBB11", estado=m.SUSPENSO),
        _alerta("ZZZZ3"),                                   # fora da carteira
    ])
    assert al.contagem_da_carteira(["aaaa3.SA", " BBBB11 "], payload=p) == 2
    assert al.contagem_da_carteira(["ZZZZ3"], payload=p) == 1
    assert al.contagem_da_carteira([], payload=p) == 0


def test_sem_vitrine_a_contagem_e_zero():
    assert al.contagem_da_carteira(["AAAA3"], payload={}) == 0


def test_selo_do_ativo_prefere_o_nao_experimental_mais_recente():
    p = _payload([
        _alerta("AAAA3", experimental=True, tipo=m.TIPO_DETERIORACAO, aberto_em="2026-10-31"),
        _alerta("AAAA3", aberto_em="2026-08-31"),
        _alerta("AAAA3", nivel=m.NIVEL_ATENCAO, aberto_em="2026-10-31"),
    ])
    assert al.selo_do_ativo("aaaa3", payload=p) == m.ROTULO_TIPO[m.TIPO_EVENTO_GRAVE]


def test_selo_experimental_vem_marcado_e_ausente_e_none():
    p = _payload([_alerta("AAAA3", experimental=True, tipo=m.TIPO_DETERIORACAO)])
    assert al.selo_do_ativo("AAAA3", payload=p) == (
        m.ROTULO_TIPO[m.TIPO_DETERIORACAO] + " (experimental)")
    assert al.selo_do_ativo("ZZZZ3", payload=p) is None
    assert al.selo_do_ativo("AAAA3", payload={}) is None


def test_resumo_da_carteira_para_o_dashboard():
    p = _payload([_alerta("AAAA3"),
                  _alerta("BBBB11", experimental=True, tipo=m.TIPO_FII_RENDA, universo="b3_fii"),
                  _alerta("ZZZZ3")])
    assert al.resumo_da_carteira(["AAAA3", "BBBB11"], payload=p, agora=AGORA) == {
        "publicada": True, "velha": False, "alta": 1, "experimentais": 1, "ativos": ["AAAA3"]}
    velha = al.resumo_da_carteira(
        ["AAAA3"], payload=_payload([], gerado_em=AGORA - timedelta(hours=49)), agora=AGORA)
    assert velha["velha"] is True and velha["alta"] == 0
    assert al.resumo_da_carteira(["AAAA3"], payload={}) == {
        "publicada": False, "velha": True, "alta": 0, "experimentais": 0, "ativos": []}


def test_cartao_escapa_texto_e_so_linka_http():
    ev = _ev("<script>alert(1)</script>", url="javascript:alert(1)")
    html = al.cartao_alerta(_alerta("AAAA3", evidencias=[ev]))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "href=" not in html and "javascript:" not in html


def test_cartao_com_link_http_abre_em_aba_nova_e_cabe_numa_linha():
    html = al.cartao_alerta(_alerta("AAAA3", evidencias=[_ev("linha 1\nlinha 2")]))
    assert 'href="https://exemplo.invalid/doc"' in html
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html
    assert "\n" not in html                      # st.markdown faria bloco de código


def test_cartao_segue_a_ordem_do_desenho():
    html = al.cartao_alerta(_alerta("AAAA3", confirmar_ate="2026-12-30"))
    marcos = ["Risco de tese", "AAAA3 — ", "Hipótese", "Evidências", "Seis perguntas",
              "Prazo", "O que invalidaria", "Taxa-base", "Inteligência dos Ativos"]
    posicoes = [html.index(x) for x in marcos]
    assert posicoes == sorted(posicoes)
    assert "Mês de referência 09/2026" in html and "detectado em 09/11/2026" in html
    assert "Horizonte até 30/09/2027" in html
    assert "Precisa de confirmação até 30/12/2026; sem ela, expira." in html
    assert "Pergunta 6?" in html and "Próximo ITR" in html


def test_cartao_confirmado_nao_fala_em_expirar():
    assert "expira" not in al.cartao_alerta(_alerta("AAAA3"))


def test_cartao_experimental_diz_o_motivo_e_que_nao_conta():
    html = al.cartao_alerta(_alerta("AAAA3", experimental=True), _payload([])["tipos"])
    assert "Experimental: sem teste PIT. Não entra na contagem." in html


def test_texto_da_taxa_base():
    assert al._texto_taxa_base(None).startswith("Sem taxa-base")
    pouco = {"frac_queda": 0.5, "frac_base": 0.1, "n": 5, "n_censurados": 2,
             "ret_q1": -0.3, "ret_mediana": -0.1, "ret_q3": 0.05, "origem": "calibração"}
    assert al._texto_taxa_base(pouco) == (
        "Amostra insuficiente: 5 casos válidos (calibração); 2 censurados fora da conta.")
    cheio = dict(pouco, n=40, frac_queda=0.35, frac_base=0.12)
    assert al._texto_taxa_base(cheio) == (
        "Em 40 casos passados (calibração), 35% caíram mais de 20% no horizonte de 12 meses, "
        "contra 12% dos ativos sem alerta. Retorno mediano -10,0%; no quartil ruim, -30,0%. "
        "2 casos censurados ficaram fora da conta.")


# ── tela ─────────────────────────────────────────────────────────────────────

_SCRIPT = """
import time
from datetime import datetime
import streamlit as st
st.session_state.setdefault("_app4_user",
    {"id": "11111111-1111-1111-1111-111111111111", "expires_at": time.time() + 600})
from views import investimentos_alertas as al
cfg = st.session_state["_cfg"]
al.render(cfg["tickers"], payload=cfg["payload"],
          agora=datetime.fromisoformat(cfg["agora"]),
          rotulo_ia=cfg["rotulo_ia"], chave_aba=cfg["chave_aba"])
"""


def _rodar(tickers, payload, *, rotulo_ia=None, chave_aba=None):
    at = AppTest.from_string(_SCRIPT)
    at.session_state["_cfg"] = {"tickers": list(tickers), "payload": payload,
                                "agora": AGORA.isoformat(), "rotulo_ia": rotulo_ia,
                                "chave_aba": chave_aba}
    return at.run(timeout=30)


def _textos(at) -> str:
    return " ".join(x.value for x in at.markdown)


def test_tela_mostra_o_cartao_da_carteira_e_nao_tem_botao_de_ordem():
    at = _rodar(["AAAA3"], _payload([_alerta("AAAA3"), _alerta("ZZZZ3")]))
    assert not at.exception
    texto = _textos(at)
    assert "AAAA3 — " in texto and "ZZZZ3" not in texto
    assert not at.warning
    assert not any(b.label.startswith(("Comprar", "Vender")) for b in at.button)


def test_vitrine_velha_avisa():
    at = _rodar(["AAAA3"], _payload([_alerta("AAAA3")], gerado_em=AGORA - timedelta(hours=49)))
    assert any("VELHA" in w.value for w in at.warning)


def test_sem_vitrine_diz_que_nao_foi_publicada():
    at = _rodar(["AAAA3"], {})
    assert not at.exception
    assert any("ainda não foi publicada" in i.value for i in at.info)


def test_estado_vazio_mostra_resolvidos_da_carteira_e_placar():
    resolvido = {"chave": "AAAA3|evento_grave|2026-06-30", "ativo": "AAAA3",
                 "tipo": m.TIPO_EVENTO_GRAVE, "rotulo_tipo": m.ROTULO_TIPO[m.TIPO_EVENTO_GRAVE],
                 "estado": m.INVALIDADO, "data_evento": "2026-10-31",
                 "motivo": "condição desfeita", "mes_referencia": "2026-06-30",
                 "variacao_desde_abertura": -0.05}
    fora = dict(resolvido, chave="ZZZZ3|evento_grave|2026-06-30", ativo="ZZZZ3")
    at = _rodar(["AAAA3"], _payload([], resolvidos=[resolvido, fora]))
    assert any("Nenhum risco de tese aberto" in s.value for s in at.success)
    texto = _textos(at)
    assert "AAAA3" in texto and "ZZZZ3" not in texto and "-5,0%" in texto
    assert len(at.dataframe) == 1


def test_botao_leva_para_a_inteligencia_dos_ativos():
    at = _rodar(["AAAA3"], _payload([_alerta("AAAA3")]),
                rotulo_ia="🧠  Inteligência dos Ativos", chave_aba="investimentos_aba")
    at.button(key="alertas_abrir_ia").click().run(timeout=30)
    assert at.session_state["investimentos_aba"] == "🧠  Inteligência dos Ativos"


def test_sem_destino_nao_ha_botao():
    at = _rodar(["AAAA3"], _payload([_alerta("AAAA3")]))
    assert not [b for b in at.button if b.key == "alertas_abrir_ia"]


def test_atencao_fica_recolhida_e_fora_da_contagem():
    p = _payload([_alerta("AAAA3", nivel=m.NIVEL_ATENCAO)])
    at = _rodar(["AAAA3"], p)
    rotulos = [e.label for e in at.expander]
    assert rotulos == ["Atenção (1): fora da contagem"]
    assert al.contagem_da_carteira(["AAAA3"], payload=p) == 0
    assert any("Nenhum risco de tese aberto" in s.value for s in at.success)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_investimentos_alertas.py -q`
Expected: FAIL na coleta, com `ImportError: cannot import name 'investimentos_alertas' from 'views'`.

- [ ] **Step 3: Escrever a view**

```python
# views/investimentos_alertas.py
"""Subaba 🚨 Alertas de Investimentos.

Lê só a vitrine ``data/public/alertas_ativos.json.gz``, que não guarda dado
pessoal: o filtro pela carteira acontece aqui, na hora de desenhar. Não é a
Análise › 🔔 Alertas, que aponta concentração e falta de cotação. Esta trata de
fato novo que pode quebrar a tese de um ativo. Não há botão de comprar nem de
vender: alerta não é ordem.
"""
from __future__ import annotations

import re
from datetime import date
from html import escape

import streamlit as st

from core.alertas_ativos import modelos as m
from core.alertas_ativos import vitrine
from core.memoria_mercado.amostra import N_MINIMO_EXPERIMENTAL

ROTULO = "Alertas"
ICONE = "🚨"

_VISIVEIS = (m.NOVO, m.ATUALIZADO)
_COR_NIVEL = {
    m.NIVEL_RISCO: "var(--app-danger)",
    m.NIVEL_OPORTUNIDADE: "var(--app-info)",
    m.NIVEL_ATENCAO: "var(--app-warning)",
}
_ROTULO_NIVEL = {
    m.NIVEL_RISCO: "Risco de tese",
    m.NIVEL_OPORTUNIDADE: "Oportunidade",
    m.NIVEL_ATENCAO: "Atenção",
}
_FAMILIA = {"documento": "Documento", "fundamento": "Fundamento", "preco": "Preço",
            "renda": "Renda"}
_ESTADO = {m.NOVO: "novo", m.ATUALIZADO: "atualizado", m.SUSPENSO: "suspenso: fonte velha"}
_CAIXA = ("background:var(--app-surface);border:1px solid var(--app-border);"
          "border-radius:12px;padding:14px 16px;margin-bottom:12px;")
_URL_SEGURA = re.compile(r"^https?://", re.I)
_COLUNAS_PLACAR = (("rotulo", "Tipo"), ("fechados", "Fechados"), ("avaliados", "Avaliados"),
                   ("quedas_acima_20", "Quedas > 20%"), ("invalidados", "Invalidados"),
                   ("expirados", "Expirados"), ("status", "Status"))


# ── leitura ──────────────────────────────────────────────────────────────────

def rotulo_aba(n: int) -> str:
    base = f"{ICONE}  {ROTULO}"
    return f"{base} ({n})" if n else base


def _ou_carregar(payload: dict | None) -> dict:
    return vitrine.carregar(vitrine.CAMINHO) if payload is None else payload


def _riscos_visiveis(p: dict, tickers) -> list[dict]:
    return [a for a in vitrine.filtrar_carteira(p, tickers)
            if a.get("nivel") == m.NIVEL_RISCO and a.get("estado") in _VISIVEIS]


def _ordenar(alertas: list[dict]) -> list[dict]:
    """Não experimentais primeiro; dentro de cada grupo, o mais recente primeiro."""
    por_data = sorted(alertas, key=lambda a: str(a.get("aberto_em") or ""), reverse=True)
    return sorted(por_data, key=lambda a: a.get("experimental") is not False)


def contagem_da_carteira(tickers, payload: dict | None = None) -> int:
    p = _ou_carregar(payload)
    return vitrine.contagem_alta(vitrine.filtrar_carteira(p, tickers)) if p else 0


def selo_do_ativo(ticker, payload: dict | None = None) -> str | None:
    p = _ou_carregar(payload)
    if not p:
        return None
    meus = _ordenar(_riscos_visiveis(p, [ticker]))
    if not meus:
        return None
    a = meus[0]
    rotulo = str(a.get("rotulo_tipo") or a.get("tipo") or "")
    return rotulo + (" (experimental)" if a.get("experimental") is not False else "")


def resumo_da_carteira(tickers, payload: dict | None = None, agora=None) -> dict:
    p = _ou_carregar(payload)
    if not p:
        return {"publicada": False, "velha": True, "alta": 0, "experimentais": 0, "ativos": []}
    meus = _riscos_visiveis(p, tickers)
    alta = vitrine.contagem_alta(meus)
    return {"publicada": True, "velha": vitrine.velha(p, agora=agora), "alta": alta,
            "experimentais": len(meus) - alta,
            "ativos": sorted({a["ativo"] for a in meus if a.get("experimental") is False})}


# ── formatação ───────────────────────────────────────────────────────────────

def _t(x) -> str:
    """Texto escapado numa linha só: quebra de linha viraria bloco de código."""
    return escape(" ".join(str(x).split())) if x is not None else ""


def _data(iso) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return "—"


def _mes(iso) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%m/%Y")
    except ValueError:
        return "—"


def _pct(x) -> str:
    return f"{float(x) * 100:.0f}%" if x is not None else "—"


def _pct_sinal(x) -> str:
    return f"{float(x) * 100:+.1f}%".replace(".", ",") if x is not None else "—"


def _link(url, texto) -> str:
    t = _t(texto)
    if url and _URL_SEGURA.match(str(url)):
        return (f'<a href="{escape(str(url).strip(), quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{t}</a>')
    return t


def _texto_taxa_base(tb: dict | None) -> str:
    if not tb:
        return "Sem taxa-base: o tipo ainda não passou pelo teste ponto no tempo."
    n = int(tb.get("n") or 0)
    cens = int(tb.get("n_censurados") or 0)
    origem = tb.get("origem") or "origem não informada"
    if n < N_MINIMO_EXPERIMENTAL:
        return (f"Amostra insuficiente: {n} casos válidos ({origem}); "
                f"{cens} censurados fora da conta.")
    return (f"Em {n} casos passados ({origem}), {_pct(tb.get('frac_queda'))} caíram mais de "
            f"20% no horizonte de 12 meses, contra {_pct(tb.get('frac_base'))} dos ativos sem "
            f"alerta. Retorno mediano {_pct_sinal(tb.get('ret_mediana'))}; no quartil ruim, "
            f"{_pct_sinal(tb.get('ret_q1'))}. {cens} casos censurados ficaram fora da conta.")


def _secao(titulo: str, corpo: str) -> str:
    return ('<div style="margin-top:10px;"><div style="font-size:0.72rem;'
            'text-transform:uppercase;letter-spacing:0.08em;color:var(--app-muted);'
            f'font-weight:700;margin-bottom:4px;">{escape(titulo)}</div>'
            f'<div style="color:var(--app-text);font-size:0.9rem;">{corpo}</div></div>')


def cartao_alerta(a: dict, tipos: dict | None = None) -> str:
    """Cartão de um alerta, na ordem do desenho, numa linha só de HTML."""
    nivel = a.get("nivel") or m.NIVEL_RISCO
    cor = _COR_NIVEL.get(nivel, "var(--app-muted)")
    estado = _ESTADO.get(a.get("estado"), str(a.get("estado") or ""))
    partes = [
        '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        f'border:1px solid {cor};color:{cor};font-size:0.75rem;font-weight:700;">'
        f'{_t(_ROTULO_NIVEL.get(nivel, nivel))} · {_t(estado)}</span>'
    ]
    if a.get("experimental") is not False:
        st_tipo = (tipos or {}).get(f"{a.get('universo')}/{a.get('tipo')}") or {}
        partes.append(
            '<div style="margin-top:8px;padding:6px 10px;border-radius:8px;'
            'background:var(--app-surface-raised);color:var(--app-muted);font-size:0.82rem;">'
            f'Experimental: {_t(st_tipo.get("motivo") or "sem teste PIT")}. '
            'Não entra na contagem.</div>')

    evs = sorted(a.get("evidencias") or [], key=lambda e: str(e.get("data") or ""),
                 reverse=True)
    fato = _t(evs[0].get("descricao")) if evs else "Sem evidência registrada."
    partes.append(
        '<div style="margin-top:8px;font-size:1.02rem;font-weight:800;color:var(--app-text);">'
        f'{_t(a.get("ativo"))} — {_t(a.get("rotulo_tipo") or a.get("tipo"))}</div>'
        f'<div style="color:var(--app-text);margin-top:2px;">{fato}</div>'
        '<div style="color:var(--app-muted);font-size:0.8rem;margin-top:2px;">'
        f'Mês de referência {_mes(a.get("mes_referencia"))} · '
        f'detectado em {_data(a.get("detectado_em"))}</div>')
    partes.append(_secao("Hipótese", _t(a.get("hipotese")) or "—"))

    grupos: dict[str, list[dict]] = {}
    for e in evs:
        grupos.setdefault(str(e.get("familia") or "outra"), []).append(e)
    itens = "".join(
        f'<li><b>{_t(_FAMILIA.get(f, f))}</b><ul style="margin:2px 0 4px 0;">'
        + "".join(f'<li>{_t(e.get("descricao"))} · {_link(e.get("url"), e.get("fonte"))} · '
                  f'{_data(e.get("data"))}</li>' for e in lista)
        + "</ul></li>"
        for f, lista in grupos.items())
    partes.append(_secao("Evidências", f'<ul style="margin:0;padding-left:18px;">{itens}</ul>'))

    perguntas = "".join(f"<li><b>{_t(q)}</b> {_t(r)}</li>"
                        for q, r in (a.get("perguntas") or []))
    partes.append(_secao("Seis perguntas",
                         f'<ol style="margin:0;padding-left:18px;">{perguntas}</ol>'))

    prazo = [f'Horizonte até {_data(a.get("horizonte_ate"))}.']
    if a.get("confirmar_ate"):
        prazo.append(f'Precisa de confirmação até {_data(a.get("confirmar_ate"))}; '
                     "sem ela, expira.")
    if a.get("proximo_documento"):
        prazo.append(_t(a.get("proximo_documento")))
    partes.append(_secao("Prazo", " ".join(prazo)))
    partes.append(_secao("O que invalidaria", _t(a.get("invalidaria")) or "—"))
    partes.append(_secao("Taxa-base", _t(_texto_taxa_base(a.get("taxa_base")))))
    partes.append(
        '<div style="margin-top:10px;color:var(--app-muted);font-size:0.8rem;">'
        "Para a leitura pela sua estratégia, veja este ativo na Inteligência dos Ativos "
        f"(selo {ICONE} no nome).</div>")
    return f'<div style="{_CAIXA}border-left:4px solid {cor};">' + "".join(partes) + "</div>"


# ── tela ─────────────────────────────────────────────────────────────────────

def _ir_para(chave: str, rotulo: str) -> None:
    st.session_state[chave] = rotulo


def _render_resolvidos(p: dict, tickers) -> None:
    resolvidos = vitrine.filtrar_carteira({"abertos": p.get("resolvidos_recentes") or []},
                                          tickers)
    if not resolvidos:
        st.caption("Nenhum alerta da sua carteira foi resolvido nos últimos 90 dias.")
        return
    linhas = "".join(
        f'<li><b>{_t(r.get("ativo"))}</b> — {_t(r.get("rotulo_tipo"))}: {_t(r.get("estado"))} '
        f'em {_data(r.get("data_evento"))} ({_t(r.get("motivo"))}); variação desde a abertura '
        f'{_pct_sinal(r.get("variacao_desde_abertura"))}.</li>'
        for r in resolvidos)
    st.markdown(
        '<div style="margin-top:14px;"><div style="font-weight:700;color:var(--app-text);">'
        "Resolvidos nos últimos 90 dias</div>"
        f'<ul style="margin:4px 0 0 0;padding-left:18px;color:var(--app-text);">{linhas}</ul>'
        "</div>",
        unsafe_allow_html=True)


def _render_placar(p: dict) -> None:
    placar = p.get("placar") or {}
    st.markdown(f"**Placar desde {_data(p.get('inicio_registro'))}**")
    if not placar:
        st.caption("O placar ainda não tem alerta fechado.")
        return
    st.dataframe([{rot: v.get(c) for c, rot in _COLUNAS_PLACAR} for v in placar.values()],
                 hide_index=True)


def render(tickers, *, payload: dict | None = None, rotulo_ia: str | None = None,
           chave_aba: str | None = None, agora=None) -> None:
    p = _ou_carregar(payload)
    if not p:
        st.info("A vitrine de alertas ainda não foi publicada. Ela sai da rotina local, que "
                "lê o armazém e grava data/public/alertas_ativos.json.gz.")
        return
    if vitrine.velha(p, agora=agora):
        h = vitrine.idade_horas(p, agora=agora)
        idade = f"há {h:.0f} h" if h is not None else "em data desconhecida"
        st.warning(f"VELHA: a vitrine de alertas foi gerada {idade}, acima do limite de "
                   f"{vitrine.LIMITE_VELHA_HORAS} h. Um alerta novo pode não ter chegado.")
    velhas = sorted(f for f, s in (p.get("fontes") or {}).items() if not s.get("fresca"))
    if velhas:
        st.caption("Fontes velhas nesta publicação: " + ", ".join(velhas)
                   + ". Tipos que dependem delas ficam suspensos.")

    meus = vitrine.filtrar_carteira(p, tickers)
    riscos = _ordenar([a for a in meus
                       if a.get("nivel") == m.NIVEL_RISCO and a.get("estado") in _VISIVEIS])
    suspensos = [a for a in meus if a.get("estado") == m.SUSPENSO]
    atencao = [a for a in meus
               if a.get("nivel") == m.NIVEL_ATENCAO and a.get("estado") in _VISIVEIS]
    alta = vitrine.contagem_alta(riscos)
    tipos = p.get("tipos") or {}
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;gap:8px 18px;margin:4px 0 12px 0;'
        'color:var(--app-muted);font-size:0.88rem;">'
        f'<span><b style="color:var(--app-danger);">{alta}</b> riscos de tese</span>'
        f"<span>{len(riscos) - alta} experimentais fora da contagem</span>"
        f"<span>{len(suspensos)} suspensos</span>"
        "<span>Oportunidades chegam numa fatia futura.</span></div>",
        unsafe_allow_html=True)

    if not riscos and not suspensos:
        st.success("Nenhum risco de tese aberto nos ativos da sua carteira.")
    else:
        for a in riscos + suspensos:
            st.markdown(cartao_alerta(a, tipos), unsafe_allow_html=True)
        if rotulo_ia and chave_aba:
            st.button("Abrir na Inteligência dos Ativos", key="alertas_abrir_ia",
                      on_click=_ir_para, args=(chave_aba, rotulo_ia),
                      help="Leva para a aba que lê cada ativo pela sua estratégia; "
                           f"procure o selo {ICONE} no nome.")
    if atencao:
        with st.expander(f"Atenção ({len(atencao)}): fora da contagem", expanded=False):
            for a in atencao:
                st.markdown(cartao_alerta(a, tipos), unsafe_allow_html=True)
    _render_resolvidos(p, tickers)
    _render_placar(p)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_investimentos_alertas.py -q`
Expected: PASS (21 testes).

- [ ] **Step 5: Escrever os testes da sexta aba**

Em `tests/test_investimentos_abas_sob_demanda.py`:

1. Acrescentar ao import:

```python
from views import investimentos_alertas as al
```

2. Em `test_cada_aba_so_roda_quando_aberta`, trocar `range(1, 6)` por `range(1, 7)`.

3. Acrescentar ao fim do arquivo:

```python
def test_aba_dos_alertas_acompanha_a_contagem():
    # O rótulo muda com a contagem; sem reafirmar, quem estava em 🚨 Alertas (2)
    # voltaria ao Dashboard quando um alerta fecha.
    ir = _rotulo(True)
    assert inv._aba_mantida("🚨  Alertas (2)", ir, al.rotulo_aba(0)) == "🚨  Alertas"
    assert inv._aba_mantida("🚨  Alertas", ir, al.rotulo_aba(3)) == "🚨  Alertas (3)"
    assert inv._aba_mantida("🧾  Imposto de Renda", ir, al.rotulo_aba(3)) == \
        "🧾  Imposto de Renda"


def test_alertas_e_a_quarta_aba_e_le_a_carteira():
    chamada = next(
        n for n in ast.walk(_render())
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.tabs"
    )
    rotulos = [ast.unparse(e) for e in chamada.args[0].elts]
    assert len(rotulos) == 6 and rotulos[3] == "_rotulo_alertas"
    fonte = inspect.getsource(inv.render)
    assert "_alertas.render(_tickers" in fonte
    assert "tickers_em_carteira(carteira)" in fonte
```

Em `tests/test_configuracoes_sincronizadas.py`:

1. Acrescentar ao import, junto de `from views import inteligencia_ativos`:

```python
from views import investimentos_alertas
```

2. Trocar o corpo de `_abas_de_investimentos` (linhas 91-101). O rótulo calculado deixa de ser um só, e cada nome de variável aponta para o seu `ROTULO`:

```python
_ROTULOS_CALCULADOS = {
    "_rotulo_ia": inteligencia_ativos.ROTULO,
    "_rotulo_alertas": investimentos_alertas.ROTULO,
}


def _abas_de_investimentos() -> list[str]:
    """Rótulos das abas de Investimentos, lidos do ``st.tabs`` de ``render``.

    Dois rótulos são calculados: o da Inteligência dos Ativos troca 🔒 por 🧠 e o
    dos Alertas leva a contagem entre parênteses. Cada um entra pelo ``ROTULO``
    do seu módulo.
    """
    arvore = ast.parse(textwrap.dedent(inspect.getsource(investimentos.render)))
    no = next(n for n in ast.walk(arvore)
              if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.tabs")
    return [_sem_emoji(e.value) if isinstance(e, ast.Constant)
            else _ROTULOS_CALCULADOS[ast.unparse(e)]
            for e in no.args[0].elts]
```

Antes de colar, abra as linhas 91-101 e copie dali o nome do módulo de Investimentos e a forma de chegar ao `st.tabs`; o trecho acima só troca a última expressão e a docstring. Se o arquivo já usa outro nome para o módulo (por exemplo `inv`), mantenha o nome do arquivo.

- [ ] **Step 6: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_investimentos_abas_sob_demanda.py tests/test_configuracoes_sincronizadas.py -q`
Expected: FAIL em `test_cada_aba_so_roda_quando_aberta` (falta `tab6`), em `test_aba_dos_alertas_acompanha_a_contagem` (`_aba_mantida` recebe dois argumentos) e em `test_alertas_e_a_quarta_aba_e_le_a_carteira`.

- [ ] **Step 7: Ligar a aba em `views/investimentos.py`**

1. Logo abaixo de `ABA_INVESTIMENTOS_KEY = "investimentos_aba"` (linha 5110), acrescentar:

```python
_SEM_CONTAGEM = re.compile(r"\s\(\d+\)$")
```

2. Trocar a assinatura e o fim de `_aba_mantida`:

```python
def _aba_mantida(atual: object, rotulo_ia: str, rotulo_alertas: str | None = None) -> str | None:
```

Acrescente este parágrafo ao fim da docstring atual, sem apagar o que está lá:

```
    O rótulo dos Alertas leva a contagem entre parênteses ("🚨  Alertas (2)") e
    muda quando um alerta abre ou fecha. Pelo mesmo motivo da IA, quem estava
    nos Alertas é reafirmado no rótulo novo.
```

E troque o `return atual` final por:

```python
    if rotulo_alertas and _SEM_CONTAGEM.sub("", atual) == _SEM_CONTAGEM.sub("", rotulo_alertas):
        return rotulo_alertas
    return atual
```

3. Em `render`, trocar o bloco da linha 5208 (`from core.estrategia import portao as _portao`) até o fim do `if tab5.open:` por:

```python
    from core.estrategia import portao as _portao
    from core.proventos import tickers_em_carteira
    from views import inteligencia_ativos as _ia
    from views import investimentos_alertas as _alertas
    _liberacao = _portao.verificar()
    _rotulo_ia = _ia.rotulo_aba(_liberacao)
    _tickers = sorted(tickers_em_carteira(carteira))
    _rotulo_alertas = _alertas.rotulo_aba(_alertas.contagem_da_carteira(_tickers))
    _aba = _aba_mantida(st.session_state.get(ABA_INVESTIMENTOS_KEY), _rotulo_ia,
                        _rotulo_alertas)
    if _aba is not None:
        st.session_state[ABA_INVESTIMENTOS_KEY] = _aba

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊  Dashboard",
        "📈  Histórico",
        "🔍  Análise",
        _rotulo_alertas,
        _rotulo_ia,
        "🧾  Imposto de Renda",
    ], key=ABA_INVESTIMENTOS_KEY, on_change="rerun")
    if tab1.open:
        with tab1:
            _tab_dashboard(carteira, proventos, cashflow, evolucao)
    if tab2.open:
        with tab2:
            _tab_historico(cashflow, proventos, evolucao, carteira)
            render_risco_carteira()
            render_atribuicao_carteira()
    if tab3.open:
        with tab3:
            _tab_analise(carteira, proventos)
    if tab4.open:
        with tab4:
            _alertas.render(_tickers, rotulo_ia=_rotulo_ia, chave_aba=ABA_INVESTIMENTOS_KEY)
    if tab5.open:
        with tab5:
            _ia.render(_liberacao, carteira, proventos)
    if tab6.open:
        with tab6:
            from views.ir_renda_variavel import render as _render_ir
            _render_ir()
```

4. No comentário acima do bloco (linhas 5196-5207), acrescentar ao fim:

```python
    # A 🚨 Alertas entrou como quarta aba em 11/2026. O rótulo leva a contagem
    # de riscos de tese da carteira e muda quando um alerta abre ou fecha;
    # ``_aba_mantida`` reafirma quem estava nela.
```

- [ ] **Step 8: Rodar e ver passar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_investimentos_abas_sob_demanda.py tests/test_investimentos_alertas.py -q`
Expected: PASS.

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_configuracoes_sincronizadas.py -q`
Expected: FAIL. A aba Alertas ainda não está nem na Documentação nem no Mapa. Isso é o que os Steps 9 e 10 corrigem.

- [ ] **Step 9: Documentação (`views/documentacao.py`)**

1. Logo depois do fechamento de `FLOW_INVESTIMENTOS` (o `    },\n)` que vem depois do nó `alertas_ai`, antes de `FLOW_CONTROLE_FINANCEIRO = FlowSpec(`), acrescentar:

```python
FLOW_ALERTAS = FlowSpec(
    key="alertas_investimentos",
    title="Alertas de tese dos ativos da carteira",
    subtitle=(
        "Mostra como a subaba 🚨 Alertas de Investimentos nasce: do armazém local ao motor de "
        "regras, ao registro com hashes, à vitrine publicada e ao cartão na tela. Não é a "
        "Análise › 🔔 Alertas, que aponta concentração e falta de cotação; esta trata de fato "
        "novo que pode quebrar a tese de um ativo."
    ),
    tela="Investimentos",
    abas=("Alertas",),
    rows=(
        ("Fontes", ("al_fontes",)),
        ("Motor", ("al_regras", "al_ciclo", "al_teste_pit")),
        ("Registro", ("al_registro",)),
        ("Vitrine", ("al_vitrine",)),
        ("Tela", ("al_tela", "al_confianca")),
    ),
    default="al_regras",
    nodes={
        "al_fontes": _node(
            "al_fontes", "Fontes no armazém local", "Fontes",
            "Lê do armazém local os documentos do IPE da CVM, os 8-K da SEC, as DFP e ITR, o "
            "informe mensal dos FIIs e o preço. Cada dado entra pela data em que ficou "
            "disponível, nunca pela data de referência.",
            ("IPE", "SEC 8-K", "DFP e ITR", "Informe mensal de FII", "Preço de fechamento do mês"),
            "Sem a data de disponibilidade, o motor veria hoje um fato que no passado ainda não "
            "existia, e o teste ponto no tempo mediria uma vantagem que ninguém teria.",
        ),
        "al_regras": _node(
            "al_regras", "Regras dos três tipos", "Motor",
            "Gera as observações de evento grave, de deterioração fundamental confirmada e de "
            "FII com deterioração de renda. Um orçamento de raridade de 15% limita quantos "
            "ativos do universo podem estar em alerta ao mesmo tempo, e cada alerta responde a "
            "seis perguntas fixas.",
            ("Evento grave", "Deterioração fundamental confirmada", "FII com deterioração de renda",
             "Orçamento de raridade de 15%", "Seis perguntas"),
            "Alerta que dispara para metade do universo deixa de ser alerta. As seis perguntas "
            "obrigam o cartão a dizer o que aconteceu, por que importa e o que o desfaria.",
        ),
        "al_ciclo": _node(
            "al_ciclo", "Ciclo de vida", "Motor",
            "Cada alerta nasce novo e pode ser atualizado, resolvido, invalidado, expirado ou "
            "suspenso quando a fonte envelhece. Saídas consecutivas da condição o resolvem; "
            "a falta de confirmação no prazo o expira.",
            ("Novo", "Atualizado", "Resolvido", "Invalidado", "Expirado", "Suspenso"),
            "O ciclo impede que um alerta fique aberto para sempre e separa \"a condição "
            "passou\" de \"a fonte parou de responder\".",
        ),
        "al_teste_pit": _node(
            "al_teste_pit", "Teste ponto no tempo", "Motor",
            "O script scripts/testar_alertas_pit.py reproduz o motor mês a mês com o dado "
            "disponível em cada data e mede quantos alertados caíram mais de 20% em 12 meses, "
            "contra a taxa dos ativos sem alerta. Casos censurados saem do numerador e do "
            "denominador. O resultado fica em docs/alertas_ativos/teste_pit_fatia1.md.",
            ("Calibração", "Período guardado", "Amostra mínima 8 (experimental) e 30 (robusta)",
             "Casos censurados"),
            "É o teste que decide se um tipo deixa de ser experimental. O mesmo motor roda no "
            "teste e na produção, para que o número medido seja o do alerta publicado.",
        ),
        "al_registro": _node(
            "al_registro", "Registro com hashes", "Registro",
            "Cada mudança de estado vira uma linha no schema alertas_ativos do armazém local, "
            "encadeada pelo hash da anterior. A cada execução o registro é conciliado com o que "
            "o motor reproduz.",
            ("Schema alertas_ativos", "Cadeia de hashes", "Conciliação a cada execução"),
            "O placar só tem valor se ninguém puder reescrever o passado. A cadeia mostra "
            "qualquer linha alterada depois de gravada.",
        ),
        "al_vitrine": _node(
            "al_vitrine", "Vitrine publicada", "Vitrine",
            "Grava data/public/alertas_ativos.json.gz com os alertas abertos de todo o "
            "universo, os resolvidos dos últimos 90 dias, o placar e o frescor de cada fonte. "
            "A vitrine não tem dado pessoal; com mais de 48 h vai marcada como VELHA.",
            ("Alertas abertos", "Resolvidos em 90 dias", "Placar", "Frescor das fontes",
             "Selo VELHA acima de 48 h"),
            "O app publicado não alcança o armazém local. A vitrine é o que chega a ele, e o "
            "filtro pela carteira acontece só na tela.",
        ),
        "al_tela": _node(
            "al_tela", "Subaba 🚨 Alertas", "Tela",
            "Mostra só os ativos da carteira. O cartão traz o nível e o estado, o fato, a "
            "hipótese, as evidências por família, as seis perguntas, o prazo, o que "
            "invalidaria e a taxa-base. O rótulo 🚨 Alertas (n) conta só os riscos de tese "
            "fora dos tipos experimentais. Sem alerta, a tela diz que não há risco aberto e "
            "mostra os resolvidos e o placar. Um botão leva à Inteligência dos Ativos.",
            ("Rótulo 🚨 Alertas (n)", "Cartão", "Atenção recolhida", "Resolvidos", "Placar",
             "Botão para a Inteligência dos Ativos"),
            "Alerta não é ordem: a tela não tem botão de comprar nem de vender. Tipo "
            "experimental aparece, mas não conta.",
        ),
        "al_confianca": _node(
            "al_confianca", "Grau de Confiança", "Tela",
            "O componente \"Alertas publicados\" do Grau de Confiança de Investimentos mede a "
            "idade da vitrine: fresca até 2 dias e velha a partir de 7.",
            ("Alertas publicados", "Fresca até 2 dias", "Velha a partir de 7 dias"),
            "Quem confia no alerta precisa saber se a vitrine que ele lê está em dia.",
        ),
    },
)
```

2. No nó `alertas_ai` de `FLOW_INVESTIMENTOS`, trocar o resumo:

```python
            "Em Análise › 🔔 Alertas, aponta concentração, falta de cotação, queda, "
            "dependência e outras situações relevantes da carteira. Não é a subaba 🚨 Alertas, "
            "que trata de fato novo que pode quebrar a tese de um ativo e tem fluxo próprio.",
```

3. Em `cfg_documentacao` (linha 2271), trocar `"Abas: Dashboard Geral, Controle financeiro, Carteira atual, Inteligência e IR, "` por:

```python
                "Abas: Dashboard Geral, Controle financeiro, Carteira atual, Alertas, Inteligência e IR, "
```

4. No subtítulo de `FLOW_INTELIGENCIA_IR` (linha 2567), trocar as duas primeiras frases:

```python
        "Investimentos tem seis subabas. Dashboard, Histórico e Análise estão no fluxo da "
        "carteira atual e Alertas tem fluxo próprio; este cobre as duas últimas. A Inteligência dos Ativos lê a carteira "
```

Mantenha o resto do subtítulo como está.

5. Em `FLOWS` (linha 2709) e em `fluxos` de `render_corpo` (linha 3363), acrescentar `FLOW_ALERTAS,` logo depois de `FLOW_INVESTIMENTOS,`. Em `tab_labels` (linha 3346), acrescentar `"Alertas",` logo depois de `"Carteira atual",`. O `assert len(fluxos) == len(tab_labels)` continua valendo: são 16 de cada.

6. Em `_generic_flow_detail`, logo depois do ramo `elif flow.key == "analise_investimentos":`, acrescentar:

```python
    elif flow.key == "alertas_investimentos":
        detail["impacto"] = (
            "Afeta o que a subaba 🚨 Alertas mostra, a contagem no rótulo dela e o selo no nome "
            "do ativo na Inteligência dos Ativos."
        )
```

O guarda `tests/test_documentacao_conteudo.py` reprova palavra sem acento ("nao", "analise", "ultimo", "so"...). Todo texto acima já vem acentuado; não o reescreva sem acento.

- [ ] **Step 10: Mapa do App**

Os JSON de `docs/mapa_app/areas/` usam indentação 2, `ensure_ascii=False`, fim de linha CRLF e CRLF no final. Edite com este script, que preserva o formato:

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
import json
from pathlib import Path

def ler(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def gravar(p, d):
    txt = json.dumps(d, indent=2, ensure_ascii=False).replace("\n", "\r\n") + "\r\n"
    Path(p).write_bytes(txt.encode("utf-8"))

def achar(nos, nome):
    return next(n for n in nos if n["nome"] == nome)

# Investimentos
p = "docs/mapa_app/areas/investimentos.json"
d = ler(p)
raiz = d if "filhos" in d and "nome" in d else d["telas"][0]
assert "cinco abas" in raiz["explicacao"], raiz["explicacao"]
raiz["resumo"] = raiz["resumo"].replace("análises, IA e imposto", "análises, alertas, IA e imposto")
raiz["explicacao"] = raiz["explicacao"].replace("cinco abas", "seis abas")
filhos = raiz["filhos"]
analise = achar(filhos, "Análise")
alertas_analise = achar(analise["filhos"], "Alertas")
alertas_analise["explicacao"] += (" Não confundir com a aba 🚨 Alertas, que trata de fato novo "
                                  "que pode quebrar a tese de um ativo.")
filhos.insert(filhos.index(analise) + 1, {
    "nome": "Alertas",
    "resumo": "Fato novo que pode quebrar a tese de um ativo da carteira",
    "explicacao": (
        "Mostra, só para os ativos da sua carteira, três tipos de alerta: evento grave "
        "(como um fato relevante ou uma recuperação judicial), deterioração fundamental "
        "confirmada e FII com renda caindo. Cada cartão diz o que aconteceu, por que importa, "
        "as evidências com link para o documento, as seis perguntas, até quando o alerta vale, "
        "o que o desfaria e o que aconteceu no passado com casos parecidos. Tipo ainda não "
        "testado aparece como experimental e não entra na contagem do rótulo. Não há botão de "
        "comprar nem de vender: alerta não é ordem. Não é a Análise › Alertas, que aponta "
        "concentração e falta de cotação."
    ),
    "so_admin": False,
    "fonte": "views/investimentos_alertas.py",
    "filhos": [],
})
gravar(p, d)

# Configurações › Documentação
p = "docs/mapa_app/areas/configuracoes.json"
d = ler(p)
def andar(nos):
    for n in nos:
        yield n
        yield from andar(n.get("filhos") or [])
raizes = [d] if "nome" in d else d.get("telas", [])
doc = next(n for n in andar(raizes) if n["nome"] == "Documentação")
assert "15 sub-abas" in doc["explicacao"], doc["explicacao"]
doc["explicacao"] = doc["explicacao"].replace("15 sub-abas", "16 sub-abas")
carteira = achar(doc["filhos"], "Carteira atual")
doc["filhos"].insert(doc["filhos"].index(carteira) + 1, {
    "nome": "Alertas",
    "resumo": "Como os alertas de tese são gerados e testados",
    "explicacao": ("Fluxograma da subaba 🚨 Alertas de Investimentos: do armazém local ao "
                   "motor, ao registro com hashes, à vitrine e ao cartão; não é a "
                   "Análise › 🔔 Alertas."),
    "so_admin": False,
    "fonte": "views/documentacao.py (FLOW_ALERTAS)",
    "filhos": [],
})
gravar(p, d)
print("ok")
EOF
```

Se um `assert` do script falhar, a frase do JSON mudou desde este plano. Abra o arquivo, ajuste o `replace` à frase atual e rode de novo. Não pule o `assert`.

Depois regenere o mapa consolidado:

Run: `PYTHONIOENCODING=utf-8 "$PY" scripts/montar_mapa_app.py`
Expected: termina sem erro e `docs/mapa_app/mapa.json` muda.

Confira que só o necessário mudou:

Run: `git diff --stat docs/mapa_app/`
Expected: três arquivos (`investimentos.json`, `configuracoes.json` e `mapa.json`), sem diff de fim de linha no resto do arquivo.

- [ ] **Step 11: Rodar os guardas de Configurações e a aba**

Run:
```bash
PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_investimentos_alertas.py tests/test_investimentos_abas_sob_demanda.py tests/test_configuracoes_sincronizadas.py tests/test_configuracoes_mapa_do_app.py tests/test_documentacao_conteudo.py -q
```
Expected: PASS.

O Grau de Confiança desta aba já entrou na Task 8 (componente `_alertas_publicados` em `confianca_investimentos`). A aba nova é parte de Investimentos, que já tem seção, por isso `SUBABAS_FORA_DA_NOTA` não muda. Releia os três textos que esta tarefa tocou (o nó `al_tela`, o filho "Alertas" do Mapa e a linha do componente) e confira se descrevem a tela como ela ficou.

- [ ] **Step 12: Commit**

```bash
git add views/investimentos_alertas.py tests/test_investimentos_alertas.py views/investimentos.py \
  tests/test_investimentos_abas_sob_demanda.py tests/test_configuracoes_sincronizadas.py \
  views/documentacao.py docs/mapa_app/areas/investimentos.json \
  docs/mapa_app/areas/configuracoes.json docs/mapa_app/mapa.json
git commit -m "$(cat <<'EOF'
Alertas: aba 🚨 Alertas em Investimentos, lida da vitrine

Subaba nova de Investimentos, entre Análise e Inteligência dos Ativos,
com a contagem de riscos de tese da carteira no rótulo. Tipo experimental
aparece, mas não conta. Documentação, Mapa do App e o teste de sincronia
acompanham a aba nova.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---


### Task 10: Cartão no Dashboard e selo na Inteligência dos Ativos

**Files:**
- Modify: `views/dashboard_geral.py` (helpers novos depois de `_card_lista_html`, linha 1294; chamada no `render`, bloco 3, linhas 2124–2128)
- Modify: `views/inteligencia_ativos_resumida.py` (`rotulo_expander`, linha 121; `_render_ativo`, linha 650)
- Modify: `core/confianca_secao.py` (só o comentário de `SUBABAS_FORA_DA_NOTA`, linhas 141–149)
- Modify: `views/documentacao.py` (linha `("Fatos do período", ...)` em 2305; nó novo antes de `dg_raio_x`, linha 2391; nó `ia_painel`, linha 2600)
- Modify: `docs/mapa_app/areas/dashboard_geral.json`, `docs/mapa_app/areas/investimentos.json`, `docs/mapa_app/mapa.json` (regenerado)
- Test: `tests/test_dashboard_geral_modernization.py`, `tests/test_inteligencia_ativos_resumida.py`

**Interfaces:**
- Consumes (Task 9): `views.investimentos_alertas.resumo_da_carteira(tickers, payload=None, agora=None) -> dict` com as chaves `publicada`, `velha`, `alta`, `experimentais` e `ativos`; `views.investimentos_alertas.selo_do_ativo(ticker, payload=None) -> str | None`. Consumes `core.proventos.tickers_em_carteira(carteira: dict | None) -> set`, que lê `carteira["posicoes"]`.
- Produces: `views.dashboard_geral._html_alertas_carteira(r: dict) -> str`, `views.dashboard_geral._secao_alertas_carteira(carteira: dict) -> None`, `views.inteligencia_ativos_resumida.rotulo_expander(..., alerta: str | None = None)` e `views.inteligencia_ativos_resumida._selo_alerta(ticker: str) -> str | None`.

Notas que valem para a tarefa inteira:

- A vitrine é lida por `core.inteligencia_ativos.arquivo_publicado.ler`, que memoriza pelo `(mtime, tamanho)` do arquivo e nunca levanta. Chamar `selo_do_ativo` uma vez por caixa de ativo não relê o `.json.gz`. Por isso não há `try` nem cache novo na view, e nada de `st.cache_data`.
- O cartão sai num `st.markdown` só, com o HTML numa linha, sem `\n`. Quebra de linha dentro do HTML vira bloco de código no Streamlit.
- A cor do texto vem só de token: `var(--app-text)`, `var(--app-muted)` e `var(--app-warning)`. A cor do título usa as constantes `_COR_NEGATIVO`/`_COR_NEUTRO` que o arquivo já passa por `_cor_texto`.
- Ticker de teste é fictício (AAAA3, ZZZZ3, BBBB11). Nos testes da IA, a fixture `carteira` já existe e não se escreve o ticker dela literalmente: compara-se `rotulo_expander(a, alerta=...)` com `rotulo_expander(a)`.

- [ ] **Step 1: Escrever os testes do cartão do Dashboard**

Acrescente ao fim de `tests/test_dashboard_geral_modernization.py`. Os imports `inspect` e `dashboard` e o helper `_capture_markdown` já existem no arquivo.

```python
# ── 🚨 Alertas da carteira ────────────────────────────────────────────────────

def _resumo(**kw):
    base = {"publicada": True, "velha": False, "alta": 0, "experimentais": 0, "ativos": []}
    base.update(kw)
    return base


def test_alertas_carteira_conta_risco_confirmado_e_separa_experimental():
    html = dashboard._html_alertas_carteira(
        _resumo(alta=2, experimentais=1, ativos=["AAAA3", "ZZZZ3"]))
    assert "<b>2</b> riscos de tese abertos: AAAA3, ZZZZ3." in html
    assert "1 alerta experimental, fora da contagem" in html
    assert "VELHA" not in html
    assert "\n" not in html

    um = dashboard._html_alertas_carteira(_resumo(alta=1, experimentais=3, ativos=["AAAA3"]))
    assert "<b>1</b> risco de tese aberto: AAAA3." in um
    assert "3 alertas experimentais, fora da contagem" in um


def test_alertas_carteira_sem_risco_diz_que_nao_ha():
    html = dashboard._html_alertas_carteira(_resumo())
    assert "Nenhum risco de tese aberto nos ativos da carteira." in html
    assert "experimenta" not in html


def test_alertas_carteira_lista_no_maximo_cinco_ativos():
    ativos = ["AAA1", "AAA2", "AAA3", "AAA4", "AAA5", "AAA6", "AAA7"]
    html = dashboard._html_alertas_carteira(_resumo(alta=7, ativos=ativos))
    assert "AAA1, AAA2, AAA3, AAA4, AAA5 e mais 2." in html
    assert "AAA6" not in html


def test_alertas_carteira_nao_publicada_e_velha():
    nao = dashboard._html_alertas_carteira(
        {"publicada": False, "velha": True, "alta": 0, "experimentais": 0, "ativos": []})
    assert "A vitrine de alertas ainda não foi publicada." in nao
    assert "VELHA" not in nao
    assert "Nenhum risco" not in nao

    velha = dashboard._html_alertas_carteira(_resumo(velha=True))
    assert "VELHA: a vitrine tem mais de 48 h." in velha
    assert "var(--app-warning)" in velha


def test_alertas_carteira_escapa_o_ticker():
    html = dashboard._html_alertas_carteira(_resumo(alta=1, ativos=["<b>x</b>"]))
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_secao_alertas_sem_ticker_nao_desenha(monkeypatch):
    rendered = _capture_markdown(monkeypatch)
    monkeypatch.setattr("core.proventos.tickers_em_carteira", lambda carteira: set())
    chamado = []
    monkeypatch.setattr("views.investimentos_alertas.resumo_da_carteira",
                        lambda tickers, **kw: chamado.append(tickers))
    dashboard._secao_alertas_carteira({"posicoes": []})
    assert rendered == []
    assert chamado == []


def test_secao_alertas_le_a_vitrine_com_os_tickers_ordenados(monkeypatch):
    rendered = _capture_markdown(monkeypatch)
    monkeypatch.setattr("core.proventos.tickers_em_carteira",
                        lambda carteira: {"ZZZZ3", "AAAA3"})
    chamados = []

    def fake_resumo(tickers, **kw):
        chamados.append(list(tickers))
        return _resumo(alta=1, ativos=["AAAA3"])

    monkeypatch.setattr("views.investimentos_alertas.resumo_da_carteira", fake_resumo)
    dashboard._secao_alertas_carteira({"posicoes": [{"ticker": "AAAA3"}]})
    assert chamados == [["AAAA3", "ZZZZ3"]]
    assert len(rendered) == 1
    assert "\n" not in rendered[0]
    assert "Alertas da carteira" in rendered[0]
    assert "Investimentos → 🚨 Alertas" in rendered[0]


def test_alertas_vem_antes_do_raio_x_no_render():
    fonte = inspect.getsource(dashboard.render)
    assert "_secao_alertas_carteira(carteira)" in fonte
    assert fonte.index("_secao_alertas_carteira(") < fonte.index("_secao_raio_x_portfolio(")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_dashboard_geral_modernization.py -q -k "alertas"`
Expected: FAIL com `AttributeError: module 'views.dashboard_geral' has no attribute '_html_alertas_carteira'`.

- [ ] **Step 3: Escrever o cartão em `views/dashboard_geral.py`**

Logo depois de `_card_lista_html` (termina na linha 1304), acrescente:

```python
# ── 🚨 Alertas da carteira ────────────────────────────────────────────────────

_ALERTAS_MAX_TICKERS = 5


def _html_alertas_carteira(r: dict) -> str:
    """Corpo do cartão 🚨 Alertas da carteira, numa linha só. Puro.

    Só risco de tese confirmado entra na contagem. Tipo que ainda não passou
    no teste PIT aparece numa linha à parte, como experimental."""
    estilo = "font-size:0.88rem;line-height:1.5;"
    if not r.get("publicada"):
        return (f'<div style="{estilo}color:var(--app-muted);">'
                "A vitrine de alertas ainda não foi publicada.</div>")
    partes: list[str] = []
    alta = int(r.get("alta") or 0)
    if alta:
        ativos = [escape(str(t)) for t in (r.get("ativos") or [])]
        lista = ", ".join(ativos[:_ALERTAS_MAX_TICKERS])
        resto = len(ativos) - _ALERTAS_MAX_TICKERS
        if resto > 0:
            lista += f" e mais {resto}"
        rotulo = "risco de tese aberto" if alta == 1 else "riscos de tese abertos"
        partes.append(f'<div style="{estilo}color:var(--app-text);">'
                      f"<b>{alta}</b> {rotulo}: {lista}.</div>")
    else:
        partes.append(f'<div style="{estilo}color:var(--app-text);">'
                      "Nenhum risco de tese aberto nos ativos da carteira.</div>")
    exp = int(r.get("experimentais") or 0)
    if exp:
        rotulo = "alerta experimental" if exp == 1 else "alertas experimentais"
        partes.append(f'<div style="{estilo}color:var(--app-muted);">'
                      f"{exp} {rotulo}, fora da contagem: o tipo ainda não passou no teste.</div>")
    if r.get("velha"):
        partes.append(f'<div style="{estilo}color:var(--app-warning);">'
                      "VELHA: a vitrine tem mais de 48 h.</div>")
    return "".join(partes)


def _secao_alertas_carteira(carteira: dict) -> None:
    """Cartão 🚨 Alertas da carteira, lido da vitrine ``alertas_ativos``.

    Diz se há algo a olhar e em quais ativos; o porquê e as evidências ficam
    na aba 🚨 Alertas de Investimentos."""
    from core.proventos import tickers_em_carteira
    from views import investimentos_alertas as alertas

    tickers = sorted(tickers_em_carteira(carteira))
    if not tickers:
        return
    r = alertas.resumo_da_carteira(tickers)
    cor = _COR_NEGATIVO if r.get("alta") else _COR_NEUTRO
    st.markdown(
        _card_lista_html("🚨 Alertas da carteira", cor, _html_alertas_carteira(r),
                         "Detalhe em Investimentos → 🚨 Alertas."),
        unsafe_allow_html=True,
    )
```

Os imports ficam dentro da função de propósito. O Dashboard abre sem pagar o import da view de alertas quando Investimentos está fora do menu, e o monkeypatch dos testes alcança os dois nomes.

No `render`, troque o bloco 3 (linhas 2124–2128):

```python
    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Raio X do portfólio investido
    # ══════════════════════════════════════════════════════════════════════════
    if tem_carteira:
        _secao_raio_x_portfolio(carteira, evolucao_inv, classes)
```

por:

```python
    # ══════════════════════════════════════════════════════════════════════════
    # BLOCO 3 — Alertas da carteira e Raio X do portfólio investido
    # ══════════════════════════════════════════════════════════════════════════
    if tem_carteira:
        _secao_alertas_carteira(carteira)
        _secao_raio_x_portfolio(carteira, evolucao_inv, classes)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_dashboard_geral_modernization.py -q`
Expected: PASS. `test_cash_history_renders_before_portfolio_xray` continua verde, porque o cartão entra depois do histórico do caixa e antes do Raio X.

- [ ] **Step 5: Escrever os testes do selo na IA**

Acrescente ao fim de `tests/test_inteligencia_ativos_resumida.py`. `tela`, `carteira` (fixture) e `inspect`, se ainda não importado, são o que o teste usa. Acrescente `import inspect` no topo do arquivo, junto de `import re`, se ele não estiver lá.

```python
# ── selo 🚨 de alerta de tese ────────────────────────────────────────────────

def test_rotulo_ganha_o_selo_do_alerta(carteira):
    _ctx_, analises = carteira
    a = analises[0]
    assert tela.rotulo_expander(a, alerta="Evento grave") == (
        tela.rotulo_expander(a) + " · 🚨 Evento grave")
    assert "🚨" not in tela.rotulo_expander(a)
    assert tela.rotulo_expander(a, alerta=None) == tela.rotulo_expander(a)


def test_selo_vem_da_vitrine_de_alertas(monkeypatch):
    monkeypatch.setattr("views.investimentos_alertas.selo_do_ativo",
                        lambda ticker: "Deterioração fundamental" if ticker == "AAAA3" else None)
    assert tela._selo_alerta("AAAA3") == "Deterioração fundamental"
    assert tela._selo_alerta("ZZZZ3") is None


def test_caixa_do_ativo_usa_o_selo():
    fonte = inspect.getsource(tela._render_ativo)
    assert "alerta=_selo_alerta(a.ativo.ticker)" in fonte
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_inteligencia_ativos_resumida.py -q -k "selo"`
Expected: FAIL com `TypeError: rotulo_expander() got an unexpected keyword argument 'alerta'`.

- [ ] **Step 7: Escrever o selo em `views/inteligencia_ativos_resumida.py`**

Troque `rotulo_expander` (linhas 121–132) por:

```python
def rotulo_expander(a: m.AnaliseAtivo,
                    sugerido: rs.AlvoSugerido | None = None,
                    avaliacao: av_.Avaliacao | None = None,
                    d: rs.Decisao | None = None,
                    alerta: str | None = None) -> str:
    """"BBAS3 · 8,5% → 2,3% · Rebalancear": peso atual, % devida e a ação.
    ``d`` reaproveita a decisão já calculada. ``alerta`` é o selo do alerta de
    tese aberto do ativo (``_selo_alerta``) e vai ao fim: "· 🚨 <tipo>"."""
    d = d if d is not None else rs.decisao(a, sugerido, avaliacao)
    alvo = rs.alvo_do_ativo(a, sugerido)
    peso = _pct(a.ativo.peso_atual)
    if alvo is not None:
        peso += f" → {_pct(alvo)}"
    rotulo = f"{a.ativo.ticker} · {peso} · {d.rotulo}"
    return rotulo + (f" · 🚨 {alerta}" if alerta else "")


def _selo_alerta(ticker: str) -> str | None:
    """Selo do alerta de tese aberto do ativo, lido da vitrine ``alertas_ativos``.

    A leitura é memorizada pelo arquivo e nunca levanta: vitrine ausente ou
    ilegível só tira o selo. A falha é nomeada na aba 🚨 Alertas."""
    from views.investimentos_alertas import selo_do_ativo

    return selo_do_ativo(ticker)
```

Em `_render_ativo` (linha 650), troque:

```python
    with st.expander(rotulo_expander(a, sugerido, av, d)):
```

por:

```python
    with st.expander(rotulo_expander(a, sugerido, av, d, alerta=_selo_alerta(a.ativo.ticker))):
```

O selo é um texto no título da caixa, e não um botão nem um link profundo. O Streamlit não abre a aba 🚨 Alertas a partir de outro lugar (Desvio 23).

- [ ] **Step 8: Rodar e ver passar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_inteligencia_ativos_resumida.py -q`
Expected: PASS. O teste antigo `tela.rotulo_expander(a) == "... · Rebalancear"` continua verde, porque sem `alerta` o rótulo não muda. O autouse `_alertas_sem_vitrine_publicada` da Task 11 ainda não existe nesta tarefa. Mesmo assim, enquanto `data/public/alertas_ativos.json.gz` não existir, `selo_do_ativo` devolve `None`.

- [ ] **Step 9: Grau de Confiança**

Nenhuma função de medição muda, pelos dois motivos abaixo. Registre-os no comentário para o próximo leitor não achar que foi esquecido.

1. O Dashboard Geral herda o pior entre caixa e Investimentos (`confianca_dashboard_geral`, linha 1093). A nota de Investimentos já mede a vitrine de alertas pelo componente `_alertas_publicados`, desde a Task 8. Logo, o cartão novo já está coberto.
2. A Inteligência dos Ativos está em `SUBABAS_FORA_DA_NOTA["Investimentos"]`. O selo lê a mesma vitrine que `_alertas_publicados` mede e não afirma número próprio.

Em `core/confianca_secao.py`, troque as linhas do comentário:

```python
#   * Inteligencia dos Ativos: estrategia, politicas e LLM; a nota de
#     Investimentos so mede o registro de operacoes.
```

por:

```python
#   * Inteligencia dos Ativos: estrategia, politicas e LLM; a nota de
#     Investimentos so mede o registro de operacoes. O selo 🚨 das caixas le
#     a vitrine alertas_ativos, que a nota de Investimentos ja mede
#     (_alertas_publicados); por isso ele nao tira a subaba daqui.
```

- [ ] **Step 10: Documentação (`views/documentacao.py`)**

Na linha 2305, troque:

```python
        ("Fatos do período", ("dg_leitura", "dg_resumo_areas", "dg_evolucao", "dg_raio_x")),
```

por:

```python
        ("Fatos do período", ("dg_leitura", "dg_resumo_areas", "dg_evolucao", "dg_alertas",
                              "dg_raio_x")),
```

Logo antes de `"dg_raio_x": _node(` (linha 2391), acrescente:

```python
        "dg_alertas": _node(
            "dg_alertas", "Alertas da carteira", "Fatos do período",
            "Cartão que diz quantos riscos de tese estão abertos nos ativos da sua carteira "
            "e em quais. Tipo de alerta que ainda não passou no teste aparece numa linha à "
            "parte, sem entrar na contagem.",
            (
                "_secao_alertas_carteira()",
                "Lê data/public/alertas_ativos.json.gz por resumo_da_carteira()",
                "Só risco de tese confirmado conta; experimental aparece separado",
                "Vitrine com mais de 48 h sai marcada como VELHA",
                "Só com Investimentos no menu",
            ),
            "O Dashboard diz se há algo a olhar. O porquê e as evidências ficam na aba "
            "🚨 Alertas de Investimentos, para o resumo não virar ordem de venda.",
        ),
```

No nó `ia_painel` (linha 2600), acrescente um item ao fim da tupla de detalhes, depois de `"Cada foto é gravada no histórico, que não aparece na tela",`:

```python
                "Caixa de ativo com alerta de tese aberto ganha o selo '· 🚨 <tipo>' no "
                "título, lido de data/public/alertas_ativos.json.gz",
```

O guarda `tests/test_documentacao_conteudo.py` reprova palavra sem acento. Todo texto acima já vem acentuado.

- [ ] **Step 11: Mapa do App**

Os JSON de `docs/mapa_app/areas/` usam indentação 2, `ensure_ascii=False`, fim de linha CRLF e CRLF no final. Edite com este script, que preserva o formato:

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
import json
from pathlib import Path

def ler(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def gravar(p, d):
    txt = json.dumps(d, indent=2, ensure_ascii=False).replace("\n", "\r\n") + "\r\n"
    Path(p).write_bytes(txt.encode("utf-8"))

def pai_de(no, nome):
    """Nó cujo filho direto se chama ``nome`` (busca em profundidade)."""
    for f in no.get("filhos", []):
        if f["nome"] == nome:
            return no
        achado = pai_de(f, nome)
        if achado is not None:
            return achado
    return None

# Dashboard Geral: cartão novo antes do Raio X
p = "docs/mapa_app/areas/dashboard_geral.json"
d = ler(p)
frase = "a carteira que você tem investida e as carteiras sugeridas pelo app"
assert frase in d["explicacao"], d["explicacao"]
d["explicacao"] = d["explicacao"].replace(
    frase, "a carteira que você tem investida, os alertas de tese dela e as carteiras "
           "sugeridas pelo app")
raio_x = "Raio X do portfólio investido"
pai = pai_de(d, raio_x)
assert pai is d, "o Raio X deixou de ser filho direto da raiz"
filhos = pai["filhos"]
i = next(k for k, f in enumerate(filhos) if f["nome"] == raio_x)
assert all(f["nome"] != "Alertas da carteira" for f in filhos)
filhos.insert(i, {
    "nome": "Alertas da carteira",
    "resumo": "Quantos riscos de tese estão abertos na carteira, e em quais ativos",
    "explicacao": (
        "Cartão que diz quantos alertas de risco de tese estão abertos nos ativos que você "
        "tem e cita até cinco deles. Só conta tipo de alerta que já passou no teste com o "
        "histórico; tipo ainda não testado aparece numa linha à parte, como experimental. "
        "Se a vitrine de alertas tem mais de 48 h, o cartão avisa que ela está VELHA. O "
        "porquê de cada alerta e as evidências ficam em Investimentos → 🚨 Alertas. Só "
        "aparece com Investimentos no menu."
    ),
    "so_admin": False,
    "fonte": "views/dashboard_geral.py (_secao_alertas_carteira)",
    "filhos": [],
})
gravar(p, d)

# Investimentos: selo na caixa do ativo da Inteligência dos Ativos
p = "docs/mapa_app/areas/investimentos.json"
d = ler(p)
caixa_nome = "Ações · Fundos imobiliários · Internacional · Outros ativos"
pai = pai_de(d, caixa_nome)
assert pai is not None, caixa_nome
caixa = next(f for f in pai["filhos"] if f["nome"] == caixa_nome)
assert "🚨" not in caixa["explicacao"]
caixa["explicacao"] += (" Se o ativo tem um alerta de risco de tese aberto, o título ganha "
                        "ao fim o selo '· 🚨 <tipo do alerta>'; o detalhe fica na aba "
                        "🚨 Alertas de Investimentos.")
gravar(p, d)
print("ok")
EOF
```

Se um `assert` do script falhar, a frase do JSON mudou desde este plano. Abra o arquivo, ajuste o texto à frase atual e rode de novo. Não pule o `assert`.

Depois regenere o mapa consolidado:

Run: `PYTHONIOENCODING=utf-8 "$PY" scripts/montar_mapa_app.py`
Expected: termina sem erro e `docs/mapa_app/mapa.json` muda.

Confira que só o necessário mudou:

Run: `git diff --stat docs/mapa_app/`
Expected: `dashboard_geral.json`, `investimentos.json` e `mapa.json`, sem diff de fim de linha no resto do arquivo.

- [ ] **Step 12: Rodar os guardas de Configurações e as telas tocadas**

Run:
```bash
PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_dashboard_geral_modernization.py tests/test_inteligencia_ativos_resumida.py tests/test_investimentos_alertas.py tests/test_configuracoes_sincronizadas.py tests/test_configuracoes_mapa_do_app.py tests/test_documentacao_conteudo.py -q
```
Expected: PASS.

Releia os três textos que esta tarefa tocou: o nó `dg_alertas`, o item novo de `ia_painel` e os dois nós do Mapa. Confira se descrevem a tela como ela ficou.

- [ ] **Step 13: Commit**

```bash
git add views/dashboard_geral.py tests/test_dashboard_geral_modernization.py \
  views/inteligencia_ativos_resumida.py tests/test_inteligencia_ativos_resumida.py \
  core/confianca_secao.py views/documentacao.py \
  docs/mapa_app/areas/dashboard_geral.json docs/mapa_app/areas/investimentos.json \
  docs/mapa_app/mapa.json
git commit -m "$(cat <<'EOF'
Alertas: cartão no Dashboard e selo 🚨 na Inteligência dos Ativos

O Dashboard ganha o cartão "Alertas da carteira" antes do Raio X. Ele
conta só risco de tese confirmado e põe o experimental à parte. A caixa
de cada ativo na IA ganha o selo "· 🚨 <tipo>". Os dois leem a vitrine
alertas_ativos, que a nota de Investimentos já mede. Documentação e Mapa
do App acompanham.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Rotina noturna, teste PIT de verdade e primeira publicação

**Files:**
- Modify: `tests/conftest.py` (fixture autouse nova)
- Modify: `core/publicacao_agenda.py` (Alvo novo depois de `carteira20_medicao`, linha 467)
- Modify: `scripts/atualizar_vitrines.py` (entrada em `CARIMBO`)
- Test: `tests/test_publicacao_agenda.py`, `tests/test_alertas_vitrine.py`
- Create (pela execução real): `docs/alertas_ativos/regras_congeladas.json`, `docs/alertas_ativos/teste_pit_fatia1.md`, `docs/alertas_ativos/resultado_pit_fatia1.json`, `docs/alertas_ativos/guardado_executado.json`, `data/public/alertas_ativos.json.gz`

**Interfaces:**
- Consumes: `core.alertas_ativos.vitrine.carregar(caminho=CAMINHO) -> dict` e `vitrine.CAMINHO` (Task 8); `scripts/testar_alertas_pit.py --fase calibracao|guardado` e `core.alertas_ativos.motor` com `ARQ_CONGELADAS`, `ARQ_RELATORIO`, `ARQ_RESULTADO`, `ARQ_TRAVA`, `pode_rodar_guardado` e `impressao_das_regras` (Task 7) e `core.alertas_ativos.backtest.relatorio_md` (Task 7); `scripts.publish_pregoes_recentes_b3.serializar(payload, caminho)` (escritor de vitrine já existente, usado pela Task 8); `scripts/publish_alertas_ativos.py` (Task 8); `scripts/backfill_ipe_metadados.py --anos recentes|todos` e `scripts/ingerir_parecer_auditor.py` (Task 4); `run_us_ingest.py filings --warehouse`, na raiz do repositório (Task 5).
- Produces: o Alvo `alertas_ativos` na agenda noturna e a vitrine publicada que as Tasks 9 e 10 leem.

Esta é a única tarefa que escreve dado de verdade. Ela escreve no armazém local (schema `alertas_ativos`) e em `data/public/`. **Nada vai para o Supabase.** Todo escritor passa por `core.destino_local.exigir_local`.

O repositório é **público**. Os arquivos de `docs/alertas_ativos/` e a vitrine não levam a carteira do usuário. A vitrine cobre o universo inteiro, e o filtro pela carteira é aplicado na hora de desenhar. Nenhuma mensagem de commit, relatório ou corpo de PR cita ticker que o usuário tem, nem valor da carteira.

- [ ] **Step 1: Escrever o teste do isolamento da vitrine**

Assim que a vitrine for publicada (Step 11), qualquer teste que chame `resumo_da_carteira()` ou `selo_do_ativo()` sem `payload` leria o arquivo de verdade. O resultado passaria a depender do que a rotina publicou na noite anterior. A fixture abaixo esconde só o caminho publicado; caminho de `tmp_path` continua lido de verdade.

Acrescente ao fim de `tests/test_alertas_vitrine.py`:

```python
def test_conftest_esconde_a_vitrine_publicada(tmp_path):
    from core.alertas_ativos import vitrine

    assert vitrine.carregar(vitrine.CAMINHO) == {}
    assert vitrine.carregar() == {}

    from scripts.publish_pregoes_recentes_b3 import serializar

    caminho = tmp_path / "alertas_ativos.json.gz"
    serializar({"gerado_em": "2026-11-10T00:00:00+00:00", "abertos": []}, caminho)
    assert vitrine.carregar(caminho)["abertos"] == []
```

`vitrine.py` não tem escritor próprio: quem grava é `serializar`, o mesmo que `scripts/publish_alertas_ativos.py` usa na Task 8. O ponto do teste é gravar num `tmp_path` e ler de volta.

- [ ] **Step 2: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_vitrine.py -q -k conftest`
Expected: hoje o arquivo publicado ainda não existe, e `carregar` já devolve `{}`. O teste pode passar sem a fixture. Para vê-lo falhar de verdade, grave um arquivo falso no caminho publicado, rode e apague:

```bash
PYTHONIOENCODING=utf-8 "$PY" -c "from core.alertas_ativos import vitrine; from scripts.publish_pregoes_recentes_b3 import serializar; serializar({'gerado_em':'2026-11-10T00:00:00+00:00','abertos':[]}, vitrine.CAMINHO)"
PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_alertas_vitrine.py -q -k conftest
rm data/public/alertas_ativos.json.gz
```
Expected: FAIL em `assert vitrine.carregar(vitrine.CAMINHO) == {}`. Confira que o `rm` rodou: `git status --short data/public/alertas_ativos.json.gz` não mostra nada.

- [ ] **Step 3: Escrever a fixture em `tests/conftest.py`**

Acrescente ao fim de `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _alertas_sem_vitrine_publicada(monkeypatch):
    """Esconde ``data/public/alertas_ativos.json.gz`` dos testes.

    Sem isto, todo teste que chama ``resumo_da_carteira()`` ou
    ``selo_do_ativo()`` sem payload passaria a ler o que a rotina noturna
    publicou, e o resultado mudaria de um dia para o outro. Caminho de
    ``tmp_path`` continua lido de verdade."""
    try:
        from core.alertas_ativos import vitrine
    except Exception:  # pacote ausente num checkout antigo: nada a esconder
        yield
        return
    from pathlib import Path

    real = vitrine.carregar
    publicado = Path(vitrine.CAMINHO).resolve()

    def carregar(caminho=vitrine.CAMINHO):
        if Path(caminho).resolve() == publicado:
            return {}
        return real(caminho)

    monkeypatch.setattr(vitrine, "carregar", carregar)
    yield
```

Se `tests/conftest.py` ainda não importa `pytest`, acrescente `import pytest` no topo.

As views chamam `vitrine.carregar(...)` pelo módulo (`_ou_carregar` da Task 9). Por isso o `setattr` no módulo alcança todas elas. Se algum arquivo fizer `from core.alertas_ativos.vitrine import carregar`, o nome local escapa da fixture. Confira:

Run: `grep -rn "from core.alertas_ativos.vitrine import\|from core.alertas_ativos import vitrine" core views scripts`
Expected: só a forma `from core.alertas_ativos import vitrine`. Se aparecer um `import carregar` direto, troque pela forma de módulo.

- [ ] **Step 4: Rodar e ver passar**

Repita o Step 2: grave o arquivo falso, rode o teste e apague o arquivo.
Expected: PASS com o arquivo falso presente. Depois, `git status --short data/public/alertas_ativos.json.gz` não mostra nada.

- [ ] **Step 5: Escrever o teste da ordem na agenda**

Acrescente ao fim de `tests/test_publicacao_agenda.py`, que já importa `ALVOS`:

```python
def test_alertas_vem_depois_das_fontes_e_antes_da_brapi():
    ordem = [a.chave for a in ALVOS]
    i = ordem.index("alertas_ativos")
    for fonte in ("cvm_ipe", "us_sec_filings", "fii_ingest", "pregoes_recentes_b3"):
        assert ordem.index(fonte) < i, fonte
    assert i < ordem.index("b3_brapi")


def test_alvo_alertas_publica_o_artefato_e_le_o_ipe_recente():
    alvo = next(a for a in ALVOS if a.chave == "alertas_ativos")
    assert alvo.artefatos == ("data/public/alertas_ativos.json.gz",)
    assert alvo.cadencia_dias == 1
    scripts = [p[0] for p in alvo.passos]
    assert scripts == ["scripts/backfill_ipe_metadados.py",
                       "scripts/ingerir_parecer_auditor.py",
                       "scripts/publish_alertas_ativos.py"]
    assert alvo.passos[0][1:] == ("--anos", "recentes")
```

Se `ALVOS` não for o nome importado no topo do arquivo, use `from core.publicacao_agenda import ALVOS`.

- [ ] **Step 6: Rodar e ver falhar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_publicacao_agenda.py -q -k alertas`
Expected: FAIL com `ValueError: 'alertas_ativos' is not in list` e `StopIteration`.

- [ ] **Step 7: Escrever o Alvo e o carimbo**

Em `core/publicacao_agenda.py`, logo depois do `Alvo(chave="carteira20_medicao", ...)` (termina perto da linha 467) e antes do `b3_brapi`, acrescente:

```python
    # Alertas de tese (fatia 1). Vem depois de todas as fontes que o motor lê
    # (cvm_ipe, us_sec_filings, fii_ingest, pregoes_recentes_b3) e antes da
    # brapi, que é a mais lenta. Os três passos escrevem por padrão, sem
    # --apply: o motor só toca o armazém local (exigir_local) e data/public.
    # O backfill relê só os anos recentes; o histórico inteiro foi lido uma
    # vez, na Task 4 do plano da fatia 1.
    Alvo(
        chave="alertas_ativos",
        titulo="Alertas de tese dos ativos",
        passos=(
            ("scripts/backfill_ipe_metadados.py", "--anos", "recentes"),
            ("scripts/ingerir_parecer_auditor.py",),
            ("scripts/publish_alertas_ativos.py",),
        ),
        cadencia_dias=1,
        modulo="b3",
        artefatos=("data/public/alertas_ativos.json.gz",),
    ),
```

Confira os nomes dos campos com o `Alvo` vizinho antes de colar. Se o dataclass usa outro nome para algum campo (por exemplo `cadencia` em vez de `cadencia_dias`), siga o do arquivo e ajuste o teste do Step 5 junto.

Em `scripts/atualizar_vitrines.py`, no dicionário `CARIMBO`, acrescente ao lado das outras entradas de arquivo:

```python
    "alertas_ativos": ("arquivo", "data/public/alertas_ativos.json.gz"),
```

O `gerado_em` da vitrine já é ISO com fuso, que é o que `_carimbo_do_arquivo` lê.

- [ ] **Step 8: Rodar e ver passar**

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_publicacao_agenda.py -q`
Expected: os dois testes novos passam. **Um teste continua vermelho de propósito:** `test_artefato_declarado_e_rastreavel_pelo_git` exige que `data/public/alertas_ativos.json.gz` exista e esteja no git. Ele fica verde no Step 15, depois da primeira publicação. Não commite esta parte antes disso.

- [ ] **Step 9: Conferir que a rotina noturna não está rodando**

A rotina `atualizar_vitrines.py` começa perto das 19:30 e commita artefatos na `main` sozinha. Uma publicação manual concorrendo com ela pode gravar a mesma vitrine duas vezes ou commitar pela metade.

Run (PowerShell):
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'atualizar_vitrines|publish_alertas_ativos|testar_alertas_pit' } | Select-Object ProcessId, CommandLine
```
Expected: nenhuma linha. Se aparecer `atualizar_vitrines`, espere ele terminar e confira de novo. Não mate o processo.

Confira também o armazém:

Run: `docker ps --filter name=dfu_warehouse --format "{{.Names}} {{.Status}}"`
Expected: `dfu_warehouse Up ...`. Se estiver parado, suba com `docker start dfu_warehouse`, autorizado de forma permanente.

- [ ] **Step 10: Atualizar as fontes e rodar o teste PIT**

As fontes do motor precisam estar frescas antes da calibração, pelos limites de `motor.LIMITES_DIAS` (Task 8): `ipe` e `sec_8k` têm 7 dias. A ingestão pesada (histórico inteiro do IPE, parecer do auditor e 8-K) já rodou nas Tasks 4 e 5. Aqui só se completa o que entrou desde então.

```bash
PYTHONIOENCODING=utf-8 "$PY" scripts/backfill_ipe_metadados.py --anos recentes
PYTHONIOENCODING=utf-8 "$PY" scripts/ingerir_parecer_auditor.py
PYTHONIOENCODING=utf-8 "$PY" run_us_ingest.py filings --warehouse --json
```
Expected: os três terminam com código 0. O último imprime um JSON com a contagem de filings novos; zero também é aceitável.

Rode a calibração:

Run: `PYTHONIOENCODING=utf-8 "$PY" scripts/testar_alertas_pit.py --fase calibracao`
Expected: termina com código 0 e grava `docs/alertas_ativos/regras_congeladas.json` e `docs/alertas_ativos/teste_pit_fatia1.md`. O relatório diz, para cada tipo, o n, a taxa-base, a taxa condicional e o intervalo, além de se o tipo passou ou fica experimental. A calibração pode deixar **todos** os tipos experimentais (Desvio 22). Isso é resultado, não defeito: a vitrine publica do mesmo jeito, e a contagem do rótulo fica zero.

Confira que os dois arquivos não levam dado do usuário. Eles cobrem o universo, e não a carteira:

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
from core.proventos import tickers_em_carteira
from pathlib import Path
from core.investimentos import get_carteira
c = get_carteira()
if c.get("data_source") != "real" or not c.get("posicoes"):
    raise SystemExit("CARTEIRA INDISPONÍVEL — conferência não feita")
meus = {t.upper() for t in tickers_em_carteira(c)}
for p in ("docs/alertas_ativos/regras_congeladas.json", "docs/alertas_ativos/teste_pit_fatia1.md"):
    txt = Path(p).read_text(encoding="utf-8").upper()
    achados = sorted(t for t in meus if t in txt)
    print(p, "OK" if not achados else f"CITA {len(achados)} TICKER(S) DA CARTEIRA")
EOF
```
Expected: `OK` nos dois. O script imprime só a contagem, nunca o ticker, para a saída do terminal não ir parar num log. Se der `CITA`, o relatório está listando exemplos por ticker. Troque a listagem por contagem em `core/alertas_ativos/backtest.py` (`relatorio_md`, Task 7), rode a calibração de novo e confira outra vez. `get_carteira` é o leitor que o Dashboard já usa.

Se sair `CARTEIRA INDISPONÍVEL`, a leitura da carteira falhou (`get_carteira` devolve `data_source: "error"` e `posicoes` vazia quando o banco não responde) e a conferência **não** foi feita: um `OK` com a carteira vazia seria falso. Resolva o acesso ao banco e rode de novo antes de seguir.

Commite a calibração **antes** do guardado. O hash do código das regras (`impressao_das_regras`) fica registrado em `regras_congeladas.json`, e o guardado recusa rodar se ele mudar.

```bash
git add docs/alertas_ativos/regras_congeladas.json docs/alertas_ativos/teste_pit_fatia1.md
git commit -m "$(cat <<'EOF'
Alertas: calibração do teste PIT, regras congeladas

Regras e limiares congelados na janela de calibração, com o hash do
código das regras. O guardado roda uma vez sobre esse hash.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 11: Rodar o guardado, uma vez só**

Entre a calibração e este passo, **nenhum arquivo de `core/alertas_ativos/` nem `core/inteligencia_ativos/eliminatorios.py` muda**. Confira:

Run: `git diff HEAD --stat -- core/alertas_ativos/ core/inteligencia_ativos/eliminatorios.py`
Expected: vazio.

Run: `PYTHONIOENCODING=utf-8 "$PY" scripts/testar_alertas_pit.py --fase guardado`
Expected: termina com código 0 e grava `docs/alertas_ativos/resultado_pit_fatia1.json` e `docs/alertas_ativos/guardado_executado.json` (a trava). Também acrescenta a seção do guardado em `teste_pit_fatia1.md`.

Se ele **recusar** (hash diferente, trava já existente ou `pode_rodar_guardado` falso), **pare aqui e reporte ao usuário** com a mensagem do script. Não apague `guardado_executado.json`, não edite o hash e não rode de novo. Rodar o guardado duas vezes é olhar o resultado e escolher, e é exatamente o que o guardado existe para impedir.

Repita a conferência de dado do usuário do Step 10, agora com os quatro arquivos: `regras_congeladas.json`, `teste_pit_fatia1.md`, `resultado_pit_fatia1.json` e `guardado_executado.json`.
Expected: `OK` nos quatro.

```bash
git add docs/alertas_ativos/resultado_pit_fatia1.json docs/alertas_ativos/guardado_executado.json \
  docs/alertas_ativos/teste_pit_fatia1.md
git commit -m "$(cat <<'EOF'
Alertas: resultado do teste PIT na janela guardada

Rodado uma vez sobre as regras congeladas. A trava registra a execução.
Tipo que não passou fica experimental e não conta no rótulo.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 12: Primeira publicação real**

Run: `PYTHONIOENCODING=utf-8 "$PY" scripts/publish_alertas_ativos.py`
Expected: termina com código 0. Grava os alertas no schema `alertas_ativos` do armazém local, com a cadeia de hashes do registro, e grava `data/public/alertas_ativos.json.gz`.

- [ ] **Step 13: Conferir o que foi publicado**

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
from collections import Counter
from core.alertas_ativos import vitrine
from core.inteligencia_ativos.arquivo_publicado import ler  # leitor cru, fora da fixture dos testes
p = ler(str(vitrine.CAMINHO), "alertas_ativos")
print("gerado_em:", p.get("gerado_em"))
print("velha:", vitrine.velha(p))
print("fontes:", {k: (v.get("fresca"), v.get("dias")) for k, v in (p.get("fontes") or {}).items()})
print("por tipo:", dict(Counter((a.get("tipo"), a.get("experimental")) for a in p.get("abertos") or [])))
print("cadeia íntegra (vitrine):", (p.get("cadeia") or {}).get("integra"))
EOF
```
Expected:
- `velha: False`;
- as cinco fontes de `motor.LIMITES_DIAS` aparecem, cada uma como `(True, dias)`; uma fonte com `False` passou do limite e aparece nomeada (nunca some), e os tipos que dependem dela saem como sem dado;
- a contagem por tipo e por experimental;
- `cadeia íntegra (vitrine): True`.

O script não imprime ticker.

Confira a cadeia do registro no armazém:

Run: `PYTHONIOENCODING=utf-8 "$PY" -c "from sqlalchemy import create_engine; from scripts.publish_fii_selection_from_local import _warehouse_url; from core.alertas_ativos import registro as rg; print(rg.verificar(rg.ler(create_engine(_warehouse_url()), 'alertas_ativos')))"`
Expected: o `StatusCadeia` da Task 6 dizendo íntegra, a mesma resposta que a vitrine trouxe em `cadeia.integra`. Se as duas divergirem, a vitrine saiu de outro estado do registro: não siga para o commit.

- [ ] **Step 14: Rodar a suíte das telas com a vitrine presente**

Agora o arquivo publicado existe. A fixture do Step 3 precisa manter os testes iguais aos de antes:

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_investimentos_alertas.py tests/test_dashboard_geral_modernization.py tests/test_inteligencia_ativos_resumida.py tests/test_alertas_vitrine.py tests/test_publicacao_agenda.py -q`
Expected: PASS, incluindo `test_artefato_declarado_e_rastreavel_pelo_git` depois do `git add` do Step 15. Rode-o de novo depois do commit, se ele olhar o índice do git.

- [ ] **Step 15: Commit**

```bash
git add tests/conftest.py tests/test_alertas_vitrine.py core/publicacao_agenda.py \
  scripts/atualizar_vitrines.py tests/test_publicacao_agenda.py \
  data/public/alertas_ativos.json.gz
git commit -m "$(cat <<'EOF'
Alertas: rotina noturna e primeira vitrine publicada

O Alvo alertas_ativos entra na agenda depois das fontes que o motor lê e
antes da brapi. A vitrine sai do universo inteiro, sem a carteira, que é
filtrada na hora de desenhar. Os testes não leem a vitrine publicada.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
PYTHONIOENCODING=utf-8 "$PY" -m pytest tests/test_publicacao_agenda.py -q
```
Expected: PASS, agora sem nenhum vermelho.

---

### Task 12: Suíte inteira, PR, revisão do Codex e merge

**Files:** nenhum arquivo novo. Esta tarefa só entrega o que as Tasks 1–11 fizeram.

**Interfaces:**
- Consumes: o branch com os commits das Tasks 1–11.
- Produces: o PR mergeado na `main` e a fatia 1 no ar no Streamlit Cloud.

"Verificado localmente" não é entregue. Entregue é `gh pr view` dizendo `MERGED` e a `origin/main` contendo o commit.

- [ ] **Step 1: Rodar a suíte inteira**

Não rode duas suítes ao mesmo tempo: elas derrubam o schema de teste uma da outra.

Run: `PYTHONIOENCODING=utf-8 "$PY" -m pytest -q -p no:cacheprovider`
Expected: PASS. Se um teste falhar perto do TTL de 900 s de alguma vitrine, rode só ele de novo antes de concluir que é defeito. Se ele passar sozinho, cite isso no PR. Teste vermelho que nasceu nesta fatia é defeito, e se corrige com TDD antes de seguir.

Rode o lint que o CI roda:

Run: `PYTHONIOENCODING=utf-8 "$PY" -m ruff check core/alertas_ativos scripts/testar_alertas_pit.py scripts/publish_alertas_ativos.py views/investimentos_alertas.py views/dashboard_geral.py views/inteligencia_ativos_resumida.py tests`
Expected: `All checks passed!`

- [ ] **Step 2: Conferir que nada do usuário entrou no diff**

```bash
PYTHONIOENCODING=utf-8 "$PY" - <<'EOF'
import subprocess
from core.proventos import tickers_em_carteira
from core.investimentos import get_carteira
c = get_carteira()
if c.get("data_source") != "real" or not c.get("posicoes"):
    raise SystemExit("CARTEIRA INDISPONÍVEL — conferência não feita")
meus = {t.upper() for t in tickers_em_carteira(c)}
diff = subprocess.run(["git", "diff", "origin/main...HEAD", "--", ".", ":(exclude)data/public"],
                      capture_output=True, text=True, encoding="utf-8").stdout.upper()
log = subprocess.run(["git", "log", "origin/main..HEAD", "--format=%B"],
                     capture_output=True, text=True, encoding="utf-8").stdout.upper()
print("diff:", "OK" if not any(t in diff for t in meus) else "CITA TICKER DA CARTEIRA")
print("log:", "OK" if not any(t in log for t in meus) else "CITA TICKER DA CARTEIRA")
EOF
```
Expected: `OK` nos dois. A vitrine em `data/public` fica de fora da busca porque ela cobre o universo inteiro e por isso contém todos os tickers, inclusive os da carteira, sem dizer quais são seus. Se der `CITA`, ache a linha com `git diff origin/main...HEAD | grep -n <ticker>` (sem colar a saída em lugar nenhum), troque por ticker fictício e faça um commit de correção.

Se sair `CARTEIRA INDISPONÍVEL`, a leitura da carteira falhou (`get_carteira` devolve `data_source: "error"` e `posicoes` vazia quando o banco não responde) e a conferência **não** foi feita: um `OK` com a carteira vazia seria falso. Resolva o acesso ao banco e rode de novo antes de seguir.

- [ ] **Step 3: Push e PR**

```bash
git push -u origin claude/lucid-pascal-9pc9h2
gh pr create --base main --title "Alertas de tese dos ativos: fatia 1 (motor e risco)" --body "$(cat <<'EOF'
## O que entra

- Motor de alertas de tese (`core/alertas_ativos/`): três tipos de risco, que são evento grave, deterioração fundamental confirmada e FII com renda caindo. Cada alerta responde às seis perguntas, tem validade, critério de desfazer e o que aconteceu com casos parecidos.
- Registro imutável no armazém local, com cadeia de hashes. Nada vai para o Supabase.
- Teste PIT com calibração e janela guardada, rodado uma vez. Tipo que não passou fica **experimental** e não conta no rótulo. Resultado em `docs/alertas_ativos/teste_pit_fatia1.md`.
- Vitrine `data/public/alertas_ativos.json.gz`, publicada pela rotina noturna (Alvo `alertas_ativos`). Ela cobre o universo inteiro; a carteira é filtrada na hora de desenhar.
- Telas: subaba 🚨 Alertas em Investimentos, cartão "Alertas da carteira" no Dashboard e selo "· 🚨 <tipo>" na caixa do ativo da Inteligência dos Ativos. Não há botão de comprar nem de vender: alerta não é ordem.

## Configurações acompanham

- 🗺️ Mapa do App: a subaba nova em Investimentos, o cartão do Dashboard, o selo da IA e o fluxograma em Documentação.
- 📚 Documentação: fluxograma `FLOW_ALERTAS`, nó `dg_alertas` no Dashboard e o item do selo no painel da IA.
- 🎯 Grau de Confiança: componente `_alertas_publicados` na nota de Investimentos. O Dashboard herda a nota pelo mínimo. A IA segue fora da nota, e o comentário de `SUBABAS_FORA_DA_NOTA` diz por quê.

## Desvios da spec

Os 23 desvios estão declarados no topo de `docs/superpowers/plans/2026-11-09-alertas-investimentos-fatia1.md`, cada um com o motivo.

## Como foi verificado

- Suíte inteira verde, mais o `ruff`.
- Teste PIT: calibração commitada antes do guardado; guardado rodado uma vez sobre o hash congelado.
- Primeira vitrine publicada com as fontes frescas e a cadeia do registro íntegra.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Se o `gh pr create` disser que o PR já existe, use `gh pr view --json number,url` e siga com esse número.

Guarde o número: `PR=$(gh pr view --json number --jq .number)`.

Depois de abrir, ligue o PR à sessão com as ferramentas `ccd_pr`: `get_status` e, se ele não listar o PR, `bind_pr`. Não agende nem faça polling do CI por conta própria.

- [ ] **Step 4: CI verde**

Run: `gh pr checks $PR`
Expected: todos `pass`. Se algum falhar, leia o log com `gh run view <id> --log-failed`, corrija com TDD, commite e faça push. O PR acompanha o push.

- [ ] **Step 5: Revisão do Codex fechada no último commit**

O merge só sai com a revisão do Codex `OK` no commit HEAD do PR. Git Bash não tem `jq`; o filtro vai pelo `--jq` do `gh`:

```bash
HEAD=$(gh pr view $PR --json headRefOid --jq .headRefOid)
gh api repos/Tiago84Barros/Dashboard-Financeiro-Unificado/issues/$PR/comments --jq '[.[] | select(.user.login=="chatgpt-codex-connector[bot]") | .body | select(test("codex-review-state:v1")) | capture("codex-review-state:v1 (?<s>.*?) -->") | .s | fromjson | .code_review] | last | if . == null then "SEM_REVISAO" elif .commit_sha != "'"$HEAD"'" then "REVISAO_DE_OUTRO_COMMIT" elif .status != "completed" then "EM_ANDAMENTO" elif .has_unresolved_findings then "COM_PENDENCIA" else "OK" end'
```

O que fazer com cada resposta:

- `OK`: siga para o Step 6.
- `SEM_REVISAO`, `REVISAO_DE_OUTRO_COMMIT` ou `EM_ANDAMENTO`: espere em background, até uns 20 minutos, rodando o comando acima de novo. Se ao fim continuar sem revisão do HEAD, comente uma vez só: `gh pr comment $PR --body "@codex review"`. Depois espere de novo.
- `COM_PENDENCIA`: leia os achados com `gh api repos/Tiago84Barros/Dashboard-Financeiro-Unificado/pulls/$PR/comments --jq '.[] | select(.user.login=="chatgpt-codex-connector[bot]") | {path, line, body}'`. Trate cada um com TDD: teste que falha, correção e teste verde. Antes, procure os gêmeos da regra corrigida no repositório inteiro. Commite, faça push e volte ao início deste Step, porque o HEAD mudou.

- [ ] **Step 6: Merge**

Nunca use `gh pr merge --auto`: neste repositório ele mergeia na hora, sem esperar nada.

```bash
HEAD=$(gh pr view $PR --json headRefOid --jq .headRefOid)
gh pr merge $PR --merge --match-head-commit $HEAD
gh pr view $PR --json state --jq .state
git fetch origin main
git merge-base --is-ancestor $HEAD origin/main && echo "na main"
```
Expected: `MERGED` e `na main`. Se o `--match-head-commit` recusar, alguém fez push depois da revisão. Volte ao Step 5.

- [ ] **Step 7: Conferir no Streamlit Cloud**

Depois do deploy, abra o app publicado e confira:

1. Investimentos → 🚨 Alertas: a aba abre, o rótulo mostra a contagem (zero se todo tipo ficou experimental), e o carimbo da vitrine não diz VELHA.
2. Dashboard Geral: o cartão "🚨 Alertas da carteira" aparece antes do Raio X.
3. Inteligência dos Ativos: a caixa de um ativo com alerta aberto mostra o selo. Se nenhum ativo da carteira tem alerta, nenhuma caixa mostra selo, e isso está certo.
4. Configurações → 🎯 Grau de Confiança: Investimentos lista o componente dos alertas publicados.

O deploy do Cloud recarrega só o `app.py`. Se as telas novas não aparecerem, peça ao usuário para rodar "Manage app → Reboot app". Não dá para fazer isso daqui.

Se algo da lista não bater, é defeito desta fatia: corrija com TDD num PR novo e repita os Steps 3–6.

