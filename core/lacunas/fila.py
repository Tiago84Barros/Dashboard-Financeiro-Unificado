"""
core/lacunas/fila.py
Fila de correcao: funde o log local com ``app_lacunas`` e ordena por prioridade.

Funcoes puras -- nada aqui abre arquivo, banco ou processo. O I/O mora em
``scripts/lacunas_sincronizar.py``, que chama estas funcoes com o que leu.

Tres decisoes carregam o modulo:

* O STATUS mora no ``estado.json`` local, nao no ``abertas.json``. A fila e
  regerada a cada sincronizacao; o que o corretor decidiu (legitima, em_pr,
  nota de triagem) nao pode sumir com ela. O estado local vence o da nuvem,
  e a sincronizacao devolve a diferenca para ``app_lacunas``.

* A JANELA de 14 dias na nuvem sai de fotos diarias do contador, nao do
  contador total. ``app_lacunas`` guarda uma linha por impressao com o total de
  sempre; uma lacuna que apareceu 400 vezes em marco e uma vez ontem nao pode
  passar na frente de uma que aparece todo dia. Sem foto antiga o bastante, a
  contagem e o total e o item sai marcado ``janela_aproximada`` -- a
  aproximacao e nomeada, nao escondida.
      memoria: medida-de-limite-que-ignora-o-limite

* RESOLVIDA reabre sozinha: se a lacuna foi vista depois de ``resolvida_em``, a
  correcao nao pegou. Vale para as duas origens, local e nuvem.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

PESOS_FONTE = {"excecao": 3, "motor": 2, "tela": 2, "llm": 1}
FATOR_INCERTA = 0.5
JANELA_DIAS = 14
MAX_FOTOS = 40

STATUS = ("aberta", "legitima", "em_pr", "resolvida", "incerta")
#: O que entra na fila do corretor. ``incerta`` volta com prioridade reduzida.
STATUS_NA_FILA = ("aberta", "incerta")

_CAMPOS_ESTADO = ("status", "pr_url", "nota_triagem")


def _dt(valor) -> datetime | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def agregar_eventos(eventos: Iterable[dict], agora: datetime,
                    janela_dias: int = JANELA_DIAS) -> dict[str, dict]:
    """Um registro por impressao a partir das linhas do ``eventos.jsonl``.
    Linha sem impressao ou sem data valida e descartada."""
    corte = agora - timedelta(days=janela_dias)
    saida: dict[str, dict] = {}
    for ev in eventos:
        imp = ev.get("impressao")
        ts = _dt(ev.get("ts"))
        if not imp or ts is None:
            continue
        item = saida.get(imp)
        if item is None:
            item = saida[imp] = {
                "impressao": imp, "fonte": ev.get("fonte", ""),
                "modulo": ev.get("modulo", ""), "codigo": ev.get("codigo", ""),
                "entidade": ev.get("entidade", ""), "ultima_mensagem": "",
                "contexto": {}, "primeira_vez": ts, "ultima_vez": ts,
                "ocorrencias": 0, "ocorrencias_janela": 0,
            }
        item["ocorrencias"] += 1
        if ts >= corte:
            item["ocorrencias_janela"] += 1
        item["primeira_vez"] = min(item["primeira_vez"], ts)
        if ts >= item["ultima_vez"]:
            item["ultima_vez"] = ts
            item["ultima_mensagem"] = ev.get("mensagem", "")
            item["contexto"] = ev.get("contexto") or {}
    return saida


def registrar_foto(fotos: dict, linhas_cloud: Iterable[dict], agora: datetime) -> dict:
    """Acrescenta a foto de hoje do contador de cada impressao da nuvem.
    Uma foto por dia (a de hoje e substituida); guarda as ultimas ``MAX_FOTOS``."""
    hoje = agora.date().isoformat()
    novas = {imp: [list(f) for f in lista] for imp, lista in (fotos or {}).items()}
    for linha in linhas_cloud:
        lista = [f for f in novas.get(linha["impressao"], []) if f[0] != hoje]
        lista.append([hoje, int(linha.get("ocorrencias") or 0)])
        novas[linha["impressao"]] = sorted(lista)[-MAX_FOTOS:]
    return novas


def ocorrencias_na_janela(linha: dict, fotos: list, agora: datetime,
                          janela_dias: int = JANELA_DIAS) -> tuple[int, bool]:
    """(contagem, aproximada). Delta do contador desde a foto mais recente
    tirada ate o inicio da janela; sem ela, o total."""
    corte = agora - timedelta(days=janela_dias)
    total = int(linha.get("ocorrencias") or 0)
    ultima = _dt(linha.get("ultima_vez"))
    if ultima is None or ultima < corte:
        return 0, False
    antigas = [n for dia, n in (fotos or []) if dia <= corte.date().isoformat()]
    if antigas:
        return max(total - antigas[-1], 0), False
    primeira = _dt(linha.get("primeira_vez"))
    return total, not (primeira is not None and primeira >= corte)


def fundir(local: dict[str, dict], cloud: Iterable[dict], estado: dict[str, dict],
           fotos: dict, agora: datetime) -> list[dict]:
    """Uma lista de itens, um por impressao, com origem, status e prioridade."""
    cloud = {linha["impressao"]: linha for linha in cloud}
    itens = []
    for imp in sorted(set(local) | set(cloud)):
        loc, nuv = local.get(imp), cloud.get(imp)
        base = dict(loc or nuv)
        base.pop("ocorrencias_janela", None)
        base["origem"] = "ambos" if loc and nuv else ("local" if loc else "cloud")
        janela, aproximada = 0, False
        total = 0
        primeiras, ultimas = [], []
        if loc:
            janela += loc["ocorrencias_janela"]
            total += loc["ocorrencias"]
            primeiras.append(loc["primeira_vez"])
            ultimas.append(loc["ultima_vez"])
        if nuv:
            n, aproximada = ocorrencias_na_janela(nuv, fotos.get(imp, []), agora)
            janela += n
            total += int(nuv.get("ocorrencias") or 0)
            primeiras.append(_dt(nuv.get("primeira_vez")))
            ultimas.append(_dt(nuv.get("ultima_vez")))
            ult_nuv = _dt(nuv.get("ultima_vez"))
            if not loc or (ult_nuv and ult_nuv > loc["ultima_vez"]):
                base["ultima_mensagem"] = nuv.get("ultima_mensagem", "")
                base["contexto"] = nuv.get("contexto") or {}
        primeiras = [d for d in primeiras if d]
        ultimas = [d for d in ultimas if d]
        base["primeira_vez"] = _iso(min(primeiras)) if primeiras else None
        base["ultima_vez"] = _iso(max(ultimas)) if ultimas else None
        base["ocorrencias"] = total
        base["ocorrencias_14d"] = janela
        base["janela_aproximada"] = aproximada

        est = estado.get(imp, {})
        base["status"] = est.get("status") or (nuv or {}).get("status") or "aberta"
        base["pr_url"] = est.get("pr_url") or (nuv or {}).get("pr_url")
        base["nota_triagem"] = est.get("nota_triagem") or (nuv or {}).get("nota_triagem")
        base["reincidente"] = bool(est.get("reincidente") or (nuv or {}).get("reincidente"))
        base["tentativas"] = list(est.get("tentativas") or [])
        resolvida_em = _dt(est.get("resolvida_em"))
        if (base["status"] == "resolvida" and resolvida_em and ultimas
                and max(ultimas) > resolvida_em):
            base["status"] = "aberta"
            base["reincidente"] = True
        base["prioridade"] = prioridade(base)
        itens.append(base)
    return itens


def prioridade(item: dict) -> float:
    fator = FATOR_INCERTA if item.get("status") == "incerta" else 1.0
    return item.get("ocorrencias_14d", 0) * PESOS_FONTE.get(item.get("fonte"), 1) * fator


def fila(itens: list[dict]) -> list[dict]:
    """So o que o corretor pode pegar, da maior prioridade para a menor.
    Empate: a vista mais recentemente primeiro; depois a impressao, so para a
    ordem ser deterministica."""
    candidatos = [i for i in itens if i["status"] in STATUS_NA_FILA and i["prioridade"] > 0]
    candidatos.sort(key=lambda i: i["impressao"])
    candidatos.sort(key=lambda i: i["ultima_vez"] or "", reverse=True)
    candidatos.sort(key=lambda i: i["prioridade"], reverse=True)
    return candidatos


def atualizar_estado(estado: dict[str, dict], itens: list[dict]) -> dict[str, dict]:
    """Grava no estado local o que a fusao mudou (reabertura de resolvida)."""
    novo = {imp: dict(v) for imp, v in estado.items()}
    for item in itens:
        est = novo.get(item["impressao"])
        if est is None:
            continue
        if est.get("status") == "resolvida" and item["status"] == "aberta":
            est["status"] = "aberta"
            est["reincidente"] = True
            est.pop("resolvida_em", None)
    return novo


def aplicar_prs(estado: dict[str, dict], consultar: Callable[[str], str | None],
                agora: datetime) -> list[str]:
    """Move ``em_pr`` conforme o PR: MERGED -> resolvida; CLOSED -> aberta com a
    tentativa anotada. ``consultar(url)`` devolve o state do ``gh`` ou None
    quando nao conseguiu perguntar -- nesse caso nada muda. Devolve as
    impressoes alteradas."""
    mudadas = []
    for imp, est in estado.items():
        url = est.get("pr_url")
        if est.get("status") != "em_pr" or not url:
            continue
        situacao = (consultar(url) or "").upper()
        if situacao == "MERGED":
            est["status"] = "resolvida"
            est["resolvida_em"] = agora.isoformat()
        elif situacao == "CLOSED":
            est["status"] = "aberta"
            est.setdefault("tentativas", []).append(
                {"pr_url": url, "fechado_em": agora.isoformat(),
                 "nota_triagem": est.get("nota_triagem")})
            est["nota_triagem"] = (f"PR anterior fechado sem merge ({url}); "
                                   "ler `tentativas` antes de repetir a abordagem.")
            est["pr_url"] = None
        else:
            continue
        mudadas.append(imp)
    return mudadas


def marcar(estado: dict[str, dict], impressao: str, status: str, *,
           pr_url: str | None = None, nota: str | None = None,
           agora: datetime) -> str:
    """Muda o status de uma lacuna no estado local. ``impressao`` aceita
    prefixo unico. Devolve a impressao completa. Levanta ``ValueError``."""
    if status not in STATUS:
        raise ValueError(f"status invalido: {status!r} (use {', '.join(STATUS)})")
    if status == "em_pr" and not pr_url:
        raise ValueError("em_pr exige --pr-url")
    est = estado.setdefault(impressao, {})
    est["status"] = status
    est["atualizado_em"] = agora.isoformat()
    if pr_url is not None:
        est["pr_url"] = pr_url
    if nota is not None:
        est["nota_triagem"] = nota
    if status == "resolvida":
        est["resolvida_em"] = agora.isoformat()
    return impressao


def resolver_prefixo(prefixo: str, conhecidas: Iterable[str]) -> str:
    achadas = sorted({i for i in conhecidas if i.startswith(prefixo)})
    if len(achadas) != 1:
        raise ValueError(f"prefixo {prefixo!r} casa com {len(achadas)} lacunas")
    return achadas[0]


def diferencas_para_cloud(itens: list[dict], cloud: Iterable[dict]) -> list[dict]:
    """UPDATEs para ``app_lacunas``: so linhas que ja existem na nuvem. Lacuna
    so local nao sobe -- o Supabase free nao guarda o que a Cloud nao viu."""
    cloud = {linha["impressao"]: linha for linha in cloud}
    saida = []
    for item in itens:
        nuv = cloud.get(item["impressao"])
        if nuv is None:
            continue
        novo = {c: item.get(c) for c in _CAMPOS_ESTADO}
        novo["reincidente"] = item["reincidente"]
        if any(novo[c] != nuv.get(c) for c in novo):
            saida.append({"impressao": item["impressao"], **novo})
    return saida


def meses_para_rotacionar(eventos: Iterable[dict], agora: datetime) -> dict[str, list[dict]]:
    """Eventos de meses anteriores ao corrente, agrupados por ``AAAA-MM``.
    Roda a cada sincronizacao, nao so no dia 1: com o PC desligado no dia 1
    a rotacao do mes nao pode se perder.
        memoria: cadencia-em-horas-pula-dia"""
    mes_atual = agora.strftime("%Y-%m")
    grupos: dict[str, list[dict]] = {}
    for ev in eventos:
        ts = _dt(ev.get("ts"))
        if ts is None:
            continue
        mes = ts.strftime("%Y-%m")
        if mes < mes_atual:
            grupos.setdefault(mes, []).append(ev)
    return grupos


def resumo_semanal(itens: list[dict], agora: datetime) -> str:
    """Markdown para revisao humana: o que o corretor chamou de legitima (e
    portanto nao vai corrigir), reincidentes e contagem por status."""
    por_status = {s: 0 for s in STATUS}
    for i in itens:
        por_status[i["status"]] = por_status.get(i["status"], 0) + 1
    linhas = [f"# Lacunas — resumo semanal ({agora.date().isoformat()})", "",
              "| status | lacunas |", "|---|---:|"]
    linhas += [f"| {s} | {n} |" for s, n in por_status.items()]

    def secao(titulo, selecionados, vazio):
        linhas.extend(["", f"## {titulo}", ""])
        if not selecionados:
            linhas.append(vazio)
        for i in selecionados:
            linhas.append(f"- `{i['impressao'][:8]}` {i['fonte']} · {i['modulo']} · "
                          f"{i['codigo'] or '(sem codigo)'} {i['entidade']}".rstrip())
            linhas.append(f"  - mensagem: {i['ultima_mensagem']}")
            if i.get("nota_triagem"):
                linhas.append(f"  - triagem: {i['nota_triagem']}")

    ordem = sorted(itens, key=lambda i: (-i["prioridade"], i["impressao"]))
    secao("Marcadas como legítimas — conferir se o aviso é mesmo o certo",
          [i for i in ordem if i["status"] == "legitima"], "Nenhuma.")
    secao("Reincidentes — a correção não pegou",
          [i for i in ordem if i["reincidente"] and i["status"] != "resolvida"], "Nenhuma.")
    return "\n".join(linhas) + "\n"
