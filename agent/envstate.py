"""The parts of the environment that commands change outside the text-file snapshot: .venv, its Python packages, node_modules.
State = {"venv": bool, "pip": {name: version}, "npm": {name: version or "link:<path>"} or None when there is no node_modules}."""
import json
import os
import shlex
import shutil
import sys
from pathlib import Path


def env_state(root):
    root = Path(root)
    pip = {}
    for d in (root / ".venv").glob("lib/python*/site-packages/*.dist-info"):
        name, _, version = d.name[:-len(".dist-info")].partition("-")
        pip[name.lower().replace("_", "-")] = version
    nm, npm = root / "node_modules", None
    if nm.is_dir():
        npm = {}
        for pj in [*nm.glob("*/package.json"), *nm.glob("@*/*/package.json")]:
            try:
                meta = json.loads(pj.read_text())
            except (OSError, ValueError):
                meta = None
            version = str(meta.get("version", "?")) if isinstance(meta, dict) else "?"
            npm[pj.parent.relative_to(nm).as_posix()] = "link:" + os.path.realpath(pj.parent) if pj.parent.is_symlink() else version
    return {"venv": (root / ".venv").is_dir(), "pip": pip, "npm": npm}


def valid_state(st):
    return (isinstance(st, dict) and set(st) == {"venv", "pip", "npm"} and isinstance(st["venv"], bool)
            and all(isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in m.items())
                    for m in (st["pip"], st["npm"] or {})) and (st["npm"] is None or isinstance(st["npm"], dict)))


def reach(root, target, executor, timeout=600):
    """Change the environment until env_state(root) == target, using pip/uv/npm. Returns (reached, log)."""
    root, log = Path(root), []

    def sh(cmd):
        code, out, _, err = executor.run(cmd, root, timeout)
        log.append(f"$ {cmd}\n{out}{err or ''}")

    def remove(rel):
        p = root / rel
        if not p.parent.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"refusing to remove outside the workspace: {rel}")
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.exists():
            shutil.rmtree(p)
        if p.parent != root / "node_modules" and p.parent.name.startswith("@"):
            try:
                p.parent.rmdir()  # empty @scope directory
            except OSError:
                pass

    cur = env_state(root)
    if cur["venv"] and not target["venv"]:
        remove(".venv")
    if not cur["venv"] and target["venv"]:
        sh("uv venv .venv" if shutil.which("uv") else f"{shlex.quote(sys.executable)} -m venv .venv")
    cur = env_state(root)
    if target["venv"]:
        py = shlex.quote(str(root / ".venv" / "bin" / "python"))
        pip = f"{py} -m pip" if "pip" in cur["pip"] else None
        gone = [shlex.quote(n) for n in cur["pip"] if n not in target["pip"]]
        put = [shlex.quote(f"{n}=={v}") for n, v in target["pip"].items() if cur["pip"].get(n) != v]
        if gone:
            sh(f"{pip} uninstall -y {' '.join(gone)}" if pip else f"uv pip uninstall --python {py} {' '.join(gone)}")
        if put:
            sh(f"{pip} install {' '.join(put)}" if pip else f"uv pip install --python {py} {' '.join(put)}")
    have, want = cur["npm"] or {}, target["npm"]
    if want is None:
        remove("node_modules")
    else:
        for n in have:
            if n not in want:
                remove(f"node_modules/{n}")
        need = [shlex.quote(v[5:] if v.startswith("link:") else f"{n}@{v}") for n, v in want.items() if have.get(n) != v]
        if need:
            sh(f"npm install --no-save {' '.join(need)}")
    return env_state(root) == target, "\n".join(log)
