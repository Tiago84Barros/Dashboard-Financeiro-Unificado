"""Travas, limites de uso e trilha de auditoria.

Continuação dos dez testes de :mod:`tests.test_seguranca`. Aqui o alvo são as
três peças que agem *depois* de o conteúdo externo ter sido contido: o que o
sistema desliga sozinho, quanto ele pode gastar, e o que fica escrito.

Nenhum teste toca banco. ``registrar`` é exercitado contra uma engine falsa que
falha -- o caso que importa não é o INSERT feliz (isso o Postgres garante), é o
sistema fazer a coisa certa quando a trilha não pode ser gravada.
"""
from __future__ import annotations

import datetime as dt

import pytest

from core.auditoria import confirmacao as C
from core.auditoria import trilha as T
from core.seguranca import limites as LI
from core.seguranca import travas as TR

AGORA = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)


# ── Travas ───────────────────────────────────────────────────────────────────
def test_trava_nao_dispara_por_falta_de_informacao():
    """``ok=None`` é 'não medido', nunca ``False`` -- lei do projeto.

    Uma trava que disparasse no escuro daria a mesma resposta para "está tudo
    bem" e "não olhei", e a segunda é a que precisa de alguém vendo.
    """
    e = TR.avaliar()
    assert e.disparadas == ()
    assert len(e.nao_verificadas) == 6
    assert e.permite(TR.RECOMENDACAO_EMERGENCIAL)
    assert e.permite(TR.MUDANCA_ESTRATEGICA)
    # ...mas a ignorância fica publicada, e não escondida atrás do "permite".
    assert set(e.resumo_auditoria()["nao_verificadas"]) == set(TR.EFEITO)


def test_cada_trava_desliga_so_o_que_lhe_cabe():
    """Seis gatilhos, seis efeitos diferentes.

    Tratar todas como "desliga tudo" seria tão errado quanto não ter nenhuma:
    um preço que não carregou não pode calar a explicação do painel inteiro.
    """
    e = TR.avaliar(preco_indisponivel=True, dados_vencidos=False,
                   auditoria_falhou=False)
    assert not e.permite(TR.IMPACTO_ATUAL)
    assert e.permite(TR.RECOMENDACAO_EMERGENCIAL)
    assert e.permite(TR.MUDANCA_ESTRATEGICA)
    assert e.motivos(TR.IMPACTO_ATUAL)
    assert "não foi calculado" in e.motivos(TR.IMPACTO_ATUAL)[0]


def test_divergencia_entre_provedores_rebaixa_confianca_e_nao_bloqueia():
    """Incerteza com tamanho vira banda, não portão.

    ``memoria: incerteza-com-tamanho-nao-bloqueia``: transformar divergência em
    bloqueio esconderia o evento em vez de qualificá-lo.
    """
    e = TR.avaliar(provedores_divergem=True)
    assert e.confianca_rebaixada
    assert e.bloqueios == ()
    assert e.permite(TR.RECOMENDACAO_EMERGENCIAL)


def test_auditoria_falha_bloqueia_mudanca_estrategica():
    e = TR.avaliar(auditoria_falhou=True)
    assert not e.permite(TR.MUDANCA_ESTRATEGICA)
    assert "sem registro" in e.motivos(TR.MUDANCA_ESTRATEGICA)[0]


def test_dados_vencidos_bloqueiam_recomendacao_emergencial():
    e = TR.avaliar(dados_vencidos=True, detalhes={TR.DADOS_VENCIDOS: "72h"})
    assert not e.permite(TR.RECOMENDACAO_EMERGENCIAL)
    assert "(72h)" in e.motivos(TR.RECOMENDACAO_EMERGENCIAL)[0]


def test_todas_as_seis_travas_podem_disparar_e_todas_podem_nao_disparar():
    """Critério que só pode dar um valor não é proteção.

    ``memoria: gate-que-so-dava-false``. O teste é bobo de escrever e é o único
    que pega a trava que ficou pendurada num sinal que nunca chega.
    """
    ligadas = TR.avaliar(**{n: True for n in
                            ("dados_vencidos", "provedores_divergem",
                             "preco_indisponivel", "modelo_fora_dos_limites",
                             "llm_inventou_numero", "auditoria_falhou")})
    assert len(ligadas.disparadas) == 6
    # Cinco bloqueiam; divergência de provedores rebaixa confiança.
    assert len(ligadas.bloqueios) == 5
    desligadas = TR.avaliar(**{n: False for n in
                               ("dados_vencidos", "provedores_divergem",
                                "preco_indisponivel", "modelo_fora_dos_limites",
                                "llm_inventou_numero", "auditoria_falhou")})
    assert desligadas.disparadas == () and desligadas.bloqueios == ()


# ── Limites de uso ───────────────────────────────────────────────────────────
def test_limite_e_janela_deslizante_e_nao_balde_de_hora_cheia():
    """Contador que zera na virada deixa passar o dobro na fronteira.

    Gasta tudo às 10h59 e tudo de novo às 11h00. O erro é o de
    ``memoria: cadencia-em-horas-pula-dia`` visto do outro lado: alinhar a
    régua ao relógio de parede em vez de ao intervalo que se quer proteger.
    """
    c = LI.Contador({"x": LI.Regra("x", maximo=2, janela_s=3600)})
    assert c.permitir("x", agora=AGORA).permitido
    assert c.permitir("x", agora=AGORA + dt.timedelta(minutes=59)).permitido
    v = c.permitir("x", agora=AGORA + dt.timedelta(minutes=59, seconds=30))
    assert not v.permitido
    # A virada da hora libera UM slot -- o do evento que saiu da janela --, e
    # não o teto inteiro. Um balde de hora cheia devolveria os dois aqui, e o
    # consumo real na fronteira seria o dobro do declarado.
    depois = AGORA + dt.timedelta(minutes=61)
    assert c.permitir("x", agora=depois).permitido
    assert not c.permitir("x", agora=depois).permitido
    # O segundo evento (aos 59 min) só sai da janela depois dos 119 min.
    assert c.permitir("x", agora=AGORA + dt.timedelta(minutes=119, seconds=1)).permitido


def test_espera_informada_e_o_tempo_ate_liberar_de_fato():
    c = LI.Contador({"x": LI.Regra("x", maximo=1, janela_s=600)})
    c.permitir("x", agora=AGORA)
    v = c.permitir("x", agora=AGORA + dt.timedelta(seconds=100))
    assert not v.permitido
    assert v.espera_s == pytest.approx(500, abs=1)


def test_consultar_sem_consumir_nao_gasta_o_teto():
    """A tela mostra a pressão; mostrar não pode custar um slot."""
    c = LI.Contador({"x": LI.Regra("x", maximo=1, janela_s=600)})
    assert c.permitir("x", agora=AGORA, consumir=False).permitido
    assert c.pressao("x", agora=AGORA) == 0.0
    assert c.permitir("x", agora=AGORA).permitido


def test_limite_nao_declarado_falha_alto_em_vez_de_se_criar_sozinho():
    with pytest.raises(KeyError):
        LI.Contador({}).permitir("inexistente", agora=AGORA)


def test_regra_inalcancavel_e_recusada_na_construcao():
    """Limite que nunca deixa passar é desligamento silencioso."""
    with pytest.raises(ValueError):
        LI.Regra("x", maximo=0, janela_s=60)


def test_pressao_publica_se_o_teto_esta_calibrado():
    """Peso sugerido não é verdade definitiva (Prompt 3), teto também não.

    Sem esse número, um limite cravado em 0,0 por semanas passa por proteção
    quando é decoração.
    """
    c = LI.Contador({"x": LI.Regra("x", maximo=4, janela_s=3600)})
    for _ in range(3):
        c.permitir("x", agora=AGORA)
    assert c.pressao("x", agora=AGORA) == 0.75
    c.permitir("x", agora=AGORA)
    c.permitir("x", agora=AGORA)
    assert c.negados("x") == 1
    assert c.resumo_auditoria(agora=AGORA)["x"]["negados"] == 1


# ── Trilha ───────────────────────────────────────────────────────────────────
def registro(**extra) -> T.Registro:
    base = dict(
        acao="Reduzir exposição", ativo="PETR4", percentual=3.5,
        motivo="nível de crise 2 com duas fontes independentes",
        evidencias=("queda de 4,1% em 2 pregões", "2 fontes oficiais"),
        motor="eventos_extraordinarios", nivel_crise=2, momento=AGORA,
        versao_modelo="calibracao-2026.09", versao_dados="b3-2026-09-02",
        frescor_horas=3.2)
    base.update(extra)
    return T.Registro(**base)


def test_trilha_responde_a_pergunta_do_requisito():
    """As três partes: por que, essa mudança, naquele momento."""
    texto = registro().responder()
    assert "03/09/2026 12:00 UTC" in texto            # naquele momento
    assert "Reduzir exposição em PETR4" in texto      # essa mudança
    assert "3.50% da carteira" in texto
    assert "nível de crise 2" in texto                # por que
    assert "queda de 4,1% em 2 pregões" in texto
    assert "modelo calibracao-2026.09" in texto
    assert "dados b3-2026-09-02" in texto


def test_registro_sem_acao_e_recusado_na_origem():
    with pytest.raises(ValueError):
        T.Registro(acao="   ")


def test_segredo_nao_chega_ao_banco_pela_trilha():
    """A trilha é o lugar mais fácil de esquecer que existe."""
    senha_teste = "x" * 16
    linha = registro(
        motivo=f"falha em postgresql://dfu:{senha_teste}@localhost:5433/wh",
        explicacao_llm="contato: fulano@exemplo.com").para_linha()
    assert senha_teste not in linha["motivo"]
    assert "[oculto:url_com_senha]" in linha["motivo"]
    assert "localhost:5433" in linha["motivo"]          # diagnóstico sobrevive
    assert "fulano@exemplo.com" not in linha["explicacao_llm"]


def test_falha_de_gravacao_vira_erro_proprio_e_nao_passa_batido():
    """É o gatilho da trava ``auditoria_falhou``.

    Se ``registrar`` engolisse a exceção, a trava viraria um ``pass`` e a
    mudança estratégica seguiria sem registro -- exatamente o que ela existe
    para impedir.
    """
    class EngineQuebrada:
        def begin(self):
            raise RuntimeError("connection refused")

    with pytest.raises(T.AuditoriaIndisponivel):
        T.registrar(registro(), engine=EngineQuebrada())

    e = TR.avaliar(auditoria_falhou=True)
    assert not e.permite(TR.MUDANCA_ESTRATEGICA)


def test_explicacao_da_llm_nao_ocupa_o_lugar_do_motivo():
    """Guardar a frase do modelo como justificativa faria a trilha responder
    'porque o texto dizia isso' -- a resposta que o requisito proíbe."""
    linha = registro(explicacao_llm="O mercado está nervoso.",
                     llm_aprovada=False, llm_motivo="números inventados"
                     ).para_linha()
    assert linha["motivo"] != linha["explicacao_llm"]
    assert "duas fontes independentes" in linha["motivo"]
    assert "reprovada e não foi exibida" in registro(
        llm_aprovada=False, llm_motivo="números inventados").responder()


# ── Confirmação explícita ────────────────────────────────────────────────────
def test_confirmacao_exibe_os_nove_pontos():
    c = C.montar(acao="Reduzir PETR4", tamanho="3,5% da carteira",
                 motivo="crise nível 2", riscos="pode reverter em dias",
                 custos="R$ 12,40 de corretagem", impostos="isento até R$ 20 mil",
                 concentracao="energia cai de 31% para 27%",
                 liquidez="0,3 dia de volume médio",
                 reversao="reversível a qualquer momento")
    assert c.completa and c.lacunas == ()
    for chave in C.PONTOS:
        assert C.ROTULO[chave] in c.texto()
    assert "Nenhuma operação é executada pelo APP4" in c.texto()


def test_ponto_nao_calculado_aparece_como_nao_calculado_e_nao_como_zero():
    """Imposto exibido como R$ 0,00 sem ninguém tê-lo calculado é afirmação
    falsa sobre o custo -- e o usuário decide com base nela."""
    c = C.montar(acao="Reduzir PETR4", tamanho="3,5%")
    assert not c.completa
    assert "Impostos" in c.lacunas
    assert "Impostos: não calculado" in c.texto()
    assert "não foram estimados no lugar" in c.texto()
    assert c.de("impostos").aparencia()["marca"] == "?"


def test_lacuna_nao_bloqueia_a_decisao_do_usuario():
    """Negar a decisão porque o sistema não calculou um campo trocaria
    transparência por paternalismo. A lacuna é publicada, não punitiva."""
    c = C.montar(acao="Reduzir PETR4")
    assert len(c.lacunas) == 8
    assert c.resumo_auditoria()["lacunas"]
    assert "confirmar" in C.BOTOES


def test_texto_que_induz_pressa_e_apontado():
    """"Não use botões ou textos que induzam decisões impulsivas."""
    assert C.texto_induz("Aproveite agora, é uma oportunidade única")
    assert C.texto_induz("Retorno garantido e sem risco")
    c = C.montar(acao="Reduzir PETR4", motivo="Última chance de sair antes da queda")
    assert c.problemas_de_tom()
    limpa = C.montar(acao="Reduzir PETR4",
                     motivo="crise nível 2 com duas fontes independentes")
    assert limpa.problemas_de_tom() == ()


def test_botoes_descrevem_o_ato_e_nao_o_resultado_esperado():
    for rotulo in C.BOTOES.values():
        assert C.texto_induz(rotulo) == (), rotulo
    assert C.BOTOES["recusar"] == "Não fazer"


def test_confirmacao_e_trilha_leem_a_mesma_origem():
    """Montar as duas separadamente deixaria o registro dizer uma coisa e o
    usuário ver outra -- e a auditoria responderia pela versão que ninguém leu."""
    reg = registro()
    c = C.do_registro(reg, riscos="pode reverter em dias")
    assert c.registro_id == reg.id
    assert c.de("acao").texto == "Reduzir exposição em PETR4"
    assert c.de("tamanho").texto == "3.50% da carteira"
    assert c.de("motivo").texto == reg.motivo
    assert "Impostos" in c.lacunas
