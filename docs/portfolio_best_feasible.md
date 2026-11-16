# Composição possível com proteção ao investidor

A criação de B3, Americanas e FIIs mantém o caminho original quando ele comporta
uma composição dentro dos critérios. Quando a seleção ou os tetos impedem essa
composição, apresenta uma proposta calculada para revisão, com ativos e capital
não alocado. A proposta pode ser baixada em JSON com seus pesos e ressalvas.

## Critério determinístico

O otimizador resolve até três problemas em sequência (lexicográfico): maximiza a
fração alocável sob os limites rígidos; quando o módulo informa metas por faixa
(bandas táticas dos FIIs), minimiza o desvio total fora dessas faixas mantendo a
fração; por fim maximiza a qualidade observada mantendo as duas anteriores. Meta
nunca veta capital: o que não cabe nela vira desvio medido (`target_deviation`).
Pesos são frações do capital total, não da parte investida. Portanto, ativos mais
saldo não alocado somam 100%, sem renormalização que estoure os tetos. O saldo não
tem rendimento, produto financeiro ou liquidez presumidos.

O modelo busca a melhor solução desse objetivo matemático com os candidatos
observados. Não equivale à melhor carteira futura nem garante retorno. Se o solver
atingir o prazo, uma solução factível passa por verificação independente e pode
ser exibida, declarando que sua otimalidade não foi comprovada. Sem solução
verificável ou sem candidatos suficientes, o saldo não alocado é explicitado.

## Regras por módulo

- FIIs: usa o universo que passou pela elegibilidade. As bandas táticas são
  as mesmas efetivas da carteira normal (`_adaptive_type_bands`, que já as
  ajusta ao universo disponível) e entram como metas: depois do capital, a
  composição minimiza o desvio fora das faixas. A qualidade que decide entre
  fundos é `utilidade_fundamental`, a mesma função da carteira normal
  (percentil no tipo, confiança, renda recorrente, evidência complementar,
  penalidade de cenário adverso e de incerteza); os cards de mesmo peso seguem
  essa utilidade. Preserva quantidade máxima, peso mínimo econômico, teto
  individual (incluindo redução por opacidade), teto de categoria (70%),
  concentrações observadas, iliquidez e incerteza média da parcela investida.
  Exposições obrigatórias ausentes recebem peso zero. Como o capital vem antes
  das metas, um tipo sem capacidade suficiente deixa a sobra para os outros,
  até o teto de 70%, e o desvio aparece medido. Diferença que permanece: a rota
  normal afrouxa tetos individuais por viabilidade; a revisão os mantém rígidos.
  Até 15/11/2026 a revisão usava utilidade própria (sem penalidades de cenário
  e incerteza) e as bandas eram só reportadas; como `type_score` é percentil
  dentro do tipo, ele decidia a divisão entre tipos e um tipo chegava a 70%
  com outro em zero.
- Americanas: as candidatas saem da mesma função da carteira normal
  (`_lideres_por_industria`): até `leaders_per_industry` empresas por
  indústria, pelo score de entrada e fundamentalista dentro da indústria, com
  o piso de qualidade e o portão da Inteligência dos Ativos substituindo pelo
  próximo da mesma indústria. Havendo indústria aprovada (a revisão roda porque
  os tetos não fecham), as candidatas são exatamente as da carteira normal e o
  que não cabe fica como capital não alocado; o corte no `top_n` usa a mesma
  ordem (`_ordenar_por_score`: os dois scores e o ticker como desempate), para
  que um empate na fronteira não dependa da ordem em que as indústrias chegam.
  Sem nenhuma aprovada, uma
  indústria sem aprovação estatística não apaga as empresas da proposta: entram
  primeiro as que têm amostra mínima, pelo menor número de critérios não
  atendidos e pela maior margem até o corte (o menor entre `min_entry_score` e
  a vantagem sobre a mediana), e por último as de amostra abaixo do mínimo.
  Os pesos perseguem o mesmo alvo da carteira normal (`_base_target`) como meta
  lexicográfica; a fila só desempata. Tetos por ativo, indústria e setor são
  preservados. O caminho de criação deixou de ampliar automaticamente tetos
  para conseguir investir 100%. Até 15/11/2026 a revisão juntava toda elegível
  de toda indústria, sem cota, e maximizava o `entry_score`, percentil dentro
  da indústria: os pesos encostavam no teto por ativo (cerca de 13 nomes a 8%
  contra 26 na carteira normal).
- B3: cada segmento analisado entra com uma única candidata, a de maior
  `score_proximo` dentro do segmento, a mesma nota que escolhe a líder na rota
  normal. Notas de segmentos diferentes não são comparadas: a ordem entre
  segmentos é aprovados no backtest primeiro, depois os demais pela menor
  margem até a aprovação (pior entre margem contra Selic e contra Pesos
  Iguais). Score de Entrada "Excluída", veto da Inteligência dos Ativos
  (o mesmo `veredito.VETA_SELECAO` da rota normal), risco crítico e vetos
  qualitativos conhecidos só excluem; a vaga passa à próxima empresa do mesmo
  segmento. Inteligência indisponível não veta e a tela diz isso. Cada card
  mostra o status do segmento no backtest. A nota é percentil entre as
  compráveis de hoje do segmento (a régua análoga às elegíveis da época que
  o backtest usa); com uma só comprável não há par, ela sai 0,0 sem medir
  nada, e o card diz "sem par comparável" (`score_sem_par`), fora da média do
  score. Até 16/11/2026 o card mostrava 0,0 (caso WEGE3, #1 da revisão). Preserva tetos por ativo, setor e
  classe cíclica. Até 14/11/2026 a rota juntava todas as empresas de todos os
  segmentos e ordenava pelo Score de Entrada, um percentil dentro do segmento
  que não compara setores, e descartava `score_proximo`. Também intercepta projeções legadas que excederiam
  tetos conjuntos.
  Depois da composição, a tela segue com os mesmos painéis da rota normal
  (Patches 1 a 5, desempenho da safra vigente, safras ano a ano contra Selic e
  Pesos Iguais, metodologia), calculados sobre os ativos da proposta. Sem
  segmento aprovado, as safras reconstroem todos os segmentos analisados, o
  mesmo conjunto de onde sai a proposta, e a tela avisa. De 10/09 a 13/11/2026
  a rota encerrava a página logo após os cards e esses painéis sumiam.

## Limitações e publicação

Proposta não é aprovação estatística. A rota alternativa declara `can_publish`
falso e não usa os botões legados que pressupõem 100% em ativos. O download
permite revisar a composição completa, inclusive saldo. A carteira-modelo
anterior armazenada não é sobrescrita. Falta de evidência pode resultar em saldo
integralmente não alocado; não se inventam empresas, fundamentos ou pesos para
apresentar uma carteira artificialmente cheia.

As taxas e dimensões são as dos dados carregados; nenhuma consulta externa nova
foi criada. Testes e inspeção visual usam dados sintéticos. A captura do usuário
fornece quantidades por tipo, mas não a matriz de exposições dos 17 FIIs;
o teste com essa cardinalidade não certifica os pesos do universo de produção.

## Verificação

- Testes numéricos: orçamento, tetos conjuntos, cardinalidade, ausência, risco
  crítico, entrada em observação e prazo do solver.
- Regressões dos módulos FII, B3, Americanas e contratos de publicação.
- AppTest: apresentação de composição parcial e 100% de saldo.
- Navegador local: cenário sintético de 17 candidatos, composição de sete FIIs,
  50% alocados e 50% de saldo; viewport normal e 390 × 844, expander e console
  sem erros. Não foi validada uma sessão autenticada com dados reais.
- `python scripts/run_quality_checks.py`: skills, fórmulas, segredos e ambiente.
- Resultado final: 146 testes focados aprovados; Ruff nos arquivos alterados e
  `git diff --check` aprovados. `streamlit run app.py` iniciou em loopback.

A proposta alternativa usa os scores e concentrações observáveis, sem
otimização de correlação; nos FIIs, aplica as bandas táticas do cenário como
metas e as penalidades de cenário adverso e de incerteza da carteira normal. Essas camadas continuam no caminho original e não
são apresentadas como verificadas na proposta. Cinco testes antigos de alocação
integral e um de apresentação exploratória foram atualizados para verificar
saldo explícito e tetos preservados; a primeira execução identificou essa
mudança de contrato antes da regressão final.

Não foram executadas migrações, ordens, gravações em bancos remotos ou alterações
em carteiras reais.
