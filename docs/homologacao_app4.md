# Homologação e liberação gradual — APP4

> *"Não libere imediatamente todas as funcionalidades para decisões reais."*

Data desta medição: **03/09/2026**. Tudo que está escrito abaixo como número saiu
de execução, não de leitura de código.

---

## 1. Como reproduzir a evidência

```bash
python -m pytest tests/test_homologacao.py tests/test_homologacao_cenarios.py -q -s
```

O interpretador é o `Python312` (o `python` do PATH cai na venv do Hermes e não
tem pytest). Resultado medido em 03/09/2026:

| arquivo | testes | resultado |
|---|---:|---|
| `tests/test_homologacao.py` | 38 | passou |
| `tests/test_homologacao_cenarios.py` | 24 (17 cenários; o C12 é parametrizado em 8) | passou |
| suíte completa do projeto | 3.847 passaram, 3 puladas | ver nota |

**Nota sobre a suíte completa.** A execução de 492 s acusou 11 falhas, todas em
`tests/test_views_inteligencia_mercado.py`, todas com a mesma assinatura: o teste
lê o corpo de uma função com `inspect.getsource` e recebeu o corpo de **outra**
função (`render_antifragilidade` devolvendo o código de `render_noticias`, por
exemplo). A causa é minha: editei `views/inteligencia_mercado.py` **enquanto a
suíte rodava**, e o `inspect` casou offsets de linha antigos com o arquivo novo.
Rodado isoladamente depois da edição, o mesmo arquivo dá **24 passed in 4,82 s**.
Nenhuma correção de código foi necessária — e fica o registro operacional: não
editar fonte com a suíte em execução.

---

## 2. As quatro fases

A diferença entre as fases **não é quanto o sistema calcula** — é quanto ele
afirma.

| fase | nome | o que o usuário vê |
|---:|---|---|
| 1 | Observação silenciosa | nada. Coleta e mede; ninguém decide com base nisso. |
| 2 | Painel informativo | o que foi medido, com fonte e frescor. Sem recomendação. |
| 3 | Recomendações conjunturais | sugestões, com confirmação explícita. |
| 4 | Modo Crise | comportamento excepcional completo, alertas externos inclusive. |

A fase vem de `APP4_FASE`. **Valor ausente, ilegível ou fora de 1..4 cai na
Fase 1** — cair na Fase 4 por causa de um typo liberaria decisão real por
engano (cenário C02, medido).

## 3. As nove chaves independentes

Cada funcionalidade tem flag própria e variável própria. Duas travas atuam sobre
ela, e elas não se substituem: **a flag** (a vontade de quem configura) e **a
fase** (o teto do que essa vontade alcança).

| flag | variável | fase mínima | quando desligada |
|---|---|---:|---|
| coleta | `APP4_FLAG_COLETA` | 1 | nenhuma notícia nova entra |
| classificação | `APP4_FLAG_CLASSIFICACAO` | 1 | notícias entram sem tipo nem severidade |
| impacto histórico | `APP4_FLAG_IMPACTO_HISTORICO` | 2 | some a comparação com eventos passados |
| antifragilidade | `APP4_FLAG_ANTIFRAGILIDADE` | 2 | some o índice e sua decomposição |
| LLM | `APP4_FLAG_LLM` | 2 | a explicação passa a ser a determinística do backend |
| alteração de prioridade | `APP4_FLAG_ALTERACAO_PRIORIDADE` | 3 | a ordem dos aportes não muda por conjuntura |
| Modo Crise | `APP4_FLAG_MODO_CRISE` | 4 | níveis 3 e 4 registrados, tela não muda de modo |
| alertas externos | `APP4_FLAG_ALERTAS_EXTERNOS` | 4 | alertas ficam só no painel |
| recomendação emergencial | `APP4_FLAG_RECOMENDACAO_EMERGENCIAL` | 4 | nenhuma recomendação de emergência é gerada |

**Estado de fábrica, medido (C01):** `fase=1`, ligadas = `('coleta',
'classificacao')`. Nada que afirme algo ao usuário vem ligado.

**O teto funciona (C03):** com `APP4_FASE=2` e `APP4_FLAG_MODO_CRISE=true`,

```
ativo=False  motivo='a fase corrente é Fase 1 — ... e esta funcionalidade exige Fase 4 — Modo Crise'
barradas_pela_fase=('modo_crise',)
```

A distinção entre *"a flag está desligada"* e *"a fase não alcança"* existe para
que ninguém passe a tarde ligando um interruptor que a fase anula.

**Independência (C04):** desligar só a LLM com as outras oito ligadas deixa
`ligadas=8 de 9`, `llm=False`. Desligar a LLM não desliga a coleta.

## 4. As portas de entrada — por que as flags não são decoração

Uma flag que ninguém consulta é enfeite (`memoria:
diagnostico-precisa-porta-de-entrada`). Estas são as chamadas que a consultam,
verificadas por teste:

| flag | onde é lida |
|---|---|
| coleta | `views/inteligencia_mercado.py::coletar_noticias` — **dentro da função**, não só no botão |
| Modo Crise | `render_crise` |
| antifragilidade | `render_antifragilidade` |
| impacto histórico | `render_memoria` |
| LLM | `render_explicacao` |

Medido (C10), com a coleta desligada:

```
resultado=None  motivo='a coleta está desligada (a flag APP4_FLAG_COLETA está desligada)'
```

A verificação mora dentro de `coletar_noticias` e não apenas no botão porque uma
checagem que vive só na tela deixa qualquer outro chamador passar por baixo dela.

**Seção desligada não some em silêncio.** `secao_desligada()` diz qual das duas
travas atua, o que muda quando for liberada, e qual é a fase corrente. Sumiço
silencioso não distingue *"desligado de propósito"* de *"quebrado"*.

**A LLM é a exceção deliberada:** com a flag desligada a seção continua na tela,
com a explicação determinística do backend. Esconder a seção inteira puniria o
usuário por uma decisão de liberação, quando o conteúdo verificável existe de
qualquer forma.

## 5. Critérios objetivos de avanço

Um critério devolve `None` quando ninguém mediu. **`None` não avança a fase e
não reprova o sistema** — é a lei do projeto (`ok=None` nunca é `False`), e aqui
ela importa mais do que em qualquer outro lugar: um critério que devolvesse
`False` por falta de medição faria o avanço parecer "reprovado por desempenho"
quando ninguém rodou o teste; um que devolvesse `True` liberaria decisão real
com base em nada.

### Fase 1 → 2 (Painel informativo)

| critério | limiar | por quê |
|---|---|---|
| `cobertura_de_frescor` | ≥ 0,95 | dado sem carimbo não pode ser exibido como atual |
| `itens_sem_fonte` | ≤ 0 | toda notícia mostra fonte, data e hora |
| `taxa_de_erro_da_coleta` | ≤ 0,05 | coleta que falha muito publica painel velho com cara de novo |

### Fase 2 → 3 (Recomendações conjunturais)

| critério | limiar | por quê |
|---|---|---|
| `erro_de_calibracao_probabilidade` | ≤ 0,10 | é o critério explícito de não-produção do Prompt 3 |
| `alarmes_por_semana` | ≤ 7 | alarme excessivo treina o usuário a ignorar |
| `cobertura_da_trilha` | ≥ 1,0 | recomendação sem registro não responde "por que naquele momento" |
| `respostas_llm_reprovadas` | ≤ 0,10 | reprovação alta é sinal de modelo inventando, não de filtro bom |

### Fase 3 → 4 (Modo Crise)

| critério | limiar | por quê |
|---|---|---|
| `falsos_positivos_nivel_3_ou_4` | ≤ 0 | crise declarada por engano é o dano mais caro deste sistema |
| `cenarios_historicos_reproduzidos` | ≥ 11 | os 11 cenários históricos do requisito original |
| `tempo_ate_rebaixar_nivel_h` | ≤ 24 h | sem rebaixamento e encerramento, o Modo Crise vira estado permanente |

Medições (C05, C06, C07):

```
sem medir nada:  fase=1  nao_medidos=(cobertura_de_frescor, itens_sem_fonte, taxa_de_erro_da_coleta)  reprovados=()
cobertura 0,40:  cobertura_de_frescor: 0.4 — NÃO atende (exigido ≥ 0.95)   → fase=1
tudo atendido:   fase=2, llm=True, modo_crise=False (a Fase 2 não alcança o Modo Crise)
```

Cada critério foi testado nos dois sentidos: todos podem atender e todos podem
reprovar. Portão que só sabe dar um resultado é decoração (`memoria:
gate-que-so-dava-false`).

### 5.1 Quem mede (03/09/2026)

Até aqui **nenhum critério tinha medidor**. Todos saíam `None`, a tela escrevia
"não medido nesta instalação" para os dez, e nenhuma fase podia avançar por
*ausência de medição* — não por reprovação. `core/homologacao/medicoes.py` é o
registro dos medidores, e a tela passa a mostrar o valor medido, o veredito
(`✓ atendido` / `⊘ reprovado` / `· não medido`) e, quando não há medidor, **o
motivo**.

| critério | medidor | situação |
|---|---|---|
| `cenarios_historicos_reproduzidos` | `stress_tests.cenarios_reproduzidos` | **medido: 11** |
| `falsos_positivos_nivel_3_ou_4` | — | exige operação real; a Fase 4 nunca rodou |
| `tempo_ate_rebaixar_nivel_h` | — | exige ciclo completo de subida e rebaixamento em produção |

**Os dois sem medidor continuam sem medidor de propósito.** Ambos são "menor
melhor": um medidor que devolvesse `0.0` por não ter encontrado nada aprovaria o
critério exatamente por não tê-lo testado. É o pior defeito possível neste
lugar, e a Fase 4 segue bloqueada por medição ausente — que é a verdade.

### 5.2 Os 11 cenários históricos

O módulo tinha 5. Os seis novos foram escolhidos por **mecanismo distinto**, não
por número redondo: onze repetições de "bolsa cai e dólar sobe" mediriam o mesmo
evento onze vezes.

| cenário | referência | IBOV observado | USD/BRL | mecanismo |
|---|---|---|---|---|
| Crise CDS 2002 | abr–out/2002 | −34% | +52% | risco-país |
| Subprime 2008 | set/2008–fev/2009 | −41% | +30% | crise financeira global |
| Janeiro Vermelho 2015 | jan–jul/2015 | −13% | +12% | fiscal e downgrade |
| Joesley Day 2017 | 18/mai/2017 | −9% | +8% | choque político de um pregão |
| COVID Crash 2020 | fev–mar/2020 | −29% | +25% | pandemia |
| **Crise Asiática 1997** | out–nov/1997 | −25% | +1% | contágio com câmbio ancorado |
| **Moratória Russa 1998** | ago–set/1998 | −40% | +2% | fuga de capital, câmbio ainda ancorado |
| **Maxidesvalorização 1999** | jan–mar/1999 | −10% | **+64%** | choque cambial puro |
| **Zona do Euro 2011** | jul–dez/2011 | −18% | +13% | contágio soberano |
| **Taper Tantrum 2013** | mai–ago/2013 | −20% | +17% | fluxo saindo de emergentes (IFIX −18%) |
| **Aperto Global 2022** | jan–out/2022 | **+5%** | **−5%** | exterior cai e bolsa BR resiste |

O de 2022 é deliberado: sem ele o conjunto afirmaria que crise é sempre bolsa
brasileira caindo com dólar subindo, e uma carteira que passasse nos onze
estaria protegida contra um cenário, não contra onze.

**Contar não é medir.** `cenarios_reproduzidos()` **não** devolve
`len(SCENARIOS)` — isso fecharia o critério sem conferir nada. Cada cenário é
aplicado a uma carteira canônica pelo caminho de código real (mapa de classe →
atributo de choque → agregação) e o resultado é comparado ao retorno observado
no índice, com tolerância de 0,1 pp. Um choque adulterado reprova, e há teste
que prova isso (`test_cenario_com_choque_adulterado_reprova`).

**O que a conferência não prova:** que os números estão historicamente certos,
que os choques das outras classes estão calibrados, ou que a modelagem (choque
uniforme por classe, sem correlação cross-asset) descreve o evento. O docstring
do módulo já diz que não descreve; para análise rigorosa a recomendação continua
sendo cópulas (M2 do parecer). Cenário sem observado declarado sai **não
conferido**, nunca reprovado — e não entra na contagem.

## 6. Rollback

`rollback()` mexe **só na fase**; as flags ficam como estavam. O teto por fase já
desliga o que a fase menor não alcança, e reconfigurar nove chaves no pior
momento possível é como um rollback vira um segundo incidente.

Medido (C08, C09):

```
4 → 3:  saíram do ar ('modo_crise', 'alertas_externos', 'recomendacao_emergencial');
        'alteracao_prioridade' continua ativa; a configuração das nove chaves é idêntica.
4 → 1:  restaram ligadas ('coleta', 'classificacao').
```

Na prática: mudar `APP4_FASE` e reiniciar. A tela de Homologação mostra, antes,
exatamente o que sairia do ar.

## 7. Os 17 cenários de homologação

Todos executáveis em `tests/test_homologacao_cenarios.py`. Cada teste nomeia no
docstring o que aconteceria em produção se ele falhasse.

| # | cenário | evidência medida |
|---:|---|---|
| C01 | instalação nova, ninguém configurou | `fase=1`, ligadas = coleta, classificação |
| C02 | `APP4_FASE='quatro'` | fase resultante = 1 |
| C03 | Modo Crise ligado na Fase 2 | não liga; motivo cita a fase |
| C04 | desligar só a LLM | 8 de 9 ligadas; coleta intacta |
| C05 | avanço sem nenhuma medida | não avança; **zero reprovados** |
| C06 | cobertura de frescor 0,40 | `NÃO atende (exigido ≥ 0.95)`; fase mantida |
| C07 | tudo medido e atendido | fase 2; configuração preservada |
| C08 | rollback 4→3 | 3 funcionalidades saem; 1 permanece |
| C09 | rollback 4→1 | só coleta e classificação sobrevivem |
| C10 | coleta desligada | `coletar_noticias` recusa e nomeia a variável |
| C11 | LLM desligada | explicação determinística continua na tela |
| C12 | 8 manchetes hostis (uma por categoria proibida) | todas cercadas e denunciadas por categoria |
| C13 | número que só existe na manchete (A-148) | sem atribuir: reprovada, `inventados=('37,4',)`; atribuindo: aprovada, `externos=('37,4',)` |
| C14 | dados vencidos | `bloqueios=['recomendacao_emergencial']` |
| C15 | provedores divergem | `bloqueios=()`, `confianca_rebaixada=True` |
| C16 | auditoria indisponível | `bloqueios=('mudanca_estrategica',)` |
| C17 | limite de notificação externa estourado | `permitido=False`, `espera=3600s`, com motivo |

C13 merece uma nota que só apareceu ao rodar de novo em 03/09/2026: ele passou
a ficar verde **sem guardar nada**. Com o contexto macro ligado, o lastro do
backend foi de 7 para 71 números, e com esse tamanho a aritmética da ancoragem
"deriva" 37,4 sozinha. O cenário não tinha mudado; o dado tinha. Está corrigido
e documentado como A-161 em `docs/seguranca_app4.md`, com dois testes novos —
um que reprova sem a correção e um que prova que derivação legítima continua
ancorada. A lição para este relatório: **cenário verde não é evidência se a
entrada dele depende do ambiente de quem roda.**

C15 merece destaque: divergência entre provedores **rebaixa confiança e não
bloqueia**. Incerteza com tamanho vira banda, não portão (`memoria:
incerteza-com-tamanho-nao-bloqueia`).

## 8. A tela de administração — e por que ela não liga nada

`views/homologacao.py` (rota **🚦 Homologação**) mostra a fase, as nove chaves
com símbolo textual além do badge (`✓` ligada, `·` desligada, `⊘` barrada pela
fase — a leitura não pode depender de distinguir verde de vermelho), o que falta
medir para avançar, o efeito de um rollback e o estado bruto lido da
configuração.

Ela **não tem botão, toggle nem checkbox**, e existe teste para isso. O motivo é
uma limitação real e não uma escolha estética: **o app não tem controle de
acesso por papel** — não existe usuário administrador separado do usuário comum.
Um botão que liberasse o Modo Crise a partir da tela seria um botão de liberação
de decisão real ao alcance de qualquer sessão aberta. Enquanto isso for verdade,
quem muda a fase é quem tem acesso aos secrets do deploy.

---

## 9. Relatório final

**1. O APP4 está liberado para decisões reais?** Não. Está na **Fase 1 —
Observação silenciosa**, e é o que sai de fábrica.

**2. O que está ligado hoje?** Coleta e classificação. As outras sete flags estão
desligadas e, mesmo se ligadas, seis delas seriam barradas pela fase.

**3. O sistema executa alguma operação sozinho?** Não. Nenhuma recomendação vira
ordem; a confirmação de mudança grande (`core/auditoria/confirmacao.py`) exige
nove pontos preenchidos e usa rótulos neutros ("Confirmar esta mudança", "Não
fazer", "Decidir depois").

**4. Toda decisão automática é auditável?** As que a trilha cobre, sim:
`core/auditoria/trilha.py` responde por escrito "por que o APP4 recomendou essa
mudança naquele momento", com motor, versão de modelo, versão de dados, frescor
e evidências. A tabela `public.recomendacao_auditoria` (SQL 067) foi executada
no Supabase em **03/09/2026**: 20 colunas, 2 constraints de domínio, 2 índices,
tamanho do banco inalterado em 427 MB. Verificada com uma gravação e uma leitura
de ida e volta, e a linha de teste foi apagada em seguida — a tabela está vazia.

**5. A LLM pode inventar número?** Ela pode gerar; o número não passa.
`validar()` ancora a resposta no **`texto_backend`** — o prompt sem o bloco de
conteúdo recuperado. Antes dessa correção (A-148), uma manchete plantada
ancorava qualquer afirmação: medido, `aprovada=True, ancoragem=1.00` para um
número que só existia na manchete do atacante. Em 03/09/2026 a mesma defesa
foi encontrada **diluída** pelo crescimento do lastro (A-161): com 71 números no
contexto, o número da manchete voltou a ancorar por derivação. Corrigido; medido
depois: `aprovada=False, inventados=('37,4',), ancoragem=0,00`.

**6. A LLM pode alterar score?** Não. Ela não escreve em lugar nenhum; a resposta
é texto validado contra o painel, e a tentativa de instrução vinda de notícia é
detectada por categoria antes de o prompt ser montado.

**7. Notícia externa pode instruir o sistema?** As oito categorias proibidas são
reconhecidas e registradas (C12). O que nenhum teste local prova é que o modelo
obedecerá à cerca — por isso existe a verificação de saída, e não só a de
entrada.

**8. O que acontece quando os dados estão velhos?** Nenhuma recomendação de
emergência é emitida (C14), e o dado continua à vista com o carimbo de idade.
Esconder o dado velho seria trocar um problema por outro.

**9. E quando as fontes discordam?** A confiança cai e nada é bloqueado (C15).

**10. Existe limite de uso?** Sim, janela deslizante por nome de limite: LLM
30/h, coleta 240/h, preço 600/h, notificação externa 6/h. Janela deslizante e não
balde de hora cheia — o balde deixa passar o dobro do teto na virada.

**11. Dá para voltar atrás com segurança?** Sim, mudando `APP4_FASE`. O rollback
não mexe nas flags e está medido nos dois saltos (C08, C09).

**12. Os critérios de avanço são objetivos?** São dez critérios com limiar
numérico declarado e justificativa escrita. Nenhum deles está medido nesta
instalação — e por isso **nenhuma fase está liberada para avançar**.

**13. O que ainda não está pronto?** Ver a seção 10.

**14. Alguma coisa está sendo apresentada como pronta sem estar?** Não que eu
tenha encontrado. As pendências abaixo estão desativadas por flag ou barradas
pela fase, e a tela de Homologação as mostra com o motivo.

---

## 10. O que ainda não está pronto

1. **A medição dos dez critérios de avanço não está automatizada.** Os limiares
   existem, o motor de avaliação existe e está testado; quem preenche `medidas`
   ainda é uma pessoa. Enquanto isso, `pode_avancar` é `False` por
   `nao_medidos`, que é o lado seguro.
2. ~~SQL 067 não executado no Supabase.~~ **Resolvido em 03/09/2026** — a
   tabela existe e a trilha grava. ~~Falta agendar o expurgo.~~ **Agendado em
   03/09/2026** (`update_retencao`, diário, `priority` 11). Ele **simula por
   omissão**: conta e reporta quantas linhas a janela de 365 dias alcança, e só
   apaga com `AUDITORIA_EXPURGO_APLICAR=true`. Apagar auditoria é irreversível,
   e essa é uma decisão de quem opera — não do código.
3. **Sem controle de acesso por papel.** É o motivo de a tela de administração
   ser de leitura (seção 8).
4. ~~**Duas travas sem fonte de dados.**~~ **Resolvido em 03/09/2026.**
   `modelo_fora_dos_limites` sai de `travas.fora_dos_limites` (domínio das
   saídas do motor) e `auditoria_falhou` de `trilha.sonda`. O motor também
   ganhou porta de entrada: `views/inteligencia_mercado.py` avalia as seis e
   `design.inteligencia.barra_travas` as publica. Continua valendo o que falta:
   enquanto o motor de crise não for ligado à tela, o `veredito` é `None` e a
   trava do modelo confere só o índice de antifragilidade — e diz isso.
5. ~~**Três tipos de evento faltam na taxonomia**~~ — **resolvido em
   03/09/2026.** `pandemia`, `quebra_bancaria` e `evento_climatico` entraram na
   taxonomia (v1.1.0), no classificador por palavra-chave e em
   `catalogo.SEM_FONTE`. Três consequências ficam registradas porque nenhuma
   delas é obviamente boa: (a) a cobertura da calibração **caiu** de 3/25 para
   3/28 — o denominador cresceu e o numerador não; (b) a calibração publicada
   continua carimbada com `taxonomia_versao` 1.0.0 e **não** foi refeita, então
   os três carregam prior declarado e nunca medido; (c) `evento_climatico`
   ficou deliberadamente **fora** de `TIPOS_EMERGENCIAIS` — enchente e seca são
   frequentes e locais, e um gatilho de cadência por manchete climática seria
   pago em falso alarme, que é justamente o critério de Fase 4 com limiar zero.
   `quebra_bancaria` é setorial e distinta de `crise_sistemica`: quebrar não é
   contagiar, e quem decide se escalou é o motor de mercado — não o título da
   notícia.
6. **Alertas externos e recomendação emergencial nunca foram exercitados em
   produção** — estão barrados pela Fase 4 e não há medição de campo.
