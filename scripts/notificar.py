"""Manda um aviso para o Telegram pelo Hermes, sem gastar LLM.

Por que isto existe antes do orquestrador: em 31/08/2026 havia duas automações
falhando e nenhuma das duas tinha reclamado. A tarefa local de backfill saía com
código 1 a cada logon; o job de FIIs do `market-refresh.yml` acumulava dez
execuções diárias em erro. As duas estavam registradas, agendadas e mudas.

Automação sem canal de falha não resolve "esqueci de rodar" -- troca por "não
sei que parou de rodar", que é pior porque parece resolvido. Publicar mais
rápido sem isto só produziria lixo mais rápido.

Regra de degradação: **falta de notificação nunca derruba a publicação.** Se o
Hermes não estiver instalado, ou o Telegram não estiver configurado, ou a rede
cair, isto escreve o aviso no stderr e sai com 0. O inverso -- abortar a
publicação porque o aviso não saiu -- transformaria o canal de alerta em mais um
ponto de falha do que ele deveria proteger.

Uso:
    python scripts/notificar.py --assunto "Vitrines" "3 alvos publicados"
    python scripts/notificar.py --arquivo local_staging/logs/atualizacao.log
    echo "texto" | python scripts/notificar.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Onde o instalador do Hermes põe o executável no Windows. Procurar aqui antes
# do PATH é deliberado: o `python` do PATH desta máquina resolve para a venv do
# Hermes (que não tem sqlalchemy), então PATH não é fonte confiável de caminho
# neste projeto.
CANDIDATOS = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe",
    Path.home() / ".local" / "bin" / "hermes",
)


def _executavel() -> str | None:
    for caminho in CANDIDATOS:
        if caminho and caminho.exists():
            return str(caminho)
    return shutil.which("hermes")


def notificar(texto: str, assunto: str | None = None,
              destino: str = "telegram", timeout: int = 30) -> bool:
    """Tenta enviar. Devolve se saiu, nunca levanta."""
    exe = _executavel()
    if not exe:
        print("[notificar] Hermes não encontrado; aviso não enviado:",
              file=sys.stderr)
        print(texto, file=sys.stderr)
        return False
    comando = [exe, "send", "--to", destino, "--quiet"]
    if assunto:
        comando += ["--subject", assunto]
    try:
        proc = subprocess.run(comando, input=texto, text=True, timeout=timeout,
                              capture_output=True, encoding="utf-8",
                              errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[notificar] falhou ({type(exc).__name__}: {exc}); aviso não enviado",
              file=sys.stderr)
        print(texto, file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"[notificar] hermes saiu com {proc.returncode}: "
              f"{(proc.stderr or '').strip()}", file=sys.stderr)
        print(texto, file=sys.stderr)
        return False
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mensagem", nargs="?", help="Texto. Sem isto, lê de --arquivo ou stdin.")
    p.add_argument("--assunto", default=None)
    p.add_argument("--arquivo", type=Path, default=None)
    p.add_argument("--destino", default="telegram")
    p.add_argument("--ultimas", type=int, default=0,
                   help="Com --arquivo, envia só as N últimas linhas.")
    args = p.parse_args(argv)

    if args.mensagem:
        texto = args.mensagem
    elif args.arquivo:
        texto = args.arquivo.read_text(encoding="utf-8", errors="replace")
        if args.ultimas > 0:
            texto = "\n".join(texto.splitlines()[-args.ultimas:])
    else:
        texto = sys.stdin.read()

    texto = texto.strip()
    if not texto:
        print("[notificar] nada a enviar", file=sys.stderr)
        return 0
    # O Telegram recusa mensagem acima de ~4096 caracteres. Cortar o começo e
    # manter o fim é intencional: num log de publicação, o desfecho está no fim.
    if len(texto) > 3800:
        texto = "(...)\n" + texto[-3800:]

    notificar(texto, args.assunto, args.destino)
    # Sempre 0: quem chama está publicando vitrine, não enviando mensagem.
    return 0


if __name__ == "__main__":
    sys.exit(main())
