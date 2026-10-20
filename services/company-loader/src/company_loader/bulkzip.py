"""Minimal streaming zip reader for the SEC bulk files.

zipfile.ZipFile keeps a ZipInfo per entry in memory: ~780 MB for submissions.zip's ~1M entries.
This reads the central directory record by record instead, and checks each entry's CRC-32 and
size when it is read.
"""

import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

_EOCD = struct.Struct("<4s4H2LH")             # end of central directory
_EOCD64_LOCATOR = struct.Struct("<4sLQL")
_EOCD64 = struct.Struct("<4sQ2H2L4Q")
_CENTRAL = struct.Struct("<4s4B4HL2L5H2L")    # central directory file header
_LOCAL = struct.Struct("<4s2B4HL2L2H")        # local file header
_MAX32 = 0xFFFFFFFF
_TAIL = 65536 + _EOCD.size                    # EOCD + the longest possible archive comment


class BadZip(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    method: int  # 0 stored, 8 deflated
    crc: int
    compressed_size: int
    size: int
    header_offset: int


def entries(f: BinaryIO) -> Iterator[Entry]:
    """Every entry in central directory order."""
    offset, size = _central_directory(f)
    f.seek(offset)
    end = offset + size
    pos = offset
    while pos < end:
        f.seek(pos)
        head = f.read(_CENTRAL.size)
        rec = _CENTRAL.unpack(head)
        if rec[0] != b"PK\x01\x02":
            raise BadZip(f"bad central directory record at {pos}")
        method, crc, csize, usize = rec[6], rec[9], rec[10], rec[11]
        name_len, extra_len, comment_len, header_offset = rec[12], rec[13], rec[14], rec[18]
        name = f.read(name_len).decode("utf-8")
        extra = f.read(extra_len)
        if _MAX32 in (csize, usize, header_offset):
            usize, csize, header_offset = _zip64_extra(extra, usize, csize, header_offset)
        yield Entry(name, method, crc, csize, usize, header_offset)
        pos += _CENTRAL.size + name_len + extra_len + comment_len


def read(f: BinaryIO, e: Entry) -> bytes:
    """The entry's uncompressed bytes, CRC-checked."""
    f.seek(e.header_offset)
    head = _LOCAL.unpack(f.read(_LOCAL.size))
    if head[0] != b"PK\x03\x04":
        raise BadZip(f"bad local header for {e.name}")
    f.seek(head[10] + head[11], 1)  # local name and extra field
    raw = f.read(e.compressed_size)
    if e.method == 8:
        data = zlib.decompress(raw, -15)
    elif e.method == 0:
        data = raw
    else:
        raise BadZip(f"{e.name}: unsupported compression method {e.method}")
    if len(data) != e.size or zlib.crc32(data) != e.crc:
        raise BadZip(f"{e.name}: CRC or size mismatch")
    return data


def _central_directory(f: BinaryIO) -> tuple[int, int]:
    f.seek(0, 2)
    file_size = f.tell()
    f.seek(max(0, file_size - _TAIL))
    tail = f.read()
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise BadZip("no end of central directory record")
    _, _, _, _, count, size, offset, _ = _EOCD.unpack_from(tail, i)
    j = i - _EOCD64_LOCATOR.size
    if j >= 0 and tail[j:j + 4] == b"PK\x06\x07":
        _, _, eocd64_offset, _ = _EOCD64_LOCATOR.unpack_from(tail, j)
        f.seek(eocd64_offset)
        rec = _EOCD64.unpack(f.read(_EOCD64.size))
        if rec[0] != b"PK\x06\x06":
            raise BadZip("bad zip64 end of central directory record")
        size, offset = rec[8], rec[9]
    return offset, size


def _zip64_extra(extra: bytes, usize: int, csize: int, offset: int) -> tuple[int, int, int]:
    """Replace 0xFFFFFFFF placeholders from the zip64 extra field, in its fixed order."""
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack_from("<2H", extra, pos)
        if tag == 1:
            values = iter(struct.unpack_from(f"<{length // 8}Q", extra, pos + 4))
            if usize == _MAX32:
                usize = next(values)
            if csize == _MAX32:
                csize = next(values)
            if offset == _MAX32:
                offset = next(values)
            break
        pos += 4 + length
    return usize, csize, offset
