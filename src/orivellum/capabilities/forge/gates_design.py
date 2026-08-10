"""Forge design-standards gates — accessibility, performance, and design quality.

Deterministic checks inspired by WCAG 2.2 AA and Core Web Vitals guidance,
applied to the static build output. All findings surface as "conditional"
(never "blocked") so the REPAIR loop can act on them without bricking a build.
"""

from __future__ import annotations

import pathlib
import re
from html.parser import HTMLParser

_MAX_DETAILS = 6

# ── WCAG contrast math ─────────────────────────────────────────────────────────


def _srgb_channel(v: float) -> float:
    v = v / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float | None:
    """WCAG relative luminance for a #rgb/#rrggbb color; None if unparseable."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def contrast_ratio(fg: str, bg: str) -> float | None:
    """WCAG contrast ratio between two hex colors (1..21); None if unparseable."""
    lf, lb = relative_luminance(fg), relative_luminance(bg)
    if lf is None or lb is None:
        return None
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


# ── HTML page scanner (stdlib parser — handles quoted/unquoted attributes) ────


class _PageScan(HTMLParser):
    """Single-pass element/attribute collector used by the a11y and
    performance gates. Uses html.parser so single-quoted and unquoted
    attributes are handled correctly (regexes were false-flagging them)."""

    _SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang: str | None = None  # None = no <html> tag seen
        self.viewport = False
        self.description = False
        self.h1_count = 0
        self.has_main = False
        self.imgs: list[dict[str, str]] = []
        self.clicky = 0
        self.controls: list[dict[str, str]] = []  # visible form controls only
        self.label_for: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.html_lang = a.get("lang", "")
        elif tag == "meta":
            self._on_meta(a)
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "main":
            self.has_main = True
        elif tag == "img":
            self.imgs.append(a)
        elif tag in ("div", "span") and "onclick" in a:
            self.clicky += 1
        else:
            self._on_form_tag(tag, a)

    def _on_meta(self, a: dict[str, str]) -> None:
        n = a.get("name", "").lower()
        if n == "viewport":
            self.viewport = True
        elif n == "description":
            self.description = True

    def _on_form_tag(self, tag: str, a: dict[str, str]) -> None:
        if tag in ("input", "select", "textarea"):
            if tag == "input" and a.get("type", "").lower() in self._SKIP_INPUT_TYPES:
                return
            self.controls.append(a)
        elif tag == "label" and a.get("for"):
            self.label_for.add(a["for"])


def _scan_page(content: str) -> _PageScan:
    scan = _PageScan()
    try:
        scan.feed(content)
        scan.close()
    except Exception:  # noqa: BLE001 - malformed HTML: report what was collected
        pass
    return scan


# ── Accessibility gate ─────────────────────────────────────────────────────────


def _page_a11y_problems(name: str, scan: _PageScan) -> list[str]:
    problems: list[str] = []
    if scan.html_lang is not None and not scan.html_lang:
        problems.append(f"{name}: <html> has no lang attribute")
    if not scan.viewport:
        problems.append(f"{name}: missing viewport meta tag")
    if scan.h1_count != 1:
        problems.append(f"{name}: {scan.h1_count} <h1> headings (must be exactly 1)")
    if not scan.has_main:
        problems.append(f"{name}: missing <main> landmark")
    no_alt = sum(1 for img in scan.imgs if "alt" not in img)
    if no_alt:
        problems.append(f"{name}: {no_alt} <img> without alt text")
    if scan.clicky:
        problems.append(f"{name}: clickable <div>/<span> — use <button> or <a>")
    unlabeled = sum(
        1
        for c in scan.controls
        if not c.get("aria-label")
        and not c.get("aria-labelledby")
        and c.get("id", "") not in scan.label_for
    )
    if unlabeled:
        problems.append(f"{name}: {unlabeled} form control(s) without <label for> or aria-label")
    return problems


def gate_a11y(build_dir: pathlib.Path) -> dict:
    """WCAG 2.2 AA structural checks on every generated page."""
    pages = sorted(build_dir.glob("**/*.html"))
    if not pages:
        return {"name": "a11y", "status": "conditional", "detail": "No HTML pages found."}
    problems: list[str] = []
    for page in pages:
        scan = _scan_page(page.read_text(encoding="utf-8", errors="replace"))
        problems.extend(_page_a11y_problems(page.name, scan))
    if problems:
        extra = f" (+{len(problems) - _MAX_DETAILS} more)" if len(problems) > _MAX_DETAILS else ""
        return {
            "name": "a11y",
            "status": "conditional",
            "detail": "; ".join(problems[:_MAX_DETAILS]) + extra,
        }
    return {
        "name": "a11y",
        "status": "passed",
        "detail": f"Accessibility checks OK ({len(pages)} page(s)).",
    }


# ── Performance gate ───────────────────────────────────────────────────────────


def _page_perf_problems(name: str, scan: _PageScan) -> list[str]:
    problems: list[str] = []
    missing_dims = sum(1 for i in scan.imgs if "width" not in i or "height" not in i)
    if missing_dims:
        problems.append(f"{name}: {missing_dims} <img> without width/height (CLS risk)")
    if len(scan.imgs) > 1:
        not_lazy = sum(1 for i in scan.imgs[1:] if i.get("loading", "").lower() != "lazy")
        if not_lazy:
            problems.append(f"{name}: {not_lazy} below-the-fold image(s) not lazy-loaded")
    if not scan.description:
        problems.append(f"{name}: missing meta description")
    return problems


def gate_performance(build_dir: pathlib.Path) -> dict:
    """Core-Web-Vitals-style static checks (CLS, lazy loading, metadata)."""
    pages = sorted(build_dir.glob("**/*.html"))
    if not pages:
        return {"name": "performance", "status": "conditional", "detail": "No HTML pages found."}
    problems: list[str] = []
    for page in pages:
        scan = _scan_page(page.read_text(encoding="utf-8", errors="replace"))
        problems.extend(_page_perf_problems(page.name, scan))
    if problems:
        extra = f" (+{len(problems) - _MAX_DETAILS} more)" if len(problems) > _MAX_DETAILS else ""
        return {
            "name": "performance",
            "status": "conditional",
            "detail": "; ".join(problems[:_MAX_DETAILS]) + extra,
        }
    return {
        "name": "performance",
        "status": "passed",
        "detail": f"Performance checks OK ({len(pages)} page(s)).",
    }


# ── Contrast gate (token palette) ──────────────────────────────────────────────

_TOKEN_RE = re.compile(r"--color-([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\b", re.IGNORECASE)


def gate_contrast(build_dir: pathlib.Path) -> dict:
    """Check text/background token pairs meet the 4.5:1 WCAG AA ratio."""
    tokens_file = build_dir / "design-tokens.css"
    if not tokens_file.exists():
        return {"name": "contrast", "status": "conditional", "detail": "design-tokens.css missing."}
    tokens = {
        m.group(1).lower(): m.group(2)
        for m in _TOKEN_RE.finditer(tokens_file.read_text(encoding="utf-8", errors="replace"))
    }
    text = next((tokens[k] for k in ("text", "foreground", "body") if k in tokens), None)
    bg = next((tokens[k] for k in ("background", "bg", "surface") if k in tokens), None)
    if not text or not bg:
        return {
            "name": "contrast",
            "status": "conditional",
            "detail": "No --color-text/--color-background token pair to check.",
        }
    ratio = contrast_ratio(text, bg)
    if ratio is None:
        return {
            "name": "contrast",
            "status": "conditional",
            "detail": "Token colors are not opaque 3/6-digit hex — cannot verify contrast.",
        }
    if ratio < 4.5:
        return {
            "name": "contrast",
            "status": "conditional",
            "detail": f"Token text-on-background contrast is {ratio:.2f}:1 — WCAG AA needs 4.5:1.",
        }
    return {
        "name": "contrast",
        "status": "passed",
        "detail": f"Token text/background contrast {ratio:.2f}:1 (≥ 4.5:1; token-level check).",
    }


# ── Design-quality gate (anti-trope + token discipline) ────────────────────────

_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,6}\b")
_GRADIENT_RE = re.compile(r"linear-gradient\s*\(([^)]*)\)", re.IGNORECASE)
_GENERIC_FONTS = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "inherit",
}


def _distinct_font_families(css: str) -> set[str]:
    fams: set[str] = set()
    for decl in _FONT_FAMILY_RE.findall(css):
        first = decl.split(",")[0].strip().strip("'\"").lower()
        if first.startswith("var("):
            continue
        if first and first not in _GENERIC_FONTS:
            fams.add(first)
    return fams


def _is_purple(hex_color: str) -> bool:
    lum = relative_luminance(hex_color)
    if lum is None:
        return False
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return b > 100 and r > 80 and g < min(r, b) * 0.75


def _normalize_hex(hex_color: str) -> str:
    h = hex_color.lstrip("#").lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return h


def _purple_gradient_trope(css: str, palette_hexes: set[str]) -> bool:
    """Advisory: an OFF-PALETTE purple gradient is the stock AI look.
    Purple gradients built from the concept's own tokens are legitimate."""
    for body in _GRADIENT_RE.findall(css):
        hexes = _HEX_RE.findall(body)
        purples = [hx for hx in hexes if _is_purple(hx)]
        off_palette = [hx for hx in purples if _normalize_hex(hx) not in palette_hexes]
        if len(purples) >= 2 and off_palette:
            return True
    return False


def gate_design_quality(build_dir: pathlib.Path) -> dict:
    """Score the build against the design constitution: token discipline,
    ≤ 2 typefaces, and no stock 'AI purple gradient' trope."""
    css_files = [p for p in build_dir.glob("**/*.css") if p.name != "design-tokens.css"]
    if not css_files:
        return {"name": "design_quality", "status": "conditional", "detail": "No CSS files found."}
    combined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in css_files)
    tokens_css = ""
    tf = build_dir / "design-tokens.css"
    if tf.exists():
        tokens_css = tf.read_text(encoding="utf-8", errors="replace")

    problems: list[str] = []
    fams = _distinct_font_families(combined + "\n" + tokens_css)
    if len(fams) > 2:
        problems.append(f"{len(fams)} typeface families ({', '.join(sorted(fams)[:4])}) — max 2")
    hardcoded = len(_HEX_RE.findall(combined))
    var_uses = combined.count("var(--")
    if hardcoded > 12 and hardcoded > var_uses:
        problems.append(
            f"{hardcoded} hardcoded hex colors vs {var_uses} token uses — style with var(--…)"
        )
    palette_hexes = {_normalize_hex(hx) for hx in _HEX_RE.findall(tokens_css)}
    if _purple_gradient_trope(combined, palette_hexes):
        problems.append(
            "off-palette purple gradient detected — the stock AI look; use the concept's tokens"
        )

    if problems:
        return {"name": "design_quality", "status": "conditional", "detail": "; ".join(problems)}
    return {
        "name": "design_quality",
        "status": "passed",
        "detail": f"Design discipline OK ({len(fams)} typeface(s), {var_uses} token uses).",
    }


DESIGN_GATES = [gate_a11y, gate_performance, gate_contrast, gate_design_quality]
