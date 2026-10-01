"""Universo do coletor IPE: o mapa do cadastro CVM entra onde cvm_to_ticker falta.

Lacuna 7b45c481: o chat da SOND5 pedia as notas explicativas de 2025 porque o
coletor nunca baixou documento da Sondotécnica — ela, e outras 86 empresas do
universo, não estão em public.cvm_to_ticker, mas estão em market.ticker_cvm.
"""
from data_pipeline.jobs.update_cvm_ipe import _codigo_to_ticker


class _Resultado:
    def __init__(self, linhas):
        self.linhas = linhas

    def fetchall(self):
        return self.linhas

    def scalar(self):
        return self.linhas[0][0] if self.linhas else None


class _Conexao:
    def __init__(self, *, ticker_cvm_existe=True):
        self.ticker_cvm_existe = ticker_cvm_existe
        self.consultas = []

    def execute(self, sql, params=None):
        texto = str(sql)
        self.consultas.append(texto)
        if "to_regclass('market.ticker_cvm')" in texto:
            return _Resultado([(self.ticker_cvm_existe,)])
        if "public.cvm_to_ticker" in texto:
            return _Resultado([(9512, "PETR4")])
        if "market.ticker_cvm" in texto:
            # mesmo código, outro ticker: o registro oficial não é sobrescrito
            return _Resultado([(10880, "SOND3"), (9512, "PETR3")])
        if "docs_corporativos" in texto:
            return _Resultado([(19348, "ITUB4")])
        raise AssertionError(texto)


def test_empresa_fora_do_cvm_to_ticker_entra_pelo_cadastro_cvm():
    mapa = _codigo_to_ticker(_Conexao())
    assert mapa[10880] == "SOND3"
    assert mapa[9512] == "PETR4"
    assert mapa[19348] == "ITUB4"  # o complemento de docs_corporativos segue vivo


def test_sem_ticker_cvm_nao_consulta_a_tabela_e_mantem_o_resto():
    conn = _Conexao(ticker_cvm_existe=False)
    mapa = _codigo_to_ticker(conn)
    assert 10880 not in mapa
    assert mapa == {9512: "PETR4", 19348: "ITUB4"}
    assert not any("FROM market.ticker_cvm" in q for q in conn.consultas)
