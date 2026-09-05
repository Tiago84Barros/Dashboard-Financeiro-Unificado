"""O agendador da coleta e o destino que ele precisa alcançar (Prompt 2).

Por que este arquivo existe
---------------------------
O Prompt 2 pede notícias atualizadas automaticamente. O `.github/workflows/
noticias.yml` já existia, com cron de 30 minutos -- e a conclusão fácil era
"o agendador está pronto, falta o usuário ligar". Ela é falsa desde que o
acervo mudou de casa: `noticias_itens` mora no armazém local, e um runner do
GitHub não o alcança. Ligar o cron faria a coleta rodar, gastar requisição de
provedor em Alpha Vantage e Marketaux, e descartar o resultado inteiro.

O job **avisa** -- vira ``partial_success`` com "coleta não persistida" --, mas
o aviso chega depois de a cota ter sido paga. Gastar para jogar fora é o
defeito, e ele é evitável antes da primeira requisição.

Três coisas são cobradas aqui:

1. **A saúde mede o banco em que a coleta é gravada**, e não só o da vitrine.
   Enquanto ``checar_banco`` era a única medição de banco, um agendador remoto
   deixava o painel todo verde com o acervo inalcançável -- "medir a fonte que
   a decisão lê", e a decisão aqui é gravar.
2. **Sem destino é falha, não desconhecido.** Em toda a saúde, não-verificado é
   ``None``; aqui a configuração é lida localmente e sempre pode ser lida, então
   "não há destino" é medição, e seu efeito é a coleta se perder.
3. **O agendador que sustenta a cadência é o local**, na máquina do armazém.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from core.noticias import saude
from data_pipeline import cli_noticias

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOW = RAIZ / ".github" / "workflows" / "noticias.yml"
TAREFAS = RAIZ / "scripts" / "registrar_tarefas.ps1"


def _texto(caminho: Path) -> str:
    with io.open(caminho, encoding="utf-8") as fh:
        return fh.read()


class _EngineFalso:
    def __init__(self, erro: Exception | None = None):
        self.erro = erro

    def connect(self):
        if self.erro is not None:
            raise self.erro
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_a, **_k):
        return None


# ───────────────────────────── a saúde do destino ────────────────────────────

def test_sem_destino_a_saude_reprova_em_vez_de_dizer_desconhecido(monkeypatch):
    """``None`` aqui seria ler "ainda não verifiquei" para algo verificado.

    E o custo do erro não é simétrico: desconhecido não segura ninguém, e o
    efeito real é a coleta gastar cota para descartar o que coletou.
    """
    monkeypatch.setattr("core.noticias.destino.url_acervo", lambda: "")

    v = saude.checar_acervo()

    assert v.ok is False, "acervo sem destino passou como desconhecido"
    assert "descartada" in v.detalhe
    assert "cota" in v.detalhe


def test_com_destino_que_responde_a_saude_aprova():
    v = saude.checar_acervo(engine=_EngineFalso())

    assert v.ok is True
    assert "gravada" in v.detalhe


def test_destino_configurado_mas_fora_do_ar_e_falha_com_a_causa():
    v = saude.checar_acervo(engine=_EngineFalso(RuntimeError("conexao recusada")))

    assert v.ok is False
    assert "conexao recusada" in v.detalhe


def test_resolver_o_destino_e_falivel_e_isso_nao_derruba_a_saude(monkeypatch):
    """Configuração incompleta já subiu ``AttributeError`` por um job inteiro
    neste projeto. Painel de saúde que levanta é painel que não abre.
    """
    monkeypatch.setattr("core.noticias.destino.url_acervo",
                        lambda: "postgresql://x/y")

    def _explode():
        raise AttributeError("config incompleta")

    monkeypatch.setattr("core.noticias.destino.engine_acervo", _explode)

    v = saude.checar_acervo()

    assert v.ok is False
    assert "config incompleta" in v.detalhe


def test_o_acervo_entra_no_conjunto_verificado():
    """Verificação que existe e ninguém chama não protege nada."""
    servicos = [v.servico for v in saude.checar_tudo()]

    assert saude.SERVICO_ACERVO in servicos
    assert servicos.index(saude.SERVICO_ACERVO) == servicos.index(
        saude.SERVICO_BANCO) + 1, (
        "acervo longe de banco: as duas se parecem e a ordem é o que ensina "
        "que não são a mesma coisa")


def test_o_banco_da_vitrine_nao_responde_pelo_acervo():
    """Se um dia ``checar_banco`` voltar a medir o destino da gravação, esta
    asserção cai -- e é o dia certo para descobrir, não meses depois por um
    painel verde com o acervo vazio.
    """
    doc = saude.checar_banco.__doc__ or ""
    assert "Não é onde o acervo é gravado" in doc
    assert "checar_acervo" in doc


# ─────────────────────────── o freio antes da cota ───────────────────────────

def test_destino_ausente_reprova_o_passo_de_linha_de_comando(monkeypatch,
                                                             capsys):
    monkeypatch.setattr(saude, "checar_acervo",
                        lambda: saude.Verificacao("acervo", False, "sem destino"))

    assert cli_noticias.main(["--destino"]) == 1
    assert "sem destino" in capsys.readouterr().out


def test_destino_presente_libera_o_passo(monkeypatch):
    monkeypatch.setattr(saude, "checar_acervo",
                        lambda: saude.Verificacao("acervo", True, "ok"))

    assert cli_noticias.main(["--destino"]) == 0


def test_o_passo_de_destino_nao_coleta_nada(monkeypatch):
    """A checagem não pode ser o que ela evita.

    Um pré-voo que dispara a coleta para descobrir se dá para gravar gastaria
    a cota que existe para poupar.
    """
    def _proibido(*_a, **_k):
        raise AssertionError("--destino executou o job de coleta")

    monkeypatch.setattr("data_pipeline.jobs.update_noticias.run", _proibido)
    monkeypatch.setattr(saude, "checar_acervo",
                        lambda: saude.Verificacao("acervo", True, "ok"))

    assert cli_noticias.main(["--destino"]) == 0


def test_saude_continua_sem_reprovar_o_passo(monkeypatch):
    """``--saude`` informa e ``--destino`` decide. Trocar isso faria um provedor
    sem chave -- configuração do usuário -- pintar o agendador de vermelho.
    """
    monkeypatch.setattr(saude, "checar_tudo",
                        lambda: (saude.Verificacao("banco", False, "caiu"),))
    monkeypatch.setattr(saude, "resumo", lambda v: {"falha": 1})

    assert cli_noticias.main(["--saude"]) == 0


# ──────────────────────────── a fiação do agendador ──────────────────────────

def test_o_workflow_checa_o_destino_antes_de_coletar():
    """Ordem invertida devolve o defeito inteiro: reprovar depois de gastar."""
    yaml = pytest.importorskip("yaml")

    dados = yaml.safe_load(_texto(WORKFLOW))
    passos = dados["jobs"]["coletar"]["steps"]
    comandos = [str(p.get("run") or "") for p in passos]

    destino = next(i for i, c in enumerate(comandos) if "--destino" in c)
    coleta = next(i for i, c in enumerate(comandos)
                  if "cli_noticias" in c and "--" not in c.split("cli_noticias")[1])

    assert destino < coleta, (
        "a coleta roda antes da checagem de destino: a cota é gasta e só "
        "depois se descobre que não havia onde gravar")


def test_o_workflow_declara_o_destino_do_acervo_sem_valor_padrao():
    """Um fallback para o Supabase aqui seria o oposto do que se decidiu: ele é
    exatamente o banco que o acervo não pode ocupar.
    """
    yaml = pytest.importorskip("yaml")

    env = yaml.safe_load(_texto(WORKFLOW))["jobs"]["coletar"]["env"]

    assert "NOTICIAS_LOCAL_DB_URL" in env
    assert "secrets.NOTICIAS_LOCAL_DB_URL" in env["NOTICIAS_LOCAL_DB_URL"]
    assert "SUPABASE" not in env["NOTICIAS_LOCAL_DB_URL"].upper()


def test_o_workflow_nao_se_apresenta_mais_como_o_agendador():
    """A frase que envelheceu invertido.

    O cabeçalho dizia "quem sustenta a cadência é o cron" -- verdade até o
    acervo mudar de casa, e desde então uma instrução para gastar cota à toa.
    """
    cabecalho = _texto(WORKFLOW).split("name:")[0]

    assert "NÃO É MAIS O AGENDADOR PRINCIPAL" in cabecalho
    assert "registrar_tarefas.ps1" in cabecalho


def test_existe_tarefa_local_de_coleta_com_repeticao():
    """O agendador que de fato alcança o armazém."""
    texto = _texto(TAREFAS)

    assert "DFU - Coleta de noticias" in texto
    assert "-m data_pipeline.cli_noticias" in texto
    assert "RepetitionInterval" in texto
    assert "New-TimeSpan -Minutes 30" in texto


def test_a_tarefa_local_nao_usa_a_duracao_maxima():
    """``[TimeSpan]::MaxValue`` é aceito por umas versões do agendador e
    recusado por outras -- e a recusa aconteceria na máquina do usuário.
    """
    codigo = [l for l in _texto(TAREFAS).splitlines()
              if not l.lstrip().startswith("#")]
    assert not any("MaxValue" in l for l in codigo)


def test_o_script_de_tarefas_continua_valido_para_o_powershell():
    """Erro de sintaxe aqui só apareceria quando o usuário fosse registrar."""
    if sys.platform != "win32":  # pragma: no cover - CI Linux
        pytest.skip("parser do PowerShell só existe no Windows")

    ps = (
        "$e=$null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{TAREFAS}',[ref]$null,[ref]$e);"
        "if($e.Count -gt 0){$e|%{$_.Message};exit 1}"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
