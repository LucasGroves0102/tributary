import gzip
import hashlib
import io
import os
import time

import pytest
import zstandard

from tributary_bodies import (
    Body,
    Compressing,
    MissingBody,
    Store,
    compress,
    decompress,
    open_body,
    read_body,
)

HTML = b"<html><body>" + b"<p>Item 7. Management's discussion and analysis</p>" * 2000 + b"</html>"


def test_body_of_hashes_the_uncompressed_bytes_and_compresses_them():
    body = Body.of(HTML)
    assert body.sha256 == hashlib.sha256(HTML).hexdigest()
    assert body.size == len(HTML)
    assert len(body.data) < len(HTML) // 20
    assert decompress(body.data) == HTML
    assert zstandard.frame_content_size(body.data) == len(HTML)


def test_a_streamed_body_is_the_same_body_without_a_content_size():
    c = Compressing()
    for i in range(0, len(HTML), 1000):
        c.update(HTML[i:i + 1000])
    body = c.finish()
    assert (body.sha256, body.size) == (Body.of(HTML).sha256, len(HTML))
    assert zstandard.frame_content_size(body.data) == -1
    assert decompress(body.data) == HTML


def test_put_writes_the_file_under_its_hash_and_read_returns_the_body(tmp_path):
    store = Store(tmp_path / "bodies")
    body = Body.of(HTML)
    n = store.put(body)
    path = tmp_path / "bodies" / body.sha256[:2] / f"{body.sha256}.zst"
    assert path.read_bytes() == body.data and n == len(body.data)
    assert store.read(body.sha256) == HTML
    assert store.verify(body.sha256, len(HTML))
    assert not store.verify(body.sha256, len(HTML) + 1)
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temporary left
    assert path.stat().st_mode & 0o777 == 0o644


def test_put_of_a_stored_body_only_touches_it(tmp_path):
    store = Store(tmp_path)
    body = Body.of(HTML)
    store.put(body)
    path = store.path(body.sha256)
    os.utime(path, (1_000_000, 1_000_000))
    streamed = Compressing()
    streamed.update(HTML)
    assert store.put(streamed.finish()) == len(body.data)  # the file there, not the new bytes
    assert path.stat().st_mtime > 1_000_000
    assert path.read_bytes() == body.data


def test_a_failed_write_leaves_no_temporary(tmp_path, monkeypatch):
    store = Store(tmp_path)
    body = Body.of(HTML)

    def boom(*_):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.put(body)
    assert list((tmp_path / body.sha256[:2]).iterdir()) == []
    assert not store.exists(body.sha256)


def test_paths_take_only_a_sha256(tmp_path):
    store = Store(tmp_path)
    for bad in ("../../etc/passwd", "ABC", "a" * 63, "g" * 64):
        with pytest.raises(ValueError):
            store.path(bad)


def test_missing_bodies_are_key_errors(tmp_path):
    store = Store(tmp_path)
    sha = hashlib.sha256(b"x").hexdigest()
    with pytest.raises(MissingBody):
        store.read(sha)
    with pytest.raises(KeyError):
        read_body(None, None, sha)
    with pytest.raises(MissingBody), open_body(store, None, sha):
        pass


def test_read_body_takes_the_column_before_the_file(tmp_path):
    store = Store(tmp_path)
    body = Body.of(HTML)
    store.put(body)
    assert read_body(store, gzip.compress(b"old"), body.sha256) == b"old"
    assert read_body(store, None, body.sha256) == HTML
    assert read_body(None, gzip.compress(HTML), body.sha256) == HTML


@pytest.mark.parametrize("stored", ["gzip", "file", "streamed file"])
def test_open_body_streams_either(tmp_path, stored):
    store = Store(tmp_path)
    if stored == "streamed file":
        c = Compressing()
        c.update(HTML)
        body = c.finish()
    else:
        body = Body.of(HTML)
    store.put(body)
    column = gzip.compress(HTML) if stored == "gzip" else None
    with open_body(store, column, body.sha256) as f:
        text = io.TextIOWrapper(f, encoding="utf-8")  # as facts-fetcher's ixbrl reads it
        assert text.read(100) == HTML[:100].decode()
        assert text.read() == HTML[100:].decode()


def test_from_env(tmp_path):
    assert Store.from_env({}) is None
    assert Store.from_env({"BODIES_DIR": " "}) is None
    store = Store.from_env({"BODIES_DIR": str(tmp_path)})
    assert store is not None and store.root == tmp_path


def test_files_lists_bodies_only(tmp_path):
    store = Store(tmp_path)
    bodies = [Body.of(HTML), Body.of(b"exhibit"), Body.of(compress(b"x"))]
    for b in bodies:
        store.put(b)
    (tmp_path / bodies[0].sha256[:2] / f".{bodies[0].sha256}.1.2.tmp").write_bytes(b"partial")
    (tmp_path / "trash").mkdir()
    (tmp_path / "trash" / f"{'a' * 64}.zst").write_bytes(b"")
    (tmp_path / "lost+found").mkdir()
    assert sorted(h for h, _ in store.files()) == sorted(b.sha256 for b in bodies)
    assert list(Store(tmp_path / "none").files()) == []


def test_trash_untrash_and_purge(tmp_path):
    store = Store(tmp_path)
    body = Body.of(HTML)
    store.put(body)
    now = time.time()
    assert not store.trash(body.sha256, older_than=now - 86400)  # written just now: kept
    os.utime(store.path(body.sha256), (now - 2 * 86400, now - 2 * 86400))
    assert store.trash(body.sha256, older_than=now - 86400)
    assert not store.exists(body.sha256)
    assert not store.trash(body.sha256, older_than=now)  # gone already
    assert store.untrash(body.sha256)
    assert store.read(body.sha256) == HTML
    assert not store.untrash(body.sha256)
    os.utime(store.path(body.sha256), (now - 2 * 86400, now - 2 * 86400))
    store.trash(body.sha256, older_than=now - 86400)
    assert store.purge_trash(older_than=now - 7 * 86400) == 0  # trashed just now
    assert store.purge_trash(older_than=time.time() + 1) == 1
    assert not store.untrash(body.sha256)


def test_remove_temporaries(tmp_path):
    store = Store(tmp_path)
    body = Body.of(HTML)
    store.put(body)
    old = tmp_path / body.sha256[:2] / f".{body.sha256}.1.2.tmp"
    new = tmp_path / body.sha256[:2] / f".{body.sha256}.3.4.tmp"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (1_000_000, 1_000_000))
    assert store.remove_temporaries(older_than=time.time() - 86400) == 1
    assert not old.exists() and new.exists() and store.exists(body.sha256)
