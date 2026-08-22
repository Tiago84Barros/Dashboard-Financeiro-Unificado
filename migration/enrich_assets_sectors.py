"""
migration/enrich_assets_sectors.py
====================================
Enriquece a tabela assets.sector usando dados da tabela setores (App 1).

Três camadas de enriquecimento:
  1. Match direto: assets.ticker == setores.ticker  (18 ativos)
  2. Herança "F": strip última letra do ticker para encontrar ação-mãe  (18 variantes)
  3. Regras manuais: REITs, renda fixa, ações conhecidas não listadas em setores

Campos atualizados em assets:
  sector — setor B3 ou categoria do ativo (ex: "Fundos Imobiliários", "Renda Fixa")

Segurança:
  - dry_run=True por padrão — só mostra o que seria atualizado
  - Usa UPDATE ... WHERE id = :id (individual, controlado)
  - Não altera registros onde sector já está preenchido (a menos que --overwrite)

Uso:
  python migration/enrich_assets_sectors.py             # dry run
  python migration/enrich_assets_sectors.py --apply     # atualiza de verdade
  python migration/enrich_assets_sectors.py --apply --overwrite  # sobrescreve existentes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Regras manuais: ticker (upper) → setor
# Para ativos não presentes na tabela setores
# ---------------------------------------------------------------------------

MANUAL_SECTOR_MAP: dict[str, tuple[str, str]] = {
    # (setor, segmento)
    # FIIs — Fundos Imobiliários
    "BCFF11":  ("Fundos Imobiliários", "FII"),
    "BITH11":  ("Fundos Imobiliários", "FII"),
    "BOVA11":  ("Fundos Imobiliários", "ETF"),
    "BRCO11":  ("Fundos Imobiliários", "FII"),
    "BRCR11":  ("Fundos Imobiliários", "FII"),
    "FAMB11B": ("Fundos Imobiliários", "FII"),
    "FIIP11B": ("Fundos Imobiliários", "FII"),
    "HGLG11":  ("Fundos Imobiliários", "FII"),
    "HGRE11":  ("Fundos Imobiliários", "FII"),
    "IRDM11":  ("Fundos Imobiliários", "FII"),
    "JRDM11":  ("Fundos Imobiliários", "FII"),
    "KNCR11":  ("Fundos Imobiliários", "FII"),
    "MFII11":  ("Fundos Imobiliários", "FII"),
    "MXRF11":  ("Fundos Imobiliários", "FII"),
    "SAPR11":  ("Fundos Imobiliários", "FII"),
    "VISC11":  ("Fundos Imobiliários", "FII"),
    # FII frações/séries
    "BCFF12":  ("Fundos Imobiliários", "FII"),
    "HGLG12":  ("Fundos Imobiliários", "FII"),
    "HGRE12":  ("Fundos Imobiliários", "FII"),
    "HGRE13":  ("Fundos Imobiliários", "FII"),
    "MFII12":  ("Fundos Imobiliários", "FII"),
    "MXRF12":  ("Fundos Imobiliários", "FII"),
    "MXRF15":  ("Fundos Imobiliários", "FII"),
    "BRCR12":  ("Fundos Imobiliários", "FII"),
    # Renda Fixa
    "CDB":     ("Renda Fixa", "CDB"),
    "DEB":     ("Renda Fixa", "Debênture"),
    "CFF":     ("Renda Fixa", "Fundo Infraestrutura"),
    # Tesouro Direto
    "TESOURO IPCA+ 2024": ("Tesouro Direto", "IPCA+"),
    # Saneamento — não listados em setores mas conhecidos
    "SAPR3":   ("Utilidade Pública", "Água e Saneamento"),
    "SAPR3F":  ("Utilidade Pública", "Água e Saneamento"),
    "SAPR11F": ("Utilidade Pública", "Água e Saneamento"),
    "SBSP3":   ("Utilidade Pública", "Água e Saneamento"),
    "SBSP3F":  ("Utilidade Pública", "Água e Saneamento"),
    # Energia elétrica — transmissão
    "TRPL3":   ("Utilidade Pública", "Energia Elétrica"),
    "TRPL3F":  ("Utilidade Pública", "Energia Elétrica"),
    # Seguros
    "PSSA3":   ("Financeiro", "Seguradoras"),
    "PSSA3F":  ("Financeiro", "Seguradoras"),
    # Marfrig — carnes (MBRF = MARFRIG, mesma empresa que MRFG)
    "MBRF3":   ("Consumo não Cíclico", "Carnes e Derivados"),
    "MBRF3F":  ("Consumo não Cíclico", "Carnes e Derivados"),
    # Equatorial variantes (EQTL1, EQTL9 = séries / subscrições)
    "EQTL1":   ("Utilidade Pública", "Energia Elétrica"),
    "EQTL9":   ("Utilidade Pública", "Energia Elétrica"),
    # Gerdau Mets variantes
    "GMAT1":   ("Consumo não Cíclico", "Alimentos"),
    "GMAT1F":  ("Consumo não Cíclico", "Alimentos"),
    # FRAS-LE (L = série especial)
    "FRAS3L":  ("Bens Industriais", "Material Rodoviário"),
    # ABC Brasil série 2
    "ABCB2":   ("Financeiro", "Bancos"),
    # XP Investimentos BDR
    "XPBR31":  ("Financeiro", "Corretoras"),
    # Xinguara
    "XPAR3":   ("Consumo Cíclico", "Madeira e Papel"),
}


def run(apply: bool, overwrite: bool) -> int:
    from sqlalchemy import text

    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine

    _ensure_utf8_stdout()
    cfg = MigrationConfig.from_env(dry_run=not apply)

    if not cfg.dest_url:
        print("ERRO: SUPABASE_UNIFICADO_URL nao configurado.")
        return 1

    sep = "=" * 60
    mode = "APLICANDO" if apply else "DRY RUN"
    print(sep)
    print(f"  Enriquecimento assets.sector — {mode}")
    if overwrite:
        print("  --overwrite: sobrescreve sector existente")
    print(sep)
    print()

    engine = make_engine(cfg.dest_url, source_label="enrich_assets", read_only_hint=False)

    updated_direct = 0
    updated_fvar = 0
    updated_manual = 0
    skipped = 0

    with engine.begin() as conn:
        # ── Carregar todos os assets ──────────────────────────────────────
        assets = conn.execute(text(
            "SELECT id, ticker, class, sector FROM assets ORDER BY ticker"
        )).fetchall()

        # ── Carregar mapa ticker → (setor, segmento) do setores ──────────
        setores_rows = conn.execute(text(
            'SELECT upper(ticker), "SETOR", "SEGMENTO" FROM setores'
        )).fetchall()
        setores_map: dict[str, tuple[str, str]] = {
            r[0]: (r[1], r[2]) for r in setores_rows
        }

        print(f"  assets carregados : {len(assets)}")
        print(f"  setores carregados: {len(setores_map)}")
        print()

        for row in assets:
            asset_id = str(row[0])
            ticker = str(row[1]).strip().upper()
            asset_class = str(row[2]).strip()
            current_sector = row[3]

            # Pular se já tem setor e não está em modo overwrite
            if current_sector and not overwrite:
                skipped += 1
                continue

            sector_val = None
            source = None

            # 1. Match direto no setores
            if ticker in setores_map:
                sector_val = setores_map[ticker][0]
                source = "setores_direto"

            # 2. Herança "F" — strip última letra e busca pai
            elif len(ticker) > 1 and ticker[-1] == "F" and ticker[:-1] in setores_map:
                sector_val = setores_map[ticker[:-1]][0]
                source = f"setores_heranca({ticker[:-1]})"

            # 3. Regra por classe reit (FIIs sem match direto)
            elif asset_class == "reit" and ticker not in MANUAL_SECTOR_MAP:
                sector_val = "Fundos Imobiliários"
                source = "regra_class_reit"

            # 4. Mapa manual
            elif ticker in MANUAL_SECTOR_MAP:
                sector_val = MANUAL_SECTOR_MAP[ticker][0]
                source = "manual"

            if not sector_val:
                print(f"  ?? {ticker:<20} [{asset_class}] sem setor mapeado")
                skipped += 1
                continue

            tag = "(seria)" if not apply else ""
            print(f"  ++ {ticker:<20} [{asset_class}] {tag} sector={sector_val!r:<45} via {source}")

            if not apply:
                if ticker in setores_map:
                    updated_direct += 1
                elif source and "heranca" in source:
                    updated_fvar += 1
                else:
                    updated_manual += 1
                continue

            conn.execute(text(
                "UPDATE assets SET sector = :sector WHERE id = :id"
            ), {"sector": sector_val, "id": asset_id})

            if ticker in setores_map:
                updated_direct += 1
            elif source and "heranca" in source:
                updated_fvar += 1
            else:
                updated_manual += 1

    print()
    print(sep)
    print("  RESUMO")
    print(sep)
    print(f"  match direto (setores) : {updated_direct}")
    print(f"  heranca F-variant      : {updated_fvar}")
    print(f"  regras manuais         : {updated_manual}")
    print(f"  sem mapeamento/pulados : {skipped}")
    print(f"  total atualizado       : {updated_direct + updated_fvar + updated_manual}")
    if not apply:
        print()
        print("  Para aplicar de verdade:")
        print("    python migration/enrich_assets_sectors.py --apply")
    print(sep)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Enriquece assets.sector com dados do App 1")
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--overwrite", action="store_true", default=False,
                        help="Sobrescreve sector mesmo se ja preenchido")
    args = parser.parse_args()
    return run(apply=args.apply, overwrite=args.overwrite)


if __name__ == "__main__":
    sys.exit(main())
