from scripts.archive_remote_brapi_raw import _chunks, _json


def test_archive_chunks_are_bounded_and_complete():
    assert list(_chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_archive_json_preserves_null_and_serializes_payload():
    assert _json(None) is None
    assert _json({"a": 1}) == '{"a":1}'
