import subprocess


def test_git_server_serves_https_ssh_and_scp_urls(git_server):
    repo = git_server.repo("team/pkg")
    sha = repo.commit({"apm.yml": "name: pkg\nversion: '1.0.0'\n"})
    repo.tag("v1.0.0")
    for url in (repo.https, repo.ssh, repo.scp):
        out = subprocess.run(["git", "ls-remote", url], capture_output=True, text=True, check=True)
        assert sha in out.stdout
        assert "refs/tags/v1.0.0" in out.stdout


def test_model_owner_and_marker(tmp_path):
    from apm_lite.model import DepSpec, ResolvedPackage

    spec = DepSpec(raw="x", kind="remote", key="git.test/team/pkg", repo_path="team/pkg")
    pkg = ResolvedPackage(
        spec=spec,
        commit="a" * 40,
        ref_is_immutable=True,
        depth=1,
        parent_key=None,
        parent_repo_url=None,
        source_dir=tmp_path,
        package_type="apm_package",
        name="pkg",
        version="1.0.0",
        content_hash=None,
    )
    assert (pkg.owner, pkg.marker, pkg.label, pkg.repo_url) == (
        "git.test/team/pkg",
        "team/pkg",
        "pkg",
        "team/pkg",
    )
