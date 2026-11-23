# -*- coding: utf-8 -*-
"""O teste de vazamento da IA não envia nem exporta dossiês reais sem a flag."""
from __future__ import annotations

import sys

import pytest

from scripts import testar_vazamento_ia_b3 as vz


@pytest.mark.parametrize("extra", [[], ["--exportar", "x"], ["--com-texto"]])
def test_sem_flag_para_antes_de_montar_dossie(monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", ["testar_vazamento_ia_b3.py", "--n", "1", *extra])
    with pytest.raises(SystemExit) as exc:
        vz.main()
    assert "--dados-reais-publicos" in str(exc.value)


def test_corrigir_nao_exige_flag(monkeypatch, tmp_path):
    chamado = {}
    monkeypatch.setattr(vz, "corrigir", lambda *a: chamado.setdefault("ok", 0))
    monkeypatch.setattr(sys, "argv", ["testar_vazamento_ia_b3.py",
                                      "--corrigir", str(tmp_path / "r.json")])
    assert vz.main() == 0
    assert "ok" in chamado
