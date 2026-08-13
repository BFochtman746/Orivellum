"""WP2 design-token contract tests.

Three guarantees, enforced forever:

1. **Contrast** — every pair declared in ``design/tokens.json`` under
   ``$extensions["orivellum.contrastPairs"]`` meets its minimum WCAG ratio,
   and every per-app accent holds the component minimum on its canvas.
2. **Parity** — ``src/styles/gd-tokens.css`` (both theme blocks + per-app
   accent lines) mirrors tokens.json exactly; the CSS is a build artifact of
   the token source, never an independent palette.
3. **shadcn fidelity** — the shadcn HSL triples in ``src/index.css``
   (``:root`` / ``.dark`` blocks) round-trip to the same colors as the token
   source (±2/255 per channel for HSL rounding). WP3 deleted the temporary
   legacy alias sheet; index.css is now the only home of these triples.

Plus lockdown floors: no Google Fonts anywhere in UI source, and no
``-webkit-font-smoothing`` overrides (platform rendering is the contract).
"""

from __future__ import annotations

import colorsys
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKENS_PATH = ROOT / "design" / "tokens.json"
UI = ROOT / "artifacts" / "orivellum-ui"
GD_CSS = UI / "src" / "styles" / "gd-tokens.css"
INDEX_CSS = UI / "src" / "index.css"

TOKENS = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


# ── token resolution ─────────────────────────────────────────────────────────

def _resolve(ref: str) -> str:
    """Resolve a {path.to.token} reference (or literal hex) to a hex string."""
    seen = 0
    while ref.startswith("{"):
        seen += 1
        assert seen < 10, f"reference loop resolving {ref!r}"
        node: object = TOKENS
        for part in ref.strip("{}").split("."):
            assert isinstance(node, dict) and part in node, f"dangling reference {ref!r}"
            node = node[part]
        assert isinstance(node, dict), f"reference {ref!r} does not resolve to a token"
        ref = node["$value"]  # type: ignore[index]
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", ref), f"expected 6-digit hex, got {ref!r}"
    return ref.upper()


def semantic_hex(name: str, mode: str) -> str:
    tok = TOKENS["semantic"][name]
    return _resolve(tok["$extensions"]["orivellum.modes"][mode])


def token_hex(path: str, mode: str) -> str:
    group, name = path.split(".", 1)
    if group == "semantic":
        return semantic_hex(name, mode)
    if group == "raw":
        return _resolve(TOKENS["raw"][name]["$value"])
    raise AssertionError(f"unknown token path {path!r}")


# ── WCAG math ────────────────────────────────────────────────────────────────

def _rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rel_lum(hexstr: str) -> float:
    def chan(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (_chan for _chan in map(chan, _rgb(hexstr)))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    la, lb = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ── 1. contrast contract ─────────────────────────────────────────────────────

_PAIRS = TOKENS["$extensions"]["orivellum.contrastPairs"]


@pytest.mark.parametrize(
    "fg,bg,minimum,mode",
    [(p["fg"], p["bg"], p["min"], m) for p in _PAIRS for m in p["modes"]],
)
def test_contrast_pair(fg: str, bg: str, minimum: float, mode: str):
    ratio = contrast(token_hex(fg, mode), token_hex(bg, mode))
    assert ratio >= minimum, (
        f"{fg} on {bg} [{mode}] = {ratio:.2f}:1, below required {minimum}:1"
    )


APPS = sorted(TOKENS["component"]["accent"])


def _component_hex(group: str, app: str, mode: str) -> str:
    return _resolve(TOKENS["component"][group][app]["$extensions"]["orivellum.modes"][mode])


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_component_accent_contrast(app: str, mode: str):
    minimum = TOKENS["$extensions"]["orivellum.componentAccentPairs"]["onCanvasMin"]
    ratio = contrast(_component_hex("accent", app, mode), semantic_hex("canvas", mode))
    assert ratio >= minimum, f"{app} accent [{mode}] = {ratio:.2f}:1 on canvas (< {minimum})"


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_component_accent_ink_contrast(app: str, mode: str):
    """Every app's label ink must hold 4.5:1 on that app's solid accent."""
    ink = _component_hex("accent-ink", app, mode)
    accent = _component_hex("accent", app, mode)
    ratio = contrast(ink, accent)
    assert ratio >= 4.5, f"{app} accent-ink [{mode}] = {ratio:.2f}:1 on its accent (< 4.5)"


# ── 2. CSS parity ────────────────────────────────────────────────────────────

# semantic token → CSS custom property that must carry the identical literal
CSS_PARITY = {
    "canvas": "--gd-canvas",
    "raised": "--gd-raised",
    "recessed": "--gd-recessed",
    "surface": "--gd-surface",
    "line-decorative": "--gd-line-decorative",
    "line-control": "--gd-line-control",
    "text": "--gd-text",
    "text-muted": "--gd-muted",
    "text-subtle": "--gd-dim",
    "primary": "--gd-primary",
    "focus": "--gd-focus",
    "olive": "--gd-olive",
    "bronze": "--gd-bronze",
    "sonar": "--gd-sonar",
    "slate": "--gd-slate",
    "danger": "--gd-danger",
    "caution": "--gd-caution",
    "success": "--gd-success",
    "info": "--gd-info",
    "violet": "--gd-violet",
    "accent-default": "--gd-accent",
    "accent-ink": "--gd-accent-ink",
}


def _css_theme_block(mode: str) -> dict[str, str]:
    css = GD_CSS.read_text(encoding="utf-8").split("@media", 1)[0]
    m = re.search(
        rf"html\[data-theme='{mode}'\]\s*\{{(.*?)\}}", css, re.DOTALL
    )
    assert m, f"no {mode} theme block in gd-tokens.css"
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})\s*;", m.group(1)))


@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_css_parity(mode: str):
    block = _css_theme_block(mode)
    mismatches = []
    for name, var in CSS_PARITY.items():
        expected = semantic_hex(name, mode)
        actual = block.get(var, "").upper()
        if actual != expected:
            mismatches.append(f"{var} [{mode}]: css={actual or 'MISSING'} tokens={expected}")
    assert not mismatches, "gd-tokens.css diverged from design/tokens.json:\n" + "\n".join(mismatches)


def _per_app_rule(app: str, mode: str) -> str:
    css = GD_CSS.read_text(encoding="utf-8").split("@media", 1)[0]
    m = re.search(
        rf"html\[data-theme='{mode}'\]\[data-app='{app}'\]\s*\{{([^}}]*)\}}", css
    )
    assert m, f"no per-app accent rule for {app} [{mode}]"
    return m.group(1)


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_css_per_app_accent_parity(app: str, mode: str):
    rule = _per_app_rule(app, mode)
    m = re.search(r"--gd-accent:\s*(#[0-9A-Fa-f]{6})", rule)
    assert m, f"{app} [{mode}] rule missing --gd-accent"
    expected = _component_hex("accent", app, mode)
    assert m.group(1).upper() == expected, f"{app} [{mode}]: css={m.group(1)} tokens={expected}"


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_css_per_app_accent_ink_parity(app: str, mode: str):
    """Per-app ink: hull rules must carry the app's tinted ink; daylight rules
    inherit the theme block's white ink (so they must NOT override it)."""
    rule = _per_app_rule(app, mode)
    m = re.search(r"--gd-accent-ink:\s*(#[0-9A-Fa-f]{6})", rule)
    expected = _component_hex("accent-ink", app, mode)
    if mode == "hull":
        assert m, f"{app} [hull] rule missing --gd-accent-ink"
        assert m.group(1).upper() == expected, (
            f"{app} [hull] ink: css={m.group(1)} tokens={expected}"
        )
    else:
        assert m is None, f"{app} [daylight] must inherit theme-block accent-ink"
        block = _css_theme_block("daylight")
        assert block.get("--gd-accent-ink", "").upper() == expected, (
            f"daylight theme-block ink diverged from tokens for {app}"
        )


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_css_per_app_soft_derives_from_accent(app: str, mode: str):
    """--gd-accent-soft must be the app's own accent at low alpha — a soft
    tint drifting to a different hue than its accent is token drift."""
    rule = _per_app_rule(app, mode)
    m = re.search(r"--gd-accent-soft:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*(0?\.\d+)\)", rule)
    assert m, f"{app} [{mode}] rule missing rgba --gd-accent-soft"
    rgb = tuple(int(x) for x in m.groups()[:3])
    assert rgb == _rgb(_component_hex("accent", app, mode)), (
        f"{app} [{mode}] soft tint {rgb} is not its accent color"
    )
    assert float(m.group(4)) <= 0.2, f"{app} [{mode}] soft alpha too strong"


# ── 3. shadcn HSL triple fidelity (index.css) ────────────────────────────────

ALIAS_HSL = {
    "daylight": {
        "--background": "semantic.canvas",
        "--foreground": "semantic.text",
        "--border": "semantic.line-decorative",
        "--card": "semantic.raised",
        "--sidebar": "semantic.surface",
        "--sidebar-primary": "semantic.primary",
        "--sidebar-accent": "semantic.recessed",
        "--secondary": "semantic.recessed",
        "--muted": "semantic.recessed",
        "--accent": "semantic.recessed",
        "--muted-foreground": "semantic.text-muted",
        "--destructive": "semantic.danger",
        "--input": "semantic.line-control",
        "--primary": "semantic.primary",
        "--ring": "semantic.focus",
    },
    "hull": {
        "--background": "semantic.canvas",
        "--foreground": "semantic.text",
        "--border": "semantic.line-decorative",
        "--card": "semantic.raised",
        "--sidebar": "semantic.surface",
        "--sidebar-primary": "semantic.primary",
        "--sidebar-accent": "semantic.raised",
        "--secondary": "semantic.raised",
        "--muted": "semantic.raised",
        "--accent": "semantic.raised",
        "--muted-foreground": "semantic.text-muted",
        "--destructive": "semantic.danger",
        "--input": "semantic.line-2",
        "--primary": "semantic.primary",
        "--ring": "semantic.focus",
    },
}


def _alias_block(mode: str) -> dict[str, str]:
    css = INDEX_CSS.read_text(encoding="utf-8")
    selector = ".dark" if mode == "hull" else ":root"
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.DOTALL)
    assert m, f"no {selector} block in index.css"
    return dict(
        re.findall(r"(--[\w-]+):\s*([\d.]+ [\d.]+% [\d.]+%)\s*;", m.group(1))
    )


def _hsl_to_rgb(triple: str) -> tuple[int, int, int]:
    h, s, lightness = (float(x.rstrip("%")) for x in triple.split())
    r, g, b = colorsys.hls_to_rgb(h / 360, lightness / 100, s / 100)
    return round(r * 255), round(g * 255), round(b * 255)


@pytest.mark.parametrize("mode", ["daylight", "hull"])
def test_alias_hsl_fidelity(mode: str):
    block = _alias_block(mode)
    failures = []
    for var, token_path in ALIAS_HSL[mode].items():
        assert var in block, f"{var} missing from {mode} index.css block"
        actual = _hsl_to_rgb(block[var])
        expected = _rgb(token_hex(token_path, mode))
        if any(abs(a - e) > 2 for a, e in zip(actual, expected)):
            failures.append(f"{var} [{mode}]: hsl→{actual} vs token {expected} ({token_path})")
    assert not failures, "index.css shadcn triples diverged from tokens:\n" + "\n".join(failures)


def test_index_css_has_no_hex():
    """index.css must stay hex-free — colors come only from gd-tokens.css
    (parity-checked) or HSL triples (fidelity-checked). WP3 floor."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", css), "hex literal crept into index.css"


def test_legacy_alias_sheet_deleted():
    """WP3 deleted the temporary alias sheet; it must never come back."""
    assert not (UI / "src" / "styles" / "legacy-aliases.css").exists()


# ── lockdown floors ──────────────────────────────────────────────────────────

def _ui_source_files() -> list[Path]:
    files = [p for p in (UI / "src").rglob("*") if p.suffix in {".ts", ".tsx", ".css"}]
    files.append(UI / "index.html")
    return files


def test_no_google_fonts_anywhere():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _ui_source_files()
        if "fonts.googleapis" in p.read_text(encoding="utf-8", errors="replace")
        or "fonts.gstatic" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, f"Google Fonts references found: {offenders}"


def test_no_font_smoothing_overrides():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _ui_source_files()
        if p.suffix == ".css"
        and "-webkit-font-smoothing" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, f"font-smoothing overrides found: {offenders}"


# ── chrome (PWA/meta) colors mirror the token source ─────────────────────────

def test_chrome_colors_match_tokens():
    chrome = TOKENS["$extensions"]["orivellum.chrome"]
    day = _resolve(chrome["daylight"]["themeColor"])
    hull = _resolve(chrome["hull"]["themeColor"])
    assert day == semantic_hex("canvas", "daylight")
    assert hull == semantic_hex("canvas", "hull")

    index_html = (UI / "index.html").read_text(encoding="utf-8")
    assert f'name="theme-color" content="{day}"' in index_html
    assert f"'{hull}'" in index_html, "boot script must flip theme-color to hull canvas"

    vite = (UI / "vite.config.ts").read_text(encoding="utf-8")
    assert f"theme_color: '{day}'" in vite
    assert f"background_color: '{day}'" in vite
