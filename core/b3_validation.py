"""Persistencia auditavel das validacoes da metodologia Empresas B3.

O modulo registra um manifesto compacto e deterministico de cada execucao da
criacao de portfolio. Ele nao converte ``first_seen_proxy`` em data de
publicacao: a qualidade PIT e declarada explicitamente no manifesto.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from numbers import Real
from typing import Any
from uuid import uuid4

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    """Normaliza estruturas para JSON estavel, sem NaN/objetos pandas."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Real):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except Exception:
            pass
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(_clean(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}).scalar())


def lineage_counts(conn, table: str) -> dict[str, int]:
    """Procedencia VERIFICADA das demonstracoes anuais, nao ponteiro nao-nulo.

    `raw_payload_id IS NOT NULL` mede que existe um ponteiro, nao que ele leva a
    algum lugar. `market.brapi_raw_payloads.id` e um BIGSERIAL e a compactacao
    remota fazia DROP/CREATE: a sequencia REINICIA em 1 e os ponteiros antigos
    passam a resolver para payloads de outra geracao -- com o endpoint certo e o
    conteudo de outra empresa, de modo que nada parece errado.

    Medido no Supabase em 01/09/2026: das 85.889 linhas com ponteiro, 84.116
    apontavam para um payload coletado DEPOIS da propria linha. A causa nao pode
    ser posterior ao efeito; esse e o teste barato que separa procedencia de
    coincidencia. `traced_rows` passa a exigir que o payload exista E seja
    anterior a linha; `dangling_rows` e `impossible_rows` deixam o resto visivel
    em vez de somar como linhagem.
    """
    row = conn.execute(text(f"""
        SELECT count(*) AS rows,
               count(*) FILTER (WHERE d.raw_payload_id IS NOT NULL) AS pointed,
               count(*) FILTER (
                   WHERE d.raw_payload_id IS NOT NULL AND p.id IS NULL)
                   AS dangling,
               count(*) FILTER (
                   WHERE p.id IS NOT NULL AND p.fetched_at > d.first_seen_at)
                   AS impossible,
               count(*) FILTER (
                   WHERE p.id IS NOT NULL AND p.fetched_at <= d.first_seen_at)
                   AS traced
        FROM {table} d
        LEFT JOIN market.brapi_raw_payloads p ON p.id = d.raw_payload_id
        WHERE d.period='annual'
    """)).mappings().one()
    return {
        "rows": int(row["rows"] or 0),
        "traced_rows": int(row["traced"] or 0),
        "pointer_rows": int(row["pointed"] or 0),
        "dangling_rows": int(row["dangling"] or 0),
        "impossible_rows": int(row["impossible"] or 0),
    }


def _survivorship_status() -> dict[str, Any]:
    """Mede o universo de deslistadas em vez de declarar um literal.

    A-126: este bloco era ``{"strict_available": False, "reason": "..."}`` fixo
    no codigo. ``core/survivorship_ingestion.py`` (478 linhas, recomendacao C3c
    da banca de 2026-05-23) existe exatamente para integrar esse universo, e
    nenhum modulo o consultava -- de modo que ingerir deslistadas jamais mudaria
    o veredito de ``validation_readiness``. O contraste estava no proprio
    arquivo: o bloco ``pit`` comeca falso e e sobrescrito por uma medicao real.

    27/08/2026: ``strict_available`` deixou de ser o literal ``False``. Ele era
    inalcancavel por construcao -- nenhuma ingestao, por melhor que fosse, mudava
    o veredito -- e um gate que so pode reprovar nunca e revisto. Agora ele
    compara a cobertura MEDIDA contra ``SURVIVORSHIP_SHARE_MINIMA``.

    A cobertura e medida na unidade certa e contra o denominador certo: das 133
    companhias Categoria A que negociavam em BOLSA e tiveram registro cancelado
    de 2010 em diante, quantas tem ao menos um ticker resolvido. Hoje sao 59 --
    44,4%. Os 147 tickers do universo bruto pareciam cobrir as 133 companhias,
    mas ticker e companhia sao unidades diferentes (ON, PN e UNIT da mesma
    empresa), e a comparacao direta e a mesma troca de casca por descarte que o
    A-154 corrigiu no denominador de cobertura.
    """
    try:
        from core.survivorship_ingestion import resumo_ingestao

        # A-137: `incluir_cvm` era False, entao o manifesto reportava zero
        # cancelamentos da CVM mesmo depois de a ponte FCA existir -- o gate
        # media uma fonte que nunca chegava a ser consultada.
        #
        # `permitir_download=False` e deliberado: este manifesto e renderizado
        # na Saude dos Dados, e tela nao baixa nada. Le o que
        # scripts/atualizar_universo_deslistadas.py deixou em cache; sem cache,
        # devolve zero e o motivo diz que a fonte nao foi populada.
        resumo = resumo_ingestao(incluir_cvm=True, permitir_download=False)
    except Exception:  # noqa: BLE001 - manifesto nao pode quebrar por diagnostico
        logger.warning("survivorship: resumo_ingestao indisponivel", exc_info=True)
        return {
            "strict_available": False,
            "reason": "universo historico de deslistadas nao pode ser medido",
        }
    # A-137: `cvm_canceladas` e o registro bruto do cadastro (1.912 companhias,
    # a maioria sem acao em bolsa -- Categoria B, registro so para divida, ou
    # cancelada antes de o FCA existir). Somar isso a "fontes externas" produzia
    # um numero maior que o proprio total de deslistadas. Quem entra no universo
    # e o mapeado para ticker.
    fontes = {
        "curados": int(resumo.get("curados") or 0),
        "locais": int(resumo.get("locais") or 0),
        "b3_cache": int(resumo.get("b3_cache") or 0),
        "cvm_mapeadas": int(resumo.get("cvm_mapeadas") or 0),
        "cvm_canceladas_registro": int(resumo.get("cvm_canceladas") or 0),
    }
    # Somar as fontes seria inflar: o CSV exportado pelo script aparece tanto em
    # `locais` quanto em `cvm_mapeadas` (e o mesmo conjunto, lido por dois
    # caminhos), e parte dos curados reaparece no cadastro da CVM. So
    # `total_unicos`, que vem do merge deduplicado, e somavel.
    total = int(resumo.get("total_unicos") or 0)
    try:
        from core.survivorship_ingestion import cobertura_relevante
        cob = cobertura_relevante(permitir_download=False)
    except Exception:  # noqa: BLE001
        logger.warning("survivorship: cobertura nao medida", exc_info=True)
        cob = {"relevantes": 0, "cobertas": 0, "share": None, "tickers": 0}
    share = cob.get("share")
    ok = share is not None and share >= SURVIVORSHIP_SHARE_MINIMA
    if share is None:
        # O texto nomeia o portao ("deslistadas") em TODOS os ramos. Quem le o
        # bloqueio -- tela e teste -- so tem a prosa para saber qual gate falou,
        # e este ramo era o unico que nao se identificava: quando o cache some,
        # o bloqueador virava uma frase sobre "universo historico" que podia ser
        # de qualquer coisa.
        motivo = (f"universo historico de deslistadas nao medido: {total} "
                  f"tickers unicos, sem cadastro CVM em cache para estratificar")
    elif ok:
        motivo = (f"universo historico completo: {cob['cobertas']} de "
                  f"{cob['relevantes']} companhias deslistadas relevantes "
                  f"({share * 100:.0f}%)")
    else:
        motivo = (
            f"universo historico de deslistadas incompleto: so {share * 100:.0f}% "
            f"das companhias relevantes tem ticker resolvido "
            f"({cob['cobertas']} de {cob['relevantes']}, minimo "
            f"{SURVIVORSHIP_SHARE_MINIMA * 100:.0f}%)")
    return {
        "strict_available": ok,
        "reason": motivo,
        "delisted_total": total,
        "delisted_por_fonte": fontes,
        "cobertura_relevante": cob,
    }


# Fatia da serie ANUAL que precisa ter data de protocolo da CVM para que o
# resultado possa ser tratado como validacao estrita. Nao e 100%: a base da CVM
# comeca em 2010, o exercicio corrente ainda nao foi protocolado, e BDR nao
# entrega DFP no Brasil. Exigir 100% seria exigir que fontes inexistentes
# existissem, e o gate nunca sairia do lugar -- que e como ele passou os
# ultimos meses.
PIT_SHARE_MINIMA = 0.90

# Fatia das companhias deslistadas RELEVANTES (Categoria A, negociadas em bolsa,
# canceladas de 2010 em diante) que precisa ter ticker resolvido para o universo
# historico contar como completo.
#
# Igual ao piso do PIT, e de proposito. No PIT os 10% que faltam sao
# estruturalmente indisponiveis -- a base da CVM comeca em 2010 e BDR nao
# entrega DFP. Aqui nao ha fonte faltando: o FCA cobre Categoria A, e a companhia
# que nao resolve nao resolveu por lacuna de ingestao. Baixar o piso seria
# rebaixar a regua para o gate passar, e o gate existe para dizer que ainda nao
# passou. Medido em 27/08/2026: 44,4%.
SURVIVORSHIP_SHARE_MINIMA = 0.90


def build_data_manifest(engine) -> dict[str, Any]:
    """Resume cobertura e qualidade de disponibilidade sem carregar dados brutos."""
    with engine.connect() as conn:
        if not _table_exists(conn, "market.assets"):
            return {"status": "unavailable", "reason": "market.assets ausente"}
        base = conn.execute(text("""
            SELECT count(*) FILTER (WHERE is_active
                                      AND asset_type IN ('stock','unit')
                                      AND company_id IS NOT NULL) AS universe,
                   count(*) FILTER (WHERE is_active
                                      AND asset_type IN ('stock','unit')
                                      AND company_id IS NULL) AS unmapped_company
            FROM market.assets
        """)).mappings().one()
        manifest: dict[str, Any] = {
            "universe_definition": "active stock/unit with company_id",
            "universe": int(base["universe"] or 0),
            "unmapped_company": int(base["unmapped_company"] or 0),
            "pit": {"strict_available": False, "reason": "published_at CVM ainda nao integrado"},
            "survivorship": _survivorship_status(),
        }
        if _table_exists(conn, "market.calculated_metric_vintages"):
            vintages = conn.execute(text("""
                SELECT availability_quality, count(*) AS n
                FROM market.calculated_metric_vintages
                GROUP BY availability_quality
            """)).mappings().all()
            quality = {str(r["availability_quality"]): int(r["n"] or 0) for r in vintages}
            manifest["metric_vintages"] = quality
            # A-155: o denominador e ANUAL de proposito. `ttm` e `spot` nao
            # derivam de uma DFP e nunca poderao ter data de protocolo; conta-los
            # faria a cobertura parecer eternamente incompleta por uma lacuna que
            # nao existe.
            anual = conn.execute(text("""
                SELECT count(*) AS n,
                       count(*) FILTER (WHERE availability_quality='published_at') AS p
                FROM market.calculated_metric_vintages WHERE period='annual'
            """)).mappings().one()
            anual_n, anual_p = int(anual["n"] or 0), int(anual["p"] or 0)
            share = (anual_p / anual_n) if anual_n else 0.0
            manifest["pit"] = {
                # Ate 27/08/2026 este gate era `published_at_rows > 0`. Enquanto
                # a terceira qualidade nao existia, isso era inofensivo: dava
                # False sempre. Agora que ela existe, UMA linha promoveria a
                # base inteira a "PIT estrito" -- um gate que o primeiro dado
                # bom desarma nao e gate. O criterio passa a ser a FATIA da
                # serie anual que tem data de protocolo real.
                "strict_available": anual_p > 0 and share >= PIT_SHARE_MINIMA,
                "published_at_rows": int(quality.get("published_at", 0)),
                "first_seen_proxy_rows": int(quality.get("first_seen_proxy", 0)),
                "migration_baseline_rows": int(quality.get("migration_baseline", 0)),
                "annual_rows": anual_n,
                "annual_published_rows": anual_p,
                "annual_published_share": round(share, 4),
            }
        for table, key in (
            ("market.income_statements", "income"),
            ("market.balance_sheets", "balance"),
            ("market.cash_flow_statements", "cashflow"),
        ):
            if _table_exists(conn, table):
                manifest.setdefault("lineage", {})[key] = lineage_counts(conn, table)
    return manifest


def validation_readiness(manifest: dict[str, Any]) -> dict[str, Any]:
    """Define se um resultado pode ser tratado como validacao estrita.

    A ausencia de PIT publicado ou de universo historico completo nao invalida a
    analise exploratoria; apenas impede que ela seja promovida a recomendacao
    estatisticamente validada.
    """
    pit = manifest.get("pit") or {}
    pit_ok = bool(pit.get("strict_available"))
    surv = manifest.get("survivorship") or {}
    survivorship_ok = bool(surv.get("strict_available"))
    blockers: list[str] = []
    if not pit_ok:
        share = pit.get("annual_published_share")
        blockers.append(
            "PIT estrito sem published_at/revisoes CVM" if not share else
            f"PIT estrito: so {share * 100:.0f}% da serie anual tem data de "
            f"protocolo na CVM (minimo {PIT_SHARE_MINIMA * 100:.0f}%)")
    if not survivorship_ok:
        # O motivo MEDIDO, nao o rotulo generico: "so 44% das companhias
        # relevantes tem ticker resolvido" diz ao usuario o tamanho da lacuna e
        # se ela esta diminuindo. "incompleto" nao diz nem uma coisa nem outra.
        blockers.append(str(surv.get("reason")
                            or "universo historico de deslistadas incompleto"))
    return {"ready": not blockers, "blockers": blockers}


def persist_readiness_snapshot(*, engine) -> str | None:
    """Registra o estado dos dados ao fim do ETL, sem depender da interface."""
    try:
        manifest = build_data_manifest(engine)
        readiness = validation_readiness(manifest)
        payload = {"manifest": manifest, "readiness": readiness}
        artifact_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        with engine.begin() as conn:
            if not _table_exists(conn, "market.b3_data_readiness_snapshots"):
                logger.info("b3_data_readiness_snapshots ausente; execute a migration 043")
                return None
            conn.execute(text("""
                INSERT INTO market.b3_data_readiness_snapshots (
                    universe_definition, snapshot_json, artifact_hash
                ) VALUES (:definition, CAST(:snapshot AS jsonb), :hash)
                ON CONFLICT (artifact_hash) DO NOTHING
            """), {
                "definition": manifest.get("universe_definition", "unknown"),
                "snapshot": _canonical(payload),
                "hash": artifact_hash,
            })
        return artifact_hash
    except Exception as exc:
        logger.warning("Nao foi possivel persistir snapshot de prontidao B3: %s", exc)
        return None


def persist_validation_run(
    *,
    engine,
    methodology_version: str,
    score_version: str,
    validation_mode: str,
    input_params: dict[str, Any],
    result_summary: dict[str, Any],
    status: str = "completed",
    notes: str | None = None,
) -> str | None:
    """Grava uma execucao; falha de auditoria nunca interrompe a analise da UI."""
    try:
        manifest = build_data_manifest(engine)
        readiness = validation_readiness(manifest)
        if not readiness["ready"] and status == "completed":
            status = "blocked"
        with engine.begin() as conn:
            if not _table_exists(conn, "market.b3_validation_runs"):
                logger.info("b3_validation_runs ausente; execute a migration 043")
                return None
            payload = {
                "methodology_version": methodology_version,
                "score_version": score_version,
                "validation_mode": validation_mode,
                "input_params": input_params,
                "result_summary": result_summary,
                "data_manifest": manifest,
                "status": status,
                "readiness": readiness,
            }
            artifact_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
            run_id = str(uuid4())
            now = datetime.now(timezone.utc)
            conn.execute(text("""
                INSERT INTO market.b3_validation_runs (
                    run_id, methodology_version, score_version, validation_mode,
                    data_as_of, status, input_params, result_summary,
                    data_manifest, artifact_hash, notes
                ) VALUES (
                    CAST(:run_id AS uuid), :methodology_version, :score_version, :validation_mode,
                    :data_as_of, :status, CAST(:input_params AS jsonb), CAST(:result_summary AS jsonb),
                    CAST(:data_manifest AS jsonb), :artifact_hash, :notes
                )
            """), {
                "run_id": run_id,
                "methodology_version": methodology_version,
                "score_version": score_version,
                "validation_mode": validation_mode,
                "data_as_of": now,
                "status": status,
                "input_params": _canonical(input_params),
                "result_summary": _canonical(result_summary),
                "data_manifest": _canonical(manifest),
                "artifact_hash": artifact_hash,
                "notes": notes,
            })
        return run_id
    except Exception as exc:
        logger.warning("Nao foi possivel persistir validacao B3: %s", exc)
        return None
