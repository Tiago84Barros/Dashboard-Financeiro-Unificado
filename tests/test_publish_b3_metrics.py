"""Backup automático das linhas apagadas pelo publicador da vitrine.

Contexto (01/08/2026): o backup dependia de quem rodava lembrar de fazê-lo à
mão. A tentativa manual de baixar a tabela anual inteira caiu no meio da rede e
deixou um CSV com 34.781 de 56.460 linhas — com nome de arquivo íntegro. Só não
virou perda porque as linhas apagadas tinham sido salvas à parte, por sorte de
método. Proteção que depende de disciplina não é proteção.
"""
import csv

import pytest

from scripts.publish_b3_metrics_to_supabase import _salvar_backup_orfas


class _ConnFake:
    """Devolve a linha completa de cada órfã, como o Postgres faria."""

    def __init__(self, linhas: dict[tuple, dict], falhar_em: int | None = None):
        self._linhas = linhas
        self._falhar_em = falhar_em
        self._chamadas = 0

    def execute(self, _sql, params):
        self._chamadas += 1
        if self._falhar_em is not None and self._chamadas > self._falhar_em:
            raise ConnectionError("SSL SYSCALL error: EOF detected")
        chave = (params["ticker"], params["metric_name"])
        achada = self._linhas.get(chave)

        class _Res:
            def mappings(_self):
                return [achada] if achada else []
        return _Res()


def _orfa(ticker: str, metrica: str) -> dict:
    return {"ticker": ticker, "period": "annual", "year": 2025,
            "quarter": 0, "metric_name": metrica}


def _completa(ticker: str, metrica: str) -> dict:
    return {**_orfa(ticker, metrica), "metric_value": 1.0,
            "calculation_method": "x", "source": "market.compute",
            "confidence_score": 85.0}


def test_grava_linhas_completas_nao_so_as_chaves(tmp_path):
    """Backup com só a chave não restaura nada — precisa do valor e do método."""
    orfas = [_orfa("BBAS3", "FCO_Negativo"), _orfa("ITSA4", "FCO_Negativo")]
    conn = _ConnFake({("BBAS3", "FCO_Negativo"): _completa("BBAS3", "FCO_Negativo"),
                      ("ITSA4", "FCO_Negativo"): _completa("ITSA4", "FCO_Negativo")})
    destino = tmp_path / "sub" / "orfas.csv"

    _salvar_backup_orfas(conn, orfas, destino)

    with destino.open(encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    assert len(linhas) == 2
    assert {linha["ticker"] for linha in linhas} == {"BBAS3", "ITSA4"}
    assert linhas[0]["metric_value"] == "1.0"          # valor, não só a chave
    assert linhas[0]["calculation_method"] == "x"


def test_leitura_incompleta_aborta_sem_apagar(tmp_path):
    """A conexão caiu no meio: é exatamente o caso que gerou o CSV truncado."""
    orfas = [_orfa("A3", "FCO_Negativo"), _orfa("B3", "FCO_Negativo"),
             _orfa("C3", "FCO_Negativo")]
    conn = _ConnFake({("A3", "FCO_Negativo"): _completa("A3", "FCO_Negativo")},
                     falhar_em=1)

    with pytest.raises(ConnectionError):
        _salvar_backup_orfas(conn, orfas, tmp_path / "orfas.csv")


def test_linha_que_sumiu_entre_a_leitura_e_o_backup_aborta(tmp_path):
    """Menos linhas lidas que órfãs: aborta em vez de salvar backup parcial."""
    orfas = [_orfa("A3", "FCO_Negativo"), _orfa("SUMIU3", "FCO_Negativo")]
    conn = _ConnFake({("A3", "FCO_Negativo"): _completa("A3", "FCO_Negativo")})

    with pytest.raises(RuntimeError, match="backup incompleto"):
        _salvar_backup_orfas(conn, orfas, tmp_path / "orfas.csv")


def test_confere_o_que_chegou_no_disco(tmp_path, monkeypatch):
    """Contar as linhas de volta é o que teria pego o CSV de 34.781/56.460."""
    orfas = [_orfa("A3", "FCO_Negativo")]
    conn = _ConnFake({("A3", "FCO_Negativo"): _completa("A3", "FCO_Negativo")})
    destino = tmp_path / "orfas.csv"

    real = csv.DictWriter.writerows

    def _escrita_truncada(self, linhas):        # simula disco cheio / IO parcial
        return real(self, [])

    monkeypatch.setattr(csv.DictWriter, "writerows", _escrita_truncada)
    with pytest.raises(RuntimeError, match="gravou 0 de 1"):
        _salvar_backup_orfas(conn, orfas, destino)


def test_cria_o_diretorio_de_destino(tmp_path):
    orfas = [_orfa("A3", "FCO_Negativo")]
    conn = _ConnFake({("A3", "FCO_Negativo"): _completa("A3", "FCO_Negativo")})
    destino = tmp_path / "nao" / "existe" / "ainda" / "orfas.csv"

    assert _salvar_backup_orfas(conn, orfas, destino).exists()
