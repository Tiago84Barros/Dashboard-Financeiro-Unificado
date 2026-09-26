"""
core/inteligencia_ativos/fontes_valuation.py
Leitores do valuation e do universo de pares, por mercado. Única parte com
I/O das duas camadas; ``valuation`` e ``pares`` são puros.

De onde vem cada número:

- Ações B3: valor atual de ``load_multiplos_todos`` (vitrine no Supabase),
  taxonomia de ``load_setores``; histórico anual, porte e volatilidade do
  arquivo ``data/public/valuation_historico.json.gz``, gerado do armazém
  local por ``scripts/publish_valuation_historico.py`` (o histórico gravado
  na vitrine mistura preço retroajustado com LPA publicado; ver o script).
- FII: valor atual e atributos de ``load_fii_methodology_inputs``; histórico
  mensal de P/VP e DY do mesmo arquivo.
- Ações EUA: tudo do arquivo (retrato de ``market_us.company_snapshots`` e
  histórico anual), com o SIC da SEC como modelo de negócio.
- Tesouro Direto: ``tesouro_market_rates`` (última taxa de cada título e a
  trajetória diária do título do ativo).
- Outra renda fixa e ETF: sem cotação pública; sai o motivo.

Os construtores (``candidatos_*``, ``entradas_*``) são puros e testáveis;
os ``_carregar_*`` fazem a leitura.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import re
from datetime import date, timedelta
from pathlib import Path

from core.inteligencia_ativos import pares as p
from core.inteligencia_ativos import valuation as v
from core.inteligencia_ativos.fundamentos import (
    ACAO,
    ETF,
    FII,
    RENDA_FIXA,
    Dado,
    tipo_do_ativo,
)

logger = logging.getLogger(__name__)

ARQUIVO = Path(__file__).resolve().parents[2] / "data" / "public" / \
    "valuation_historico.json.gz"

FONTE_MULTIPLOS_B3 = "Vitrine de múltiplos B3 (Supabase)"
FONTE_FII = "Vitrine de FIIs (retrato da metodologia)"
FONTE_HISTORICO = "Histórico de valuation (armazém local, arquivo publicado)"
FONTE_EUA = "Retrato de Empresas Americanas (SEC + preço)"
FONTE_TESOURO = "Tesouro Direto (taxas publicadas)"
AVISO_VITRINE_FII_INDISPONIVEL = "Vitrine de FIIs indisponível nesta leitura"

# SUBSETOR da B3 cujo EV/EBIT não tem leitura (dívida é matéria-prima)
SUBSETORES_FINANCEIROS = frozenset({"Intermediários Financeiros",
                                    "Previdência e Seguros"})
# rótulos de segmento de FII que não dizem nada do negócio
SEGMENTOS_FII_VAGOS = frozenset({"Multicategoria", "Outros", "-", "",
                                 "Setor1"})
# grafias do mesmo segmento no cadastro
SINONIMOS_SEGMENTO_FII = {"Logístico": "Logística",
                          "Operador Logístico": "Logística",
                          "Comércio Varejista": "Varejo"}


def _num(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _pct(x) -> float | None:
    f = _num(x)
    return None if f is None else f * 100.0


def _positivo(x) -> float | None:
    f = _num(x)
    return f if f is not None and f > 0 else None


def _txt(x) -> str | None:
    s = str(x).strip() if x is not None else ""
    return None if s in ("", "nan", "None") else s


def _serie(historico: dict | None, chave: str) -> tuple:
    return tuple((str(r), val) for r, val in (historico or {}).get(chave) or ())


def _ano(x) -> int | None:
    """Ano de ``date``/``Timestamp``/"exercício 2025"/"2025-12-31"."""
    if hasattr(x, "year"):
        try:
            return int(x.year)
        except (TypeError, ValueError):
            return None
    achado = re.search(r"(19|20)\d{2}", str(x or ""))
    return int(achado.group(0)) if achado else None


def _raiz_b3(ticker: str) -> str:
    return str(ticker or "").upper()[:4]


# -- arquivo publicado ---------------------------------------------------------------

def _ler_arquivo(caminho: str) -> dict:
    try:
        with gzip.open(caminho, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except FileNotFoundError:
        logger.info("[valuation] arquivo de histórico ausente: %s", caminho)
    except Exception as exc:  # arquivo corrompido não derruba a seção
        logger.warning("[valuation] arquivo de histórico ilegível: %s",
                       type(exc).__name__)
    return {}


def _cache(fn):
    try:
        import streamlit as st
        return st.cache_data(ttl=900, show_spinner=False)(fn)
    except Exception:  # pragma: no cover - contexto sem Streamlit
        return fn


_arquivo_cache = _cache(_ler_arquivo)


def arquivo() -> dict:
    return _arquivo_cache(str(ARQUIVO))


def _premissas_arquivo(art: dict, mercado: str) -> tuple[str, ...]:
    if not art:
        return ("Histórico indisponível: o arquivo publicado de valuation "
                "não foi encontrado; só há valor atual e pares.",)
    saida = []
    metodo = (art.get("metodo") or {}).get(mercado)
    if metodo:
        saida.append(f"Método do histórico: {metodo}")
    if art.get("gerado_em"):
        saida.append(f"Histórico gerado em {str(art['gerado_em'])[:10]}.")
    vol = (art.get("metodo") or {}).get("volatilidade")
    if vol:
        saida.append(f"Volatilidade (risco na seleção de pares): {vol}")
    return tuple(saida)


# -- B3 ------------------------------------------------------------------------------

def _ref_multiplos(linha: dict) -> str | None:
    d = linha.get("data")
    try:
        return f"exercício {d.year}" if hasattr(d, "year") else _txt(d)
    except Exception:
        return None


def candidatos_b3(multiplos: list[dict], setores: dict[str, dict],
                  art_b3: dict) -> dict[str, p.Candidato]:
    saida = {}
    for m in multiplos:
        tk = str(m.get("Ticker") or "").upper()
        if not tk:
            continue
        s = setores.get(tk) or {}
        a = art_b3.get(tk) or {}
        financeira = s.get("SUBSETOR") in SUBSETORES_FINANCEIROS
        porte, vol = _positivo(a.get("porte")), _positivo(a.get("volatilidade"))
        metricas = {
            "p_l": _positivo(m.get("P/L")), "p_vp": _positivo(m.get("P/VP")),
            "dividend_yield": _pct(m.get("DY")),
            "ev_ebit": None if financeira else _positivo(m.get("EV_EBIT")),
            "roe": _pct(m.get("ROE")),
            "margem_liquida": _pct(m.get("Margem_Liquida")),
            "volatilidade": vol, "porte": porte,
        }
        saida[tk] = p.Candidato(
            tk, _txt(s.get("nome_empresa")), ACAO, "B3",
            (("segmento", _txt(s.get("SEGMENTO"))),
             ("subsetor", _txt(s.get("SUBSETOR"))),
             ("setor", _txt(s.get("SETOR")))),
            porte, vol, "volatilidade",
            {k: x for k, x in metricas.items() if x is not None}, _raiz_b3(tk),
            _ano(m.get("data")))
    return saida


def entradas_b3(linha: dict | None, art: dict | None,
                financeira: bool) -> dict[str, v.Entrada]:
    linha, art = linha or {}, art or {}
    hist = art.get("historico") or {}
    ref = _ref_multiplos(linha)

    def entrada(chave, bruto, *, pct=False, excl_nao_positivo=None,
                excluida=None):
        if excluida:
            return v.Entrada(excluida=excluida)
        val = _num(bruto)
        if val is not None and pct:
            val *= 100.0
        if excl_nao_positivo and val is not None and val <= 0:
            return v.Entrada(excluida=excl_nao_positivo)
        serie = _serie(hist, chave)
        return v.Entrada(Dado(val, FONTE_MULTIPLOS_B3, ref) if val is not None
                         else None, serie, v.ANUAL,
                         FONTE_HISTORICO if serie else None)

    return {
        "p_l": entrada("p_l", linha.get("P/L"), excl_nao_positivo="lucro_negativo"),
        "p_vp": entrada("p_vp", linha.get("P/VP"),
                        excl_nao_positivo="patrimonio_negativo"),
        "dividend_yield": entrada("dividend_yield", linha.get("DY"), pct=True),
        "ev_ebit": entrada("ev_ebit", linha.get("EV_EBIT"),
                           excl_nao_positivo="ebit_negativo",
                           excluida="financeira" if financeira else None),
    }


def _carregar_b3() -> tuple[list[dict], dict[str, dict]]:
    from core import market_read
    mult = market_read.load_multiplos_todos()
    setores = market_read.load_setores()
    return (mult.to_dict("records") if mult is not None else [],
            {str(r["ticker"]).upper(): r for r in setores.to_dict("records")}
            if setores is not None and not setores.empty else {})


# -- FII -----------------------------------------------------------------------------

def _taxonomia_fii(linha: dict) -> tuple:
    tipo = _txt(linha.get("tipo"))
    setor = _txt(linha.get("sector"))
    setor = SINONIMOS_SEGMENTO_FII.get(setor, setor)
    mandato = _txt(linha.get("mandate"))
    return (("tipo e segmento declarado", f"{tipo} · {setor}"
             if tipo and setor and setor not in SEGMENTOS_FII_VAGOS else None),
            ("tipo e mandato", f"{tipo} · {mandato}" if tipo and mandato else None),
            ("tipo", tipo))


def candidatos_fii(linhas: list[dict]) -> dict[str, p.Candidato]:
    saida = {}
    for r in linhas:
        tk = str(r.get("ticker") or "").upper()
        if not tk:
            continue
        queda = _num(r.get("max_drawdown"))
        risco = abs(queda) * 100.0 if queda is not None and queda != 0 else None
        porte = _positivo(r.get("patrimonio_liquido"))
        metricas = {
            "p_vp": _positivo(r.get("pvp")), "dividend_yield": _pct(r.get("dy_12m")),
            "cap_rate": _pct(r.get("implied_cap_rate")),
            "alavancagem": _pct(r.get("leverage")), "max_drawdown": risco,
            "liquidez_diaria": _num(r.get("liquidez_diaria")), "porte": porte,
        }
        saida[tk] = p.Candidato(
            tk, _txt(r.get("name")), FII, "B3", _taxonomia_fii(r), porte, risco,
            "maior queda", {k: x for k, x in metricas.items() if x is not None},
            _raiz_b3(tk))
    return saida


def _ref_fii(linha: dict) -> str | None:
    meta = linha.get("snapshot_metadata")
    if isinstance(meta, dict):
        return _txt(meta.get("reference_date"))
    return None


def entradas_fii(linha: dict | None, art: dict | None) -> dict[str, v.Entrada]:
    linha, art = linha or {}, art or {}
    hist = art.get("historico") or {}
    ref = _ref_fii(linha)

    def entrada(chave, val):
        serie = _serie(hist, chave)
        return v.Entrada(Dado(val, FONTE_FII, ref) if val is not None else None,
                         serie, v.MENSAL, FONTE_HISTORICO if serie else None)

    pvp = _num(linha.get("pvp"))
    saida = {
        "p_vp": (v.Entrada(excluida="patrimonio_negativo")
                 if pvp is not None and pvp <= 0 else entrada("p_vp", pvp)),
        "dividend_yield": entrada("dividend_yield", _pct(linha.get("dy_12m"))),
    }
    tipo = _txt(linha.get("tipo"))
    # tipo desconhecido não prova que o fundo não é de tijolo
    if tipo is not None and tipo != "tijolo":
        saida["cap_rate"] = v.Entrada(excluida="fii_nao_tijolo")
    else:
        saida["cap_rate"] = entrada("cap_rate", _pct(linha.get("implied_cap_rate")))
    return saida


def _carregar_fii() -> tuple[list[dict], str | None]:
    """(linhas, falha). ``falha`` é a frase da causa quando a vitrine não leu.

    Sem ela a falha de leitura virava "Dado não disponível" no P/VP e "Ativo
    fora do universo" nos pares, e o fundo parecia sem dado.
    """
    from core.market_read import (causa_falha_vitrine_fii,
                                  load_fii_methodology_inputs)
    frame = load_fii_methodology_inputs()
    erro = frame.attrs.get("load_error") if frame is not None else "sem_quadro"
    if erro or frame.empty:
        causa = causa_falha_vitrine_fii(erro)
        return [], f"{causa} ({erro or 'universo vazio'})"
    return frame.to_dict("records"), None


# -- EUA -----------------------------------------------------------------------------

def financeira_sic(sic: str | None) -> bool:
    """Bancos, crédito, corretoras e seguradoras (SIC 6000–6411)."""
    try:
        return 6000 <= int(str(sic)) <= 6411
    except (TypeError, ValueError):
        return False


def _taxonomia_sic(sic: str | None, descricao: str | None) -> tuple:
    s = _txt(sic)
    if not s or not s.isdigit():
        return (("SIC de 4 dígitos", None), ("SIC de 3 dígitos", None),
                ("SIC de 2 dígitos", None))
    s = s.zfill(4)
    return (("SIC de 4 dígitos", f"{s} {descricao}" if descricao else s),
            ("SIC de 3 dígitos", f"{s[:3]}x"), ("SIC de 2 dígitos", f"{s[:2]}xx"))


def candidatos_eua(art_eua: dict) -> dict[str, p.Candidato]:
    saida = {}
    for sym, a in (art_eua or {}).items():
        atual = a.get("atual") or {}
        porte, vol = _positivo(a.get("porte")), _positivo(a.get("volatilidade"))
        financeira = financeira_sic(a.get("sic"))
        metricas = {
            "p_l": _positivo(atual.get("p_l")), "p_vp": _positivo(atual.get("p_vp")),
            "dividend_yield": _num(atual.get("dividend_yield")),
            "ev_ebit": None if financeira else _positivo(atual.get("ev_ebit")),
            "roe": _num(atual.get("roe")),
            "margem_liquida": _num(atual.get("margem_liquida")),
            "volatilidade": vol, "porte": porte,
        }
        saida[str(sym).upper()] = p.Candidato(
            str(sym).upper(), _txt(a.get("nome")), ACAO, "EUA",
            _taxonomia_sic(a.get("sic"), _txt(a.get("sic_descricao"))),
            porte, vol, "volatilidade",
            {k: x for k, x in metricas.items() if x is not None},
            _txt(a.get("cik")) or str(sym).upper(), _ano(a.get("atual_ref")))
    return saida


def entradas_eua(art: dict | None) -> dict[str, v.Entrada]:
    art = art or {}
    atual, hist = art.get("atual") or {}, art.get("historico") or {}
    ref = _txt(art.get("atual_ref"))

    def entrada(chave):
        val = _num(atual.get(chave))
        serie = _serie(hist, chave)
        return v.Entrada(Dado(val, FONTE_EUA, ref) if val is not None else None,
                         serie, v.ANUAL, FONTE_HISTORICO if serie else None)

    saida = {c: entrada(c) for c in ("p_l", "p_vp", "dividend_yield", "ev_ebit")}
    if financeira_sic(art.get("sic")):
        saida["ev_ebit"] = v.Entrada(excluida="financeira")
    return saida


# -- Tesouro Direto ------------------------------------------------------------------

_PREFIXO = re.compile(r"(TSELIC|TIPCAJ|TIPCA|TPREJ|TPRE|TEDUCA|TRENDA|TESOUR)"
                      r"(\d{4})")
NOTA_TAXA = {
    "TSELIC": "spread sobre a Selic, não a taxa cheia",
    "TIPCA": "taxa real, acima do IPCA",
    "TPRE": "taxa nominal prefixada",
    "TEDUCA": "taxa real, acima do IPCA",
    "TRENDA": "taxa real, acima do IPCA",
    "TESOUR": "taxa real, acima do IPCA",
}


def _partes_tesouro(chave: str) -> tuple[str, str] | None:
    """``TIPCAJ2032`` → ("TIPCAJ", "TIPCA")."""
    achado = _PREFIXO.fullmatch(str(chave or "").upper())
    if not achado:
        return None
    papel = achado.group(1)
    return papel, papel.rstrip("J") if papel in ("TIPCAJ", "TPREJ") else papel


def _anos_ate(vencimento, hoje: date) -> float | None:
    try:
        d = vencimento if isinstance(vencimento, date) else \
            date.fromisoformat(str(vencimento)[:10])
    except ValueError:
        return None
    anos = (d - hoje).days / 365.25
    return round(anos, 2) if anos > 0 else None


def candidatos_tesouro(taxas: list[dict], hoje: date) -> dict[str, p.Candidato]:
    saida = {}
    for r in taxas:
        chave = str(r.get("security_key") or "").upper()
        partes = _partes_tesouro(chave)
        if not partes:
            continue
        prazo = _anos_ate(r.get("maturity_date"), hoje)
        metricas = {"taxa_mercado": _num(r.get("sell_rate")), "prazo": prazo}
        saida[chave] = p.Candidato(
            chave, _txt(r.get("title_name")), RENDA_FIXA, "Tesouro Direto",
            (("papel", partes[0]), ("família", partes[1])), None, prazo,
            "prazo", {k: x for k, x in metricas.items() if x is not None})
    return saida


def chave_tesouro(ticker: str, chaves) -> str | None:
    """Título do ativo: chave igual; senão mesma família e ano com UM só
    candidato (dois seria escolher por conta própria)."""
    t = str(ticker or "").upper()
    if t in chaves:
        return t
    partes = _partes_tesouro(t)
    ano = t[-4:]
    if not partes:
        return None
    cand = [c for c in chaves if c.endswith(ano)
            and (_partes_tesouro(c) or (None, None))[1] == partes[1]]
    return cand[0] if len(cand) == 1 else None


def entradas_tesouro(chave: str, linha: dict | None,
                     serie: list[tuple]) -> dict[str, v.Entrada]:
    linha = linha or {}
    partes = _partes_tesouro(chave)
    nota = NOTA_TAXA.get(partes[1]) if partes else None
    val = _num(linha.get("sell_rate"))
    ref = _txt(linha.get("base_date"))
    return {"taxa_mercado": v.Entrada(
        Dado(val, FONTE_TESOURO, ref, nota) if val is not None else None,
        tuple((str(r), x) for r, x in serie), v.DIARIA,
        FONTE_TESOURO if serie else None)}


def _carregar_taxas_tesouro() -> list[dict]:
    from core.database import get_engine
    from core.tesouro_curva import taxas_mais_recentes
    taxas = taxas_mais_recentes(get_engine())
    return taxas.to_dict("records") if taxas is not None else []


def _carregar_serie_tesouro(chave: str) -> list[tuple]:
    from core.database import get_engine
    from core.tesouro_curva import serie_titulo
    s = serie_titulo(get_engine(), chave,
                     desde=date.today() - timedelta(days=3 * 366))
    if s is None or s.empty:
        return []
    return [(str(r["base_date"])[:10], _num(r["sell_rate"]))
            for r in s.to_dict("records")]


# -- entrada única -------------------------------------------------------------------

def _montar(tipo, moeda, entradas, alvo, universo, premissas_fonte,
            motivo_sem_pares=None):
    if alvo is None:
        grupo = p.GrupoPares(motivo=motivo_sem_pares or
                             "Ativo fora do universo com dados de comparação.")
    else:
        grupo = p.selecionar(alvo, universo)
    valores = {m.chave: grupo.valores(m.chave) for m in v.CATALOGO.get(tipo, ())}
    val = v.montar(tipo, entradas, pares=valores, grupo_pares=grupo.descricao,
                   moeda=moeda, premissas_fonte=premissas_fonte)
    comp = (p.comparar(alvo, grupo, moeda=moeda) if alvo is not None
            else p.ComparacaoPares("", tipo, moeda, grupo, (), grupo.motivo))
    return val, comp


def ler_sem_cache(ticker: str, nome: str, classe: str, moeda: str):
    """(Valuation, ComparacaoPares) do ativo."""
    from core.inteligencia_ativos import calculos
    tipo = tipo_do_ativo(classe, moeda)
    moeda = (moeda or "BRL").upper()
    tk = str(ticker or "").upper()

    if tipo == ACAO and moeda == "BRL":
        mult, setores = _carregar_b3()
        art = arquivo()
        universo = candidatos_b3(mult, setores, art.get("b3") or {})
        linha = next((m for m in mult if str(m.get("Ticker")).upper() == tk), None)
        financeira = (setores.get(tk) or {}).get("SUBSETOR") in SUBSETORES_FINANCEIROS
        alvo = universo.get(tk)
        if alvo is None and (tk in setores or tk in (art.get("b3") or {})):
            alvo = candidatos_b3([{"Ticker": tk}], setores, art.get("b3") or {})[tk]
        return _montar(tipo, moeda,
                       entradas_b3(linha, (art.get("b3") or {}).get(tk), financeira),
                       alvo, list(universo.values()),
                       (f"Valor atual: {FONTE_MULTIPLOS_B3} (múltiplos do "
                        "último exercício publicado a preço recente); o "
                        "histórico usa o fechamento de cada fim de ano.",)
                       + _premissas_arquivo(art, "b3"))
    if tipo == ACAO:
        art = arquivo()
        eua = art.get("eua") or {}
        universo = candidatos_eua(eua)
        return _montar(tipo, moeda, entradas_eua(eua.get(tk)), universo.get(tk),
                       list(universo.values()),
                       (f"Valor atual: {FONTE_EUA}; último exercício fiscal "
                        "a preço do retrato.",) + _premissas_arquivo(art, "eua"),
                       "Ação fora do universo americano coberto (só ações, "
                       "sem REIT).")
    if tipo == FII:
        linhas, falha = _carregar_fii()
        art = arquivo()
        universo = candidatos_fii(linhas)
        linha = next((r for r in linhas if str(r.get("ticker")).upper() == tk), None)
        aviso = ((f"{AVISO_VITRINE_FII_INDISPONIVEL}: {falha}. P/VP, "
                  "DY e cap rate atuais e o universo de pares dependem dela; "
                  "onde aparecer \"Dado não disponível\", a causa é a falha "
                  "da leitura, não o fundo.",) if falha else ())
        return _montar(tipo, moeda,
                       entradas_fii(linha, (art.get("fii") or {}).get(tk)),
                       universo.get(tk), list(universo.values()),
                       aviso + (f"Valor atual: {FONTE_FII}.",
                        "Segmento de FII é o declarado pelo próprio fundo no "
                        "cadastro e pode divergir do portfólio (HGLG11, "
                        "logístico, aparece como Varejo); \"Multicategoria\" "
                        "e \"Outros\" não servem de modelo de negócio e o "
                        "grupo sobe para tipo e mandato.")
                       + _premissas_arquivo(art, "fii"),
                       f"Sem pares: {falha}." if falha else None)
    if tipo == RENDA_FIXA:
        posicao = {"ticker": ticker, "nome": nome, "classe": classe,
                   "moeda": moeda}
        if calculos.emissor(posicao) != calculos.TESOURO_NACIONAL:
            val = v.montar(tipo, {"taxa_mercado": v.Entrada(
                excluida="fora_do_tesouro")}, moeda=moeda)
            grupo = p.GrupoPares(motivo="Renda fixa fora do Tesouro Direto não "
                                        "tem cotação pública para comparar.")
            return val, p.ComparacaoPares(tk, tipo, moeda, grupo, (),
                                          grupo.motivo)
        linhas = _carregar_taxas_tesouro()
        chaves = {str(r.get("security_key")).upper() for r in linhas}
        chave = chave_tesouro(tk, chaves)
        if chave is None:
            # o extrato do dono desfaz a ambiguidade principal × com-cupom
            from core.inteligencia_ativos import fontes_fundamentos as ff
            titulo = ff.titulo_da_posicao(tk, ff._titulos_tesouro())
            k = str(getattr(titulo, "security_key", "") or "").upper()
            chave = k if k in chaves else None
        serie = _carregar_serie_tesouro(chave) if chave else []
        universo = candidatos_tesouro(linhas, date.today())
        linha = next((r for r in linhas
                      if str(r.get("security_key")).upper() == chave), None)
        nota = ("Taxa de venda (a que o Tesouro recompra), em % a.a.; "
                "TSELIC é spread sobre a Selic, IPCA+ é taxa real, "
                "prefixado é taxa nominal — só se compara dentro da mesma "
                "família.")
        return _montar(tipo, moeda,
                       entradas_tesouro(chave, linha, serie) if chave else
                       {"taxa_mercado": v.Entrada()},
                       universo.get(chave) if chave else None,
                       list(universo.values()), (nota,),
                       "Título do Tesouro não identificado sem ambiguidade "
                       "entre os publicados.")
    if tipo == ETF:
        val = v.montar(tipo, {}, moeda=moeda, motivo=(
            "ETF: nenhuma fonte do projeto traz os múltiplos da carteira do "
            "fundo; valuation e pares não são calculados."))
        grupo = p.GrupoPares(motivo="ETF sem métricas de comparação no projeto.")
        return val, p.ComparacaoPares(tk, tipo, moeda, grupo, (), grupo.motivo)
    val = v.montar(None, {}, moeda=moeda)
    return val, p.ComparacaoPares(tk, None, moeda, p.GrupoPares(
        motivo="Classe sem comparação com pares."), (),
        "Classe sem comparação com pares.")


def _ler_como_dicts(ticker: str, nome: str, classe: str, moeda: str):
    val, comp = ler_sem_cache(ticker, nome, classe, moeda)
    return val.como_dict(), comp.como_dict()


_ler_cache = _cache(_ler_como_dicts)


def ler(ticker: str, nome: str, classe: str, moeda: str):
    """(Valuation, ComparacaoPares), com cache de 15 min por ativo.

    Leitura com a vitrine de FIIs em falha não fica no cache: guardá-la
    prenderia o fundo em "Dado não disponível" por 15 min depois de a vitrine
    voltar (mesma regra de ``load_fii_methodology_inputs``).
    """
    dv, dc = _ler_cache(ticker, nome, classe, moeda)
    if (any(str(x).startswith(AVISO_VITRINE_FII_INDISPONIVEL)
            for x in dv.get("premissas") or ())
            and hasattr(_ler_cache, "clear")):
        _ler_cache.clear()
    return v.Valuation.de_dict(dv), p.ComparacaoPares.de_dict(dc)
