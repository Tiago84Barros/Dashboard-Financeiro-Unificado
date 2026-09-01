import json

import pytest

from core.publicacao_agenda import ALVOS, POR_CHAVE
from scripts import atualizar_vitrines as av


def test_todo_alvo_tem_carimbo_de_onde_ler_a_ultima_publicacao():
    """Alvo sem carimbo é semeado como "nunca publicado" e republica tudo.

    Para `us_prices` isso significa reescrever 346 mil linhas no Supabase por
    falta de uma linha de mapeamento -- e sem erro nenhum, porque republicar é
    tecnicamente correto.
    """
    assert set(av.CARIMBO) == set(POR_CHAVE)


@pytest.mark.parametrize("chave,onde", sorted((k, v[0]) for k, v in av.CARIMBO.items()))
def test_carimbo_aponta_para_a_base_que_o_alvo_escreve(chave, onde):
    """A ingestão escreve no armazém; os publicadores, no Supabase.

    Semear `fii_ingest` pelo `market.fiis` do Supabase leria a cópia que o
    workflow remoto mantém -- um carimbo recente de uma tabela que este alvo não
    escreve. A rotina acharia que está em dia com o armazém parado há semanas,
    que foi exatamente o estado encontrado em 01/09/2026: Supabase de 26/08,
    armazém de 11/08.
    """
    assert onde == ("armazem" if chave == "fii_ingest" else "supabase")


def test_resumo_json_pega_a_ultima_linha_e_so_o_que_interessa():
    saida = ('log solto\n'
             '{"published_rows": 1, "ignorado": "x"}\n'
             'ruido\n'
             '{"published_rows": 394, "validation_status": "passed", "lixo": 1}\n')
    resumo = json.loads(av._resumo_json(saida))
    assert resumo == {"published_rows": 394, "validation_status": "passed"}


@pytest.mark.parametrize("saida", ["", "sem json", "{quebrado", "[1, 2]"])
def test_resumo_json_sem_json_valido_devolve_vazio(saida):
    assert av._resumo_json(saida) == ""


def test_estado_ilegivel_vira_primeira_execucao(tmp_path, monkeypatch):
    arquivo = tmp_path / "estado.json"
    arquivo.write_text("{isto nao e json", encoding="utf-8")
    monkeypatch.setattr(av, "ESTADO", arquivo)
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    assert av.ler_estado() == {}


def test_estado_com_lista_no_lugar_de_objeto_nao_derruba(tmp_path, monkeypatch):
    arquivo = tmp_path / "estado.json"
    arquivo.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(av, "ESTADO", arquivo)
    assert av.ler_estado() == {}


def test_gravacao_de_estado_e_atomica(tmp_path, monkeypatch):
    """Meia gravação deixaria o arquivo ilegível e a rotina republicaria tudo."""
    arquivo = tmp_path / "estado.json"
    monkeypatch.setattr(av, "ESTADO", arquivo)
    av.gravar_estado({"fii_selection": {"ultimo_status": "ok"}})
    assert json.loads(arquivo.read_text(encoding="utf-8"))["fii_selection"]
    assert not arquivo.with_suffix(".json.tmp").exists()


def test_semear_nao_sobrescreve_registro_ja_existente(monkeypatch):
    """O histórico da rotina vale mais que o carimbo da tabela.

    O carimbo diz quando a linha foi escrita; não diz se a publicação inteira
    terminou. Sobrescrever um `erro` conhecido por um `ok` inferido apagaria a
    única evidência de que o alvo precisa de nova tentativa.
    """
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    monkeypatch.setattr(av, "CARIMBO", {})
    estado = {"fii_selection": {"ultima_publicacao": "2026-08-31T20:03:00+00:00",
                                "ultimo_status": "ok"}}
    assert av.semear(estado, {}) == estado


def test_alvo_por_versao_le_a_versao_declarada():
    alvo = next(a for a in ALVOS if a.por_versao)
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION

    assert av.versao_corrente(alvo) == US_FUNDAMENTAL_SCORE_VERSION


def test_alvo_sem_versao_declarada_nao_inventa():
    alvo = next(a for a in ALVOS if not a.por_versao)
    assert av.versao_corrente(alvo) is None


def test_versao_de_modulo_inexistente_avisa_e_nao_derruba(monkeypatch):
    avisos = []
    monkeypatch.setattr(av, "registrar", avisos.append)
    falso = POR_CHAVE["us_vintages"].__class__(
        chave="x", titulo="x", passos=(), cadencia_dias=None, modulo="us",
        versao_de="core.modulo_que_nao_existe:COISA")
    assert av.versao_corrente(falso) is None
    assert avisos and "não consegui ler" in avisos[0]


def _nunca(*_a, **_k):
    raise AssertionError("não deveria ter sido chamado")


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


@pytest.mark.parametrize("proc,esperado", [
    (_Proc(0, "27.3.1\n"), True),
    (_Proc(1, ""), False),
    (_Proc(0, "\n"), False),
])
def test_daemon_responde_exige_resposta_do_servidor(monkeypatch, proc, esperado):
    """`docker version` sozinho responde com o motor morto.

    Ele imprime a versão do CLIENTE e só depois reclama do servidor. Uma checagem
    por código de saída de `docker version` daria "motor de pé" em máquina sem
    daemon nenhum -- e a rotina seguiria para os 600s de espera pela saúde de um
    container que não existe na sessão.
    """
    monkeypatch.setattr(av.subprocess, "run", lambda *a, **k: proc)
    assert av.daemon_responde() is esperado


def test_daemon_pronto_nao_abre_nada_quando_ja_responde(monkeypatch):
    monkeypatch.setattr(av, "daemon_responde", lambda: True)
    monkeypatch.setattr(av.subprocess, "Popen", _nunca)
    assert av.daemon_pronto() is True


def test_daemon_pronto_sem_o_executavel_falha_rapido(monkeypatch, tmp_path):
    """Sem o Docker Desktop instalado, esperar 300s não muda o desfecho."""
    avisos = []
    monkeypatch.setattr(av, "daemon_responde", lambda: False)
    monkeypatch.setattr(av, "DOCKER_DESKTOP", tmp_path / "nao_existe.exe")
    monkeypatch.setattr(av, "registrar", avisos.append)
    monkeypatch.setattr(av.subprocess, "Popen", _nunca)
    assert av.daemon_pronto() is False
    assert avisos and "não existe" in avisos[0]


def test_armazem_nao_e_culpado_quando_quem_esta_fora_e_o_motor(monkeypatch):
    """Motor fora do ar e container parado falham de jeitos diferentes.

    Em 01/09/2026 o gatilho de logon disparou 3 minutos depois da sessão abrir,
    antes de o Docker Desktop existir: o log dizia "armazém não ficou saudável"
    sobre um container intacto, e mandava investigar o lugar errado.
    """
    monkeypatch.setattr(av, "daemon_pronto", lambda: False)
    monkeypatch.setattr(av.subprocess, "run", _nunca)
    assert av.armazem_pronto() is False
