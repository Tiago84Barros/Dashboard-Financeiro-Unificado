"""Orquestrador único das atualizações: roda o que está vencido e avisa.

Substitui o conjunto de rotinas soltas (uma tarefa agendada só para FIIs, um
workflow remoto para o resto) por um ponto único que decide pela cadência de
`core.publicacao_agenda`, publica na ordem, confere pelo leitor da tela e avisa
no Telegram.

Três decisões que valem explicar:

**A cadência é contra a última publicação bem-sucedida, não contra o relógio.**
É o que faz a recuperação por inicialização funcionar sem código extra: rodar
isto ao ligar o computador publica exatamente o que venceu enquanto ele esteve
desligado, seja uma hora ou uma semana. Um agendador puramente horário perderia
o dia e no dia seguinte publicaria como se nada tivesse acontecido.

**O estado é gravado depois de CADA alvo, não no fim.** A rotina pode levar
hora e meia; se a máquina hibernar no meio, o que já publicou tem de continuar
publicado do ponto de vista do agendador. Gravar só no fim faria a execução
seguinte repetir tudo -- e, pior, um travamento recorrente no quinto alvo
deixaria os quatro primeiros sendo republicados para sempre sem nunca chegar ao
quinto.

**Falhar avisa; ficar em dia não avisa.** Em 31/08/2026 duas automações estavam
quebradas há dias sem nunca terem reclamado -- a tarefa de backfill saindo com
código 1 a cada logon e o `market-refresh.yml` acumulando dez execuções em erro.
O oposto também é defeito: avisar todo dia que está tudo bem treina a pessoa a
ignorar a notificação, e aí o aviso de falha some no meio.

Uso:
    python scripts/atualizar_vitrines.py                # publica o que venceu
    python scripts/atualizar_vitrines.py --listar       # só mostra o que deve
    python scripts/atualizar_vitrines.py --apenas fii_selection --forcar
    python scripts/atualizar_vitrines.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.publicacao_agenda import (  # noqa: E402
    ALVOS,
    POR_CHAVE,
    alvos_devidos,
    registrar_resultado,
)

ESTADO = ROOT / "local_staging" / "estado_publicacao.json"
LOG_DIR = ROOT / "local_staging" / "logs"
LOG = LOG_DIR / "atualizacao_vitrines.log"
CONTAINER = "dfu_warehouse"
# O motor do Docker, não o container. São camadas diferentes e falham de jeitos
# diferentes: com o daemon fora do ar, `docker start` nem chega no container.
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
# Um alvo travado não pode segurar a fila para sempre: sem teto, um passo que
# nunca retorna deixa todos os alvos seguintes vencidos e silenciosos.
TIMEOUT_PASSO = 90 * 60


def registrar(mensagem: str) -> None:
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {mensagem}"
    print(linha, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(linha + "\n")


def _python() -> str:
    """O interpretador desta rotina, não o do PATH.

    O `python` do PATH nesta máquina resolve para a venv do Hermes, que não tem
    sqlalchemy. Foi assim que `run_fii_document_backfill.ps1` passou a sair com
    código 1 a cada logon sem ninguém perceber: o erro é `ModuleNotFoundError`,
    parece problema do projeto e é escolha de interpretador.
    """
    return sys.executable


def daemon_responde() -> bool:
    """O motor do Docker está atendendo?

    Perguntar pelo servidor, e não pelo cliente: `docker version` sozinho
    responde com o daemon morto (imprime a versão do cliente e sai 1). É o
    `{{.Server.Version}}` que exige resposta do outro lado do pipe.
    """
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def daemon_pronto(espera_max: int = 300) -> bool:
    """Garante o motor do Docker de pé, subindo o Docker Desktop se preciso.

    Daemon fora do ar não é "container parado", e tratar os dois como a mesma
    coisa custa a execução inteira: `docker start` devolve erro de conexão, a
    espera por saúde que vem depois gasta os 600s perguntando por um serviço que
    não existe, e o log culpa o container -- que está intacto.

    Isto existe por incidente. Em 01/09/2026, no primeiro logon depois de a
    rotina entrar em produção, o gatilho de inicialização disparou 3 minutos
    depois de entrar na sessão e o Docker Desktop ainda não tinha subido. O
    atraso do gatilho não resolve: ele é uma aposta sobre quanto o Docker demora
    para abrir, e a aposta erra no dia em que a máquina estiver lenta. Esperar
    pelo que se precisa é a versão que não tem palpite dentro.
    """
    if daemon_responde():
        return True
    if not DOCKER_DESKTOP.exists():
        registrar(f"ERRO: motor do Docker fora do ar e {DOCKER_DESKTOP} não existe.")
        return False

    registrar("Motor do Docker fora do ar; abrindo o Docker Desktop.")
    try:
        # Desacoplado de propósito: a rotina não pode ficar presa ao ciclo de
        # vida de uma janela, e o Docker Desktop só sai quando o usuário fecha.
        subprocess.Popen([str(DOCKER_DESKTOP)], close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError as exc:
        registrar(f"ERRO: não consegui abrir o Docker Desktop ({exc}).")
        return False

    limite = time.monotonic() + espera_max
    while time.monotonic() < limite:
        time.sleep(10)
        if daemon_responde():
            registrar("Motor do Docker respondeu.")
            return True
    registrar(f"ERRO: o motor do Docker não subiu em {espera_max}s.")
    return False


def armazem_pronto(espera_max: int = 600) -> bool:
    """Sobe o container se preciso e espera ficar saudável.

    Subir o armazém é autorização permanente do usuário: parado, ele trava as
    três publicações de uma vez.
    """
    if not daemon_pronto():
        return False

    def inspecionar(formato: str) -> str:
        try:
            proc = subprocess.run(
                ["docker", "inspect", "-f", formato, CONTAINER],
                capture_output=True, text=True, timeout=30, check=False)
            return (proc.stdout or "").strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    if inspecionar("{{.State.Running}}") != "true":
        registrar(f"Armazém parado; subindo {CONTAINER}.")
        try:
            subprocess.run(["docker", "start", CONTAINER],
                           capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            registrar(f"ERRO: não consegui subir o armazém ({exc}).")
            return False

    limite = time.monotonic() + espera_max
    saude = ""
    while time.monotonic() < limite:
        saude = inspecionar("{{.State.Health.Status}}")
        if saude == "healthy":
            return True
        time.sleep(10)
    registrar(f"ERRO: armazém não ficou saudável em {espera_max}s (status={saude!r}).")
    return False


def ler_estado() -> dict:
    if not ESTADO.exists():
        return {}
    try:
        dados = json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Estado ilegível é tratado como "nunca publicado": a saída conservadora
        # das duas é publicar. Herdar um arquivo corrompido como "em dia"
        # deixaria as vitrines vencerem em silêncio.
        registrar(f"ATENÇÃO: estado ilegível ({exc}); tratando como primeira execução.")
        return {}
    return dados if isinstance(dados, dict) else {}


def gravar_estado(estado: dict) -> None:
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    temporario = ESTADO.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(estado, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    temporario.replace(ESTADO)


# Onde cada alvo carimba a hora da própria publicação. Serve para semear o
# estado na primeira execução: sem isto, um arquivo de estado vazio faria a
# rotina republicar tudo -- inclusive as 346 mil linhas de `prices_monthly` --
# só porque nunca tinha rodado. O carimbo não é convenção única (`generated_at`,
# `updated_at`, `ingested_at`, `derived_at`, `recorded_at`), então está mapeado
# alvo a alvo em vez de adivinhado.
CARIMBO = {
    "fii_ingest": ("armazem", "SELECT max(updated_at) FROM market.fiis"),
    "fii_selection": ("supabase",
                      "SELECT max(generated_at) FROM market.fii_selection_inputs"),
    "b3_metrics": ("supabase", "SELECT max(updated_at) FROM market.calculated_metrics"),
    "b3_vintages": ("supabase",
                    "SELECT max(recorded_at) FROM market.calculated_metric_vintages"),
    "us_snapshot": ("supabase",
                    "SELECT max(generated_at) FROM market_us.company_snapshots"),
    "us_vintages": ("supabase", "SELECT max(created_at) FROM market_us.score_vintages"),
    "us_delistings": ("supabase", "SELECT max(derived_at) FROM market_us.delistings"),
    "us_prices": ("supabase", "SELECT max(ingested_at) FROM market_us.prices_monthly"),
}


def semear(estado: dict, versoes: dict) -> dict:
    """Estado inicial lido dos bancos, não presumido.

    Só preenche o que ainda não tem registro: um alvo já acompanhado pela rotina
    mantém o histórico dela, que é mais confiável que o carimbo da tabela (o
    carimbo diz quando a linha foi escrita, não se a publicação inteira deu
    certo).
    """
    from sqlalchemy import create_engine, text

    from core.database import get_engine
    from scripts.publish_fii_selection_from_local import _warehouse_url

    conexoes: dict[str, object] = {}
    novo = {k: dict(v) for k, v in estado.items()}
    try:
        for chave, (onde, consulta) in CARIMBO.items():
            if novo.get(chave, {}).get("ultima_publicacao"):
                continue
            if onde not in conexoes:
                motor = get_engine() if onde == "supabase" else create_engine(_warehouse_url())
                conexoes[onde] = motor.connect()
            try:
                quando = conexoes[onde].execute(text(consulta)).scalar()
            except Exception as exc:  # noqa: BLE001
                registrar(f"ATENÇÃO: sem carimbo para {chave} ({exc}); ficará como devido.")
                continue
            if quando is None:
                registrar(f"{chave}: tabela vazia; ficará como devido.")
                continue
            registro = novo.setdefault(chave, {})
            registro["ultima_publicacao"] = quando.isoformat()
            registro["ultimo_status"] = "ok"
            registro["semeado_do_banco"] = True
            if POR_CHAVE[chave].por_versao and versoes.get(chave):
                registro["versao"] = versoes[chave]
            registrar(f"{chave}: semeado com {quando:%Y-%m-%d %H:%M}.")
    finally:
        for conexao in conexoes.values():
            try:
                conexao.close()
            except Exception:  # noqa: BLE001
                pass
    return novo


def versao_corrente(alvo) -> str | None:
    """Lê a versão da metodologia declarada em ``Alvo.versao_de``."""
    if not alvo.versao_de:
        return None
    modulo, _, atributo = alvo.versao_de.partition(":")
    try:
        return str(getattr(__import__(modulo, fromlist=[atributo]), atributo))
    except Exception as exc:  # noqa: BLE001
        registrar(f"ATENÇÃO: não consegui ler {alvo.versao_de} ({exc}).")
        return None


def executar(alvo, ambiente: dict) -> tuple[bool, str]:
    """Roda os passos do alvo em ordem. Um passo que falha aborta os seguintes."""
    for indice, passo in enumerate(alvo.passos, start=1):
        rotulo = " ".join(passo)
        if len(alvo.passos) > 1:
            registrar(f"    passo {indice}/{len(alvo.passos)}: {rotulo}")
        try:
            proc = subprocess.run([_python(), *passo], cwd=str(ROOT), env=ambiente,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=TIMEOUT_PASSO, check=False)
        except subprocess.TimeoutExpired:
            return False, f"passo {indice} ({rotulo}) estourou {TIMEOUT_PASSO}s"
        except OSError as exc:
            return False, f"passo {indice} ({rotulo}) não executou: {exc}"
        if proc.returncode != 0:
            cauda = "\n".join(((proc.stderr or proc.stdout or "").strip()
                               ).splitlines()[-8:])
            return False, f"passo {indice} ({rotulo}) saiu com {proc.returncode}\n{cauda}"
        resumo = _resumo_json(proc.stdout)
        if resumo:
            registrar(f"    -> {resumo}")
    return True, ""


def _resumo_json(saida: str) -> str:
    """Última linha JSON da saída, que é onde os publicadores põem o resumo."""
    for linha in reversed((saida or "").splitlines()):
        linha = linha.strip()
        if linha.startswith("{") and linha.endswith("}"):
            try:
                dados = json.loads(linha)
            except ValueError:
                continue
            interessa = ("published_rows", "rows", "validation_status",
                         "publication_ready", "status", "records_updated")
            resumido = {k: v for k, v in dados.items() if k in interessa}
            return json.dumps(resumido or dados, ensure_ascii=False)[:300]
    return ""


def verificar(modulos: set[str], ambiente: dict) -> tuple[bool, str]:
    """Confere as vitrines pelo leitor da tela.

    Publicar sem verificar seria repetir o defeito que originou tudo isto: em
    31/08/2026 a vitrine de FIIs venceu e a tela creditou a falha aos filtros de
    elegibilidade, reprovando os 394 fundos por métrica ausente (PR #190).
    """
    if not modulos:
        return True, ""
    argumentos = []
    for modulo in sorted(modulos):
        argumentos += ["--modulo", modulo]
    proc = subprocess.run(
        [_python(), "scripts/verificar_frescor_vitrines.py", *argumentos],
        cwd=str(ROOT), env=ambiente, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800, check=False)
    saida = "\n".join(linha for linha in (proc.stdout or "").splitlines()
                      if linha.startswith(("OK", "REPROVOU")))
    return proc.returncode == 0, saida


def notificar(texto: str, assunto: str) -> None:
    try:
        from scripts.notificar import notificar as enviar
        enviar(texto, assunto)
    except Exception as exc:  # noqa: BLE001
        registrar(f"ATENÇÃO: aviso não enviado ({exc}).")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apenas", action="append", dest="apenas",
                   choices=sorted(POR_CHAVE), default=None)
    p.add_argument("--forcar", action="store_true",
                   help="Publica mesmo dentro da cadência.")
    p.add_argument("--listar", action="store_true",
                   help="Mostra o que está devendo e sai, sem publicar.")
    p.add_argument("--dry-run", action="store_true",
                   help="Decide e registra, mas não executa nem grava estado.")
    p.add_argument("--sem-armazem", action="store_true",
                   help="Não sobe nem espera o Docker (para diagnóstico).")
    p.add_argument("--semear", action="store_true",
                   help="Lê dos bancos a data da última publicação de cada alvo "
                        "sem registro e grava o estado inicial.")
    args = p.parse_args(argv)

    agora = datetime.now(timezone.utc)
    estado = ler_estado()
    apenas = tuple(args.apenas or ())
    versoes = {a.chave: versao_corrente(a) for a in ALVOS if a.por_versao}
    if args.semear:
        estado = semear(estado, versoes)
        gravar_estado(estado)
    devidos = alvos_devidos(estado, agora, versoes, args.forcar, apenas)

    if args.listar:
        for alvo in ALVOS:
            motivo = dict((a.chave, m) for a, m in devidos).get(alvo.chave)
            marca = "DEVE" if motivo else "  ok"
            print(f"{marca}  {alvo.chave:15s} {alvo.titulo:38s} {motivo or ''}")
        return 0

    if not devidos:
        registrar("Nada vencido; nenhuma publicação necessária.")
        return 0

    registrar("=== início: " + ", ".join(f"{a.chave} ({m})" for a, m in devidos))

    if args.dry_run:
        registrar("--dry-run: parando antes de executar.")
        return 0

    if not args.sem_armazem and any(a.precisa_armazem for a, _ in devidos):
        if not armazem_pronto():
            notificar("O armazém local (Docker dfu_warehouse) não subiu -- veja em "
                      f"{LOG} se foi o motor do Docker ou o container. "
                      "Nenhuma vitrine foi publicada.",
                      "Dashboard: atualização bloqueada")
            return 1

    ambiente = dict(os.environ)
    ambiente["PYTHONPATH"] = str(ROOT)
    ambiente["PYTHONIOENCODING"] = "utf-8"

    falhas: list[str] = []
    publicados: list[str] = []
    modulos: set[str] = set()

    for alvo, motivo in devidos:
        registrar(f"  {alvo.chave}: {alvo.titulo} -- {motivo}")
        ok, detalhe = executar(alvo, ambiente)
        # Gravado a cada alvo, não no fim: hibernar no meio não pode desfazer o
        # que já foi publicado.
        estado = registrar_resultado(estado, alvo.chave, ok, agora,
                                     versoes.get(alvo.chave))
        gravar_estado(estado)
        if ok:
            publicados.append(alvo.chave)
            modulos.add(alvo.modulo)
            registrar(f"  {alvo.chave}: OK")
        else:
            falhas.append(f"{alvo.chave}: {detalhe}")
            registrar(f"  {alvo.chave}: FALHOU -- {detalhe}")

    verificacao_ok, verificacao = verificar(modulos, ambiente)
    if verificacao:
        registrar("verificação:\n" + verificacao)
    if not verificacao_ok:
        falhas.append("verificação de frescor reprovou:\n" + verificacao)

    registrar("=== fim: "
              f"{len(publicados)} publicado(s), {len(falhas)} falha(s).")

    if falhas:
        notificar("Falhou:\n\n" + "\n\n".join(falhas)
                  + (f"\n\nPublicados: {', '.join(publicados)}" if publicados else "")
                  + f"\n\nLog: {LOG}",
                  "Dashboard: atualização com falha")
        return 1

    # Só avisa quando houve publicação. Aviso diário de "está tudo bem" treina a
    # ignorar a notificação, e aí o aviso de falha some junto.
    notificar("Publicado: " + ", ".join(publicados) + "\n\n" + verificacao,
              "Dashboard: vitrines atualizadas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
