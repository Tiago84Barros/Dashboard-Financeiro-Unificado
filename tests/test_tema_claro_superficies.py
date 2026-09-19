"""Superfícies que o tema claro precisa alcançar — e as que não pode apagar."""
from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _classes_com_logo_inline() -> set[str]:
    """Classes que recebem a imagem do logo como ``background-image`` inline.

    Sai da estrutura, não de uma lista fixa: a assinatura de
    ``company_logo_html`` dá o padrão e cada chamada dá as variações.
    """
    fonte = (RAIZ / "design" / "market_companies.py").read_text(encoding="utf-8")
    classes = set(re.findall(r'css_class: str = "([\w-]+)"', fonte))
    for arquivo in list((RAIZ / "views").glob("*.py")) + list((RAIZ / "design").glob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        if "company_logo_html" in texto:
            classes |= set(re.findall(r'css_class="([\w-]+)"', texto))
    assert classes, "nenhuma classe de logo encontrada — o padrão mudou"
    return classes


def test_tema_claro_nao_apaga_o_logo_com_o_atalho_background():
    """``background:...!important`` zera o ``background-image`` do estilo inline.

    Inline perde para ``!important``: a regra do tema claro pintava a placa e
    levava a imagem junto, e o card ficava com o quadrado vazio.
    """
    from design.theme_light import LIGHT_CSS

    classes = _classes_com_logo_inline()
    for regra in LIGHT_CSS.split("}"):
        if "{" not in regra:
            continue
        seletor, corpo = regra.split("{", 1)
        alvos = {c for c in classes if re.search(rf"\.{re.escape(c)}\b", seletor)}
        if not alvos:
            continue
        assert not re.search(r"(^|[;\s])background\s*:[^;]*!important", corpo), (
            f"{sorted(alvos)}: use background-color; o atalho apaga a imagem do logo"
        )


# Cromo do tema escuro que não pode sobrar em HTML da tela de controle: sobre a
# página clara vira o bloco escuro que o usuário enxerga.
_CROMO_ESCURO = ("#12151E", "#0E1117", "#1E2533", "#1A1F2E", "#2A1A12",
                 "#718096", "#4A5568", "#CBD5E0", "#E2E8F0", "#9CA3AF")


def test_controle_financeiro_nao_pinta_html_com_cromo_escuro():
    fonte = (RAIZ / "views" / "controle_financeiro.py").read_text(encoding="utf-8")
    sujas = [
        f"{n}: {linha.strip()}"
        for n, linha in enumerate(fonte.splitlines(), 1)
        if ("style=" in linha or "<hr" in linha)
        and any(cor.lower() in linha.lower() for cor in _CROMO_ESCURO)
    ]
    assert not sujas, "literais do tema escuro em HTML:\n" + "\n".join(sujas)
