"""
WR-00 Forensic Inventory
========================

Recursively expands an archive (including nested ZIPs), computes a SHA-256 for
every real payload, and captures metadata. This is the evidentiary foundation
the specification demands before any other work begins:

    "Create a read-only evidence snapshot of the supplied ZIP and record its
     SHA-256. Do not rewrite originals."  (spec sec. 12.1)

Design rules:
  * The original archive is NEVER modified. We only read it.
  * macOS resource-fork noise (__MACOSX/, ._* AppleDouble files, .DS_Store)
    is recorded as PACKAGING metadata, not counted as a real payload.
  * Nested ZIPs are expanded in-memory and their members enumerated, so a
    file's logical path reflects the full containment chain, e.g.
        WRITING_ARCHITECT.zip!Module writing.zip!WRITING_SYSTEM/CH01.docx
  * Depth is bounded to prevent zip-bomb / cyclic expansion.

No third-party dependencies. Python 3.9+.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterator, Optional

# ---- classification of "noise" that is not a real intellectual payload ----
_MAC_NOISE_PREFIXES = ("__MACOSX/",)
_MAC_NOISE_BASENAMES = (".DS_Store",)
MAX_NEST_DEPTH = 6            # hard stop against cyclic / bomb nesting
NEST_SEPARATOR = "!"          # separates containment levels in logical paths


def _is_apple_double(name: str) -> bool:
    base = posixpath.basename(name)
    return base.startswith("._")


def _is_packaging_noise(name: str) -> bool:
    if any(name.startswith(p) for p in _MAC_NOISE_PREFIXES):
        return True
    if _is_apple_double(name):
        return True
    if posixpath.basename(name) in _MAC_NOISE_BASENAMES:
        return True
    return False


def _looks_like_zip(name: str, blob: bytes) -> bool:
    # Trust the magic bytes, not the extension — several archive members are
    # DOCX/ZIP payloads carrying no extension at all (e.g. SOVEREIGN_MASTER_v1.4).
    if len(blob) < 4:
        return False
    if blob[:4] not in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return False
    # A .docx/.xlsx/.pptx is *also* a zip. We only want to recurse into
    # *container* zips, not office documents, so exclude the OOXML family by
    # sniffing for the tell-tale [Content_Types].xml member.
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = z.namelist()
        if "[Content_Types].xml" in names:
            return False          # it's an office doc — treat as a leaf payload
        return True
    except zipfile.BadZipFile:
        return False


@dataclass
class FileRecord:
    """One real payload discovered anywhere in the archive tree."""
    logical_path: str            # full containment chain
    display_path: str            # human-facing path (last container segment)
    container: str               # the immediate parent container
    depth: int                   # nesting depth (0 = top-level archive member)
    size: int
    sha256: str
    ext: str                     # lower-cased extension, or "" if none
    modified: Optional[str]      # ISO timestamp from zip metadata if available
    kind: str                    # "payload" | "packaging"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Inventory:
    archive_path: str
    archive_sha256: str
    captured_utc: str
    records: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def payloads(self) -> list:
        return [r for r in self.records if r.kind == "payload"]

    @property
    def packaging(self) -> list:
        return [r for r in self.records if r.kind == "packaging"]

    def as_dict(self) -> dict:
        return {
            "archive_path": self.archive_path,
            "archive_sha256": self.archive_sha256,
            "captured_utc": self.captured_utc,
            "counts": {
                "total_records": len(self.records),
                "payloads": len(self.payloads),
                "packaging": len(self.packaging),
                "errors": len(self.errors),
            },
            "records": [r.as_dict() for r in self.records],
            "errors": self.errors,
        }


def _sha256(blob: bytes) -> str:
    h = hashlib.sha256()
    h.update(blob)
    return h.hexdigest()


def _iter_zip_bytes(
    blob: bytes,
    container: str,
    depth: int,
    errors: list,
) -> Iterator[FileRecord]:
    """Yield FileRecords for every member of a zip given as raw bytes."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        errors.append({"container": container, "error": f"bad zip: {exc}"})
        return

    with zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue  # directory entry, not a payload
            logical = f"{container}{NEST_SEPARATOR}{name}"
            try:
                member = zf.read(info)
            except Exception as exc:  # noqa: BLE001 - want the reason recorded
                errors.append({"path": logical, "error": f"read failed: {exc}"})
                continue

            kind = "packaging" if _is_packaging_noise(name) else "payload"
            ext = posixpath.splitext(name)[1].lower().lstrip(".")
            try:
                dt = datetime(*info.date_time).replace(tzinfo=timezone.utc).isoformat()
            except Exception:  # noqa: BLE001
                dt = None

            yield FileRecord(
                logical_path=logical,
                display_path=name,
                container=container,
                depth=depth,
                size=len(member),
                sha256=_sha256(member),
                ext=ext,
                modified=dt,
                kind=kind,
            )

            # Recurse into *container* zips only (not office docs) and only
            # for real payloads within the depth budget.
            if (
                kind == "payload"
                and depth < MAX_NEST_DEPTH
                and _looks_like_zip(name, member)
            ):
                yield from _iter_zip_bytes(member, logical, depth + 1, errors)


def build_inventory(archive_path: str) -> Inventory:
    """Read-only recursive inventory of an archive on disk."""
    with open(archive_path, "rb") as fh:
        blob = fh.read()
    inv = Inventory(
        archive_path=archive_path,
        archive_sha256=_sha256(blob),
        captured_utc=datetime.now(timezone.utc).isoformat(),
    )
    root = posixpath.basename(archive_path)
    for rec in _iter_zip_bytes(blob, root, 0, inv.errors):
        inv.records.append(rec)
    return inv
