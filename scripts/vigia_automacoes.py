"""Vigia das automações: avisa no Telegram o que parou sem reclamar.

Confere três coisas e avisa pelo `scripts.notificar` (Hermes -> Telegram):

1. workflow AGENDADO do GitHub Actions com 2+ execuções agendadas seguidas em
   falha (lista descoberta em `.github/workflows/*.yml` pelo gatilho
   ``schedule:``; consulta pelo `gh`);
2. publicação da rotina local (`scripts/atualizar_vitrines.py`) cuja última
   publicação bem-sucedida passou do limite do alvo -- lida do estado local
   `local_staging/estado_publicacao.json`, sem tocar no Supabase.
3. trimestre vigente das demonstrações da B3 no armazém local, medido por
   cobertura do universo, contra o calendário da CVM (B3-02).

A decisão é de `core.vigia_automacoes`; aqui só há I/O. A memória dos avisos
fica em `local_staging/vigia_automacoes.json` (estado de máquina, fora do git):
o mesmo problema só é reavisado se mudar ou depois de 24 h, e o que se resolve
avisa uma vez.

**Onde roda.** O Telegram só existe nesta máquina (Hermes), então o vigia não
é um GitHub Action. Ele é chamado no fim de `scripts/atualizar_vitrines.py`,
que a tarefa "DFU - Atualizar vitrines" dispara todo dia às 19:30 e no logon
(`scripts/registrar_tarefas.ps1`). Falha do vigia nunca derruba a publicação,
e falta de notificação nunca derruba nada.

Uso:
    python scripts/vigia_automacoes.py             # confere e avisa
    python scripts/vigia_automacoes.py --dry-run   # imprime o que avisaria
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.vigia_automacoes import (  # noqa: E402
    CHAVE_ATUALIDADE_B3,
    atualizar_cegueira,
    decidir,
    montar_mensagem,
    problema_atualidade_b3,
    problema_workflow,
    problemas_vitrine,
    tem_agendamento,
)

WORKFLOWS_DIR = ROOT / ".github" / "workflows"
ESTADO_PUBLICACAO = ROOT / "local_staging" / "estado_publicacao.json"
MEMORIA = ROOT / "local_staging" / "vigia_automacoes.json"
JANELA_RUNS = 10
_GH_WINDOWS = Path(r"C:\Program Files\GitHub CLI\gh.exe")


def workflows_agendados(diretorio: Path = WORKFLOWS_DIR) -> list[str]:
    """Nomes dos workflows com ``schedule:`` ativo, descobertos e não listados.

    Lista fixa envelheceria: um workflow agendado novo ficaria sem vigia.
    """
    nomes = []
    for arquivo in sorted(diretorio.glob("*.y*ml")):
        try:
            if tem_agendamento(arquivo.read_text(encoding="utf-8")):
                nomes.append(arquivo.name)
        except OSError:
            continue
    return nomes


def _gh() -> str | None:
    return shutil.which("gh") or (str(_GH_WINDOWS) if _GH_WINDOWS.exists() else None)


def runs_agendados(workflow: str, gh: str) -> list[dict] | None:
    """Últimas execuções agendadas do workflow, ou ``None`` se o `gh` falhou."""
    try:
        proc = subprocess.run(
            [gh, "run", "list", "--workflow", workflow, "--event", "schedule",
             "--json", "status,conclusion,createdAt,url,headBranch",
             "-L", str(JANELA_RUNS)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        dados = json.loads(proc.stdout or "[]")
    except ValueError:
        return None
    return dados if isinstance(dados, list) else None


def atualidade_b3_do_armazem(hoje) -> dict | None:
    """Avaliação do trimestre vigente no armazém local, ou ``None`` se não deu.

    Só o armazém: é dele que o alvo ``b3_metrics`` publica, e lê-lo não gasta
    egress do Supabase. Docker desligado não é problema a avisar aqui, porque a
    própria rotina de publicação já falha alto sem ele.
    """
    try:
        from sqlalchemy import create_engine

        from core.b3_atualidade_trimestral import (
            avaliar_universo,
            desde_ano,
            ler_contagens,
        )
        from scripts.publish_fii_selection_from_local import _warehouse_url
        eng = create_engine(_warehouse_url(), pool_pre_ping=True)
        try:
            with eng.connect() as conn:
                contagens = ler_contagens(conn, desde_ano(hoje))
        finally:
            eng.dispose()
        return avaliar_universo(contagens, hoje)
    except Exception:  # noqa: BLE001 - vigia não derruba a rotina
        return None


def _ler_json(caminho: Path) -> dict | None:
    """Conteúdo do JSON, ``{}`` se não existe, ``None`` se ilegível."""
    if not caminho.exists():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def _gravar_json(caminho: Path, dados: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(dados, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    temporario.replace(caminho)


def vigiar(dry_run: bool = False, estado_publicacao: Path = ESTADO_PUBLICACAO,
           memoria_path: Path = MEMORIA, agora: datetime | None = None,
           saida=print) -> int:
    """Uma rodada do vigia. Devolve 0 sempre que conseguiu decidir."""
    agora = agora or datetime.now(timezone.utc)
    memoria_bruta = _ler_json(memoria_path) or {}
    avisos_anteriores = memoria_bruta.get("avisos") or {}

    problemas = []
    verificados: set[str] = {"vigia:github"}

    # 1. Workflows agendados
    gh = _gh()
    algum_ok = False
    nomes = workflows_agendados()
    for nome in nomes:
        runs = runs_agendados(nome, gh) if gh else None
        if runs is None:
            saida(f"  {nome}: não consegui ler as execuções pelo gh")
            continue
        algum_ok = True
        verificados.add(f"workflow:{nome}")
        problema = problema_workflow(nome, runs)
        saida(f"  {nome}: {len(runs)} execuções agendadas lidas -- "
              + ("PROBLEMA" if problema else "ok"))
        if problema:
            problemas.append(problema)
    meta_github, cego = atualizar_cegueira(memoria_bruta.get("github"),
                                           algum_ok or not nomes, agora)
    if cego:
        problemas.append(cego)

    # 2. Publicações locais (estado da rotina; zero egress)
    estado = _ler_json(estado_publicacao)
    problemas_pub, verif_pub = problemas_vitrine(estado or {},
                                                 agora.astimezone().date())
    problemas.extend(problemas_pub)
    verificados |= verif_pub
    saida(f"  publicações: {len(verif_pub) - 1} alvo(s) medido(s) em "
          f"{estado_publicacao} -- {len(problemas_pub)} acima do limite")

    # 3. Trimestre vigente das demonstrações da B3 no armazém (zero egress)
    avaliacao = atualidade_b3_do_armazem(agora.astimezone().date())
    if avaliacao is None:
        saida("  demonstrações B3: armazém local indisponível, não medido")
    else:
        verificados.add(CHAVE_ATUALIDADE_B3)
        problema_b3 = problema_atualidade_b3(avaliacao)
        saida(f"  demonstrações B3: {avaliacao.get('texto')}")
        if problema_b3:
            problemas.append(problema_b3)

    decisao = decidir(problemas, avisos_anteriores, agora, verificados)
    mensagem = montar_mensagem(decisao, avisos_anteriores)
    silenciados = len(problemas) - len(decisao.avisar)
    if silenciados:
        saida(f"  {silenciados} problema(s) já avisado(s) há menos de 24 h, sem mudança")

    if dry_run:
        if mensagem:
            saida(f"\n[dry-run] avisaria -- assunto: {mensagem[0]}\n{mensagem[1]}")
        else:
            saida("\n[dry-run] nada a avisar")
        return 0

    enviado = True
    if mensagem:
        try:
            from scripts.notificar import notificar
            enviado = notificar(mensagem[1], mensagem[0])
        except Exception as exc:  # noqa: BLE001
            saida(f"  ATENÇÃO: aviso não enviado ({exc})")
            enviado = False
        saida(("  aviso enviado: " if enviado else "  aviso NÃO enviado: ")
              + mensagem[0])

    # Aviso que não saiu não conta como dado: a memória antiga fica, e a
    # próxima rodada tenta de novo. A cegueira do gh é medida, não aviso.
    nova = {"avisos": decisao.memoria if enviado else avisos_anteriores,
            "github": meta_github, "ultima_rodada": agora.isoformat()}
    try:
        _gravar_json(memoria_path, nova)
    except OSError as exc:
        saida(f"  ATENÇÃO: memória do vigia não gravada ({exc})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="Lê gh e estado, imprime o que avisaria; não envia nem grava.")
    p.add_argument("--estado", type=Path, default=ESTADO_PUBLICACAO,
                   help="Estado da rotina de publicação a ler (padrão: o deste checkout).")
    args = p.parse_args(argv)
    return vigiar(dry_run=args.dry_run, estado_publicacao=args.estado)


if __name__ == "__main__":
    sys.exit(main())
