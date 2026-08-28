---
name: profissionalizar-app4
description: Conduz subagentes sequenciais implementador-auditor, em rodadas verificáveis e econômicas, para profissionalizar Empresas B3, Seleção de FIIs, Empresas Americanas e Portfólio Global. Use somente quando o usuário pedir explicitamente a auditoria ou profissionalização ampla do App 4.
argument-hint: "[módulo ou gate inicial opcional]"
---

# Profissionalização verificável do App 4

Execute este protocolo como líder. O objetivo não é declarar que o sistema é
profissional; é produzir evidência suficiente para que um auditor independente
possa concluir isso nos limites documentados.

## 1. Preparar

1. Leia `AGENTS.md`, `CLAUDE.md` e as regras do cofre `../ProjetoIA/`.
2. Leia integralmente `references/rubrica-profissional.md` e
   `references/formato-veredito.md`.
3. Verifique o Git. Preserve mudanças do usuário e não misture escopo alheio.
4. Crie `artifacts/app4_professionalizacao/` somente para relatórios gerados,
   resultados e evidências não sensíveis. Nunca copie dados financeiros reais,
   segredos ou URLs de conexão para lá.

## 2. Orquestração sequencial e controle de custo

Não crie agent team. Use subagentes comuns, um por vez, reutilizando os tipos
do projeto:

- `app4-implementador` para uma correção delimitada;
- `app4-auditor` somente depois que o implementador terminar.

O líder é o canal entre eles: entrega o achado ao implementador, recebe a
evidência, encerra esse contexto e só então abre o auditor. Devolve a reprovação
ao implementador em uma nova rodada. Nunca mantenha os dois ativos em paralelo
e nunca permita edição concorrente.

Política obrigatória de custo:

- líder e subagentes usam Sonnet; implementador em esforço médio e auditor em
  esforço alto apenas durante a revisão;
- não use Opus, contexto 1M, agent teams ou agentes adicionais sem autorização
  humana explícita;
- se Sonnet falhar duas vezes no mesmo problema, registre o impasse e pergunte
  antes de usar Opus;
- cada spawn recebe somente o achado atual, arquivos envolvidos, critério de
  aceite e checkpoint curto; não copie a conversa inteira ou logs extensos;
- encerre cada subagente assim que ele devolver o resultado e compacte o
  contexto do líder por checkpoints baseados em arquivos e comandos.

## 3. Levantar a baseline

Abra primeiro um subagente `app4-auditor` para inspecionar os quatro fluxos:

1. Empresas B3: dados → análise/score → criação → avaliação de carteira;
2. Seleção de FIIs: dados/documentos → score → carteira → monitoramento;
3. Empresas Americanas: EDGAR/mercado → análise/score → carteira;
4. Portfólio Global: adapters B3/FII/EUA → consolidação, moeda e risco.

Produza uma matriz requisito×evidência e um backlog ordenado por risco. Não
comece por refinamento visual enquanto houver falha de integridade, temporalidade,
cálculo, isolamento de dados ou risco de decisão.

## 4. Rodar o ciclo implementador ↔ auditor

Para cada fatia vertical:

1. O líder inicia `app4-implementador` com um achado reproduzível e seu critério
   de aceite.
2. O implementador cria plano curto, adiciona teste de reprodução, corrige a
   causa-raiz, roda verificações focadas e devolve evidências. O contexto encerra.
3. O líder inicia `app4-auditor` em contexto limpo, passando somente requisito,
   diff, comandos e evidências da rodada.
4. O auditor repete testes independentemente, procura regressões e
   contraexemplos, devolve `ACEITO`, `REABERTO` ou `NOVO ACHADO` e encerra.
5. Se reprovado, o líder abre nova rodada do implementador com o feedback exato.
6. Se aceito, o líder registra checkpoint curto e segue para o próximo gate.

Após cada conjunto coerente, execute testes focados. Antes do veredito, execute
`python scripts/run_quality_checks.py --full`, testes direcionados aos quatro
módulos, inicialização do Streamlit e validação funcional/visual com dados
sintéticos. Rede, banco ou navegador indisponível é pendência documentada, não
resultado aprovado.

## 5. Limites do ciclo

- Nunca contorne permissões nem execute mudança destrutiva para destravar a meta.
- Solicite autorização humana antes de qualquer migração persistente, gravação
  remota, deploy, push, compra, venda ou rebalanceamento.
- Se o mesmo bloqueio externo persistir por três rodadas, pare esse ramo,
  registre evidência e peça a decisão humana; não entre em loop infinito.
- Não encerre porque o tempo ou o contexto está acabando. Registre checkpoint e
  continue em contexto limpo.
- O sistema oferece apoio analítico e cenários; não promete resultados nem
  substitui aconselhamento profissional ou decisão humana.

## 6. Encerrar

O auditor emite o veredito no formato exigido. O líder só conclui quando:

- todos os gates obrigatórios estiverem `APROVADO` com evidência;
- os quatro módulos tiverem cobertura explícita;
- não houver achado `CRITICA` ou `ALTA` aberto;
- a suíte completa e as validações obrigatórias tiverem passado sem skips novos;
- documentação, limitações e riscos residuais estiverem atualizados no app e
  no cofre;
- o auditor tiver emitido literalmente `APP4_PROFISSIONAL_APROVADO`.

Encerre qualquer subagente remanescente e apresente ao usuário mudanças,
evidências, riscos residuais e decisões que ainda exijam revisão humana.
