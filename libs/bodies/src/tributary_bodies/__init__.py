"""Document bodies as files: the storage diet (migration 150).

A stored document or exhibit body is one zstd file named by the SHA-256 of its uncompressed bytes
(the row's ``content_sha256``)::

    <root>/<sha256[:2]>/<sha256>.zst

Postgres keeps the metadata: the hash, the raw size (``size_bytes``) and the file's size
(``body_bytes``). A row whose ``content_gzip`` is still set predates the move (the parser
catch-up's ``bodies`` kind empties the column, oldest filing first) and is read from that
column; `read_body` and `open_body` take either.

Writing: `Body.of(raw)` (or `Compressing` for a body that streams in), then `Store.put` BEFORE
the transaction that inserts the row. The file goes to a temporary name, is fsynced, renamed into
place and its directory fsynced, so a committed row always has its file; a body already there
(same hash) is only touched, which keeps it out of the orphan sweep. A file whose row never
committed is an orphan: harmless, moved to ``trash/`` after a day and deleted a week after that
(`Store.trash`, `Store.purge_trash`; the sweep itself is services/parser's ``bodies.py``).

zstd level 12 with a frame checksum: on 635 sampled hermes documents 0.72 of the stored gzip-9
bytes, decompressed about 3x faster than gzip (migration 150's header has the measurements).
"""

import gzip
import hashlib
import io
import os
import re
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import zstandard

ENV = "BODIES_DIR"
LEVEL = 12
TRASH = "trash"
CHUNK = 1 << 20

_HEX = re.compile(r"[0-9a-f]{64}")
_FAN = re.compile(r"[0-9a-f]{2}")
_local = threading.local()


class MissingBody(KeyError):
    """A row's body is a file that isn't in the store (or no store is configured). A KeyError,
    as for a missing row: the parsers log and skip the document."""


def _compressor() -> zstandard.ZstdCompressor:
    c = getattr(_local, "compressor", None)
    if c is None:
        c = _local.compressor = zstandard.ZstdCompressor(level=LEVEL, write_checksum=True)
    return c


def _decompressor() -> zstandard.ZstdDecompressor:
    d = getattr(_local, "decompressor", None)
    if d is None:
        d = _local.decompressor = zstandard.ZstdDecompressor()
    return d


def compress(raw: bytes) -> bytes:
    return _compressor().compress(raw)


def decompress(data: bytes) -> bytes:
    """One zstd frame, with its content size in the header (`compress`) or without (a body that
    streamed in, `Compressing`)."""
    if zstandard.frame_content_size(data) >= 0:
        return _decompressor().decompress(data)
    return _decompressor().decompressobj().decompress(data)


@dataclass(frozen=True)
class Body:
    """A body as stored: zstd bytes, plus SHA-256 and size of the uncompressed bytes."""
    data: bytes
    sha256: str
    size: int

    @classmethod
    def of(cls, raw: bytes) -> "Body":
        return cls(compress(raw), hashlib.sha256(raw).hexdigest(), len(raw))


class Compressing:
    """A body that arrives in chunks (the crawl's downloads), compressed and hashed as it streams
    in: only the compressed bytes are held."""

    def __init__(self) -> None:
        self._obj = zstandard.ZstdCompressor(level=LEVEL, write_checksum=True).compressobj()
        self._sha = hashlib.sha256()
        self._parts: list[bytes] = []
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self.size += len(chunk)
        self._sha.update(chunk)
        self._parts.append(self._obj.compress(chunk))

    def finish(self) -> Body:
        self._parts.append(self._obj.flush())
        return Body(b"".join(self._parts), self._sha.hexdigest(), self.size)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    """The body files under `root` (BODIES_DIR: /bodies in the pods, a PersistentVolumeClaim)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Store | None":
        root = env.get(ENV, "").strip()
        return cls(root) if root else None

    def path(self, sha256: str) -> Path:
        if not _HEX.fullmatch(sha256):
            raise ValueError(f"not a SHA-256: {sha256!r}")
        return self.root / sha256[:2] / f"{sha256}.zst"

    def exists(self, sha256: str) -> bool:
        return self.path(sha256).is_file()

    def put(self, body: Body) -> int:
        """Store a body (idempotent by hash) durably; returns the file's size (body_bytes)."""
        path = self.path(body.sha256)
        try:
            os.utime(path)  # stored already: a fresh mtime keeps it out of the sweep
            return path.stat().st_size
        except FileNotFoundError:
            pass
        directory = path.parent
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            _fsync_dir(self.root)
        # a unique temporary name (O_EXCL): two pods storing the same body at once (both PID 1)
        # must not write into one file
        fd, name = tempfile.mkstemp(prefix=f".{body.sha256}.", suffix=".tmp", dir=directory)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o644)
                f.write(body.data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            with suppress(FileNotFoundError):
                tmp.unlink()
            raise
        _fsync_dir(directory)
        return len(body.data)

    def read(self, sha256: str) -> bytes:
        """The uncompressed body."""
        try:
            data = self.path(sha256).read_bytes()
        except FileNotFoundError:
            raise MissingBody(f"no body file {sha256}") from None
        return decompress(data)

    @contextmanager
    def open(self, sha256: str) -> Iterator[BinaryIO]:
        """The uncompressed body as a stream, for documents too large to hold twice."""
        try:
            f = self.path(sha256).open("rb")
        except FileNotFoundError:
            raise MissingBody(f"no body file {sha256}") from None
        with f, zstandard.ZstdDecompressor().stream_reader(f) as reader:
            yield io.BufferedReader(reader, CHUNK)  # type: ignore[arg-type]

    def verify(self, sha256: str, size: int) -> bool:
        """The file decompresses to `size` bytes with this SHA-256 (read back from disk)."""
        digest, n = hashlib.sha256(), 0
        with self.open(sha256) as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)
                n += len(chunk)
        return n == size and digest.hexdigest() == sha256

    # the sweep (services/parser bodies.py) ----------------------------------------------------

    def files(self) -> Iterator[tuple[str, float]]:
        """(sha256, mtime) of every stored body; temporary files and the trash left out."""
        if not self.root.is_dir():
            return
        for d in os.scandir(self.root):
            if not (_FAN.fullmatch(d.name) and d.is_dir(follow_symlinks=False)):
                continue
            for e in os.scandir(d.path):
                if e.name.endswith(".zst") and _HEX.fullmatch(e.name[:-4]):
                    with suppress(FileNotFoundError):
                        yield e.name[:-4], e.stat(follow_symlinks=False).st_mtime

    def remove_temporaries(self, older_than: float) -> int:
        """Delete temporary files a crashed writer left (modified before `older_than`)."""
        n = 0
        if not self.root.is_dir():
            return n
        for d in os.scandir(self.root):
            if not (_FAN.fullmatch(d.name) and d.is_dir(follow_symlinks=False)):
                continue
            for e in os.scandir(d.path):
                if e.name.startswith(".") and e.name.endswith(".tmp"):
                    with suppress(FileNotFoundError):
                        if e.stat(follow_symlinks=False).st_mtime < older_than:
                            os.unlink(e.path)
                            n += 1
        return n

    def _trash_path(self, sha256: str) -> Path:
        self.path(sha256)  # validates
        return self.root / TRASH / f"{sha256}.zst"

    def trash(self, sha256: str, older_than: float, now: float | None = None) -> bool:
        """Move an orphan to the trash unless it was modified since `older_than` (a writer that
        stored the same body since then touched it), stamped `now`. True when moved."""
        path, dest = self.path(sha256), self._trash_path(sha256)
        try:
            if path.stat().st_mtime >= older_than:
                return False
            dest.parent.mkdir(exist_ok=True)
            os.replace(path, dest)
        except FileNotFoundError:
            return False
        os.utime(dest, None if now is None else (now, now))  # purge_trash counts from here
        return True

    def untrash(self, sha256: str) -> bool:
        """Put a trashed body back (a row references it after all). True when restored."""
        src, path = self._trash_path(sha256), self.path(sha256)
        if not src.is_file():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, path)
        os.utime(path)
        return True

    def purge_trash(self, older_than: float) -> int:
        """Delete trashed bodies trashed before `older_than`."""
        n = 0
        trash = self.root / TRASH
        if not trash.is_dir():
            return n
        for e in os.scandir(trash):
            with suppress(FileNotFoundError):
                if e.stat(follow_symlinks=False).st_mtime < older_than:
                    os.unlink(e.path)
                    n += 1
        return n


def read_body(store: Store | None, content_gzip: bytes | None, sha256: str) -> bytes:
    """A row's uncompressed body: its content_gzip while it has one (before the move), else its
    file."""
    if content_gzip is not None:
        return gzip.decompress(content_gzip)
    if store is None:
        raise MissingBody(f"body {sha256} is a file and {ENV} is not set")
    return store.read(sha256)


@contextmanager
def open_body(store: Store | None, content_gzip: bytes | None,
              sha256: str) -> Iterator[BinaryIO]:
    """`read_body` as a stream."""
    if content_gzip is not None:
        with gzip.GzipFile(fileobj=io.BytesIO(content_gzip)) as f:
            yield f  # type: ignore[misc]
        return
    if store is None:
        raise MissingBody(f"body {sha256} is a file and {ENV} is not set")
    with store.open(sha256) as f:
        yield f
