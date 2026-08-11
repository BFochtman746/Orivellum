"""Code projects must pass their own generated tests before a Workbench
version counts as good.

Covers:
- happy path: build → generated tests run in the sandbox → verdict 'tested',
  test file ships with the version, output stored in checks_json
- repair loop: a build whose tests fail is fixed by the LLM and re-tested;
  attempts recorded
- persistent failure: no version is published, the error names the tests
- test-generation failure blocks the version (never silently untested)
- non-Python projects (JS/HTML) are tested too via file-verifying Python
  tests — there is no untested path to a good verdict
"""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch

from tests.test_workbench import _make_app

_GOOD_SCRIPT = """\
import pathlib
out = pathlib.Path("out")
out.mkdir(exist_ok=True)
(out / "calc.py").write_text("def add(a, b):\\n    return a + b\\n")
print("built calc")
"""

_BAD_SCRIPT = """\
import pathlib
out = pathlib.Path("out")
out.mkdir(exist_ok=True)
(out / "calc.py").write_text("def add(a, b):\\n    return a - b\\n")
print("built calc (buggy)")
"""

_JS_SCRIPT = """\
import pathlib
out = pathlib.Path("out")
out.mkdir(exist_ok=True)
(out / "app.js").write_text("function add(a, b) { return a + b; }\\nmodule.exports = { add };\\n")
(out / "index.html").write_text("<!DOCTYPE html><html><body><h1>Calc</h1></body></html>\\n")
print("built js app")
"""

_JS_TESTS = """\
import pathlib
import re
import unittest


class TestJsProject(unittest.TestCase):
    def test_add_function_exists(self):
        src = pathlib.Path("app.js").read_text()
        self.assertRegex(src, re.compile(r"function add\\(a, b\\)"))
        self.assertIn("return a + b", src)

    def test_html_has_heading(self):
        html = pathlib.Path("index.html").read_text()
        self.assertIn("<h1>Calc</h1>", html)


if __name__ == "__main__":
    unittest.main()
"""

_TESTS = """\
import unittest
import calc


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""


def _llm_results(*texts):
    """Sequence of LLMResult stubs; None text means a failed call."""
    from orivellum.capabilities.llm import LLMResult

    return [
        LLMResult(t, t is not None, "test", 0, error=None if t is not None else "down")
        for t in texts
    ]


class TestCodeProjectTests(unittest.TestCase):
    def _build(self, tmp, llm_side_effect, instruction="add function"):
        from orivellum.capabilities.workbench import run_build

        _, db, cfg = _make_app(tmp)
        p = db.create_wb_project("Calc", "code", "a calculator module")
        with patch("orivellum.capabilities.llm.llm_call", side_effect=llm_side_effect):
            run_build(db, cfg, p["id"], instruction)
        return db, cfg, p

    def test_passing_tests_publish_tested_version_with_test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg, p = self._build(tmp, _llm_results(_GOOD_SCRIPT, _TESTS))
            proj = db.get_wb_project(p["id"])
            self.assertIsNone(proj["last_error"], proj["last_error"])
            versions = db.list_wb_versions(p["id"])
            self.assertEqual(len(versions), 1)
            v = versions[0]
            self.assertEqual(v["verdict"], "tested")
            checks = json.loads(v["checks_json"])
            self.assertTrue(checks["tests"]["passed"])
            self.assertEqual(checks["tests"]["attempts"], 1)
            names = [f["name"] for f in json.loads(v["files_json"])]
            self.assertIn("project_tests.py", names)  # tests ship with the version

            from orivellum.capabilities.workbench import version_dir

            self.assertTrue((version_dir(cfg, p["id"], 1) / "project_tests.py").is_file())

    def test_failing_tests_trigger_repair_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            # bad build → tests fail → fix returns the good script → retest OK
            db, _, p = self._build(tmp, _llm_results(_BAD_SCRIPT, _TESTS, _GOOD_SCRIPT))
            proj = db.get_wb_project(p["id"])
            self.assertIsNone(proj["last_error"], proj["last_error"])
            v = db.list_wb_versions(p["id"])[0]
            self.assertEqual(v["verdict"], "tested")
            checks = json.loads(v["checks_json"])
            self.assertTrue(checks["tests"]["passed"])
            self.assertEqual(checks["tests"]["attempts"], 2)

    def test_persistently_failing_tests_block_the_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            # every "fix" returns the same broken script — retries exhaust
            db, _, p = self._build(tmp, _llm_results(_BAD_SCRIPT, _TESTS, _BAD_SCRIPT, _BAD_SCRIPT))
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_testgen_failure_blocks_the_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(tmp, _llm_results(_GOOD_SCRIPT, None))
            proj = db.get_wb_project(p["id"])
            self.assertIn("test generation failed", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_exit_zero_with_no_tests_is_not_a_pass(self):
        # a test "suite" that just prints and exits 0 must never count
        no_tests = "print('looks fine')\n"
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(
                tmp, _llm_results(_GOOD_SCRIPT, no_tests, _GOOD_SCRIPT, _GOOD_SCRIPT)
            )
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertIn("no real tests", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_spoofed_test_output_cannot_certify_a_pass(self):
        # printing unittest-looking output ("Ran 1 test ... OK") with no real
        # tests must never pass — only the trusted runner's result counts
        spoof = 'print("Ran 1 test in 0.001s")\nprint("OK")\n'
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(
                tmp, _llm_results(_GOOD_SCRIPT, spoof, _GOOD_SCRIPT, _GOOD_SCRIPT)
            )
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_forged_result_via_main_introspection_is_screened_out(self):
        # a test that tries to steal the token / result path via __main__ (or
        # bail out early with os._exit) must be rejected before it ever runs
        forger = """\
import __main__
import os
import unittest


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertTrue(True)
        os._exit(0)


if __name__ == "__main__":
    unittest.main()
"""
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(
                tmp, _llm_results(_GOOD_SCRIPT, forger, _GOOD_SCRIPT, _GOOD_SCRIPT)
            )
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertIn("safety screen", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_monkeypatched_unittest_is_screened_out(self):
        # neutering the harness in-process must be rejected statically
        patcher = """\
import unittest

unittest.TextTestRunner.run = lambda self, suite: None


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
"""
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(
                tmp, _llm_results(_GOOD_SCRIPT, patcher, _GOOD_SCRIPT, _GOOD_SCRIPT)
            )
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertIn("safety screen", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_sys_modules_poisoning_in_tests_is_screened_out(self):
        # replacing the cached unittest module via sys.modules must be
        # rejected statically (sys isn't even importable in test files)
        poisoner = """\
import sys
import unittest

class _Fake:
    def run(self, suite):
        class R:
            testsRun = 5
            def wasSuccessful(self):
                return True
        return R()

sys.modules["unittest"].TextTestRunner = lambda **k: _Fake()


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertTrue(False)


if __name__ == "__main__":
    unittest.main()
"""
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(
                tmp, _llm_results(_GOOD_SCRIPT, poisoner, _GOOD_SCRIPT, _GOOD_SCRIPT)
            )
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertIn("safety screen", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_project_import_poisoning_is_screened_out(self):
        # a PROJECT file that poisons the harness when imported by an
        # otherwise-benign test must also be caught — every project .py is
        # screened, not just the test file
        poisoned_build = """\
import pathlib
out = pathlib.Path("out")
out.mkdir(exist_ok=True)
out.joinpath("calc.py").write_text(
    "import sys\\n"
    "sys.modules['unittest'].TextTestRunner = None\\n"
    "def add(a, b):\\n    return a + b\\n"
)
print("built poisoned calc")
"""
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(
                tmp,
                _llm_results(poisoned_build, _TESTS, poisoned_build, poisoned_build),
            )
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertIn("safety screen", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])

    def test_mutating_test_cannot_certify_different_bytes(self):
        # a test that rewrites calc.py and then passes must only touch the
        # throwaway copy — the published version keeps the original bytes
        mutating_tests = """\
import pathlib
import unittest
import calc

pathlib.Path("calc.py").write_text("def add(a, b):\\n    return 999\\n")


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg, p = self._build(tmp, _llm_results(_GOOD_SCRIPT, mutating_tests))
            proj = db.get_wb_project(p["id"])
            self.assertIsNone(proj["last_error"], proj["last_error"])
            from orivellum.capabilities.workbench import version_dir

            published = (version_dir(cfg, p["id"], 1) / "calc.py").read_text()
            self.assertIn("return a + b", published)
            self.assertNotIn("999", published)

    def test_project_unittest_shadow_cannot_neuter_the_harness(self):
        # a project shipping its own unittest.py must not shadow the stdlib
        # harness (which would turn the suite into a no-op or a crash)
        shadow_script = """\
import pathlib
out = pathlib.Path("out")
out.mkdir(exist_ok=True)
(out / "calc.py").write_text("def add(a, b):\\n    return a + b\\n")
(out / "unittest.py").write_text("raise RuntimeError('shadowed!')\\n")
print("built calc + shadow")
"""
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(tmp, _llm_results(shadow_script, _TESTS))
            proj = db.get_wb_project(p["id"])
            self.assertIsNone(proj["last_error"], proj["last_error"])
            v = db.list_wb_versions(p["id"])[0]
            self.assertEqual(v["verdict"], "tested")
            checks = json.loads(v["checks_json"])
            self.assertTrue(checks["tests"]["passed"])
            self.assertGreaterEqual(checks["tests"]["tests_run"], 1)

    def test_non_python_projects_are_tested_too(self):
        # JavaScript/HTML outputs get Python tests that read and verify the
        # files — there is no untested path to a good verdict
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(tmp, _llm_results(_JS_SCRIPT, _JS_TESTS))
            proj = db.get_wb_project(p["id"])
            self.assertIsNone(proj["last_error"], proj["last_error"])
            v = db.list_wb_versions(p["id"])[0]
            self.assertEqual(v["verdict"], "tested")
            checks = json.loads(v["checks_json"])
            self.assertTrue(checks["tests"]["passed"])
            self.assertEqual(checks["tests"]["tests_run"], 2)

    def test_non_python_project_failing_tests_blocks_the_version(self):
        # a JS build that doesn't satisfy its tests never publishes
        bad_js = _JS_SCRIPT.replace("return a + b", "return a - b")
        with tempfile.TemporaryDirectory() as tmp:
            db, _, p = self._build(tmp, _llm_results(bad_js, _JS_TESTS, bad_js, bad_js))
            proj = db.get_wb_project(p["id"])
            self.assertIn("tests failed", proj["last_error"])
            self.assertEqual(db.list_wb_versions(p["id"]), [])


if __name__ == "__main__":
    unittest.main()
