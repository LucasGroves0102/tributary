import zipfile

import pytest

from company_loader import bulkzip

from .conftest import write_zip


def test_matches_zipfile(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stored.json", b"{}", compress_type=zipfile.ZIP_STORED)
        z.writestr("deflated.json", b"x" * 10_000, compress_type=zipfile.ZIP_DEFLATED)
    with path.open("rb") as f:
        got = {e.name: bulkzip.read(f, e) for e in bulkzip.entries(f)}
    assert got == {"stored.json": b"{}", "deflated.json": b"x" * 10_000}


def test_zip64_directory(tmp_path):
    # more than 65535 entries needs the zip64 end of central directory record
    names = {f"CIK{i:010d}.json": b"" for i in range(65_600)}
    path = write_zip(tmp_path / "big.zip", names)
    with path.open("rb") as f:
        assert sum(1 for _ in bulkzip.entries(f)) == 65_600


def test_corrupt_entry_is_detected(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.json", b"hello world", compress_type=zipfile.ZIP_STORED)
    data = bytearray(path.read_bytes())
    data[data.index(b"hello")] = ord("j")
    path.write_bytes(bytes(data))
    with path.open("rb") as f, pytest.raises(bulkzip.BadZip):
        bulkzip.read(f, next(bulkzip.entries(f)))


def test_not_a_zip(tmp_path):
    path = tmp_path / "a.zip"
    path.write_bytes(b"<html>403</html>")
    with path.open("rb") as f, pytest.raises(bulkzip.BadZip):
        next(bulkzip.entries(f))
