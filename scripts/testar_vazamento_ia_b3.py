# -*- coding: utf-8 -*-
"""Etapa 1 da §21 do guia: a IA reconhece a empresa e o ano num dossiê anônimo?

Se reconhece (> 20% de acerto no ticker), qualquer nota que ela der a uma
empresa-safra do passado pode ser memória do que veio depois, e a etapa 2
(notas às cegas, retroativas) fica invalidada.

O dossiê leva só números do retrato PIT da safra (múltiplos, setor), o
histórico de ROIC e o de lucro, com os anos relativos (N-1, N-2, ...). Sem
nome, ticker, data nem texto. Amostra fixa (``--semente``), prompt fixo.

Uso::

    python scripts/testar_vazamento_ia_b3.py --cache-dir data/cache_wh --n 60 --dados-reais-publicos
    python scripts/testar_vazamento_ia_b3.py --cache-dir data/cache_wh --n 2 --dry-run --dados-reais-publicos

Grava `data/oos_vazamento_ia_b3.json`. Chama a LLM do app pela cadeia de
`_chat_complete` (custo baixo: uma chamada curta por empresa-safra).

Dados reais, não sintéticos: o teste só faz sentido com empresas de verdade
(a pergunta é se a IA as reconhece). O AGENTS.md pede dados sintéticos em
prompts; por isso a chamada à LLM e o ``--exportar`` exigem
``--dados-reais-publicos``, que confirma o que vai no dossiê: balanço e
documentos públicos da CVM de companhias abertas, anonimizados, sem dado de
usuário, carteira ou pessoa física. Sem a flag, o script para antes de enviar
ou exportar. ``--corrigir`` só lê respostas já gravadas e não exige a flag.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_vazamento_ia_b3.json"
LIMITE_VAZAMENTO = 0.20
COLUNAS = ["P/L", "P/VP", "DY", "ROE", "ROIC", "Margem_Operacional",
           "Margem_Liquida", "Endividamento_Total", "Liquidez_Corrente",
           "P_FCO", "EV_EBIT"]

PROMPT = (
    "Abaixo há o retrato financeiro anônimo de uma empresa listada na B3. Os "
    "anos são relativos: 'N-1' é o último exercício publicado. Diga qual é a "
    "empresa (ticker B3) e qual é o ano N-1. Se não souber, responda "
    "'desconhecida'. Não invente. Responda só com um JSON: "
    '{"ticker": "XXXX3 ou desconhecida", "ano_n1": 2018 ou null, '
    '"confianca": 0 a 1}.\n\n')


PROMPT_TEXTO = (
    "Abaixo há o retrato financeiro anônimo de uma empresa listada na B3 e "
    "trechos de documentos que ela publicou na CVM, com nome, ticker, CNPJ e "
    "nomes próprios removidos. Os anos são relativos: 'N-1' é o último "
    "exercício publicado e 'N' é o ano em curso. Diga qual é a empresa (ticker "
    "B3) e qual é o ano N-1. Se não souber, responda 'desconhecida'. Não "
    "invente. Responda só com um JSON: "
    '{"ticker": "XXXX3 ou desconhecida", "ano_n1": 2018 ou null, '
    '"confianca": 0 a 1}.\n\n')

DOSSIE_NUMEROS = "só números (retrato PIT, setor, ROIC e lucro por exercício), sem texto"
DOSSIE_TEXTO = ("números + release mais recente e 2 fatos relevantes da CVM/IPE "
                "publicados até 31/03/N, anonimizados (safras 2024-2025)")

# Safras com texto público até 31/03/N: o acervo da CVM/IPE começa em 01/2023.
SAFRAS_COM_TEXTO = (2024, 2025)
LIMITE_RELEASE = 3500   # caracteres do release de resultados mais recente
LIMITE_FATO = 1200      # caracteres de cada um dos 2 fatos relevantes mais recentes

# Palavras de nome de empresa que também são vocabulário de setor: ficam no texto.
_COMUNS = {
    "companhia", "cia", "sa", "s.a", "ltda", "holding", "grupo", "participacoes",
    "brasil", "brasileira", "brasileiro", "nacional", "energia", "eletrica",
    "banco", "bancos", "industria", "industrias", "comercio", "servicos",
    "sistemas", "saneamento", "seguros", "seguridade", "investimentos", "imoveis",
    "empreendimentos", "construtora", "incorporadora", "alimentos", "transportes",
    "logistica", "distribuidora", "telecomunicacoes", "petroleo", "mineracao",
    "siderurgica", "papel", "celulose", "saude", "educacao", "varejo", "lojas",
    "tecnologia", "engenharia", "ferrovia", "centro", "geral", "sul", "norte",
    "nordeste", "rio", "paulo", "minas", "estado", "mercado", "aberta",
    "fato", "relevante", "comunicado", "release", "resultados", "diretoria",
    "conselho", "administracao", "acionistas", "assembleia",
    "recuperacao", "judicial", "extrajudicial",
}
_INSTITUCIONAIS = (
    "Comissão", "Comissao", "Resolução", "Resolucao", "Instrução", "Instrucao",
    "Lei", "Conselho", "Assembleia", "Banco Central", "Diretoria", "Receita",
    "Tribunal", "Supremo", "Superior", "Ministério", "Ministerio", "Agência",
    "Agencia", "Tesouro", "Bolsa", "Brasil Bolsa", "Novo Mercado", "Relações",
    "Demonstrações", "Demonstracoes", "Fato", "Comunicado", "Aviso",
)
# Apelidos entre aspas que são termo jurídico ou de mercado, não nome: ficam.
_ALIAS_GENERICOS = {
    "companhia", "company", "emissora", "issuer", "investidor", "investor", "ofertante",
    "compradora", "vendedora", "controladora", "controlada", "incorporada", "incorporadora",
    "lei das s.a", "lei das sociedades por acoes", "lsa", "age", "ago", "agoe", "rca", "b3",
    "cvm", "cade", "aneel", "anatel", "anp", "ans", "bacen", "fato relevante",
    "material fact", "operacao", "transacao", "transaction", "contrato", "acordo",
    "agreement", "oferta", "offering", "debentures", "emissao", "acoes", "conselho",
    "assembleia", "reorganizacao", "incorporacao", "aquisicao", "closing", "plano",
    "programa", "recompra", "aumento de capital", "partes", "parties", "edital",
}
_ACENTOS = {"a":"aáàâãä", "e": "eéèêë", "i": "iíìîï", "o": "oóòôõö",
            "u": "uúùûü", "c": "cç", "n": "nñ"}


def _sem_acento(s: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(ch))


def _padrao(termo: str) -> str:
    """Regex que casa o termo sem ligar para acento, caixa e hífen/espaço."""
    partes = []
    for ch in _sem_acento(termo).lower():
        if ch in _ACENTOS:
            partes.append(f"[{_ACENTOS[ch]}]")
        elif ch in " -":
            partes.append(r"[\s\-]+")
        else:
            partes.append(re.escape(ch))
    return r"(?<!\w)" + "".join(partes) + r"(?!\w)"


def _ano_relativo(ano: int, ano_max: int) -> str:
    k = ano_max + 1 - ano  # N = ano_max + 1
    return "N" if k == 0 else (f"N-{k}" if k > 0 else f"N+{-k}")


def termos_da_empresa(nomes: list[str], texto: str) -> list[str]:
    """Nome inteiro, palavras distintivas do nome e razão social do cabeçalho.
    Mais longos primeiro. Apelidos (ex.: ("FCA")) saem em `anonimizar`."""
    nomes = [n for n in nomes if n]
    # Cabeçalho que o acervo põe em cada trecho: "Empresa: GOAU4 (METALURGICA GERDAU SA) | ...".
    nomes += re.findall(r"Empresa:\s*\S+\s*\(([^)|\n]+)\)", texto)
    cab = re.split(r"CNPJ", texto[:800], maxsplit=1, flags=re.I)
    if len(cab) == 2:
        linhas = [ln.strip() for ln in cab[0].splitlines() if re.search(r"[A-Za-z]{3}", ln)]
        linhas = [ln for ln in linhas if not re.search(r"p[aá]gina|companhia aberta", ln, re.I)]
        if linhas:
            nomes.append(linhas[-1])
    termos = set()
    for nome in nomes:
        limpo = re.sub(r"\b(S\.?\s?A\.?|LTDA\.?|CIA\.?)(?!\w)", " ", nome, flags=re.I)
        limpo = re.sub(r"\s+", " ", limpo).strip(" .,-")
        if len(limpo) >= 3 and _sem_acento(limpo).lower() not in _COMUNS:
            termos.add(limpo)
        for w in re.findall(r"[\wÀ-ÿ&\-]+", limpo):
            if len(w) >= 4 and _sem_acento(w).lower().strip("-") not in _COMUNS:
                termos.add(w)
    return sorted(termos, key=len, reverse=True)


def anonimizar(texto: str, nomes: list[str], ano_max: int) -> str:
    """Tira o que identifica a empresa e a data, e deixa os anos relativos a N."""
    termos = termos_da_empresa(list(nomes), texto)
    t = re.sub(r"(?m)^\s*Empresa:[^\n]*\|[^\n]*$", "", texto)  # metadado do acervo, não do documento
    t = re.sub(r"(https?://|www\.)\S+", "[site]", t, flags=re.I)
    t = re.sub(r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:com|net|org)(?:\.br)?\b", "[site]", t, flags=re.I)
    for termo in termos:
        p = _padrao(termo)
        if " " not in termo and len(termo) >= 5:  # "Randon" leva junto "Randoncorp"
            p = p[: -len(r"(?!\w)")] + r"\w*"
        t = re.sub(p, "[EMPRESA]", t, flags=re.I)
    # Apelido definido logo depois do nome: [EMPRESA] S.A. ("Companhia" ou "FCA").
    aspas = "\"“”'‘’"
    for m in re.finditer(r"\[EMPRESA\][^()\n]{0,30}\(([^)]{1,60})\)", t):
        for a in re.findall(rf"[{aspas}]\s*([A-Z][\w&\-]{{1,20}})\s*[{aspas}]", m.group(1)):
            if _sem_acento(a).lower() not in _COMUNS:
                t = re.sub(rf"(?<!\w){re.escape(a)}(?!\w)", "[EMPRESA]", t)
    # Qualquer outro apelido entre aspas, como (“Itaú Unibanco” ou “Companhia”): some.
    for a in set(re.findall(rf"\([^()\n{aspas}]{{0,20}}[{aspas}]\s*([A-ZÁ-Ú][^{aspas}()\n]{{1,40}}?)\s*[{aspas}]", t)):
        if _sem_acento(a).lower().strip(" .") not in _ALIAS_GENERICOS and "[" not in a:
            t = re.sub(rf"(?<!\w){re.escape(a)}(?!\w)", "[NOME]", t)
    # Palavra colada antes do nome: "Metalúrgica [EMPRESA]" separa GOAU de GGBR.
    t = re.sub(r"\b[A-ZÁ-Ú][\wÀ-ÿ]{3,}\s+(?=\[EMPRESA\])",
               lambda m: m.group(0) if _sem_acento(m.group(0)).strip().lower() in _COMUNS
               else "", t)
    # Razão social de qualquer sociedade (inclusive em maiúsculas): "X Y DE Z S.A.".
    t = re.sub(r"(?<![\w\]])(?:[A-ZÁ-Ú][\wÀ-ÿ&.\-]*\s+(?:(?:de|da|do|dos|das|e|DE|DA|DO|DOS|DAS|E|–|-)\s+)?)"
               r"{1,7}(?=S\s?\.\s?A\.|S/A\b)",
               lambda m: m.group(0) if re.match(r"(Lei|B3)\b", m.group(0)) else "[NOME] ", t)
    t = re.sub(r"\b(OTC|NYSE|NASDAQ|LATIBEX|ADRs?(?:\s+(?:I{1,3}|N[ií]vel\s+\w+))?|B3|Bovespa|BM&FBovespa)"
               r"\s*:\s*[A-Z][A-Z0-9.]{2,7}\b(?!\d)", r"\1: [TICKER]", t)
    # Sigla própria logo antes da listagem: "O GPA [B3: ...; NYSE: ...]" some no texto todo.
    for sigla in set(re.findall(r"\b([A-Z][A-Za-z&]{1,9})\s*[\[(]\s*(?:B3|Bovespa|NYSE|OTC|NASDAQ)\s*:", t)):
        if _sem_acento(sigla).lower() not in _COMUNS | _ALIAS_GENERICOS:
            t = re.sub(rf"(?<![\w\[]){re.escape(sigla)}(?!\w)", "[EMPRESA]", t)
    t = re.sub(r"\b(Rua|Avenida|Av\.|Alameda|Al\.|Pra[cç]a|Rodovia|Estrada)\s[^,\n]+",
               "[endereço]", t)
    # Cidade na linha de data: "Jaraguá do Sul, 25 de fevereiro de 2025".
    t = re.sub(r"(?m)^\s*[A-ZÁ-Ú][\wÀ-ÿ'\-]*(?:\s+(?:de|da|do|dos|das)?\s*[A-ZÁ-Ú][\wÀ-ÿ'\-]*)*"
               r"(?:\s*/\s*[A-Z]{2})?(?:\s*,\s*Brasil)?\s*(?:,|\-)\s*"
               r"(?=\d{1,2}\s*(?:de\s+)?[a-zç]+\s+de\s+\d{4})",
               "[cidade], ", t)
    # Raiz com dígito também ("B3SA3"); mascarar a mais (ex.: "CPC06") não faz mal aqui.
    t = re.sub(r"\b[A-Z][A-Z0-9]{3}(3|4|5|6|11)\b", "[TICKER]", t)
    t = re.sub(r"\d{2}\.?\d{3}\.?\d{3}\s?/\s?\d{4}\s?-\s?\d{2}", "[CNPJ]", t)
    t = re.sub(r"(NIRE|CVM)\s*(n[º°o.]*)?\s*[:\-]?\s*[\d.\-/]{5,}", r"\1 [n]", t, flags=re.I)
    t = re.sub(r"(https?://|www\.)\S+", "[site]", t, flags=re.I)
    t = re.sub(r"\S+@\S+", "[email]", t)
    # Assinaturas: linha curta com cargo e a linha de nome logo acima.
    linhas, saida = t.splitlines(), []
    cargo = re.compile(r"Diretor|Diretora|Presidente|\bCEO\b|\bCFO\b|Rela[cç][oõ]es com Investidores", re.I)
    for ln in linhas:
        if cargo.search(ln) and len(ln.strip()) < 80:
            while saida and not saida[-1].strip():
                saida.pop()
            if saida and len(saida[-1].strip()) < 60 and not re.search(r"\d", saida[-1]):
                saida.pop()
            continue
        saida.append(ln)
    t = "\n".join(saida)
    # Nomes próprios em sequência (pessoas, marcas, endereços), menos instituições.
    nome_proprio = re.compile(
        r"\b[A-ZÁ-Ú][a-zà-ÿ]+(?:\s+(?:de|da|do|dos|das|e)?\s*[A-ZÁ-Ú][a-zà-ÿ]+){2,}")
    t = nome_proprio.sub(lambda m: m.group(0) if m.group(0).startswith(_INSTITUCIONAIS)
                         else "[NOME]", t)
    t = re.sub(r"\b([1-4])\s?T\s?(\d{2})\b",
               lambda m: f"{m.group(1)}T({_ano_relativo(2000 + int(m.group(2)), ano_max)})", t)
    t = re.sub(r"\b(\d{1,2})M(\d{2})\b",
               lambda m: f"{m.group(1)}M({_ano_relativo(2000 + int(m.group(2)), ano_max)})", t)
    t = re.sub(r"\b([12])\s?S\s?(\d{2})\b",
               lambda m: f"{m.group(1)}S({_ano_relativo(2000 + int(m.group(2)), ano_max)})", t)
    t = re.sub(r"(?<![\d.,])(19[89]\d|20[0-3]\d)(?![\d]|[.,]\d)",
               lambda m: _ano_relativo(int(m.group(1)), ano_max), t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def textos_publicos(conn, ticker: str, safra: int) -> list[tuple[str, str]]:
    """(tipo, texto bruto) do release mais recente e dos 2 fatos relevantes mais
    recentes publicados de 01/01/N-1 a 31/03/N (antes de a safra começar)."""
    from datetime import date

    from sqlalchemy import text as sql

    docs = conn.execute(sql("""
        SELECT d.id, d.tipo FROM public.docs_corporativos d
        WHERE LEFT(UPPER(d.ticker), 4) = :raiz
          AND d.document_date >= :ini AND d.document_date < :fim
          AND d.tipo IN ('Press-release', 'Fato Relevante')
          AND EXISTS (SELECT 1 FROM public.docs_corporativos_chunks k WHERE k.doc_id = d.id)
        ORDER BY d.document_date DESC, d.id DESC"""),
        {"raiz": ticker.upper()[:4], "ini": date(safra - 1, 1, 1),
         "fim": date(safra, 4, 1)}).fetchall()
    quota = {"Press-release": 1, "Fato Relevante": 2}
    out = []
    for doc_id, tipo in docs:
        if not quota[tipo]:
            continue
        pedacos = conn.execute(sql(
            "SELECT chunk_text FROM public.docs_corporativos_chunks "
            "WHERE doc_id = :i ORDER BY chunk_index"), {"i": doc_id}).fetchall()
        bruto = "\n".join(p[0] or "" for p in pedacos)
        corpo = re.sub(r"(?m)^\s*Empresa:[^\n]*\|[^\n]*$", "", bruto)
        if len(corpo.strip()) < 200:
            continue  # só o cabeçalho do acervo, sem o documento: vai o próximo
        limite = LIMITE_RELEASE if tipo == "Press-release" else LIMITE_FATO
        if len(bruto) > limite:  # corta na última palavra inteira ("DF 202" vazaria o ano)
            bruto = bruto[:limite].rsplit(None, 1)[0] + " [...]"
        out.append((tipo, bruto))
        quota[tipo] -= 1
    return out


def _fmt(v) -> str:
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return "n/d"


def dossie(linha, setor, roic_hist: dict[int, float], lucro: dict[int, float],
           ano_max: int) -> str:
    """Texto anônimo: múltiplos do retrato, setor e séries com anos relativos."""
    partes = [f"Setor: {setor or 'n/d'}", "Múltiplos do exercício N-1:"]
    partes += [f"- {c}: {_fmt(linha.get(c))}" for c in COLUNAS]
    if roic_hist:
        partes.append("ROIC por exercício: " + ", ".join(
            f"N-{ano_max - a + 1}={_fmt(v)}" for a, v in sorted(roic_hist.items())))
    if lucro:
        partes.append("Lucro líquido (R$ mi) por exercício: " + ", ".join(
            f"N-{ano_max - a + 1}={_fmt(v)}" for a, v in sorted(lucro.items())))
    return "\n".join(partes)


def parse_resposta(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def resposta_valida(resp) -> bool:
    """Resposta que o modelo de fato deu: objeto com ``ticker`` em texto.

    "desconhecida" é abstenção válida; ``{}`` (sem resposta ou JSON quebrado) não é.
    """
    return isinstance(resp, dict) and isinstance(resp.get("ticker"), str) and bool(resp["ticker"].strip())


def _ano(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None  # "2023/2024", "desconhecido", 2023.5 em texto: conta como ano errado


def acertou(resp: dict, ticker: str, ano_verdadeiro: int) -> tuple[bool, bool]:
    """(acertou o ticker, acertou o ticker e o ano). Casa pela raiz do ticker."""
    if not resposta_valida(resp):
        return False, False
    t = resp["ticker"].upper()
    raiz = lambda x: re.sub(r"\d+$", "", x)  # noqa: E731  (ON/PN/UNIT contam)
    ok_t = "DESCONHECIDA" not in t and raiz(t) == raiz(ticker.upper())
    ok_a = ok_t and _ano(resp.get("ano_n1")) == int(ano_verdadeiro)
    return ok_t, ok_a


def veredito(acertos: int, invalidas: int, n: int) -> bool | None:
    """Invalida a etapa 2? None quando as respostas que faltam podem virar o resultado.

    Conta cada resposta faltante como erro (piso) e como acerto (teto): só há
    veredito quando os dois lados do limite concordam.
    """
    if n <= 0:
        return None
    piso, teto = acertos / n, (acertos + invalidas) / n
    if piso > LIMITE_VAZAMENTO:
        return True
    if teto <= LIMITE_VAZAMENTO:
        return False
    return None


def resumo(amostra: list[dict], respostas: list[dict], modelo_por_caso: list[str], n_universo: int,
           semente: int, dossie_usado: str = None) -> dict:
    """Pontua as respostas (na ordem da amostra) e monta o JSON de saída.

    ``modelo_por_caso`` diz quem respondeu cada dossiê. Se a cadeia de provedores
    trocou de modelo no meio, a taxa mistura modelos e não vale para nenhum deles:
    o veredito fica None e a taxa sai separada por modelo.
    """
    linhas, hit_t, hit_a, invalidas = [], 0, 0, 0
    por_modelo: dict[str, list[int]] = {}
    for c, resp, mod in zip(amostra, respostas, modelo_por_caso):
        ok_t, ok_a = acertou(resp, c["ticker"], c["ano_n1"])
        valida = resposta_valida(resp)
        hit_t += ok_t
        hit_a += ok_a
        invalidas += not valida
        conta = por_modelo.setdefault(str(mod), [0, 0])
        conta[0] += ok_t
        conta[1] += 1
        linhas.append({"ticker": c["ticker"], "safra": c["safra"], "ano_n1": c["ano_n1"],
                       "modelo": str(mod), "resposta": resp, "resposta_valida": valida,
                       "acertou_ticker": ok_t, "acertou_ticker_e_ano": ok_a})
    n = max(len(amostra), 1)
    taxa = hit_t / n
    inv = veredito(hit_t, invalidas, len(amostra))
    if len(por_modelo) > 1:
        inv = None  # taxa de modelos misturados não diz nada sobre nenhum deles
    return {
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "modelos_que_responderam": sorted(por_modelo), "semente": semente, "amostra": len(amostra),
        "universo_de_casos": n_universo,
        "dossie": dossie_usado or DOSSIE_NUMEROS,
        "taxa_acerto_ticker": taxa, "taxa_acerto_ticker_e_ano": hit_a / n,
        "taxa_acerto_ticker_por_modelo": {m: a / k for m, (a, k) in sorted(por_modelo.items())},
        "sem_resposta": invalidas,
        "limite": LIMITE_VAZAMENTO,
        "invalida_etapa_2": inv,
        "casos": linhas,
    }


def corrigir(gabarito: Path, respostas: Path, modelo: str, saida: Path) -> int:
    """Modo sem LLM: pontua respostas dadas fora do app (ex.: ajudante do Claude Code).

    ``respostas`` é uma lista JSON de objetos com ``id`` e os campos do PROMPT.
    """
    gab = json.loads(gabarito.read_text(encoding="utf-8"))
    por_id = {str(r.get("id")): r for r in json.loads(respostas.read_text(encoding="utf-8"))}
    amostra = gab["casos"]
    resps = [por_id.get(str(c["id"]), {}) for c in amostra]
    out = resumo(amostra, resps, [modelo] * len(amostra), gab["universo_de_casos"], gab["semente"],
                 gab.get("dossie"))
    out["canal"] = "fora do app: ajudante sem contexto, um lote de dossiês por chamada"
    saida.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{modelo}: acerto ticker={out['taxa_acerto_ticker']:.1%} "
          f"ticker+ano={out['taxa_acerto_ticker_e_ano']:.1%} sem_resposta={out['sem_resposta']} "
          f"invalida_etapa_2={out['invalida_etapa_2']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--n", type=int, default=60, help="tamanho da amostra")
    ap.add_argument("--semente", type=int, default=20261122)
    ap.add_argument("--dry-run", action="store_true", help="não grava o JSON")
    ap.add_argument("--exportar", type=Path, default=None,
                    help="grava dossies.json (sem resposta) e gabarito.json nesta pasta; não chama LLM")
    ap.add_argument("--corrigir", type=Path, default=None, help="respostas.json a pontuar")
    ap.add_argument("--gabarito", type=Path, default=None)
    ap.add_argument("--modelo", default="desconhecido")
    ap.add_argument("--saida", type=Path, default=CAMINHO)
    ap.add_argument("--com-texto", action="store_true",
                    help="dossiê de números + trechos anonimizados da CVM/IPE (safras 2024-2025)")
    ap.add_argument("--dados-reais-publicos", action="store_true",
                    help="confirma o envio/exportação de dossiês reais (dados públicos da CVM, "
                         "anonimizados); exceção à regra de dados sintéticos do AGENTS.md")
    args = ap.parse_args()

    if args.corrigir:
        return corrigir(args.gabarito, args.corrigir, args.modelo, args.saida)
    if not args.dados_reais_publicos:
        raise SystemExit(
            "este teste monta dossiês com dados reais (públicos, anonimizados) e os envia a "
            "uma LLM externa ou os exporta; o AGENTS.md pede dados sintéticos em prompts. "
            "Passe --dados-reais-publicos para confirmar (ver §21 do guia).")

    import core.llm_b3 as llm
    cadeia_original = llm._provider_chain  # `preparar` desliga a LLM da medição
    from scripts.medir_nota_fatores_b3 import preparar
    prep = preparar(args.cache_dir)
    llm._provider_chain = cadeia_original
    M = prep["M"]
    resultados, precos, params, decisao = prep["rodada"]

    import pandas as pd

    from core.b3_retrato_pit import historico_ate, series_pl_ate
    from core.b3_vigencia import ano_base_do_score

    contexto, por_safra = M._segmentos_por_safra(
        resultados, precos, params, decisao, prep["cost_cfg"], prep["hoje"])
    series_pl = contexto["series_pl"]
    hist_batch = decisao.get("hist_batch") or {}
    cls = decisao["classificacao"]
    setor_de = {str(t).upper(): s for t, s in zip(cls["ticker"], cls["SETOR"])}

    conn, nome_de = None, {}
    if args.com_texto:
        from sqlalchemy import create_engine
        from sqlalchemy import text as sql

        from scripts.publish_fii_selection_from_local import _warehouse_url

        conn = create_engine(_warehouse_url()).connect()  # acervo CVM/IPE inteiro mora aqui
        nome_de = {}  # pela raiz: o cadastro tem uma classe só (CMIG3, não CMIG4)
        for t, n in conn.execute(sql("SELECT ticker, nome_empresa FROM public.setores")).fetchall():
            if n:
                nome_de.setdefault(str(t).upper()[:4], []).append(n)

    casos = []
    for p in por_safra:
        safra = p["safra"]
        if args.com_texto and int(safra) not in SAFRAS_COM_TEXTO:
            continue
        ano_max = ano_base_do_score(safra)
        alvos = {str(t).upper() for t in p["universo"]}
        quadro = p["df_pit"]
        if quadro is None or quadro.empty:
            continue
        hist = historico_ate({tk: df for tk, df in hist_batch.items() if tk in alvos},
                             ano_max, M._aceita_da_safra(safra))
        pl = series_pl_ate({tk: s for tk, s in series_pl.items() if tk in alvos}, ano_max)
        for _, linha in quadro.iterrows():
            tk = str(linha["Ticker"]).upper()
            if tk not in alvos:
                continue
            h = hist.get(tk)
            roic = {}
            if h is not None and not h.empty and "ROIC" in h.columns:
                anos = pd.to_datetime(h["Data"], errors="coerce").dt.year
                roic = {int(a): float(r) for a, r in zip(anos, pd.to_numeric(h["ROIC"], errors="coerce"))
                        if pd.notna(a) and pd.notna(r)}
            lucro = {int(r["ano"]): r.get("lucro_mi") for r in (pl.get(tk) or [])
                     if r.get("ano") is not None and r.get("lucro_mi") is not None}
            texto = dossie(linha, setor_de.get(tk), roic, lucro, ano_max)
            if conn is not None:
                docs = textos_publicos(conn, tk, int(safra))
                if not docs:
                    continue  # sem texto público antes da safra: fora deste teste
                rotulo = {"Press-release": "Release de resultados", "Fato Relevante": "Fato relevante"}
                texto += "\n\nDocumentos publicados antes de abril/N (anonimizados):"
                for tipo, bruto in docs:
                    texto += f"\n\n[{rotulo[tipo]}]\n" + anonimizar(bruto, nome_de.get(tk[:4], []), ano_max)
            casos.append({"ticker": tk, "safra": safra, "ano_n1": ano_max, "texto": texto})

    random.Random(args.semente).shuffle(casos)
    amostra = casos[:args.n]
    print(f"{len(casos)} empresa-safras; amostra de {len(amostra)}", flush=True)
    prompt = PROMPT_TEXTO if args.com_texto else PROMPT
    rotulo_dossie = (DOSSIE_TEXTO if args.com_texto else DOSSIE_NUMEROS)

    if args.exportar:
        # Ids opacos: a ordem não revela nada; o gabarito fica em outro arquivo.
        args.exportar.mkdir(parents=True, exist_ok=True)
        for i, c in enumerate(amostra, 1):
            c["id"] = f"d{i:03d}"
        (args.exportar / "dossies.json").write_text(json.dumps(
            {"prompt": prompt, "dossies": [{"id": c["id"], "texto": c["texto"]} for c in amostra]},
            indent=1, ensure_ascii=False), encoding="utf-8")
        (args.exportar / "gabarito.json").write_text(json.dumps(
            {"semente": args.semente, "universo_de_casos": len(casos), "dossie": rotulo_dossie,
             "casos": [{k: c[k] for k in ("id", "ticker", "safra", "ano_n1")} for c in amostra]},
            indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"exportado em {args.exportar}")
        return 0

    if not llm.llm_disponivel():
        raise SystemExit("nenhum provedor de LLM disponível")
    respostas, modelo_por_caso = [], []
    for i, c in enumerate(amostra, 1):
        # Pelo portão do app (`_chat_complete`): mesma cadeia de provedores.
        txt = llm._chat_complete([{"role": "user", "content": prompt + c["texto"]}],
                                 json_mode=True)
        modelo_por_caso.append(str(llm.ultimo_modelo()))  # a cadeia pode trocar no meio
        resp = parse_resposta(txt)
        respostas.append(resp)
        print(f"{i}/{len(amostra)} {c['ticker']}/{c['safra']} -> {resp.get('ticker')} "
              f"({modelo_por_caso[-1]})", flush=True)

    saida = resumo(amostra, respostas, modelo_por_caso, len(casos), args.semente, rotulo_dossie)
    print(f"acerto ticker={saida['taxa_acerto_ticker']:.1%} "
          f"ticker+ano={saida['taxa_acerto_ticker_e_ano']:.1%} "
          f"sem_resposta={saida['sem_resposta']} modelos={saida['modelos_que_responderam']} "
          f"invalida_etapa_2={saida['invalida_etapa_2']}", flush=True)
    if not args.dry_run:
        args.saida.write_text(json.dumps(saida, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        print(f"gravado em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
