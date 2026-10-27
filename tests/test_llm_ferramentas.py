"""Function calling: as ferramentas de dados e o laço que as executa.

A LLM dos chats pede a série que a resposta precisa (preço de um ativo, série
macro longa, meses em que uma condição valeu). Aqui o banco, o painel e o CDI
são dublês: o que se testa é o texto que volta à LLM -- fonte, janela, n,
falha nomeada -- e o laço que não pode travar nem perder chamada.
"""
from __future__ import annotations

import json
import types
from datetime import date

import pytest

import core.llm_b3 as llm
import core.llm_ferramentas as lf

# ── dublês ───────────────────────────────────────────────────────────────────

class _Resultado:
    def __init__(self, linhas): self._linhas = linhas

    def all(self): return list(self._linhas)


class _Conn:
    def __init__(self, responder): self._responder = responder

    def __enter__(self): return self

    def __exit__(self, *a): return False

    def execute(self, sql, params): return _Resultado(self._responder(str(sql), params))


class _Engine:
    """Engine cujo ``execute`` devolve ``responder(sql, params)``."""

    def __init__(self, responder):
        self.chamadas: list[tuple[str, dict]] = []

        def _r(sql, params):
            self.chamadas.append((sql, dict(params)))
            return responder(sql, params)
        self._r = _r

    def connect(self): return _Conn(self._r)


def _mensal(inicio: tuple[int, int], valores: list[float]) -> list[tuple[date, float]]:
    ano, mes = inicio
    saida = []
    for v in valores:
        saida.append((date(ano, mes, 28), v))
        mes += 1
        if mes > 12:
            ano, mes = ano + 1, 1
    return saida


def _linha(mes: str, ipca: float, bova12_depois: float | None, selic: float = 10.0) -> dict:
    return {"mes": mes,
            "estado": {"selic": selic, "selic_6m": 0.0, "ipca12": ipca, "usd12": 0.05,
                       "us10": 4.0, "pl": 9.0, "bova12": 0.10},
            "depois": {"bova_3": 0.01, "bova_6": 0.02, "bova_12": bova12_depois,
                       "usd_12": 0.03, "selic_12": -0.5},
            "refs": {}}


def _meses(inicio_ano: int, n: int) -> list[str]:
    return [f"{inicio_ano + i // 12:04d}-{i % 12 + 1:02d}" for i in range(n)]


# ── serie_macro ──────────────────────────────────────────────────────────────

def test_serie_do_painel_traz_fonte_janela_e_percentil(monkeypatch):
    painel = [_linha(m, 3.0 + i * 0.1, 0.1) for i, m in enumerate(_meses(2020, 24))]
    monkeypatch.setattr(lf, "_painel", lambda: (painel, "publicado em 01/10/2026"))
    texto = lf.serie_macro("ipca_12m", anos=10)
    assert "IPCA acumulado em 12 meses" in texto
    assert "painel da Memória de Mercado, publicado em 01/10/2026" in texto
    assert "24 meses de 01/2020 a 12/2021" in texto
    assert "percentil 100" in texto
    # Menos de três anos: a LLM é avisada de que não é o ciclo longo.
    assert "HISTÓRICO CURTO" in texto


def test_pl_mediano_declara_que_nao_e_o_pl_do_ibovespa(monkeypatch):
    painel = [_linha(m, 4.0, 0.1) for m in _meses(2015, 60)]
    monkeypatch.setattr(lf, "_painel", lambda: (painel, "x"))
    assert "NÃO é o P/L do Ibovespa" in lf.serie_macro("pl_mediano")


def test_painel_ausente_e_falha_nomeada(monkeypatch):
    monkeypatch.setattr(lf, "_painel", lambda: (None, "arquivo ausente"))
    texto = lf.serie_macro("selic")
    assert "indisponível (arquivo ausente)" in texto


def test_cdi_sai_do_ultimo_dia_de_cada_mes_e_mostra_a_data_parcial(monkeypatch):
    # Taxa diária de 0,04% ~ 10,6% a.a.; o último ponto é 07/10/2026.
    cdi = {date(2026, 9, 1): 0.0003, date(2026, 9, 30): 0.0004,
           date(2026, 10, 7): 0.0004}
    monkeypatch.setattr(lf, "_cdi", lambda: cdi)
    texto = lf.serie_macro("cdi", anos=3)
    assert "2 meses de 09/2026 a 10/2026" in texto
    assert "em 07/10/2026" in texto


def test_serie_do_supabase_agrega_no_sql_e_passa_a_janela(monkeypatch):
    engine = _Engine(lambda sql, p: _mensal((2021, 1), [100 + i for i in range(48)]))
    monkeypatch.setattr(lf, "_engine", lambda: engine)
    texto = lf.serie_macro("bova11", anos=4)
    sql, params = engine.chamadas[0]
    assert "DISTINCT ON" in sql and "market.historical_prices" in sql
    assert params == {"chave": "BOVA11", "anos": 4}
    assert "48 meses de 01/2021 a 12/2024" in texto
    # 48 pontos passam do teto de 40: a amostra vira trimestral.
    assert "Pontos (trimestral)" in texto


def test_falha_do_supabase_vira_texto(monkeypatch):
    def _quebra(sql, p):
        raise RuntimeError("egress\nexceeded")
    monkeypatch.setattr(lf, "_engine", lambda: _Engine(_quebra))
    texto = lf.serie_macro("dolar")
    assert "falha na leitura do Supabase (egress exceeded)" in texto


def test_serie_desconhecida_lista_as_validas():
    texto = lf.serie_macro("petroleo")
    assert "não existe" in texto and "ipca_12m" in texto and "tesouro_ipca_2029" in texto


def test_anos_fica_entre_1_e_o_teto():
    assert lf._anos_arg(99, 10) == lf.MAX_ANOS
    assert lf._anos_arg(0, 10) == 1
    assert lf._anos_arg("lixo", 7) == 7


# ── preco_ativo ──────────────────────────────────────────────────────────────

def _responder_precos(b3: dict, eua: dict):
    def _r(sql, params):
        fonte = eua if "market_us" in sql else b3
        return [(tk, d, v) for tk in params["tickers"] for d, v in fonte.get(tk, [])]
    return _r


def test_preco_normaliza_ticker_e_cai_para_eua_so_no_que_falta(monkeypatch):
    itub = _mensal((2021, 1), [30.0] * 12 + [20.0] * 6 + [33.0] * 6)
    aapl = _mensal((2023, 1), [150.0 + i for i in range(12)])
    engine = _Engine(_responder_precos({"ITUB4": itub}, {"AAPL": aapl}))
    monkeypatch.setattr(lf, "_engine", lambda: engine)
    texto = lf.preco_ativo(["itub4.sa", "AAPL", "ITUB4", "XXXX3"], anos=5)
    (sql_b3, p_b3), (sql_eua, p_eua) = engine.chamadas
    assert p_b3["tickers"] == ["ITUB4", "AAPL", "XXXX3"]
    assert "market_us" in sql_eua and p_eua["tickers"] == ["AAPL", "XXXX3"]
    # A maior queda vai do pico (12/2021) ao vale e diz quando recuperou.
    assert "Maior queda de pico a vale nos fechamentos mensais: −33,3%" in texto
    assert "voltou ao pico em 07/2022" in texto
    assert "AAPL — fonte: market_us.prices_monthly" in texto
    assert "XXXX3: sem preço no banco (nem B3/FII nem EUA)" in texto
    assert texto.endswith("Retorno passado não é previsão.")


def test_preco_aceita_string_e_nomeia_os_excedentes(monkeypatch):
    engine = _Engine(lambda sql, p: [])
    monkeypatch.setattr(lf, "_engine", lambda: engine)
    texto = lf.preco_ativo("A1, B2; C3, D4, E5", anos=1)
    assert "Não consultados (teto de 4 por chamada): E5" in texto


def test_preco_sem_ticker_e_sem_banco():
    assert "Nenhum ticker informado" in lf.preco_ativo([])


def test_preco_com_banco_ausente(monkeypatch):
    monkeypatch.setattr(lf, "_engine", lambda: None)
    assert "banco Supabase indisponível" in lf.preco_ativo(["ITUB4"])


# ── meses_com_condicao ───────────────────────────────────────────────────────

def _painel_com_inflacao_alta() -> list[dict]:
    """Três surtos de IPCA > 6: 2011 (6 meses), 2015-16 (14 meses), 2021 (3)."""
    linhas = []
    for i, m in enumerate(_meses(2010, 144)):
        ano = int(m[:4])
        alta = (m.startswith("2011") and int(m[5:]) <= 6) or \
            ("2015-06" <= m <= "2016-07") or ("2021-04" <= m <= "2021-06")
        depois = None if ano >= 2021 else (0.2 if alta else 0.05)
        linhas.append(_linha(m, 7.0 if alta else 4.0, depois))
    return linhas


def test_condicao_agrupa_episodios_e_nao_faz_faixa_com_n_pequeno(monkeypatch):
    monkeypatch.setattr(lf, "_painel", lambda: (_painel_com_inflacao_alta(), "teste"))
    texto = lf.meses_com_condicao([{"campo": "ipca_12m", "operador": ">", "valor": 6}])
    assert "Meses com IPCA 12m (%) > 6,00" in texto
    assert "Hoje (12/2021) NÃO satisfaz" in texto
    # 6 + 14 + 3 = 23 meses; o surto de 14 meses vira dois episódios (06/2015
    # e 06/2016, 12 meses depois), e cada surto é um caso, não uma amostra.
    assert "23 meses satisfazem, agrupados em 4 episódios" in texto
    assert "- 06/2015:" in texto and "- 06/2016:" in texto
    assert "em 3 de 3 episódios com 12 meses completos (n=3, abaixo de 8" in texto
    assert "sem faixa" in texto and "mediana" not in texto.split("Contraponto")[0]
    assert "Contraponto, todos os" in texto
    assert "não previsão" in texto


def test_condicao_com_n_suficiente_traz_faixa_marcada_experimental(monkeypatch):
    # Um surto por ano, 12 anos: 12 episódios com futuro, n entre 8 e 30.
    linhas = [_linha(m, 7.0 if m.endswith("-01") else 4.0, 0.1)
              for m in _meses(2010, 156)]
    monkeypatch.setattr(lf, "_painel", lambda: (linhas, "teste"))
    texto = lf.meses_com_condicao(json.dumps(
        [{"campo": "ipca_12m", "operador": ">=", "valor": 7}]))
    assert "agrupados em 13 episódios" in texto
    assert "Os 10 episódios mais recentes (mais 3 antigos entram no resumo)" in texto
    assert "p10" in texto and "experimental, abaixo de 30" in texto


def test_condicao_em_variacao_e_digitada_em_percentual(monkeypatch):
    linhas = [_linha(m, 4.0, 0.1) for m in _meses(2010, 30)]
    linhas[5]["estado"]["bova12"] = 0.25
    monkeypatch.setattr(lf, "_painel", lambda: (linhas, "teste"))
    texto = lf.meses_com_condicao({"campo": "bova11_12m", "operador": ">", "valor": 20})
    assert "1 meses satisfazem" in texto


def test_condicao_invalida_e_nomeada():
    validas, erros = lf._ler_condicoes([{"campo": "pib", "operador": ">", "valor": 1},
                                        {"campo": "selic", "operador": "=", "valor": 1},
                                        {"campo": "selic", "operador": ">", "valor": "x"},
                                        "lixo"])
    assert validas == []
    assert len(erros) == 4 and "campo 'pib' não existe" in erros[0]


def test_nenhuma_condicao_valida_explica_o_formato(monkeypatch):
    monkeypatch.setattr(lf, "_painel", lambda: ([_linha("2020-01", 4.0, 0.1)], "t"))
    assert "Formato:" in lf.meses_com_condicao("{nao e json")


# ── executar ─────────────────────────────────────────────────────────────────

def test_executar_filtra_argumentos_e_corta_no_teto():
    ferramenta = lf.Ferramenta("eco", "d", {"type": "object",
                                            "properties": {"x": {"type": "string"}}},
                               lambda x="": x * 10_000)
    texto = lf.executar("eco", '{"x": "a", "intruso": 1}', [ferramenta])
    assert len(texto) <= lf.MAX_CHARS_RESULTADO
    assert texto.endswith("[resultado cortado no teto de tamanho]")


def test_executar_nunca_levanta():
    def _quebra(**kw):
        raise KeyError("boom")
    ferramenta = lf.Ferramenta("q", "d", {"properties": {}}, _quebra)
    assert "q: falha ao executar" in lf.executar("q", "{}", [ferramenta])
    assert "não existe" in lf.executar("nenhuma", "{}", [ferramenta])
    assert "ilegíveis" in lf.executar("q", "{x", [ferramenta])
    assert "objeto JSON" in lf.executar("q", "[1]", [ferramenta])


def test_esquemas_sao_tools_da_api_openai():
    for f in lf.FERRAMENTAS_MERCADO:
        esq = f.esquema()
        assert esq["type"] == "function"
        assert esq["function"]["parameters"]["required"]
    nomes = [f.nome for f in lf.FERRAMENTAS_MERCADO]
    assert nomes == ["serie_macro", "preco_ativo", "meses_com_condicao"]


# ── o laço em core.llm_b3 ────────────────────────────────────────────────────

def _chamada(i: int, nome: str, args: dict):
    return types.SimpleNamespace(id=f"c{i}", type="function",
                                 function=types.SimpleNamespace(name=nome,
                                                                arguments=json.dumps(args)))


def _msg(content=None, tool_calls=None):
    return types.SimpleNamespace(content=content, tool_calls=tool_calls)


class _ClienteRoteiro:
    """Devolve as mensagens do roteiro em ordem e guarda cada pedido."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.pedidos: list[dict] = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.pedidos.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        item = self.roteiro.pop(0)
        if isinstance(item, Exception):
            raise item
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=item)])


_ECO = lf.Ferramenta("eco", "devolve o argumento",
                     {"type": "object", "properties": {"x": {"type": "string"}},
                      "required": ["x"]},
                     lambda x="": f"resultado de {x}")


@pytest.fixture
def cadeia(monkeypatch):
    llm._limpar_estado_provedores()
    monkeypatch.setattr(llm, "_com_instrucao_lacunas", lambda m: m, raising=False)

    def _montar(*clientes):
        elos = [(f"p{i}", c, f"m{i}") for i, c in enumerate(clientes)]
        monkeypatch.setattr(llm, "_provider_chain", lambda *a, **k: elos)
    yield _montar
    llm._limpar_estado_provedores()


def _sistema(texto="SYS"):
    return [{"role": "system", "content": texto}, {"role": "user", "content": "pergunta"}]


def test_laco_executa_a_ferramenta_e_devolve_a_resposta_final(cadeia):
    cliente = _ClienteRoteiro([_msg(tool_calls=[_chamada(1, "eco", {"x": "ITUB4"})]),
                               _msg(content="resposta final")])
    cadeia(cliente)
    assert llm._chat_complete(_sistema(), ferramentas=[_ECO]) == "resposta final"
    primeiro, segundo = cliente.pedidos
    assert primeiro["tools"][0]["function"]["name"] == "eco"
    assert lf.REGRA_FERRAMENTAS in primeiro["messages"][0]["content"]
    tool_msg = segundo["messages"][-1]
    assert tool_msg == {"role": "tool", "tool_call_id": "c1",
                        "content": "resultado de ITUB4"}
    assert segundo["messages"][-2]["tool_calls"][0]["id"] == "c1"
    assert llm.ultimas_ferramentas() == [{"nome": "eco", "argumentos": '{"x": "ITUB4"}',
                                          "chars": len("resultado de ITUB4")}]
    assert llm.ultimo_modelo() == "p0/m0"


def test_regra_das_ferramentas_nao_vaza_para_a_mensagem_original(cadeia):
    mensagens = _sistema()
    cadeia(_ClienteRoteiro([_msg(content="ok")]))
    llm._chat_complete(mensagens, ferramentas=[_ECO])
    assert mensagens[0]["content"] == "SYS"


def test_teto_de_chamadas_por_rodada_responde_a_todas(cadeia):
    pedidas = [_chamada(i, "eco", {"x": str(i)}) for i in range(6)]
    cliente = _ClienteRoteiro([_msg(tool_calls=pedidas), _msg(content="fim")])
    cadeia(cliente)
    llm._chat_complete(_sistema(), ferramentas=[_ECO])
    respostas = [m for m in cliente.pedidos[1]["messages"] if m.get("role") == "tool"]
    # O provedor exige uma resposta por id: as excedentes recebem a recusa.
    assert [m["tool_call_id"] for m in respostas] == [f"c{i}" for i in range(6)]
    assert respostas[-1]["content"].startswith("Não executada: teto de 4")
    assert len(llm.ultimas_ferramentas()) == llm.MAX_CHAMADAS_POR_RODADA


def test_ultima_rodada_proibe_ferramenta(cadeia):
    insistente = [_msg(tool_calls=[_chamada(i, "eco", {"x": "y"})])
                  for i in range(llm.MAX_RODADAS_FERRAMENTA)]
    cliente = _ClienteRoteiro(insistente + [_msg(content="cansei")])
    cadeia(cliente)
    assert llm._chat_complete(_sistema(), ferramentas=[_ECO]) == "cansei"
    assert "tool_choice" not in cliente.pedidos[0]
    assert cliente.pedidos[-1]["tool_choice"] == "none"
    assert len(cliente.pedidos) == llm.MAX_RODADAS_FERRAMENTA + 1


class _Erro(Exception):
    def __init__(self, status):
        super().__init__(f"erro {status}")
        self.status_code = status


def test_tools_recusadas_caem_para_chamada_sem_ferramentas(cadeia):
    cliente = _ClienteRoteiro([_Erro(400), _msg(content="sem tools"),
                               _msg(content="de novo sem tools")])
    cadeia(cliente)
    assert llm._chat_complete(_sistema(), ferramentas=[_ECO]) == "sem tools"
    assert "tools" not in cliente.pedidos[1]
    assert lf.REGRA_FERRAMENTAS not in cliente.pedidos[1]["messages"][0]["content"]
    assert llm.ultimas_ferramentas() == []
    # O modelo que recusou não recebe tools de novo nesta sessão.
    llm._chat_complete(_sistema(), ferramentas=[_ECO])
    assert "tools" not in cliente.pedidos[2]


def test_resposta_vazia_nao_desliga_as_tools_do_modelo(cadeia):
    cliente = _ClienteRoteiro([_msg(content=""), _msg(content="sem tools"),
                               _msg(content="com tools")])
    cadeia(cliente)
    assert llm._chat_complete(_sistema(), ferramentas=[_ECO]) == "sem tools"
    llm._chat_complete(_sistema(), ferramentas=[_ECO])
    assert "tools" in cliente.pedidos[2]


def test_429_nas_tools_passa_para_o_proximo_provedor(cadeia):
    primeiro = _ClienteRoteiro([_Erro(429)])
    segundo = _ClienteRoteiro([_msg(content="do segundo")])
    cadeia(primeiro, segundo)
    assert llm._chat_complete(_sistema(), ferramentas=[_ECO]) == "do segundo"
    assert len(primeiro.pedidos) == 1
    assert "tools" in segundo.pedidos[0]
    assert llm.ultimo_modelo() == "p1/m1"


def test_sem_ferramentas_e_em_json_o_caminho_nao_muda(cadeia):
    cliente = _ClienteRoteiro([_msg(content="texto"), _msg(content='{"a": 1}')])
    cadeia(cliente)
    llm._chat_complete(_sistema())
    llm._chat_complete(_sistema(), json_mode=True, ferramentas=[_ECO])
    assert all("tools" not in p for p in cliente.pedidos)
    assert lf.REGRA_FERRAMENTAS not in cliente.pedidos[0]["messages"][0]["content"]


def test_mensagem_do_provedor_volta_com_o_que_ele_mandou():
    class _Pydantic:
        def model_dump(self, exclude_none=False):
            return {"role": "assistant", "tool_calls": [
                {"id": "c1", "extra_content": {"google": {"thought_signature": "sig"}}}]}
    volta = llm._mensagem_assistente(_Pydantic())
    assert volta["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig"
