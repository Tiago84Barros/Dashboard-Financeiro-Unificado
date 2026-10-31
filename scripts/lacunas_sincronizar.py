"""
scripts/lacunas_sincronizar.py
Funde o log local de lacunas com ``app_lacunas`` e gera a fila do corretor.

Uso:
    python scripts/lacunas_sincronizar.py              # sincroniza
    python scripts/lacunas_sincronizar.py marcar <impressao|prefixo> \\
        --status legitima|incerta|em_pr|aberta|resolvida [--pr-url URL] [--nota TEXTO]

Arquivos (todos em ``local_staging/lacunas/``, fora do git):
    eventos.jsonl          -- o que o app grava localmente (core/lacunas/destino.py)
    eventos-AAAA-MM.jsonl.gz -- meses anteriores, rotacionados aqui
    estado.json            -- status decidido pelo corretor + fotos do contador da nuvem
    abertas.json           -- a fila, regerada a cada execucao
    resumo-semanal.md      -- para revisao humana; regerado a cada 7 dias

Fonte que falha e nomeada em ``abertas.json`` (``fontes``), nunca some: sem
Supabase a fila sai so com o local e diz isso.

A logica e pura e mora em ``core/lacunas/fila.py``; aqui so ha I/O.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.lacunas import fila  # noqa: E402
from core.lacunas.leitura import (  # noqa: E402
    eventos_locais,
    gravar_cloud,
    ler_cloud,
)
from core.lacunas.leitura import gravar_json as _gravar_json  # noqa: E402
from core.lacunas.leitura import ler_json as _ler_json  # noqa: E402
from core.lacunas.leitura import ler_jsonl as _ler_jsonl  # noqa: E402

_log = logging.getLogger("lacunas_sincronizar")

PASTA = RAIZ / "local_staging" / "lacunas"
DIAS_RESUMO = 7



# ── arquivos ──────────────────────────────────────────────────────────────────
def rotacionar(pasta: Path, agora: datetime) -> list[str]:
    """Move eventos de meses anteriores do ``eventos.jsonl`` para o ``.gz`` do
    mes. O ``.gz`` e gravado ANTES de o jsonl ser reescrito: se cair no meio,
    o pior caso e o evento repetido, nunca perdido."""
    arquivo = pasta / "eventos.jsonl"
    eventos = _ler_jsonl(arquivo)
    grupos = fila.meses_para_rotacionar(eventos, agora)
    if not grupos:
        return []
    for mes, lista in grupos.items():
        with gzip.open(pasta / f"eventos-{mes}.jsonl.gz", "at", encoding="utf-8") as fh:
            for ev in lista:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    movidos = {id(ev) for lista in grupos.values() for ev in lista}
    restantes = [ev for ev in eventos if id(ev) not in movidos]
    tmp = arquivo.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in restantes),
                   encoding="utf-8")
    os.replace(tmp, arquivo)
    return sorted(grupos)



# ── nuvem e gh ────────────────────────────────────────────────────────────────
def _engine():
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    from core.database import get_engine

    return get_engine()


def consultar_pr(url: str) -> str | None:
    try:
        saida = subprocess.run(["gh", "pr", "view", url, "--json", "state", "-q", ".state"],
                               capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("gh pr view %s falhou: %s", url, exc)
        return None
    return saida.stdout.strip() or None


# ── comandos ──────────────────────────────────────────────────────────────────
def sincronizar(pasta: Path = PASTA, *, agora: datetime | None = None,
                engine_factory=_engine, consultar=consultar_pr) -> dict:
    agora = agora or datetime.now(timezone.utc)
    pasta.mkdir(parents=True, exist_ok=True)
    fontes: dict[str, str] = {}

    rotacionados = rotacionar(pasta, agora)
    local = fila.agregar_eventos(eventos_locais(pasta), agora)
    fontes["local"] = f"ok ({len(local)} lacunas)"

    arquivo_estado = pasta / "estado.json"
    guardado = _ler_json(arquivo_estado, {})
    estado = guardado.get("lacunas", {})
    fotos = guardado.get("fotos_cloud", {})

    engine, cloud = None, []
    try:
        engine = engine_factory()
        if engine is None:
            fontes["cloud"] = "indisponivel: DATABASE_URL ausente"
        else:
            cloud = ler_cloud(engine)
            fontes["cloud"] = f"ok ({len(cloud)} lacunas)"
    except Exception as exc:  # noqa: BLE001 - sem nuvem a fila sai so com o local
        engine = None
        fontes["cloud"] = f"indisponivel: {type(exc).__name__}"
        _log.warning("app_lacunas nao lida", exc_info=True)

    fila.importar_decisoes_admin(estado, cloud, agora)
    prs = fila.aplicar_prs(estado, consultar, agora)
    fotos = fila.registrar_foto(fotos, cloud, agora)
    itens = fila.fundir(local, cloud, estado, fotos, agora)
    estado = fila.atualizar_estado(estado, itens)

    if engine is not None:
        diferencas = fila.diferencas_para_cloud(itens, cloud)
        try:
            gravar_cloud(engine, diferencas)
            fontes["cloud_escrita"] = f"ok ({len(diferencas)} atualizadas)"
        except Exception as exc:  # noqa: BLE001
            fontes["cloud_escrita"] = f"falhou: {type(exc).__name__}"
            _log.warning("status nao devolvido a app_lacunas", exc_info=True)

    _gravar_json(arquivo_estado, {"lacunas": estado, "fotos_cloud": fotos})
    na_fila = fila.fila(itens)
    contagem = {s: sum(1 for i in itens if i["status"] == s) for s in fila.STATUS}
    _gravar_json(pasta / "abertas.json", {
        "gerado_em": agora.isoformat(),
        "fontes": fontes,
        "por_status": contagem,
        "prs_atualizados": prs,
        "fila": na_fila,
    })

    resumo = pasta / "resumo-semanal.md"
    antigo = (not resumo.exists()) or (
        agora - datetime.fromtimestamp(resumo.stat().st_mtime, timezone.utc)
        >= timedelta(days=DIAS_RESUMO))
    # Segunda-feira, ou 7 dias sem resumo: com o PC desligado na segunda o
    # resumo sai no primeiro dia em que a rotina rodar.
    if agora.weekday() == 0 or antigo:
        resumo.write_text(fila.resumo_semanal(itens, agora), encoding="utf-8")

    return {"fontes": fontes, "na_fila": len(na_fila), "por_status": contagem,
            "prs_atualizados": prs, "rotacionados": rotacionados}


def cmd_marcar(args, pasta: Path = PASTA, agora: datetime | None = None) -> str:
    agora = agora or datetime.now(timezone.utc)
    arquivo_estado = pasta / "estado.json"
    guardado = _ler_json(arquivo_estado, {})
    estado = guardado.setdefault("lacunas", {})
    conhecidas = set(estado)
    abertas = _ler_json(pasta / "abertas.json", {})
    conhecidas |= {i["impressao"] for i in abertas.get("fila", [])}
    imp = fila.resolver_prefixo(args.impressao, conhecidas)
    fila.marcar(estado, imp, args.status, pr_url=args.pr_url, nota=args.nota, agora=agora)
    _gravar_json(arquivo_estado, guardado)
    return imp


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="comando")
    m = sub.add_parser("marcar", help="muda o status de uma lacuna no estado local")
    m.add_argument("impressao")
    m.add_argument("--status", required=True, choices=fila.STATUS)
    m.add_argument("--pr-url")
    m.add_argument("--nota")
    args = parser.parse_args(argv)

    if args.comando == "marcar":
        try:
            imp = cmd_marcar(args)
        except ValueError as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 2
        print(f"{imp[:8]} -> {args.status} (vale na proxima sincronizacao)")
        return 0

    r = sincronizar()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
