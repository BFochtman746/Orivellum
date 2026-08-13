#!/usr/bin/env python3
"""WP0 UI baseline metrics — collect and regression-check.

Captures the frozen-baseline numbers the UI convergence project (WP0–WP7)
gates on, and fails when they regress:

* **route_count** — `<Route path=` entries in App.tsx. Must never DECREASE
  (every existing deep link must keep working; additions are fine).
* **hex_literal_count** — hard-coded hex color literals in UI source outside
  the design-token allowlist. Must never INCREASE (WP-level work drives it
  down toward zero).
* **gzip bundle sizes** — total gzip JS and CSS emitted by the production
  build. Must not grow more than GROWTH_TOLERANCE (5%) over the baseline.

Usage (from the repo root):
    uv run python scripts/ui_baseline_metrics.py collect   # write baseline/metrics.json
    uv run python scripts/ui_baseline_metrics.py check     # exit 1 on regression

Both modes require a fresh production build:
    cd artifacts/orivellum-ui && NODE_ENV=production pnpm run build
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = REPO_ROOT / "artifacts" / "orivellum-ui"
UI_SRC = UI_ROOT / "src"
APP_TSX = UI_SRC / "App.tsx"
DIST_ASSETS = UI_ROOT / "dist" / "public" / "assets"
BASELINE_FILE = REPO_ROOT / "baseline" / "metrics.json"

# Design-token files where hex literals are the point (single source of truth).
HEX_ALLOWLIST = {
    UI_SRC / "index.css",
    UI_SRC / "styles" / "gd-tokens.css",
}

HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
SRC_EXTENSIONS = {".ts", ".tsx", ".css"}
GROWTH_TOLERANCE = 0.05  # 5%
# Strip the content hash vite inserts: "index-C8LyZ_Nb.js" -> "index.js"
CHUNK_HASH_RE = re.compile(r"-[A-Za-z0-9_]{8}(?=\.[a-z]+$)")


def count_routes() -> int:
    return len(re.findall(r"<Route path=", APP_TSX.read_text(encoding="utf-8")))


def count_hex_literals() -> dict:
    total = 0
    allowlisted = 0
    offenders: dict[str, int] = {}
    for path in sorted(UI_SRC.rglob("*")):
        if not path.is_file() or path.suffix not in SRC_EXTENSIONS:
            continue
        n = len(HEX_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
        if n == 0:
            continue
        total += n
        if path in HEX_ALLOWLIST:
            allowlisted += n
        else:
            offenders[str(path.relative_to(REPO_ROOT))] = n
    return {
        "total": total,
        "allowlisted": allowlisted,
        "counted": total - allowlisted,
        "files": offenders,
    }


def collect_assets() -> dict:
    if not DIST_ASSETS.is_dir():
        print(
            f"ERROR: {DIST_ASSETS.relative_to(REPO_ROOT)} not found — run a "
            "production build first:\n"
            "  cd artifacts/orivellum-ui && NODE_ENV=production pnpm run build",
            file=sys.stderr,
        )
        sys.exit(2)
    chunks = []
    gzip_js_total = 0
    gzip_css_total = 0
    for path in sorted(DIST_ASSETS.iterdir()):
        if not path.is_file() or path.suffix not in {".js", ".css"}:
            continue
        raw = path.read_bytes()
        gz = len(gzip.compress(raw, 9))
        chunks.append(
            {
                "chunk": CHUNK_HASH_RE.sub("", path.name),
                "file": path.name,
                "raw_bytes": len(raw),
                "gzip_bytes": gz,
            }
        )
        if path.suffix == ".js":
            gzip_js_total += gz
        else:
            gzip_css_total += gz
    # Fail closed: an empty/stale assets dir must never pass the size gate.
    if gzip_js_total == 0 or gzip_css_total == 0:
        print(
            f"ERROR: no JS/CSS assets found in {DIST_ASSETS.relative_to(REPO_ROOT)} — "
            "stale or incomplete build? Rebuild before collecting/checking:\n"
            "  cd artifacts/orivellum-ui && NODE_ENV=production pnpm run build",
            file=sys.stderr,
        )
        sys.exit(2)
    return {
        "chunks": chunks,
        "gzip_js_total": gzip_js_total,
        "gzip_css_total": gzip_css_total,
    }


def collect() -> dict:
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "route_count": count_routes(),
        "hex_literals": count_hex_literals(),
        "assets": collect_assets(),
    }


def cmd_collect() -> int:
    metrics = collect()
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"Baseline written to {BASELINE_FILE.relative_to(REPO_ROOT)}")
    print(f"  routes:        {metrics['route_count']}")
    print(f"  hex literals:  {metrics['hex_literals']['counted']} (outside token files)")
    print(f"  gzip JS total: {metrics['assets']['gzip_js_total']:,} bytes")
    print(f"  gzip CSS total:{metrics['assets']['gzip_css_total']:,} bytes")
    return 0


def cmd_check() -> int:
    if not BASELINE_FILE.is_file():
        print(f"ERROR: no baseline at {BASELINE_FILE.relative_to(REPO_ROOT)}; run `collect` first.", file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    if not baseline["assets"]["gzip_js_total"] or not baseline["assets"]["gzip_css_total"]:
        print("ERROR: baseline has zero asset totals — recollect from a real build.", file=sys.stderr)
        return 2
    current = collect()
    failures: list[str] = []

    if current["route_count"] < baseline["route_count"]:
        failures.append(
            f"route_count decreased: {baseline['route_count']} -> {current['route_count']} "
            "(every baseline deep link must keep working)"
        )
    if current["hex_literals"]["counted"] > baseline["hex_literals"]["counted"]:
        failures.append(
            f"hex literal count increased: {baseline['hex_literals']['counted']} -> "
            f"{current['hex_literals']['counted']} (use design tokens, not raw hex)"
        )
    for key, label in (("gzip_js_total", "gzip JS"), ("gzip_css_total", "gzip CSS")):
        base_v = baseline["assets"][key]
        cur_v = current["assets"][key]
        if base_v and cur_v > base_v * (1 + GROWTH_TOLERANCE):
            failures.append(
                f"{label} grew >{GROWTH_TOLERANCE:.0%}: {base_v:,} -> {cur_v:,} bytes"
            )

    print(
        f"routes {baseline['route_count']} -> {current['route_count']} | "
        f"hex {baseline['hex_literals']['counted']} -> {current['hex_literals']['counted']} | "
        f"gzip JS {baseline['assets']['gzip_js_total']:,} -> {current['assets']['gzip_js_total']:,} | "
        f"gzip CSS {baseline['assets']['gzip_css_total']:,} -> {current['assets']['gzip_css_total']:,}"
    )
    if failures:
        print("\nUI baseline regression(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK — no regressions against the recorded baseline.")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "collect":
        return cmd_collect()
    if mode == "check":
        return cmd_check()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
