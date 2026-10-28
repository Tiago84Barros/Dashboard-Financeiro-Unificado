"""
core/b3_troca_codigo.py — uma companhia, uma classe, um código.

Quando a companhia troca de código na B3 (ELET3 → AXIA3, CCRO3 → MOTV3,
ARZZ3 → AZZA3...), o universo do app (``public.setores``) passou a ter as
duas linhas, a ingestão diária reativava as duas em ``market.assets`` e a
aba de portfólio tratava cada código como uma empresa: a mesma companhia
liderava dois segmentos no mesmo ano e entrava duas vezes no benchmark do
segmento. Pior, os dois códigos caíam em segmentos diferentes, porque cada
um casava com uma linha diferente de ``public.setores``.

Este módulo é a regra, sem banco e sem streamlit:

* ``mapa_de_trocas`` decide, para cada código antigo, o código atual;
* ``unificar_classificacao`` dá aos dois códigos UMA classificação setorial;
* ``deduplicar_por_empresa`` tira do universo o código substituído;
* ``dobrar_*`` levam os dados que ainda estão sob o código antigo (preço,
  múltiplos, volume, valor de mercado) para o código atual, para a empresa
  não perder o histórico de antes da troca.

Identidade da empresa é (company_id, classe), não o ticker: ON (3), PN (4)
e Unit (11) da mesma companhia são ativos diferentes e continuam separados.

Regra de classificação setorial (documentada também no PR):

1. vale a linha de ``public.setores`` com LISTAGEM preenchida — é a que veio
   do arquivo oficial da B3; a outra foi inferida;
2. empate (as duas com LISTAGEM, ou nenhuma): vale a do código novo;
3. os dois códigos recebem a classificação escolhida — nunca ficam em
   segmentos diferentes.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping

import pandas as pd

logger = logging.getLogger(__name__)

# Motivo gravado em market.ticker_alias quando a troca está PROVADA (mesma
# companhia, mesma classe). Direção: brapi_symbol = código antigo,
# b3_ticker = código atual. As linhas legadas da tabela têm a direção
# inversa (brapi_symbol = o símbolo novo que a brapi devolvia para o
# ticker antigo do universo) e motivos livres.
MOTIVO_TROCA = "troca de código B3 (mesma companhia e classe)"

# Troca que a ingestão provou (mesma companhia e classe, mesma direção do
# MOTIVO_TROCA) e ainda NÃO migrou: o histórico continua sob os dois códigos
# até alguém aprovar ``scripts/unificar_trocas_de_codigo_b3.py --apply``. Já
# basta para a tela tratar os dois como uma empresa e para o dry-run listar o
# par; a ingestão não pula o código antigo por causa dela.
MOTIVO_TROCA_PENDENTE = "troca de código B3 provada na ingestão (aguarda --apply)"

# Trocas de código conferidas em 28/10/2026 (antigo → novo): mesma companhia
# no armazém (company_id) e mesma classe. Ficam aqui porque o cadastro não
# as prova sozinho em toda base: no Supabase ALSO3 e CARD3 nem têm ativo, e
# o alias AZZA3 aponta para SOMA3 (a incorporação do Grupo Soma, outra
# companhia).
PARES_CONHECIDOS: dict[str, str] = {
    "ELET3": "AXIA3", "CARD3": "CSUD3", "CCRO3": "MOTV3", "ALSO3": "ALOS3",
    "EMBR3": "EMBJ3", "CRDE3": "FIEI3", "GUAR3": "RIAA3", "RRRP3": "BRAV3",
    "ARZZ3": "AZZA3", "WIZS3": "WIZC3", "ODPV3": "SAUD3", "NINJ3": "ARND3",
}

_TICKER = re.compile(r"^([A-Z0-9]{4})(\d{1,2})F?$")


def normalizar(tk) -> str:
    return str(tk or "").upper().replace(".SA", "").strip()


def classe(tk) -> str | None:
    """Sufixo de classe do código B3: '3' (ON), '4' (PN), '11' (Unit)..."""
    m = _TICKER.match(normalizar(tk))
    return m.group(2) if m else None


def mesma_classe(a, b) -> bool:
    ca = classe(a)
    return ca is not None and ca == classe(b)


def _cid(v) -> int | None:
    try:
        return None if v is None or pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


def _linhas_alias(aliases) -> list[tuple[str, str, str]]:
    """(brapi_symbol, b3_ticker, motivo) de DataFrame, dicts ou tuplas."""
    if aliases is None:
        return []
    if isinstance(aliases, pd.DataFrame):
        if aliases.empty:
            return []
        aliases = aliases.to_dict("records")
    linhas = []
    for a in aliases:
        if isinstance(a, Mapping):
            s, b, m = a.get("brapi_symbol"), a.get("b3_ticker"), a.get("motivo")
        else:
            s, b, m = (tuple(a) + (None,))[:3]
        s, b = normalizar(s), normalizar(b)
        if s and b and s != b:
            linhas.append((s, b, str(m or "")))
    return linhas


def _cria_ciclo(mapa: dict[str, str], antigo: str, novo: str) -> bool:
    tk, vistos = novo, set()
    while tk in mapa and tk not in vistos:
        vistos.add(tk)
        tk = mapa[tk]
    return tk == antigo


def fechar_cadeias(mapa: dict[str, str]) -> dict[str, str]:
    """Fecha cadeias (A→B, B→C vira A→C, B→C) e descarta o que fizer ciclo."""
    fechado: dict[str, str] = {}
    for antigo in mapa:
        tk, vistos = antigo, [antigo]
        while tk in mapa:
            tk = mapa[tk]
            if tk in vistos:
                tk = None
                break
            vistos.append(tk)
        if tk is not None:
            fechado[antigo] = tk
    return fechado


def mapa_de_trocas(df: pd.DataFrame | None, aliases=None) -> dict[str, str]:
    """{código antigo: código atual} — só trocas da mesma companhia e classe.

    ``df``: ativos com ``ticker`` e, se houver, ``company_id``.
    ``aliases``: linhas de ``market.ticker_alias`` (brapi_symbol, b3_ticker,
    motivo). Ordem de decisão:

    1. alias com ``MOTIVO_TROCA`` ou ``MOTIVO_TROCA_PENDENTE`` (já provado
       quando foi gravado);
    2. ``PARES_CONHECIDOS``;
    3. alias legado (brapi_symbol = novo, b3_ticker = antigo) só quando os
       dois códigos estão em ``df`` com o MESMO company_id — o que descarta
       AZUL3/AZUL4 (classe), ALOS3 → BRML3 e BRAV3 → ENAT3 (incorporações:
       a companhia incorporada some, não troca de código).

    Em toda regra a classe tem de ser a mesma, e dois company_id conhecidos
    e diferentes vetam a troca. Grupo (company_id, classe) com mais de um
    código que nenhuma regra decide fica como está, com aviso no log — a
    regra não adivinha qual é o atual.
    """
    empresa: dict[str, int | None] = {}
    if df is not None and not df.empty and "ticker" in df.columns:
        cids = df["company_id"] if "company_id" in df.columns else [None] * len(df)
        for tk, cid in zip(df["ticker"], cids):
            empresa[normalizar(tk)] = _cid(cid)

    mapa: dict[str, str] = {}

    def _aceitar(antigo: str, novo: str) -> None:
        if antigo == novo or antigo in mapa or not mesma_classe(antigo, novo):
            return
        ca, cn = empresa.get(antigo), empresa.get(novo)
        if ca is not None and cn is not None and ca != cn:
            return
        if _cria_ciclo(mapa, antigo, novo):
            return
        mapa[antigo] = novo

    provados = (MOTIVO_TROCA, MOTIVO_TROCA_PENDENTE)
    linhas = _linhas_alias(aliases)
    for s, b, motivo in linhas:
        if motivo in provados:
            _aceitar(s, b)
    for antigo, novo in PARES_CONHECIDOS.items():
        _aceitar(antigo, novo)
    for novo, antigo, motivo in linhas:
        if motivo in provados:
            continue
        cn, ca = empresa.get(novo), empresa.get(antigo)
        if cn is not None and cn == ca:
            _aceitar(antigo, novo)

    mapa = fechar_cadeias(mapa)
    _avisar_sem_regra(empresa, mapa)
    return mapa


def _avisar_sem_regra(empresa: dict[str, int | None], mapa: dict[str, str]) -> None:
    grupos: dict[tuple, list[str]] = {}
    for tk, cid in empresa.items():
        if cid is None or tk in mapa or classe(tk) is None:
            continue
        grupos.setdefault((cid, classe(tk)), []).append(tk)
    for (cid, cls), tks in grupos.items():
        if len(tks) > 1:
            logger.warning("company_id %s classe %s com %d códigos e nenhuma regra "
                           "de troca: %s", cid, cls, len(tks), sorted(tks))


# ── Classificação setorial ────────────────────────────────────────────────────

def _preenchido(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip() != ""


def escolher_classificacao(listagem_antigo, listagem_novo) -> str:
    """'antigo' ou 'novo': de qual código sai a classificação setorial.

    LISTAGEM preenchida = linha do arquivo oficial da B3, prevalece. Empate:
    o código novo.
    """
    if _preenchido(listagem_antigo) and not _preenchido(listagem_novo):
        return "antigo"
    return "novo"


def unificar_classificacao(df: pd.DataFrame, mapa: dict[str, str],
                           colunas: Iterable[str] = ("SETOR", "SUBSETOR", "SEGMENTO"),
                           col_listagem: str = "listagem") -> pd.DataFrame:
    """Cópia de ``df`` em que antigo e novo têm a mesma classificação."""
    if df is None or df.empty or not mapa or "ticker" not in df.columns:
        return df
    out = df.copy()
    pos = {}
    for i, tk in zip(out.index, out["ticker"].map(normalizar)):
        pos.setdefault(tk, i)
    colunas = [c for c in colunas if c in out.columns]
    tem_listagem = col_listagem in out.columns
    for antigo, novo in mapa.items():
        if antigo not in pos or novo not in pos:
            continue
        ia, inovo = pos[antigo], pos[novo]
        la = out.at[ia, col_listagem] if tem_listagem else None
        ln = out.at[inovo, col_listagem] if tem_listagem else None
        origem, destino = ((ia, inovo) if escolher_classificacao(la, ln) == "antigo"
                           else (inovo, ia))
        for c in colunas:
            out.at[destino, c] = out.at[origem, c]
    return out


# ── Universo: uma linha por (companhia, classe) ──────────────────────────────

def deduplicar_por_empresa(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Tira do universo o código substituído cujo código atual também está nele.

    Lê a coluna ``codigo_atual`` (posta por ``core.market_read.load_setores``).
    Devolve (universo, {antigo: atual}) — o mapa serve para dobrar os dados
    que outros carregadores ainda trazem sob o código antigo. Sem a coluna
    (base legada, fixture de teste), não mexe em nada.
    """
    if df is None or df.empty or "codigo_atual" not in df.columns or "ticker" not in df.columns:
        return df, {}
    tks = df["ticker"].map(normalizar)
    atual = df["codigo_atual"].map(normalizar)
    substituido = (atual != "") & (atual != tks) & atual.isin(set(tks))
    if not bool(substituido.any()):
        return df, {}
    mapa = dict(zip(tks[substituido], atual[substituido]))
    logger.info("universo B3: %d código(s) substituído(s) saem: %s", len(mapa),
                ", ".join(f"{a}->{n}" for a, n in sorted(mapa.items())))
    return df.loc[~substituido].reset_index(drop=True), mapa


# ── Dobras no SQL: para o que é estatística SOBRE a fita ─────────────────────
# Mediana de giro e contagem de exercícios não se dobram depois de prontas:
# a mediana de 5 meses do código antigo e a de 1 mês do atual não dão a
# mediana dos 6, e 8 anos sob um código e 3 sob o outro são 11, não
# max(8, 3). A fita tem de ser unida ANTES da conta — o que estas duas peças
# fazem, com o mesmo critério da migração (na mesma data fica a linha do
# código atual), para o número sair igual antes e depois do ``--apply``.

def sql_ticker(coluna: str = "ticker") -> str:
    """O ``normalizar`` em SQL."""
    return f"TRIM(REPLACE(UPPER({coluna}), '.SA', ''))"


def sql_dono(coluna: str = "ticker") -> str:
    """Expressão SQL do código que responde pela linha: o atual, quando o
    parâmetro ``:trocas`` (JSON {antigo: atual}) traz o ticker; senão ele mesmo."""
    tk = sql_ticker(coluna)
    return f"COALESCE(CAST(:trocas AS jsonb) ->> {tk}, {tk})"


def trocas_json(trocas: Mapping | Iterable | None) -> str:
    """O valor de ``:trocas``: aceita o mapa ou os pares (chave de cache)."""
    return json.dumps(dict(trocas or ()), sort_keys=True)


# ── Dobras: o dado do código antigo passa a responder pelo atual ─────────────
# Em todas, o valor do código atual prevalece; o do antigo só preenche o que
# falta (o histórico de antes da troca). O código antigo sai do resultado.

def dobrar_escalares(d: Mapping | None, mapa: dict[str, str], modo: str = "novo") -> dict:
    """{ticker: valor}. ``modo='max'`` fica com o maior dos dois (anos de histórico)."""
    if not d or not mapa:
        return d
    out = dict(d)
    for antigo, novo in mapa.items():
        if antigo not in out:
            continue
        v = out.pop(antigo)
        if novo not in out or out[novo] is None:
            out[novo] = v
        elif modo == "max" and v is not None:
            try:
                out[novo] = max(out[novo], v)
            except TypeError:
                pass
    return out


def _encadear(atual: pd.Series, velho: pd.Series) -> pd.Series:
    """Reescala ``velho`` para casar com ``atual`` no primeiro ponto comum.

    Preço ajustado do código antigo e do novo é a mesma companhia, mas em bases
    de ajuste que diferem alguns por cento (cada código foi ingerido num dia).
    Emendar sem reescalar transforma essa diferença num retorno que não houve.
    """
    comum = atual.notna() & velho.notna() & (velho != 0)
    if not bool(comum.any()):
        return velho
    i = comum[comum].index[0]
    try:
        return velho * (float(atual.loc[i]) / float(velho.loc[i]))
    except (TypeError, ValueError, ZeroDivisionError):
        return velho


def dobrar_colunas(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    """Quadro largo de PREÇO (uma coluna por ticker): a coluna antiga, encadeada
    no primeiro mês comum, completa a atual antes do início dela."""
    if df is None or df.empty or not mapa:
        return df
    antigos = [a for a in mapa if a in df.columns]
    if not antigos:
        return df
    out = df.copy()
    for antigo in antigos:
        novo = mapa[antigo]
        if novo in out.columns:
            out[novo] = out[novo].combine_first(_encadear(out[novo], out[antigo]))
        else:
            out[novo] = out[antigo]
    out = out.drop(columns=antigos)
    out.attrs = dict(df.attrs)
    return out


def dobrar_linhas(df: pd.DataFrame, mapa: dict[str, str], col_ticker: str = "ticker",
                  chaves: Iterable[str] = ("mes",)) -> pd.DataFrame:
    """Quadro longo: renomeia o antigo e, na mesma chave, fica a linha do atual."""
    if df is None or df.empty or not mapa or col_ticker not in df.columns:
        return df
    tks = df[col_ticker].map(normalizar)
    do_antigo = tks.isin(set(mapa))
    if not bool(do_antigo.any()):
        return df
    out = df.copy()
    out[col_ticker] = tks.map(lambda t: mapa.get(t, t))
    chaves = [c for c in chaves if c in out.columns]
    out["_do_antigo"] = do_antigo.astype(int).values
    out = (out.sort_values("_do_antigo", kind="stable")
              .drop_duplicates(subset=[col_ticker, *chaves], keep="first")
              .sort_index()
              .drop(columns="_do_antigo"))
    out.attrs = dict(df.attrs)
    return out


def _completar_por_data(atual: pd.DataFrame, velho: pd.DataFrame, col_disp: str,
                        col_ticker: str) -> pd.DataFrame:
    """``atual.combine_first(velho)`` (índice = data) sem antecipar disponibilidade.

    Na data que os dois têm, o valor que falta no atual e o antigo empresta só
    ficou disponível quando o antigo ficou. O portão point-in-time
    (``views.empresas_b3._classificar_disponibilidade_pit``) lê UM
    ``AvailableAt`` por linha — vazio é baseline (prazo legal da CVM), medido
    só passa até a data de decisão, malformado barra. Então:

    * dois baselines: empresta, a linha continua baseline;
    * dois medidos: empresta e, se emprestou algo, a linha passa a valer a
      partir do MAIS TARDIO dos dois;
    * medido com baseline, ou carimbo malformado: não empresta — as duas
      disponibilidades não se comparam, e o atual fica como está.
    """
    if col_disp not in atual.columns and col_disp not in velho.columns:
        return atual.combine_first(velho)
    if (col_disp in atual.columns and col_disp in velho.columns
            and atual[col_disp].dtype != velho[col_disp].dtype):
        # NaT puro (naive) contra carimbo com fuso: combine_first não junta
        atual = atual.assign(**{col_disp: atual[col_disp].astype(object)})
        velho = velho.assign(**{col_disp: velho[col_disp].astype(object)})
    comuns = atual.index.intersection(velho.index)
    if comuns.empty:
        return atual.combine_first(velho)
    vazio = pd.Series(None, index=comuns, dtype=object)
    disp_a = atual[col_disp].reindex(comuns) if col_disp in atual.columns else vazio
    disp_v = velho[col_disp].reindex(comuns) if col_disp in velho.columns else vazio
    t_a = pd.to_datetime(disp_a, errors="coerce", utc=True)
    t_v = pd.to_datetime(disp_v, errors="coerce", utc=True)
    medido_a, medido_v = disp_a.notna() & t_a.notna(), disp_v.notna() & t_v.notna()
    ambos_baseline = disp_a.isna() & disp_v.isna()
    ambos_medidos = medido_a & medido_v
    velho = velho.drop(index=comuns[~(ambos_baseline | ambos_medidos).to_numpy()])

    # data em que o antigo preenche algum valor que falta no atual
    valores = [c for c in velho.columns if c not in (col_disp, col_ticker)]
    v_com = velho.reindex(comuns)[valores]
    a_com = atual.reindex(columns=valores).reindex(comuns)
    empresta = (a_com.isna() & v_com.notna()).any(axis=1)
    adiar = comuns[(ambos_medidos & empresta & (t_v > t_a)).to_numpy()]

    junto = atual.combine_first(velho)
    if len(adiar):
        if junto[col_disp].dtype != disp_v.dtype:
            junto[col_disp] = junto[col_disp].astype(object)
        junto.loc[adiar, col_disp] = disp_v.loc[adiar].to_numpy()
    return junto


def dobrar_historico(hist: Mapping | None, mapa: dict[str, str], col_data: str = "Data",
                     col_ticker: str = "Ticker", col_disp: str = "AvailableAt") -> dict:
    """{ticker: quadro por data}: as datas do antigo completam as do atual.

    Na mesma data o atual prevalece; o antigo só preenche o que falta, e a
    linha nunca fica disponível antes do valor emprestado (``_completar_por_data``).
    """
    if not hist or not mapa:
        return hist
    out = dict(hist)
    for antigo, novo in mapa.items():
        velho = out.pop(antigo, None)
        if velho is None or getattr(velho, "empty", True):
            continue
        velho = velho.assign(**{col_ticker: novo}) if col_ticker in velho.columns else velho
        atual = out.get(novo)
        if atual is None or atual.empty:
            out[novo] = velho.reset_index(drop=True)
            continue
        if col_data not in atual.columns or col_data not in velho.columns:
            continue
        if atual[col_data].is_unique and velho[col_data].is_unique:
            junto = _completar_por_data(atual.set_index(col_data), velho.set_index(col_data),
                                        col_disp, col_ticker).reset_index()
        else:
            junto = (pd.concat([atual, velho], ignore_index=True)
                       .drop_duplicates(subset=[col_data], keep="first"))
        colunas = list(atual.columns) + [c for c in junto.columns if c not in atual.columns]
        junto = junto[colunas].sort_values(col_data, kind="stable").reset_index(drop=True)
        if col_ticker in junto.columns:
            junto[col_ticker] = novo
        out[novo] = junto
    return out


def dobrar_irmas(irmas: Mapping | None, mapa: dict[str, str]) -> dict:
    """{ticker: (classes irmãs)} sem os códigos substituídos."""
    if not irmas or not mapa:
        return irmas
    return {tk: tuple(t for t in grupo if t not in mapa)
            for tk, grupo in irmas.items() if tk not in mapa}
