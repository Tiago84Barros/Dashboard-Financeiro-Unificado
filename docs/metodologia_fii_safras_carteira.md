# Carteira de FIIs em safras anuais (estrutura da Empresas B3)

Versão `fii-safras-1.0.0` — `core/fii_safras_carteira.py`, medida por
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
   não há taxa suposta.
6. **Seleção por tipo aprovado:** líder + maior participação no DCA
   (`selecao_do_segmento`), com a regra de recência.
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
    winsorização P1–P99 pelo universo elegível (mínimo 30 fundos).
11. **Referências:** IFIX oficial (bruto), Pesos Iguais do universo elegível
    winsorizado, Selic. O IFIX precisa existir em cada mês observado da
    carteira; faltando um, o excesso sobre o IFIX da safra fica indisponível
    e não entra na média. Safra incompleta é contabilizada e não entra na
    média.

## O que da B3 ficou de fora

- Resiliência ROIC − Selic: FII não tem ROIC.
- Tetos de setor e de ciclo: o tipo já é o segmento, com orçamento igual.
- Cap adaptativo: tipo de FII tem dezenas de elegíveis; o cap é estrito.
- Portão qualitativo LLM, piso de qualidade e Score de Entrada: são da tela
  de ações e não têm equivalente medido para FII.

## Limites conhecidos

- Cada tipo aprovado contribui no máximo 2 nomes; com cap 0,25 são precisos
  ao menos 2 tipos aprovados, senão a safra vai para caixa.
- O portão de 15% sobre a Selic é o da B3; FII rende perto do CDI no longo
  prazo, então é o critério que mais reprova. O script mede a sensibilidade
  (`--thr-selic 15,5,0`).
- Poucas safras (uma por ano): o intervalo de confiança é largo. A leitura
  (`leitura_honesta`) diz quando ele não distingue a carteira de comprar o IFIX.
- O giro de saída para caixa segue a convenção de `portfolio_turnover`
  (meia soma das diferenças), a mesma do backtest robusto.
