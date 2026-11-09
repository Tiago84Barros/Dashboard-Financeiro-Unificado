"""
data_pipeline/us/sec_filings.py
Documentos entregues à SEC (8-K, 10-Q, 10-K...) das ações do universo dos EUA.

Fecha a lacuna que a tela de relatórios declarava em texto fixo: "não há base
de filings da SEC no projeto para ações americanas". A B3 tinha a CVM/IPE e os
FIIs o FNET; os EUA, nada -- e a LLM respondia sobre resultado trimestral sem
saber que o 8-K do resultado saiu ontem.

Fonte: ``data.sec.gov/submissions/CIK##########.json``, o MESMO endpoint que o
projeto já consulta para o perfil (``edgar.EdgarProvider.get_profile``) e para
a causa de saída (``core.us_saida_causa``). Ele traz, em listas paralelas, o
formulário, a data, os itens do 8-K e o documento principal de cada entrega
recente. Não se baixa o texto do documento: o tipo e o item já dizem o que
aconteceu ("2.02 -- divulgação de resultado", "5.02 -- saída de diretor"), e o
link leva ao original.

Uma requisição por CIK, não por símbolo: GOOG e GOOGL são a mesma entrega.
Grava no armazém local (``market_us.sec_filings``); a vitrine que o app lê é
``data/public/informacoes_recentes.json.gz``, montada por
``scripts/publish_informacoes_recentes.py``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from core.inteligencia_ativos import informacoes as inf

logger = logging.getLogger(__name__)

FONTE = "SEC/EDGAR"
MIGRACAO = (Path(__file__).resolve().parents[2] / "supabase_unificado" / "schema"
            / "085_market_us_sec_filings.sql")
ARQUIVO_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acesso}/{documento}"

#: Janela gravada. A vitrine publica 180 dias; guardar um pouco mais deixa a
#: janela da vitrine mudar sem precisar recoletar.
JANELA_DIAS = 400

# ── Itens do 8-K ──────────────────────────────────────────────────────────────
#: Código do item → (tipo do documento, rótulo). O tipo usa a MESMA taxonomia
#: dos documentos da B3 (``informacoes.ROTULO_DOC``), para a tela e a LLM
#: tratarem um 8-K 2.02 como tratam um release da CVM.
ITENS_8K: dict[str, tuple[str, str]] = {
    "1.01": (inf.FATO_RELEVANTE, "contrato relevante firmado"),
    "1.02": (inf.FATO_RELEVANTE, "contrato relevante encerrado"),
    "1.03": (inf.FATO_RELEVANTE, "falência ou recuperação judicial"),
    "1.05": (inf.FATO_RELEVANTE, "incidente de cibersegurança relevante"),
    "2.01": (inf.FATO_RELEVANTE, "aquisição ou venda de ativos concluída"),
    "2.02": (inf.RELEASE, "divulgação de resultado"),
    "2.03": (inf.FATO_RELEVANTE, "nova obrigação financeira relevante"),
    "2.04": (inf.FATO_RELEVANTE, "evento que antecipa obrigação financeira"),
    "2.05": (inf.FATO_RELEVANTE, "custos de reestruturação ou saída"),
    "2.06": (inf.FATO_RELEVANTE, "baixa contábil (impairment)"),
    "3.01": (inf.FATO_RELEVANTE, "aviso de deslistagem ou descumprimento de regra de listagem"),
    "3.02": (inf.OFERTA, "venda de ações sem registro"),
    "3.03": (inf.FATO_RELEVANTE, "mudança nos direitos dos acionistas"),
    "4.01": (inf.FATO_RELEVANTE, "troca de auditor"),
    "4.02": (inf.FATO_RELEVANTE, "demonstrações anteriores não confiáveis (republicação)"),
    "5.01": (inf.FATO_RELEVANTE, "mudança de controle"),
    "5.02": (inf.FATO_RELEVANTE, "saída ou nomeação de diretor ou conselheiro"),
    "5.03": (inf.COMUNICADO, "mudança no estatuto ou no exercício fiscal"),
    "5.07": (inf.ASSEMBLEIA, "resultado de votação em assembleia"),
    "7.01": (inf.COMUNICADO, "divulgação ao mercado (Reg FD)"),
    "8.01": (inf.COMUNICADO, "outros eventos"),
}
#: Só anexos: não diz nada sozinho.
ITEM_ANEXOS = "9.01"

# ── Formulários periódicos e outros ───────────────────────────────────────────
_FORMAS: dict[str, tuple[str, str]] = {
    "10-K": (inf.BALANCO, "Relatório anual (10-K)"),
    "10-KT": (inf.BALANCO, "Relatório anual de transição (10-KT)"),
    "10-Q": (inf.BALANCO, "Relatório trimestral (10-Q)"),
    "20-F": (inf.BALANCO, "Relatório anual de emissor estrangeiro (20-F)"),
    "40-F": (inf.BALANCO, "Relatório anual de emissor canadense (40-F)"),
    "6-K": (inf.COMUNICADO, "Comunicado de emissor estrangeiro (6-K)"),
    "DEF 14A": (inf.ASSEMBLEIA, "Proxy da assembleia (DEF 14A)"),
    "S-1": (inf.OFERTA, "Registro de oferta (S-1)"),
    "S-3": (inf.OFERTA, "Registro de oferta (S-3)"),
    "S-4": (inf.OFERTA, "Registro de fusão ou troca de ações (S-4)"),
    "SC 14D9": (inf.FATO_RELEVANTE, "Posição da empresa sobre oferta pública (SC 14D9)"),
}


def classificar(forma: str, itens: str | None) -> tuple[str, str] | None:
    """(tipo, título) de uma entrega, ou ``None`` se ela não interessa. Puro.

    Retificação (``/A``) herda o tipo da original e diz que é retificação. No
    8-K, o tipo é o do item mais importante pela ``PRIORIDADE_DOC`` da tela, e
    o título lista todos os itens -- "2.02 e 5.02" no mesmo dia não é a mesma
    notícia que só "2.02".
    """
    forma = str(forma or "").strip().upper()
    base, retifica = (forma[:-2], True) if forma.endswith("/A") else (forma, False)
    sufixo = " -- retificação" if retifica else ""
    if base == "8-K":
        codigos = [c.strip() for c in str(itens or "").split(",") if c.strip()]
        conhecidos = [c for c in codigos if c in ITENS_8K]
        if not conhecidos:
            # 8-K só de anexos, ou com item que não está no mapa: ainda é um
            # evento declarado pela empresa, mas sem dizer qual.
            return inf.COMUNICADO, f"Evento relevante (8-K){sufixo}"
        tipo = min((ITENS_8K[c][0] for c in conhecidos),
                   key=inf.PRIORIDADE_DOC.index)
        rotulos = "; ".join(f"{c} {ITENS_8K[c][1]}" for c in conhecidos)
        return tipo, f"8-K: {rotulos}{sufixo}"
    if base in _FORMAS:
        tipo, titulo = _FORMAS[base]
        return tipo, titulo + sufixo
    return None


def linhas_de_recentes(recentes: dict, cik: str, *, desde: date) -> list[dict]:
    """Uma linha por entrega de interesse a partir de ``filings.recent``. Puro.

    As listas paralelas da SEC podem vir com tamanhos diferentes quando uma
    entrega não tem item (a mesma observação de ``us_saida_causa.itens_de_8k``);
    o que falta é preenchido com vazio, nunca desalinhado.
    """
    def _lista(chave: str) -> list[str]:
        return [str(x or "") for x in (recentes.get(chave) or [])]

    acessos = _lista("accessionNumber")
    n = len(acessos)
    colunas = {c: (_lista(c) + [""] * n)[:n] for c in (
        "form", "filingDate", "reportDate", "items", "primaryDocument",
        "primaryDocDescription")}
    cik_num = str(int(cik)) if str(cik or "").strip().isdigit() else str(cik)
    saida = []
    for i, acesso in enumerate(acessos):
        try:
            entregue = date.fromisoformat(colunas["filingDate"][i])
        except ValueError:
            continue
        if entregue < desde:
            continue
        forma = colunas["form"][i]
        if classificar(forma, colunas["items"][i]) is None:
            continue
        documento = colunas["primaryDocument"][i]
        referencia = colunas["reportDate"][i]
        saida.append({
            "accession_number": acesso,
            "cik": str(cik).zfill(10),
            "form": forma.strip().upper(),
            "filing_date": entregue,
            "report_date": date.fromisoformat(referencia) if referencia else None,
            "items": colunas["items"][i] or None,
            "primary_document": documento or None,
            "description": colunas["primaryDocDescription"][i] or None,
            "url": ARQUIVO_URL.format(cik=cik_num, acesso=acesso.replace("-", ""),
                                      documento=documento) if documento else
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_num}",
        })
    return saida


# ── Coleta (rede + armazém) ───────────────────────────────────────────────────
_SQL_UNIVERSO = """
    SELECT symbol, cik FROM market_us.company_snapshots
     WHERE is_active IS TRUE AND cik IS NOT NULL AND symbol IS NOT NULL
"""

_SQL_UPSERT = """
    INSERT INTO market_us.sec_filings
        (accession_number, symbol, cik, form, filing_date, report_date, items,
         primary_document, description, url, collected_at)
    VALUES (:accession_number, :symbol, :cik, :form, :filing_date, :report_date,
            :items, :primary_document, :description, :url, :collected_at)
    ON CONFLICT (accession_number, symbol) DO UPDATE SET
        form = EXCLUDED.form, items = EXCLUDED.items,
        report_date = EXCLUDED.report_date, url = EXCLUDED.url,
        description = EXCLUDED.description, collected_at = EXCLUDED.collected_at
"""


def simbolos_por_cik(pares: Iterable[tuple[str, str]],
                     somente: Iterable[str] | None = None) -> dict[str, list[str]]:
    """``{cik10: [símbolos]}``: uma requisição serve todas as classes. Puro."""
    filtro = {str(s).upper() for s in somente} if somente else None
    saida: dict[str, list[str]] = {}
    for simbolo, cik in pares:
        simbolo = str(simbolo or "").upper().strip()
        cik = str(cik or "").strip()
        if not simbolo or not cik.isdigit():
            continue
        if filtro is not None and simbolo not in filtro:
            continue
        lista = saida.setdefault(cik.zfill(10), [])
        if simbolo not in lista:
            lista.append(simbolo)
    return saida


def coletar(provider, engine, *, tickers: Iterable[str] | None = None,
            hoje: date | None = None, janela_dias: int = JANELA_DIAS,
            aplicar_migracao: bool = True) -> dict:
    """Busca o submissions de cada CIK do universo e grava as entregas.

    Falha de um CIK não derruba os outros e vira contagem nomeada; zero CIK no
    universo é erro, não sucesso -- o mesmo cuidado do comando ``daily``.
    """
    from sqlalchemy import text

    hoje = hoje or date.today()
    desde = hoje - timedelta(days=int(janela_dias))
    with engine.begin() as conn:
        # Idempotente: o coletor não depende de alguém ter rodado `init-schema`.
        if aplicar_migracao:
            conn.execute(text(MIGRACAO.read_text(encoding="utf-8")))
        pares = [(r[0], r[1]) for r in conn.execute(text(_SQL_UNIVERSO))]
    mapa = simbolos_por_cik(pares, tickers)
    if not mapa:
        return {"ok": False, "ciks": 0, "linhas": 0,
                "erro": "nenhum símbolo com CIK em market_us.company_snapshots "
                        "(rode `snapshot` antes, ou confira --tickers)"}
    agora = datetime.now(timezone.utc)
    linhas = falhas = sem_resposta = 0
    for cik, simbolos in mapa.items():
        try:
            sub = provider.submissions(cik)
        except Exception as exc:  # noqa: BLE001 - um CIK não derruba os outros
            falhas += 1
            logger.warning("filings %s (%s) falhou: %s", cik, ",".join(simbolos), exc)
            continue
        if not sub:
            sem_resposta += 1
            continue
        recentes = ((sub.get("filings") or {}).get("recent")) or {}
        entregas = linhas_de_recentes(recentes, cik, desde=desde)
        if not entregas:
            continue
        registros = [{**e, "symbol": s, "collected_at": agora}
                     for e in entregas for s in simbolos]
        with engine.begin() as conn:
            conn.execute(text(_SQL_UPSERT), registros)
        linhas += len(registros)
    return {"ok": falhas < len(mapa), "ciks": len(mapa), "linhas": linhas,
            "falhas": falhas, "sem_resposta": sem_resposta,
            "desde": desde.isoformat()}
