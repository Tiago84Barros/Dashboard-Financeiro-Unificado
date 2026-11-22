# Carteira de FIIs em safras anuais (estrutura da Empresas B3)

Versão `fii-safras-1.1.0` — `core/fii_safras_carteira.py`, medida por
`scripts/medir_safras_fii.py` no armazém local. Ainda não alimenta nenhuma
tela: é a medição que decide se a estrutura merece substituir o otimizador
mensal da Seleção de FIIs.

## Regra

1. **Decisão em 31/03/N, vigência de abril/N a março/N+1.** O snapshot PIT
   de 31/03 (`reconstruct_snapshots(..., decision_dates=...)`) dá o
   `type_score` de cada fundo com o que era público na data.
2. **Universo:** elegibilidade integrada ESTRITA da data
   (`apply_integrated_eligibility`), sem concessão de proteção. Só entra o
   snapshot de 31/03/N (`ultimo_snapshot_na_data(..., idade_max_dias=0)`):
   o fundo que saiu da bolsa não gera snapshot novo e não herda o do ano
   anterior.
3. **Segmento = tipo** (`tijolo`, `papel`, `fof`, `hibrido`).
4. **Líderes por tipo** — o laço de `_processar_segmento` da B3, pelas mesmas
   funções (`core/pesos_por_score.py`): decaimento de 7% a.a. por ano
   consecutivo de liderança (teto 30%), n pela heurística dos três primeiros,
   no mínimo os 4 nomes que o cap 0,25 exige, pesos ∝ score^0,9 com cap suave.
5. **Portão por tipo (modo Econômico, `aprova_economico`)**, só com dados até
   março/N: DCA mensal sobre retorno total com os líderes dos anos anteriores
   precisa superar o DCA na Selic em 15%; Rank-IC médio não pode ser < −0,05;
   teste de seleção (estratégia − Pesos Iguais, janela de 2 anos) não pode
   ser CONTRA; liderança no máximo 5 anos atrás. Sem Selic observável em
   algum mês do DCA, o tipo reprova ("Selic ausente no período do portão"):
   não há taxa suposta. A referência da margem é o parâmetro `portao`
   (`aprova_fii`), ver "Adaptações ao contexto FII".
6. **Seleção por tipo aprovado:** líder + maior participação no DCA
   (`selecao_do_segmento`), com a regra de recência; ou os N primeiros do
   score, com `nomes_por_tipo` (`selecao_fii`).
7. **Montagem:** orçamento igual por tipo aprovado, cap 0,25
   (`montar_carteira`). Menos de 4 nomes → inviável.
8. **Regra D1 (`itens_da_cadeia`):** depois da primeira safra que respondeu,
   safra sem carteira salvável fica em CAIXA à Selic de 31/03/N.
9. **Custos:** giro × (0,15% + 0,10%), giro cheio na entrada; IR de 20% sobre
   ganho de capital realizado (`RastreadorIR`), provento isento. O provento
   que entra no custo é o reinvestido mês a mês
   (`core.backtest_liquido.renda_reinvestida`), a mesma conta do backtest
   robusto: o composto do total menos o do preço isentaria a alta do próprio
   provento reinvestido.
10. **Retorno da safra:** composto mês a mês; mês ausente rende 0 (A-118);
    winsorização P1–P99 pelo universo elegível (mínimo 30 fundos). O
    provento que vai ao IR acompanha o corte (`renda_winsorizada`): o corte
    para baixo sai primeiro do ganho de preço positivo e depois do provento,
    para o razão não ganhar uma perda de capital que não existiu.
11. **Referências:** IFIX oficial (bruto), Pesos Iguais do universo elegível
    winsorizado, Selic. O IFIX precisa existir em cada mês observado da
    carteira; faltando um, o excesso sobre o IFIX da safra fica indisponível
    e não entra na média. Safra incompleta é contabilizada e não entra na
    média.

## Adaptações ao contexto FII (v1.1.0)

O porte copia o algoritmo de decisão da B3, não os números dela. Cada
adaptação é um parâmetro; o default é a regra da B3, para a medição comparar
as duas no mesmo armazém (`scripts/medir_safras_fii.py`, que mede a grade
inteira das opções).

| Parâmetro | Valores | O que muda |
|---|---|---|
| `portao` | `selic` (default) | margem `thr_selic` sobre o DCA na Selic bruta — a B3 |
| | `selic_liquida` | margem `thr_selic` sobre o DCA na Selic depois de `ir_renda_fixa` (15%) sobre o ganho |
| | `ifix` | margem `thr_ifix` (default 0) sobre o DCA no IFIX oficial, nos mesmos meses |
| `nomes_por_tipo` | `None` (default) | líder + maior participação, até 2 nomes por tipo — a B3 |
| | N ≥ 1 | os N primeiros do score ajustado do tipo; cap do tipo `max(cap, 1/N)` |

Por que cada uma:

- **Selic líquida.** O provento do FII é isento para a pessoa física e é
  quase todo o retorno do fundo; a Selic paga IR. Comparar com a Selic bruta
  pune o FII por um imposto que ele não paga. A alíquota de 15% é a menor da
  tabela regressiva: dá a Selic líquida mais alta, o portão mais exigente
  dessa variante. O DCA da estratégia continua bruto dos 20% sobre ganho de
  capital (o portão só tem o retorno total), o que favorece o FII aqui.
- **IFIX.** É o que o investidor compraria no lugar da carteira, e é o
  benchmark do resultado. O IFIX precisa existir em cada mês do DCA; faltando
  um, o tipo reprova ("IFIX ausente no período do portão").
- **N por tipo.** A regra de 2 nomes é da B3, onde o segmento CVM tem 2 ou
  3 empresas. Tipo de FII tem dezenas de elegíveis; com 2 nomes por tipo e
  cap 0,25, um único tipo aprovado não fecha a carteira e a safra vai para
  caixa. Com N ≥ 4, um tipo basta. O cap da carteira (0,25) continua valendo
  na montagem, sobre todos os tipos juntos.

Nenhum valor novo foi escolhido sem medir: o default fica na regra da B3 até
a medição no armazém mostrar qual variante sustenta excesso sobre o IFIX.

## O que da B3 ficou de fora

- Resiliência ROIC − Selic: FII não tem ROIC.
- Tetos de setor e de ciclo: o tipo já é o segmento, com orçamento igual.
- Cap adaptativo: tipo de FII tem dezenas de elegíveis; o cap é estrito.
- Portão qualitativo LLM, piso de qualidade e Score de Entrada: são da tela
  de ações e não têm equivalente medido para FII.

## Limites conhecidos

- No default, cada tipo aprovado contribui no máximo 2 nomes; com cap 0,25
  são precisos ao menos 2 tipos aprovados, senão a safra vai para caixa
  (`nomes_por_tipo` resolve, se a medição sustentar).
- O portão de 15% sobre a Selic é o da B3; FII rende perto do CDI no longo
  prazo, então é o critério que mais reprova. O script mede a sensibilidade
  (`--thr-selic 15,5,0`) e as outras referências (`--portao`).
- O portão IFIX e o resultado usam o mesmo índice: quem passa no portão
  ganhou do IFIX no passado, o que não garante ganhar na safra seguinte.
- Poucas safras (uma por ano): o intervalo de confiança é largo. A leitura
  (`leitura_honesta`) diz quando ele não distingue a carteira de comprar o IFIX.
- O giro de saída para caixa segue a convenção de `portfolio_turnover`
  (meia soma das diferenças), a mesma do backtest robusto.
