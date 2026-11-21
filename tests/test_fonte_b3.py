"""Escolha da fonte da B3: armazém, túnel ou a cópia congelada do Supabase.

``core.fonte_b3.consultar`` decide em quatro degraus. Aqui se prende:

* cada degrau lê de onde diz e marca ``attrs["fonte_b3"]``;
* a reserva (Supabase congelado) só entra com motivo registrado, e o aviso
  fica de pé pela janela do cache dos loaders;
* o degrau que falha não tenta o seguinte (papel local falhou -> não chama o
  túnel), para a reserva ser uma só e nomeada.

O ``conftest`` desliga o papel e o túnel em toda a suíte; cada teste daqui
liga o que precisa.
"""
from __future__ import annotations

import pandas as pd
import pytest

import core.fonte_b3 as fb3
import core.us_read as us_read
from core.armazem_remoto import ArmazemRemotoIndisponivel

SQL = "select ticker from market.assets where ticker = :t"


class _Leitor:
    """Dublê de ``core.market_read._q``: anota o engine de cada chamada."""

    def __init__(self, erro_com_engine: str | None = None):
        self.chamadas: list = []
        self.erro_com_engine = erro_com_engine

    def __call__(self, sql, params=None, engine=None):
        self.chamadas.append(engine)
        if engine is not None and self.erro_com_engine:
            frame = pd.DataFrame()
            frame.attrs.update(load_error="query_failed", error_type=self.erro_com_engine)
            return frame
        origem = "papel" if engine is not None else "banco_do_app"
        return pd.DataFrame({"ticker": ["PETR4"], "origem": [origem]})


@pytest.fixture
def remoto(monkeypatch):
    monkeypatch.setattr(us_read, "_db_is_local", lambda: False)
    monkeypatch.setattr(fb3, "_engine_do_papel", lambda url: f"engine<{url}>")
    return monkeypatch


def test_banco_local_le_direto(monkeypatch):
    monkeypatch.setattr(us_read, "_db_is_local", lambda: True)
    tunel = []
    monkeypatch.setattr(fb3, "_consultar_tunel", lambda *a: tunel.append(a))
    ler = _Leitor()
    out = fb3.consultar(SQL, {"t": "PETR4"}, ler_direto=ler)
    assert ler.chamadas == [None] and tunel == []
    assert out.attrs["fonte_b3"] == fb3.FONTE_ARMAZEM
    assert fb3.aviso_reserva() is None


def test_papel_le_o_armazem_com_engine_proprio(remoto):
    remoto.setattr(fb3, "_url_papel", lambda: "postgresql://app4_leitura@localhost/db")
    ler = _Leitor()
    out = fb3.consultar(SQL, {"t": "PETR4"}, ler_direto=ler)
    assert ler.chamadas == ["engine<postgresql://app4_leitura@localhost/db>"]
    assert out["origem"].tolist() == ["papel"]
    assert out.attrs["fonte_b3"] == fb3.FONTE_ARMAZEM
    assert fb3.aviso_reserva() is None


def test_papel_que_falha_cai_na_reserva_sem_tentar_o_tunel(remoto):
    remoto.setattr(fb3, "_url_papel", lambda: "postgresql://app4_leitura@localhost/db")
    tunel = []
    remoto.setattr(fb3, "_consultar_tunel", lambda *a: tunel.append(a))
    ler = _Leitor(erro_com_engine="OperationalError")
    out = fb3.consultar(SQL, None, ler_direto=ler)
    assert tunel == []
    assert ler.chamadas == ["engine<postgresql://app4_leitura@localhost/db>", None]
    assert out["origem"].tolist() == ["banco_do_app"]
    assert out.attrs["fonte_b3"] == fb3.FONTE_RESERVA
    assert "OperationalError" in fb3.aviso_reserva()


def test_tunel_entrega_o_quadro(remoto):
    vindo = pd.DataFrame({"ticker": ["VALE3"]})
    pedidos = []
    remoto.setattr(fb3, "_consultar_tunel", lambda sql, params: pedidos.append((sql, params)) or vindo)
    ler = _Leitor()
    out = fb3.consultar(SQL, {"t": "VALE3"}, ler_direto=ler)
    assert pedidos == [(SQL, {"t": "VALE3"})]
    assert ler.chamadas == []
    assert out["ticker"].tolist() == ["VALE3"]
    assert out.attrs["fonte_b3"] == fb3.FONTE_TUNEL
    assert fb3.aviso_reserva() is None


def test_tunel_nao_configurado_cai_na_reserva_e_diz_por_que(remoto):
    ler = _Leitor()
    out = fb3.consultar(SQL, None, ler_direto=ler)
    assert ler.chamadas == [None]
    assert out.attrs["fonte_b3"] == fb3.FONTE_RESERVA
    assert fb3.aviso_reserva() == "túnel do armazém não configurado neste ambiente"


def test_tunel_fora_do_ar_cai_na_reserva_com_a_primeira_linha_do_erro(remoto):
    def _cai(sql, params):
        raise ArmazemRemotoIndisponivel("HTTP 503: armazém fora do ar\ndetalhe interno",
                                        fora_do_ar=True)

    remoto.setattr(fb3, "_consultar_tunel", _cai)
    out = fb3.consultar(SQL, None, ler_direto=_Leitor())
    assert out.attrs["fonte_b3"] == fb3.FONTE_RESERVA
    aviso = fb3.aviso_reserva()
    assert aviso == "túnel do armazém: HTTP 503: armazém fora do ar"
    assert "detalhe interno" not in aviso


def test_aviso_some_depois_da_janela_do_cache(remoto):
    agora = [1_000_000.0]
    remoto.setattr(fb3, "_agora", lambda: agora[0])
    fb3.consultar(SQL, None, ler_direto=_Leitor())
    agora[0] += fb3.JANELA_AVISO_S
    assert fb3.aviso_reserva() is not None
    agora[0] += 1
    assert fb3.aviso_reserva() is None


def test_leitura_boa_depois_nao_apaga_o_aviso(remoto):
    """O quadro congelado lido antes continua no cache: o aviso vale pela janela."""
    fb3.consultar(SQL, None, ler_direto=_Leitor())
    remoto.setattr(fb3, "_consultar_tunel", lambda sql, params: pd.DataFrame({"x": [1]}))
    fb3.consultar(SQL, None, ler_direto=_Leitor())
    assert fb3.aviso_reserva() is not None


def test_engine_do_papel_e_reaproveitado_e_so_leitura(monkeypatch):
    criados = []

    def _criar(url, **kw):
        criados.append((url, kw))
        return object()

    import sqlalchemy

    monkeypatch.setattr(sqlalchemy, "create_engine", _criar)
    a = fb3._engine_do_papel("postgresql://app4_leitura@localhost/db")
    b = fb3._engine_do_papel("postgresql://app4_leitura@localhost/db")
    assert a is b and len(criados) == 1
    opcoes = criados[0][1]["connect_args"]["options"]
    assert "default_transaction_read_only=on" in opcoes


# ── quem lê a B3 fora do market_read passa pela mesma fonte ─────────────────

def test_dossie_le_mercado_pela_fonte_da_b3_e_devolve_none_no_lugar_de_nan(monkeypatch):
    import core.dossie_b3 as dossie
    import core.market_read as mr

    pedidos = []

    def _q_b3(sql, params=None):
        pedidos.append(params)
        return pd.DataFrame({"ano": [2024, 2025], "lucro": [1.5, float("nan")]})

    monkeypatch.setattr(mr, "_q_b3", _q_b3)
    monkeypatch.setattr("core.database.get_engine",
                        lambda: pytest.fail("market.* não vai ao banco do app"))
    linhas = dossie._rows("SELECT ano, lucro FROM market.income_statements WHERE ticker = :t",
                          t="PETR4")
    assert pedidos == [{"t": "PETR4"}]
    assert linhas == [{"ano": 2024, "lucro": 1.5}, {"ano": 2025, "lucro": None}]


def test_dossie_falha_de_leitura_levanta_e_banco_ausente_e_vazio(monkeypatch):
    import core.dossie_b3 as dossie
    import core.market_read as mr

    def _falha(**attrs):
        frame = pd.DataFrame()
        frame.attrs.update(attrs)
        return lambda sql, params=None: frame

    monkeypatch.setattr(mr, "_q_b3", _falha(load_error="query_failed", error_type="OperationalError"))
    with pytest.raises(RuntimeError, match="OperationalError"):
        dossie._rows("SELECT 1 FROM market.assets")
    monkeypatch.setattr(mr, "_q_b3", _falha(load_error="database_unavailable"))
    assert dossie._rows("SELECT 1 FROM market.assets") == []


def test_dossie_documentos_e_macro_ficam_no_banco_do_app(monkeypatch):
    """O papel só-leitura do armazém não enxerga ``public.docs_corporativos``
    nem ``public.macro``: pedir ao armazém registraria uma falha falsa."""
    import core.dossie_b3 as dossie
    import core.market_read as mr

    monkeypatch.setattr(mr, "_q_b3", lambda *a, **k: pytest.fail("não é B3 de mercado"))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, stmt, params):
            class _R:
                def mappings(self):
                    return self

                def all(self):
                    return [{"ok": params}]
            return _R()

    class _Eng:
        def connect(self):
            return _Conn()

    monkeypatch.setattr("core.database.get_engine", lambda: _Eng())
    assert dossie._rows("SELECT 1 FROM public.docs_corporativos", _banco_do_app=True,
                        n=3) == [{"ok": {"n": 3}}]


def test_selo_da_b3_le_carimbo_e_preco_pela_fonte_da_b3(monkeypatch):
    import datetime as dt

    import core.frescor as frescor
    import core.market_read as mr

    respostas = {
        "max(updated_at)": pd.Timestamp("2026-11-17 03:00", tz="UTC"),
        "to_regclass": True,
        "GREATEST": dt.date(2026, 11, 13),
    }

    def _q_b3(sql, params=None):
        chave = next(k for k in respostas if k in sql)
        return pd.DataFrame({"v": [respostas[chave]]})

    monkeypatch.setattr(mr, "_q_b3", _q_b3)
    monkeypatch.setattr("core.database.get_engine",
                        lambda: pytest.fail("o selo não lê o Supabase congelado"))
    assert frescor.carimbo_do_modulo("b3") == respostas["max(updated_at)"]
    assert frescor.data_do_preco("b3") == dt.date(2026, 11, 13)


def test_selo_da_b3_sem_leitura_fica_sem_data(monkeypatch):
    import core.frescor as frescor
    import core.market_read as mr

    vazio = pd.DataFrame()
    vazio.attrs["load_error"] = "query_failed"
    monkeypatch.setattr(mr, "_q_b3", lambda sql, params=None: vazio)
    assert frescor.carimbo_do_modulo("b3") is None
    assert frescor.data_do_preco("b3") is None


def test_confianca_sem_engine_mede_pela_fonte_da_b3(monkeypatch):
    """O universo apto da B3 e o Grau de Confiança saem daqui: medidos na cópia
    congelada, ``dias_preco`` cresceria todo dia sem a ingestão ter parado."""
    import core.data_confidence as dc
    import core.market_read as mr

    pedidos = []

    def _q_b3(sql, params=None):
        pedidos.append((sql, params))
        return pd.DataFrame({
            "ticker": ["PETR4", "NOVA3"], "n_key_ttm": [9, 0],
            "ymax": [2025, float("nan")], "dias_preco": [3, float("nan")],
            "n_flags": [0, 0], "frac_px_invalida": [0.0, 0.0]})

    monkeypatch.setattr(mr, "_q_b3", _q_b3)
    monkeypatch.setattr("core.database.get_engine",
                        lambda: pytest.fail("a confiança não mede o Supabase congelado"))
    out = {d["ticker"]: d for d in dc.compute_confidence(tickers=["petr4.sa", "NOVA3"])}
    assert pedidos[0][1]["tks"] == ["PETR4", "NOVA3"]
    assert "market.historical_prices" in pedidos[0][0]
    assert out["PETR4"]["dias_preco"] == 3 and type(out["PETR4"]["ymax"]) is int
    assert out["NOVA3"]["dias_preco"] is None and out["NOVA3"]["ymax"] is None


def test_confianca_com_leitura_falha_e_vazia(monkeypatch):
    import core.data_confidence as dc
    import core.market_read as mr

    vazio = pd.DataFrame()
    vazio.attrs.update(load_error="query_failed", error_type="OperationalError")
    monkeypatch.setattr(mr, "_q_b3", lambda sql, params=None: vazio)
    assert dc.compute_confidence() == []


def test_aviso_do_topo_e_decidido_depois_da_subaba(monkeypatch):
    """O aviso de reserva enche o espaço do topo só se houver motivo.

    É chamado no ``finally`` do despacho das subabas: a leitura que cai na
    reserva no meio da subaba ainda chega ao aviso do mesmo run.
    """
    import contextlib

    import core.fonte_b3 as fonte
    import views.empresas_b3 as tela

    avisos = []
    monkeypatch.setattr(tela.st, "warning", lambda msg, **k: avisos.append(msg))
    monkeypatch.setattr(tela, "detalhe_tecnico", lambda *a, **k: None)

    class Slot:
        usado = 0

        @contextlib.contextmanager
        def container(self):
            Slot.usado += 1
            yield

    monkeypatch.setattr(fonte, "aviso_reserva", lambda: None)
    tela._aviso_fonte_reserva_b3(Slot())
    assert Slot.usado == 0 and avisos == []

    monkeypatch.setattr(fonte, "aviso_reserva", lambda: "túnel fora do ar")
    tela._aviso_fonte_reserva_b3(Slot())
    assert Slot.usado == 1 and len(avisos) == 1


def test_rotas_que_leem_a_b3_pelo_market_read_ganham_o_aviso():
    """Portfólio Global lê preço mensal da B3 por ``market_read``; sem o aviso
    no roteador, a cópia congelada entraria nas contas sem ninguém saber."""
    import ast
    from pathlib import Path

    arvore = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    rotas = conjunto = None
    for n in ast.walk(arvore):
        alvo = (n.target if isinstance(n, ast.AnnAssign)
                else n.targets[0] if isinstance(n, ast.Assign) else None)
        nome = getattr(alvo, "id", "")
        if nome == "_ROTAS":
            rotas = set(ast.literal_eval(n.value).values())
        elif nome == "_ROTAS_QUE_LEEM_B3":
            conjunto = set(ast.literal_eval(n.value.args[0]))
    assert conjunto and conjunto <= rotas
    # Empresas B3 reserva o próprio lugar; as demais, pelo roteador.
    leem = {"dashboard_geral", "fiis", "portfolio_global"}
    assert leem <= conjunto
    assert "empresas_b3" not in conjunto


def test_aviso_unico_serve_empresas_b3_e_o_roteador(monkeypatch):
    import streamlit as st

    import core.fonte_b3 as fb
    from design import componentes

    escritos = []

    class _Slot:
        def container(self):
            import contextlib
            return contextlib.nullcontext()

    monkeypatch.setattr(fb, "aviso_reserva", lambda: "túnel caiu")
    monkeypatch.setattr(st, "warning", lambda t, **_: escritos.append(t))
    monkeypatch.setattr(componentes, "detalhe_tecnico", lambda *a, **k: None)
    componentes.aviso_fonte_reserva_b3(_Slot())
    assert escritos and "15/11/2026" in escritos[0]

    escritos.clear()
    monkeypatch.setattr(fb, "aviso_reserva", lambda: None)
    componentes.aviso_fonte_reserva_b3(_Slot())
    assert escritos == []


# ── O armazém voltou: cache limpo e aviso retirado (20/11/2026) ──────────────

@pytest.fixture
def volta(remoto):
    """Relógio dublê, cache dublê e uma reserva registrada no instante 0."""
    agora = [1_000_000.0]
    limpezas: list[int] = []
    remoto.setattr(fb3, "_agora", lambda: agora[0])
    remoto.setattr(fb3, "_limpar_caches", lambda: limpezas.append(1))

    def _cai(sql, params):
        raise ArmazemRemotoIndisponivel("sem resposta (ChunkedEncodingError)")

    remoto.setattr(fb3, "_consultar_tunel", _cai)
    fb3.consultar(SQL, None, ler_direto=_Leitor())
    assert fb3.aviso_reserva() is not None
    return agora, limpezas


def _tunel_responde(remoto, perguntas: list):
    def _ok(sql, params):
        perguntas.append(sql)
        return pd.DataFrame({"ok": [1]})

    remoto.setattr(fb3, "_consultar_tunel", _ok)


def test_sem_reserva_nao_pergunta_nada(remoto):
    perguntas: list = []
    _tunel_responde(remoto, perguntas)
    assert fb3.conferir_volta() is False
    assert perguntas == []


def test_reserva_recente_espera_o_intervalo_antes_de_perguntar(volta, remoto):
    agora, limpezas = volta
    perguntas: list = []
    _tunel_responde(remoto, perguntas)
    agora[0] += fb3.INTERVALO_CONFERE_S - 1
    assert fb3.conferir_volta() is False
    assert perguntas == [] and limpezas == []


def test_armazem_que_voltou_limpa_o_cache_e_tira_o_aviso(volta, remoto):
    agora, limpezas = volta
    perguntas: list = []
    _tunel_responde(remoto, perguntas)
    agora[0] += fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is True
    assert perguntas == ["SELECT 1 AS ok"] and limpezas == [1]
    assert fb3.aviso_reserva() is None
    # Sem reserva nova, nada mais a conferir.
    agora[0] += 10 * fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is False and perguntas == ["SELECT 1 AS ok"]


def test_armazem_ainda_fora_mantem_o_aviso_e_pergunta_de_minuto_em_minuto(volta, remoto):
    agora, limpezas = volta
    perguntas: list = []

    def _cai(sql, params):
        perguntas.append(sql)
        raise ArmazemRemotoIndisponivel("HTTP 530")

    remoto.setattr(fb3, "_consultar_tunel", _cai)
    agora[0] += fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is False
    assert fb3.conferir_volta() is False  # mesmo minuto: não pergunta de novo
    assert len(perguntas) == 1 and limpezas == []
    assert fb3.aviso_reserva() is not None
    agora[0] += fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is False and len(perguntas) == 2


def test_reserva_nova_durante_a_pergunta_mantem_o_aviso(volta, remoto):
    agora, limpezas = volta

    def _ok_mas_outra_caiu(sql, params):
        fb3._registrar_reserva("outra leitura caiu agora")
        return pd.DataFrame({"ok": [1]})

    remoto.setattr(fb3, "_consultar_tunel", _ok_mas_outra_caiu)
    agora[0] += fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is True and limpezas == [1]
    assert fb3.aviso_reserva() == "outra leitura caiu agora"


def test_papel_local_que_voltou_tambem_conta(volta, remoto):
    agora, _ = volta

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            return None

    class _Engine:
        def connect(self):
            return _Conn()

    remoto.setattr(fb3, "_url_papel", lambda: "postgresql://app4_leitura@localhost/db")
    remoto.setattr(fb3, "_engine_do_papel", lambda url: _Engine())
    agora[0] += fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is True and fb3.aviso_reserva() is None


def test_roteador_confere_a_volta_antes_de_desenhar_a_tela():
    """Depois do desenho seria tarde: a tela mostraria a cópia sem o aviso."""
    from pathlib import Path

    fonte = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert fonte.index("conferir_volta()") < fonte.index("modulo.render()")


def test_resultado_guardado_com_a_reserva_avisa_quando_o_armazem_volta(monkeypatch):
    import design.componentes as comp

    estado: dict = {}
    avisos: list[str] = []
    monkeypatch.setattr(comp.st, "session_state", estado)
    monkeypatch.setattr(comp.st, "warning", avisos.append)
    monkeypatch.setattr(comp, "detalhe_tecnico", lambda *a, **k: None)

    monkeypatch.setattr(fb3, "aviso_reserva", lambda: "túnel caiu")
    comp.guardar_fonte_do_resultado_b3("pb3_fonte_reserva")
    assert estado["pb3_fonte_reserva"] == "túnel caiu"
    # Armazém ainda fora: o aviso do topo cobre, este fica calado.
    comp.aviso_resultado_da_reserva_b3("pb3_fonte_reserva", "gere o portfólio")
    assert avisos == []
    # Armazém voltou: o resultado guardado continua velho e a tela diz.
    monkeypatch.setattr(fb3, "aviso_reserva", lambda: None)
    comp.aviso_resultado_da_reserva_b3("pb3_fonte_reserva", "gere o portfólio")
    assert len(avisos) == 1 and "gere o portfólio de novo" in avisos[0]


def test_resultado_gerado_com_o_armazem_nao_avisa(monkeypatch):
    import design.componentes as comp

    estado: dict = {}
    avisos: list[str] = []
    monkeypatch.setattr(comp.st, "session_state", estado)
    monkeypatch.setattr(comp.st, "warning", avisos.append)
    monkeypatch.setattr(fb3, "aviso_reserva", lambda: None)
    comp.guardar_fonte_do_resultado_b3("pb3_fonte_reserva")
    comp.aviso_resultado_da_reserva_b3("pb3_fonte_reserva", "gere o portfólio")
    assert estado["pb3_fonte_reserva"] is None and avisos == []


@pytest.mark.parametrize("chave, view, funcao, avaliador", [
    ("pb3_intel_cache", "views.portfolio_b3", "_intel_avaliar_cached", "veredito.avaliar_acao_b3"),
    ("pb3_quali_cache", "views.portfolio_b3", "_quali_avaliar_cached", "avaliar_para_selecao"),
    ("fii_intel_cache", "views.fiis", "_intel_avaliar_fii", "veredito.avaliar_ativo"),
    ("us_intel_cache", "views.empresas_americanas", "_intel_avaliar_us", "veredito.avaliar_ativo"),
])
def test_parecer_guardado_na_sessao_e_refeito_quando_o_armazem_volta(
        volta, remoto, monkeypatch, chave, view, funcao, avaliador):
    """Achado do Codex no #787: o parecer da sessão feito com a cópia
    decidia veto e troca da carteira gerada de novo depois da volta."""
    import importlib

    import streamlit as st

    agora, _ = volta
    estado: dict = {}
    monkeypatch.setattr(st, "session_state", estado)
    mod = importlib.import_module(view)
    chamadas: list[str] = []

    def _avalia(tk, *a, **k):
        chamadas.append(tk)
        return {"classificacao": f"parecer {len(chamadas)}"}

    alvo, _, nome = avaliador.rpartition(".")
    monkeypatch.setattr(getattr(mod, alvo) if alvo else mod, nome, _avalia)

    primeiro = getattr(mod, funcao)("ABCD3")
    assert getattr(mod, funcao)("ABCD3") == primeiro and len(chamadas) == 1

    _tunel_responde(remoto, [])
    agora[0] += fb3.INTERVALO_CONFERE_S
    assert fb3.conferir_volta() is True
    segundo = getattr(mod, funcao)("ABCD3")
    assert len(chamadas) == 2 and segundo != primeiro
    assert estado[chave] == {"ABCD3": segundo}


def test_cache_de_sessao_sem_marca_vale_para_a_geracao_atual(monkeypatch):
    """Dicionário guardado antes deste PR (ou por teste) é aproveitado."""
    import streamlit as st

    estado = {"pb3_quali_cache": {"AAAA3": {"classificacao": "vetar"}}}
    monkeypatch.setattr(st, "session_state", estado)
    assert fb3.cache_de_sessao("pb3_quali_cache") == {"AAAA3": {"classificacao": "vetar"}}
    assert estado["pb3_quali_cache__geracao"] == fb3.geracao()
