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
