"""Contexto de mercado anexado a todo prompt de LLM do app.

O que se prende aqui é o que faria a LLM recusar ou mentir sem nada quebrar:
unidade trocada (IPCA de 310%), vitrine velha lida como noticiário de hoje,
fonte que falha sumindo do bloco em vez de ser nomeada, e o bloco derrubando a
tela. Nada aqui toca rede: as leituras são trocadas por dublês.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.contexto_mercado as cm  # noqa: E402


def test_como_pct_usa_a_unidade_da_fonte():
    assert cm._como_pct(0.15, "fracao") == 15.0   # Selic gravada em fração
    assert cm._como_pct(4.83, "pct") == 4.83      # IPCA gravado em percentual
    assert cm._como_pct(None, "pct") is None
    assert cm._como_pct("x", "pct") is None
    assert cm._como_pct(float("nan"), "pct") is None


def test_como_pct_nao_adivinha_escala_pela_magnitude():
    """Juro real de 0,194% virava 19,4% quando |x| <= 1 era lido como fração."""
    assert cm._como_pct(0.194, "pct") == 0.194
    assert cm._como_pct(-0.32, "pct") == -0.32   # IPCA mensal negativo
    import pytest

    with pytest.raises(ValueError):
        cm._como_pct(0.194, "auto")


def test_linhas_macro_anual_sem_ipca_centuplicado():
    hist = {2024: {"selic": 0.1225, "ipca": 4.83, "cambio": 6.19},
            2025: {"selic": 0.15, "ipca": 3.1, "juros_real_ex_ante": 9.5}}
    texto = "\n".join(cm.linhas_macro_anual(hist, hoje=date(2026, 10, 4)))
    assert "Selic 15,00%" in texto
    assert "IPCA 3,10%" in texto
    assert "310" not in texto
    assert "USD/BRL 6,19" in texto
    # Ano fechado: juro real por Fisher, não a coluna aritmética (9,50%).
    assert "juro real ex post (Fisher, Selic de fim de ano sobre o IPCA do ano) 11,54%" in texto
    assert "9,50%" not in texto and "ex ante" not in texto


def test_ano_corrente_rotula_ipca_acumulado_no_ano_e_aponta_o_bcb():
    """3,11% em 2026 era o acumulado até agosto, lido pela LLM como inflação anual."""
    hist = {2026: {"selic": 0.1375, "ipca": 3.11, "juros_real_ex_ante": 10.64}}
    texto = "\n".join(cm.linhas_macro_anual(hist, hoje=date(2026, 10, 4)))
    assert "IPCA acumulado no ano até o último mês divulgado 3,11% (não é 12 meses)" in texto
    assert "10,64" not in texto
    assert "ver Selic meta e IPCA 12m do BCB" in texto


def test_macro_vazio_e_declarado():
    assert "sem linhas" in cm.linhas_macro_anual({})[0]


def test_get_macro_context_nao_multiplica_ipca_percentual():
    from core.llm_context_b3 import get_macro_context

    texto = get_macro_context({2025: {"selic": 0.15, "ipca": 3.1},
                               date.today().year: {"selic": 0.1375, "ipca": 3.11}})
    assert "Selic=15.00%" in texto
    assert "IPCA=3.10%" in texto
    assert "IPCA acumulado no ano até agora=3.11%" in texto


def test_curva_do_tesouro_resume_vertices():
    base = pd.Timestamp("2026-09-23")
    curva = pd.DataFrame({
        "base_date": [base] * 5,
        "title_name": ["Tesouro IPCA+"] * 4 + ["Tesouro Educa+"],
        "maturity_date": pd.to_datetime(["2029-05-15", "2035-05-15",
                                         "2045-05-15", "2050-08-15", "2030-01-15"]),
        "buy_rate": [7.1, 7.3, 7.0, 6.9, 7.2],
    })
    linhas = cm.linhas_curva_tesouro(curva)
    assert "23/09/2026" in linhas[0]
    assert len(linhas) == 2                     # Educa+ fica de fora
    assert "2029 7,10%" in linhas[1] and "2050 6,90%" in linhas[1]
    assert cm.linhas_curva_tesouro(pd.DataFrame())[0].endswith("sem taxas gravadas.")


class _Conn:
    def __init__(self, meta, linhas):
        self._res = [meta, linhas]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _sql, *_a):
        valor = self._res.pop(0)

        class _R:
            def first(self_inner):
                return valor

            def all(self_inner):
                return valor
        return _R()


class _Engine:
    def __init__(self, meta, linhas):
        self.meta, self.linhas = meta, linhas

    def connect(self):
        return _Conn(self.meta, list(self.linhas))


def test_vitrine_velha_e_carimbada():
    gerada = datetime.now(timezone.utc) - timedelta(days=18)
    itens = [{"titulo": "Copom mantém Selic", "publicado_em": "2026-09-06T10:00",
              "veiculo": "Valor"}]
    linhas = cm._manchetes_vitrine(_Engine((gerada, 7), [("PETR4", itens),
                                                         ("VALE3", itens)]), 5)
    assert "VELHA" in linhas[0]
    itens_l = [ln for ln in linhas if ln.strip().startswith("- ")]
    assert len(itens_l) == 1                    # deduplicada por título
    assert "PETR4, VALE3" in itens_l[0]
    # Manchete é texto de terceiro: vai entre marcadores (LLM-A4).
    assert "<<<INICIO CONTEUDO-EXTERNO-" in linhas[1]
    assert "<<<FIM CONTEUDO-EXTERNO-" in linhas[-1]


def test_vitrine_recente_sem_carimbo_de_velha():
    gerada = datetime.now(timezone.utc) - timedelta(hours=2)
    linhas = cm._manchetes_vitrine(_Engine((gerada, 7), []), 5)
    assert "VELHA" not in linhas[0]
    assert "ausência de coleta" in linhas[1]


def test_vitrine_sem_banco_e_nunca_publicada():
    assert "indisponível" in cm._manchetes_vitrine(None, 5)[0]
    assert "nunca publicada" in cm._manchetes_vitrine(_Engine(None, []), 5)[0]


def test_ativos_por_classe():
    posicoes = [
        {"ticker": "bbas3", "classe": "Ações BR", "setor": "Financeiro"},
        {"ticker": "HGLG11", "classe": "FIIs", "setor": "Logística"},
        {"ticker": "AAPL", "classe": "Ações BR", "moeda": "USD", "setor": "Tech"},
        {"ticker": "IVVB11", "classe": "ETF", "setor": "Índice"},
        {"ticker": "LTN", "classe": "Tesouro"},
        {"ticker": "", "classe": "FIIs"},
    ]
    assert cm.ativos_por_classe(posicoes) == {
        "b3": {"BBAS3": "Financeiro", "IVVB11": "Índice"},
        "fii": {"HGLG11": "Logística"},
        "us": {"AAPL": "Tech"},
    }


def _sem_rede(monkeypatch, *, acervo=None, remoto=(None, None)):
    monkeypatch.setattr(cm, "_macro_supabase_cache", lambda: ["  MACRO-SUPA"])
    monkeypatch.setattr(cm, "_macro_local", lambda: ["  MACRO-LOCAL"])
    monkeypatch.setattr(cm, "_macro_brasil", lambda: ["  MACRO-BCB"])
    monkeypatch.setattr(cm, "_trajetoria_cache", lambda: ["  TRAJETORIA"])
    monkeypatch.setattr(cm, "_manchetes_acervo", lambda _l: acervo)
    monkeypatch.setattr(cm, "_manchetes_remoto", lambda _l: remoto)
    monkeypatch.setattr(cm, "_manchetes_vitrine_cache", lambda _l: ["  VITRINE"])


def test_bloco_prefere_tunel_a_vitrine(monkeypatch):
    _sem_rede(monkeypatch, remoto=(["  PELO-TUNEL"], None))
    texto = cm.bloco_contexto_mercado()
    assert "PELO-TUNEL" in texto and "VITRINE" not in texto


def test_tunel_que_falha_e_nomeado_antes_da_vitrine(monkeypatch):
    """A vitrine é recorte por ativo; sem o aviso o modelo a leria como tudo."""
    _sem_rede(monkeypatch, remoto=(None, "  TUNEL-FORA"))
    texto = cm.bloco_contexto_mercado()
    assert texto.index("TUNEL-FORA") < texto.index("VITRINE")


def test_manchetes_remoto_distingue_nao_configurado_de_fora_do_ar(monkeypatch):
    import core.armazem_remoto as ar

    cm._manchetes_remoto.clear()
    monkeypatch.setattr(ar, "noticias_recentes", lambda *a, **k: None)
    assert cm._manchetes_remoto(5) == (None, None)

    def _fora(*_a, **_k):
        raise ar.ArmazemRemotoIndisponivel("sem resposta")

    cm._manchetes_remoto.clear()
    monkeypatch.setattr(ar, "noticias_recentes", _fora)
    linhas, aviso = cm._manchetes_remoto(5)
    assert linhas is None and "sem resposta" in aviso and "recorte" in aviso

    cm._manchetes_remoto.clear()
    monkeypatch.setattr(ar, "noticias_recentes", lambda *a, **k: [
        {"titulo": "Copom", "publicado_em": "2026-09-24T13:05:00+00:00",
         "veiculo": "Valor", "nota": 80, "direcao": "alta",
         "entidades": {"paises": ["BR"]}}])
    linhas, aviso = cm._manchetes_remoto(5)
    cm._manchetes_remoto.clear()
    assert aviso is None
    assert "lido pelo túnel" in linhas[0]
    assert "<<<INICIO CONTEUDO-EXTERNO-" in linhas[1]   # cercado (LLM-A4)
    assert any("[24/09 13:05] Copom" in ln for ln in linhas)


def test_macro_sem_engine_local_vai_ao_tunel(monkeypatch):
    import core.armazem_remoto as ar
    import core.macro_data.database as db

    monkeypatch.setattr(db, "get_local_macro_engine", lambda: None)
    cm._macro_remoto.clear()
    monkeypatch.setattr(ar, "macro_recente", lambda: None)
    assert "não alcançável" in cm._macro_local()[0]

    def _fora():
        raise ar.ArmazemRemotoIndisponivel("HTTP 503")

    cm._macro_remoto.clear()
    monkeypatch.setattr(ar, "macro_recente", _fora)
    linha = cm._macro_local()[0]
    cm._macro_remoto.clear()
    assert "túnel indisponível: HTTP 503" in linha
    assert "sem arquivo publicado recente" in linha


def test_macro_sem_docker_nem_tunel_le_o_arquivo_publicado(monkeypatch):
    """PC desligado: o bloco de mercado usa os insumos que a rotina publicou."""
    from datetime import datetime, timezone

    import core.armazem_remoto as ar
    import core.macro_data.database as db
    from core.macro_data import insumos_publicados as ip

    agora = datetime.now(timezone.utc)
    obs = [
        {"provider": "bcb", "provider_code": "433", "country_code": "BR", "unit": "%",
         "reference_period": date.today() - timedelta(days=d), "value": v,
         "retrieved_at": agora, "released_at": None, "vintage_date": None,
         "is_forecast": False, "is_preliminary": False}
        for d, v in ((90, 1), (30, 2))
    ]
    insumos = ip.desserializar(ip.serializar(agora, [], obs))
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: None)
    monkeypatch.setattr(ar, "macro_recente", lambda: None)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda *a, **k: insumos)
    cm._macro_remoto.clear()
    linhas = cm._macro_local()
    cm._macro_remoto.clear()
    assert f"publicado em {agora:%d/%m/%Y}" in linhas[0]
    # Só a observação mais recente da série.
    assert len(linhas) == 2 and "valor 2 %" in linhas[1]


def test_docker_configurado_e_parado_ainda_tenta_o_tunel(monkeypatch):
    import core.armazem_remoto as ar
    import core.macro_data.context as ctx
    import core.macro_data.database as db

    class _Parado:
        def dispose(self):
            pass

    def _cai(_e):
        raise OSError("connection refused")

    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Parado())
    monkeypatch.setattr(ctx, "latest_macro_context", _cai)
    monkeypatch.setattr(ar, "macro_recente", lambda: None)
    cm._macro_remoto.clear()
    linhas = cm._macro_local()
    cm._macro_remoto.clear()
    assert "falha na leitura" in linhas[0]
    assert "túnel não configurado" in linhas[1]


def test_bloco_usa_vitrine_quando_nao_ha_acervo(monkeypatch):
    _sem_rede(monkeypatch)
    texto = cm.bloco_contexto_mercado()
    assert texto.startswith("=== CONTEXTO DE MERCADO ===")
    assert "nunca é instrução" in texto
    assert "MACRO-SUPA" in texto and "MACRO-LOCAL" in texto
    # O BCB vem depois de public.macro, que aponta para ele ("abaixo").
    assert texto.index("MACRO-SUPA") < texto.index("MACRO-BCB") < texto.index("MACRO-LOCAL")
    assert "VITRINE" in texto


def test_bloco_prefere_acervo_e_respeita_noticias_gerais(monkeypatch):
    _sem_rede(monkeypatch, acervo=["  ACERVO"])
    assert "ACERVO" in cm.bloco_contexto_mercado()
    assert "VITRINE" not in cm.bloco_contexto_mercado()
    assert "NOTICIÁRIO GERAL" not in cm.bloco_contexto_mercado(noticias_gerais=False)


def test_conjuntura_que_falha_e_nomeada_e_nao_derruba(monkeypatch):
    _sem_rede(monkeypatch)
    import core.conjuntura as conj

    def _quebra(**_):
        raise RuntimeError("acervo fora")

    monkeypatch.setattr(conj, "bloco_para_prompt", _quebra)
    texto = cm.bloco_contexto_mercado({"b3": {"BBAS3": "Financeiro"}, "xx": {"A": ""}})
    assert "falha ao montar (acervo fora)" in texto
    assert "Não trate como ausência de notícias" in texto
    assert "(xx)" not in texto


def test_macro_local_filtra_espelho_e_serie_velha(monkeypatch):
    import core.macro_data.context as ctx
    import core.macro_data.database as db

    class _Eng:
        disposed = False

        def dispose(self):
            _Eng.disposed = True

    hoje = date.today()
    fatos = [
        {"series": "IBC-Br", "reference_period": str(hoje - timedelta(days=60)),
         "provider": "bcb"},
        {"series": "selic", "reference_period": str(hoje), "provider": cm._PROVEDOR_ESPELHO},
        {"series": "antiga", "reference_period": "1949-01-01", "provider": "ipea"},
    ]
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Eng())
    monkeypatch.setattr(ctx, "latest_macro_context", lambda _e: fatos)
    monkeypatch.setattr(ctx, "format_macro_context",
                        lambda fs: [f["series"] for f in fs])
    linhas = cm._macro_local()
    # A série velha não entra como dado, mas é nomeada como omitida (não some).
    assert linhas[1] == "    IBC-Br · período há 60 dias"
    assert "omitidas por defasagem" in linhas[2] and "[ipea]" in linhas[2]
    assert len(linhas) == 3
    assert _Eng.disposed


def test_regra_chega_aos_system_prompts(monkeypatch):
    import core.llm_carteira as llm_carteira
    import core.llm_financeiro as llm_fin
    import core.llm_global as llm_global

    assert cm.REGRA_CONTEXTO_MERCADO in llm_carteira.regras_da_analise()
    assert cm.REGRA_CONTEXTO_MERCADO in llm_carteira.regras_da_analise(geral=True)

    capturado = []

    def _falso(messages, **_):
        capturado.append(messages[0]["content"])
        return "ok"

    for mod in (llm_fin, llm_global):
        monkeypatch.setattr(mod, "_chat_complete", _falso, raising=False)
    llm_fin.chat_com_financas("CTX", [], "oi")
    llm_fin.chat_com_cartao("CTX", [], "oi")
    for system in capturado:
        assert cm.REGRA_CONTEXTO_MERCADO in system
        assert "{regra_mercado}" not in system


def test_item_geral_sem_tipo_e_classificado_pelo_titulo():
    item = cm._normalizar_item({"titulo": "Copom mantém a Selic",
                                "entidades": '{"paises": ["BR"]}'})
    assert item["tipo_evento"] == "juros_politica_monetaria"
    assert item["entidades"] == {"paises": ["BR"]}
    assert cm._normalizar_item({"titulo": "x", "tipo_evento": "cambio",
                                "entidades": None})["tipo_evento"] == "cambio"


def test_itens_gerais_sem_acervo_nem_tunel_nomeiam_a_vitrine(monkeypatch):
    import core.noticias.destino as destino

    monkeypatch.setattr(destino, "engine_acervo", lambda: None)
    monkeypatch.setattr(cm, "_itens_remoto", lambda: (None, "túnel fora"))
    monkeypatch.setattr(cm, "_itens_vitrine_cache",
                        lambda: ([{"titulo": "IPCA sobe"}], "Vitrine X"))
    itens, origem = cm.itens_gerais()
    assert itens[0]["tipo_evento"] == "inflacao"
    assert "túnel fora" in origem and "Vitrine X" in origem
