from scripts.archive_remote_brapi_raw import _chunks, _json, ids_sem_manifesto


def test_archive_chunks_are_bounded_and_complete():
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_archive_json_preserves_null_and_serializes_payload():
    assert _json(None) is None
    assert _json({"a": 1}) == '{"a":1}'


def test_manifesto_completo_nao_acusa_falta():
    assert ids_sem_manifesto([1, 2, 3], [1, 2, 3]) == set()


def test_manifesto_acusa_exatamente_os_ids_que_faltam():
    assert ids_sem_manifesto([1, 2, 3], [1]) == {2, 3}


def test_manifesto_com_sobras_de_rodada_anterior_ainda_cobre():
    """O caso que travava a compactacao para sempre.

    O manifesto e chaveado por (archive_source, remote_id) e ACUMULA entre
    execucoes, enquanto o remoto e podado. Em 16/08/2026 ele tinha 26.481 linhas
    para 19.115 remotas: 7.366 ids de julho que nao existem mais la. A checagem
    antiga comparava CONTAGENS, entao acusava incompletude justamente quando
    havia dado a MAIS preservado. Cobertura e questao de conjunto, nao de total.
    """
    atuais = [10, 11, 12]
    manifesto = [10, 11, 12, 90, 91]        # 90 e 91 sobraram de uma rodada antiga
    assert ids_sem_manifesto(atuais, manifesto) == set()


def test_manifesto_com_sobras_ainda_acusa_id_novo_nao_arquivado():
    """Ter sobras nao pode mascarar um id novo sem arquivo -- o portao existe por isso."""
    assert ids_sem_manifesto([10, 11, 99], [10, 11, 90, 91]) == {99}
