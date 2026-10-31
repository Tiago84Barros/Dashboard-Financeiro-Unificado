"""
scripts/unificar_categorias_cartao.py
Leva para as categorias de sistema o que a fatura do cartão gravou com nomes
próprios.

Até 31/10/2026 o importador da fatura criava, na conta de cada usuário,
categorias com nomes que só ele usava (``Saúde & Bem-estar``, ``Lazer &
Entretenimento``, ``Transporte & Combustível``...), enquanto o extrato bancário
e o lançamento manual usavam as de sistema (``Saúde``, ``Lazer``,
``Combustível``...). O mesmo gasto caía em duas fatias conforme fosse pago no
débito ou no cartão. O importador já usa os nomes de sistema
(``core.card_categorization``); este script move o que ficou gravado.

O QUE ELE FAZ, por categoria antiga (só as criadas na conta de um usuário):
  1. Repõe cada referência no nome novo: lançamentos, movimentos de extrato
     (categoria sugerida e confirmada) e regras de classificação do extrato.
     ``Transporte & Combustível`` se divide pela descrição: posto vai para
     ``Combustível``, o resto para ``Transporte`` — a mesma regra do importador
     (``core.card_categorization.nome_unificado``).
  2. Orçamento: o limite vale do mês gravado em diante, até o próximo registro
     (``core.orcamento.vigentes``). Os cronogramas da categoria antiga e da nova
     se FUNDEM: em cada mês em que qualquer um dos dois muda, a nova recebe a
     soma dos dois limites vigentes (encerrado vale zero). Assim nenhum mês
     perde limite, e o gasto das duas também passa a somar. O relatório mostra
     cada mês que muda.
  3. Regras aprendidas na tela do cartão: troca o nome antigo pelo novo.
  4. Apaga a categoria antiga, agora vazia — senão ela segue no seletor do
     lançamento manual e a divisão volta. Antes do DELETE, confere no catálogo
     do Postgres TODA chave estrangeira que aponta para ``categories``: se
     qualquer linha ainda apontar para ela (inclusive em tabela que este
     script não conhece), a categoria fica e o relatório diz onde.

SEGURANÇA / REVERSIBILIDADE:
  - Dry-run por padrão: nada é gravado sem ``--aplicar``.
  - Tudo numa transação só. Antes de confirmá-la, escreve o de-para completo em
    ``migration/backup/`` (fora do git), com a linha inteira de cada categoria
    e orçamento apagado; ``--reverter`` refaz o estado anterior a partir dele.
  - A reversão só devolve o que continua como a migração deixou: lançamento
    recategorizado à mão depois dela fica como está, e categoria criada pela
    migração só sai se nada apontar para ela.

USO:
  python scripts/unificar_categorias_cartao.py                     # dry-run
  python scripts/unificar_categorias_cartao.py --aplicar           # aplica
  python scripts/unificar_categorias_cartao.py --reverter <backup.json>
  python scripts/unificar_categorias_cartao.py --usuario <uuid> ...  # uma conta só

Requer as credenciais do banco (.env / secrets) e MOCK_MODE desativado.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

# Permite rodar de qualquer diretório (adiciona a raiz do projeto ao path).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.card_categorization import (  # noqa: E402
    CATEGORY_TYPE,
    NOMES_LEGADOS,
    nome_unificado,
)

#: (tabela, coluna que aponta para a categoria, expressão do texto que decide
#: a divisão de ``Transporte & Combustível``). Expressões fixas, sem entrada de
#: usuário: entram no SQL por interpolação.
REFERENCIAS: tuple[tuple[str, str, str], ...] = (
    ("transactions", "category_id", "description"),
    ("bank_statement_movements", "categoria_id", "descricao_original"),
    ("bank_statement_movements", "categoria_confirmada_id", "descricao_original"),
    ("bank_statement_classification_rules", "category_id",
     "COALESCE(NULLIF(palavra_chave, ''), descricao_normalizada)"),
)

_SQL_LEGADAS = """
    SELECT c.id::text AS id, c.user_id::text AS user_id, c.name AS nome,
           c.type AS tipo, to_jsonb(c) AS linha
    FROM   categories c
    WHERE  c.name = ANY(:nomes)
    ORDER  BY c.user_id, c.name
"""

# Mesma busca de ``core.controle._get_or_create_category``: o nome, com o tipo
# esperado na frente (uma "Saúde" de receita da pessoa não vence a "Saúde" de
# despesa do sistema) e, no empate, a categoria da pessoa.
_SQL_ALVO = """
    SELECT id::text
    FROM   categories
    WHERE  LOWER(name) = LOWER(:nome)
      AND  (user_id = CAST(:uid AS uuid) OR user_id IS NULL)
    ORDER  BY CASE WHEN type = :tipo THEN 0 ELSE 1 END,
              CASE WHEN user_id = CAST(:uid AS uuid) THEN 0 ELSE 1 END
    LIMIT  1
"""

# Toda coluna com chave estrangeira para ``categories``, lida do catálogo — não
# de uma lista nossa. ``ON DELETE SET NULL`` e ``CASCADE`` deixam o DELETE
# passar calado, então "o DELETE não falhou" não prova que a categoria estava
# vazia: é preciso contar.
_SQL_FKS_PARA_CATEGORIES = """
    SELECT con.conrelid::regclass::text AS tabela,
           quote_ident(att.attname)     AS coluna
    FROM   pg_constraint con
    JOIN   pg_attribute  att
           ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
    WHERE  con.contype   = 'f'
      AND  con.confrelid = 'public.categories'::regclass
    ORDER  BY 1, 2
"""

_DIR_BACKUP = os.path.join(os.path.dirname(__file__), "..", "migration", "backup")


# ── leitura ──────────────────────────────────────────────────────────────────

def _existe(conn, tabela: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"),
                             {"t": f"public.{tabela}"}).scalar())


def categorias_legadas(conn, usuario: str | None = None) -> tuple[list[dict], list[dict]]:
    """``(da_conta, de_sistema)``. Só as da conta são migradas: a de sistema é
    de todo mundo, e o importador nunca a criou — ela só é relatada."""
    linhas = [dict(r._mapping) for r in
              conn.execute(text(_SQL_LEGADAS), {"nomes": list(NOMES_LEGADOS)})]
    de_sistema = [r for r in linhas if not r["user_id"]]
    da_conta = [r for r in linhas if r["user_id"]
                and (usuario is None or r["user_id"] == usuario)]
    return da_conta, de_sistema


def referencias_a(conn, categoria_id: str) -> list[tuple[str, str, int]]:
    """``(tabela, coluna, linhas)`` de cada lugar que ainda aponta para a categoria."""
    achadas = []
    for fk in conn.execute(text(_SQL_FKS_PARA_CATEGORIES)).fetchall():
        # Identificadores vêm do catálogo, já citados (regclass/quote_ident).
        n = conn.execute(text(
            f"SELECT COUNT(*) FROM {fk.tabela} WHERE {fk.coluna} = CAST(:cid AS uuid)"),
            {"cid": categoria_id}).scalar()
        if n:
            achadas.append((fk.tabela.removeprefix("public."), fk.coluna, int(n)))
    return achadas


def _descrever(refs: list[tuple[str, str, int]]) -> str:
    return "ainda há referência em " + "; ".join(f"{t}.{c}: {n}" for t, c, n in refs)


def prever_mantidas(conn, plano: dict) -> None:
    """Dry-run: tira de ``categorias`` (e põe em ``mantidas``) a que tem linha
    numa coluna que este script não move — subcategoria, tabela desconhecida."""
    movidas = {(t, c) for t, c, _ in REFERENCIAS} | {("budgets", "category_id")}
    ficam = []
    for c in plano["categorias"]:
        resto = [r for r in referencias_a(conn, c["id"]) if (r[0], r[1]) not in movidas]
        if resto:
            plano["mantidas"].append({**c, "motivo": _descrever(resto)})
            ficam.append(c["id"])
    plano["categorias"] = [c for c in plano["categorias"] if c["id"] not in ficam]


def _buscar_alvo(conn, uid: str, nome: str) -> str | None:
    return conn.execute(text(_SQL_ALVO), {
        "nome": nome, "uid": uid, "tipo": CATEGORY_TYPE.get(nome, "expense")}).scalar()


def resolvedor_somente_leitura(conn):
    """Acha a categoria-alvo sem criar. ``None``: será criada ao aplicar."""
    cache: dict[tuple[str, str], str | None] = {}

    def resolver(uid: str, nome: str) -> str | None:
        if (uid, nome) not in cache:
            cache[(uid, nome)] = _buscar_alvo(conn, uid, nome)
        return cache[(uid, nome)]

    return resolver


def resolvedor_que_cria(conn, criadas: list[dict]):
    """Acha ou cria a categoria-alvo — a mesma função do importador da fatura.

    Cada categoria criada vai para ``criadas``: a reversão a apaga se nada
    apontar para ela.
    """
    from core.controle import _get_or_create_category

    cache: dict[tuple[str, str], str | None] = {}

    def resolver(uid: str, nome: str) -> str | None:
        if (uid, nome) not in cache:
            existia = _buscar_alvo(conn, uid, nome)
            cid = _get_or_create_category(conn, uid, nome, CATEGORY_TYPE.get(nome, "expense"))
            if not cid:
                raise RuntimeError(f"Não foi possível obter/criar a categoria {nome!r}.")
            if not existia:
                criadas.append({"id": cid, "user_id": uid, "nome": nome})
            cache[(uid, nome)] = cid
        return cache[(uid, nome)]

    return resolver


# ── orçamento ────────────────────────────────────────────────────────────────

def _vigente(cronograma: list[tuple[str, Decimal]], mes: str) -> Decimal:
    """Limite em vigor no mês, pela regra de ``core.orcamento.vigentes``: o
    último registro até o mês; sem registro, ou encerrado (<= 0), vale zero."""
    anteriores = [(m, v) for m, v in cronograma if m <= mes]
    if not anteriores:
        return Decimal(0)
    valor = Decimal(str(max(anteriores)[1]))
    return valor if valor > 0 else Decimal(0)


def mesclar_orcamentos(cronogramas: list[list[tuple[str, Decimal]]]) -> list[tuple[str, Decimal]]:
    """Um cronograma cujo limite vigente em CADA mês é a soma dos vigentes das
    fontes. Basta um registro por mês em que alguma fonte muda; entre eles o
    limite se carrega, como nas fontes. Soma zero vira registro 0 (encerra)."""
    meses = sorted({m for cron in cronogramas for m, _ in cron})
    return [(m, sum((_vigente(c, m) for c in cronogramas), Decimal(0))) for m in meses]


def _planejar_orcamentos(conn, legadas: list[dict], resolver) -> list[dict]:
    # As categorias antigas que caem no mesmo alvo (por conta) se fundem juntas.
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for leg in legadas:
        # Orçamento não tem descrição: o nome que se divide vai para o
        # destino padrão (``Transporte``).
        grupos[(leg["user_id"], nome_unificado(leg["nome"], ""))].append(leg)

    ops = []
    for (uid, novo), legs in grupos.items():
        legado = [dict(r._mapping) for r in conn.execute(text(
            "SELECT id::text AS id, category_id::text AS de, month_year::text AS mes, "
            "amount_limit AS limite, to_jsonb(b) AS linha FROM budgets b "
            "WHERE category_id = ANY(CAST(:ids AS uuid[])) ORDER BY month_year"),
            {"ids": [leg["id"] for leg in legs]})]
        if not legado:
            continue
        # Só resolve (e, ao aplicar, cria) o alvo se houver orçamento.
        para = resolver(uid, novo)
        alvo_antes = [] if not para else [dict(r._mapping) for r in conn.execute(text(
            "SELECT id::text AS id, month_year::text AS mes, amount_limit AS limite "
            "FROM budgets WHERE user_id = CAST(:uid AS uuid) "
            "AND category_id = CAST(:cid AS uuid) ORDER BY month_year"),
            {"uid": uid, "cid": para})]
        cronogramas = [[(r["mes"], r["limite"]) for r in alvo_antes]]
        cronogramas += [[(r["mes"], r["limite"]) for r in legado if r["de"] == leg["id"]]
                        for leg in legs]
        id_por_mes = {r["mes"]: r["id"] for r in alvo_antes}
        ops.append({
            "user_id": uid, "para": para, "para_nome": novo,
            "de_nomes": sorted({leg["nome"] for leg in legs}),
            "legado": legado, "alvo_antes": alvo_antes,
            "alvo_depois": [{"mes": m, "limite": v, "id": id_por_mes.get(m)}
                            for m, v in mesclar_orcamentos(cronogramas)],
        })
    return ops


# ── plano ────────────────────────────────────────────────────────────────────

def planejar(conn, legadas: list[dict], resolver) -> dict:
    """Tudo o que muda, sem gravar nada. ``para`` é ``None`` só no dry-run,
    quando a categoria-alvo ainda não existe."""
    plano: dict = {"categorias": [], "referencias": [], "orcamentos": [],
                   "regras_cartao": [], "mantidas": [], "criadas": []}
    tabelas = {tabela for tabela, _, _ in REFERENCIAS} | {"budgets", "card_category_rules"}
    existentes = {t for t in tabelas if _existe(conn, t)}

    for leg in legadas:
        uid, de, nome = leg["user_id"], leg["id"], leg["nome"]

        for tabela, coluna, texto in REFERENCIAS:
            if tabela not in existentes:
                continue
            for r in conn.execute(text(
                    f"SELECT id::text AS id, {texto} AS texto FROM {tabela} "
                    f"WHERE {coluna} = CAST(:cid AS uuid)"), {"cid": de}):
                novo = nome_unificado(nome, r.texto)
                plano["referencias"].append({
                    "tabela": tabela, "coluna": coluna, "id": r.id, "user_id": uid,
                    "de": de, "de_nome": nome, "para": resolver(uid, novo), "para_nome": novo,
                })

        if "card_category_rules" in existentes:
            for r in conn.execute(text(
                    "SELECT id::text AS id, palavra_chave FROM card_category_rules "
                    "WHERE user_id = CAST(:uid AS uuid) AND category_name = :nome"),
                    {"uid": uid, "nome": nome}):
                plano["regras_cartao"].append({
                    "id": r.id, "user_id": uid, "palavra_chave": r.palavra_chave,
                    "de": nome, "para": nome_unificado(nome, r.palavra_chave)})

        plano["categorias"].append({"id": de, "user_id": uid, "nome": nome,
                                    "linha": leg["linha"]})

    if "budgets" in existentes:
        plano["orcamentos"] = _planejar_orcamentos(conn, legadas, resolver)
    return plano


# ── relatório ────────────────────────────────────────────────────────────────

def relatar(plano: dict, de_sistema: list[dict], *, aplicado: bool = False) -> None:
    if de_sistema:
        print("ATENÇÃO — categorias DE SISTEMA com nome antigo (não são tocadas):")
        for c in de_sistema:
            print(f"   {c['nome']} ({c['tipo']}) id={c['id']}")
        print()
    contas = sorted({c["user_id"] for c in plano["categorias"] + plano["mantidas"]})
    if not contas:
        print("Nenhuma categoria com nome antigo da fatura. Nada a migrar.")
        return
    apagar = "apagada" if aplicado else "será apagada"
    for uid in contas:
        print(f"Conta {uid}")
        destinos: Counter = Counter()
        criadas = set()
        for ref in plano["referencias"]:
            if ref["user_id"] != uid:
                continue
            destinos[(ref["de_nome"], ref["para_nome"], ref["tabela"])] += 1
            if not ref["para"]:
                criadas.add(ref["para_nome"])
        for (de, para, tabela), n in sorted(destinos.items()):
            print(f"   {de:<34} -> {para:<14} {n:>5} em {tabela}")
        for o in plano["orcamentos"]:
            if o["user_id"] != uid:
                continue
            if not o["para"]:
                criadas.add(o["para_nome"])
            antes = {r["mes"]: r["limite"] for r in o["alvo_antes"]}
            print(f"   orçamento de {o['para_nome']}, somando {', '.join(o['de_nomes'])} "
                  f"({len(o['legado'])} registro(s) antigo(s) saem):")
            for r in o["alvo_depois"]:
                era = antes.get(r["mes"])
                if era is not None and Decimal(str(era)) == Decimal(str(r["limite"])):
                    continue
                print(f"      a partir de {r['mes']}: "
                      f"{'—' if era is None else era} -> {r['limite']}")
        for r in plano["regras_cartao"]:
            if r["user_id"] == uid:
                print(f"   regra do cartão '{r['palavra_chave']}': {r['de']} -> {r['para']}")
        for c in plano["categorias"]:
            if c["user_id"] == uid:
                print(f"   categoria '{c['nome']}' {apagar} (vazia depois da migração)")
        for c in plano["mantidas"]:
            if c["user_id"] == uid:
                print(f"   categoria '{c['nome']}' FICA: {c['motivo']}")
        if criadas:
            print(f"   categorias criadas na conta: {', '.join(sorted(criadas))}")
        print()


# ── aplicar ──────────────────────────────────────────────────────────────────

def _caminho_backup() -> str:
    """Caminho NOVO para o de-para. Nunca reaproveita um existente: duas
    execuções no mesmo segundo sobrescreviam o backup da primeira, e com ele a
    única forma de reverter. O arquivo nasce vazio aqui (modo ``x``)."""
    os.makedirs(_DIR_BACKUP, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    caminho = os.path.abspath(os.path.join(_DIR_BACKUP, f"unificar_categorias_cartao_{carimbo}.json"))
    with open(caminho, "x", encoding="utf-8"):
        pass
    return caminho


def executar(conn, plano: dict) -> None:
    """Grava o plano. Preenche o id dos orçamentos inseridos e move para
    ``mantidas`` a categoria antiga que ainda tiver referência."""
    for ref in plano["referencias"]:
        conn.execute(text(
            f"UPDATE {ref['tabela']} SET {ref['coluna']} = CAST(:para AS uuid) "
            f"WHERE id = CAST(:id AS uuid) AND {ref['coluna']} = CAST(:de AS uuid)"),
            ref)

    for o in plano["orcamentos"]:
        for r in o["alvo_depois"]:
            if r["id"]:
                conn.execute(text(
                    "UPDATE budgets SET amount_limit = :limite WHERE id = CAST(:id AS uuid)"), r)
            else:
                r["id"] = conn.execute(text(
                    "INSERT INTO budgets (user_id, category_id, month_year, amount_limit) "
                    "VALUES (CAST(:uid AS uuid), CAST(:cid AS uuid), CAST(:mes AS date), :limite) "
                    "RETURNING id::text"),
                    {"uid": o["user_id"], "cid": o["para"], "mes": r["mes"],
                     "limite": r["limite"]}).scalar()
        conn.execute(text("DELETE FROM budgets WHERE id = ANY(CAST(:ids AS uuid[]))"),
                     {"ids": [r["id"] for r in o["legado"]]})

    for r in plano["regras_cartao"]:
        conn.execute(text(
            "UPDATE card_category_rules SET category_name = :para, updated_at = now() "
            "WHERE id = CAST(:id AS uuid)"), r)

    apagadas = []
    for c in plano["categorias"]:
        restantes = referencias_a(conn, c["id"])
        if restantes:
            plano["mantidas"].append({**c, "motivo": _descrever(restantes)})
            continue
        conn.execute(text("DELETE FROM categories WHERE id = CAST(:id AS uuid)"), c)
        apagadas.append(c)
    plano["categorias"] = apagadas


def cmd_aplicar(engine, usuario: str | None) -> int:
    with engine.connect() as conn:
        legadas, de_sistema = categorias_legadas(conn, usuario)
    if not legadas:
        relatar({"categorias": [], "mantidas": []}, de_sistema)
        return 0

    backup = _caminho_backup()
    try:
        with engine.begin() as conn:
            # Dentro da transação: as categorias-alvo que faltarem são criadas
            # aqui e somem junto se algo falhar.
            criadas: list[dict] = []
            plano = planejar(conn, legadas, resolvedor_que_cria(conn, criadas))
            plano["criadas"] = criadas
            executar(conn, plano)
            # O de-para sai DEPOIS de executar (para levar o id dos orçamentos
            # inseridos) e ANTES do commit: se o commit falhar, o except
            # apaga o arquivo; se a escrita falhar, nada é confirmado.
            with open(backup, "w", encoding="utf-8") as fh:
                json.dump({"gerado_em": datetime.now().isoformat(), "plano": plano},
                          fh, ensure_ascii=False, indent=1, default=str)
    except Exception:
        # Nada foi gravado: um backup de algo que não aconteceu só confundiria
        # uma reversão futura.
        if os.path.exists(backup):
            os.remove(backup)
        raise

    relatar(plano, de_sistema, aplicado=True)
    print(f"Aplicado. De-para gravado em: {backup}")
    print("   (guarde este arquivo — é o que permite reverter)")
    _limpar_caches()
    return 0


# ── reverter ─────────────────────────────────────────────────────────────────

def cmd_reverter(engine, caminho: str) -> int:
    if not os.path.isfile(caminho):
        print(f"Backup não encontrado: {caminho}")
        return 1
    with open(caminho, encoding="utf-8") as fh:
        plano = json.load(fh)["plano"]

    def _linha(valor) -> str:
        return valor if isinstance(valor, str) else json.dumps(valor, default=str)

    voltaram = Counter()
    ficaram = Counter()
    with engine.begin() as conn:
        # Primeiro as categorias: as referências precisam delas para voltar.
        for c in plano["categorias"]:
            conn.execute(text(
                "INSERT INTO categories SELECT * FROM "
                "jsonb_populate_record(NULL::categories, CAST(:linha AS jsonb)) "
                "ON CONFLICT (id) DO NOTHING"), {"linha": _linha(c["linha"])})
        for ref in plano["referencias"]:
            n = conn.execute(text(
                f"UPDATE {ref['tabela']} SET {ref['coluna']} = CAST(:de AS uuid) "
                f"WHERE id = CAST(:id AS uuid) AND {ref['coluna']} = CAST(:para AS uuid)"),
                ref).rowcount
            (voltaram if n else ficaram)[ref["tabela"]] += 1

        for o in plano["orcamentos"]:
            antes = {r["id"]: r for r in o["alvo_antes"]}
            for r in o["alvo_depois"]:
                params = {"id": r["id"], "depois": str(r["limite"])}
                if r["id"] in antes:
                    n = conn.execute(text(
                        "UPDATE budgets SET amount_limit = CAST(:antes AS numeric) "
                        "WHERE id = CAST(:id AS uuid) AND amount_limit = CAST(:depois AS numeric)"),
                        {**params, "antes": str(antes[r["id"]]["limite"])}).rowcount
                else:
                    n = conn.execute(text(
                        "DELETE FROM budgets "
                        "WHERE id = CAST(:id AS uuid) AND amount_limit = CAST(:depois AS numeric)"),
                        params).rowcount
                (voltaram if n else ficaram)["budgets (categoria nova)"] += 1
            for r in o["legado"]:
                conn.execute(text(
                    "INSERT INTO budgets SELECT * FROM "
                    "jsonb_populate_record(NULL::budgets, CAST(:linha AS jsonb)) "
                    "ON CONFLICT DO NOTHING"), {"linha": _linha(r["linha"])})
                voltaram["budgets (categoria antiga)"] += 1

        for r in plano["regras_cartao"]:
            n = conn.execute(text(
                "UPDATE card_category_rules SET category_name = :de, updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND category_name = :para"), r).rowcount
            (voltaram if n else ficaram)["card_category_rules"] += 1

        # Por último, as que a migração criou: saem só se NADA apontar para
        # elas. Contar, e não confiar no DELETE: movimento de extrato
        # (SET NULL) e regra do extrato (CASCADE) não fazem o DELETE falhar —
        # perderiam a classificação em silêncio.
        for c in plano.get("criadas", []):
            if referencias_a(conn, c["id"]):
                ficaram["categorias criadas pela migração (em uso, ficam)"] += 1
                continue
            conn.execute(text("DELETE FROM categories WHERE id = CAST(:id AS uuid)"), c)
            voltaram["categorias criadas pela migração (apagadas)"] += 1

    print(f"Revertido. {len(plano['categorias'])} categoria(s) recriada(s).")
    if voltaram:
        print("Voltaram ao estado anterior:")
        for tabela, n in sorted(voltaram.items()):
            print(f"   {tabela}: {n}")
    if ficaram:
        print("Ficaram como estão (mudaram depois da migração, ou já estão em uso):")
        for tabela, n in sorted(ficaram.items()):
            print(f"   {tabela}: {n}")
    _limpar_caches()
    return 0


def _limpar_caches() -> None:
    try:
        from core.controle import _clear_controle_caches
        _clear_controle_caches()
    except Exception:  # noqa: BLE001 - cache é conveniência, não requisito
        pass


def main(argv: list[str] | None = None) -> int:
    from core.config import settings
    from core.database import get_engine

    ap = argparse.ArgumentParser(
        description="Unifica as categorias que a fatura do cartão criou com nomes próprios.")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--aplicar", action="store_true",
                     help="Aplica a migração (grava o de-para antes do commit).")
    grp.add_argument("--reverter", metavar="BACKUP_JSON",
                     help="Desfaz a partir do de-para gravado pelo --aplicar.")
    ap.add_argument("--usuario", metavar="UUID",
                    help="Migra só esta conta (padrão: todas com nome antigo).")
    args = ap.parse_args(argv)

    if settings.MOCK_MODE:
        print("MOCK_MODE ativo — abortado (não há banco real para alterar).")
        return 1
    engine = get_engine()
    if engine is None:
        print("Banco não configurado — abortado.")
        return 1

    if args.reverter:
        return cmd_reverter(engine, args.reverter)
    if args.aplicar:
        return cmd_aplicar(engine, args.usuario)

    with engine.connect() as conn:
        legadas, de_sistema = categorias_legadas(conn, args.usuario)
        plano = planejar(conn, legadas, resolvedor_somente_leitura(conn))
        prever_mantidas(conn, plano)
    relatar(plano, de_sistema)
    if legadas:
        print("Nada foi alterado (dry-run). Use --aplicar para aplicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
