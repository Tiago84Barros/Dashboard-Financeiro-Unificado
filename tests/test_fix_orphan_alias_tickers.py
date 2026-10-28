"""Limpeza de órfãos não consolida troca de código B3.

A ingestão grava a troca ELET3 -> AXIA3 como pendente, ao lado do alias
legado AXIA3 -> ELET3. Se a limpeza lesse os dois como divergência da brapi,
moveria a fita do AXIA3 para o ELET3 e apagaria o ativo atual — fora da
migração, sem backup nem reversão.
"""
from __future__ import annotations

from core.b3_troca_codigo import MOTIVO_TROCA, MOTIVO_TROCA_PENDENTE
from scripts.fix_orphan_alias_tickers import _load_aliases

_ALIASES = [
    ("ELET3", "AXIA3", MOTIVO_TROCA_PENDENTE),
    ("AXIA3", "ELET3", "brapi devolve outro símbolo"),   # legado, direção oposta
    ("CCRO3", "MOTV3", MOTIVO_TROCA),
    ("ALOS3", "BRML3", None),                            # incorporação, toca ALSO3 -> ALOS3
    ("AZUL4", "AZUL3", "brapi devolve outro símbolo"),   # classe: segue para a limpeza
]


class _Conn:
    def __init__(self, linhas, tem_motivo=True):
        self.linhas, self.tem_motivo = linhas, tem_motivo

    def execute(self, sql, *_):
        conn = self

        class _Res:
            def scalar(self):
                return 1 if conn.tem_motivo else None

            def fetchall(self):
                if conn.tem_motivo:
                    return list(conn.linhas)
                return [(s, b, None) for s, b, _ in conn.linhas]
        return _Res()


def test_troca_de_codigo_fica_de_fora_nas_duas_direcoes():
    out, puladas = _load_aliases(_Conn(_ALIASES), None)
    assert out == {"AZUL4": "AZUL3"}
    assert set(puladas) == {("ELET3", "AXIA3"), ("AXIA3", "ELET3"),
                            ("CCRO3", "MOTV3"), ("ALOS3", "BRML3")}


def test_sem_a_coluna_motivo_os_pares_conhecidos_ainda_barram():
    out, puladas = _load_aliases(_Conn(_ALIASES, tem_motivo=False), None)
    assert out == {"AZUL4": "AZUL3"}
    assert ("AXIA3", "ELET3") in puladas and ("CCRO3", "MOTV3") in puladas


def test_filtro_de_simbolos_segue_valendo():
    out, puladas = _load_aliases(_Conn(_ALIASES), ["azul4.sa"])
    assert out == {"AZUL4": "AZUL3"} and puladas == []
    out, puladas = _load_aliases(_Conn(_ALIASES), ["AXIA3"])
    assert out == {} and puladas == [("AXIA3", "ELET3")]
