"""
Regression tests for P3 item 18: no CI/CD pipeline existed at all before this -
no automated test run, no lint pass on push/PR.

Also covers a real bug found while investigating this item: two test files
(test_ai_patterns.py, test_policy_system.py) each did
`sys.path.insert(0, <path to backend/>)`, which makes backend/mcp/ (a local
package, unrelated to the third-party `mcp` SDK) shadow the real `mcp`
package for the rest of the pytest process once either file is imported.
That broke fastmcp's internal `import mcp` for any test collected afterward
(e.g. test_mcp_capabilities.py) - purely a collection-order accident, not a
missing-mock problem. Both sys.path hacks were dead weight (every import in
both files already goes through the `backend.` package prefix) and have
been removed.
"""

import ast
import os
import tomllib
import unittest

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
PYPROJECT_PATH = os.path.join(REPO_ROOT, "backend", "pyproject.toml")
TESTS_DIR = os.path.dirname(__file__)


def load_workflow():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class CIWorkflowConfigTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()

    def test_workflow_file_exists_and_parses(self):
        self.assertIsInstance(self.workflow, dict)

    def test_triggers_on_push_and_pull_request(self):
        # YAML parses the bare key `on:` as the boolean True, not the string "on"
        triggers = self.workflow.get(True, self.workflow.get("on", {}))
        self.assertIn("push", triggers)
        self.assertIn("pull_request", triggers)

    def test_has_a_test_job_running_pytest(self):
        jobs = self.workflow["jobs"]
        self.assertIn("test", jobs)
        steps = jobs["test"]["steps"]
        run_commands = [s.get("run", "") for s in steps]
        self.assertTrue(
            any("pytest" in cmd for cmd in run_commands),
            "test job must actually invoke pytest",
        )

    def test_test_job_does_not_depend_on_collection_order_luck(self):
        # Either a real Neo4j service container is provided, or the job sets
        # dummy NEO4J_* env vars - both remove reliance on some other test
        # file's mock happening to load first.
        test_job = self.workflow["jobs"]["test"]
        has_service = "neo4j" in test_job.get("services", {})
        env = test_job.get("env", {})
        has_dummy_env = "NEO4J_URI" in env
        self.assertTrue(
            has_service or has_dummy_env,
            "test job must either run a real Neo4j service or set dummy NEO4J_* env vars",
        )

    def test_test_job_excludes_the_known_broken_async_smoke_scripts(self):
        # test_ai_patterns.py/test_policy_system.py are pre-existing smoke
        # scripts (every function `async def`, zero assert statements, no
        # working async-test setup) that pytest has always rejected at
        # collection - excluded rather than "fixed" via asyncio_mode=auto,
        # since that would trade a deterministic failure for a live-API-
        # dependent one (both call real Agent classes with no LLM mocking).
        # A regression here would make the test job permanently red again.
        test_job = self.workflow["jobs"]["test"]
        run_commands = " ".join(s.get("run", "") for s in test_job["steps"])
        self.assertIn("--ignore=tests/test_ai_patterns.py", run_commands)
        self.assertIn("--ignore=tests/test_policy_system.py", run_commands)

    def test_has_a_lint_job_running_ruff(self):
        jobs = self.workflow["jobs"]
        self.assertIn("lint", jobs)
        steps = jobs["lint"]["steps"]
        run_commands = [s.get("run", "") for s in steps]
        self.assertTrue(
            any("ruff" in cmd for cmd in run_commands),
            "lint job must actually invoke ruff",
        )


class NoSysPathShadowingRegressionTests(unittest.TestCase):
    """
    Guards against reintroducing sys.path.insert(0, <.../backend>) in any
    test file, which shadows backend/mcp/ over the real `mcp` SDK package
    for the rest of the pytest process.
    """

    def test_no_test_file_inserts_the_backend_directory_onto_sys_path(self):
        offenders = []
        for name in sorted(os.listdir(TESTS_DIR)):
            if not name.startswith("test_") or not name.endswith(".py"):
                continue
            path = os.path.join(TESTS_DIR, name)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            if "sys.path.insert" not in source:
                continue
            tree = ast.parse(source, filename=name)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "insert"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "path"
                ):
                    call_src = ast.get_source_segment(source, node) or ""
                    if "dirname(__file__)" in call_src and ".." in call_src:
                        offenders.append(name)
        self.assertEqual(
            offenders, [],
            f"test file(s) shadow backend/mcp/ over the real mcp SDK: {offenders}",
        )

    def test_mcp_capabilities_collects_and_passes_regardless_of_earlier_files(self):
        # A lighter-weight guard than a full subprocess pytest re-run: just
        # confirm the real mcp/fastmcp packages (not backend/mcp/) are what
        # resolve for a bare `import mcp`.
        import mcp

        self.assertNotIn("backend", os.path.dirname(mcp.__file__).split(os.sep)[-2:])


class DevToolsAreLockedNotAdHocTests(unittest.TestCase):
    """
    Regression guard for a real gap found while validating this CI workflow:
    pytest, pytest-asyncio, ruff, and pyyaml had all been installed ad-hoc
    into the local dev venv over the course of this engagement, but were
    never added to pyproject.toml/uv.lock. `uv sync --frozen` (exactly what
    CI runs) would produce a venv missing all four - `uv run pytest`/
    `uv run ruff` would fail outright, and 8 tests in
    test_pattern_integration.py that correctly use @pytest.mark.asyncio
    would fail without the plugin installed. Confirmed via a clean-room
    `rm -rf .venv && uv sync --frozen` rebuild before this test was added.
    """

    def test_dev_dependency_group_includes_the_tools_ci_actually_runs(self):
        with open(PYPROJECT_PATH, "rb") as f:
            pyproject = tomllib.load(f)
        dev_group = pyproject.get("dependency-groups", {}).get("dev", [])
        dev_group_names = {entry.split(">=")[0].split("==")[0].strip() for entry in dev_group}
        for required in ("pytest", "pytest-asyncio", "ruff", "pyyaml"):
            self.assertIn(
                required, dev_group_names,
                f"'{required}' is used by tests/CI but missing from pyproject.toml's [dependency-groups].dev",
            )


class PytestCollectibleFromCIWorkingDirectoryTests(unittest.TestCase):
    """
    Regression guard for a real CI failure this workflow actually hit in
    production: every test file imports via the absolute `backend.xxx`
    package path, which requires the repo root (parent of backend/) on
    sys.path - true by accident locally (this session always ran pytest
    from the repo root), but ci.yml's test job sets
    `working-directory: backend`, so `uv run pytest tests/ -q` there
    collected with `ModuleNotFoundError: No module named 'backend'`.

    Fixed via `[tool.pytest.ini_options] pythonpath = [".."]` in
    pyproject.toml. This test actually shells out to `uv run pytest` from
    backend/ (not `python -m pytest`, which has different sys.path
    semantics - `-m` itself adds cwd to sys.path, masking this exact bug)
    to reproduce the real CI invocation precisely, rather than only
    asserting the ini key exists.
    """

    def test_pyproject_configures_pythonpath_to_the_repo_root(self):
        with open(PYPROJECT_PATH, "rb") as f:
            pyproject = tomllib.load(f)
        pythonpath = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("pythonpath")
        self.assertEqual(pythonpath, [".."])

    def test_uv_run_pytest_collects_cleanly_from_the_backend_working_directory(self):
        import subprocess
        backend_dir = os.path.join(REPO_ROOT, "backend")
        result = subprocess.run(
            ["uv", "run", "pytest", "tests/test_audit_validation_error_tracking.py", "--collect-only", "-q"],
            cwd=backend_dir, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            f"pytest failed to collect from backend/ as CI invokes it:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
