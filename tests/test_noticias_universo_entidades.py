"""O universo de entidades, e a porta de entrada que não existia.

O defeito
---------
``core.noticias.entidades`` sabe casar nome de empresa com ticker, expandir para
todas as classes e herdar setor e país. Tudo isso depende de um ``Universo``
carregado — e o job de coleta chamava ``coletar(consulta, provedores,
registro=registro)``, sem ``universo=``. O padrão do parâmetro é
``UNIVERSO_VAZIO``, então o motor rodava inteiro no ramo degradado: aceitava o
ticker que o provedor declarasse e nada mais.

Medido na coleta de 04/09/2026: 43 notícias, 2 ativos resolvidos (AAPL e MSFT,
declarados por provedor americano), zero tickers brasileiros. Com o piso de três
itens por ativo, isso é vitrine com zero ativos medidos — o componente de
notícias no denominador sem nunca mover peso
(``memoria: diagnostico-precisa-porta-de-entrada``).

O que este arquivo cobra
------------------------
1. **O job passa o universo.** Um teste que executa ``_executar`` com dublês e
   confere o argumento que chegou a ``coletar``. É o teste que teria falhado no
   dia em que a chamada foi escrita sem ``universo=``.
2. **Nome de empresa vira ticker**, e multi-classe expande.
3. **Notícia macro não vira notícia de empresa** — o risco que o universo
   introduz é o falso positivo, e ele é cobrado de frente.
4. **Fonte que falha vira limitação escrita**, não universo silenciosamente
   menor.
"""
from __future__ import annotations

import re

import pytest

from core.noticias import entidades as ent
from core.noticias import universo_entidades as ue
from core.noticias.entidades import Universo, resolver
from core.noticias.normalizacao import normalizar_texto


@pytest.fixture(autouse=True)
def _sem_cache():
    ue.limpar_cache()
    yield
    ue.limpar_cache()


# ─────────────────────────── dublês do cadastro ──────────────────────────────

def _rotulo(sql) -> str:
    texto = str(sql)
    if "market.assets" in texto:
        return "B3"
    if "market_us" in texto:
        return "EUA"
    if "market.fiis" in texto:
        return "FIIs"
    if "ticker_alias" in texto:
        return "ALIAS"
    return "?"


class _Result:
    """Dicionários por ``.mappings()``, tuplas na iteração crua.

    É a distinção que os dois caminhos do módulo usam: os blocos por fonte leem
    ``linha["ticker"]``; o remapeamento de apelidos lê ``r[0]``/``r[1]``.
    """

    def __init__(self, linhas):
        self._linhas = list(linhas)

    def mappings(self):
        return iter(self._linhas)

    def __iter__(self):
        return iter(tuple(d.values()) if isinstance(d, dict) else d
                    for d in self._linhas)


class _Conn:
    def __init__(self, linhas_por_sql, falhar_em=()):
        self._linhas = linhas_por_sql
        self._falhar = falhar_em

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *args, **kw):
        chave = _rotulo(sql)
        if chave in self._falhar:
            raise RuntimeError(f'relation "{chave.lower()}" does not exist')
        return _Result(self._linhas.get(chave, []))


class _Engine:
    def __init__(self, linhas, falhar_em=()):
        self._linhas = linhas
        self._falhar = falhar_em

    def connect(self):
        return _Conn(self._linhas, self._falhar)


LINHAS = {
    "B3": [
        {"ticker": "PETR3", "nome": "Petroleo Brasileiro SA Pfd",
         "setor": "Petróleo"},
        {"ticker": "PETR4", "nome": "Petroleo Brasileiro SA Pfd",
         "setor": "Petróleo"},
        {"ticker": "VALE3", "nome": "Vale S.A.", "setor": "Mineração"},
        {"ticker": "BBAS3", "nome": "Banco do Brasil SA",
         "setor": "Serviços Financeiros"},
        {"ticker": "AXIA3", "nome": "AXIA Energia SA Non-Cum Perp Pfd Shs",
         "setor": "Energia"},
    ],
    "EUA": [{"ticker": "AAPL", "nome": "Apple Inc.", "setor": "Technology"}],
    "FIIs": [{"ticker": "HGLG11", "nome": "CSHG Logistica FII",
              "setor": "Logística"}],
    "ALIAS": [("AXIA3", "ELET3")],
}


def _universo(falhar_em=()) -> tuple[Universo, tuple[str, ...]]:
    return ue.carregar(engine=_Engine(LINHAS, falhar_em), usar_cache=False)


# ────────────────────────── montagem do universo ─────────────────────────────

def test_nome_de_empresa_vira_ticker():
    """O caso que não acontecia: manchete sem ticker declarado resolve."""
    u, _ = _universo()

    assert resolver("Vale reduz producao de minerio", universo=u).tickers == \
        ("VALE3",)


def test_apelido_de_marca_encontra_todas_as_classes():
    """"Petrobras" não é derivável de "Petroleo Brasileiro" por regra nenhuma.

    É conhecimento de mundo, mora em ``APELIDOS`` declarado à mão, e vale para
    as duas classes — notícia da empresa afeta ON e PN.
    """
    u, _ = _universo()

    assert resolver("Petrobras aprova dividendos", universo=u).tickers == \
        ("PETR3", "PETR4")


def test_o_nome_oficial_continua_valendo_ao_lado_do_apelido():
    """Apelido soma, não substitui: as duas grafias têm de funcionar."""
    u, _ = _universo()

    assert resolver("Petroleo Brasileiro divulga producao",
                    universo=u).tickers == ("PETR3", "PETR4")


def test_ticker_alias_manda_a_noticia_para_o_simbolo_que_o_app_le():
    """AXIA3 é ELET3. Sem o remapeamento, a vitrine ganharia linha órfã.

    ``market.ticker_alias`` já existe para o mesmo problema na ingestão de
    preços (``memoria: ingestao-brapi-tickers-divergentes``); aqui ela é lida em
    vez de reimplementada.
    """
    u, _ = _universo()

    assert "ELET3" in u.tickers
    assert "AXIA3" not in u.tickers


def test_setor_e_pais_vem_do_cadastro_e_nao_do_provedor():
    """Sem universo, setor só existia se a API o declarasse."""
    u, _ = _universo()
    resolvido = resolver("Apple lanca produto novo", universo=u)

    assert resolvido.setores == ("Technology",)
    assert "US" in resolvido.paises


def test_noticia_macro_nao_vira_noticia_de_empresa():
    """O risco que o universo introduz, cobrado de frente.

    Um mapa de nomes amplo pode atribuir matéria macro a uma empresa qualquer.
    O cadastro tem "Banco do Brasil", e esta manchete tem as três palavras — mas
    não a expressão. O que protege é a fronteira de palavra da regex, não a
    sorte, e por isso o caso está escrito.
    """
    u, _ = _universo()

    assert resolver("Copom mantem Selic e o banco central ve inflacao no "
                    "Brasil", universo=u).tickers == ()


def test_nome_generico_nao_entra_no_mapa_de_nomes():
    """"Energia" sozinha casaria com metade do noticiário do setor elétrico.

    O filtro age só sobre ``por_nome``: o ticker segue no universo, porque
    reconhecer um símbolo que o provedor declarou não tem risco nenhum.
    """
    u, _ = _universo()

    assert "energia" not in u.por_nome
    assert "banco" not in u.por_nome
    assert "ELET3" in u.tickers


def test_fonte_que_falha_vira_limitacao_escrita():
    """Universo menor em silêncio apresentaria ignorância como ausência.

    "não achamos ticker nesta notícia" e "não sabíamos procurar por ele" pedem
    providências opostas, e só a limitação escrita distingue as duas.
    """
    u, limitacoes = _universo(falhar_em=("EUA",))

    assert "AAPL" not in u.tickers
    assert any("EUA" in texto for texto in limitacoes), limitacoes
    assert "VALE3" in u.tickers   # as outras fontes seguiram


def test_universo_vazio_se_declara(monkeypatch):
    """Sem banco não há universo — e isso precisa chegar escrito ao ciclo.

    Universo vazio é estado legítimo (primeira execução, banco fora do ar). O
    que não é legítimo é ficar indistinguível de "o noticiário não falou de
    nenhuma empresa nossa".
    """
    import core.database as db

    monkeypatch.setattr(db, "get_engine", lambda: None)
    u, limitacoes = ue.carregar(engine=None, usar_cache=False)

    assert u.vazio
    assert limitacoes, "universo vazio sem limitação é ignorância muda"


def test_poda_de_sufixo_nao_inventa_apelido():
    """"Petroleo Brasileiro SA Pfd" vira "Petroleo Brasileiro", nunca outra coisa.

    A poda tira forma jurídica e classe. Marca é outra categoria e passa pela
    lista explícita — uma heurística que "adivinhasse" a marca erraria calada.
    """
    assert ue._limpar_nome("Itau Unibanco Holding SA Pfd") == "Itau Unibanco"
    assert ue._limpar_nome("Petroleo Brasileiro SA Pfd") == "Petroleo Brasileiro"
    assert ue._limpar_nome("Vale S.A.") == "Vale"
    assert ue._limpar_nome("AXIA Energia SA Non-Cum Perp Pfd Shs") == \
        "AXIA Energia"


def test_barra_do_edgar_nao_congela_a_poda():
    """``TJX COMPANIES INC /DE/`` tem de virar "TJX COMPANIES", não a string inteira.

    A poda anda do fim para o começo e para no primeiro token que não
    reconhece. O cadastro da SEC termina o nome com o estado de constituição
    entre barras, e a barra não é removida por ``strip(" ,.-")`` nem casada por
    ``endswith(" inc")`` — então ``INC`` e ``CORP``, um token atrás, nunca eram
    alcançados e a chave gravada não aparecia em manchete nenhuma.

    Os três formatos observados no cadastro estão aqui de propósito: com espaço
    antes da barra, sem espaço nenhum, e a barra órfã sem estado — esta última
    é a que uma lista de UFs deixaria passar.
    """
    assert ue._limpar_nome("TJX COMPANIES INC /DE/") == "TJX COMPANIES"
    assert ue._limpar_nome("PROGRESSIVE CORP/OH/") == "PROGRESSIVE"
    assert ue._limpar_nome("FEDERAL SIGNAL CORP /DE/") == "FEDERAL SIGNAL"
    assert ue._limpar_nome("AMETEK INC/") == "AMETEK"


def test_virgula_antes_da_forma_juridica_nao_impede_a_poda():
    """``AGILENT TECHNOLOGIES, INC.`` e ``Leidos Holdings, Inc.`` também podam.

    Mesma família do defeito da barra: era a pontuação, não o sufixo. ``COPART
    INC`` podava e ``AMETEK INC/`` não, com um caractere de diferença.
    """
    assert ue._limpar_nome("AGILENT TECHNOLOGIES, INC.") == "AGILENT TECHNOLOGIES"
    assert ue._limpar_nome("Leidos Holdings, Inc.") == "Leidos"
    assert ue._limpar_nome("BRINKER INTERNATIONAL, INC") == "BRINKER INTERNATIONAL"


def test_a_poda_pela_barra_nao_alcanca_nome_brasileiro():
    """Nenhum nome sem a barra do EDGAR pode se mover.

    O corte é ancorado na barra justamente para isso: "de" é preposição
    corrente em português e um corte cego de duas letras no fim comeria nome
    real. Estes quatro vêm do cadastro da B3 e têm de sair inalterados.
    """
    for nome in (
        "M DIAS BRANCO SA INDUSTRIA E COMERCIO DE ALIMENTOS",
        "BANCO DO ESTADO DE SERGIPE SA BANESE",
        "CIA SANEAMENTO BASICO DE SAO PAULO",
        "DTCOM DIRECT TO CO",
    ):
        assert ue._limpar_nome(nome) == nome


def test_apelido_nao_inventa_ticker():
    """``APELIDOS`` é chave a mais em ``por_nome``, nunca ativo a mais.

    Se o apelido entrasse em ``tickers``, ``conhece()`` passaria a aprovar
    símbolo inexistente — e o provedor que declarasse esse símbolo veria a
    notícia aceita.
    """
    u, _ = _universo()

    assert "petrobras" in u.por_nome
    assert "PETROBRAS" not in u.tickers
    # O universo é exatamente o cadastro lido (com AXIA3 já remapeado). Nem um
    # símbolo a mais: apelido é forma de escrever, não ativo novo.
    assert set(u.tickers) == {"PETR3", "PETR4", "VALE3", "BBAS3", "ELET3",
                              "AAPL", "HGLG11"}


def test_apelido_de_ticker_ausente_do_cadastro_nao_entra():
    """Entrada que envelheceu some sozinha em vez de virar nome órfão."""
    u, _ = _universo()

    # SBSP3 está em APELIDOS mas não neste cadastro de teste.
    assert "SBSP3" in ue.APELIDOS
    assert "sabesp" not in u.por_nome


def test_indice_por_primeiro_termo_da_o_mesmo_que_a_forca_bruta():
    """O índice é otimização; se mudasse o resultado, seria outro algoritmo.

    Ele existe por custo medido: sem ele, casar nome era uma ``re.search`` por
    nome do cadastro **por notícia** — 360 ms por item com ~3 mil nomes.
    Otimização que altera resultado é defeito, e a única forma de saber é
    comparar com quem varre tudo; o oráculo abaixo é a versão anterior do laço.

    Comparação por conjunto: a ordem passou a seguir os termos do texto em vez
    da ordem de inserção do cadastro. Segue determinística para um mesmo texto,
    que é o que o resto do motor exige (``memoria: determinismo-carteira-b3``).
    """
    u, _ = _universo()
    texto = "Vale e Petrobras divulgam resultados; Apple tambem"
    normalizado = normalizar_texto(texto)

    bruta = {t for nome, tickers in u.por_nome.items()
             if len(nome) >= 4 and re.search(
                 rf"(?<![a-z0-9]){re.escape(nome)}(?![a-z0-9])", normalizado)
             for t in tickers}

    assert bruta, "o texto tinha de casar com alguma coisa"
    assert set(ent.resolver_tickers((), texto, u)) == bruta


# ───────────────────────── a porta de entrada, no job ────────────────────────

class _Parar(Exception):
    """Corta ``_executar`` no ponto exato de interesse."""


class _ConsultaFalsa:
    def __init__(self, **kw):
        self.kw = kw


class _OrcamentoFalso:
    def __init__(self, **kw):
        pass


class _CicloFalso:
    modo = "normal"
    iniciado_em = None
    status = ""


class _SettingsFalso:
    noticias_limite = 10
    noticias_cache_ttl_s = 60
    noticias_max_retentativas = 0
    noticias_backoff_s = 0
    noticias_retencao_dias = 30


class _CadFalso:
    STATUS_INDISPONIVEL = "indisponivel"


class _EcFalso:
    class ConsumoBanco:
        def __init__(self, engine):
            pass

        def disponivel(self):
            return False


def _rodar_ate_a_coleta(job, ent_uni, coletar, perfil_mod=None, bases_mod=None):
    """Harness compartilhado: ``tests/test_noticias_perfil_carteira.py`` o
    importa para o teste espelhado do perfil. ``perfil_mod`` é opcional aqui
    porque este arquivo mede o universo; quem mede o perfil injeta o seu."""
    from core.noticias import portoes as _portoes

    if perfil_mod is None:
        class perfil_mod:  # noqa: N801 - dublê mínimo
            @staticmethod
            def carregar():
                return _portoes.PERFIL_VAZIO, ()

    if bases_mod is None:
        class bases_mod:  # noqa: N801 - duble minimo
            @staticmethod
            def carregar():
                return {}, ()

    class _Uni:
        LIMITE_TICKERS = 20

        @staticmethod
        def montar(modo, *, engine=None):
            return ("PETR4",), ()

    return job._executar(
        job._resultado_base(), _CicloFalso(), object(), (), engine=None,
        settings=_SettingsFalso(), cad=_CadFalso(), ec=_EcFalso(),
        uni=_Uni, ent_uni=ent_uni, perfil_mod=perfil_mod, bases_mod=bases_mod,
        gravar=lambda r: {"gravado": True},
        Cache=lambda **kw: None, coletar=coletar,
        RegistroColeta=lambda **kw: None, Consulta=_ConsultaFalsa,
        construir=lambda **kw: [object()], Orcamento=_OrcamentoFalso,
        da_coleta=None, trilha=None)


def test_o_job_entrega_o_universo_a_coleta():
    """O teste que teria pego o defeito no dia em que ele foi escrito.

    Não afirma que a resolução funciona — os testes acima fazem isso. Afirma que
    o job **chama** o motor com o universo. Motor correto que ninguém alimenta é
    decoração, e decoração não deixa erro no log.
    """
    from data_pipeline.jobs import update_noticias as job

    recebido: dict = {}
    u, _ = _universo()

    def _coletar(consulta, provedores, **kw):
        recebido.update(kw)
        raise _Parar()

    class _EntUni:
        @staticmethod
        def carregar(*, engine=None):
            return u, ()

    with pytest.raises(_Parar):
        _rodar_ate_a_coleta(job, _EntUni, _coletar)

    assert "universo" in recebido, (
        "coletar foi chamado sem universo=: o resolvedor roda no ramo degradado")
    assert recebido["universo"] is u
    assert not recebido["universo"].vazio


# ───────────────── falsos positivos que só a coleta real mostrou ─────────────
#
# Os três testes acima passavam antes destes existirem. O que os produziu não
# foi leitura de código: foi rodar o coletor com o universo já ligado e olhar o
# acervo. Dos 48 itens, 30 tinham sido atribuídos a POST e 3 a VALE3 — e VALE3
# com 3 itens é exatamente o piso de exibição da vitrine
# (``memoria: revisao-por-execucao``).

def test_rodape_de_plugin_nao_vira_noticia_da_post_holdings():
    """"The post … appeared first on …" é assinatura de CMS, não conteúdo.

    Com ele no resumo, o cadastro americano casava "post" com POST (Post
    Holdings) em qualquer matéria de qualquer veículo WordPress. Foram 30 dos 48
    itens do acervo — uma empresa que dominaria a vitrine sem que uma linha de
    notícia falasse dela.
    """
    from core.noticias.normalizacao import sem_rodape_de_feed

    assert sem_rodape_de_feed(
        "Analise do setor. The post Quando o fee based vale a pena "
        "appeared first on Braz Journal.") == "Analise do setor."
    # Texto sem rodapé passa intacto: o corte é do rabo, não do conteúdo.
    assert sem_rodape_de_feed("Vale reduz producao") == "Vale reduz producao"


def test_nome_de_um_termo_so_exige_evidencia_de_nome_proprio():
    """Os três falsos positivos de VALE3, um a um.

    "Vale" é empresa e é verbo. Barrar a palavra custaria toda notícia real da
    Vale; aceitar sem evidência dava três atribuições falsas em 48 itens. A
    saída é exigir do texto o que distingue as duas: maiúscula inicial e a
    palavra solta.
    """
    u, _ = _universo()

    assert resolver("Quando o fee based vale a pena", universo=u).tickers == ()
    assert resolver("Naval Ravikant, o guru do vale do silicio",
                    universo=u).tickers == ()
    assert resolver("Vale-refeicao entra em nova disputa",
                    universo=u).tickers == ()
    # E o caso verdadeiro continua passando — é a metade que importa.
    assert resolver("Vale reduz producao de minerio",
                    universo=u).tickers == ("VALE3",)


def test_a_trava_de_caixa_nao_alcanca_nome_de_dois_termos():
    """Só o nome de um termo precisa da evidência; exigir de todos seria caro.

    "banco do brasil" em minúsculas no meio de um resumo continua valendo: a
    chance de a expressão inteira aparecer por acaso é de outra ordem, e exigir
    caixa alta perderia toda citação em texto corrido.
    """
    u, _ = _universo()

    assert resolver("segundo apuracao, o banco do brasil elevou a provisao",
                    universo=u).tickers == ("BBAS3",)


def test_ticker_declarado_pelo_provedor_nao_passa_pela_trava_de_caixa():
    """A trava é do casamento por nome. Declarado é outra origem, e continua.

    Confundir as duas desligaria a via de maior confiança para consertar a de
    menor.
    """
    u, _ = _universo()

    assert resolver("acoes sobem", tickers_declarados=("VALE3",),
                    universo=u).tickers == ("VALE3",)
