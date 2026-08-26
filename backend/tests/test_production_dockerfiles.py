"""
Regression tests for P3 item 19: both Dockerfiles were dev-only single-stage
builds (backend ran `fastapi dev` with the full dev dependency group and
hot-reload; frontend ran the Vite dev server). Neither had a production
target at all.

These tests parse the Dockerfiles/compose file as text (no Docker engine
required, matching test_docker_compose_local_infra.py's approach) and assert
the production stages are meaningfully different from the dev ones - not
just a relabeled copy of the same commands.

NOTE: live `docker build`/`docker compose up` verification of these images
was not possible in this environment - the local Docker daemon has been
stuck/unresponsive since an unrelated disk-space incident earlier in this
engagement (see P3 item 17's docker-compose local Neo4j+Redis work). These
tests are the offline substitute; a live build/run pass is still owed
whenever Docker is available again.
"""

import os
import re
import unittest

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BACKEND_DOCKERFILE = os.path.join(REPO_ROOT, "backend", "Dockerfile")
FRONTEND_DOCKERFILE = os.path.join(REPO_ROOT, "frontend", "Dockerfile")
COMPOSE_PATH = os.path.join(REPO_ROOT, "docker-compose.yml")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def stage_names(dockerfile_text):
    return re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", dockerfile_text, re.MULTILINE | re.IGNORECASE)


class BackendDockerfileTests(unittest.TestCase):
    def setUp(self):
        self.text = read(BACKEND_DOCKERFILE)

    def test_is_multi_stage(self):
        stages = stage_names(self.text)
        self.assertIn("dev", stages)
        self.assertIn("production", stages)
        self.assertGreater(len(stages), 2, "expected more than a single relabeled stage")

    def test_dev_stage_keeps_hot_reload_entrypoint(self):
        self.assertIn("fastapi dev", self.text)

    def test_production_stage_uses_non_dev_entrypoint(self):
        # `fastapi run` (production mode, no --reload) not `fastapi dev`.
        production_section = self.text.split("AS production\n")[-1]
        self.assertIn('"fastapi", "run"', production_section)
        self.assertNotIn("fastapi dev", production_section)

    def test_production_build_excludes_dev_dependency_group(self):
        self.assertIn("--no-dev", self.text)

    def test_production_stage_does_not_run_as_root(self):
        # No static `USER appuser` line anymore (real fix, see docker-
        # entrypoint.sh docstring): a Docker named volume's mount point is
        # created root-owned by the daemon on first attach, independent of
        # this image's own build-time chown, and a static USER directive
        # can't fix that after the fact. The container now boots as root
        # deliberately so the entrypoint can re-chown the mount, then drops
        # to appuser via gosu before the real process ever runs - verified
        # here by checking that hand-off actually happens, not by looking
        # for a USER line that no longer reflects how privilege drop works.
        production_section = self.text.split("AS production\n")[-1]
        self.assertIn('ENTRYPOINT ["docker-entrypoint.sh"]', production_section)
        entrypoint_script = read(os.path.join(REPO_ROOT, "backend", "docker-entrypoint.sh"))
        self.assertIn("gosu appuser", entrypoint_script)

    def test_production_stage_excludes_test_code(self):
        self.assertIn("rm -rf ./backend/tests", self.text)


class EvalArtifactSurvivesTestCodeStripTests(unittest.TestCase):
    """Real, confirmed bug found live: backend/api/admin_evaluations_api.py's
    RESULTS_PATH used to resolve under backend/tests/ - which the assertion
    right above (test_production_stage_excludes_test_code) confirms this
    same Dockerfile deletes entirely in every production build. GET /api/
    admin/evaluations always reported "no evaluation has been run yet" in
    production regardless of how many times evaluate_retrieval.py ran or
    the backend redeployed, since the artifact never survived the image
    build - only caught by live production verification, not by any test,
    since local dev's `dev` Dockerfile target never strips backend/tests/
    at all. This test parses the real `rm -rf` target from the Dockerfile
    text (not a hardcoded assumption of what it strips) and cross-checks
    it against the *real* RESULTS_PATH both the writer (evaluate_retrieval.
    py) and the reader (admin_evaluations_api.py) actually use - so a
    future edit reintroducing this exact bug class, on either side, fails
    fast without needing a real Docker build to catch it."""

    def _stripped_prefix(self) -> str:
        dockerfile_text = read(BACKEND_DOCKERFILE)
        match = re.search(r"RUN rm -rf \./(\S+)", dockerfile_text)
        self.assertIsNotNone(match, "expected a real `RUN rm -rf ./<path>` line in the production Dockerfile")
        # e.g. "backend/tests" -> the real repo-relative prefix this stage deletes.
        return match.group(1)

    def test_admin_evaluations_results_path_survives_the_real_strip(self):
        from backend.api import admin_evaluations_api

        stripped_prefix = self._stripped_prefix()
        repo_relative = os.path.relpath(admin_evaluations_api.RESULTS_PATH, REPO_ROOT)
        self.assertFalse(
            repo_relative.replace(os.sep, "/").startswith(stripped_prefix + "/"),
            f"admin_evaluations_api.RESULTS_PATH ({repo_relative}) resolves under "
            f"'{stripped_prefix}/', which the production Dockerfile deletes entirely - "
            "this artifact would always be missing in production.",
        )

    def test_evaluate_retrieval_results_path_survives_the_real_strip(self):
        import importlib.util

        stripped_prefix = self._stripped_prefix()
        script_path = os.path.join(REPO_ROOT, "backend", "scripts", "evaluate_retrieval.py")
        spec = importlib.util.spec_from_file_location("evaluate_retrieval", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        repo_relative = os.path.relpath(module.RESULTS_PATH, REPO_ROOT)
        self.assertFalse(
            repo_relative.replace(os.sep, "/").startswith(stripped_prefix + "/"),
            f"evaluate_retrieval.RESULTS_PATH ({repo_relative}) resolves under "
            f"'{stripped_prefix}/', which the production Dockerfile deletes entirely - "
            "the script would be writing an artifact production can never serve.",
        )

    def test_both_writer_and_reader_agree_on_the_same_real_path(self):
        """The two real halves of this feature (the batch writer, the
        read-only API) must point at the exact same file - a drift here
        would silently reintroduce "ran the script, dashboard still empty"
        even with both paths individually outside backend/tests/."""
        import importlib.util

        from backend.api import admin_evaluations_api

        script_path = os.path.join(REPO_ROOT, "backend", "scripts", "evaluate_retrieval.py")
        spec = importlib.util.spec_from_file_location("evaluate_retrieval", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            os.path.realpath(module.RESULTS_PATH),
            os.path.realpath(admin_evaluations_api.RESULTS_PATH),
        )


class FrontendDockerfileTests(unittest.TestCase):
    def setUp(self):
        self.text = read(FRONTEND_DOCKERFILE)

    def test_is_multi_stage(self):
        stages = stage_names(self.text)
        self.assertIn("dev", stages)
        self.assertIn("build", stages)
        self.assertIn("production", stages)

    def test_dev_stage_keeps_dev_server_entrypoint(self):
        self.assertIn('"npm", "run", "dev"', self.text)

    def test_production_stage_serves_static_build_not_the_dev_server(self):
        production_section = self.text.split("AS production\n")[-1]
        self.assertNotIn("npm run dev", production_section)
        self.assertNotIn("vite", production_section.lower())
        self.assertIn("nginx", self.text.split("FROM")[-1].split("AS production")[0].lower())

    def test_production_stage_proxies_api_calls_to_backend(self):
        # A static SPA build has no Vite dev-server proxy - without an
        # equivalent, every /api/* fetch in the built app would 404.
        nginx_conf_path = os.path.join(REPO_ROOT, "frontend", "nginx.conf.template")
        self.assertTrue(os.path.exists(nginx_conf_path))
        conf = read(nginx_conf_path)
        self.assertIn("location /api/", conf)
        self.assertIn("proxy_pass", conf)


class ComposeFileTargetsDevExplicitlyTests(unittest.TestCase):
    """
    Now that both Dockerfiles are multi-stage with `production` as the last
    stage, an unpinned `docker compose build` would default to building
    `production` instead of `dev` - silently changing the local dev
    workflow. Guard against losing the explicit `target: dev` pin.
    """

    def setUp(self):
        with open(COMPOSE_PATH, encoding="utf-8") as f:
            self.compose = yaml.safe_load(f)

    def test_backend_service_pins_dev_target(self):
        self.assertEqual(self.compose["services"]["backend"]["build"].get("target"), "dev")

    def test_ui_service_pins_dev_target(self):
        self.assertEqual(self.compose["services"]["ui"]["build"].get("target"), "dev")


if __name__ == "__main__":
    unittest.main()
