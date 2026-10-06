"""Publica no Supabase o que a ingestão brapi da B3 grava no armazém local.

Caminho A (outubro/2026): ``daily``, ``annual`` e ``setores`` saíram do GitHub
Actions e passaram a rodar com ``--warehouse`` na rotina local. Lá, cada
recálculo de indicador lia demonstrações e preços do Supabase — egress, numa
conta que estourou a cota (7,8 de 5 GB) — e cada consulta à brapi gravava o
payload bruto na nuvem (``brapi_raw_payloads``, 64 MB do disco de 500 MB).
Agora a ingestão escreve só no armazém, e este script leva à vitrine apenas as
tabelas que o app lê.

O que viaja, e como cada tabela sabe o que mudou:

- ``companies``, as três demonstrações, ``dividends`` e ``calculated_metrics``:
  linhas do armazém com ``updated_at`` posterior à marca da última publicação
  (``local_staging/marca_b3_brapi.json``, fora do git), menos um dia de
  sobreposição. Os gatilhos ``market.set_updated_at`` dessas tabelas só mexem
  na coluna quando o valor muda, e o upsert do repositório só regrava quando
  algum valor difere — reenviar a sobreposição custa leitura local, não escrita.
- ``historical_prices``: janela de datas (``--dias-precos``). A tabela não tem
  gatilho de ``updated_at``, que fica com a hora do INSERT; uma marca deixaria
  passar a correção de um pregão já gravado.
- ``calculated_metric_vintages``: safras com ``recorded_at`` depois da marca,
  ``ON CONFLICT DO NOTHING``. O ``b3_vintages`` semanal segue como conciliação
  completa.
- ``b3_data_readiness_snapshots``: o snapshot mais recente, se o hash faltar.
- ``assets``: não viaja. Só publica linha de ticker que a vitrine já tem; o
  resto sai em ``tickers_retidos``. Medido em 05/10/2026: o armazém tinha 8
  tickers que a vitrine não tem, e 3 deles eram fóssil de alias (a brapi
  devolveu ALOS3 para BRML3 e CSUD3 para CARD3 — o ``ticker_alias`` local tem
  pares que o da nuvem não tem, e vice-versa). Criar o ativo traria o fóssil de
  volta à vitrine. ``companies`` segue a mesma regra, por ``codigo_cvm``.

O que NÃO viaja: ``brapi_raw_payloads`` (procedência fica no armazém — por isso
``raw_payload_id`` sai das linhas, os ids são locais), ``id``, ``created_at`` e
``updated_at``. Métrica órfã, que deixou de ser calculável, sai pelo
``b3_metrics`` semanal, que faz backup antes de apagar; aqui não há DELETE.

Indicadores passam pelo mesmo portão de atualidade do ``b3_metrics`` (achado
B3-02): se o armazém tiver trimestre vigente anterior ao da vitrine, os
indicadores ficam de fora e o resto segue.

Teto de disco: o plano free do Supabase tem 500 MB e, passando disso, o banco
vira somente leitura. Antes de gravar cada tabela o script mede o banco e soma
o pior caso: toda linha candidata reescrita, ao custo médio por linha daquela
tabela (heap, índices e TOAST). Se a soma passar de ``--teto-disco-mb``, ele
para ali, não grava o resto, sai com código 1 e a rotina notifica. A marca das
tabelas não gravadas não avança, então nada se perde: volta na próxima.

Padrão da casa: DRY-RUN por omissão; nada é escrito sem ``--apply``, e só o
``--apply`` avança a marca.

    python scripts/publish_b3_brapi_from_local.py                       # simula
    python scripts/publish_b3_brapi_from_local.py --apply               # diário
    python scripts/publish_b3_brapi_from_local.py --apply --dias-precos 400
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

load_dotenv(ROOT / ".env")

ESTADO_PADRAO = ROOT / "local_staging" / "marca_b3_brapi.json"
EXCLUDED_COLUMNS = {"id", "created_at", "updated_at", "raw_payload_id"}
# Ordem de escrita: o pai antes dos filhos (FK para market.assets).
TABELAS_MARCA = (
    "income_statements",
    "balance_sheets",
    "cash_flow_statements",
    "dividends",
    "calculated_metrics",
)
SOBREPOSICAO = timedelta(days=1)
# Sem marca, olha 30 dias para trás: cobre as demonstrações renormalizadas no
# armazém em 05/10/2026 (B3-02), que nunca chegaram à vitrine.
JANELA_INICIAL = timedelta(days=30)
DIAS_PRECOS_PADRAO = 35
# Megabytes decimais, a unidade do plano (500 MB). Em 06/10/2026 o banco estava
# em 463,9 MB e o pior caso da primeira publicação era +55 MB.
TETO_DISCO_MB_PADRAO = 485
# Tabela que o ANALYZE nunca viu (reltuples < 0) não tem custo médio medido.
BYTES_POR_LINHA_SEM_ESTATISTICA = 2048


# ── Partes puras ────────────────────────────────────────────────────────────

def ler_estado(caminho: Path) -> dict[str, str]:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in dados.items()} if isinstance(dados, dict) else {}


def gravar_estado(caminho: Path, estado: dict[str, str]) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(caminho)


def corte(estado: dict[str, str], tabela: str, agora: datetime) -> datetime:
    """Desde quando ler a tabela: marca menos sobreposição, ou a janela inicial."""
    marca = estado.get(tabela)
    if marca:
        try:
            visto = datetime.fromisoformat(marca)
        except ValueError:
            visto = None
        if visto is not None:
            if visto.tzinfo is None:
                visto = visto.replace(tzinfo=timezone.utc)
            return visto - SOBREPOSICAO
    return agora - JANELA_INICIAL


def avancar_marca(estado: dict[str, str], tabela: str, maximo) -> dict[str, str]:
    """Nova marca = maior carimbo publicado. Nunca recua."""
    if maximo is None:
        return dict(estado)
    if isinstance(maximo, datetime) and maximo.tzinfo is None:
        maximo = maximo.replace(tzinfo=timezone.utc)
    novo = dict(estado)
    atual = estado.get(tabela)
    if atual:
        try:
            anterior = datetime.fromisoformat(atual)
            if anterior.tzinfo is None:
                anterior = anterior.replace(tzinfo=timezone.utc)
            if anterior >= maximo:
                return novo
        except ValueError:
            pass
    novo[tabela] = maximo.isoformat()
    return novo


def colunas_publicaveis(origem: list[str], destino: set[str]) -> list[str]:
    """Colunas do armazém que a vitrine também tem, sem as de controle."""
    return [c for c in origem if c in destino and c not in EXCLUDED_COLUMNS]


def reter_ausentes(linhas: list[dict], presentes: set[str], chave: str = "ticker"
                   ) -> tuple[list[dict], set[str]]:
    """Separa as linhas cuja chave a vitrine já tem das que ficam retidas."""
    publicaveis, retidos = [], set()
    for r in linhas:
        valor = r.get(chave)
        if valor in presentes:
            publicaveis.append(r)
        else:
            retidos.add(str(valor))
    return publicaveis, retidos


def cabe_no_disco(usado: int, linhas: int, bytes_por_linha: float,
                  teto_mb: float) -> bool:
    """Se o pior caso (toda linha reescrita) ainda deixa o banco abaixo do teto.

    Teto zero ou negativo desliga a guarda.
    """
    if teto_mb <= 0 or linhas <= 0:
        return True
    return usado + linhas * bytes_por_linha <= teto_mb * 1_000_000


# ── Acesso ao banco ─────────────────────────────────────────────────────────

def _columns(conn, table: str) -> list[str]:
    """Colunas graváveis (geradas ficam de fora: dividends.event_date)."""
    return [
        str(row[0])
        for row in conn.execute(text("""
            SELECT column_name, is_generated
            FROM information_schema.columns
            WHERE table_schema='market' AND table_name=:table
            ORDER BY ordinal_position
        """), {"table": table})
        if str(row[1]) != "ALWAYS"
    ]


def _ler(src, tabela: str, colunas: list[str], filtro: str, params: dict) -> list[dict]:
    lista = ",".join(f'"{c}"' for c in colunas)
    return [dict(r) for r in src.execute(
        text(f"SELECT {lista} FROM market.{tabela} WHERE {filtro}"), params
    ).mappings()]


def _maximo(src, tabela: str, coluna: str, desde: datetime):
    return src.execute(text(
        f"SELECT max({coluna}) FROM market.{tabela} WHERE {coluna} >= :desde"
    ), {"desde": desde}).scalar()


def _usado(dst) -> int:
    return int(dst.execute(text(
        "SELECT pg_database_size(current_database())")).scalar() or 0)


def _bytes_por_linha(dst, tabela: str) -> float:
    """Custo médio de uma linha da tabela no destino, com índices e TOAST."""
    total, tuplas = dst.execute(text(
        "SELECT pg_total_relation_size(c.oid), c.reltuples "
        "FROM pg_class c WHERE c.oid = to_regclass(:t)"
    ), {"t": f"market.{tabela}"}).one()
    if not tuplas or tuplas <= 0:
        return float(BYTES_POR_LINHA_SEM_ESTATISTICA)
    return float(total) / float(tuplas)


def _bloqueio_atualidade(src, dst) -> str | None:
    from core import b3_atualidade_trimestral as atualidade
    desde = atualidade.desde_ano(datetime.now(timezone.utc).date())
    return atualidade.bloqueio_de_publicacao(atualidade.ler_contagens(src, desde),
                                             atualidade.ler_contagens(dst, desde))


def _inserir_safras(dst, linhas: list[dict]) -> int:
    from psycopg2.extras import execute_values

    if not linhas:
        return 0
    cols = list(linhas[0].keys())
    sql = (f"INSERT INTO market.calculated_metric_vintages ({','.join(cols)}) VALUES %s "
           "ON CONFLICT (ticker,period,year,quarter,metric_name,available_at,recorded_at) "
           "DO NOTHING")
    cur = dst.connection.cursor()
    try:
        execute_values(cur, sql, [tuple(r[c] for c in cols) for r in linhas], page_size=500)
    finally:
        cur.close()
    return len(linhas)


# ── Publicação ──────────────────────────────────────────────────────────────

def publish(*, apply: bool = False, dias_precos: int = DIAS_PRECOS_PADRAO,
            estado_path: Path = ESTADO_PADRAO, hoje: date | None = None,
            teto_disco_mb: float = TETO_DISCO_MB_PADRAO) -> dict:
    from data_pipeline.market import repository
    from scripts.publish_b3_tickers_from_local import _remote_url
    from scripts.publish_b3_vintages_from_local import publicar_prontidao
    from scripts.publish_fii_selection_from_local import _warehouse_url
    from scripts.publish_us_snapshot import _engine

    remote_url = _remote_url()
    if not remote_url:
        raise RuntimeError("Supabase não configurado — defina SUPABASE_DB_URL no .env")

    agora = datetime.now(timezone.utc)
    hoje = hoje or agora.date()
    estado = ler_estado(estado_path)
    novo_estado = dict(estado)
    source = _engine(_warehouse_url())
    target = _engine(remote_url)
    repository.reset_db_cols_cache()
    resultado: dict = {
        "modo": "APLICADO" if apply else "SIMULACAO (nada foi escrito)",
        "tabelas": {},
    }

    try:
        with source.connect() as src, target.connect() as dst:
            destino = {t: set(_columns(dst, t)) for t in
                       ("companies", "historical_prices", *TABELAS_MARCA,
                        "calculated_metric_vintages")}
            bloqueio = _bloqueio_atualidade(src, dst)
            dst.rollback()

            # Linhas a publicar, lidas do armazém antes de abrir escrita.
            lotes: dict[str, list[dict]] = {}
            maximos: dict[str, object] = {}
            desde_emp = corte(estado, "companies", agora)
            cols = colunas_publicaveis(_columns(src, "companies"), destino["companies"])
            lotes["companies"] = _ler(src, "companies", cols,
                                      "updated_at >= :d", {"d": desde_emp})
            maximos["companies"] = _maximo(src, "companies", "updated_at", desde_emp)

            inicio_precos = hoje - timedelta(days=int(dias_precos))
            cols = colunas_publicaveis(_columns(src, "historical_prices"),
                                       destino["historical_prices"])
            lotes["historical_prices"] = _ler(src, "historical_prices", cols,
                                              "date >= :d", {"d": inicio_precos})

            for tabela in TABELAS_MARCA:
                desde = corte(estado, tabela, agora)
                cols = colunas_publicaveis(_columns(src, tabela), destino[tabela])
                lotes[tabela] = _ler(src, tabela, cols, "updated_at >= :d", {"d": desde})
                maximos[tabela] = _maximo(src, tabela, "updated_at", desde)

            desde_safra = corte(estado, "calculated_metric_vintages", agora)
            cols = [c for c in _columns(src, "calculated_metric_vintages")
                    if c in destino["calculated_metric_vintages"] and c != "id"]
            lotes["calculated_metric_vintages"] = _ler(
                src, "calculated_metric_vintages", cols,
                "recorded_at >= :d", {"d": desde_safra})
            maximos["calculated_metric_vintages"] = _maximo(
                src, "calculated_metric_vintages", "recorded_at", desde_safra)

            if bloqueio:
                resultado["indicadores_retidos"] = bloqueio
                lotes["calculated_metrics"] = []
                lotes["calculated_metric_vintages"] = []
                maximos.pop("calculated_metrics", None)
                maximos.pop("calculated_metric_vintages", None)

            # Só linha de ticker (e empresa) que a vitrine já conhece.
            presentes = set(dst.execute(text(
                "SELECT ticker FROM market.assets")).scalars())
            cvms = set(dst.execute(text(
                "SELECT codigo_cvm FROM market.companies")).scalars())
            dst.rollback()
            retidos: set[str] = set()
            for tabela in ("historical_prices", *TABELAS_MARCA,
                           "calculated_metric_vintages"):
                lotes[tabela], fora = reter_ausentes(lotes[tabela], presentes)
                retidos |= fora
            lotes["companies"], fora_cvm = reter_ausentes(
                lotes["companies"], cvms, chave="codigo_cvm")
            resultado["tickers_retidos"] = sorted(retidos)
            resultado["empresas_retidas"] = len(fora_cvm)
            resultado["janela_precos_desde"] = inicio_precos.isoformat()
            for tabela, linhas in lotes.items():
                resultado["tabelas"][tabela] = {"linhas": len(linhas)}

            usado = _usado(dst)
            pior = sum(len(linhas) * _bytes_por_linha(dst, t)
                       for t, linhas in lotes.items() if linhas)
            dst.rollback()
            resultado["disco_mb"] = {"usado": round(usado / 1e6, 1),
                                     "pior_caso": round(pior / 1e6, 1),
                                     "teto": teto_disco_mb}
            if not apply:
                return resultado

            # Cada tabela na sua transação; a marca dela só avança depois do
            # COMMIT. Antes de cada uma, o teto de disco.
            # Tamanho do banco antes de cada tabela e no fim: a diferença é o
            # que cada uma custou de fato.
            disco: dict[str, int] = {}
            resultado["disco_bytes"] = disco

            def cabe(tabela: str, linhas: list[dict]) -> bool:
                usado = _usado(dst)
                por_linha = _bytes_por_linha(dst, tabela)
                dst.rollback()
                disco[tabela] = usado
                if cabe_no_disco(usado, len(linhas), por_linha, teto_disco_mb):
                    return True
                resultado["parado_por_disco"] = {
                    "tabela": tabela,
                    "linhas": len(linhas),
                    "usado_mb": round(usado / 1e6, 1),
                    "pior_caso_mb": round(len(linhas) * por_linha / 1e6, 1),
                    "teto_mb": teto_disco_mb,
                }
                return False

            def gravar(tabela: str, escrever) -> bool:
                nonlocal novo_estado
                if not cabe(tabela, lotes[tabela]):
                    return False
                with dst.begin():
                    dst.execute(text("SET LOCAL statement_timeout='300s'"))
                    escrever(lotes[tabela])
                if tabela in maximos:
                    novo_estado = avancar_marca(novo_estado, tabela, maximos[tabela])
                    gravar_estado(estado_path, novo_estado)
                return True

            seguiu = True
            for tabela in ("companies", "historical_prices", *TABELAS_MARCA):
                seguiu = gravar(tabela, lambda linhas, t=tabela:
                                repository.upsert(dst, t, linhas))
                if not seguiu:
                    break
            if seguiu:
                seguiu = gravar("calculated_metric_vintages",
                                lambda linhas: _inserir_safras(dst, linhas))
            disco["fim"] = _usado(dst)
            dst.rollback()

        if "parado_por_disco" in resultado:
            resultado["marcas"] = novo_estado
            return resultado
        resultado["prontidao_publicada"] = publicar_prontidao(source, target)
        resultado["marcas"] = novo_estado
    finally:
        source.dispose()
        target.dispose()
    return resultado


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="grava de fato (sem isto, apenas simula e não avança a marca)")
    p.add_argument("--dias-precos", type=int, default=DIAS_PRECOS_PADRAO,
                   help=f"pregões dos últimos N dias (padrão {DIAS_PRECOS_PADRAO}; "
                        "a rodada semanal do annual usa 400)")
    p.add_argument("--estado", default=str(ESTADO_PADRAO),
                   help="arquivo da marca por tabela (fora do git)")
    p.add_argument("--teto-disco-mb", type=float, default=TETO_DISCO_MB_PADRAO,
                   help=f"para antes de a tabela seguinte poder levar o Supabase "
                        f"acima disto, em MB decimais (padrão {TETO_DISCO_MB_PADRAO}; "
                        "0 desliga)")
    args = p.parse_args(argv)
    saida = publish(apply=bool(args.apply), dias_precos=args.dias_precos,
                    estado_path=Path(args.estado), teto_disco_mb=args.teto_disco_mb)
    print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
    return 1 if "parado_por_disco" in saida else 0


if __name__ == "__main__":
    raise SystemExit(main())
