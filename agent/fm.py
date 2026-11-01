"""Python client for the Swift helper (agent/helper/main.swift): one model session per `run` call."""
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "helper" / "fm-helper"   # built by build.sh, or by the installer


@dataclass
class Tool:
    name: str
    description: str
    parameters: list  # [{name, type: string|integer|number|boolean, description, optional}]
    handler: object = None  # callable(**args) -> str

    def spec(self):
        return {k: v for k, v in asdict(self).items() if k != "handler"}


class FM:
    def __init__(self):
        self.p = subprocess.Popen([str(HELPER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def _read(self):
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("fm helper exited")
        try:
            return json.loads(line)
        except ValueError:
            raise RuntimeError(f"fm helper sent invalid output: {line[:100]!r}") from None

    def status(self):
        """Whether the on-device model can be used: 'available', or the reason it cannot."""
        self._send({"cmd": "status"})
        return self._read()["availability"]

    def count(self, tools=(), instructions="", prompt=""):
        """Exact token counts: {'tools': n, 'instructions': n, 'prompt': n}."""
        self._send({"cmd": "count", "instructions": instructions, "prompt": prompt, "tools": [t.spec() for t in tools]})
        return self._read()

    def run(self, prompt, tools=(), instructions="", max_tool_calls=8, temperature=None):
        """One fresh session. Tool calls are executed here (a failing handler returns its error to the model).
        Returns {'content', 'stopped', 'calls': [(name, args, result)]} or {'error': kind, 'message', 'calls'}."""
        by_name = {t.name: t for t in tools}
        self._send({"cmd": "run", "instructions": instructions, "prompt": prompt, "tools": [t.spec() for t in tools],
                    "max_tool_calls": max_tool_calls, "temperature": temperature})
        calls = []
        while True:
            msg = self._read()
            if msg["event"] == "tool_call":
                try:
                    result = str(by_name[msg["name"]].handler(**msg["args"]))
                except Exception as e:
                    result = f"error: {e}"
                calls.append((msg["name"], msg["args"], result))
                self._send({"id": msg["id"], "result": result})
            elif msg["event"] == "done":   # tokens / context: how much of the window this session used, when the helper says so
                return {"content": msg["content"], "stopped": msg["stopped"], "calls": calls, **{k: msg[k] for k in ("tokens", "context") if k in msg}}
            else:
                return {"error": msg["kind"], "message": msg["message"], "calls": calls}

    def close(self):
        self.p.stdin.close()
        self.p.wait()
