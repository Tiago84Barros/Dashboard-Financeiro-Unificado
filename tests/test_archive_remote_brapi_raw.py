from datetime import datetime, timedelta, timezone

from scripts.archive_remote_brapi_raw import (
    _chunks,
    _json,
    chave_de_payload,
    chaves_sem_manifesto,
)

T0 = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _linha(id_, *, hora=T0, ticker="PETR4", sha="abc", endpoint="quote",
           status="success"):
    return {"id": id_, "endpoint": endpoint, "fetched_at": hora,
            "request_status": status, "ticker": ticker, "content_sha256": sha}


def test_archive_chunks_are_bounded_and_complete():
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_archive_json_preserves_null_and_serializes_payload():
    assert _json(None) is None
    assert _json({"a": 1}) == '{"a":1}'


def test_manifesto_completo_nao_acusa_falta():
    linhas = [_linha(1), _linha(2, sha="def")]
    assert chaves_sem_manifesto(linhas, {chave_de_payload(r) for r in linhas}) == set()


def test_manifesto_acusa_exatamente_o_payload_que_falta():
    coberta, faltante = _linha(1), _linha(2, sha="def")
    sobra = chaves_sem_manifesto([coberta, faltante], {chave_de_payload(coberta)})
    assert sobra == {chave_de_payload(faltante)}


def test_manifesto_com_sobras_de_rodada_anterior_ainda_cobre():
    """O caso que travava a compactacao para sempre.

    O manifesto ACUMULA entre execucoes, enquanto o remoto e podado. Em
    16/08/2026 ele tinha 26.481 linhas para 19.115 remotas: 7.366 payloads de
    julho que nao existem mais la. A checagem antiga comparava CONTAGENS, entao
    acusava incompletude justamente quando havia dado a MAIS preservado.
    """
    atuais = [_linha(10), _linha(11, sha="b")]
    antigas = [_linha(90, hora=T0 - timedelta(days=30), sha="velho1"),
               _linha(91, hora=T0 - timedelta(days=30), sha="velho2")]
    manifesto = {chave_de_payload(r) for r in atuais + antigas}
    assert chaves_sem_manifesto(atuais, manifesto) == set()


def test_manifesto_com_sobras_ainda_acusa_payload_novo_nao_arquivado():
    """Ter sobras nao pode mascarar um payload novo sem arquivo."""
    novo = _linha(3, sha="novo")
    manifesto = {chave_de_payload(_linha(90, hora=T0 - timedelta(days=30)))}
    assert chaves_sem_manifesto([novo], manifesto) == {chave_de_payload(novo)}


def test_id_remoto_reiniciado_nao_conta_como_coberto():
    """A compactacao faz DROP/CREATE e a sequencia REINICIA em 1.

    Medido em 01/09/2026: 6.851 dos 8.996 ids remotos ja constavam no manifesto
    apontando para payloads de julho completamente diferentes. Chavear por
    remote_id daria "coberto" para dado que nunca foi arquivado, e a compactacao
    apagaria o original.
    """
    julho = _linha(1, hora=T0 - timedelta(days=55), ticker="VALE3", sha="julho")
    setembro = _linha(1, hora=T0, ticker="PETR4", sha="setembro")
    assert julho["id"] == setembro["id"]
    assert chaves_sem_manifesto([setembro], {chave_de_payload(julho)}) == {
        chave_de_payload(setembro)}


def test_chave_trata_ticker_e_hash_nulos_sem_colidir():
    """Linhas operacionais (falha de rede) nao tem hash; nao podem virar uma so."""
    a = _linha(1, ticker=None, sha=None, status="failed")
    b = _linha(2, ticker=None, sha=None, status="failed",
               hora=T0 + timedelta(minutes=1))
    assert chave_de_payload(a) != chave_de_payload(b)
    assert chaves_sem_manifesto([a, b], {chave_de_payload(a)}) == {chave_de_payload(b)}
