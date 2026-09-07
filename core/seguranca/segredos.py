"""Mascaramento de segredo e de dado pessoal em qualquer texto que saia daqui.

O que este módulo resolve
-------------------------
Três saídas levam texto para fora do processo e nenhuma delas era filtrada:
o log (arquivo e stdout do Streamlit Cloud), o prompt da LLM (que vai para um
provedor externo) e a mensagem de erro que a tela mostra. Uma ``DATABASE_URL``
com senha aparece inteira em qualquer uma das três no dia em que a conexão
falhar -- e falhar é justamente quando alguém copia o log para pedir ajuda.

Duas famílias, e elas não são a mesma coisa
-------------------------------------------
``CREDENCIAIS`` são chaves: vazar uma dá acesso a um sistema.
``PESSOAIS`` são dados do usuário: CPF, conta, cartão. Vazar um não dá acesso,
mas é o dado que a instrução proíbe mandar para a LLM ("nenhuma credencial,
dado bancário completo ou informação pessoal desnecessária").

Separadas porque o tratamento difere: credencial some sempre, em todo lugar.
Dado pessoal some do prompt e do log, mas a tela do próprio dono pode mostrá-lo
-- é dele.

Por que mascarar e não recusar
-------------------------------
Recusar a linha inteira apagaria a evidência de diagnóstico junto com o segredo,
que é o erro de ``memoria: faixa-de-validacao-apaga-evidencia``. O rótulo do que
foi ocultado fica: ``[oculto:url_com_senha]`` diz que havia uma URL com senha
ali, e isso é exatamente o que o log precisava dizer.

Este módulo é puro: sem rede, sem banco, sem LLM. Ele nunca registra, devolve
nem levanta exceção contendo o valor que ocultou -- se registrasse, seria a
própria fuga que existe para impedir.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

#: Marca do que foi ocultado. O rótulo entra, o valor nunca.
MOLDE = "[oculto:{rotulo}]"


@dataclass(frozen=True)
class Achado:
    """Um segredo encontrado. Guarda o rótulo e a posição, **nunca o valor**.

    Não ter campo para o valor é intencional e não é excesso de zelo: um
    ``Achado`` acaba em log, em teste e em mensagem de erro, e um campo
    ``valor`` vazaria por todos esses caminhos com aparência de diagnóstico.
    """

    rotulo: str
    familia: str            # "credencial" ou "pessoal"
    inicio: int
    fim: int

    @property
    def tamanho(self) -> int:
        return self.fim - self.inicio


# ── Credenciais ──────────────────────────────────────────────────────────────
# Cada padrão é ancorado numa forma que só credencial tem. Regex genérica de
# "sequência longa" marcaria hash de commit, id_dedup de notícia (sha256) e
# ticker mal formatado -- e mascarar diagnóstico legítimo faz o filtro ser
# desligado por quem depende dele.
CREDENCIAIS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chave_privada", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
        r"[\s\S]*?-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("chave_anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("chave_openai", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("token_github", re.compile(r"\bgh[opsu]_[A-Za-z0-9]{30,}")),
    ("chave_google", re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
                       r"\.[A-Za-z0-9_\-]{10,}")),
    # A senha e só ela: o host e o banco ficam visíveis porque são o que o
    # diagnóstico precisa ("conectou no 5433 ou no Supabase?").
    ("url_com_senha", re.compile(r"(?<=://)([^\s:/@]+):([^\s/@]{4,})(?=@)")),
    # Atribuição explícita: SENHA=..., api_key: "...", token = '...'
    ("valor_atribuido", re.compile(
        r"(?i)\b(?:senha|password|passwd|secret|api[_-]?key|apikey|token|"
        r"access[_-]?key|private[_-]?key|client[_-]?secret)\b"
        r"\s*[=:]\s*[\"']?([^\s\"',;)]{6,})")),
)

# ── Dados pessoais ───────────────────────────────────────────────────────────
# Não entram no prompt da LLM nem no log. A tela do dono continua mostrando.
PESSOAIS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cpf", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")),
    ("cnpj", re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")),
    ("cartao", re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b|\b\d{16}\b")),
    ("agencia_conta", re.compile(r"\b\d{4,5}-?\d?\s*/\s*\d{5,12}-\d\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
)


def _varrer(texto: str, familia: str,
            padroes: tuple[tuple[str, re.Pattern[str]], ...]) -> list[Achado]:
    achados_: list[Achado] = []
    for rotulo, padrao in padroes:
        for m in padrao.finditer(texto):
            if rotulo == "url_com_senha":
                inicio, fim = m.span(2)
            elif m.groups() and m.group(1) is not None:
                # Em ``SENHA=abc`` o achado é ``abc``, não a palavra ``SENHA``:
                # mascarar o rótulo tornaria o log ilegível sem esconder nada.
                inicio, fim = m.span(1)
            else:
                inicio, fim = m.span()
            achados_.append(Achado(rotulo, familia, inicio, fim))
    return achados_


def achados(texto: str, *, pessoais: bool = True) -> tuple[Achado, ...]:
    """O que há de sensível no texto, em ordem de posição. Sem os valores."""
    if not texto:
        return ()
    encontrados = _varrer(texto, "credencial", CREDENCIAIS)
    if pessoais:
        encontrados += _varrer(texto, "pessoal", PESSOAIS)
    return tuple(sorted(encontrados, key=lambda a: (a.inicio, -a.tamanho)))


def contem_segredo(texto: str, *, pessoais: bool = False) -> bool:
    """``True`` se há credencial (e, se pedido, dado pessoal) no texto."""
    return bool(achados(texto, pessoais=pessoais))


def mascarar(texto, *, pessoais: bool = True) -> str:
    """Devolve o texto com todo trecho sensível trocado pelo seu rótulo.

    Trechos que se sobrepõem são resolvidos pelo mais longo -- uma chave privada
    inteira vence a detecção de ``valor_atribuido`` dentro dela, senão o
    resultado sairia com marcas aninhadas e ilegível.
    """
    if texto is None:
        return ""
    texto = texto if isinstance(texto, str) else str(texto)
    lista = achados(texto, pessoais=pessoais)
    if not lista:
        return texto

    partes: list[str] = []
    cursor = 0
    for a in lista:
        if a.inicio < cursor:            # já coberto por um achado maior
            continue
        partes.append(texto[cursor:a.inicio])
        partes.append(MOLDE.format(rotulo=a.rotulo))
        cursor = a.fim
    partes.append(texto[cursor:])
    return "".join(partes)


# ── Filtro de logging ────────────────────────────────────────────────────────
class FiltroDeSegredos(logging.Filter):
    """Mascara a mensagem **já formatada** antes de ela chegar ao handler.

    Formatar primeiro é o ponto: a senha quase nunca está no template, está no
    argumento (``logger.warning("falha em %s", url)``). Um filtro que olhasse só
    ``record.msg`` deixaria passar exatamente o caso que importa.

    Depois de mascarar, ``args`` é zerado -- senão o handler formataria de novo,
    reinserindo os argumentos originais por cima do texto já limpo.
    """

    def __init__(self, *, pessoais: bool = True) -> None:
        super().__init__()
        self._pessoais = pessoais

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            formatada = record.getMessage()
        except Exception:                # pragma: no cover - template quebrado
            return True
        limpa = mascarar(formatada, pessoais=self._pessoais)
        if limpa != formatada:
            record.msg = limpa
            record.args = ()
        if record.exc_info:
            # A exceção carrega a mesma URL na mensagem. Some com o traceback
            # estruturado e deixa o texto já mascarado no lugar.
            tipo, valor, _ = record.exc_info
            record.exc_info = None
            record.msg = (f"{limpa} | {getattr(tipo, '__name__', 'Erro')}: "
                          f"{mascarar(str(valor), pessoais=self._pessoais)}")
            record.args = ()
        return True


def instalar_no_logger_raiz(*, pessoais: bool = True) -> FiltroDeSegredos:
    """Instala o filtro em todos os handlers da raiz. Idempotente."""
    filtro = FiltroDeSegredos(pessoais=pessoais)
    raiz = logging.getLogger()
    for handler in raiz.handlers:
        if not any(isinstance(f, FiltroDeSegredos) for f in handler.filters):
            handler.addFilter(filtro)
    if not any(isinstance(f, FiltroDeSegredos) for f in raiz.filters):
        raiz.addFilter(filtro)
    return filtro
