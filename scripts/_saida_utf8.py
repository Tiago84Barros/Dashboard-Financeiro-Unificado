# -*- coding: utf-8 -*-
"""Saída padrão em UTF-8 para os scripts rodados à mão no Windows.

No Windows, quando a saída de um script vai para um pipe (``| Tee-Object``) ou
para um arquivo, o Python escreve em cp1252, a página de código da máquina. O
cp1252 não tem o sinal de menos tipográfico ``−`` (U+2212) que
``core.fii_validation._pp`` põe nos bloqueios, e o ``print`` final do
walk-forward FII de 19/11/2026 quebrou com ``UnicodeEncodeError`` depois de a
validação já estar gravada no armazém.

O agendador (``scripts/atualizar_vitrines.py``) não sofre disso porque passa
``PYTHONIOENCODING=utf-8`` aos filhos. Definir essa variável dentro do próprio
script não adianta: o Python a lê ao iniciar, antes de qualquer linha do
script. Por isso a correção é reconfigurar os fluxos já abertos.
"""
from __future__ import annotations

import sys


def saida_utf8() -> None:
    """Passa ``stdout`` e ``stderr`` para UTF-8, sem falhar onde não dá.

    Um fluxo trocado por um objeto sem ``reconfigure`` (o ``capsys`` do pytest,
    um ``StringIO``) fica como está: ele já aceita qualquer caractere.
    """
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is None:
            continue
        try:
            reconfigurar(encoding="utf-8")
        except (ValueError, OSError):
            pass
