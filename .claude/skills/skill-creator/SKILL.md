# skill-creator

> Padrão operacional para criar e manter skills locais deste projeto.

## Objetivo

Garantir que toda skill em `.claude/skills/` siga o mesmo formato — independente
de o Claude Code estar interpretando o arquivo automaticamente ou apenas como
documentação operacional consultada pelo agente.

## Quando usar

- Sempre que criar uma nova skill nesta árvore.
- Antes de editar uma skill existente para impacto cruzado.
- Antes de aprovar uma alteração que toque comportamento "transversal" (UI,
  segurança, pipeline, schema).

## Limites

- Skill é **documentação operacional**, não código executável.
- Skill não substitui revisão de código: é checklist + contexto.
- Skill não impõe regra que conflite com `CLAUDE.md` / `AGENTS.md` na raiz.
- Skill nunca grava credenciais, segredos ou caminhos absolutos do usuário.

## Estrutura mínima

Cada skill mora em `.claude/skills/<nome-kebab>/SKILL.md` e segue este esqueleto:

```markdown
# <nome>

> Frase de 1 linha descrevendo o domínio.

## Objetivo
## Quando usar
## Limites
## Checklist de implementação
## Critérios de aceite
## Cuidados para não quebrar o app4
## Documentação de testes
```

Cabeçalhos podem ser reordenados se a skill demandar, mas todos devem existir.

## Checklist de implementação (skill-creator)

- [ ] Nome em kebab-case, curto, descritivo.
- [ ] Frase-resumo na primeira linha do arquivo.
- [ ] Seções obrigatórias presentes.
- [ ] Sem informação sensível.
- [ ] Sem caminho absoluto de máquina do usuário.
- [ ] Referência a arquivos sempre relativa à raiz do repositório.
- [ ] Skill testada mentalmente: "se eu apagasse o restante do projeto, este
  texto sozinho daria o contexto suficiente para retomar o trabalho?"

## Critérios de aceite

- A skill responde sem ambiguidade às perguntas:
  - *quando ela se aplica?*
  - *o que ela pede para eu verificar antes de codar?*
  - *o que ela proíbe explicitamente?*
- Outra IA / outro humano consegue ler a skill e replicar o padrão sem
  contexto extra.

## Cuidados para não quebrar o app4

- Skill não muda comportamento em runtime; só o agente muda. Mesmo assim:
  - Não introduzir convenção que conflite com `CLAUDE.md` ou com o pipeline
    automático em `.github/workflows/`.
  - Não pedir refatoração ampla "de passagem" — escopo da skill é
    delimitado.

## Documentação de testes

- Skills não têm testes automatizados.
- Validação manual: aplicar a skill a uma issue/tarefa real e confirmar que o
  resultado segue o checklist.
- Se a skill induzir erro repetido, revisar o texto da skill antes de revisar
  o código.
