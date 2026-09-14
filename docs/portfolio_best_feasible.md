# Composição possível com proteção ao investidor

A criação de B3, Americanas e FIIs mantém o caminho original quando ele comporta
uma composição dentro dos critérios. Quando a seleção ou os tetos impedem essa
composição, apresenta uma proposta calculada para revisão, com ativos e capital
não alocado. A proposta pode ser baixada em JSON com seus pesos e ressalvas.

## Critério determinístico

O otimizador resolve dois problemas em sequência: maximiza a fração alocável sob
os limites rígidos; depois maximiza a qualidade observada mantendo essa fração.
Pesos são frações do capital total, não da parte investida. Portanto, ativos mais
saldo não alocado somam 100%, sem renormalização que estoure os tetos. O saldo não
tem rendimento, produto financeiro ou liquidez presumidos.

O modelo busca a melhor solução desse objetivo matemático com os candidatos
observados. Não equivale à melhor carteira futura nem garante retorno. Se o solver
atingir o prazo, uma solução factível passa por verificação independente e pode
ser exibida, declarando que sua otimalidade não foi comprovada. Sem solução
verificável ou sem candidatos suficientes, o saldo não alocado é explicitado.

## Regras por módulo

- FIIs: usa o universo que passou pela elegibilidade. Bandas táticas tornam-se
  metas reportadas lado a lado com o resultado. Preserva quantidade máxima,
  peso mínimo econômico, teto individual (incluindo redução por opacidade),
  teto de categoria, concentrações observadas, iliquidez e incerteza média da
  parcela investida. Exposições obrigatórias ausentes recebem peso zero.
- Americanas: usa empresas elegíveis e o piso individual existente; uma
  indústria sem aprovação estatística não apaga as empresas da proposta.
  Tetos por ativo, indústria e setor são preservados. O caminho de criação
  deixou de ampliar automaticamente tetos para conseguir investir 100%.
- B3: utiliza o ranking atual dos segmentos, o guard de entrada e a avaliação
  de saúde já enriquecida. Excluídas, risco crítico e vetos qualitativos
  conhecidos continuam fora. A nota numérica ordena, enquanto a aprovação
  estatística do segmento permanece uma ressalva. Preserva tetos por ativo,
  setor e classe cíclica. Também intercepta projeções legadas que excederiam
  tetos conjuntos.

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

A proposta alternativa usa os scores e concentrações observáveis, sem tilt macro
ou otimização de correlação. Essas camadas continuam no caminho original e não
são apresentadas como verificadas na proposta. Cinco testes antigos de alocação
integral e um de apresentação exploratória foram atualizados para verificar
saldo explícito e tetos preservados; a primeira execução identificou essa
mudança de contrato antes da regressão final.

Não foram executadas migrações, ordens, gravações em bancos remotos ou alterações
em carteiras reais.
