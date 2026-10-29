"""
scripts/montar_mapa_app.py
Monta docs/mapa_app/mapa.json -- os dados do Mapa do App (Configurações →
🗺️ Mapa do App) -- a partir de um arquivo por área em docs/mapa_app/areas/.

Cada área é uma árvore de nós ``{nome, resumo, explicacao, so_admin, fonte,
filhos}``. ``fonte`` diz de que arquivo:linha o texto saiu e serve só para
conferir o mapa contra o código: fica nos arquivos de área e não vai para a
tela. A montagem segue a ordem do menu lateral (``app.py``), acrescenta a cada
área o grupo, a cor e se ela funciona por trás das telas, e avisa nó sem nome
ou sem explicação.

O desenho mora à parte, em docs/mapa_app/modelo.html, e quem junta os dois na
hora de mostrar é ``design/mapa_app.py``. Mudou o texto de uma tela: edite a
área e rode este script -- o modelo não muda. ``tests/test_configuracoes_mapa_do_app.py``
falha enquanto o mapa.json não refletir as áreas.

Uso:
    python scripts/montar_mapa_app.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PASTA = RAIZ / "docs" / "mapa_app"
AREAS_DIR = PASTA / "areas"
SAIDA = PASTA / "mapa.json"

# Ordem e grupos iguais ao menu lateral do app4 (app.py). A cor é o índice da
# paleta do modelo (classes .r1 ... .r9), herdado por todo o ramo da área.
AREAS = [
    ("dashboard_geral.json", "Visão geral", 1, False),
    ("controle_financeiro.json", "Finanças do dia a dia", 2, False),
    ("investimentos.json", "Investimentos", 3, False),
    ("empresas_b3.json", "Investimentos", 4, False),
    ("empresas_americanas.json", "Investimentos", 5, False),
    ("fiis.json", "Investimentos", 6, False),
    ("portfolio_global.json", "Investimentos", 7, False),
    ("configuracoes.json", "Sistema", 8, False),
    ("bastidores.json", "Por trás das telas", 9, True),
]
CAMPOS = ("nome", "resumo", "explicacao", "so_admin", "filhos")


def limpar(n: dict, caminho: str, problemas: list[str]) -> dict:
    """O nó como a tela o lê: sem ``fonte``, com ``so_admin`` sempre booleano."""
    nome = str(n.get("nome") or "").strip()
    if not nome:
        problemas.append(f"nó sem nome em {caminho}")
    saida = {k: n[k] for k in CAMPOS if k in n and k != "filhos"}
    saida["so_admin"] = bool(n.get("so_admin"))
    if not str(n.get("explicacao") or "").strip():
        problemas.append(f"sem explicação: {caminho} > {nome}")
    filhos = [limpar(f, f"{caminho} > {nome}", problemas) for f in n.get("filhos") or []]
    if filhos:
        saida["filhos"] = filhos
    return saida


def montar(pasta: Path = AREAS_DIR, *, data_mapa: str) -> tuple[dict, list[str]]:
    """Junta as áreas na raiz do mapa. Devolve (raiz, problemas)."""
    problemas: list[str] = []
    areas: list[dict] = []
    for arq, grupo, cor, bastidores in AREAS:
        caminho = pasta / arq
        if not caminho.exists():
            problemas.append(f"FALTA {arq}")
            continue
        n = limpar(json.loads(caminho.read_text(encoding="utf-8")), "", problemas)
        n.update(grupo=grupo, cor=cor, bastidores=bastidores)
        areas.append(n)
    raiz = {
        "nome": "Dashboard Financeiro Unificado",
        "explicacao": "Junta num lugar só suas contas do dia a dia, seus investimentos e a análise de "
                      "empresas do Brasil e dos EUA. Abaixo estão as 8 telas do menu lateral e, por último, "
                      "o que funciona por trás delas.",
        "data_mapa": data_mapa,
        "filhos": areas,
    }
    return raiz, problemas


def contar(n: dict) -> int:
    return 1 + sum(contar(f) for f in n.get("filhos", []))


def profundidade(n: dict) -> int:
    return 1 + max((profundidade(f) for f in n.get("filhos", [])), default=0)


def main() -> int:
    raiz, problemas = montar(data_mapa=date.today().strftime("%d/%m/%Y"))
    texto = json.dumps(raiz, ensure_ascii=False, indent=2) + "\n"
    SAIDA.write_text(texto, encoding="utf-8")

    for a in raiz["filhos"]:
        print(f"{a['nome']:<28} {contar(a):>4} partes  profundidade {profundidade(a)}")
    print("total", contar(raiz) - 1, "| tamanho", len(texto.encode("utf-8")) // 1024, "KB")
    for p in problemas:
        print("AVISO:", p)
    return 1 if any(p.startswith("FALTA") for p in problemas) else 0


if __name__ == "__main__":
    sys.exit(main())
