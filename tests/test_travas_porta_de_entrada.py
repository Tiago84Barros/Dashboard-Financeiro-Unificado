"""As travas de circuito ganham porta de entrada -- e as duas que faltavam.

Antes disto o motor das seis travas avaliava e ninguém o consultava:
``travas.do_painel`` não tinha um único chamador em ``views/``, ``core/`` ou
``data_pipeline/``. Motor de diagnóstico que a decisão não consulta é decoração
(``memoria: diagnostico-precisa-porta-de-entrada``), e era exatamente o defeito
que o próprio docstring do módulo citava.

Duas das seis também não tinham de onde tirar sinal:
``modelo_fora_dos_limites`` não era derivada em lugar nenhum, e
``auditoria_falhou`` tinha parâmetro que ninguém passava. Aqui elas passam a
ter fonte -- e os testes cobrem tanto o disparo quanto o silêncio honesto.
"""
from __future__ import annotations

import datetime as dt
import inspect
import math

from core.auditoria import trilha as T
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz
from core.seguranca import travas as TR
from design import inteligencia as ui
from views import inteligencia_mercado as V

AGORA = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)


class ParteFalsa:
    def __init__(self, chave, nota):
        self.chave, self.nota = chave, nota


class IndiceFalso:
    def __init__(self, valor=0.6, bruto=0.6, cobertura=0.8, partes=()):
        self.valor, self.bruto = valor, bruto
        self.cobertura, self.partes = cobertura, partes


class NivelFalso:
    def __init__(self, codigo):
        self.codigo = codigo


class VereditoFalso:
    def __init__(self, severidade=0.4, confianca=0.7, codigo=2):
        self.severidade, self.confianca = severidade, confianca
        self.nivel = NivelFalso(codigo)


# -- modelo_fora_dos_limites -------------------------------------------------
def test_saida_dentro_do_dominio_nao_dispara():
    fora, detalhe = TR.fora_dos_limites(indice=IndiceFalso(),
                                        veredito=VereditoFalso())
    assert fora is False and detalhe == ""


def test_sem_saida_de_modelo_a_trava_fica_nao_verificada():
    """``None``, nunca ``False``: não houve o que conferir."""
    fora, detalhe = TR.fora_dos_limites()
    assert fora is None and "nenhuma saída" in detalhe


def test_indice_acima_de_um_e_saida_corrompida():
    fora, detalhe = TR.fora_dos_limites(indice=IndiceFalso(valor=1.4))
    assert fora is True and "índice.valor" in detalhe


def test_nan_no_indice_nao_passa_pela_comparacao_ingenua():
    """NaN aprova em ``0 <= x <= 1``; é a saída mais corrompida de todas."""
    assert (0.0 <= math.nan <= 1.0) is False  # a armadilha, explícita
    assert not (0.0 <= math.nan <= 1.0)
    fora, _ = TR.fora_dos_limites(indice=IndiceFalso(valor=math.nan))
    assert fora is True


def test_infinito_e_texto_tambem_sao_fora_dos_limites():
    assert TR.fora_dos_limites(indice=IndiceFalso(valor=math.inf))[0] is True
    assert TR.fora_dos_limites(indice=IndiceFalso(valor="alto"))[0] is True


def test_componente_fora_da_faixa_e_nomeado():
    idx = IndiceFalso(partes=(ParteFalsa("liquidez", 0.5),
                              ParteFalsa("concentracao", -0.2)))
    fora, detalhe = TR.fora_dos_limites(indice=idx)
    assert fora is True
    assert "concentracao" in detalhe and "liquidez" not in detalhe


def test_componente_nao_medido_nao_e_fora_dos_limites():
    """``None`` é ausência de medição, e ausência não é valor inválido."""
    idx = IndiceFalso(valor=None, bruto=None,
                      partes=(ParteFalsa("liquidez", None),))
    assert TR.fora_dos_limites(indice=idx)[0] is False


def test_severidade_e_nivel_do_veredito_tambem_sao_conferidos():
    fora, detalhe = TR.fora_dos_limites(veredito=VereditoFalso(severidade=3.0))
    assert fora is True and "severidade" in detalhe
    fora, detalhe = TR.fora_dos_limites(veredito=VereditoFalso(codigo=9))
    assert fora is True and "nível" in detalhe


def test_a_trava_disparada_desliga_a_saida_do_modelo():
    est = TR.do_painel(P.montar(agora=AGORA), indice=IndiceFalso(valor=1.4))
    assert est.de(TR.MODELO_FORA_DOS_LIMITES).disparada is True
    assert not est.permite(TR.SAIDA_DO_MODELO)


# -- auditoria_falhou: sonda de leitura x gravação observada ------------------
class EngineQuebrada:
    def connect(self):
        raise RuntimeError("relation does not exist")


class EngineViva:
    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **kw):
        return None


def test_sonda_que_nao_le_declara_falha():
    ok, motivo = T.sonda(engine=EngineQuebrada())
    assert ok is True and "does not exist" in motivo


def test_sonda_que_le_nao_declara_gravacao_boa():
    """Ler não prova gravar -- ``None``, jamais ``False``.

    ``memoria: quem-pergunta-menos-tira-nota-maior``: uma pergunta barata
    (existe a tabela?) não pode responder pela cara (o INSERT funciona?). A
    tabela pode existir e a gravação falhar por permissão, por coluna nova ou
    por disco cheio. Só ``registrar`` observa gravação, e só ele diz ``False``.
    """
    ok, motivo = T.sonda(engine=EngineViva())
    assert ok is None
    assert "gravação" in motivo


def test_sonda_alimenta_a_trava_de_auditoria():
    est = TR.do_painel(P.montar(agora=AGORA),
                       auditoria=T.sonda(engine=EngineQuebrada()))
    assert est.de(TR.AUDITORIA_FALHOU).disparada is True
    assert not est.permite(TR.MUDANCA_ESTRATEGICA)
    assert "does not exist" in est.de(TR.AUDITORIA_FALHOU).detalhe


def test_gravacao_observada_manda_sobre_a_sonda():
    """A sonda diz "responde à leitura"; quem gravou diz que não gravou."""
    est = TR.do_painel(P.montar(agora=AGORA),
                       auditoria=T.sonda(engine=EngineViva()),
                       auditoria_ok=False)
    assert est.de(TR.AUDITORIA_FALHOU).disparada is True
    assert not est.permite(TR.MUDANCA_ESTRATEGICA)


def test_sonda_boa_sozinha_deixa_a_trava_nao_verificada():
    est = TR.do_painel(P.montar(agora=AGORA),
                       auditoria=T.sonda(engine=EngineViva()))
    assert est.de(TR.AUDITORIA_FALHOU).disparada is None
    assert est.permite(TR.MUDANCA_ESTRATEGICA)  # não medido não bloqueia


# -- A porta de entrada ------------------------------------------------------
def test_a_tela_avalia_as_travas_e_as_renderiza():
    corpo = inspect.getsource(V.render)
    assert "avaliar_travas(" in corpo and "barra_travas(" in corpo


def test_a_montagem_entrega_o_indice_cru_para_a_conferencia():
    """O Bloco guarda o número já arredondado; conferi-lo seria conferir a
    formatação, não o modelo. A trava precisa da saída do motor."""
    corpo = inspect.getsource(V.montar_tudo)
    assert "return Montagem(" in corpo and "indice=indice" in corpo
    assert "indice=montagem.indice" in inspect.getsource(V.avaliar_travas)


def test_montar_painel_continua_devolvendo_o_painel():
    corpo = inspect.getsource(V.montar_painel)
    assert "montar_tudo(agora=agora).painel" in corpo


def test_a_sonda_da_tela_e_cacheada_para_nao_bater_no_banco_a_cada_clique():
    fonte = inspect.getsource(V)
    assert ("@st.cache_data(ttl=300, show_spinner=False)\n"
            "def sonda_auditoria" in fonte)


def test_falha_da_sonda_nao_derruba_a_tela(monkeypatch):
    def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(V, "sonda_auditoria", explode)
    montagem = V.Montagem(painel=P.montar(agora=AGORA))
    assert V.avaliar_travas(montagem) is None


# -- Acessibilidade: nunca só a cor ------------------------------------------
def test_as_tres_situacoes_tem_simbolo_e_palavra_alem_de_cor():
    simbolos = {ap[0] for ap in ui.APARENCIA_TRAVA.values()}
    palavras = {ap[2] for ap in ui.APARENCIA_TRAVA.values()}
    assert len(ui.APARENCIA_TRAVA) == 3
    assert len(simbolos) == 3 and len(palavras) == 3


def test_toda_trava_tem_rotulo_legivel():
    assert set(ui.ROTULO_TRAVA) == set(TR.EFEITO)


def test_o_card_sai_num_markdown_so():
    """Div aberta num bloco e fechada em outro vira moldura vazia."""
    corpo = inspect.getsource(ui.barra_travas)
    assert corpo.count("st.markdown(") == 1
    assert "app-kpi-card" in corpo and "</div>" in corpo


def test_nao_verificada_nao_e_publicada_como_em_ordem():
    corpo = inspect.getsource(ui.barra_travas)
    assert "tudo bem" in corpo


# -- Integração rasa: painel real, travas reais ------------------------------
def test_painel_com_dado_vencido_bloqueia_recomendacao_emergencial():
    pn = P.montar(
        frescor=[qz.Frescor("Notícias",
                            atualizado_em=AGORA - dt.timedelta(hours=99),
                            validade_horas=6.0)], agora=AGORA)
    est = TR.do_painel(pn)
    assert est.de(TR.DADOS_VENCIDOS).disparada is True
    assert not est.permite(TR.RECOMENDACAO_EMERGENCIAL)


def test_as_seis_aparecem_sempre_mesmo_sem_sinal_nenhum():
    est = TR.do_painel(P.montar(agora=AGORA))
    assert len(est.travas) == 6
    assert {t.nome for t in est.travas} == set(TR.EFEITO)
