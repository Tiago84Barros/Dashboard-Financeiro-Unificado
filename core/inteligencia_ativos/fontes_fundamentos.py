"""
core/inteligencia_ativos/fontes_fundamentos.py
De onde vêm os fundamentos de cada classe, e como cada fonte vira ``Dado``.

Para cada classe há duas funções:

- ``dados_<classe>(linha)``: pura. Recebe a linha da fonte já lida e devolve
  ``{chave do catálogo: Dado}``. Converte unidade (fração → %) e carrega a
  procedência. Nunca preenche lacuna: métrica sem valor na fonte fica fora
  do dict e sai como "Dado não disponível.".
- ``ler_<classe>(...)``: faz a leitura, sempre por funções já cacheadas do
  projeto (não abre conexão própria), e chama a ``dados_<classe>``.

As fontes por classe estão documentadas em ``docs/fundamentos_fontes.md``.
"""
from __future__ import annotations

import logging
import math
import re

from core.inteligencia_ativos.fundamentos import Dado

logger = logging.getLogger(__name__)


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _pct(x) -> float | None:
    v = _num(x)
    return None if v is None else v * 100.0


def _txt(x) -> str | None:
    t = str(x).strip() if x is not None else ""
    return t or None


def _br(v: float, casas: int = 1) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# -- FII ----------------------------------------------------------------------------

FONTE_FII = "Snapshot da Seleção de FIIs (data/public/fii_selection_snapshot_v2)"

# chave do catálogo → chave do payload do snapshot (para achar a procedência)
_ORIGEM_FII = {
    "p_vp": "pvp", "dividend_yield": "dy_12m", "ocupacao": "vacancia_fisica",
    "vacancia_fisica": "vacancia_fisica",
    "vacancia_financeira": "vacancia_financeira",
    "inadimplencia": "delinquency",
    "concentracao_locatarios": "tenant_concentration",
    "concentracao_geografica": "regions",
    "prazo_contratos": "lease_expiry_concentration_24m",
    "wault": "wault_anos", "emissoes": "shares_outstanding",
    "alavancagem": "leverage", "qualidade_ativos": "property_count",
}

# O snapshot traz o perfil de contrato com a codificação quebrada
# ("At�pico"). Só os rótulos reconhecidos são traduzidos; "-" e "NA"
# são ausência.
_PERFIL_CONTRATO = {"t�pico": "Típico", "at�pico": "Atípico",
                    "típico": "Típico", "atípico": "Atípico",
                    "tipico": "Típico", "atipico": "Atípico"}


def _perfil_contrato(bruto) -> list[str]:
    itens = bruto if isinstance(bruto, (list, tuple)) else [bruto]
    saida: list[str] = []
    for item in itens:
        if not isinstance(item, str):
            continue
        partes = item.split()
        rotulos = [_PERFIL_CONTRATO.get(p.lower()) for p in partes]
        if partes and all(rotulos):
            texto = " + ".join(rotulos)
            if texto not in saida:
                saida.append(texto)
    return saida


def _referencia_fii(linha: dict, chave_payload: str) -> tuple[str | None, str]:
    meta = (linha.get("metric_metadata") or {}).get(chave_payload) or {}
    ref = meta.get("reference_date")
    if chave_payload == "vacancia_fisica" and linha.get("vacancia_ref_date"):
        ref = linha.get("vacancia_ref_date")
    origem = meta.get("source")
    fonte = FONTE_FII + (f" · origem {origem}" if origem else "")
    return (str(ref)[:10] if ref else None), fonte


def dados_fii(linha: dict) -> dict[str, Dado]:
    def dado(chave: str, valor, nota: str | None = None) -> None:
        if valor is None:
            return
        ref, fonte = _referencia_fii(linha, _ORIGEM_FII[chave])
        saida[chave] = Dado(valor, fonte, ref, nota)

    saida: dict[str, Dado] = {}
    dado("p_vp", _num(linha.get("pvp")))
    dado("dividend_yield", _pct(linha.get("dy_12m")))
    vac = _pct(linha.get("vacancia_fisica"))
    if vac is not None:
        dado("ocupacao", 100.0 - vac, "calculada como 100% − vacância física")
    dado("vacancia_fisica", vac)
    dado("vacancia_financeira", _pct(linha.get("vacancia_financeira")))
    dado("inadimplencia", _pct(linha.get("delinquency")))

    loc = _pct(linha.get("tenant_concentration"))
    if loc is not None:
        dado("concentracao_locatarios",
             f"Maior locatário: {_br(loc)}% da receita",
             "a lista de locatários não é publicada, só a participação do maior")

    regioes = linha.get("regions")
    if isinstance(regioes, dict) and regioes:
        pares = sorted(((str(k), _num(v)) for k, v in regioes.items()
                        if _num(v) is not None), key=lambda kv: -kv[1])
        if pares:
            dado("concentracao_geografica",
                 ", ".join(f"{k} {_br(v * 100)}%" for k, v in pares))

    venc = _pct(linha.get("lease_expiry_concentration_24m"))
    perfil = _perfil_contrato(linha.get("contract_profile_text"))
    partes = []
    if venc is not None:
        partes.append(f"{_br(venc)}% dos contratos vencem em até 24 meses")
    if perfil:
        partes.append("perfil " + ", ".join(perfil))
    if partes:
        dado("prazo_contratos", "; ".join(partes))

    dado("wault", _num(linha.get("wault_anos")))

    cotas = _num(linha.get("shares_outstanding"))
    if cotas is not None:
        dado("emissoes", f"{_br(cotas, 0)} cotas em circulação",
             "só o saldo atual; não há histórico de ofertas publicado")

    dado("alavancagem", _pct(linha.get("leverage")),
         "passivo total ÷ ativo total")

    qualidade = []
    n = _num(linha.get("property_count"))
    if n is not None:
        qualidade.append(f"{n:.0f} imóveis")
    div = _num(linha.get("property_diversification"))
    if div is not None:
        qualidade.append(f"diversificação entre imóveis {_br(div, 2)} (0 a 1)")
    cap = _pct(linha.get("implied_cap_rate"))
    if cap is not None:
        qualidade.append(f"cap rate implícito {_br(cap)}%")
    if qualidade:
        dado("qualidade_ativos", "; ".join(qualidade),
             "não há nota de qualidade; estes são os insumos disponíveis")
    return saida


def _linha_por_ticker(frame, ticker: str) -> dict | None:
    if frame is None or getattr(frame, "empty", True) or "ticker" not in frame:
        return None
    achado = frame[frame["ticker"].astype(str).str.upper() == ticker.upper()]
    if achado.empty:
        return None
    return achado.iloc[0].to_dict()


def ler_fii(ticker: str) -> dict[str, Dado]:
    from core.market_read import load_fii_methodology_inputs
    linha = _linha_por_ticker(load_fii_methodology_inputs(), ticker)
    return dados_fii(linha) if linha else {}


# -- renda fixa ---------------------------------------------------------------------
#
# Só o Tesouro Direto tem dado por título (Extrato Analítico em
# ``tesouro_lots`` + curva do Tesouro, via
# ``core.tesouro_posicao.carregar_titulos``). CDB, LCI/LCA, CRI/CRA e
# debêntures entram na carteira só com ticker, nome, valor e custo: não há
# emissor, rating, vencimento nem taxa.

FONTE_TESOURO = "Extrato Analítico do Tesouro (tesouro_lots) + curva do Tesouro"
FONTE_REGRA = "Regra do sistema pela natureza do papel"
FONTE_CARTEIRA = "Nome/ticker da posição na carteira"

_FGC_COBRE = ("CDB", "LCI", "LCA", "LC", "LCD", "RDB", "LIG")
_FGC_NAO_COBRE = ("CRI", "CRA", "DEBENTURE", "DEBÊNTURE", "LF", "FIDC")

_TAXA_ROTULO = {"IPCA": "IPCA + {t}", "SELIC": "Selic + {t}",
                "IGPM": "IGP-M + {t}", "PRE": "{t}"}
_ROTULO_INDEXADOR = {"PRE": "Prefixado", "SELIC": "Selic", "IPCA": "IPCA",
                     "IGPM": "IGP-M"}


def _taxa_txt(indexador: str | None, dec) -> str | None:
    v = _num(dec)
    if v is None:
        return None
    t = f"{_br(v * 100, 2)}% a.a."
    return _TAXA_ROTULO.get(str(indexador or "").upper(), "{t}").format(t=t)


def _familia(chave: str) -> tuple[str, str] | None:
    """``TIPCAJ2032`` → ("TIPCA", "2032"): principal e com-cupom juntos."""
    achado = re.fullmatch(r"(TSELIC|TIPCA|TPRE|TEDUCA|TRENDA|TESOUR)J?(\d{4})",
                          str(chave or "").upper())
    return (achado.group(1), achado.group(2)) if achado else None


def titulo_da_posicao(ticker: str, titulos) -> object | None:
    """O título do Tesouro que corresponde à posição, ou None.

    A carteira colapsa principal e com-cupom num ticker só (``TIPCA2032``);
    o extrato os separa (``TIPCA2032`` / ``TIPCAJ2032``). Ticker igual vence;
    senão, mesma família e ano, e só havendo UM candidato. Com dois, nada é
    escolhido: escolher seria inventar qual título é o da posição.
    """
    t = str(ticker or "").upper()
    exatos = [x for x in titulos if str(x.security_key).upper() == t]
    if len(exatos) == 1:
        return exatos[0]
    fam = _familia(t)
    if fam is None:
        return None
    candidatos = [x for x in titulos if _familia(str(x.security_key)) == fam]
    return candidatos[0] if len(candidatos) == 1 else None


def _palavras(posicao: dict) -> set[str]:
    texto = f"{posicao.get('ticker') or ''} {posicao.get('nome') or ''}".upper()
    return set(re.findall(r"[A-ZÀ-Ú]+", texto))


def dados_renda_fixa(posicao: dict, titulo=None) -> dict[str, Dado]:
    """``posicao``: ticker/nome/classe/moeda. ``titulo``: ``TituloAnalitico``
    do Tesouro já casado com a posição, ou None."""
    from core.inteligencia_ativos import calculos

    saida: dict[str, Dado] = {}
    eh_tesouro = calculos.emissor(posicao) == calculos.TESOURO_NACIONAL
    palavras = _palavras(posicao)

    if eh_tesouro:
        saida["emissor"] = Dado(calculos.TESOURO_NACIONAL, FONTE_REGRA,
                                nota="título público federal")
        saida["risco_credito"] = Dado(
            "Risco soberano (Tesouro Nacional)", FONTE_REGRA,
            nota="classificação pela natureza do emissor; não é rating")
        saida["liquidez"] = Dado(
            "Recompra diária pelo Tesouro Nacional, a preço de mercado",
            FONTE_REGRA)
        saida["cobertura_fgc"] = Dado(
            "Não se aplica: título público não tem cobertura do FGC",
            FONTE_REGRA)
    elif palavras & set(_FGC_NAO_COBRE):
        saida["cobertura_fgc"] = Dado(
            "Sem cobertura do FGC (tipo de papel não coberto)", FONTE_REGRA)
    elif palavras & set(_FGC_COBRE):
        saida["cobertura_fgc"] = Dado(
            "Tipo de papel coberto pelo FGC (até R$ 250 mil por CPF e "
            "instituição)", FONTE_REGRA,
            nota="o emissor não está na carteira, então o limite por "
                 "instituição não pôde ser conferido")

    if titulo is None:
        idx = calculos.indexador(posicao)
        if idx and idx != calculos.NAO_IDENTIFICADO:
            saida["indexador"] = Dado(idx, FONTE_CARTEIRA,
                                      nota="identificado pelo nome do papel")
        return saida

    ref = str(titulo.report_date) if titulo.report_date else None
    curva = str(titulo.data_curva) if titulo.data_curva else None
    idx = str(titulo.indexador or "").upper()
    if idx in _ROTULO_INDEXADOR:
        saida["indexador"] = Dado(_ROTULO_INDEXADOR[idx], FONTE_TESOURO, ref,
                                  "da taxa contratada no extrato")
    if titulo.vencimento:
        saida["vencimento"] = Dado(str(titulo.vencimento)[:10], FONTE_TESOURO,
                                   ref)
        # Duration só onde ela é exata sem modelo: prefixado e IPCA+ sem
        # cupom (Macaulay = prazo). Com cupom, Selic, Renda+ e Educa+ ficam
        # de fora: estimá-la exigiria fluxo e curva que não são publicados.
        fam = _familia(str(titulo.security_key))
        sem_cupom = (fam is not None and fam[0] in ("TPRE", "TIPCA")
                     and "JUROS" not in str(titulo.titulo or "").upper()
                     and not str(titulo.security_key).upper()
                     .startswith(fam[0] + "J"))
        avaliacoes = getattr(titulo, "avaliacoes", None) or []
        if sem_cupom and avaliacoes:
            du = _num(avaliacoes[0].du_restante)
            if du is not None and du >= 0:
                saida["duration"] = Dado(
                    du / 252.0, FONTE_TESOURO,
                    str(avaliacoes[0].data_avaliacao),
                    "título sem cupom: duration de Macaulay = prazo até o "
                    "vencimento (dias úteis ÷ 252)")

    taxas: list[str] = []
    for lote in titulo.lotes:
        tc = lote.taxa_contratada
        txt = _taxa_txt(tc.indexador, tc.valor) if tc is not None else None
        if txt and txt not in taxas:
            taxas.append(txt)
    if taxas:
        saida["taxa_contratada"] = Dado(
            "; ".join(taxas), FONTE_TESOURO, ref,
            f"{len(titulo.lotes)} lotes" if len(titulo.lotes) > 1 else None)

    tm = _taxa_txt(idx, titulo.taxa_mercado_venda)
    if tm:
        saida["taxa_mercado"] = Dado(
            tm, FONTE_TESOURO, curva,
            "taxa de venda (recompra) publicada pelo Tesouro")
    ganho = _num(titulo.ganho_mtm_reais)
    if titulo.marcado_a_mercado and ganho is not None:
        pct = _num(titulo.mtm_pct)
        saida["marcacao_mercado"] = Dado(
            ganho, FONTE_TESOURO, curva,
            "ganho (+) ou perda (−) de marcação sobre o valor na curva do "
            "papel" + (f": {_br(pct * 100, 2)}%" if pct is not None else ""))
    return saida


def _titulos_tesouro() -> list:
    """Títulos do Tesouro do dono da carteira, uma leitura por TTL."""
    from core.config import settings
    owner = getattr(settings, "OWNER_USER_ID", None)
    return _titulos_cache(str(owner)) if owner else []


def _carregar_titulos(owner: str) -> list:
    from core.database import get_engine
    from core.tesouro_posicao import carregar_titulos
    return carregar_titulos(get_engine(), owner)


try:
    import streamlit as st
    _titulos_cache = st.cache_data(ttl=900, show_spinner=False)(
        _carregar_titulos)
except Exception:  # pragma: no cover - contexto sem Streamlit
    _titulos_cache = _carregar_titulos


def ler_renda_fixa(posicao: dict) -> dict[str, Dado]:
    from core.inteligencia_ativos import calculos
    titulo = None
    if calculos.emissor(posicao) == calculos.TESOURO_NACIONAL:
        titulo = titulo_da_posicao(str(posicao.get("ticker") or ""),
                                   _titulos_tesouro())
    return dados_renda_fixa(posicao, titulo)


# -- ETF ----------------------------------------------------------------------------
#
# Nenhuma fonte do projeto traz índice de referência, composição, taxa de
# administração, tracking error nem exposição por dentro do ETF. País e
# moeda da posição dizem onde o ETF é negociado, não o que ele carrega
# (IVVB11 é listado no Brasil e expõe aos EUA), por isso não viram
# "exposição geográfica". Todos os indicadores saem "Dado não disponível.".

def dados_etf(posicao: dict) -> dict[str, Dado]:
    return {}


def ler_etf(posicao: dict) -> dict[str, Dado]:
    return dados_etf(posicao)


# -- ações B3 -----------------------------------------------------------------------
#
# Demonstrações anuais (market.income_statements/balance_sheets/
# cash_flow_statements, R$ absolutos) e métricas calculadas
# (market.calculated_metrics, frações, TTM), pelas leituras cacheadas de
# core.market_read. Não existem na base: margem bruta publicada, EBITDA,
# despesa financeira e guidance. Dívida líquida/EBITDA e cobertura de juros
# vêm, quando a base não as tem, do financialData da brapi publicado no
# arquivo de valuation (ver scripts/publish_valuation_historico.py).

FONTE_B3_DEMO = "Demonstrações anuais B3 (market.income_statements, balance_sheets, cash_flow_statements)"
FONTE_B3_MET = "Métricas calculadas B3 (market.calculated_metrics, 12 meses)"
FONTE_B3_BRAPI = ("brapi financialData e demonstrativo anual "
                  "(data/public/valuation_historico)")
# Nota do indicador sem valor quando o EBITDA é negativo: a avaliação a
# reconhece por este prefixo e levanta o alerta (a razão não tem número).
NOTA_EBITDA_NEGATIVO = "EBITDA de 12 meses negativo"
# Nota do indicador retido porque a dívida líquida da brapi não cabe na
# dívida bruta do balanço da base: a avaliação a reconhece por este prefixo
# e avisa sem eliminar (DIRR3, 10/2026: brapi 6,44x, líquida ~R$ 8 bi contra
# bruta ~R$ 3,5 bi no balanço; incorporadora soma ao totalDebt passivos que
# não são dívida corporativa).
NOTA_FONTES_DIVERGENTES = "Fontes divergentes"
# Folga sobre a dívida bruta do balanço: a foto da brapi é mais nova que o
# exercício anual, e a dívida pode ter crescido nesse meio tempo. Líquida
# acima de 1,5× a bruta anual já não é crescimento, é outra definição.
FOLGA_DIVIDA_BRAPI = 1.5


def _crescimento(atual, anterior) -> float | None:
    a, b = _num(atual), _num(anterior)
    if a is None or b is None or b <= 0:
        return None  # base nula ou negativa: a taxa não tem leitura
    return (a / b - 1.0) * 100.0


def _divergencia_brapi(dle: float | None, ebitda: float | None,
                       bruta: Dado | None) -> str | None:
    """Nota de divergência quando a dívida líquida implícita na razão da
    brapi (razão × EBITDA de 12 meses) passa da dívida bruta do balanço da
    base com folga: líquida maior que bruta não existe, então as duas fontes
    não medem a mesma dívida e a razão não pode eliminar ninguém. Sem
    EBITDA ou sem bruta positiva não há como conferir, e nada muda."""
    b = _num(bruta.valor) if bruta is not None else None
    if dle is None or ebitda is None or ebitda <= 0 or b is None or b <= 0:
        return None
    liquida = dle * ebitda
    if liquida <= FOLGA_DIVIDA_BRAPI * b:
        return None
    return (f"{NOTA_FONTES_DIVERGENTES}: a razão da brapi ({_br(dle, 2)}x) "
            f"implica dívida líquida de R$ {_br(liquida / 1e9, 2)} bi, acima "
            f"da dívida bruta de R$ {_br(b / 1e9, 2)} bi do balanço "
            f"({bruta.referencia}); as fontes não medem a mesma dívida e o "
            "indicador fica retido até a conferência")


def dados_acao_b3(demonstracoes, multiplos,
                  alavancagem: dict | None = None) -> dict[str, Dado]:
    """``demonstracoes``: DataFrame anual de ``load_demonstracoes`` (colunas
    PT, ``Data`` = 31/12). ``multiplos``: Series de ``load_multiplos``.
    ``alavancagem``: o item do ticker no arquivo de valuation (chaves
    ``divida_liquida_ebitda``, ``cobertura_juros``...), só preenche o que a
    base contábil não tem."""
    saida: dict[str, Dado] = {}
    if demonstracoes is not None and not getattr(demonstracoes, "empty", True):
        df = demonstracoes.sort_values("Data")
        ult = df.iloc[-1]
        ano = int(ult["Data"].year)
        ref = f"exercício {ano}"

        def put(chave, valor, nota=None, ref_=ref):
            if valor is not None:
                saida[chave] = Dado(valor, FONTE_B3_DEMO, ref_, nota)

        put("receita", _num(ult.get("Receita_Liquida")))
        put("lucro_liquido", _num(ult.get("Lucro_Liquido")))
        put("fluxo_caixa_operacional", _num(ult.get("FCO")))
        put("fluxo_caixa_livre", _num(ult.get("FCF")))
        put("divida_bruta", _num(ult.get("Divida_Total")))
        put("divida_liquida", _num(ult.get("Divida_Liquida")))
        dl, ebitda = _num(ult.get("Divida_Liquida")), _num(ult.get("EBITDA"))
        if dl is not None and ebitda is not None and ebitda > 0:
            put("divida_liquida_ebitda", dl / ebitda,
                "calculada: dívida líquida ÷ EBITDA do mesmo exercício")
        bruta, pl = _num(ult.get("Divida_Total")), _num(ult.get("Patrimonio_Liquido"))
        if bruta is not None and pl is not None and pl > 0:
            put("divida_bruta_patrimonio", bruta / pl,
                "calculada: dívida bruta ÷ patrimônio líquido do mesmo exercício")
        if len(df) >= 2:
            ant = df.iloc[-2]
            if int(ant["Data"].year) == ano - 1:
                ref_g = f"{ano} vs {ano - 1}"
                put("crescimento_receita",
                    _crescimento(ult.get("Receita_Liquida"),
                                 ant.get("Receita_Liquida")),
                    "variação anual", ref_g)
                put("crescimento_lucro",
                    _crescimento(ult.get("Lucro_Liquido"),
                                 ant.get("Lucro_Liquido")),
                    "variação anual; sem valor quando o lucro anterior é "
                    "nulo ou negativo", ref_g)

    if multiplos is not None and not getattr(multiplos, "empty", True):
        data = multiplos.get("data")
        ref_m = str(data)[:10] if data is not None and str(data) != "NaT" else None
        for chave, coluna, nota in (
                ("margem_ebit", "Margem_Operacional", "margem operacional"),
                ("margem_liquida", "Margem_Liquida", None),
                ("roe", "ROE", None),
                ("roic", "ROIC", "antes de impostos: EBIT ÷ (patrimônio "
                                 "líquido + dívida bruta − caixa)"),
                ("payout", "Payout", None),
                ("dividend_yield", "DY", "proventos de 12 meses ÷ preço, como "
                                         "gravado na base")):
            v = _pct(multiplos.get(coluna))
            if v is not None:
                saida[chave] = Dado(v, FONTE_B3_MET, ref_m, nota)
        # Dív/PL: o mesmo número que a Empresas B3 mede contra o teto do
        # segmento; tem precedência sobre o calculado do balanço.
        endiv = _num(multiplos.get("Endividamento_Total"))
        if endiv is not None and endiv >= 0:
            saida["divida_bruta_patrimonio"] = Dado(
                endiv, FONTE_B3_MET, ref_m,
                "dívida bruta ÷ patrimônio líquido, o mesmo da Empresas B3")

    al = alavancagem or {}
    ref_al = _txt(al.get("alavancagem_ref"))
    dl_base = saida.get("divida_liquida")
    ebitda_al = _num(al.get("ebitda_12m"))
    if "divida_liquida_ebitda" not in saida and dl_base is not None \
            and ebitda_al is not None and ebitda_al > 0:
        # A dívida é a do balanço da base, a mesma que a Empresas B3 e o
        # chat leem; da brapi vem só o EBITDA. A razão pronta da brapi mede
        # outra dívida em parte das empresas (DIRR3: 6,44x contra ~1,4x).
        saida["divida_liquida_ebitda"] = Dado(
            dl_base.valor / ebitda_al, f"{FONTE_B3_DEMO} + {FONTE_B3_BRAPI}",
            f"dívida do {dl_base.referencia}; EBITDA de 12 meses"
            + (f" ({ref_al})" if ref_al else ""),
            "calculada: dívida líquida do balanço ÷ EBITDA de 12 meses do "
            "provedor (a base não grava o EBITDA)")
    if "divida_liquida_ebitda" not in saida:
        dle = _num(al.get("divida_liquida_ebitda"))
        divergencia = _divergencia_brapi(dle, _num(al.get("ebitda_12m")),
                                         saida.get("divida_bruta"))
        if divergencia is not None:
            saida["divida_liquida_ebitda"] = Dado(
                None, FONTE_B3_BRAPI, ref_al, divergencia)
        elif dle is not None:
            saida["divida_liquida_ebitda"] = Dado(
                dle, FONTE_B3_BRAPI, ref_al,
                "(dívida total − caixa) ÷ EBITDA de 12 meses, os três da "
                "mesma foto; EBITDA do provedor, não o ajustado da empresa")
        elif al.get("ebitda_negativo"):
            saida["divida_liquida_ebitda"] = Dado(
                None, FONTE_B3_BRAPI, ref_al,
                f"{NOTA_EBITDA_NEGATIVO}: a razão não tem leitura, e a "
                "operação não gera caixa para a dívida")
    if "cobertura_juros" not in saida:
        cob = _num(al.get("cobertura_juros"))
        if cob is not None:
            saida["cobertura_juros"] = Dado(
                cob, FONTE_B3_BRAPI, _txt(al.get("cobertura_ref")) or ref_al,
                "EBIT ÷ despesas financeiras do exercício; a despesa inclui "
                "variação cambial e monetária, então sai conservadora")
    return saida


def ler_acao_b3(ticker: str) -> dict[str, Dado]:
    from core import market_read
    from core.inteligencia_ativos import fontes_valuation
    alav = ((fontes_valuation.arquivo().get("b3") or {})
            .get(str(ticker or "").upper()))
    return dados_acao_b3(market_read.load_demonstracoes(ticker),
                         market_read.load_multiplos(ticker), alav)


# -- ações EUA ----------------------------------------------------------------------
#
# Vitrine market_us.company_snapshots: ``metrics`` (último exercício anual,
# frações e USD) e ``financials`` (série anual, USD). Guidance não existe.

FONTE_EUA = "Vitrine Empresas Americanas (market_us.company_snapshots, SEC/EDGAR)"


def dados_acao_eua(metricas: dict | None, financials) -> dict[str, Dado]:
    saida: dict[str, Dado] = {}
    ref = None
    ult = None
    if financials is not None and not getattr(financials, "empty", True) \
            and "fiscal_year" in financials:
        serie = financials.dropna(subset=["fiscal_year"]).sort_values(
            "fiscal_year")
        if not serie.empty:
            ult = serie.iloc[-1]
            ref = f"exercício {int(ult['fiscal_year'])}"
    m_ = metricas or {}

    def put(chave, valor, nota=None):
        if valor is not None:
            saida[chave] = Dado(valor, FONTE_EUA, ref, nota)

    put("receita", _num(m_.get("_revenue")))
    put("lucro_liquido", _num(m_.get("_net_income")))
    put("crescimento_receita", _pct(m_.get("revenue_cagr_3y")),
        "crescimento anual composto em 3 anos")
    put("crescimento_lucro", _pct(m_.get("eps_growth_3y")),
        "crescimento do lucro por ação em 3 anos")
    put("margem_bruta", _pct(m_.get("gross_margin")))
    put("margem_ebit", _pct(m_.get("operating_margin")), "margem operacional")
    put("margem_liquida", _pct(m_.get("net_margin")))
    put("roe", _pct(m_.get("roe")))
    put("roic", _pct(m_.get("roic")),
        "depois de impostos (NOPAT ÷ capital investido)")
    put("fluxo_caixa_livre", _num(m_.get("_fcf")))
    put("divida_liquida", _num(m_.get("_net_debt")))
    put("divida_liquida_ebitda", _num(m_.get("net_debt_ebitda")))
    put("divida_bruta_patrimonio", _num(m_.get("debt_to_equity")),
        "dívida total ÷ patrimônio líquido")
    put("cobertura_juros", _num(m_.get("interest_coverage")),
        "EBIT ÷ despesa de juros")
    put("payout", _pct(m_.get("payout_ratio")))
    put("dividend_yield", _pct(m_.get("dividend_yield")),
        "dividendos pagos no exercício ÷ valor de mercado")
    if ult is not None:
        put("fluxo_caixa_operacional", _num(ult.get("operating_cash_flow")))
        put("divida_bruta", _num(ult.get("total_debt")))
    return saida


def ler_acao_eua(ticker: str) -> dict[str, Dado]:
    from core import us_data
    return dados_acao_eua(us_data.company_metrics(ticker),
                          us_data.company_financials(ticker))


# -- entrada única ------------------------------------------------------------------

def ler(ticker: str, nome: str, classe: str, moeda: str):
    """Fundamentos do ativo no catálogo da sua classe."""
    from core.inteligencia_ativos import fundamentos as f
    tipo = f.tipo_do_ativo(classe, moeda)
    moeda = (moeda or "BRL").upper()
    posicao = {"ticker": ticker, "nome": nome, "classe": classe,
               "moeda": moeda}
    if tipo == f.ACAO:
        dados = ler_acao_b3(ticker) if moeda == "BRL" else ler_acao_eua(ticker)
    elif tipo == f.FII:
        dados = ler_fii(ticker)
    elif tipo == f.RENDA_FIXA:
        dados = ler_renda_fixa(posicao)
    elif tipo == f.ETF:
        dados = ler_etf(posicao)
    else:
        dados = {}
    return f.montar(tipo, dados, moeda=moeda)
