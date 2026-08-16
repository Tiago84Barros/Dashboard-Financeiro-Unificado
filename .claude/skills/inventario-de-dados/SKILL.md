---
name: inventario-de-dados
description: Use ANTES de planejar ou implementar qualquer coisa que envolva dado neste projeto — ingerir de API, criar tabela, mover dado entre bancos, resolver falta de espaço, ou dizer que uma informação "não existe". Levanta o que já está disponível nos dois bancos e na base de conhecimento antes de propor obter o que talvez já esteja lá.
---

# Inventário de dados antes de planejar

Este projeto tem dois bancos e uma base de conhecimento. Planejar sem consultar os
três leva a redescobrir o que já existe, ou a construir coletor para dado que já
está no disco.

## Por que esta skill existe

Em 10/08/2026, uma sessão gastou horas planejando ingerir preços mensais dos EUA
via yfinance, discutindo custo de armazenamento e propondo criar um segundo
Supabase. Os dados já estavam no warehouse local: `market_us.prices_monthly` com
609 mil linhas, incluindo os 12 ativos da carteira com histórico desde 1984.

A informação estava documentada no vault desde julho. A memória do projeto a
mencionava, mas escrita como "plano" quando já era realidade operante. Ninguém
verificou antes de afirmar.

O erro não foi falta de informação. Foi não ter olhado.

## Quando usar

Invoque antes de:

- propor ingerir dado de qualquer fonte externa
- criar tabela nova ou schema novo
- planejar mover dado entre bancos, ou dividir banco
- responder que um dado "não existe" ou "não está disponível"
- estimar custo de armazenamento
- dizer que algo "exige decisão de arquitetura"

Se a frase que você está prestes a escrever contém *"seria necessário ingerir"*,
*"não temos essa série"*, *"exigiria criar"* ou *"não é fechável por código"* —
pare e rode o inventário primeiro.

## O inventário

### 1. Warehouse local — onde o dado pesado mora

```bash
docker ps --filter name=dfu_warehouse --format "{{.Names}} | {{.Status}}"
```

Se estiver de pé, liste o conteúdo:

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -c "
from sqlalchemy import create_engine, text
from scripts.publish_fii_selection_from_local import _warehouse_url
with create_engine(_warehouse_url()).connect() as c:
    print('tamanho:', c.execute(text('SELECT pg_size_pretty(pg_database_size(current_database()))')).scalar())
    q = text('''SELECT n.nspname||'.'||c.relname t, pg_total_relation_size(c.oid) b, c.reltuples::bigint l
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relkind='r' AND n.nspname NOT IN ('pg_catalog','information_schema')
                ORDER BY b DESC LIMIT 15''')
    for r in c.execute(q): print('  %-45s %8.1f MB %12s' % (r[0], r.b/1e6, f'{r.l:,}'))
"
```

Este banco costuma ter séries históricas longas que o Supabase não tem. **O app
publicado não o alcança** — ele serve para gerar e publicar vitrines.

### 2. Supabase — o que a produção enxerga

Mesma consulta com `core.database.get_engine()`. Vale também o tamanho total,
porque o plano free tem teto de 500 MB e o projeto já o estourou uma vez.

### 3. Vault Obsidian — o histórico das decisões

Em `../ProjetoIA/`, acessível como diretório de trabalho adicional.

```bash
ls ../ProjetoIA/04_App_Dashboard_Financeiro_Unificado/
grep -rl -i "<seu tema>" ../ProjetoIA/04_App_Dashboard_Financeiro_Unificado/ ../ProjetoIA/05_Banco_de_Dados/
```

As notas `atualizacoes_AAAA-MM_*.md` registram o que foi feito e por quê. Se o
tema que você está atacando já apareceu antes, a nota diz como foi resolvido.

O `graphify-out/GRAPH_REPORT.md` na raiz do vault é o mapa do código — leia antes
de sair fazendo grep para entender relações entre módulos.

### 4. Scripts que já existem

Antes de escrever um coletor ou publicador, veja o que há:

```bash
ls scripts/ | grep -i "publish\|archive\|ingest\|backfill\|compact"
```

O padrão do projeto é simulação por omissão e `--apply` para gravar. Se já existe
script para o que você quer, use-o em vez de escrever outro.

## O que reportar

Depois do inventário, diga o que encontrou **antes** de propor qualquer coisa:

- o dado já existe? onde, quantas linhas, que período cobre
- há script que já faz o trabalho?
- o vault já registra uma decisão sobre isso?

Só então proponha. E se propuser obter dado de fora, diga explicitamente que
verificou o warehouse local e ele não tem.

## Armadilha conhecida

Memória e documentação envelhecem. Uma linha escrita como *"plano: mover dados
pesados para Postgres local"* pode descrever algo que já foi feito há meses.
**Verifique o estado real antes de repetir o que a nota diz** — o custo de rodar
`docker ps` é um segundo; o de assumir foi uma sessão inteira.
