"""Stockage des fichiers déposés (docs/02_ARCHITECTURE.md § Stockage des fichiers).

Noms internes = UUID ; le nom d'origine n'est stocké qu'en base. Le type réel est déterminé
par les octets magiques, jamais par l'extension (docs/05_SECURITE_RGPD.md § Fichiers).
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

CHUNK = 1024 * 1024

# Phase 0 : PDF, JPEG, PNG. HEIC (via idvision), TIFF, WEBP, ZIP : phase 1.
MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)


class StorageError(Exception):
    """Refus d'un fichier, avec un code stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StoredFile:
    path: Path
    sha256: str
    size_bytes: int
    mime: str


def detect_type(head: bytes) -> tuple[str, str] | None:
    """(mime, extension) d'après les premiers octets, ou None si format non pris en charge."""
    for magic, mime, ext in MAGIC:
        if head.startswith(magic):
            return mime, ext
    return None


def clean_original_name(name: str | None) -> str:
    """Nom d'origine sans chemin (le navigateur peut envoyer `C:\\…\\x.pdf`), 255 caractères max."""
    raw = (name or "").replace("\x00", "")
    base = PureWindowsPath(PurePosixPath(raw).name).name.strip()
    return (base or "sans-nom")[:255]


def file_dir(data_dir: Path, batch_id: str, file_id: str) -> Path:
    return data_dir / "batches" / batch_id / "files" / file_id


def save_upload(src: BinaryIO, dest_dir: Path, max_bytes: int) -> StoredFile:
    """Copie le flux dans `dest_dir/original.<ext>` en calculant l'empreinte SHA-256.

    En cas de refus, rien ne reste sur disque.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / "original.part"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with tmp.open("wb") as out:
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise StorageError("FILE_TOO_LARGE")
                if len(head) < 16:
                    head = (head + chunk)[:16]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise StorageError("EMPTY_FILE")
        detected = detect_type(head)
        if detected is None:
            raise StorageError("UNSUPPORTED_FORMAT")
        mime, ext = detected
        final = dest_dir / f"original.{ext}"
        tmp.replace(final)
        return StoredFile(final, digest.hexdigest(), size, mime)
    except BaseException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
