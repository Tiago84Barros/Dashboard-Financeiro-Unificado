"""Agenda das publicações de vitrine: quem está devendo atualização, e por quê.

Núcleo puro. A decisão de "o que publicar agora" não depende de relógio de
agendador, de rede nem de banco -- só do estado gravado e do instante passado.
É isso que torna o comportamento testável e que faz a recuperação por
inicialização funcionar de graça.

**A cadência é medida contra a última publicação BEM-SUCEDIDA, nunca contra um
horário.** Um agendador que dispara "todo dia às 19:30" perde o dia inteiro se a
máquina estiver desligada às 19:30, e no dia seguinte publica como se nada
tivesse acontecido -- a vitrine envelhece e o registro diz que está em dia. Aqui
o atraso é sempre visível: se a última publicação do alvo tem 3 dias e a
cadência é diária, ele está devendo, seja qual for a hora em que a rotina rodar.
Ligar o computador depois de uma semana fora publica o que venceu, na ordem.

Duas regras que existem por incidente, não por gosto:

**Falha não vira silêncio.** Um alvo cujo último desfecho foi erro está sempre
devendo, independentemente da cadência. Sem isso, uma falha em alvo mensal
espera um mês pela próxima tentativa. Em 31/08/2026 havia duas automações
falhando sem ninguém saber -- a tarefa local de backfill (exit 1 a cada logon,
desde que passou a chamar um Python sem as dependências) e o job de FIIs do
`market-refresh.yml` (10 execuções diárias seguidas em erro, consultando no
Supabase tabelas que a migração local-first deixou só no armazém).

**"Diário" é dia de calendário local, não 24 horas corridas.** A diferença
parece cosmética e não é: com um gatilho fixo às 19:30 e a régua em horas, uma
vitrine publicada às 20:03 tem 23h27 na hora do gatilho seguinte, é pulada, e só
sai no dia seguinte -- a rotina publica dia sim, dia não e o log não acusa nada,
porque pular estava certo pela regra. Pior no regime estável: publicando todo
dia às 19:30, a idade no gatilho seguinte é exatamente 24h, e ficar acima ou
abaixo do limite passa a depender do jitter do agendador, em segundos. Dia de
calendário elimina a borda -- publicou ontem, está devendo hoje.

O dia é o **local**, e não o UTC, porque o gatilho é local. Em UTC-3 a virada
do dia UTC cai às 21:00 locais: uma publicação que termine depois disso -- e a
cadeia de FIIs leva perto de uma hora, então começar às 19:30 e fechar às 21:10
não é hipótese remota -- cairia no mesmo dia UTC do gatilho da noite seguinte.
Uma régua em UTC pularia esse dia, que é o defeito de novo, só que mais raro e
por isso mais difícil de enxergar.

**Safra PIT não tem cadência de calendário.** `market_us.score_vintages` é
história point-in-time: republicar a mesma versão todo dia grava exatamente as
mesmas linhas. O gatilho dela é a versão da metodologia mudar. Manter isso como
cadência de dias seria pagar IO do Supabase todo dia para não mudar nada.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Alvo:
    """Uma superfície publicada e como ela é atualizada.

    ``passos`` é uma sequência de comandos, cada um sendo o que vai depois do
    interpretador Python. Sequência, e não comando único, porque a atualização
    de FIIs é uma cadeia de sete etapas que só faz sentido inteira: o snapshot
    publicado sai da última, mas a primeira é que traz o dado novo. Um passo que
    falha aborta os seguintes e reprova o alvo -- meia cadeia publicada é pior
    do que cadeia nenhuma, porque a vitrine sai internamente incoerente.

    Guardar os comandos aqui -- e não no script do agendador -- existe porque os
    publicadores divergem no padrão de escrita: ``publish_fii_selection_from_local``
    e ``publish_us_snapshot_from_local`` GRAVAM por omissão (``--dry-run`` é que
    é opcional), enquanto ``publish_us_score_vintages``, ``publish_us_prices_monthly``,
    ``publish_us_delistings`` e ``publish_b3_metrics_to_supabase`` SIMULAM por
    omissão e só gravam com ``--apply``. Chamar os do segundo grupo sem a flag
    não dá erro: imprime o resumo, sai com código 0 e não publica nada. A rotina
    marcaria sucesso e a vitrine envelheceria em silêncio.

    ``precisa_armazem`` diz se o alvo depende do Docker local. Vale para todos os
    de hoje, e é o motivo estrutural de nada disto poder ser um GitHub Action:
    as 18 tabelas de trabalho do pipeline de FIIs existem só no armazém.

    ``artefatos`` são os caminhos DO REPOSITÓRIO que a publicação reescreve.
    Ficam declarados aqui, e não descobertos por varredura de `data/public/`,
    para que um alvo nunca leve junto o artefato de outro. O único diretório
    declarado é `data/public/rag`, do corpus RAG, porque o número de partições
    varia com o dado (letra e ano) e partição removida também precisa ir para
    o commit. Quem os commita é `core.publicacao_git`.
    """

    chave: str
    titulo: str
    passos: tuple[tuple[str, ...], ...]
    cadencia_dias: int | None
    modulo: str
    versao_de: str | None = None
    precisa_armazem: bool = True
    artefatos: tuple[str, ...] = ()

    @property
    def por_versao(self) -> bool:
        return self.cadencia_dias is None


# A cadeia de FIIs roda contra o ARMAZÉM (`--warehouse`), não contra o Supabase.
# Não é preferência: das 22 tabelas `market.fii*`, 18 existem só no armazém --
# `fii_source_releases`, `fii_metric_observations`, `fii_parser_calibrations` e
# companhia. Foi tentar rodar isto remotamente que quebrou o `market-refresh.yml`
# em 10 execuções diárias seguidas, sempre no mesmo ponto: `apply_fii_schema`
# batendo em `relation "market.fii_source_releases" does not exist`.
_CADEIA_FII = (
    ("-m", "scripts.apply_fii_schema", "--warehouse"),
    ("run_market_ingest.py", "fiis", "--warehouse", "--json"),
    ("run_market_ingest.py", "fiis-cvm", "--warehouse", "--json"),
    ("run_market_ingest.py", "fiis-entities", "--warehouse", "--json"),
    ("run_market_ingest.py", "fiis-confidence", "--warehouse", "--json"),
    ("run_market_ingest.py", "fiis-series", "--warehouse", "--json"),
    ("run_market_ingest.py", "fiis-monitor", "--warehouse", "--json"),
)

# A fita diária da B3 (COTAHIST) no armazém: FIIs (BDI 12) em
# `fii_b3_security_history`, ações (BDI 02) em `b3_security_history`. Nada a
# agendava: em 29/09/2026 a de FIIs parava em 14/07 e a de ações em 01/09, e a
# liquidez, a Memória de Mercado e o detalhe da B3 no túnel liam essa idade.
# O primeiro passo baixa o ZIP (e o deixa no cache); o segundo relê o mesmo
# ZIP, sem rede. Dois anos para não perder dezembro na virada -- o ano
# fechado e já carregado não é baixado de novo.
_CADEIA_PREGAO = (
    ("run_market_ingest.py", "fiis-b3-history", "--warehouse", "--json",
     "--years", "2"),
    ("scripts/ingerir_precos_b3.py", "--apply", "--anos", "recentes",
     "--cache-apenas"),
)

# Documentos de companhias abertas (CVM/IPE) em `public.docs_corporativos`.
# Nada agendava a coleta: em 29/09/2026 o último documento era de 04/09, e as
# LLMs da B3 e as Informações Recentes liam essa idade. O primeiro passo traz
# os metadados novos do ano (resultado, fato relevante, provento); o segundo
# extrai o texto completo com teto de 100 documentos por dia -- o mesmo job do
# gotejamento, com disjuntor contra bloqueio da CVM. Uma cadeia só: metadado
# sem texto vira chunk de ruído no RAG.
_CADEIA_CVM_IPE = (
    ("scripts/backfill_cvm_ipe.py", "--years", "recentes", "--apply"),
    ("scripts/drenar_cvm_fulltext.py", "--ciclos", "5", "--por-ciclo", "20",
     "--delay", "1.5"),
)
ALVOS: tuple[Alvo, ...] = (
    Alvo(
        # Primeiro da fila: quem vem depois (FIIs, valuation) lê esta fita.
        chave="b3_pregao",
        titulo="Pregão diário da B3 no armazém (FIIs e ações)",
        passos=_CADEIA_PREGAO,
        cadencia_dias=1,
        modulo="b3",
    ),
    Alvo(
        # Documentos de FIIs (FNET) em `market.fii_documents`: saem do arquivo
        # EVENTUAL da CVM, lido pela carga estruturada. Ela não estava na
        # cadeia diária -- em 29/09/2026 o último documento era de 15/07, a
        # data da última carga manual. Download condicional (ETag) e ponto de
        # controle por hash: arquivo que não mudou não é relido. Dois anos
        # para não perder dezembro na virada. Antes de `fii_ingest`, que lê as
        # observações mensais que esta carga também grava.
        chave="fii_documentos",
        titulo="Documentos de FIIs da CVM (FNET) no armazém",
        passos=(("run_market_ingest.py", "fiis-cvm-structured", "--warehouse",
                 "--json", "--years", "2"),),
        cadencia_dias=1,
        modulo="fii",
    ),
    Alvo(
        chave="cvm_ipe",
        titulo="Documentos CVM/IPE das companhias no armazém",
        passos=_CADEIA_CVM_IPE,
        cadencia_dias=1,
        modulo="b3",
    ),
    Alvo(
        chave="fii_ingest",
        titulo="Ingestão de FIIs no armazém",
        passos=_CADEIA_FII,
        cadencia_dias=1,
        modulo="fii",
    ),
    Alvo(
        chave="fii_selection",
        titulo="Vitrine de FIIs (seleção)",
        passos=(("scripts/publish_fii_selection_from_local.py",),),
        cadencia_dias=1,
        modulo="fii",
        # O app publicado lê este arquivo do repositório quando o Supabase não
        # responde; ele vence por `as_of_date`, então republicar sem commitar
        # deixa a `main` com um fallback que vai expirar.
        artefatos=("data/public/fii_selection_snapshot_v2.json.gz",),
    ),
    Alvo(
        chave="b3_metrics",
        titulo="Métricas B3 (calculated_metrics)",
        passos=(("scripts/publish_b3_metrics_to_supabase.py", "--apply"),),
        cadencia_dias=7,
        modulo="b3",
    ),
    Alvo(
        chave="b3_vintages",
        titulo="Safras PIT da B3",
        passos=(("scripts/publish_b3_vintages_from_local.py",),),
        cadencia_dias=7,
        modulo="b3",
    ),
    Alvo(
        chave="us_snapshot",
        titulo="Vitrine dos EUA (company_snapshots)",
        # Cadeia, e não só o publicador: o preço diário dos EUA não era coletado
        # por rotina nenhuma. `prices_daily` parou em 15/09, o giro saiu dali
        # e, a partir de 26/09, o publicador recusou todo dia por giro com mais
        # de 7 dias (`LIQUIDITY_MAX_AGE_DAYS`) -- e sem giro fresco a Criação de
        # Portfólio dos EUA bloqueia. Mesmo precedente de `fii_ingest` e
        # `macro_insumos`: publicar sem coletar antes só renova a data do
        # arquivo sobre o dado velho.
        #
        # Cadência de 2 dias, e não 7: com teto de giro de 7 dias, publicar a
        # cada 7 vence no meio do ciclo por qualquer atraso. Dois dias absorvem
        # fim de semana e feriado. O custo no Supabase é baixo: upsert por
        # símbolo numa tabela de ~39 MB, espaço que o autovacuum reaproveita.
        passos=(
            ("run_us_ingest.py", "daily", "--warehouse", "--json"),
            ("run_us_ingest.py", "snapshot", "--warehouse", "--json"),
            ("scripts/publish_us_snapshot_from_local.py",),
        ),
        cadencia_dias=2,
        modulo="us",
    ),
    Alvo(
        chave="us_vintages",
        titulo="Safras PIT dos EUA",
        passos=(("-m", "scripts.publish_us_score_vintages", "--apply"),
                ("-m", "scripts.publish_us_score_panel", "--apply")),
        cadencia_dias=None,
        modulo="us",
        versao_de="core.us_methodology:US_FUNDAMENTAL_SCORE_VERSION",
    ),
    Alvo(
        chave="us_delistings",
        titulo="Saídas de bolsa dos EUA",
        passos=(("-m", "scripts.publish_us_delistings", "--apply"),
                ("-m", "scripts.publish_us_score_panel", "--apply")),
        cadencia_dias=30,
        modulo="us",
    ),
    Alvo(
        # Sem este alvo a vitrine ficou 18 dias parada (06/09 a 24/09/2026)
        # com a coleta local rodando a cada 30 min: o publicador existia e
        # ninguém o chamava. Em produção é o único noticiário que as LLMs
        # alcançam. Simula por omissão -- sem `--apply` sairia 0 sem gravar.
        chave="noticias_vitrine",
        titulo="Vitrine de notícias",
        passos=(("scripts/publish_noticias_vitrine.py", "--apply"),),
        cadencia_dias=1,
        modulo="noticias",
    ),
    Alvo(
        # Sentido inverso dos demais: lê o Supabase e grava no armazém os dados
        # de controle financeiro e carteira que só o app publicado escreve.
        # Simula por omissão.
        chave="espelho_supabase",
        titulo="Espelho do Supabase no armazém",
        passos=(("scripts/espelhar_supabase_local.py", "--apply"),),
        cadencia_dias=1,
        modulo="espelho",
    ),
    Alvo(
        # O ajuste macro de score e peso (B3, EUA, FIIs, Portfólio Global) só
        # existia no Docker: em produção as quatro telas diziam "indisponível".
        # E a coleta nem estava agendada -- parou em 09/09/2026 sem ninguém ver.
        # Uma cadeia só, e não dois alvos: publicar sem coletar antes renovaria
        # a data do arquivo sobre o cenário velho. O publicador também recusa
        # coleta com mais de 7 dias. A coleta doméstica LÊ o Supabase
        # (`public.macro`) e grava no Docker.
        chave="macro_insumos",
        titulo="Insumos macro das carteiras",
        passos=(
            ("run_macro_updates.py",),
            ("run_macro_domestic_sync.py",),
            ("scripts/publish_macro_insumos.py",),
        ),
        cadencia_dias=1,
        modulo="macro",
        artefatos=("data/public/macro_insumos.json.gz",),
    ),
    Alvo(
        # CDI diário da comparação "Rentabilidade vs CDI". O SGS do BCB não
        # responde fora do Brasil: em 03/10/2026 a Streamlit Cloud mostrava
        # "Sem série do CDI" e o `update_bcb` do GitHub Actions voltava com 0
        # pontos. A rotina local alcança o BCB; o app lê o arquivo e só pede ao
        # BCB os dias depois dele. Não usa o armazém.
        chave="cdi_diario",
        titulo="CDI diário (BCB/SGS 12)",
        passos=(("scripts/publicar_cdi_diario.py",),),
        cadencia_dias=1,
        modulo="macro",
        precisa_armazem=False,
        artefatos=("data/public/cdi_diario.json.gz",),
    ),
    Alvo(
        # Histórico de múltiplos (B3 anual, FII mensal, EUA anual) e a
        # volatilidade usada na escolha de pares da Inteligência dos Ativos.
        # Vem de tabelas pesadas que só existem no armazém (fita da B3,
        # prices_monthly), por isso é arquivo em data/public, não tabela no
        # Supabase. O dado é anual/mensal: semanal basta.
        chave="valuation_historico",
        titulo="Histórico de valuation da Inteligência dos Ativos",
        passos=(("scripts/publish_valuation_historico.py",),),
        cadencia_dias=7,
        modulo="b3",
        artefatos=("data/public/valuation_historico.json.gz",),
    ),
    Alvo(
        # Notícias filtradas por relevância, relatórios (documentos CVM/SEC) e
        # eventos datados por ativo, para a Inteligência dos Ativos. Lê o
        # acervo de notícias e os documentos que só existem no armazém; o
        # Supabase passou dos 500 MB, então sai como arquivo em data/public.
        # Grava por omissão (`--dry-run` só mede). Notícia envelhece em dias.
        chave="informacoes_recentes",
        titulo="Informações recentes dos ativos",
        passos=(("scripts/publish_informacoes_recentes.py",),),
        cadencia_dias=1,
        modulo="noticias",
        artefatos=("data/public/informacoes_recentes.json.gz",),
    ),
    Alvo(
        # Corpus RAG (chunks CVM/IPE) em Parquet, que o app lê por DuckDB. Só
        # o armazém tem os chunks, e desde o PR #393 a coleta e a extração de
        # texto rodam todo dia -- sem este alvo o Parquet parou em 08/09. É
        # semanal, e não diário, porque cada republicação vira histórico no
        # git; o publicador não reescreve nada se a origem não mudou, e a
        # partição por ano limita o que muda ao ano que recebeu documento.
        chave="rag_corpus",
        titulo="Corpus RAG dos documentos CVM (Parquet)",
        passos=(("scripts/publish_rag_corpus_parquet.py",),),
        cadencia_dias=7,
        modulo="b3",
        artefatos=("data/public/rag",),
    ),
    Alvo(
        # O cache bruto da brapi é o que mais cresce no Supabase: ~2,6 MB/dia de
        # cotações e ~48 MB aos sábados (anuais), sem nada que pode. Os dois
        # scripts existiam e ninguém os chamava -- o último arquivamento foi em
        # 06/09/2026, e o banco passou dos 500 MB do plano free em 26/09.
        # A compactação recusa apagar payload sem cópia local, então arquivar
        # antes é o que a destrava. Preserva o último por (endpoint, ticker), o
        # referenciado e as últimas 48 h. O VACUUM FULL continua manual.
        chave="brapi_raw_poda",
        titulo="Poda do cache bruto da brapi",
        passos=(
            ("scripts/archive_remote_brapi_raw.py",),
            ("scripts/compact_remote_brapi_raw.py", "--apply"),
        ),
        cadencia_dias=1,
        modulo="b3",
    ),
    Alvo(
        chave="us_prices",
        titulo="Preços mensais dos EUA",
        passos=(("-m", "scripts.publish_us_prices_monthly", "--apply"),
                ("-m", "scripts.publish_us_score_panel", "--apply")),
        cadencia_dias=30,
        modulo="us",
    ),
)

POR_CHAVE = {a.chave: a for a in ALVOS}


def _instante(valor) -> datetime | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor))
        except ValueError:
            # Data ilegível é indistinguível de nunca publicado, e a saída
            # segura das duas é a mesma: publicar. Silenciar aqui devolveria
            # "em dia" para um estado corrompido.
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def motivo_para_publicar(
    alvo: Alvo,
    registro: dict | None,
    agora: datetime,
    versao_corrente: str | None = None,
) -> str | None:
    """Devolve por que o alvo deve publicar agora, ou ``None`` se está em dia.

    O motivo é texto porque ele vai para o log e para a notificação: uma rotina
    que só diz "publiquei 4 alvos" não deixa auditar se publicou o que devia.
    """
    registro = registro or {}
    if registro.get("ultimo_status") not in (None, "ok"):
        return "última tentativa falhou"

    ultima = _instante(registro.get("ultima_publicacao"))
    if ultima is None:
        return "nunca publicado"

    if alvo.por_versao:
        if versao_corrente is None:
            return None
        anterior = registro.get("versao")
        if anterior != versao_corrente:
            return "versão mudou ({} para {})".format(
                anterior or "sem registro", versao_corrente
            )
        return None

    if agora - ultima < timedelta(0):
        # Registro no futuro: relógio mexido ou estado adulterado. Publicar é a
        # saída conservadora -- a alternativa é confiar num carimbo impossível.
        return "registro com data futura"

    dias = (agora.astimezone().date() - ultima.astimezone().date()).days
    if dias >= alvo.cadencia_dias:
        return "{}d de calendário desde a última (cadência {}d)".format(
            dias, alvo.cadencia_dias
        )
    return None


def alvos_devidos(
    estado: dict,
    agora: datetime,
    versoes: dict[str, str] | None = None,
    forcar: bool = False,
    apenas: tuple[str, ...] = (),
) -> list[tuple[Alvo, str]]:
    """Alvos a publicar agora, na ordem de ``ALVOS``, com o motivo de cada um."""
    versoes = versoes or {}
    selecao = [a for a in ALVOS if not apenas or a.chave in apenas]
    if forcar:
        return [(a, "forçado") for a in selecao]
    devidos = []
    for alvo in selecao:
        motivo = motivo_para_publicar(
            alvo, estado.get(alvo.chave), agora, versoes.get(alvo.chave)
        )
        if motivo:
            devidos.append((alvo, motivo))
    return devidos


def registrar_resultado(
    estado: dict,
    chave: str,
    ok: bool,
    agora: datetime,
    versao: str | None = None,
) -> dict:
    """Estado novo após uma tentativa. Não muta o recebido.

    ``ultima_publicacao`` só avança quando deu certo: se a falha carimbasse a
    data, o alvo sairia da lista de devedores sem ter publicado -- que é
    exatamente como uma vitrine vence sem ninguém notar.
    """
    novo = {k: dict(v) for k, v in estado.items()}
    registro = novo.setdefault(chave, {})
    registro["ultimo_status"] = "ok" if ok else "erro"
    registro["ultima_tentativa"] = agora.isoformat()
    if ok:
        registro["ultima_publicacao"] = agora.isoformat()
        if versao is not None:
            registro["versao"] = versao
    return novo
