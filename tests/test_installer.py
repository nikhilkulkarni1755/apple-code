"""The installer, the release artifacts, and the global command. The fast tests cover its safety rules; the real installs run with `pytest -m net`."""
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

import agent

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
mac = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="needs macOS with the Swift compiler")
tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("rsync", "zip", "npm", "node")), reason="needs rsync, zip, npm and node")


@pytest.fixture
def box(tmp_path):
    """A pretend user: their own HOME, shell startup file, and install locations. Nothing touches the real home folder."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("# my shell setup\nalias ll='ls -l'\n")
    return home


def sh(box, *args, script=INSTALL, cwd=None, stdin=None, check=False):
    env = {**os.environ, "HOME": str(box), "SHELL": "/bin/zsh"}
    r = subprocess.run(["sh", str(script), *args], env=env, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=600)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


# ------------------------------------------------------------------ fast: syntax, options, and the safety rules
@pytest.mark.parametrize("script", ["install.sh", "scripts/make-dist.sh", "scripts/Install.command"])
def test_shell_scripts_parse_and_are_executable(script):
    assert subprocess.run(["sh", "-n", str(ROOT / script)]).returncode == 0
    assert os.access(ROOT / script, os.X_OK)


def test_help_lists_every_option(box):
    out = sh(box, "--help").stdout
    for option in ("--from", "--home", "--prefix", "--no-modify-path", "--uninstall", "--help"):
        assert option in out


@pytest.mark.parametrize("args", [["--bogus"], ["--from"], ["--home"], ["--prefix"], ["stray-argument"]])
def test_bad_options_fail_loudly_and_change_nothing(box, args):
    before = sorted(p.name for p in box.iterdir())
    r = sh(box, *args)
    assert r.returncode == 1 and r.stderr.startswith("error:") and sorted(p.name for p in box.iterdir()) == before


def test_uninstalling_when_nothing_is_installed_is_harmless(box):
    r = sh(box, "--uninstall")
    assert r.returncode == 0 and "uninstalled" in r.stdout and (box / ".zshrc").read_text() == "# my shell setup\nalias ll='ls -l'\n"


def test_uninstall_never_deletes_a_folder_it_did_not_create(box):
    mine = box / "my-stuff"
    mine.mkdir()
    (mine / "precious.txt").write_text("keep\n")
    r = sh(box, "--uninstall", "--home", str(mine))
    assert r.returncode == 0 and "left" in r.stdout and (mine / "precious.txt").read_text() == "keep\n"
    r = sh(box, "--uninstall", "--home", str(box))                       # even if pointed at the home folder itself
    assert (box / ".zshrc").exists() and (mine / "precious.txt").exists()


def test_uninstall_removes_only_its_own_launcher_and_its_own_path_line(box):
    bin_dir = box / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "apple-code").write_text("#!/bin/sh\necho somebody else's program\n")
    (box / ".zshrc").write_text('alias ll="ls -l"\nexport PATH="/x/bin:$PATH" # added by the apple-code installer\nexport EDITOR=vim\n')
    sh(box, "--uninstall", check=True)
    assert (bin_dir / "apple-code").exists()                             # not ours: no launcher marker
    assert (box / ".zshrc").read_text() == 'alias ll="ls -l"\nexport EDITOR=vim\n'


@mac
def test_install_refuses_to_use_a_non_empty_folder_it_did_not_create(box):
    mine = box / "my-stuff"
    mine.mkdir()
    (mine / "precious.txt").write_text("keep\n")
    r = sh(box, "--from", str(ROOT), "--home", str(mine), "--prefix", str(box / "bin"))
    assert r.returncode == 1 and "was not created by this installer" in r.stderr
    assert sorted(p.name for p in mine.iterdir()) == ["precious.txt"] and not (box / "bin").exists()


@mac
def test_install_refuses_to_overwrite_someone_elses_command(box):
    (box / "bin").mkdir()
    (box / "bin" / "apple-code").write_text("#!/bin/sh\necho not ours\n")
    r = sh(box, "--from", str(ROOT), "--home", str(box / "h"), "--prefix", str(box / "bin"))
    assert r.returncode == 1 and "not an apple-code launcher" in r.stderr and "not ours" in (box / "bin" / "apple-code").read_text()
    assert not (box / "h").exists()


@mac
def test_install_rejects_something_that_is_not_apple_code_source(box, tmp_path):
    (tmp_path / "other").mkdir()
    r = sh(box, "--from", str(tmp_path / "other"), "--home", str(box / "h"), "--prefix", str(box / "bin"))
    assert r.returncode == 1 and not (box / "h" / "venv").exists()
    r = sh(box, "--from", str(tmp_path / "missing"), "--home", str(box / "h"), "--prefix", str(box / "bin"))
    assert r.returncode == 1 and "cannot find" in r.stderr


# ------------------------------------------------------------------ fast: the release is consistent and complete
def test_every_way_of_starting_the_program_ignores_the_current_folder():
    """`python -m agent` would import a project's own agent/ folder instead of apple-code; -I stops that."""
    assert '"$VENV/bin/python" -I -m agent' in INSTALL.read_text() and '"$VENV/bin/python" -I -c' in INSTALL.read_text()
    assert '["-I", "-m", "agent"' in (ROOT / "npm" / "bin" / "apple-code.js").read_text()


def test_the_version_is_the_same_everywhere():
    npm = json.loads((ROOT / "npm" / "package.json").read_text())
    assert agent.__version__ == npm["version"] and re.fullmatch(r"\d+\.\d+\.\d+", agent.__version__)
    assert npm["bin"] == {"apple-code": "bin/apple-code.js"} and npm["os"] == ["darwin"] and npm["cpu"] == ["arm64"]


def test_the_readme_install_commands_match_the_real_installer():
    readme = (ROOT / "README.md").read_text()
    assert f"curl -fsSL https://raw.githubusercontent.com/nikhilkulkarni1755/apple-code/main/install.sh | sh" in readme
    assert re.search(r'REPO="([^"]+)"', INSTALL.read_text()).group(1) in readme
    for command in ("npx apple-code", "apple-code solve", "apple-code doctor", "apple-code uninstall", "apple-code update"):
        assert command in readme


@tools
def test_make_dist_builds_lean_artifacts_with_the_right_contents():
    subprocess.run([str(ROOT / "scripts" / "make-dist.sh")], check=True, capture_output=True, cwd=ROOT, timeout=300)
    dist, v = ROOT / "dist", agent.__version__
    assert sorted(p.name for p in dist.iterdir()) == sorted([f"apple-code-{v}.tar.gz", f"apple-code-{v}.zip", f"apple-code-{v}.tgz", "SHA256SUMS"])
    names = zipfile.ZipFile(dist / f"apple-code-{v}.zip").namelist()
    for needed in ("install.sh", "Install.command", "pyproject.toml", "README.md", "agent/helper/main.swift", "agent/__main__.py"):
        assert f"apple-code-{v}/{needed}" in names
    assert not any(re.search(r"/tests/|__pycache__|fm-helper$|\.pyc$|\.DS_Store|\.venv|\.agent/", n) for n in names)
    tgz = subprocess.run(["tar", "-tzf", str(dist / f"apple-code-{v}.tgz")], capture_output=True, text=True).stdout.split()
    assert "package/bin/apple-code.js" in tgz and "package/payload/install.sh" in tgz and "package/payload/agent/helper/main.swift" in tgz
    assert not any("fm-helper" in n or "__pycache__" in n for n in tgz)
    unzip = subprocess.run(["unzip", "-Z", str(dist / f"apple-code-{v}.zip")], capture_output=True, text=True).stdout
    assert re.search(r"-rwx\S+\s.*Install\.command", unzip) and re.search(r"-rwx\S+\s.*install\.sh", unzip)       # still executable after zipping
    assert not (ROOT / "npm" / "payload").exists()                                                       # the build leaves no litter
    shas = (dist / "SHA256SUMS").read_text()
    assert f"apple-code-{v}.zip" in shas and len(shas.splitlines()) == 3


# ------------------------------------------------------------------ real installs (pytest -m net): all three routes, upgrade, uninstall
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def installed_ok(box, project):
    """The command works from an unrelated folder, with no checkout and no PYTHONPATH."""
    exe = box / ".local" / "bin" / "apple-code"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")} | {"HOME": str(box)}
    (project / "agent").mkdir(exist_ok=True)                                                             # a project with its own "agent" package
    (project / "agent" / "__init__.py").write_text("raise RuntimeError('the project agent/ shadowed apple-code')\n")
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, env=env, cwd=project)
    assert version.stdout.strip() == f"apple-code {agent.__version__}", version.stderr
    ran = subprocess.run([str(exe), "run", "echo hello from a global install"], capture_output=True, text=True, env=env, cwd=project)
    assert ran.returncode == 0 and "exit=0" in ran.stdout, ran.stderr
    doctor = subprocess.run([str(exe), "doctor"], capture_output=True, text=True, env=env, cwd=project)
    assert doctor.returncode == 0 and "Ready." in doctor.stdout, doctor.stdout
    assert (project / ".agent" / "ledger.db").exists()                                                   # state goes in the project, not the install
    return exe, env


@pytest.fixture(scope="module")
def release():
    if not all(shutil.which(t) for t in ("rsync", "zip", "npm", "node", "swiftc")):
        pytest.skip("needs rsync, zip, npm, node and swiftc")
    subprocess.run([str(ROOT / "scripts" / "make-dist.sh")], check=True, capture_output=True, cwd=ROOT, timeout=300)
    return ROOT / "dist"


@pytest.mark.net
@mac
def test_install_from_a_folder_then_upgrade_then_uninstall(box, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sh(box, "--from", str(ROOT), check=True)
    installed_ok(box, project)
    assert (box / ".apple-code" / "install.sh").exists() and (box / ".apple-code" / ".apple-code-home").exists()
    assert "apple-code" in (box / ".zshrc").read_text() and (box / ".zshrc").read_text().startswith("# my shell setup")
    sh(box, "--from", str(ROOT), check=True)                                                             # install again over the top
    assert (box / ".zshrc").read_text().count("added by the apple-code installer") == 1                 # PATH is not added twice
    installed_ok(box, project)
    exe = box / ".local" / "bin" / "apple-code"
    r = subprocess.run([str(exe), "uninstall"], capture_output=True, text=True, env={**os.environ, "HOME": str(box)}, cwd=project)
    assert r.returncode == 0 and "uninstalled" in r.stdout
    assert not (box / ".apple-code").exists() and not exe.exists() and (box / ".zshrc").read_text() == "# my shell setup\nalias ll='ls -l'\n"
    assert (project / ".agent").exists()                                                                 # projects are left alone


@pytest.mark.net
@mac
@pytest.mark.parametrize("original,restored", [("", ""), ("line one\nline two\n", "line one\nline two\n"), ("no trailing newline", "no trailing newline\n")])
def test_the_path_edit_is_one_line_and_uninstall_takes_it_out_again(box, original, restored):
    (box / ".zshrc").write_text(original)
    sh(box, "--from", str(ROOT), check=True)
    added = (box / ".zshrc").read_text()[len(original):]
    assert added.count("\n") == (2 if original and not original.endswith("\n") else 1) and added.strip().startswith("export PATH=")
    sh(box, "--uninstall", check=True)
    assert (box / ".zshrc").read_text() == restored                    # a file with no final newline gets one; nothing else changes


@pytest.mark.net
@mac
def test_no_modify_path_leaves_shell_files_alone(box):
    sh(box, "--from", str(ROOT), "--no-modify-path", check=True)
    assert (box / ".zshrc").read_text() == "# my shell setup\nalias ll='ls -l'\n" and (box / ".local" / "bin" / "apple-code").exists()


@pytest.mark.net
@mac
def test_install_from_the_downloadable_zip(box, tmp_path, release):
    project = tmp_path / "project"
    project.mkdir()
    folder = tmp_path / "unzipped"
    subprocess.run(["unzip", "-q", str(release / f"apple-code-{agent.__version__}.zip"), "-d", str(folder)], check=True)
    unpacked = folder / f"apple-code-{agent.__version__}"
    before = sorted(p.name for p in unpacked.iterdir())
    sh(box, script=unpacked / "install.sh", cwd=unpacked, check=True)                                    # exactly what a user does after unzipping
    installed_ok(box, project)
    assert sorted(p.name for p in unpacked.iterdir()) == before                                          # nothing built inside the download folder
    sh(box, "--from", str(release / f"apple-code-{agent.__version__}.zip"), "--home", str(box / "other"), "--prefix", str(box / "bin2"), check=True)
    assert (box / "bin2" / "apple-code").exists()                                                        # --from also takes the zip file itself


@pytest.mark.net
@mac
def test_install_with_the_curl_pipe(box, tmp_path, release):
    project = tmp_path / "project"
    project.mkdir()
    www = tmp_path / "www"
    www.mkdir()
    shutil.copy(release / f"apple-code-{agent.__version__}.tar.gz", www)
    shutil.copy(INSTALL, www)
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(www), **k)
    handler.log_message = lambda *a: None
    srv = http.server.HTTPServer(("127.0.0.1", free_port()), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        script = subprocess.run(["curl", "-fsSL", f"{base}/install.sh"], capture_output=True, text=True, check=True).stdout
        r = subprocess.run(["sh", "-s", "--", "--from", f"{base}/apple-code-{agent.__version__}.tar.gz"], input=script, capture_output=True, text=True,
                           env={**os.environ, "HOME": str(box), "SHELL": "/bin/zsh"}, timeout=600)                    # exactly: curl ... | sh -s -- --from URL
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        srv.shutdown()
    installed_ok(box, project)
    assert (box / ".apple-code" / "install.sh").exists()                                                 # saved even though it was piped, so update/uninstall work


@pytest.mark.net
@mac
def test_install_through_npm_and_run_instantly_afterwards(box, tmp_path, release):
    import time
    project = tmp_path / "project"
    project.mkdir()
    env = {**os.environ, "HOME": str(box), "SHELL": "/bin/zsh"}
    tgz = release / f"apple-code-{agent.__version__}.tgz"
    first = subprocess.run(["npm", "exec", "--yes", f"--package={tgz}", "--", "apple-code", "--version"], capture_output=True, text=True, env=env, cwd=project, timeout=600)
    assert first.returncode == 0 and f"apple-code {agent.__version__}" in first.stdout, first.stdout + first.stderr
    installed_ok(box, project)
    start = time.time()
    again = subprocess.run(["npm", "exec", "--yes", f"--package={tgz}", "--", "apple-code", "--version"], capture_output=True, text=True, env=env, cwd=project)
    assert again.stdout.strip() == f"apple-code {agent.__version__}" and time.time() - start < 10 and "Setting up" not in again.stderr
    explicit = subprocess.run(["npm", "exec", "--yes", f"--package={tgz}", "--", "apple-code", "install"], capture_output=True, text=True, env=env, cwd=project, timeout=600)
    assert explicit.returncode == 0 and "installed" in explicit.stdout                                   # `apple-code install` reinstalls on request
