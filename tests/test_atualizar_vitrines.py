import json
from datetime import datetime, timezone

import pytest

from core.publicacao_agenda import ALVOS, POR_CHAVE
from scripts import atualizar_vitrines as av

# A implementação real, antes de a fixture abaixo trocá-la por um no-op.
_VIGIAR_REAL = av.vigiar


@pytest.fixture(autouse=True)
def _sem_vigia_real(monkeypatch):
    """O vigia chama o `gh` e o Telegram; aqui ele nunca roda de verdade."""
    monkeypatch.setattr(av, "vigiar", lambda: None)


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
    armazém de 11/08. O espelho também escreve no armazém (lê o Supabase).
    """
    escreve_no_armazem = {"fii_ingest", "espelho_supabase", "brapi_raw_poda",
                          "b3_pregao", "fii_documentos", "cvm_ipe",
                          "b3_brapi", "b3_brapi_anual"}
    escreve_arquivo = {"macro_insumos", "cdi_diario", "macro_brasil", "valuation_historico",
                       "informacoes_recentes", "rag_corpus", "fii_metrics_monthly",
                       "b3_linhagem"}
    esperado = ("armazem" if chave in escreve_no_armazem
                else "arquivo" if chave in escreve_arquivo else "supabase")
    assert onde == esperado


def test_carimbo_de_arquivo_le_o_generated_at(tmp_path, monkeypatch):
    from core.macro_data import insumos_publicados as ip

    quando = datetime(2026, 9, 26, 3, tzinfo=timezone.utc)
    (tmp_path / "m.json.gz").write_bytes(ip.serializar(quando, [], []))
    monkeypatch.setattr(av, "ROOT", tmp_path)
    assert av._carimbo_do_arquivo("m.json.gz") == quando
    assert av._carimbo_do_arquivo("ausente.json.gz") is None


def test_carimbo_de_arquivo_generico_le_o_gerado_em(tmp_path, monkeypatch):
    import gzip
    import json

    (tmp_path / "v.json.gz").write_bytes(gzip.compress(json.dumps(
        {"gerado_em": "2026-09-26T03:00:00+00:00"}).encode("utf-8")))
    monkeypatch.setattr(av, "ROOT", tmp_path)
    assert av._carimbo_do_arquivo("v.json.gz") == datetime(
        2026, 9, 26, 3, tzinfo=timezone.utc)


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


class _AlvoFalso:
    def __init__(self, chave, artefatos, modulo="fii", titulo="Vitrine de FIIs"):
        self.chave = chave
        self.artefatos = artefatos
        self.modulo = modulo
        self.titulo = titulo


class _ResultadoFalso:
    def __init__(self, ok, resumo):
        self.ok = ok
        self._resumo = resumo

    def resumo(self):
        return self._resumo


def test_so_alvo_com_artefato_declarado_chega_ao_git(monkeypatch):
    """Alvo que publica só no Supabase não pode disparar commit nenhum.

    Chamar `git` para cada alvo custaria um `git status` por publicação e, pior,
    tornaria plausível o dia em que alguém varresse o diretório em vez de ler a
    lista declarada -- e `data/public/` também guarda 25 MB de parquets do RAG.
    """
    chamados = []
    monkeypatch.setattr(av, "registrar", lambda _: None)
    monkeypatch.setattr(av, "publicar_artefatos",
                        lambda raiz, caminhos, msg: chamados.append(caminhos)
                        or _ResultadoFalso(True, "ok"))

    avisos = av.levar_artefatos_ao_repositorio([
        _AlvoFalso("us_prices", ()),
        _AlvoFalso("fii_selection", ("data/public/vitrine.json.gz",)),
    ])

    assert chamados == [("data/public/vitrine.json.gz",)]
    assert avisos == []


def test_recusa_do_git_vira_falha_e_nao_some_no_log(monkeypatch):
    """A vitrine chegou ao Supabase, mas o fallback do repositório não.

    Se isto só fosse ao log, o artefato pararia de ser commitado e ninguém
    saberia até a tela reprovar os 394 fundos por vencimento -- que é o defeito
    de 31/08/2026 voltando pela porta dos fundos.
    """
    monkeypatch.setattr(av, "registrar", lambda _: None)
    monkeypatch.setattr(
        av, "publicar_artefatos",
        lambda *_: _ResultadoFalso(False, "ramo atual é trabalho, não main"))

    avisos = av.levar_artefatos_ao_repositorio(
        [_AlvoFalso("fii_selection", ("data/public/vitrine.json.gz",))])

    assert len(avisos) == 1
    assert "fii_selection" in avisos[0] and "não main" in avisos[0]


def test_cdi_a_espera_da_primeira_publicacao_fica_devido(tmp_path, monkeypatch):
    """O arquivo vazio commitado não pode semear o alvo como em dia."""
    import gzip
    import json
    (tmp_path / "cdi.json.gz").write_bytes(
        gzip.compress(json.dumps({"gerado_em": None, "serie": []}).encode()))
    monkeypatch.setattr(av, "ROOT", tmp_path)
    assert av._carimbo_do_arquivo("cdi.json.gz") is None


class _Atualizacao:
    def __init__(self, avancou):
        self.avancou = avancou

    def resumo(self):
        return f"main local avançou {self.avancou} commit(s)"


def test_main_que_avancou_recarrega_antes_de_decidir(monkeypatch):
    """Alvo novo chegado pelo pull tem de entrar na mesma execução.

    Em 03/10/2026 o alvo `cdi_diario` foi mergeado, mas a rotina decide pelo
    `ALVOS` importado antes do pull: o CDI só sairia na execução seguinte.
    """
    monkeypatch.delenv(av._RECARREGADA, raising=False)
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    monkeypatch.setattr(av, "atualizar_main", lambda _r: _Atualizacao(2))
    monkeypatch.setattr(av, "ler_estado", _nunca)
    chamadas = []
    monkeypatch.setattr(av.subprocess, "run",
                        lambda cmd, **k: chamadas.append((cmd, k["env"])) or _Proc(0))
    assert av.main(["--apenas", "cdi_diario"]) == 0
    cmd, env = chamadas[0]
    assert cmd[-2:] == ["--apenas", "cdi_diario"]
    assert env[av._RECARREGADA] == "1"


def test_execucao_recarregada_nao_recarrega_de_novo(monkeypatch):
    monkeypatch.setenv(av._RECARREGADA, "1")
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    monkeypatch.setattr(av, "atualizar_main", _nunca)
    monkeypatch.setattr(av, "ler_estado", lambda: {})
    monkeypatch.setattr(av, "alvos_devidos", lambda *a, **k: [])
    assert av.main([]) == 0


def test_listar_nao_mexe_no_git(monkeypatch, capsys):
    monkeypatch.delenv(av._RECARREGADA, raising=False)
    monkeypatch.setattr(av, "atualizar_main", _nunca)
    monkeypatch.setattr(av, "ler_estado", lambda: {})
    assert av.main(["--listar"]) == 0


def test_vigia_roda_no_fim_da_execucao_real_mesmo_sem_nada_vencido(monkeypatch):
    """É quando nada publica que a vitrine envelhece calada."""
    monkeypatch.setenv(av._RECARREGADA, "1")
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    monkeypatch.setattr(av, "ler_estado", lambda: {})
    monkeypatch.setattr(av, "alvos_devidos", lambda *a, **k: [])
    chamadas = []
    monkeypatch.setattr(av, "vigiar", lambda: chamadas.append(1))
    assert av.main([]) == 0
    assert chamadas == [1]


@pytest.mark.parametrize("argv", [["--listar"], ["--dry-run"], ["--sem-vigia"]])
def test_vigia_nao_roda_em_listar_dry_run_ou_sem_vigia(monkeypatch, argv):
    monkeypatch.setenv(av._RECARREGADA, "1")
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    monkeypatch.setattr(av, "ler_estado", lambda: {})
    monkeypatch.setattr(av, "alvos_devidos", lambda *a, **k: [])
    monkeypatch.setattr(av, "vigiar", _nunca)
    assert av.main(argv) == 0


def test_falha_do_vigia_nao_muda_o_desfecho_da_rotina(monkeypatch):
    """O vigia é isolado: exceção dele vira linha de log, não exceção."""
    import scripts.vigia_automacoes as vigia

    monkeypatch.setattr(vigia, "vigiar", _nunca)
    avisos = []
    monkeypatch.setattr(av, "registrar", avisos.append)
    _VIGIAR_REAL()
    assert any("vigia de automações falhou" in a for a in avisos)


@pytest.mark.parametrize("decorrido_min, prazo_min, esperado", [
    (224, 225, False), (225, 225, True), (600, 0, False),
])
def test_prazo_esgotado(decorrido_min, prazo_min, esperado):
    assert av.prazo_esgotado(1000.0, prazo_min, 1000.0 + decorrido_min * 60) is esperado


class _AtualizacaoOk:
    ok = True

    def resumo(self):
        return "main local já em dia"


def test_prazo_esgotado_adia_o_resto_e_notifica_em_vez_de_morrer_calado(monkeypatch):
    """Em 05/10/2026 a tarefa agendada (limite de 4 h) matou a rotina no meio do
    `us_snapshot`: sete alvos não rodaram e ninguém foi avisado."""
    devidos = [(POR_CHAVE[c], "vencido") for c in ("cdi_diario", "macro_brasil", "b3_brapi")]
    relogio = iter([0.0, 0.0, 300 * 60])  # início, antes do 1º, antes do 2º
    monkeypatch.setenv(av._RECARREGADA, "1")
    monkeypatch.setattr(av.time, "monotonic", lambda: next(relogio))
    monkeypatch.setattr(av, "registrar", lambda _m: None)
    monkeypatch.setattr(av, "ler_estado", lambda: {})
    monkeypatch.setattr(av, "alvos_devidos", lambda *a, **k: devidos)
    monkeypatch.setattr(av, "atualizar_main", lambda _r: _AtualizacaoOk())
    gravados = []
    monkeypatch.setattr(av, "gravar_estado", lambda e: gravados.append(dict(e)))
    executados = []
    monkeypatch.setattr(av, "executar",
                        lambda alvo, _amb: executados.append(alvo.chave) or (True, ""))
    monkeypatch.setattr(av, "levar_artefatos_ao_repositorio", lambda _a: [])
    monkeypatch.setattr(av, "verificar", lambda *_a: (True, ""))
    avisos = []
    monkeypatch.setattr(av, "notificar", lambda msg, _titulo: avisos.append(msg))

    assert av.main(["--sem-armazem", "--sem-vigia"]) == 1
    assert executados == ["cdi_diario"]
    assert "macro_brasil, b3_brapi" in avisos[0]
    # Adiado não ganha registro: segue vencido para a próxima execução.
    assert "cdi_diario" in gravados[-1]
    assert "macro_brasil" not in gravados[-1] and "b3_brapi" not in gravados[-1]


def test_ingestao_brapi_e_a_ultima_da_fila():
    """O `daily` leva cerca de 2 h; quem o prazo adia tem de ser ele, não notícias
    e macro. E depois da poda, que renormaliza o que o publicador leva."""
    ordem = [a.chave for a in ALVOS]
    assert ordem[-2:] == ["b3_brapi", "b3_brapi_anual"]
    assert ordem.index("brapi_raw_poda") < ordem.index("b3_brapi")
