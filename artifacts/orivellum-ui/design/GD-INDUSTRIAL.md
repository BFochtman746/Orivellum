# GD-INDUSTRIAL — Orivellum design foundation

Character: a fusion of **GDLS** (land systems — armor plate, olive drab, stencil
marking) and **GDEB** (Electric Boat — hull steel, deep naval grays, sonar
signal). Dark-mode-first: this is a **night-usable command console**. The light
theme exists for daylight and the shop floor, not as an inversion.

All colors below are specified in **CIE LCH** (L 0–100, C chroma, H hue angle)
with HEX/RGB equivalents. CSS ships hex; LCH is the design source of truth.

---

## 1. Structure — 60 / 30 / 10

| Share | Family | Role |
| --- | --- | --- |
| 60% | **Hull steel** (gunmetal surfaces) | Dominant. Every background, card, and panel. |
| 30% | **Bone** (neutral text/line family) | Text, hairlines, dividers, muted labels. |
| 10% | **Land + Sea accents** | Interactive/selected states, live signals, attention. |

## 2. Dominant — hull-steel surface ramp (dark theme `hull`)

Cool steel: hue held constant at **H 250°, C 4** (a faint blue cast — steel,
not asphalt). Ramp steps are a fixed **ΔL = 4**, which at this chroma is a
near-constant perceptual **ΔE ≈ 4** between adjacent steps — every elevation
change is equally visible, none jumps.

| Token | LCH | HEX | RGB | Use |
| --- | --- | --- | --- | --- |
| `--gd-bg` | L 10 · C 4 · H 250 | `#14181D` | 20 24 29 | App background (hull) |
| `--gd-surface` | L 14 · C 4 · H 250 | `#1B2026` | 27 32 38 | Shell surfaces, header ground |
| `--gd-card` | L 18 · C 4 · H 250 | `#232930` | 35 41 48 | Tiles, panels, rows |
| `--gd-card-hi` | L 22 · C 4 · H 250 | `#2B323A` | 43 50 58 | Pressed / hover / raised |
| `--gd-line` | L 28 · C 4 · H 250 | `#39424C` | 57 66 76 | Hairlines, borders |
| `--gd-line-2` | L 34 · C 4 · H 250 | `#485460` | 72 84 96 | Emphasized rules |

## 3. Neutral — bone text family (dark theme)

| Token | LCH | HEX | RGB | Use | Contrast vs `--gd-bg` |
| --- | --- | --- | --- | --- | --- |
| `--gd-text` | L 92 · C 3 · H 85 | `#EAE7E0` | 234 231 224 | Body text | **13.9 : 1** (AAA) |
| `--gd-muted` | L 72 · C 4 · H 250 | `#A6B1BC` | 166 177 188 | Secondary labels | **7.4 : 1** (AAA) |
| `--gd-dim` | L 56 · C 5 · H 250 | `#76838F` | 118 131 143 | Tertiary / metadata | 4.6 : 1 (AA large only — never body copy) |

Body text is always `--gd-text` (≥ 7:1 on every surface in the ramp: 13.9:1 on
bg, 11.4:1 on card, 9.9:1 on card-hi). `--gd-muted` clears 7:1 on bg and
surface. `--gd-dim` is restricted to metadata at ≥ 15px semibold.

The text family is warm (H 85 — bone, not blue-white) while the surfaces are
cool (H 250). This cool-ground / warm-figure opposition is deliberate: it
keeps text perceptually "in front of" the steel without raising luminance.

## 4. Accents — land and sea (dark theme)

Each accent has **one Gestalt function**. Similarity does the grouping: a color
means the same thing on every screen, so the eye learns the code once.

### Land family (GDLS)

| Token | LCH | HEX | Gestalt function |
| --- | --- | --- | --- |
| `--gd-olive` | L 70 · C 42 · H 115 | `#A8B36B` | **Grouping / classification** — tier badges, category chips, the Learning accent |
| `--gd-bronze` | L 68 · C 48 · H 75 | `#C9964B` | **Figure emphasis** — the thing you're working on; the Writing accent |

### Sea family (GDEB)

| Token | LCH | HEX | Gestalt function |
| --- | --- | --- | --- |
| `--gd-sonar` | L 76 · C 40 · H 185 | `#3FC9B8` | **Live signal / common fate** — streaming, in-progress, connectivity OK; the Chat accent |
| `--gd-slate` | L 66 · C 22 · H 260 | `#7C9BC0` | **Reference / continuity** — links, informational states; the Studio accent |

### Semantic states (adjusted off pure primaries)

| Token | LCH | HEX | Rule |
| --- | --- | --- | --- |
| `--gd-danger` | L 62 · C 52 · H 35 | `#E5604A` | **Exactly one control per screen** — the irreversible one. Red everywhere means nothing. |
| `--gd-caution` | L 76 · C 55 · H 85 | `#E0A93E` | Needs attention, not destructive; the Command accent |
| `--gd-success` | L 72 · C 44 · H 150 | `#5FBF7E` | Confirmed / healthy |
| `--gd-info` | = `--gd-slate` | `#7C9BC0` | Neutral notice |
| `--gd-violet` | L 68 · C 48 · H 300 | `#A78BFA` | **Machine-written text only.** Never decorative. (Carried over from VELLUM — unchanged rule.) |

Accents are never body-text colors; they color 48px controls, icon plaques,
badges, and hairlines. Where accent text appears (chip labels), it is ≥ 4.5:1
against its surface and ≥ 15px semibold.

## 5. Light theme `daylight` — opponent-process mapping, not inversion

A brightness inversion of cool steel gives a cold clinical white that reads as
a different product. Instead the mapping follows opponent-process balance:
the dark theme is a **cool ground / warm figure**; the light theme swaps the
axis — **warm ground / cool figure**. Surfaces move to warm bone-concrete
(H 85, the dark theme's *text* hue), and text moves to deep steel (H 250, the
dark theme's *surface* hue). The product keeps one identity — steel and bone —
with the roles exchanged, the way the same hull reads at night and at noon.

| Token | LCH | HEX | Contrast (text vs) |
| --- | --- | --- | --- |
| `--gd-bg` | L 90 · C 6 · H 85 | `#E6E2D8` | — |
| `--gd-surface` | L 93 · C 5 · H 85 | `#EFECE3` | — |
| `--gd-card` | L 96 · C 4 · H 85 | `#F7F5EE` | — |
| `--gd-card-hi` | L 99 · C 2 · H 85 | `#FFFEFA` | — |
| `--gd-line` | L 80 · C 6 · H 85 | `#C9C4B7` | — |
| `--gd-line-2` | L 72 · C 7 · H 85 | `#B1AC9E` | — |
| `--gd-text` | L 16 · C 5 · H 250 | `#20262D` | **12.1 : 1** on bg (AAA) |
| `--gd-muted` | L 38 · C 6 · H 250 | `#4E5A66` | **7.1 : 1** on bg (AAA) |
| `--gd-dim` | L 52 · C 6 · H 250 | `#6D7985` | AA-large only |

Accents in daylight are darkened along their own hue lines to hold ≥ 4.5:1
against bone (olive `#5F6E2A`, bronze `#8A6320`, sonar `#0F7E71`, slate
`#3E608C`, danger `#B23A24`, caution `#8A6412`, success `#1F7A44`, violet
`#6D4FD8`). Same hue, same function — deeper cut.

## 6. Per-app accent tinting

Each app tints `--gd-accent` from the families above via
`<html data-app="…">`. The steel ground never changes — only the accent.

| App | Accent | Family |
| --- | --- | --- |
| Writing | bronze | land |
| Learning | olive | land |
| Chat | sonar | sea |
| Studio | slate | sea |
| Command | caution amber | signal |
| Mail | slate | sea |
| Library | olive | land |

## 7. Deuteranopia pass (dual coding)

No state in the system is carried by hue alone:

- **Connectivity ribbon** — color + *width* (full / half / sliver) + a text
  label (`ONLINE` / `AI OFFLINE` / `OFFLINE`).
- **Danger** — color + explicit verb label on the control ("Delete", never a
  lone red icon) + confirmation step.
- **Machine-written (violet)** — color + a `Sparkles`/`AI` icon or "AI" text
  badge wherever it appears.
- **Tier/status badges** — color + text of the tier name.
- **Success/caution/danger toast-level states** — icon (check / triangle /
  octagon) + text, color is reinforcement only.

Under simulated deuteranopia, olive and bronze converge (both land colors —
acceptable: they never encode *different states* on the same element), and
sonar vs slate remain separable by lightness (L 76 vs L 66). Danger `#E5604A`
vs success `#5FBF7E` differ by width/icon/label in every use, per above.

## 8. Typography

| Role | Face | Rationale |
| --- | --- | --- |
| Display / wordmark | **Saira Condensed** (600/700, tracked wide, uppercase) | Engineering-plate condensed grotesque — hull marking without costume |
| Stencil accent | **Allerta Stencil** (wordmark + app names only) | The GDLS stencil note; restricted so it stays a mark, not a body face |
| Body | system UI stack (SF / Segoe) | Maximum legibility at 17px on iPhone; zero font-load cost |
| Machine text / data | **IBM Plex Mono** | Data, IDs, logs; violet + mono = machine wrote it |

## 9. Layout constitution (from the orivellum-app design constitution)

1. **One scroll container.** `100dvh` shell; `100vh` is banned (iOS address
   bar). Nothing inside the scroll region gets its own `overflow-y` unless it
   *is* the designated container for that screen.
2. **Three primitives** — `.gd-tile`, `.gd-row`, `.gd-panel`. A screen that
   needs a fourth is doing too much.
3. **Four tiles above the fold** (visual working memory is 4±1).
4. **Thumb zone** — 48px (`--gd-tap`) minimum targets; primary actions in the
   lower two-thirds on phones.
5. **State is ambient** — the hairline ribbon under the header, never a toast.
6. **The shell never moves.** Screen changes swap content only.
