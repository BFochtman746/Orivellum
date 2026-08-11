"""Build a representative EPUB with the real builder and validate it.

CI gate for B15: the accessible-EPUB builder's output must pass BOTH
EPUBCheck (spec conformance) and Ace by DAISY (accessibility). A failure
in either exits nonzero — the job is the proof that what the app ships
is clean.

Usage:
    uv run python scripts/validate_epub.py --epubcheck /path/to/epubcheck.jar
    (Ace is invoked via `npx @daisy/ace`; requires Node.)

The sample book deliberately exercises every feature the real pipeline
uses: front/back matter, epigraphs, Hebrew-script runs (xml:lang), a
print-derived page-list, and schema.org accessibility metadata.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orivellum.capabilities.finishing import epub_a11y, typeset  # noqa: E402


def _sample_book() -> bytes:
    """Render the print PDF first so the page-list references real pages."""
    prose = (
        "The night held its breath while the city slept below. "
        "Somewhere beyond the walls a watchman called the hour. " * 30
    )
    chapters = [
        {
            "number": i,
            "seq": i,
            "title": f"The {name}",
            "text": "\n\n".join(prose.strip() for _ in range(8)),
            "epigraph_text": "Dust remembers the shape of the wind.\n— Anonymous" if i == 1 else "",
        }
        for i, name in ((1, "Storm"), (2, "Calm"), (3, "Reckoning"))
    ]
    chapters[1]["text"] += (
        "\n\nAt the gate the old scribe wrote \u05e9\u05dc\u05d5\u05dd \u05e2"
        "\u05dc\u05d9\u05db\u05dd and sealed the letter."
    )
    headers = {i: f"Chapter {i}" for i in (1, 2, 3)}
    book = {
        "title": "Validation Sample",
        "author_name": "Orivellum CI",
        "has_front": True,
        "has_back": True,
    }
    style = {
        "trim": "6x9",
        "body_font": "Garamond",
        "heading_font": "Helvetica",
        "body_size": "11pt",
        "leading": "15pt",
        "chapter_style": "arabic",
        "epigraphs": "on",
    }
    pdf = typeset.render_print_pdf(book, style, chapters, headers)
    print(f"print render: {pdf['actual_pages']} pages, {len(pdf['page_map'])} anchors")
    return epub_a11y.build_accessible_epub(
        title=book["title"],
        author=book["author_name"],
        book_id="validation-sample",
        chapters=chapters,
        chapter_headers=headers,
        page_map=pdf["page_map"],
        chapter_pages=pdf["chapter_pages"],
        has_front=True,
        has_back=True,
    )


def _run_epubcheck(jar: str, epub: Path) -> bool:
    proc = subprocess.run(
        ["java", "-jar", jar, str(epub)], capture_output=True, text=True, timeout=300
    )
    print("── EPUBCheck ──")
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def _run_ace(epub: Path, outdir: Path) -> bool:
    npx = shutil.which("npx")
    if not npx:
        print("Ace: npx not found — Node is required.", file=sys.stderr)
        return False
    proc = subprocess.run(
        [npx, "--yes", "@daisy/ace", "-f", "-s", "-o", str(outdir), str(epub)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    print("── Ace by DAISY ──")
    print((proc.stdout.strip() or proc.stderr.strip())[-2000:])
    report = outdir / "report.json"
    if not report.exists():
        print("Ace produced no report — treating as failure.", file=sys.stderr)
        return False
    data = json.loads(report.read_text())
    # earl:result carries the overall outcome; any violation fails the gate.
    outcome = data.get("earl:result", {}).get("earl:outcome") or data.get("earl:outcome") or ""
    assertions = data.get("assertions") or []
    print(f"Ace outcome: {outcome!r}, {len(assertions)} document assertion group(s)")
    return outcome == "pass"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epubcheck", required=True, help="path to epubcheck.jar")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        epub = Path(td) / "sample.epub"
        epub.write_bytes(_sample_book())
        print(f"built {epub} ({epub.stat().st_size} bytes)")

        ok_check = _run_epubcheck(args.epubcheck, epub)
        ok_ace = _run_ace(epub, Path(td) / "ace-report")

    print(f"epubcheck={'clean' if ok_check else 'FAILED'} ace={'clean' if ok_ace else 'FAILED'}")
    return 0 if (ok_check and ok_ace) else 1


if __name__ == "__main__":
    raise SystemExit(main())
