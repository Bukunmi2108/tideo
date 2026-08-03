import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_vps_deploy_cleans_source_checks_runtime_and_rolls_back():
    script = (ROOT / "deploy/deploy.sh").read_text()

    for contract in (
        "git -C \"$SOURCE_ROOT\" clean -ffdx",
        "status --porcelain",
        "services_running",
        "workers_ready",
        "dispatcher:heartbeat",
        "audit:heartbeat",
        "beat:heartbeat",
        "--no-build --remove-orphans",
        "--connect-timeout 10 --max-time 30",
    ):
        assert contract in script
    assert "docker image prune" not in script


def test_legacy_space_bundle_is_gone():
    for path in (
        "deploy/.env.deploy",
        "deploy/Dockerfile",
        "deploy/README.space.md",
        "deploy/deploy-space.sh",
        "deploy/kraft-tideo.properties",
        "deploy/supervisord.conf",
        "deploy/scripts",
    ):
        assert not (ROOT / path).exists()


def test_github_actions_are_pinned_to_full_shas():
    for workflow in ("deploy.yml", "tests.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text()
        refs = re.findall(r"uses:\s+[^\s]+@([^\s]+)", text)
        assert refs
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)
