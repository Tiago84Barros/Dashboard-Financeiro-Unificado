import csv
import io
import zipfile
from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

from data_pipeline.market.cvm_fundamentos import carregar_fundamentos_cvm

CODIGO = 987654
DFP = ("DFP", 2025)
ITR = ("ITR", 2026)
HEADER_FIELDS = "CD_CVM DT_REFER VERSAO DT_RECEB ID_DOC".split()
COMMON_FIELDS = (
    "CD_CVM DT_REFER VERSAO MOEDA ESCALA_MOEDA ORDEM_EXERC "
    "DT_FIM_EXERC CD_CONTA DS_CONTA VL_CONTA"
).split()
DESCRICOES = {
    "3.01": "Receita de Venda de Bens e/ou Serviços",
    "3.05": "Resultado Antes do Resultado Financeiro e dos Tributos",
    "3.11": "Lucro/Prejuízo Consolidado do Período",
    "3.11.01": "Atribuído a Sócios da Empresa Controladora",
    "3.11.02": "Atribuído a Sócios Não Controladores",
    "1": "Ativo Total",
    "1.01": "Ativo Circulante",
    "1.01.01": "Caixa e Equivalentes de Caixa",
    "1.01.02": "Aplicações Financeiras",
    "2.01": "Passivo Circulante",
    "2.01.04": "Empréstimos e Financiamentos",
    "2.02.01": "Empréstimos e Financiamentos",
    "2.03": "Patrimônio Líquido Consolidado",
    "2.03.09": "Participação dos Acionistas Não Controladores",
}


def _cabecalho(ref, recebido, versao=1):
    return dict(
        CD_CVM=CODIGO, DT_REFER=ref, VERSAO=versao,
        DT_RECEB=recebido, ID_DOC=1000 + versao,
    )


def _contas(ref, valores, *, inicio=None, fim=None, ordem="ÚLTIMO",
            versao=1, escala="MIL", moeda="REAL"):
    return [
        dict(
            CD_CVM=CODIGO, DT_REFER=ref, VERSAO=versao, MOEDA=moeda,
            ESCALA_MOEDA=escala, ORDEM_EXERC=ordem, DT_FIM_EXERC=fim or ref,
            CD_CONTA=conta, DS_CONTA=DESCRICOES[conta], VL_CONTA=valor,
            **({"DT_INI_EXERC": inicio} if inicio else {}),
        )
        for conta, valor in valores.items()
    ]


def _zip(cache_dir, pacote, cabecalho, **demonstracoes):
    categoria, ano = pacote
    prefixo = f"{categoria.lower()}_cia_aberta"
    destino = cache_dir / categoria.lower() / f"{prefixo}_{ano}.zip"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        arquivos = {destino.with_suffix(".csv").name: (HEADER_FIELDS, cabecalho)}
        for tipo, linhas in demonstracoes.items():
            campos = COMMON_FIELDS + (["DT_INI_EXERC"] if tipo.startswith("DRE") else [])
            arquivos[f"{prefixo}_{tipo}_{ano}.csv"] = (campos, linhas)
        for nome, (campos, linhas) in arquivos.items():
            dados = io.StringIO()
            writer = csv.DictWriter(dados, campos, delimiter=";")
            writer.writeheader()
            writer.writerows(linhas)
            z.writestr(nome, dados.getvalue().encode("latin-1"))
    return destino


def _ler(cache_dir, as_of=date(2026, 11, 25)):
    return carregar_fundamentos_cvm([CODIGO], as_of=as_of, cache_dir=cache_dir, somente_cache=True)


def _dfp(cache_dir, *, escopo="con"):
    ref = "2025-12-31"
    dre = _contas(ref, {"3.01": 120, "3.05": 38, "3.11": 18, "3.11.01": 16},
                  inicio="2025-01-01")
    _zip(cache_dir, DFP, [_cabecalho(ref, "2026-03-25")], **{f"DRE_{escopo}": dre})


@pytest.mark.parametrize(
    ("fim_atual", "fim_anterior", "inicio_trimestre"),
    [("2026-03-31", "2025-03-31", None),
     ("2026-06-30", "2025-06-30", "2026-04-01"),
     ("2026-09-30", "2025-09-30", "2026-07-01")],
)
def test_ttm_usa_ytd_da_itr_e_dfp_do_mesmo_escopo(tmp_path, fim_atual, fim_anterior, inicio_trimestre):
    _dfp(tmp_path)
    dre = _contas(fim_atual, {"3.01": 102, "3.05": 30, "3.11": 23,
                             "3.11.01": 0, "3.11.02": 4}, inicio="2026-01-01")
    dre += _contas(fim_atual, {"3.01": 90, "3.05": 34, "3.11": 14,
                              "3.11.01": 11, "3.11.02": 3},
                   inicio="2025-01-01", fim=fim_anterior, ordem="PENÚLTIMO")
    if inicio_trimestre:
        dre = _contas(fim_atual, {"3.01": 500}, inicio=inicio_trimestre) + dre
    bpa = _contas(
        fim_atual, {"1": 1_200_000, "1.01": 540_000, "1.01.01": 225_000, "1.01.02": 95_000},
        escala="UNIDADE",
    )
    bpp = _contas(fim_atual, {"2.01": 380, "2.01.04": 350, "2.02.01": 100, "2.03": 612, "2.03.09": 40})
    _zip(tmp_path, ITR, [_cabecalho(fim_atual, "2026-11-20")], DRE_con=dre, BPA_con=bpa, BPP_con=bpp)

    fundamentos = _ler(tmp_path)[CODIGO]

    assert fundamentos.period_end == date.fromisoformat(fim_atual)
    assert fundamentos.filing_date == date(2026, 11, 20)
    assert fundamentos.scope == "con"
    assert fundamentos.revenue_ttm == 132_000
    assert fundamentos.ebit_ttm == 34_000
    assert fundamentos.net_income_ttm == 24_000
    assert fundamentos.equity == 572_000
    assert fundamentos.cash == 225_000
    assert fundamentos.gross_debt == 450_000
    assert fundamentos.net_debt == 225_000
    assert fundamentos.total_assets == 1_200_000
    assert fundamentos.current_assets == 540_000
    assert fundamentos.current_liabilities == 380_000
    assert [(doc.category, doc.period_end, doc.version) for doc in fundamentos.source_documents] == [
        ("ITR", date.fromisoformat(fim_atual), 1), ("DFP", date(2025, 12, 31), 1),
    ]


def test_versao_e_publicacao_respeitam_a_data_de_corte(tmp_path):
    ref = "2025-12-31"
    dre = _contas(ref, {"3.01": 80, "3.05": 1}, inicio="2025-01-01")
    dre += _contas(ref, {"3.01": 90}, inicio="2025-01-01", versao=2)
    bpp = _contas(ref, {"2.03": 20}, versao=1)
    _zip(tmp_path, DFP, [_cabecalho(ref, "2026-03-25"), _cabecalho(ref, "2026-05-30", 2)],
         DRE_ind=dre, BPP_ind=bpp)

    anterior = _ler(tmp_path, date(2026, 5, 29))[CODIGO]
    atual = _ler(tmp_path, date(2026, 5, 30))[CODIGO]

    assert anterior.revenue_ttm == 80_000
    assert anterior.ebit_ttm == 1_000
    assert anterior.equity == 20_000
    assert atual.revenue_ttm == 90_000
    assert atual.ebit_ttm is None
    assert atual.equity is None
    assert atual.source_documents[0].version == 2
    assert atual.source_documents[0].document_id == "1002"
    assert _ler(tmp_path, date(2026, 3, 24)) == {}


def test_itr_sem_comparaveis_do_mesmo_escopo_mantem_apenas_o_balanco(tmp_path):
    _dfp(tmp_path, escopo="ind")
    ref = "2026-09-30"
    dre = _contas(ref, {"3.01": 90, "3.05": 33, "3.11.01": 15}, inicio="2026-01-01")
    dre += _contas(ref, {"3.01": 70, "3.05": 21, "3.11.01": 10},
                   inicio="2025-01-01", fim="2025-09-30", ordem="PENÚLTIMO")
    bpa = _contas(ref, {"1.01.01": 0})
    bpp = _contas(ref, {"2.01.04": 550, "2.02.01": 0})
    _zip(tmp_path, ITR, [_cabecalho(ref, "2026-11-20")],
         DRE_con=dre, DRE_ind=dre, BPA_con=bpa, BPP_con=bpp)

    fundamentos = _ler(tmp_path)[CODIGO]

    assert fundamentos.scope == "con"
    assert fundamentos.period_end == date(2026, 9, 30)
    assert fundamentos.revenue_ttm is None
    assert fundamentos.ebit_ttm is None
    assert fundamentos.net_income_ttm is None
    assert fundamentos.gross_debt == 550_000
    assert fundamentos.net_debt == 550_000
    assert fundamentos.cash == 0


def test_ausencia_de_contas_e_conflito_de_valores_nao_viram_zero(tmp_path):
    ref = "2025-12-31"
    dre = _contas(ref, {"3.01": 90, "3.05": 30, "3.11": 4, "3.11.01": 0, "3.11.02": 4},
                  inicio="2025-01-01")
    dre += _contas(ref, {"3.01": 95}, inicio="2025-01-01") + dre
    bpa = _contas(ref, {"1.01.01": 3}, escala="DESCONHECIDA")
    bpp = _contas(ref, {"2.01.04": 50, "2.03": 55})
    _zip(tmp_path, DFP, [_cabecalho(ref, "2026-03-25")], DRE_con=dre, BPA_con=bpa, BPP_con=bpp)

    fundamentos = _ler(tmp_path)[CODIGO]

    assert fundamentos.revenue_ttm is None
    assert fundamentos.ebit_ttm == 30_000
    assert fundamentos.net_income_ttm == 0
    assert fundamentos.cash is None
    assert fundamentos.gross_debt is None
    assert fundamentos.net_debt is None


def test_rede_indisponivel_preserva_o_ultimo_zip_valido(tmp_path, monkeypatch):
    _dfp(tmp_path)
    caminho = tmp_path / "dfp" / "dfp_cia_aberta_2025.zip"
    original = caminho.read_bytes()

    def falhar(*args, **kwargs):
        raise requests.Timeout

    monkeypatch.setattr(requests, "get", falhar)
    monkeypatch.setattr("data_pipeline.market.cvm_fundamentos._CACHE_TTL_SECONDS", 0)

    fundamentos = carregar_fundamentos_cvm([CODIGO], as_of=date(2026, 11, 25), cache_dir=tmp_path)

    assert fundamentos[CODIGO].revenue_ttm == 120_000
    assert caminho.read_bytes() == original


@pytest.mark.parametrize("zip_valido", [True, False])
def test_cache_instala_apenas_download_validado(tmp_path, monkeypatch, zip_valido):
    cache_dir = tmp_path / "cache"
    _dfp(cache_dir)
    caminho_cache = cache_dir / "dfp" / "dfp_cia_aberta_2025.zip"
    original = caminho_cache.read_bytes()
    ref = "2025-12-31"
    dre = _contas(ref, {"3.01": 222}, inicio="2025-01-01")
    arquivo_novo = _zip(
        tmp_path / "servidor", DFP, [_cabecalho(ref, "2026-03-25")], DRE_ind=dre,
    )
    payload = arquivo_novo.read_bytes() if zip_valido else b"<html>Erro no servidor</html>"

    def responder(url, **kwargs):
        if not url.endswith("dfp_cia_aberta_2025.zip"):
            raise requests.Timeout
        resposta = MagicMock(status_code=200, headers={})
        resposta.__enter__.return_value = resposta
        resposta.iter_content.return_value = [payload]
        return resposta

    monkeypatch.setattr(requests, "get", responder)
    monkeypatch.setattr("data_pipeline.market.cvm_fundamentos._CACHE_TTL_SECONDS", 0)

    fundamentos = carregar_fundamentos_cvm([CODIGO], as_of=date(2026, 11, 25), cache_dir=cache_dir)

    assert fundamentos[CODIGO].revenue_ttm == (222_000 if zip_valido else 120_000)
    assert caminho_cache.read_bytes() == (payload if zip_valido else original)
    assert not list(caminho_cache.parent.glob("*.part"))


def test_cache_revalida_etag_sem_baixar_novamente(tmp_path, monkeypatch):
    servidor = tmp_path / "servidor"
    _dfp(servidor)
    payload = (servidor / "dfp" / "dfp_cia_aberta_2025.zip").read_bytes()
    cache = tmp_path / "cache"
    etag = '"pacote-atual"'
    leituras = []

    def responder(url, *, headers, **kwargs):
        if not url.endswith("dfp_cia_aberta_2025.zip"):
            raise requests.Timeout
        resposta = MagicMock(status_code=200, headers={"ETag": etag})
        resposta.__enter__.return_value = resposta
        if headers.get("If-None-Match") == etag:
            resposta.status_code = 304
        else:
            def ler_conteudo(**kwargs):
                leituras.append(etag)
                yield payload
            resposta.iter_content.side_effect = ler_conteudo
        return resposta

    monkeypatch.setattr(requests, "get", responder)
    monkeypatch.setattr("data_pipeline.market.cvm_fundamentos._CACHE_TTL_SECONDS", 0)
    for _ in range(2):
        fundamentos = carregar_fundamentos_cvm([CODIGO], as_of=date(2026, 11, 25), cache_dir=cache)
        assert fundamentos[CODIGO].revenue_ttm == 120_000

    assert leituras == [etag]
    (cache / "dfp" / "dfp_cia_aberta_2025.zip").write_bytes(payload)
    carregar_fundamentos_cvm([CODIGO], as_of=date(2026, 11, 25), cache_dir=cache)
    assert leituras == [etag, etag]


def test_itr_com_comparavel_de_janela_diferente_nao_gera_fluxo_ttm(tmp_path):
    _dfp(tmp_path)
    ref = "2026-09-30"
    dre = _contas(ref, {"3.01": 90}, inicio="2026-01-01")
    dre += _contas(ref, {"3.01": 70}, inicio="2025-07-01", fim="2025-09-30", ordem="PENÚLTIMO")
    _zip(tmp_path, ITR, [_cabecalho(ref, "2026-11-20")], DRE_con=dre)

    fundamentos = _ler(tmp_path)[CODIGO]

    assert fundamentos.period_end == date(2026, 9, 30)
    assert fundamentos.revenue_ttm is None


def test_plano_bancario_nao_interpreta_caixa_e_passivo_financeiro_como_circulantes(tmp_path):
    ref = "2025-12-31"
    dre = _contas(ref, {"3.01": 90}, inicio="2025-01-01")
    bpa = _contas(ref, {"1": 250, "1.01": 30})
    bpp = _contas(ref, {"2.01": 40})
    bpa[1]["DS_CONTA"] = "Caixa e Equivalentes de Caixa"
    bpp[0]["DS_CONTA"] = "Passivos Financeiros ao Valor Justo"
    _zip(tmp_path, DFP, [_cabecalho(ref, "2026-03-25")], DRE_con=dre, BPA_con=bpa, BPP_con=bpp)

    fundamentos = _ler(tmp_path)[CODIGO]

    assert fundamentos.total_assets == 250_000
    assert fundamentos.cash == 30_000
    assert fundamentos.current_assets is None
    assert fundamentos.current_liabilities is None
