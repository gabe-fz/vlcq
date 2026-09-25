"""Persistent file identity; Darwin device numbers are only mount-local IDs."""
from __future__ import annotations

import ctypes
import errno
import os
import sys
import uuid
from pathlib import Path
from typing import NamedTuple


class FileIdentity(NamedTuple):
    device: int
    inode: int
    size: int
    mtime_ns: int
    volume_uuid: str | None = None

    def matches(self, current: FileIdentity) -> bool:
        # Once pinned, a volume UUID is required: never fall back to a reused
        # device number when UUID discovery fails or a different disk appears.
        same_volume = (
            self.volume_uuid == current.volume_uuid
            if self.volume_uuid is not None
            else self.device == current.device
        )
        return same_volume and self[1:4] == current[1:4]


class _AttrList(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    ]


def volume_uuid(path: Path) -> str | None:
    """Read ATTR_VOL_UUID without spawning diskutil or touching media contents.

    Unsupported filesystems/platforms keep the conservative device-based identity.
    No device-keyed cache: device numbers can be reused after an unmount.
    """
    if sys.platform != "darwin":
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    getattrlist = libc.getattrlist
    getattrlist.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(_AttrList), ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_ulong,
    ]
    getattrlist.restype = ctypes.c_int
    # sys/attr.h: ATTR_BIT_MAP_COUNT, ATTR_VOL_INFO | ATTR_VOL_UUID.
    attrs = _AttrList(5, 0, 0, 0x80040000, 0, 0, 0)
    buffer = ctypes.create_string_buffer(20)
    if getattrlist(os.fsencode(path), ctypes.byref(attrs), buffer, len(buffer), 0) != 0:
        return None
    if int.from_bytes(buffer.raw[:4], sys.byteorder) != 20:
        return None
    value = uuid.UUID(bytes=buffer.raw[4:20])
    return str(value) if value.int else None


def file_identity(path: Path, stat: os.stat_result | None = None) -> FileIdentity:
    stat = path.stat() if stat is None else stat
    volume = volume_uuid(path)
    after = path.stat()
    before_key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    after_key = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_key != after_key:
        raise OSError(errno.ESTALE, "file changed during identity validation", str(path))
    return FileIdentity(*before_key, volume)
