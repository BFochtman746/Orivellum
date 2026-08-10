# WRITING_ARCHITECT — Plain-Language Guide

This guide explains, in ordinary language, what you have, what it does, and
exactly how to run it. No prior knowledge of the code is assumed. Every command
is written out in full so you can copy it as-is.

---

## 1. What this is (in one paragraph)

Your archive was a large pile of overlapping "master" documents with no single
source of truth. The forensic specification you commissioned said: stop making
master prompts, and instead build a *governed* Book Production Operating System
— one where the rules are enforced by software, not by asking a model nicely.
This package is the first working piece of that system. It does two jobs today:

1. **WR-00 — the forensic baseline.** It reads your archive without changing a
   single byte, fingerprints every file, finds every duplicate, and proposes
   which file should be the one authority for each capability. This is the
   evidence floor the whole rebuild stands on.
2. **WR-01 — the governed foundation.** A local database that holds books,
   research, evidence, chapter contracts, drafts, editorial findings, approvals
   and an audit trail — with the spec's hard rules wired in so forbidden actions
   are physically refused, not merely discouraged.

Everything runs on your machine, offline, with no third-party software to
install. It is built entirely on Python's standard library.

---

## 2. What you need

- **Python 3.9 or newer.** To check, open a terminal and run:
  ```
  python3 --version
  ```
  If that prints `Python 3.9` or higher, you are ready. If not, install it from
  <https://www.python.org/downloads/>.

That is the only requirement. There is nothing else to download.

---

## 3. Installing (one time)

**macOS / Linux** — in a terminal, from inside the unzipped package folder:
```
bash install.sh
```

**Windows** — in PowerShell, from inside the package folder:
```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer finds your Python, installs the `wa` command, and then runs a
self-check. When it finishes you should see a block ending in:
```
  status           : HEALTHY
```

If the word `wa` is not recognized afterward, either open a new terminal window,
or run the tool the long way (this always works):
```
python3 -m writing_architect <command>
```
For example, `python3 -m writing_architect doctor`.

---

## 4. The five commands you will actually use

### 4.1 Check the tool is healthy
```
wa doctor
```
Prints versions and confirms the governance rules loaded. Run this first.

### 4.2 Run the forensic baseline on your archive  ← **start here**
```
wa forensics WRITING_ARCHITECT.zip --out wr00_baseline
```
Point it at your archive (use the real path if it is elsewhere). It creates a
folder `wr00_baseline/` containing:

| File | What it is |
|---|---|
| `WR00_REPORT.md` | The human-readable forensic report — open this first |
| `baseline_manifest.json` | The complete machine record (every file, hash, disposition) |
| `INVENTORY.csv` | One row per file — open in Excel |
| `DUPLICATES.csv` | Every set of byte-identical copies |
| `baseline.sha256` | A seal so you can later prove the report was not altered |

**Your archive is never modified.** The tool only reads it.

### 4.3 Create a governed book project
```
wa init mybook.sqlite
wa book-new mybook.sqlite --title "Ash and Silence" --author "Brian Fochtman" --form "biblical historical fiction" --audience "Adult literary readers" --reader-promise "A grief-soaked account of exile and return"
```
This starts a book in state **B0 (Intake)** inside a single database file
(`mybook.sqlite`). That one file is your entire project; back it up like any
document.

### 4.4 See where a book stands and what is blocking it
```
wa status mybook.sqlite <book_id>
```
(The `<book_id>` is printed when you create the book, e.g. `book_1a2b3c...`.)
It shows the current lifecycle state, whether it can advance, and a gate report
listing exactly what is unfinished.

### 4.5 Move a book forward through the lifecycle
```
wa advance mybook.sqlite <book_id> --actor "Brian Fochtman" --reason "intake complete"
```
The system **refuses** to advance if a gate is not satisfied and tells you why.
That refusal is the whole point — you cannot accidentally skip research,
approve an empty contract, or release with an open blocker.

If you discover a problem that belongs to an earlier stage, send the book back:
```
wa return mybook.sqlite <book_id> --to B3 --actor "Brian Fochtman" --reason "found an unsupported claim"
```

---

## 5. See the rules enforce themselves (recommended once)

Run the built-in walkthrough. It creates a throwaway project and deliberately
tries five forbidden actions so you can watch each one get refused:
```
wa demo demo.sqlite
```
You will see lines like:
```
[ 6] PROVE GATE: accept the factual claim with NO evidence -> must REFUSE
     REFUSED as designed: POLICY FM-07: cannot accept a factual claim ...
```
Every `REFUSED` line is a spec rule being enforced by the database itself.

---

## 6. The lifecycle, in plain terms

A book moves through 14 states. It cannot jump ahead, and a later polish can
never paper over an earlier defect.

| State | Meaning |
|---|---|
| B0 Intake | Files received and fingerprinted |
| B1 Authority resolution | Decide which file is the real authority |
| B2 Book definition | Promise, audience, premise agreed |
| B3 Research baseline | Questions, sources, evidence, conflicts |
| B4 Architecture | Plan tree and chapter contracts approved |
| B5 Drafting | Prose written only from an approved contract + evidence |
| B6 Developmental edit | Structure, causality, pacing repaired |
| B7 Verification | Facts, citations, continuity checked |
| B8 Line edit | Voice, rhythm, clarity |
| B9 Copyedit | Grammar, consistency, style sheet |
| B10 Production | Layout and outputs |
| B11 Proof | Final rendered check |
| B12 Release candidate | Everything assembled, no open blocker |
| B13 Released | You sign off; the result is frozen and immutable |

---

## 7. The hard rules that are always on

These cannot be turned off from a prompt. They live in the database:

1. A factual claim cannot be accepted into canon with no supporting evidence.
2. No prose can be drafted against a chapter contract that is not approved.
3. Every quotation must carry an exact location reference (book/chapter/verse,
   page, etc.).
4. A reviewer cannot close their own blocking finding — someone else must.
5. Once an artifact is released, it is immutable; it cannot be edited in place.
6. Every lifecycle move records who did it and why.
7. The audit log is append-only and hash-chained, so tampering is detectable.

---

## 8. Where your data lives

- Your archive: untouched, wherever it already is.
- The forensic baseline: the `wr00_baseline/` folder you named.
- Each book project: a single `.sqlite` file you named. Copy it to back up.

Nothing leaves your computer. There is no server and no account.

---

## 9. If something goes wrong

- **`wa: command not found`** — open a new terminal, or use the long form
  `python3 -m writing_architect ...`.
- **"Python 3.9+ is required"** — install a newer Python and re-run the installer.
- **A command printed `REFUSED`** — that is not a bug. Read the message; it names
  the exact rule and what to fix.
- **Confirm nothing is broken** — run `python3 tests/test_system.py`. You should
  see `12 passed, 0 failed`.

---

## 10. What is deliberately not built yet

This foundation stops where the spec says the next human decision is required.
The drafting/editorial *automation* (the parts that call a language model) sit
behind clean interfaces but are intentionally left as the next releases
(WR-02 onward) so that no automated writing begins before you have accepted the
forensic baseline and the authority set. The governing plan for those steps is
in `DIRECTIVE.md`.
