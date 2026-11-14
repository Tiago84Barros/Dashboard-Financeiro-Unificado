# Supabase das contas novas

Desde 14/11/2026 qualquer pessoa cria a própria conta na aba **Criar conta** da
tela de login. Toda conta criada a partir daí mora num **segundo projeto
Supabase** (o banco `novos`). O principal passou dos 500 MB do plano free. As
contas que já existiam e os dados delas continuam no principal.

Enquanto `SUPABASE_NOVOS_URL` não estiver configurada, a aba Criar conta diz que
o cadastro ainda não abriu. Ela nunca grava no principal.

## Como o app escolhe o banco

- A identidade da sessão (`_app4_user`) carrega `banco`: `principal` ou `novos`.
- `core/database.py::get_engine()` devolve o engine do banco da pessoa logada.
  O resto do app não muda.
- O login (`core.user_accounts.authenticate`) procura primeiro no principal,
  depois no novos. Se o novos cair, quem mora no principal continua entrando.
- O cadastro (`cadastrar_usuario`) grava só no novos. Ele recusa um e-mail que
  já existe em qualquer um dos dois bancos.
- Dados que são do app, e não de uma pessoa, continuam gravados no principal,
  mesmo numa sessão de conta nova (exemplo: `core/lacunas/registro.py`). No
  novos essas tabelas são estrangeiras, e o `ON CONFLICT DO UPDATE` não passa
  pelo `postgres_fdw`.

## O que mora no banco novos

| Camada | Conteúdo |
|---|---|
| Local | `profiles`, toda tabela com `user_id` e as filhas delas, com as mesmas colunas, restrições e índices do principal |
| Remota (`postgres_fdw`) | todo o resto de `public`, `market` e `market_us`: cotações, fundamentos, macro, notícias e vitrines, lidos do principal sem cópia |
| Visões | as que leem tabelas de pessoa (`v_account_balance` e outras) são recriadas localmente |
| Linhas sem dono | as categorias do sistema (`user_id IS NULL`) são copiadas com o mesmo id |

RLS fica ligado nas tabelas locais, e nada é liberado para `anon` e
`authenticated`.

## Passo a passo (feito pelo administrador)

1. **Criar o projeto** no painel do Supabase, na mesma região do principal
   (`sa-east-1`). Guardar a senha do banco no gerenciador de senhas.
2. **Pegar a URL do session pooler** (porta 5432) do projeto novo, em
   *Connect › Session pooler*. O host direto do Supabase só responde em IPv6.
3. **Configurar a URL** em dois lugares, sem nunca commitar o valor:
   - Streamlit Cloud › Settings › Secrets: `SUPABASE_NOVOS_URL = "postgresql://..."`
   - `.env` local: `SUPABASE_NOVOS_URL=postgresql://...`
4. **Conferir o plano** sem gravar nada:
   ```bash
   python scripts/preparar_supabase_novos.py
   ```
   O SQL sai com a senha do principal trocada por `***`.
5. **Aplicar**:
   ```bash
   python scripts/preparar_supabase_novos.py --aplicar
   ```
6. **Testar**: criar uma conta de teste na tela de login, entrar, lançar um
   dado, conferir em Configurações › Segurança que a conta aparece com o banco
   "Supabase das contas novas", e excluí-la pelo bloco 05.

`--fdw-url` diz como o banco novos alcança o principal. O padrão é a própria
URL do principal, que deve ser a do session pooler.

## Depois de cada migration no principal

**Rode o script de novo** (`--aplicar`). As tabelas estrangeiras são uma foto
do catálogo, e tabela ou coluna nova do principal só aparece no novos depois
disso. Rodar de novo é seguro: a camada remota é refeita, tabelas e colunas
novas de pessoa são criadas, e nenhum dado de pessoa já gravado é apagado.

Uma migration que muda tipo, restrição ou índice de uma **tabela de pessoa**
tem de ser aplicada também no novos, à mão. O script cria o que falta, mas não
altera o que já existe.

## Excluir conta

Configurações › Segurança › bloco 05, só para o administrador.
`core.user_accounts.excluir_usuario(user_id, banco)` faz o seguinte:

- lê o catálogo do banco da conta e apaga, numa transação só, as linhas da
  pessoa em toda tabela que tem `user_id`;
- apaga antes as linhas filhas sem dono próprio e sem `ON DELETE CASCADE`
  (`import_logs`, por exemplo);
- apaga `profiles` por último.

A conta do administrador é recusada. A memória das conversas mora em
`user_settings` e sai junto.

## Limites conhecidos

- **Rotinas noturnas** (publicação de vitrines, medições) leem só o
  principal. Dado de conta nova não entra nelas.
- **Presença** (bloco 04, "Usuários logados agora") é registrada em memória por
  processo. Com mais de uma réplica do app, cada uma veria só as próprias
  sessões. A Streamlit Cloud roda uma réplica.
- **Plano free pausa** o projeto depois de dias sem uso. O login de contas
  antigas continua funcionando, e o de contas novas falha até o projeto ser
  reativado no painel.
- **Latência e egress**: toda leitura de mercado numa sessão de conta nova
  atravessa o `postgres_fdw` até o principal e conta no egress dos dois
  projetos.
- **Categorias do sistema** são copiadas na preparação. Uma categoria de
  sistema criada depois no principal só chega ao novos quando o script roda
  de novo.
- **Tabela criada pelo próprio app** (`CREATE TABLE IF NOT EXISTS` em tempo
  de execução) que ainda não existia no principal quando o script rodou nasce
  local no novos, numa sessão de conta nova. Depois, quando o principal também
  a cria, a do novos esconde a dele. Uma tabela que já era estrangeira não
  sofre isso, porque o `IF NOT EXISTS` a encontra. Prefira migration a criação
  preguiçosa, e rode o script depois dela.
