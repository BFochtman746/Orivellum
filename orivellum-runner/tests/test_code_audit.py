"""The rebuilt code job: orchestration + doctrine checks + SARIF.

Three promises are pinned here:

  1. SELF-TEST — the defect classes the August 2026 hand-audits found
     (fail-open gates, None-as-pass, unwired public functions, undocumented
     env vars, percentage gates, off-by-default security) are FLAGGED, with
     file:line evidence, on a fixture repo that contains exactly them.
  2. NO PHANTOM FINDINGS — fail-closed gates, documented env vars, wired
     functions, and falsy (rejection) returns produce nothing.
  3. NORMALIZATION — scanner output (ruff/bandit/mypy/tsc/eslint) maps into
     the finding schema without the binaries present, and every finding
     round-trips into valid SARIF 2.1.0.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import report, store  # noqa: E402
from runner.jobs import code as code_job  # noqa: E402
from runner.jobs import code_doctrine as doctrine  # noqa: E402
from runner.jobs import code_tools as tools  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from runner.config import CFG

    monkeypatch.setattr(CFG, "runs_dir", str(tmp_path / "runs"))
    monkeypatch.setattr(CFG, "db", str(tmp_path / "runs" / "runner.db"))
    monkeypatch.setattr(CFG, "mock", True)


def _run(target="x"):
    store.init()
    return store.start_run("code", str(target), "test", {})


def _codes(run_id):
    return [(f["code"], f["ref"], f["severity"]) for f in store.findings(run_id)]


def _by_code(run_id, code):
    return [f for f in store.findings(run_id) if f["code"] == code]


# ── fixture repo: one of each defect class found by hand this week ──────────
def _fixture_repo(root: Path):
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "gate.py").write_text(
        textwrap.dedent(
            """
            def verify_signature(data):
                try:
                    return _really_verify(data)
                except Exception:
                    return True  # fail-open: approval on crash

            def check_quota(user):
                try:
                    return _compute(user)
                except ValueError:
                    return None  # None-as-pass in a gate

            def verify_strict(data):
                try:
                    return _really_verify(data)
                except Exception:
                    raise  # fail-closed: must NOT be flagged

            def is_ready(x):
                try:
                    return _probe(x)
                except OSError:
                    return False  # falsy rejection: must NOT be flagged
            """
        ),
        encoding="utf-8",
    )
    (root / "app" / "unwired.py").write_text(
        textwrap.dedent(
            """
            def handle_export(path):
                '''Public, imported by nothing, called by nothing.'''
                return open(path).read()
            """
        ),
        encoding="utf-8",
    )
    (root / "app" / "metrics.py").write_text(
        textwrap.dedent(
            """
            import os

            def coverage_gate(stats):
                coverage_pct = stats["covered"] / stats["listed"] * 100
                if coverage_pct >= 80:
                    return True
                raise SystemExit(1)

            AUDIT_TOKEN = os.getenv("AUDIT_TOKEN")
            VERIFY_TLS = os.getenv("VERIFY_TLS", "0")
            DOCUMENTED_URL = os.getenv("DOCUMENTED_URL", "http://x")

            def fetch(url, verify_tls=False):
                return url, verify_tls
            """
        ),
        encoding="utf-8",
    )
    (root / "app" / "main.py").write_text(
        textwrap.dedent(
            """
            from app.gate import verify_signature, check_quota, verify_strict, is_ready
            from app.metrics import coverage_gate, fetch

            def main():
                verify_signature(b"x"); check_quota(1); verify_strict(b"x")
                is_ready(1); coverage_gate({}); fetch("u")
            """
        ),
        encoding="utf-8",
    )
    (root / "app" / "web.ts").write_text(
        textwrap.dedent(
            """
            export function unusedWidget(x: number) { return x + 1; }
            export function usedWidget(x: number) { return x - 1; }
            const mode = process.env.TS_SECRET_MODE;
            const flag = import.meta.env.VITE_FLAG;
            """
        ),
        encoding="utf-8",
    )
    (root / "app" / "page.tsx").write_text(
        "import { usedWidget } from './web';\nexport const N = usedWidget(2);\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_app.py").write_text(
        "from app.metrics import coverage_gate\n\ndef test_gate():\n    coverage_gate\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("Set DOCUMENTED_URL to the upstream.\n", encoding="utf-8")
    return root


def _audit(root):
    run_id = _run(root)
    files = code_job.walk_code(root)
    digests = [
        {"name": "verify_signature", "file": "app/gate.py", "loc": 5},
        {"name": "check_quota", "file": "app/gate.py", "loc": 5},
        {"name": "handle_export", "file": "app/unwired.py", "loc": 40},
        {"name": "coverage_gate", "file": "app/metrics.py", "loc": 6},
        {"name": "main", "file": "app/main.py", "loc": 4},
    ]
    summary = doctrine.audit(run_id, root, files, digests)
    return run_id, summary


# ── 1. the self-test: known past defect classes are flagged ─────────────────
class TestFailOpen:
    def test_except_returning_true_is_high(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        hits = _by_code(run_id, "DOCTRINE-FAILOPEN")
        highs = [h for h in hits if h["severity"] == "HIGH"]
        assert len(highs) == 1
        assert "gate.py:" in highs[0]["ref"]
        assert "verify_signature" in highs[0]["title"]

    def test_none_as_pass_in_gate_is_medium(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        meds = [h for h in _by_code(run_id, "DOCTRINE-FAILOPEN") if h["severity"] == "MEDIUM"]
        assert any("check_quota" in h["title"] for h in meds)

    def test_fail_closed_and_falsy_rejection_not_flagged(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        titles = " ".join(h["title"] for h in _by_code(run_id, "DOCTRINE-FAILOPEN"))
        assert "verify_strict" not in titles
        assert "is_ready" not in titles
        assert s["fail_open"] == 2


class TestNoCaller:
    def test_unwired_python_function_flagged(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        refs = [h["ref"] for h in _by_code(run_id, "DOCTRINE-NOCALLER")]
        assert "app/unwired.py::handle_export" in refs

    def test_unwired_ts_export_flagged_used_one_not(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        refs = [h["ref"] for h in _by_code(run_id, "DOCTRINE-NOCALLER")]
        assert "app/web.ts::unusedWidget" in refs
        assert "app/web.ts::usedWidget" not in refs

    def test_wired_functions_not_flagged(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        refs = " ".join(h["ref"] for h in _by_code(run_id, "DOCTRINE-NOCALLER"))
        assert "verify_signature" not in refs
        assert "coverage_gate" not in refs


class TestPctGate:
    def test_percentage_gate_flagged_with_line(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        hits = _by_code(run_id, "DOCTRINE-PCTGATE")
        assert len(hits) == 1
        assert hits[0]["ref"].startswith("app/metrics.py:")
        assert "coverage_pct" in hits[0]["title"]
        assert "denominator" in hits[0]["fix"]


class TestDefaultOff:
    def test_env_and_param_security_defaults_flagged(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        hits = _by_code(run_id, "DOCTRINE-DEFAULTOFF")
        assert any("VERIFY_TLS" in h["title"] for h in hits)
        assert any("verify_tls=False" in h["title"] for h in hits)
        # DOCUMENTED_URL has no security name — not flagged
        assert not any("DOCUMENTED_URL" in h["title"] for h in hits)


class TestEnvDoc:
    def test_each_undocumented_var_names_its_reading_file(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        hits = _by_code(run_id, "DOCTRINE-ENVDOC")
        by_var = {h["ref"].split("::")[-1]: h for h in hits}
        assert set(by_var) == {"AUDIT_TOKEN", "VERIFY_TLS", "TS_SECRET_MODE", "VITE_FLAG"}
        assert by_var["AUDIT_TOKEN"]["ref"].startswith("app/metrics.py::")
        assert "app/metrics.py" in by_var["AUDIT_TOKEN"]["detail"]
        assert by_var["TS_SECRET_MODE"]["ref"].startswith("app/web.ts::")

    def test_documented_var_not_flagged(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        assert not any("DOCUMENTED_URL" in h["ref"] for h in _by_code(run_id, "DOCTRINE-ENVDOC"))


class TestTestGap:
    def test_worst_offenders_individually_named(self, tmp_path):
        run_id, s = _audit(_fixture_repo(tmp_path / "r"))
        hits = _by_code(run_id, "DOCTRINE-TESTGAP")
        repo = [h for h in hits if h["ref"] == "(repository)"]
        assert len(repo) == 1
        assert "4 of 5" in repo[0]["title"]
        per_fn = [h for h in hits if h["ref"] != "(repository)"]
        # biggest untested function is named first-class, with its size
        assert any(h["ref"] == "app/unwired.py::handle_export" for h in per_fn)
        assert any("40 lines" in h["title"] for h in per_fn)
        # the function a test names is not listed
        assert not any("coverage_gate" in h["ref"] for h in per_fn)

    def test_no_tests_at_all_is_high(self, tmp_path):
        root = tmp_path / "empty"
        (root / "app").mkdir(parents=True)
        (root / "app" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        run_id = _run(root)
        digests = [{"name": "f", "file": "app/a.py", "loc": 2}]
        doctrine.audit(run_id, root, code_job.walk_code(root), digests)
        found = store.findings(run_id)
        assert any(f["code"] == "NOTESTS" and f["severity"] == "HIGH" for f in found)


# ── 2. retirement + plan wiring ─────────────────────────────────────────────
class TestRetirement:
    def test_bespoke_pattern_list_is_gone(self):
        assert not hasattr(code_job, "RISKY_PY")
        assert hasattr(code_job, "SECRETS")  # secrets stay: no tool covers them

    def test_plan_discloses_missing_tools(self, tmp_path):
        root = _fixture_repo(tmp_path / "r")
        plan = code_job.plan(str(root), tmp_path / "w")
        assert isinstance(plan["unavailable"], list)
        # ts/tsx units flow through plan as first-class units
        names = {u["payload"]["name"] for u in plan["units"]}
        assert {"unusedWidget", "usedWidget"} <= names

    def test_missing_tool_is_a_gap_finding_not_clean(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools.shutil, "which", lambda _: None)
        run_id = _run("x")
        assert tools.run_ruff(tmp_path, run_id) == "unavailable"
        gaps = _by_code(run_id, "TOOL-GAP")
        assert gaps and "UNEXAMINED" in gaps[0]["title"]


# ── 3. normalizers: scanner output → finding schema, no binaries needed ─────
class TestNormalizers:
    def test_ruff(self, tmp_path):
        rows = tools.normalize_ruff(
            [
                {
                    "code": "S602",
                    "message": "subprocess with shell=True",
                    "filename": str(tmp_path / "a.py"),
                    "location": {"row": 7},
                },
                {
                    "code": "E999",
                    "message": "SyntaxError",
                    "filename": str(tmp_path / "b.py"),
                    "location": {"row": 1},
                },
            ],
            tmp_path,
        )
        assert rows[0]["code"] == "RUFF-S602" and rows[0]["ref"] == "a.py:7"
        assert rows[1]["severity"] == "HIGH"  # E9 = syntax

    def test_mypy(self, tmp_path):
        text = (
            "app/x.py:12:5: error: Incompatible return value type  [return-value]\n"
            "app/x.py:12:5: note: See docs\n"
        )
        rows = tools.normalize_mypy(text, tmp_path)
        assert rows == [
            {
                "severity": "MEDIUM",
                "code": "MYPY-return-value",
                "ref": "app/x.py:12",
                "title": "Incompatible return value type",
                "fix": "The type contract and the code disagree; one of them is wrong.",
            }
        ]

    def test_tsc(self, tmp_path):
        rows = tools.normalize_tsc(
            "src/app.ts(42,7): error TS2304: Cannot find name 'foo'.\nnoise line\n", tmp_path
        )
        assert rows[0]["code"] == "TSC-TS2304"
        assert rows[0]["ref"] == "src/app.ts:42"

    def test_eslint(self, tmp_path):
        rows = tools.normalize_eslint(
            [
                {
                    "filePath": str(tmp_path / "a.tsx"),
                    "messages": [
                        {"ruleId": "no-eval", "severity": 2, "line": 3, "message": "eval"}
                    ],
                }
            ],
            tmp_path,
        )
        assert rows == [
            {"severity": "MEDIUM", "code": "ESLINT-no-eval", "ref": "a.tsx:3", "title": "eval"}
        ]

    def test_cap_is_disclosed(self, tmp_path):
        run_id = _run("x")
        rows = [
            {"severity": "LOW", "code": "RUFF-X", "ref": f"a.py:{i}", "title": "t"}
            for i in range(tools.CAP + 5)
        ]
        tools._emit(run_id, rows, "ruff")
        capped = _by_code(run_id, "RUFF-CAPPED")
        assert capped and "5 further" in capped[0]["title"]


# ── 4. SARIF emission ───────────────────────────────────────────────────────
class TestSarif:
    def test_findings_round_trip_to_valid_sarif(self, tmp_path):
        run_id, _ = _audit(_fixture_repo(tmp_path / "r"))
        p = Path(report.write_sarif(run_id))
        assert p.name == "findings.sarif"
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert doc["version"] == "2.1.0"
        run = doc["runs"][0]
        assert run["tool"]["driver"]["name"] == "orivellum-runner"
        results = run["results"]
        assert len(results) == len(store.findings(run_id))
        rule_ids = [r["id"] for r in run["tool"]["driver"]["rules"]]
        assert len(rule_ids) == len(set(rule_ids))
        assert all(r["ruleId"] in rule_ids for r in results)
        # file:line refs become physical locations with a region
        located = [r for r in results if "locations" in r]
        phys = [r for r in located if r["locations"][0].get("physicalLocation", {}).get("region")]
        assert phys, "expected at least one file:line finding with a startLine"
        # severities map onto SARIF levels
        assert {r["level"] for r in results} <= {"error", "warning", "note"}

    def test_repository_refs_have_no_bogus_location(self, tmp_path):
        run_id = _run("x")
        store.add_finding(run_id, "HIGH", "NOTESTS", "(repository)", "no tests")
        doc = json.loads(Path(report.write_sarif(run_id)).read_text(encoding="utf-8"))
        assert "locations" not in doc["runs"][0]["results"][0]
