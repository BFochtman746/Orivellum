"""Guard against PowerShell script corruption on Windows.

Windows PowerShell 5.1 decodes .ps1 files WITHOUT a UTF-8 BOM using the
legacy ANSI codepage.  Multi-byte UTF-8 characters (em-dashes, curly
quotes, box-drawing chars) then decode into stray bytes -- 0x94 becomes a
curly double-quote, which the PS parser treats as a string terminator and
the whole script fails with "missing terminator" errors.

This actually happened on the user's machine (setup-windows.ps1, Aug 2026).

Rules enforced for every scripts/**/*.ps1 file:
  1. Must start with the UTF-8 BOM (EF BB BF).
  2. Content after the BOM must be pure ASCII (belt and braces -- the BOM
     alone fixes decoding, but ASCII-only keeps scripts safe even if a
     tool strips the BOM).
  3. No NUL bytes / UTF-16 accidents.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"

PS1_FILES = sorted(SCRIPTS_DIR.rglob("*.ps1"))


def test_powershell_scripts_found():
    """Sanity: the glob must find the known scripts (guards against a
    silent pass if the directory layout changes)."""
    names = {p.name for p in PS1_FILES}
    assert "setup-windows.ps1" in names
    assert "start.ps1" in names


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_ps1_has_utf8_bom(path: Path):
    raw = path.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8), (
        f"{path.name} lacks a UTF-8 BOM. Windows PowerShell 5.1 will decode "
        "it as ANSI and multi-byte characters will corrupt the parse. "
        "Re-save with a UTF-8 BOM (bytes EF BB BF at the start)."
    )


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_ps1_is_ascii_only(path: Path):
    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8) :]

    assert b"\x00" not in raw, (
        f"{path.name} contains NUL bytes -- it was probably saved as "
        "UTF-16. Re-save as UTF-8 with BOM."
    )

    bad: list[str] = []
    for lineno, line in enumerate(raw.split(b"\n"), start=1):
        for col, byte in enumerate(line, start=1):
            if byte > 0x7F:
                try:
                    ch = line.decode("utf-8", errors="replace")
                except Exception:  # pragma: no cover - defensive
                    ch = "?"
                bad.append(f"  line {lineno} col {col}: byte 0x{byte:02X} in {ch[:80]!r}")
                break  # one report per line is enough
    assert not bad, (
        f"{path.name} contains non-ASCII characters. Replace em-dashes with "
        "'-', curly quotes with straight quotes, arrows with '->', etc.:\n" + "\n".join(bad[:20])
    )
