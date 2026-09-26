"""Notícias por ativo pelo túnel do armazém.

Até aqui a produção só via a vitrine por ativo, publicada quando alguém rodava o
publicador (em 25/09/2026 ela era de 06/09). O túnel entrega as mesmas linhas
que ``ponte.linhas_do_acervo`` lê direto, e o app agrega com a mesma fórmula.

O que se prende:

* a nota pelo túnel é **idêntica** à da leitura direta sobre as mesmas linhas;
* não configurado cai na vitrine como antes, com o aviso de sempre;
* túnel configurado e fora do ar é nomeado **antes** da vitrine;
* quem passa engines explícitos nunca sai para a rede.

O servidor sobe de verdade em 127.0.0.1; o banco é trocado por um dublê.
"""
from __future__ import annotations

import pathlib
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_remoto as ar  # noqa: E402
from core.conjuntura import ponte as P  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

TOKEN = "t" * 40
CORTE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _linha(simbolo, n, *, direcao="alta", nota=Decimal("80.00"),
           confianca=Decimal("0.9000")):
    return {"simbolo": simbolo, "id_dedup": f"{simbolo}-{n}",
            "titulo": f"{simbolo} notícia {n}", "veiculo": "Valor",
            "url": f"https://exemplo.test/{simbolo}/{n}",
            "publicado_em": CORTE - timedelta(days=n),
            "tipo_evento": "resultado", "sentimento_app4": None,
            "sentimento_api": Decimal("0.2000"), "nota": nota,
            "direcao": direcao, "confianca": confianca}


LINHAS = [_linha("PETR4", 1), _linha("PETR4", 2, direcao="baixa"),
          _linha("PETR4", 3, nota=None), _linha("VALE3", 1)]


@pytest.fixture
def servidor(monkeypatch):
    chamadas: list[dict] = []

    def _linhas(engine, *, simbolos, as_of, janela_dias):
        chamadas.append({"simbolos": list(simbolos), "as_of": as_of,
                         "janela_dias": janela_dias})
        return [dict(linha) for linha in LINHAS if linha["simbolo"] in simbolos]

    monkeypatch.setattr(P, "linhas_do_acervo", _linhas)
    monkeypatch.setattr(srv, "_url_noticias", lambda: "postgresql://dublê")
    monkeypatch.setattr(srv, "engine_leitura", lambda url: object())
    http = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.fabricar_handler(TOKEN))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{http.server_address[1]}"
    monkeypatch.setattr(ar, "_config", lambda: (url, TOKEN))
    yield chamadas
    http.shutdown()
    http.server_close()


def test_nota_pelo_tunel_e_identica_a_da_leitura_direta(servidor):
    remoto = P._ler_noticias_remoto(simbolos=["PETR4", "VALE3"], as_of=CORTE,
                                    janela_dias=30)
    direto = P._agregar(LINHAS, simbolos=["PETR4", "VALE3"], janela_dias=30)

    assert remoto.keys() == direto.keys()
    for simbolo in direto:
        assert remoto[simbolo].valor == direto[simbolo].valor
        assert remoto[simbolo].n_itens == direto[simbolo].n_itens
        assert remoto[simbolo].motivo == direto[simbolo].motivo
    assert remoto["PETR4"].medida and not remoto["VALE3"].medida
    # A data volta como datetime: a procedência citada pela LLM depende dela.
    assert remoto["PETR4"].itens[0].publicado_em == CORTE - timedelta(days=1)
    assert "Valor, 24/09/2026" in remoto["PETR4"].itens[0].procedencia


def test_rota_repassa_corte_e_janela(servidor):
    P._ler_noticias_remoto(simbolos=["petr4", "PETR4"], as_of=CORTE, janela_dias=7)
    assert servidor[-1] == {"simbolos": ["PETR4"], "as_of": CORTE, "janela_dias": 7}


def test_rota_tem_teto_de_janela_e_de_corte():
    capturado = {}

    def _linhas(engine, **kw):
        capturado.update(kw)
        return []

    orig = (P.linhas_do_acervo, srv._url_noticias, srv.engine_leitura)
    P.linhas_do_acervo = _linhas
    srv._url_noticias = lambda: "x"
    srv.engine_leitura = lambda url: object()
    try:
        futuro = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        status, corpo = srv.rota_noticias_ativos(
            {"tickers": ["PETR4"], "janela_dias": ["9999"], "as_of": [futuro]})
        assert status == 200
        assert capturado["janela_dias"] == srv.JANELA_MAX_ATIVOS
        assert capturado["as_of"] <= datetime.now(timezone.utc)

        muitos = ",".join(f"T{i}" for i in range(srv.TICKERS_MAX + 1))
        assert srv.rota_noticias_ativos({"tickers": [muitos]})[0] == 400
        assert srv.rota_noticias_ativos({"tickers": [""]})[0] == 400
    finally:
        P.linhas_do_acervo, srv._url_noticias, srv.engine_leitura = orig


def test_rota_sem_acervo_configurado_e_503(monkeypatch):
    monkeypatch.setattr(srv, "_url_noticias", lambda: "")
    assert srv.rota_noticias_ativos({"tickers": ["PETR4"]})[0] == 503


def test_tunel_nao_configurado_devolve_none_sem_rede(monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert P._ler_noticias_remoto(simbolos=["PETR4"], as_of=CORTE,
                                  janela_dias=30) is None


def test_tunel_fora_do_ar_vira_acervo_indisponivel(monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: ("http://127.0.0.1:1", TOKEN))
    with pytest.raises(P.AcervoIndisponivel, match="túnel"):
        P._ler_noticias_remoto(simbolos=["PETR4"], as_of=CORTE, janela_dias=30)


# ── carregar(): a ordem das fontes ──────────────────────────────────────────
class _VitrineFalsa:
    pass


def _carregar(monkeypatch, remoto, *, vitrine=True):
    vitrine_lida = []

    def _ler_vitrine(engine, *, simbolos):
        vitrine_lida.append(True)
        return P._agregar(LINHAS, simbolos=simbolos, janela_dias=30), None

    monkeypatch.setattr(P, "_ler_vitrine", _ler_vitrine)
    ctx = P.carregar(asset_class="acoes_br", ativos={"PETR4": "petróleo"},
                     as_of=CORTE, noticias_engine=None,
                     vitrine_engine=_VitrineFalsa() if vitrine else None,
                     noticias_remoto=remoto)
    return ctx, vitrine_lida


def test_tunel_ganha_da_vitrine(monkeypatch):
    def _remoto(**kw):
        return P._agregar(LINHAS, simbolos=kw["simbolos"],
                          janela_dias=kw["janela_dias"])

    ctx, vitrine_lida = _carregar(monkeypatch, _remoto)
    assert ctx.fonte_noticias == "acervo"
    assert not vitrine_lida, "com o túnel respondendo, a vitrine não é lida"
    assert ctx.leituras["PETR4"].medida
    assert any("pelo túnel" in lim for lim in ctx.limitacoes)
    assert "VITRINE" not in P.para_llm(ctx)


def test_tunel_nao_configurado_cai_na_vitrine(monkeypatch):
    ctx, vitrine_lida = _carregar(monkeypatch, lambda **kw: None)
    assert vitrine_lida and ctx.fonte_noticias == "vitrine"
    assert any("túnel do armazém não configurado" in lim for lim in ctx.limitacoes)


def test_tunel_fora_do_ar_e_nomeado_antes_da_vitrine(monkeypatch):
    def _cai(**kw):
        raise P.AcervoIndisponivel("acervo de notícias pelo túnel não respondeu: x")

    ctx, vitrine_lida = _carregar(monkeypatch, _cai)
    assert vitrine_lida and ctx.fonte_noticias == "vitrine"
    assert ctx.acervo_falhou is False, "a vitrine entregou; não é falha total"
    idx_tunel = next(i for i, lim in enumerate(ctx.limitacoes) if "túnel" in lim)
    idx_vitrine = next(i for i, lim in enumerate(ctx.limitacoes)
                       if "vitrine" in lim)
    assert idx_tunel < idx_vitrine


def test_tunel_fora_do_ar_sem_vitrine_e_falha_de_leitura(monkeypatch):
    def _cai(**kw):
        raise P.AcervoIndisponivel("acervo de notícias pelo túnel não respondeu: x")

    ctx, _ = _carregar(monkeypatch, _cai, vitrine=False)
    assert ctx.acervo_falhou is True
    assert "NÃO PÔDE SER LIDO" in P.para_llm(ctx)


def test_bloco_so_usa_tunel_sem_acervo_local(monkeypatch):
    """Com o armazém alcançável direto, ir pela rede seria volta à toa."""
    recebidos = []

    def _carregar_falso(**kw):
        recebidos.append(kw["noticias_remoto"])
        raise RuntimeError("parar aqui")

    monkeypatch.setattr(P, "carregar", _carregar_falso)

    class _Local:
        def dispose(self):
            pass

    monkeypatch.setattr(P, "_engines", lambda: (None, None, None))
    P.bloco_para_prompt(asset_class="b3", ativos={"PETR4": ""})
    monkeypatch.setattr(P, "_engines", lambda: (None, _Local(), None))
    P.bloco_para_prompt(asset_class="b3", ativos={"PETR4": ""})

    assert recebidos == [P._ler_noticias_remoto, None]
