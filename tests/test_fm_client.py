"""agent.fm against a scripted fake helper (protocol, errors, hostile output), plus real-model smoke tests (pytest -m fm)."""
import json
import stat
import sys

import pytest

import agent.fm as fm_module
from agent.fm import FM, HELPER, Tool

FAKE = '''#!{python}
import json, sys
def emit(o): print(json.dumps(o)); sys.stdout.flush()
for line in sys.stdin:
    req = json.loads(line)
    if req["cmd"] == "count":
        emit({{"event": "count", "tools": len(req["tools"]), "instructions": len(req["instructions"]), "prompt": len(req["prompt"])}}); continue
    p = req["prompt"]
    if p == "die": sys.exit(1)
    if p == "garbage": print("not json at all"); sys.stdout.flush(); continue
    if p == "error": emit({{"event": "error", "kind": "guardrail", "message": "blocked"}}); continue
    if p == "stop": emit({{"event": "done", "content": "", "stopped": True}}); continue
    if p.startswith("call "):
        for i, spec in enumerate(p[5:].split("|"), 1):
            name, args = spec.split(" ", 1)
            emit({{"event": "tool_call", "id": i, "name": name, "args": json.loads(args)}})
            reply = json.loads(sys.stdin.readline())
            assert reply["id"] == i
            last = reply["result"]
        emit({{"event": "done", "content": last, "stopped": False}}); continue
    if p == "tokens": emit({{"event": "done", "content": "x", "stopped": False, "tokens": 321, "context": 8192}}); continue
    emit({{"event": "done", "content": "echo:" + p + "|" + req["instructions"], "stopped": False}})
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fake-helper"
    script.write_text(FAKE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(fm_module, "HELPER", script)
    client = FM()
    yield client
    client.p.kill()


def tool(name, handler, params=()):
    return Tool(name, f"{name} tool", [{"name": p, "type": "string", "description": "", "optional": False} for p in params], handler)


def test_plain_run_returns_content_and_passes_instructions(fake):
    assert fake.run("hello", instructions="be brief") == {"content": "echo:hello|be brief", "stopped": False, "calls": []}


def test_the_helper_can_report_how_much_of_the_window_a_session_used(fake):
    assert fake.run("tokens") == {"content": "x", "stopped": False, "calls": [], "tokens": 321, "context": 8192}


def test_tool_calls_are_executed_in_python_and_results_go_back(fake):
    t = tool("add", lambda a, b: str(int(a) + int(b)), ["a", "b"])
    r = fake.run('call add {"a": "2", "b": "3"}', [t])
    assert r["content"] == "5" and r["calls"] == [("add", {"a": "2", "b": "3"}, "5")]


def test_several_tool_calls_in_one_turn_run_in_order(fake):
    seen = []
    t = tool("note", lambda x: seen.append(x) or f"saw {x}", ["x"])
    r = fake.run('call note {"x": "1"}|note {"x": "2"}|note {"x": "3"}', [t])
    assert seen == ["1", "2", "3"] and r["content"] == "saw 3" and len(r["calls"]) == 3


@pytest.mark.parametrize("handler,args,expect", [
    (lambda x: 1 / 0, '{"x": "1"}', "error: division by zero"),
    (lambda: "never", '{"x": "1"}', "error: "),                       # wrong arguments (TypeError)
    (lambda x: (_ for _ in ()).throw(RuntimeError("boom")), '{"x": "1"}', "error: boom"),
])
def test_a_failing_tool_returns_its_error_to_the_model_instead_of_crashing(fake, handler, args, expect):
    r = fake.run(f"call t {args}", [tool("t", handler, ["x"])])
    assert r["content"].startswith(expect) and "error" not in r


def test_an_unknown_tool_name_is_reported_as_text(fake):
    assert fake.run('call missing {"x": "1"}', [tool("t", lambda x: "ok", ["x"])])["content"].startswith("error:")


def test_odd_tool_results_survive_the_json_pipe(fake):
    for text in ["", "line1\nline2\n", "tab\t\"quote\" \\ back ✓ 日本語 🎉", "x" * 1_000_000, "\0nul", 'json {"a": 1}']:
        r = fake.run('call t {"x": "1"}', [tool("t", lambda x, v=text: v, ["x"])])
        assert r["content"] == text
    assert fake.run('call t {"x": "1"}', [tool("t", lambda x: 42, ["x"])])["content"] == "42"      # non-str results are stringified


def test_helper_error_events_and_stop_are_returned_not_raised(fake):
    assert fake.run("error") == {"error": "guardrail", "message": "blocked", "calls": []}
    assert fake.run("stop") == {"content": "", "stopped": True, "calls": []}
    assert fake.run("still alive")["content"].startswith("echo:")                                  # the session continues afterwards


def test_a_dead_or_garbled_helper_raises_a_clear_error(fake):
    with pytest.raises(RuntimeError, match="invalid output"):
        fake.run("garbage")
    with pytest.raises(RuntimeError, match="exited"):
        fake.run("die")


def test_count_and_tool_specs_never_leak_handlers(fake):
    t = tool("a", lambda: "x", ["p"])
    assert "handler" not in t.spec() and t.spec()["parameters"][0]["name"] == "p"
    assert fake.count([t, t], "abc", "hello") == {"event": "count", "tools": 2, "instructions": 3, "prompt": 5}
    json.dumps(t.spec())


# ---------------------------------------------------------------- real model (run with: pytest -m fm)
real = pytest.mark.skipif(not HELPER.exists(), reason="build the helper: ./build.sh")


@pytest.fixture(scope="module")
def model():
    client = FM()
    yield client
    client.close()


@real
@pytest.mark.fm
def test_real_model_counts_tokens_exactly_and_overflow_is_a_typed_error(model):
    c = model.count([tool("t", lambda x: "", ["x"])], "You are a coder.", "hello")
    assert c["event"] == "count" and all(isinstance(c[k], int) and c[k] > 0 for k in ("tools", "instructions", "prompt"))
    r = model.run("How many items are listed below? " + " ".join(f"Item {i} costs {i * 7} dollars." for i in range(1, 2500)))
    assert r["error"] == "context_overflow"


@real
@pytest.mark.fm
def test_real_model_calls_a_tool_and_stops_at_the_call_limit(model):
    seen = []
    t = tool("add_note", lambda text: seen.append(text) or "saved", ["text"])
    r = model.run("Save the note 'buy milk' using the add_note tool, then save the note 'call mom' using add_note.", [t], max_tool_calls=1)
    assert len(seen) == 1 and r["stopped"] is True and "error" not in r


@real
@pytest.mark.fm
def test_real_model_survives_a_failing_tool(model):
    r = model.run("Read the secret using the read_secret tool and tell me what it says.", [tool("read_secret", lambda x: 1 / 0, ["x"])], max_tool_calls=3)
    assert "error" not in r or r["error"] in ("guardrail",)
