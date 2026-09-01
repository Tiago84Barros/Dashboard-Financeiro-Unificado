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
    """

    chave: str
    titulo: str
    passos: tuple[tuple[str, ...], ...]
    cadencia_dias: int | None
    modulo: str
    versao_de: str | None = None
    precisa_armazem: bool = True

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

ALVOS: tuple[Alvo, ...] = (
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
        passos=(("scripts/publish_us_snapshot_from_local.py",),),
        cadencia_dias=7,
        modulo="us",
    ),
    Alvo(
        chave="us_vintages",
        titulo="Safras PIT dos EUA",
        passos=(("-m", "scripts.publish_us_score_vintages", "--apply"),),
        cadencia_dias=None,
        modulo="us",
        versao_de="core.us_methodology:US_FUNDAMENTAL_SCORE_VERSION",
    ),
    Alvo(
        chave="us_delistings",
        titulo="Saídas de bolsa dos EUA",
        passos=(("-m", "scripts.publish_us_delistings", "--apply"),),
        cadencia_dias=30,
        modulo="us",
    ),
    Alvo(
        chave="us_prices",
        titulo="Preços mensais dos EUA",
        passos=(("-m", "scripts.publish_us_prices_monthly", "--apply"),),
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
