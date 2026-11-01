"""
core/lacunas/nuvem.py
Ponte entre ``app_lacunas`` (Supabase) e o corretor que roda numa sessão do
Claude Code na nuvem.

Por que existe (01/11/2026): o corretor local (tarefa ``corrigir-lacuna-diaria``
no PC do dono) depende do PC ligado e ficou 26 dias parado. A sessão na nuvem
não alcança o Supabase -- o contêiner só sai por HTTPS e o Postgres não passa
por proxy --, mas o GitHub Actions alcança (``SUPABASE_UNIFICADO_URL``). Então:

  Actions  -> ``exportar``: lê a fila de ``app_lacunas`` e imprime no log um
              token **cifrado** (o repositório é público, e a fila cita ativos
              da carteira: texto claro no log seria vazamento).
  nuvem    -> ``ler``: baixa o log da última execução pela API pública do
              GitHub e decifra com a mesma chave (``LACUNAS_FILA_CHAVE``).
  nuvem    -> grava a decisão em ``docs/lacunas/decisoes_nuvem.jsonl`` num PR.
  Actions  -> ``aplicar``: leva as decisões de volta a ``app_lacunas``.

As decisões vão com a nota prefixada por ``fila.PREFIXO_NOTA_NUVEM``, que o
sincronizador local importa em vez de sobrescrever.

Tudo aqui é puro (sem rede, sem banco), para ser testado; o I/O mora em
``scripts/lacunas_nuvem.py``.
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta, timezone
from typing import Iterable

from core.lacunas import fila

REPO = "Tiago84Barros/Dashboard-Financeiro-Unificado"
WORKFLOW = "lacunas-nuvem.yml"
VARIAVEL_CHAVE = "LACUNAS_FILA_CHAVE"
MARCA_INICIO = "=== LACUNAS_FILA_CIFRADA ==="
MARCA_FIM = "=== FIM_LACUNAS_FILA_CIFRADA ==="
ARQUIVO_DECISOES = "docs/lacunas/decisoes_nuvem.jsonl"

#: Quantos itens da fila vão no token. A rotina trata 1 por execução; os
#: seguintes servem para pular o que já tem branch ``lacuna/`` aberto.
LIMITE_FILA = 15
LIMITE_MENSAGEM = 600

#: Decisões que a sessão na nuvem pode gravar. ``aberta`` fica de fora: reabrir
#: é papel do ``aplicar`` (reincidência) ou do administrador.
STATUS_DECISAO = ("resolvida", "legitima", "incerta", "em_pr")

#: Folga entre o merge e o deploy da Streamlit Cloud: ocorrência registrada
#: até aqui ainda pode ser do código antigo e não reabre a lacuna.
FOLGA_DEPLOY = timedelta(hours=6)

_CAMPOS_EXPORTADOS = (
    "impressao", "natureza", "tela", "fonte", "modulo", "codigo", "entidade",
    "status", "prioridade", "ocorrencias", "ocorrencias_14d", "primeira_vez",
    "ultima_vez", "reincidente", "pr_url", "nota_triagem", "contexto",
    "ultima_mensagem",
)


# ── fila exportada ────────────────────────────────────────────────────────────
def montar_fila(itens: list[dict], fontes: dict[str, str], agora: datetime,
                limite: int = LIMITE_FILA) -> dict:
    """O que vai cifrado no log: a fila do corretor, na ordem dele."""
    na_fila = fila.fila(itens)
    saida = []
    for item in na_fila[:limite]:
        linha = {c: item.get(c) for c in _CAMPOS_EXPORTADOS}
        linha["ultima_mensagem"] = (linha.get("ultima_mensagem") or "")[:LIMITE_MENSAGEM]
        saida.append(linha)
    return {
        "gerado_em": agora.isoformat(),
        "fontes": fontes,
        "total_na_fila": len(na_fila),
        "fila": saida,
    }


# ── cifra ─────────────────────────────────────────────────────────────────────
def _fernet(chave: str):
    from cryptography.fernet import Fernet

    if not chave:
        raise ValueError(f"{VARIAVEL_CHAVE} ausente")
    return Fernet(chave.strip().encode())


def cifrar(dado: dict, chave: str) -> str:
    bruto = zlib.compress(json.dumps(dado, ensure_ascii=False, default=str).encode(), 9)
    return _fernet(chave).encrypt(bruto).decode()


def decifrar(token: str, chave: str) -> dict:
    bruto = _fernet(chave).decrypt(token.strip().encode())
    return json.loads(zlib.decompress(bruto).decode())


def bloco_de_log(token: str) -> str:
    """Token entre marcas, numa linha só, para ``extrair_token`` achar."""
    return f"{MARCA_INICIO}\n{token}\n{MARCA_FIM}"


def extrair_token(log: str) -> str:
    """Acha o token no log do Actions, que prefixa cada linha com o horário.

    Pega o **último** bloco: o log de uma execução re-rodada traz os dois.
    """
    linhas = log.splitlines()
    token = None
    for n, linha in enumerate(linhas):
        if linha.rstrip().endswith(MARCA_INICIO) and n + 1 < len(linhas):
            candidato = linhas[n + 1].split()
            if candidato:
                token = candidato[-1]
    if not token:
        raise ValueError("bloco cifrado da fila não encontrado no log")
    return token


# ── decisões ──────────────────────────────────────────────────────────────────
def ler_decisoes(linhas: Iterable[str]) -> dict[str, dict]:
    """Uma decisão por impressão: a última linha válida vence."""
    decisoes: dict[str, dict] = {}
    for bruta in linhas:
        bruta = bruta.strip()
        if not bruta:
            continue
        try:
            d = json.loads(bruta)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("status") not in STATUS_DECISAO:
            continue
        imp = str(d.get("impressao") or "").strip()
        if len(imp) < 8 or not d.get("data"):
            continue
        decisoes[imp] = d
    return decisoes


def _dt(valor) -> datetime | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def url_do_pr(numero) -> str | None:
    try:
        return f"https://github.com/{REPO}/pull/{int(numero)}"
    except (TypeError, ValueError):
        return None


def nota_nuvem(data: str, texto: str | None) -> str:
    corpo = (texto or "").strip() or "sem observacao"
    return f"{fila.PREFIXO_NOTA_NUVEM}{str(data)[:10]}] {corpo}"[:500]


def estado_desejado(decisao: dict, linha: dict,
                    estado_pr: str | None = None) -> dict:
    """O que ``app_lacunas`` deve ter para esta decisão -- determinístico, para o
    ``aplicar`` poder rodar quantas vezes for sem desfazer nada.

    - ``em_pr`` com PR já MERGED vira ``resolvida``; CLOSED volta a ``aberta``.
    - ``resolvida`` que voltou a aparecer depois do merge (+ folga do deploy)
      reabre como ``aberta`` e reincidente -- a mesma regra do sincronizador.
    """
    status = decisao["status"]
    data = _dt(decisao.get("data"))
    pr_url = url_do_pr(decisao.get("pr")) or linha.get("pr_url")
    nota = nota_nuvem(decisao["data"], decisao.get("nota"))
    reincidente = bool(linha.get("reincidente"))

    if status == "em_pr" and estado_pr:
        situacao = estado_pr.upper()
        if situacao == "MERGED":
            status = "resolvida"
        elif situacao == "CLOSED":
            status = "aberta"
            nota = nota_nuvem(decisao["data"],
                              f"PR fechado sem merge ({pr_url}); não repetir a abordagem.")

    if status == "resolvida" and data is not None:
        ultima = _dt(linha.get("ultima_vez"))
        if ultima is not None and ultima > data + FOLGA_DEPLOY:
            status = "aberta"
            reincidente = True
            nota = nota_nuvem(decisao["data"],
                              f"reabriu: voltou a aparecer depois da correção ({pr_url}).")

    return {"impressao": linha["impressao"], "status": status, "pr_url": pr_url,
            "nota_triagem": nota, "reincidente": reincidente}


def diferencas(decisoes: dict[str, dict], cloud: Iterable[dict],
               consultar_pr=None) -> list[dict]:
    """UPDATEs para ``app_lacunas``: só onde o desejado difere do que está lá.

    Decisão de impressão que a nuvem não conhece é ignorada (o Supabase free não
    guarda lacuna que a Cloud não viu). Decisão do administrador **posterior**
    vence: nota ``[admin AAAA-MM-DD]`` com data maior que a da decisão.
    """
    saida = []
    for linha in cloud:
        dec = decisoes.get(linha["impressao"])
        if dec is None:
            continue
        nota_atual = linha.get("nota_triagem") or ""
        if nota_atual.startswith(fila.PREFIXO_NOTA_ADMIN):
            data_admin = nota_atual[len(fila.PREFIXO_NOTA_ADMIN):][:10]
            if data_admin > str(dec["data"])[:10]:
                continue
        estado_pr = None
        if dec["status"] == "em_pr" and consultar_pr and url_do_pr(dec.get("pr")):
            estado_pr = consultar_pr(url_do_pr(dec.get("pr")))
        novo = estado_desejado(dec, linha, estado_pr)
        if any(novo[c] != linha.get(c) for c in ("status", "pr_url", "nota_triagem")) \
                or novo["reincidente"] != bool(linha.get("reincidente")):
            saida.append(novo)
    return saida
