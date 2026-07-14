import io
import zipfile

from data_pipeline.market.fii_b3_history import parse_cotahist


def _field(line, start, end, value):
    line[start:end] = list(str(value).ljust(end - start)[:end - start])


def test_parse_cotahist_keeps_only_fii_bdi_12():
    line = list(" " * 245)
    _field(line, 0, 2, "01")
    _field(line, 2, 10, "20260130")
    _field(line, 10, 12, "12")
    _field(line, 12, 24, "TEST11")
    _field(line, 24, 27, "010")
    _field(line, 27, 39, "FUNDO TESTE")
    _field(line, 56, 69, "0000000010000")
    _field(line, 69, 82, "0000000011000")
    _field(line, 82, 95, "0000000009000")
    _field(line, 95, 108, "0000000010000")
    _field(line, 108, 121, "0000000010500")
    _field(line, 147, 152, "00010")
    _field(line, 152, 170, "000000000000001000")
    _field(line, 170, 188, "000000000010500000")
    _field(line, 230, 242, "BRTESTCTF000")
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as zipped:
        zipped.writestr("COTAHIST_A2026.TXT", "".join(line))
    rows = parse_cotahist(content.getvalue())
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TEST11"
    assert rows[0]["close"] == 105.0
