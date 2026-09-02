"""
data_pipeline/jobs/update_noticias.py
=====================================
Coleta recorrente do Motor Conjuntural de notícias.

Este é o **único** processo que mantém as notícias frescas. A sessão do
Streamlit não coleta em segundo plano, não pode: o container do Streamlit Cloud
só executa código enquanto há requisição, e um laço de atualização dentro da
interface morreria junto com a aba fechada. Quem sustenta a cadência é o cron do
GitHub Actions chamando este job -- ver ``.github/workflows/noticias.yml``.

O que mudou em relação à primeira versão
----------------------------------------
O freio de cadência lia um JSON local. Em três processos que não compartilham
disco (runner do Actions, container do Streamlit, máquina do desenvolvedor) esse
arquivo nasce vazio toda vez, e o freio nunca freava: cada execução se via como
a primeira. O estado passou para o banco (``core.noticias.estado_coleta``), que
é o que os três alcançam.

Nasce **inativo** no registro (``is_active: False``), e continua nascendo: o job
gasta cota de APIs gratuitas e ligá-lo sem o usuário pedir gastaria a cota dele.
O workflow dedicado existe e está desligado no mesmo espírito.

Garantias
---------
* **Uma execução por vez** -- advisory lock. A segunda sai declarando ``skipped``
  com o motivo, e não em silêncio.
* **Falha não vira sucesso** -- ``ultimo_sucesso`` só avança quando algum
  provedor respondeu de fato. Falha preserva o último dado válido.
* **Falha parcial não é apresentada como completa** -- o ciclo sai ``degradado``
  e as limitações viajam escritas.
* **Todo ciclo é gravado**, inclusive o que morreu no meio. Silêncio de job é
  indistinguível de job que nunca rodou.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TABLE_NAME = "noticias_itens"
SOURCE_NAME = "Motor Conjuntural (provedores de noticias)"
JOB_NAME = "update_noticias"

ORIGEM_JOB = "job"
ORIGEM_MANUAL = "manual"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _resultado_base() -> dict:
    return {
        "status": "success",
        "table_name": TABLE_NAME,
        "source_name": SOURCE_NAME,
        "job_name": JOB_NAME,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "error_message": None,
    }


def run(tickers: tuple[str, ...] = (), *, forcar: bool = False,
        emergencia: bool = False, nivel: int | None = None,
        origem: str = ORIGEM_JOB, engine=None, agora: datetime | None = None
        ) -> dict:
    """Executa um ciclo de coleta.

    Args:
        tickers: universo explícito. Vazio deixa o modo resolver o universo.
        forcar: ignora o freio de cadência. Não ignora o rate limit dos
            provedores nem o lock -- essas duas travas protegem terceiros e a
            cota, e um botão não deveria poder desligá-las.
        emergencia: compatibilidade com a chamada antiga; equivale a nível 3.
        nivel: nível do motor de eventos extremos, quando o chamador o conhece.
        origem: ``job`` ou ``manual``, só para o histórico.
        engine: injetável para teste.
    """
    from core.config import settings
    from core.noticias import cadencia as cad
    from core.noticias import estado_coleta as ec
    from core.noticias import universo_coleta as uni
    from core.noticias.armazenamento import gravar
    from core.noticias.cache import Cache
    from core.noticias.coleta import coletar
    from core.noticias.frescor_noticias import RegistroColeta
    from core.noticias.provedores.base import Consulta
    from core.noticias.provedores.registro import construir
    from core.noticias.rate_limit import Orcamento

    result = _resultado_base()
    inicio = agora or _agora()

    estado = ec.ler(engine=engine)
    if nivel is None and emergencia:
        nivel = 3
    modo = cad.modo_para_nivel(nivel) if nivel is not None else estado.modo
    ritmo = cad.cadencia(modo, config=settings)

    pode, motivo_cadencia = cad.deve_coletar(
        estado.ultima_tentativa, ritmo, agora=inicio, forcar=forcar)
    if not pode:
        result["status"] = "skipped"
        result["error_message"] = motivo_cadencia
        return result

    ciclo = ec.Ciclo(modo=modo, origem=origem, forcado=bool(forcar),
                     iniciado_em=inicio)

    with ec.travar(engine) as obtido:
        if not obtido:
            # Não é erro: é a proteção funcionando. Gravar como falha encheria
            # o histórico de incidentes que nunca existiram.
            result["status"] = "skipped"
            result["error_message"] = (
                "outra execucao da coleta ja esta em andamento")
            return result
        return _executar(result, ciclo, ritmo, tickers, engine=engine,
                         settings=settings, cad=cad, ec=ec, uni=uni,
                         gravar=gravar, Cache=Cache, coletar=coletar,
                         RegistroColeta=RegistroColeta, Consulta=Consulta,
                         construir=construir, Orcamento=Orcamento)


def _executar(result, ciclo, ritmo, tickers, *, engine, settings, cad, ec, uni,
              gravar, Cache, coletar, RegistroColeta, Consulta, construir,
              Orcamento) -> dict:
    """O ciclo em si, já sob o lock. Sempre grava o ciclo antes de retornar."""
    erros: list[str] = []
    limitacoes: list[str] = []
    sucesso_em = None

    def _encerrar(status: str) -> dict:
        ciclo.status = status
        ciclo.concluido_em = _agora()
        ciclo.erros = tuple(erros)
        ciclo.limitacoes = tuple(limitacoes)
        ciclo.proximo_ciclo_em = cad.proximo_ciclo(
            ciclo.iniciado_em, ritmo, agora=ciclo.concluido_em)
        try:
            ec.registrar(ciclo, engine=engine, sucesso_em=sucesso_em)
        except Exception as exc:  # noqa: BLE001 - registro não pode derrubar
            logger.warning("Ciclo de coleta nao registrado: %s", exc)
        if erros and result["error_message"] is None:
            result["error_message"] = "; ".join(erros)
        return result

    if not tickers:
        tickers, lim_universo = uni.montar(ciclo.modo, engine=engine)
        limitacoes.extend(lim_universo)

    # Orçamento de requisições no banco quando há banco. O arquivo sob
    # ``local_staging/`` é disco da máquina, e o runner do Actions nasce com
    # disco limpo: o teto diário do provedor deixaria de existir justamente no
    # processo que mais chama.
    armazem = ec.ConsumoBanco(engine)
    if armazem.disponivel():
        orcamento = Orcamento(armazem=armazem)
    else:
        orcamento = Orcamento()
        limitacoes.append(
            "cota de requisições contada em arquivo local: sem banco, o teto "
            "diário do provedor não é compartilhado entre processos")

    try:
        provedores = construir(
            orcamento=orcamento,
            cache=Cache(ttl_s=settings.noticias_cache_ttl_s),
        )
    except Exception as exc:  # noqa: BLE001
        erros.append(f"falha ao montar provedores: {exc}")
        result["status"] = "failed"
        return _encerrar(cad.STATUS_INDISPONIVEL)

    if not provedores:
        erros.append("nenhum provedor de noticias disponivel "
                     "(verifique NOTICIAS_PROVEDORES e as chaves)")
        result["status"] = "failed"
        return _encerrar(cad.STATUS_INDISPONIVEL)

    consulta = Consulta(tickers=tuple(tickers)[:uni.LIMITE_TICKERS],
                        limite=settings.noticias_limite)
    # ``persistir=False``: o carimbo compartilhado passou a ser o do banco, e
    # manter uma segunda cópia em JSON local criaria duas verdades sobre a mesma
    # coleta -- com a cópia do runner sendo descartada ao fim da execução.
    registro = RegistroColeta(persistir=False)

    # Retentativa com espera crescente. Só a falha total é retentada: com um
    # provedor de pé, insistir gastaria cota dele para tentar salvar o outro.
    tentativas = max(1, int(settings.noticias_max_retentativas) + 1)
    resultado = None
    for tentativa in range(1, tentativas + 1):
        resultado = coletar(consulta, provedores, registro=registro)
        if not resultado.sem_fonte:
            break
        if tentativa < tentativas:
            espera = settings.noticias_backoff_s * (2 ** (tentativa - 1))
            limitacoes.append(
                f"tentativa {tentativa} sem fonte; nova tentativa em "
                f"{espera:.0f} s")
            time.sleep(espera)

    ciclo.coletadas = len(resultado.avaliadas)
    ciclo.duplicadas = resultado.duplicatas_removidas
    ciclo.eventos = len(resultado.eventos)
    ciclo.provedores_ok = tuple(resultado.provedores_ok)
    ciclo.provedores_falha = tuple(
        f.provedor for f in resultado.falhas
        if f.provedor not in resultado.provedores_ok)
    limitacoes.extend(resultado.limitacoes)
    erros.extend(f.texto() for f in resultado.falhas)
    result["records_failed"] = len(resultado.falhas)

    usou_cache_vencido = any(f.usou_cache_vencido for f in resultado.falhas)

    if resultado.sem_fonte:
        result["status"] = "failed"
        return _encerrar(cad.STATUS_INDISPONIVEL)

    # Chegou aqui: alguém respondeu. É o único ponto em que o carimbo de sucesso
    # pode avançar, e ele avança **antes** da gravação de propósito -- a coleta
    # aconteceu, e um Supabase fora do ar não desfaz esse fato.
    sucesso_em = resultado.coletado_em or _agora()

    # Contado ANTES de gravar. Depois do upsert todo id ja existe no acervo e
    # a resposta seria zero em qualquer cenario -- um numero estavel, plausivel
    # e sempre errado.
    novas = ec.contar_novas(
        [getattr(a.noticia, "id_dedup", "") for a in resultado.avaliadas],
        engine=engine)
    if novas is None:
        limitacoes.append("quantas noticias eram ineditas: nao apurado")
    else:
        ciclo.novas = novas

    try:
        gravacao = gravar(resultado)
    except Exception as exc:  # noqa: BLE001
        erros.append(f"falha ao gravar noticias: {exc}")
        result["status"] = "partial_success"
        return _encerrar(cad.STATUS_DEGRADADO)

    result["records_inserted"] = gravacao.get("itens", 0)
    result["records_updated"] = gravacao.get("avaliacoes", 0)

    if not gravacao.get("gravado"):
        # "partial_success" e nao "partial": o orquestrador so aceita
        # {success, partial_success, skipped, failed} e converteria qualquer
        # outra coisa em "failed" com "Status de job invalido" -- o motivo real
        # se perderia.
        result["status"] = "partial_success"
        erros.append(str(gravacao.get("motivo", "gravacao nao confirmada")))

    if resultado.falhas and result["status"] == "success":
        result["status"] = "partial_success"

    try:
        expurgo = ec.expurgar(engine=engine)
        if expurgo.get("expurgado"):
            limitacoes.append(
                f"retencao: {expurgo['itens']} noticias e {expurgo['ciclos']} "
                f"ciclos alem de {settings.noticias_retencao_dias} dias")
    except Exception as exc:  # noqa: BLE001
        logger.info("Expurgo de retencao nao executado (%s)", exc)

    status = cad.status(
        sucesso_em, ritmo, agora=_agora(),
        provedores_ok=len(resultado.provedores_ok),
        provedores_previstos=len(resultado.provedores_consultados),
        parcial=bool(resultado.falhas),
        usou_cache_vencido=usou_cache_vencido)
    return _encerrar(status)
