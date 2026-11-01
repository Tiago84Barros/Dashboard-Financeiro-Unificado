"""
scripts/lacunas_nuvem.py
Fila de lacunas para o corretor que roda numa sessão do Claude Code na nuvem.

Uso:
    python scripts/lacunas_nuvem.py exportar   # Actions: fila cifrada no log
    python scripts/lacunas_nuvem.py aplicar    # Actions: decisões -> app_lacunas
    python scripts/lacunas_nuvem.py ler        # nuvem: baixa o log e decifra

``exportar`` e ``aplicar`` precisam do banco (``SUPABASE_UNIFICADO_URL``) e só
rodam no GitHub Actions (``.github/workflows/lacunas-nuvem.yml``). ``ler`` só
precisa da chave ``LACUNAS_FILA_CHAVE`` e de HTTPS para api.github.com: a API
de logs de repositório público responde sem token.

A lógica é pura e mora em ``core/lacunas/nuvem.py``; aqui só há I/O.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.lacunas import nuvem  # noqa: E402

_log = logging.getLogger("lacunas_nuvem")
_API = f"https://api.github.com/repos/{nuvem.REPO}"


def _chave() -> str:
    chave = os.environ.get(nuvem.VARIAVEL_CHAVE, "").strip()
    if not chave:
        raise SystemExit(
            f"ERRO: {nuvem.VARIAVEL_CHAVE} ausente. Gere com\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"\n'
            "e grave o MESMO valor como secret do GitHub Actions e como variável "
            "do ambiente de nuvem do Claude Code.")
    return chave


def _engine():
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise SystemExit("ERRO: banco não configurado (SUPABASE_UNIFICADO_URL ausente).")
    return engine


# ── Actions ───────────────────────────────────────────────────────────────────
def cmd_exportar() -> int:
    from core.lacunas import painel

    chave = _chave()
    agora = datetime.now(timezone.utc)
    # Pasta vazia: no runner não há log local; a fila sai só da nuvem, e diz isso.
    with tempfile.TemporaryDirectory() as vazia:
        dados = painel.carregar(pasta=Path(vazia) / "nada", agora=agora)
    exportada = nuvem.montar_fila(dados.itens, dados.fontes, agora)
    print(f"fontes: {json.dumps(dados.fontes, ensure_ascii=False)}")
    print(f"na fila: {exportada['total_na_fila']} (exportados {len(exportada['fila'])})")
    print(nuvem.bloco_de_log(nuvem.cifrar(exportada, chave)))
    return 0 if str(dados.fontes.get("cloud", "")).startswith("ok") else 1


def _estado_pr(url: str) -> str | None:
    try:
        saida = subprocess.run(["gh", "pr", "view", url, "--json", "state", "-q", ".state"],
                               capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("gh pr view %s falhou: %s", url, exc)
        return None
    return saida.stdout.strip() or None


def cmd_aplicar(arquivo: Path) -> int:
    from core.lacunas.leitura import gravar_cloud, ler_cloud

    if not arquivo.exists():
        print(f"sem decisões: {arquivo} não existe")
        return 0
    decisoes = nuvem.ler_decisoes(arquivo.read_text(encoding="utf-8").splitlines())
    if not decisoes:
        print("sem decisões válidas")
        return 0
    engine = _engine()
    cloud = ler_cloud(engine)
    mudancas = nuvem.diferencas(decisoes, cloud, consultar_pr=_estado_pr)
    gravar_cloud(engine, mudancas)
    for m in mudancas:
        print(f"{m['impressao'][:8]} -> {m['status']}"
              + (" (reincidente)" if m["reincidente"] else ""))
    print(f"decisões: {len(decisoes)} · atualizadas em app_lacunas: {len(mudancas)}")
    return 0


# ── nuvem ─────────────────────────────────────────────────────────────────────
def _get(url: str) -> bytes:
    pedido = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "lacunas-nuvem"})
    with urllib.request.urlopen(pedido, timeout=60) as resp:  # noqa: S310 - URL fixa
        return resp.read()


def baixar_log_mais_recente() -> tuple[str, dict]:
    """Log do job da última execução concluída do workflow da fila."""
    runs = json.loads(_get(f"{_API}/actions/workflows/{nuvem.WORKFLOW}/runs"
                           "?status=completed&per_page=10"))["workflow_runs"]
    for run in runs:
        jobs = json.loads(_get(f"{_API}/actions/runs/{run['id']}/jobs"))["jobs"]
        for job in jobs:
            log = _get(f"{_API}/actions/jobs/{job['id']}/logs").decode("utf-8", "replace")
            if nuvem.MARCA_INICIO in log:
                return log, {"run": run["html_url"], "conclusao": run["conclusion"],
                             "criado_em": run["created_at"]}
    raise SystemExit("ERRO: nenhuma execução recente de "
                     f"{nuvem.WORKFLOW} trouxe a fila cifrada.")


def cmd_ler(saida: Path | None) -> int:
    chave = _chave()
    log, origem = baixar_log_mais_recente()
    dados = nuvem.decifrar(nuvem.extrair_token(log), chave)
    dados["origem"] = origem
    texto = json.dumps(dados, ensure_ascii=False, indent=2)
    if saida:
        saida.write_text(texto, encoding="utf-8")
        print(f"fila gravada em {saida} ({len(dados['fila'])} itens; "
              f"execução {origem['criado_em']}, {origem['conclusao']})")
    else:
        print(texto)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("exportar", help="Actions: imprime a fila cifrada no log")
    a = sub.add_parser("aplicar", help="Actions: leva as decisões a app_lacunas")
    a.add_argument("--arquivo", type=Path, default=RAIZ / nuvem.ARQUIVO_DECISOES)
    ler = sub.add_parser("ler", help="nuvem: baixa o log e decifra a fila")
    ler.add_argument("--saida", type=Path)
    args = parser.parse_args(argv)

    if args.comando == "exportar":
        return cmd_exportar()
    if args.comando == "aplicar":
        return cmd_aplicar(args.arquivo)
    return cmd_ler(args.saida)


if __name__ == "__main__":
    raise SystemExit(main())
