"""Confronta os ativos da carteira com o universo do banco, por classe.

O que este módulo faz é o mesmo que as telas Empresas B3, Seleção de FIIs e
Empresas Americanas fazem: pontuar o ativo CONTRA os pares apurados no banco.
O que ele não faz é inventar um motor novo — cada classe usa o motor já
publicado da sua classe, e o percentil é medido dentro do universo que aquele
motor pontuou.

Nenhuma função levanta exceção para a tela: indisponibilidade volta em
``erro`` como texto sem detalhe de conexão, e a tela decide o que dizer.
Ausência do ticker no universo volta em ``ausentes`` — não vira nota neutra,
porque "não apurado" não é "mediano".
"""
from __future__ import annotations

_SEM_BANCO = "Universo indisponível no banco agora."


def _limpa_tickers(tickers) -> list[str]:
    vistos: dict[str, None] = {}
    for ticker in tickers or ():
        chave = str(ticker or "").strip().upper().replace(".SA", "")
        if chave:
            vistos.setdefault(chave, None)
    return list(vistos)


def _percentil(valores, alvo) -> float | None:
    """Fração do universo com nota estritamente menor que a do ativo."""
    validos = [v for v in valores if v is not None]
    if not validos or alvo is None:
        return None
    return sum(1 for v in validos if v < alvo) / len(validos)


def _float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def analise_acoes_db(tickers) -> dict:
    """Seis trilhas de ``core.b3_company_score`` contra o universo de public.multiplos.

    É a MESMA decomposição do painel individual de Empresas B3, não o ranking
    da Análise Avançada — aquele motor vive na tela e pondera por setor. Aqui a
    referência é o universo inteiro, para que os ativos da carteira sejam
    comparáveis entre si.
    """
    alvos = _limpa_tickers(tickers)
    saida = {"linhas": [], "universo": 0, "ausentes": alvos, "erro": None,
             "fonte": "public.multiplos + public.setores (Supabase)",
             "referencia": "universo B3"}
    if not alvos:
        saida["ausentes"] = []
        return saida
    try:
        import core.b3_data as _db
        from core.b3_company_score import (
            TRACK_LABELS,
            classification,
            score_cross_section,
        )
        from core.b3_slopes import enrich_com_slopes

        universo = _db.load_multiplos_todos()
        if universo is None or universo.empty:
            saida["erro"] = _SEM_BANCO
            return saida
        universo = universo.copy()
        universo["Ticker"] = (universo["Ticker"].astype(str)
                              .str.replace(".SA", "", regex=False).str.strip().str.upper())
        universo = universo.drop_duplicates("Ticker").reset_index(drop=True)

        try:
            setores = _db.load_setores()
            if setores is not None and not setores.empty:
                meta = setores.rename(columns={"ticker": "Ticker"}).drop_duplicates("Ticker")
                universo = universo.merge(
                    meta[[c for c in ("Ticker", "SETOR", "SUBSETOR", "nome_empresa")
                          if c in meta.columns]], on="Ticker", how="left")
        except Exception:
            pass

        crescimento_apurado = False
        try:
            historicos = _db.load_multiplos_historico_batch(
                tuple(universo["Ticker"].dropna().astype(str).tolist()))
            if historicos:
                universo = enrich_com_slopes(universo, historicos)
                crescimento_apurado = True
        except Exception:
            # Sem histórico a trilha de crescimento fica sem cobertura e o
            # motor encolhe a nota para o neutro — é perda de convicção, não
            # penalidade; a tela precisa dizer isso.
            pass

        scored = score_cross_section(universo)
        if scored is None or scored.empty:
            saida["erro"] = _SEM_BANCO
            return saida
        notas = [_float(v) for v in scored["score"].tolist()]
        indice = {str(t).upper(): i for i, t in enumerate(scored["Ticker"].tolist())}
        linhas, ausentes = [], []
        for ticker in alvos:
            pos = indice.get(ticker)
            if pos is None:
                ausentes.append(ticker)
                continue
            linha = scored.iloc[pos]
            score = _float(linha.get("score"))
            rotulo, badge = classification(score)
            trilhas = {label: _float(linha.get(coluna))
                       for coluna, label in TRACK_LABELS.items()}
            linhas.append({
                "ticker": ticker,
                "nome": str(linha.get("nome_empresa") or "") or None,
                "score": score,
                "classificacao": rotulo,
                "badge": badge,
                "cobertura": _float(linha.get("coverage")),
                "percentil": _percentil(notas, score),
                "setor": str(linha.get("SETOR") or "") or None,
                "trilhas": trilhas,
            })
        linhas.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["ticker"]))
        saida.update({"linhas": linhas, "universo": int(len(scored)),
                      "ausentes": ausentes,
                      "referencia": f"universo B3 · {len(scored)} empresas",
                      "crescimento_apurado": crescimento_apurado})
        return saida
    except Exception:
        saida["erro"] = _SEM_BANCO
        return saida


def analise_fiis_db(tickers) -> dict:
    """Nota por tipo de FII, confiança e status de publicação — motor de Seleção de FIIs."""
    alvos = _limpa_tickers(tickers)
    saida = {"linhas": [], "universo": 0, "ausentes": alvos, "erro": None,
             "fonte": "market.fii_selection_inputs (snapshot auditável)",
             "referencia": "pares do mesmo tipo"}
    if not alvos:
        saida["ausentes"] = []
        return saida
    try:
        import core.market_read as _mr
        from core.fii_methodology import METHODOLOGY_VERSION, score_fiis_by_type
        from core.fii_portfolio_v4 import LIVE_PORTFOLIO_STRATEGY_ID
        from core.fii_validation import validation_supports_strategy

        inputs = _mr.load_fii_methodology_inputs()
        if inputs is None or inputs.empty:
            saida["erro"] = _SEM_BANCO
            return saida
        try:
            validation = _mr.load_fii_validation_status(METHODOLOGY_VERSION)
            aplicavel = validation_supports_strategy(validation, LIVE_PORTFOLIO_STRATEGY_ID)
        except Exception:
            aplicavel = False
        scored = score_fiis_by_type(
            inputs.to_dict("records"),
            validation_status="passed" if aplicavel else "unvalidated")
        if not scored:
            saida["erro"] = _SEM_BANCO
            return saida
        por_ticker = {str(r.get("ticker") or "").upper(): r for r in scored}
        notas_por_tipo: dict[str, list] = {}
        for r in scored:
            notas_por_tipo.setdefault(str(r.get("tipo") or ""), []).append(
                _float(r.get("type_score")))
        linhas, ausentes = [], []
        for ticker in alvos:
            r = por_ticker.get(ticker)
            if r is None:
                ausentes.append(ticker)
                continue
            score = _float(r.get("type_score"))
            tipo = str(r.get("tipo") or "")
            linhas.append({
                "ticker": ticker,
                "score": score,
                "tipo": tipo or None,
                "segmento": str(r.get("segmento") or "") or None,
                "confianca": _float(r.get("confidence")),
                "cobertura": _float(r.get("coverage")),
                "cobertura_critica": _float(r.get("critical_coverage")),
                "status_publicacao": str(r.get("publication_status") or "diligence_only"),
                "prontidao": str(r.get("data_readiness_status") or ""),
                "faltantes": tuple(r.get("missing_critical") or ()),
                "percentil": _percentil(notas_por_tipo.get(tipo, []), score),
                "pares_tipo": len(notas_por_tipo.get(tipo, [])),
            })
        linhas.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["ticker"]))
        saida.update({"linhas": linhas, "universo": len(scored), "ausentes": ausentes,
                      "referencia": f"{len(scored)} FIIs pontuados · "
                                    f"metodologia {METHODOLOGY_VERSION}",
                      "validacao_aplicavel": aplicavel})
        return saida
    except Exception:
        saida["erro"] = _SEM_BANCO
        return saida


def analise_exterior_db(symbols) -> dict:
    """Score do universo americano para os símbolos da carteira.

    ETF, BDR e fundo não têm demonstração de companhia e por isso não estão no
    universo — eles voltam em ``ausentes``. Isso não é falha de ingestão: o
    módulo americano analisa AÇÕES, e a tela precisa dizer isso em vez de
    exibir cobertura baixa sem explicação.
    """
    alvos = _limpa_tickers(symbols)
    saida = {"linhas": [], "universo": 0, "ausentes": alvos, "erro": None,
             "fundamentos": {}, "fonte": "market_us (vitrine de scores)",
             "referencia": "universo de ações dos EUA"}
    if not alvos:
        saida["ausentes"] = []
        return saida
    try:
        import core.us_data as _us

        # Mesmos cortes de core.us_portfolio_analysis._classification (75/65/50/35);
        # a função da B3 é a única que devolve o par rótulo+badge, e duplicá-la
        # aqui criaria uma terceira cópia dos mesmos limiares.
        from core.b3_company_score import classification as us_classification

        universo = _us.scored_universe()
        if universo is None or universo.empty or "symbol" not in universo.columns:
            saida["erro"] = _SEM_BANCO
            return saida
        universo = universo.copy()
        universo["symbol"] = universo["symbol"].astype(str).str.strip().str.upper()
        notas = [_float(v) for v in universo["score"].tolist()] \
            if "score" in universo.columns else []
        indice = {s: i for i, s in enumerate(universo["symbol"].tolist())}
        linhas, ausentes, fundamentos = [], [], {}
        for symbol in alvos:
            pos = indice.get(symbol)
            if pos is None:
                ausentes.append(symbol)
                continue
            linha = universo.iloc[pos]
            score = _float(linha.get("score"))
            try:
                rotulo, badge = us_classification(score)
            except Exception:
                rotulo, badge = ("Sem classificação", "neutro")
            linhas.append({
                "ticker": symbol,
                "nome": str(linha.get("company_name") or linha.get("name") or "") or None,
                "score": score,
                "classificacao": rotulo,
                "badge": badge,
                "cobertura": _float(linha.get("coverage")),
                "confianca": _float(linha.get("score_confidence")),
                "status": str(linha.get("score_status") or "") or None,
                "setor": str(linha.get("sector") or "") or None,
                "percentil": _percentil(notas, score),
            })
            fundamentos[symbol] = {
                coluna: linha.get(coluna) for coluna in linha.index
                if coluna not in ("symbol", "score", "coverage")
            }
        linhas.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["ticker"]))
        saida.update({"linhas": linhas, "universo": int(len(universo)),
                      "ausentes": ausentes, "fundamentos": fundamentos,
                      "referencia": f"universo de ações dos EUA · {len(universo)} empresas"})
        return saida
    except Exception:
        saida["erro"] = _SEM_BANCO
        return saida


def analise_tesouro_db() -> dict:
    """Conjuntura que precifica o título público: Selic, IPCA e juro real ex-ante.

    Não há score de título do Tesouro — o emissor é um só e não há corte
    transversal para ranquear. O que o banco tem, e é o que decide entre
    Selic, IPCA+ e Prefixado, é a série macro.
    """
    saida = {"anos": [], "atual": None, "erro": None,
             "fonte": "public.macro (Supabase)"}
    try:
        import core.b3_data as _db

        historico = _db.load_macro_history()
        if not historico:
            saida["erro"] = _SEM_BANCO
            return saida
        anos = sorted(int(a) for a in historico)
        recentes = anos[-6:]
        linhas = []
        for ano in recentes:
            bruto = historico.get(ano) or historico.get(str(ano)) or {}
            linhas.append({
                "ano": ano,
                "selic": _float(bruto.get("selic")),
                "ipca": _float(bruto.get("ipca")),
                "juros_real_ex_ante": _float(bruto.get("juros_real_ex_ante")),
                "cambio": _float(bruto.get("cambio")),
                "pib": _float(bruto.get("pib")),
            })
        saida["anos"] = linhas
        saida["atual"] = linhas[-1] if linhas else None
        return saida
    except Exception:
        saida["erro"] = _SEM_BANCO
        return saida
