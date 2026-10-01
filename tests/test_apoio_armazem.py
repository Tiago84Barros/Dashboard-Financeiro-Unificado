"""Schema descartável: nome único e nenhuma queda silenciosa em ``public``."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from tests.apoio_armazem import opcoes_conexao, schema_descartavel


def test_nome_e_unico_por_chamada_e_carrega_o_prefixo():
    a, b = schema_descartavel("app4_x_teste"), schema_descartavel("app4_x_teste")
    assert a != b and a.startswith("app4_x_teste_")
    assert "public" not in opcoes_conexao(a)["options"]


def test_schema_apagado_falha_em_vez_de_cair_no_public_do_armazem():
    """``noticias_itens`` existe em ``public`` do armazém; sem o schema do
    teste, a consulta sem schema tem de falhar, não achar a tabela real."""
    schema = schema_descartavel("app4_guarda_teste")
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        motor = create_engine(_warehouse_url(), connect_args=opcoes_conexao(schema))
        with motor.connect() as conn:
            existe = conn.execute(text(
                "SELECT to_regclass('public.noticias_itens') IS NOT NULL")).scalar()
    except Exception as exc:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"armazém local indisponível: {exc}")
    try:
        if not existe:
            pytest.skip("armazém sem public.noticias_itens")
        with motor.connect() as conn, pytest.raises(ProgrammingError,
                                                    match="does not exist"):
            conn.execute(text("SELECT count(*) FROM noticias_itens"))
    finally:
        motor.dispose()
