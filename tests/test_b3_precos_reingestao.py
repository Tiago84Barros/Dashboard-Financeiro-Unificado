"""Rebaixar o COTAHIST do ano corrente nao pode duplicar o ano inteiro.

O arquivo anual da B3 cresce a cada pregao: em 02/09 ele tem um dia a mais que
em 01/09, e portanto outro sha256. Enquanto a chave unica da tabela incluia
`archive_sha256`, cada rebaixa era aceita como se fosse serie nova -- as 43 mil
linhas de 2026 ganhariam uma copia por dia, cada pregao com N fechamentos
identicos, e nenhuma excecao seria levantada. Media, retorno e volatilidade
lidos daqui sairiam errados em silencio.

Estes testes rodam contra um banco descartavel criado no proprio armazem local
(porta 5433) porque a duplicata so aparece quando o `ON CONFLICT` executa de
verdade; um dublê nao a reproduz. Sem armazem de pe, sao pulados.
"""
import io
import uuid
import zipfile

import pytest
from sqlalchemy import create_engine, text

from data_pipeline.market import b3_precos


def _linha(*, ticker="PETR4", data="20260130", fechamento="0000000010500"):
    linha = list(" " * 245)
    for inicio, fim, valor in (
        (0, 2, "01"), (2, 10, data), (10, 12, "02"), (12, 24, ticker),
        (24, 27, "010"), (27, 39, "PETROBRAS"), (39, 49, "ON"),
        (56, 69, "0000000010000"), (69, 82, "0000000011000"),
        (82, 95, "0000000009000"), (95, 108, "0000000010000"),
        (108, 121, fechamento), (147, 152, "00010"),
        (152, 170, "000000000000001000"), (170, 188, "000000000010500000"),
        (210, 217, "0000001"), (230, 242, "BRPETRACNPR6"),
    ):
        linha[inicio:fim] = list(str(valor).ljust(fim - inicio)[:fim - inicio])
    return "".join(linha)


def _zip(*linhas):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as compactado:
        compactado.writestr("COTAHIST_A2026.TXT", "\n".join(linhas))
    return buffer.getvalue()


@pytest.fixture
def banco_descartavel(monkeypatch):
    """Banco vazio no armazem local, derrubado no fim. Pula se nao houver."""
    try:
        from scripts.construir_memoria_mercado import warehouse_url
        url = warehouse_url()
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")

    nome = f"teste_b3_{uuid.uuid4().hex[:10]}"
    administrador = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with administrador.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception as erro:  # noqa: BLE001
        administrador.dispose()
        pytest.skip(f"armazem local nao respondeu ({erro})")

    engine = create_engine(url.rsplit("/", 1)[0] + f"/{nome}")
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS market"))
    # A proveniencia mora em `market.brapi_raw_payloads`, que nao faz parte
    # deste schema; o alvo aqui e a chave unica.
    monkeypatch.setattr(b3_precos, "save_raw_payload", lambda *a, **k: None)
    try:
        yield engine
    finally:
        engine.dispose()
        with administrador.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        administrador.dispose()


def _contagem(engine):
    with engine.connect() as conn:
        return dict(conn.execute(text("""
            SELECT count(*) linhas, count(DISTINCT (ticker, trade_date)) pares,
                   max(close_unitario) fechamento
              FROM market.b3_security_history
        """)).mappings().one())


def test_ano_rebaixado_com_pregao_novo_nao_duplica_o_que_ja_estava(banco_descartavel):
    ontem = _zip(_linha(data="20260130"), _linha(data="20260202"))
    hoje = _zip(_linha(data="20260130"), _linha(data="20260202"),
                _linha(data="20260203"))
    assert ontem != hoje

    b3_precos.ingerir_ano(banco_descartavel, 2026, conteudo=ontem)
    assert _contagem(banco_descartavel)["linhas"] == 2

    b3_precos.ingerir_ano(banco_descartavel, 2026, conteudo=hoje)
    depois = _contagem(banco_descartavel)
    assert depois["linhas"] == 3, "o ano foi duplicado em vez de estendido"
    assert depois["pares"] == 3


def test_arquivo_identico_e_ignorado_pelo_portao_de_reentrancia(banco_descartavel):
    conteudo = _zip(_linha(data="20260130"))
    b3_precos.ingerir_ano(banco_descartavel, 2026, conteudo=conteudo)
    segundo = b3_precos.ingerir_ano(banco_descartavel, 2026, conteudo=conteudo)
    assert segundo["pulado"] is True
    assert _contagem(banco_descartavel)["linhas"] == 1


def test_correcao_do_pregao_sobrescreve_o_valor_antigo(banco_descartavel):
    """A B3 republica arquivo corrigido; o preco novo tem de vencer o velho."""
    b3_precos.ingerir_ano(banco_descartavel, 2026,
                          conteudo=_zip(_linha(fechamento="0000000010500")))
    assert float(_contagem(banco_descartavel)["fechamento"]) == 105.0

    b3_precos.ingerir_ano(banco_descartavel, 2026,
                          conteudo=_zip(_linha(fechamento="0000000020000")))
    depois = _contagem(banco_descartavel)
    assert depois["linhas"] == 1
    assert float(depois["fechamento"]) == 200.0
