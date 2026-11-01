"""`apple-code doctor`: is this machine able to run the agent, and if not, what to do."""
import platform
import shutil
import sys

from .fm import FM, HELPER


def checks():
    """[(name, state, detail)] where state is 'ok', 'fail' (apple-code cannot work) or 'note' (worth knowing)."""
    out = []
    mac = platform.mac_ver()[0]
    out.append(("macOS 26 or newer", "ok" if mac and int(mac.split(".")[0]) >= 26 else "fail", mac or f"this is {platform.system()}, not macOS"))
    out.append(("Apple silicon", "ok" if platform.machine() == "arm64" else "fail", platform.machine()))
    out.append(("Python 3.12 or newer", "ok" if sys.version_info >= (3, 12) else "fail", platform.python_version()))
    try:
        import pytest
        out.append(("pytest (used to verify work)", "ok", pytest.__version__))
    except ImportError:
        out.append(("pytest (used to verify work)", "fail", "run: pip install pytest in the apple-code environment"))
    if not HELPER.exists():
        out.append(("model helper", "fail", f"not built ({HELPER}); run build.sh, or reinstall apple-code (needs: xcode-select --install)"))
    else:
        try:
            fm = FM()
            try:
                state = fm.status()
            finally:
                fm.close()
            out.append(("Apple on-device model", "ok" if state == "available" else "fail", state))
        except Exception as e:
            out.append(("Apple on-device model", "fail", f"the helper did not answer ({e})"))
    for tool, why in (("uv", "faster project environments"), ("npm", "JavaScript projects"), ("git", "reviewing and keeping the agent's work")):
        out.append((tool, "ok" if shutil.which(tool) else "note", shutil.which(tool) or f"not found ({why})"))
    return out


def report():
    """Print the checks; returns 0 if apple-code can run, 1 if not."""
    rows = checks()
    for name, state, detail in rows:
        print(f"  {'ok  ' if state == 'ok' else 'FAIL' if state == 'fail' else 'note'}  {name}: {detail}")
    failed = [n for n, s, _ in rows if s == "fail"]
    print("\nReady." if not failed else "\nNot ready: " + ", ".join(failed))
    return 1 if failed else 0
