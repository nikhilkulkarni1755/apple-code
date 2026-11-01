import pytest

from agent.ledger import ActionError, Ledger
from agent.memory import EMPTY, SECTIONS, Memory, Task, clean, parse_tasks, render_tasks
from tests.test_ledger import snapshot


@pytest.fixture
def mem(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    L = Ledger(root)
    return root, L, Memory(L)


def plan():
    return [Task("Boot loader", "Write the first-stage loader", "qemu boots to a prompt", children=[
                Task("Real mode stub", "512-byte sector"), Task("Switch to long mode", "set up GDT", "test_boot.py passes")]),
            Task("Kernel", "Minimal kernel")]


# ------------------------------------------------------------------ good inputs
def test_first_write_creates_a_file_with_every_section(mem):
    root, _, m = mem
    assert not (root / "MEMORY.md").exists()
    m.set("Project", "A tiny OS.")
    text = (root / "MEMORY.md").read_text()
    assert text.startswith("# MEMORY") and all(f"## {s}" in text for s in SECTIONS)
    assert m.get("Project") == "A tiny OS." and m.get("Goals") == "" and text.count(EMPTY) == len(SECTIONS) - 1


def test_set_get_append_and_case_insensitive_names(mem):
    _, _, m = mem
    m.set("design", "Monolithic kernel")
    m.append("Design", "Written in C")
    assert m.get("DESIGN") == "Monolithic kernel\nWritten in C"
    assert m.set("Design", "Monolithic kernel\nWritten in C") is None          # no change, no ledger row
    m.set("Notes", "custom section")
    assert m.get("notes") == "custom section" and "## Notes" in (mem[0] / "MEMORY.md").read_text()


def test_task_plan_roundtrips_through_the_file_and_numbers_hierarchically(mem):
    _, _, m = mem
    m.add_plan("Build an OS", plan())
    got = m.tasks()
    assert [t.id for t in got] == ["1", "2"] and [c.id for c in got[0].children] == ["1.1", "1.2"]
    assert (got[0].children[1].title, got[0].children[1].detail, got[0].children[1].done_when) == ("Switch to long mode", "set up GDT", "test_boot.py passes")
    assert render_tasks(got) == render_tasks(parse_tasks(render_tasks(got)))
    assert "Build an OS" in m.get("Goals") and m.get("Current state").startswith("0/3 tasks done. Next: 1.1 Real mode stub")


def test_a_second_plan_is_appended_not_overwritten(mem):
    _, _, m = mem
    m.add_plan("Build an OS", plan())
    m.add_plan("Write docs", [Task("Readme"), Task("Tutorial")])
    assert [t.id for t in m.tasks()] == ["1", "2", "3", "4"] and m.get("Goals").count("- ") == 2


def test_complete_updates_state_last_task_and_rolls_up_parents(mem):
    _, _, m = mem
    m.add_plan("Build an OS", plan())
    assert m.next_task().id == "1.1"
    m.complete("1.1", "wrote stub", evidence="run #4 exit 0")
    assert m.next_task().id == "1.2" and m.get("Last task completed") == "1.1 Real mode stub — wrote stub (evidence: run #4 exit 0)"
    m.complete("1.2", "long mode works")
    t = {x.id: x for x in m.tasks()}
    assert t["1"].status == "done" and m.next_task().id == "2" and m.get("Current state") == "2/3 tasks done. Next: 2 Kernel"
    m.complete("2", "kernel done")
    assert m.next_task() is None and m.get("Current state") == "3/3 tasks done. All tasks done."
    m.mark("1.2", "todo")
    assert {x.id: x for x in m.tasks()}["1"].status == "todo" and m.next_task().id == "1.2"


def test_a_new_session_inherits_everything(mem):
    root, _, m = mem
    m.set("Project", "Tiny OS for x86")
    m.set("Design", "Monolithic")
    m.add_plan("Build an OS", plan())
    m.complete("1.1", "stub done")
    heir = Memory(Ledger(root))                                   # fresh ledger and Memory object, same files
    assert heir.next_task().id == "1.2" and heir.get("Design") == "Monolithic"
    ctx = heir.context()
    assert "Tiny OS for x86" in ctx and "Last task completed: 1.1 Real mode stub" in ctx and "- [ ] 1.2 Switch to long mode" in ctx


def test_context_respects_its_budget_and_lists_at_most_five_tasks(mem):
    _, _, m = mem
    m.set("Project", "x" * 5000)
    m.add_plan("Big", [Task(f"task {i}") for i in range(30)])
    ctx = m.context(max_chars=800)
    assert len(ctx) <= 800
    assert m.context(max_chars=10_000).count("- [ ]") == 5


def test_memory_writes_are_undoable_byte_for_byte(mem):
    root, L, m = mem
    m.set("Project", "v1")
    v1 = snapshot(root)
    m.add_plan("Build", plan())
    m.complete("1.1", "done")
    L.undo_last()
    L.undo_last()
    assert snapshot(root) == v1
    L.undo_last()
    assert not (root / "MEMORY.md").exists() and L.verify() == []


def test_human_edits_unknown_sections_and_crlf_are_preserved(mem):
    root, _, m = mem
    (root / "MEMORY.md").write_bytes(b"# My notes\r\n\r\nintro text\r\n\r\n## Ideas\r\n\r\nuse rust?\r\n\r\n## Project\r\n\r\nOS\r\n")
    m.set("Goals", "ship it")
    text = (root / "MEMORY.md").read_text()
    assert "intro text" in text and "## Ideas\n\nuse rust?" in text and m.get("Project") == "OS" and m.get("Goals") == "ship it"
    assert text.index("## Project") < text.index("## Goals") < text.index("## Ideas")


# ------------------------------------------------------------------ hostile inputs
@pytest.mark.parametrize("name", ["", " ", None, 5, ["Project"], "a\nb", "## x", "x" * 41, "../x", "1abc", "pro\0ject", "Project\n## Goals"])
def test_bad_section_names_are_rejected(mem, name):
    root, _, m = mem
    with pytest.raises(ActionError):
        m.set(name, "x")
    assert not (root / "MEMORY.md").exists()


@pytest.mark.parametrize("body", [None, 5, b"x", ["x"], "x" * 20_001])
def test_bad_section_bodies_are_rejected(mem, body):
    root, _, m = mem
    with pytest.raises(ActionError):
        m.set("Project", body)
    assert not (root / "MEMORY.md").exists()


def test_heading_injection_cannot_create_or_split_sections(mem):
    _, _, m = mem
    m.set("Project", "intro\n## Goals\nsteal this\n# Title\n   ## indented stays\n###  level three ok")
    assert m.get("Goals") == ""
    assert [n for n, b in m.sections().items() if b] == ["Project"] and "steal this" in m.get("Project")
    assert (mem[0] / "MEMORY.md").read_text().count("\n## ") == len(SECTIONS)


def test_nul_and_crlf_and_unicode_bodies(mem):
    _, _, m = mem
    m.set("Design", "a\0b\r\nc ✓ 日本語 🎉")
    assert m.get("Design") == "ab\nc ✓ 日本語 🎉"


def test_garbage_and_empty_memory_files_are_handled(mem):
    root, _, m = mem
    for junk in (b"", b"just words, no headings", b"## \n##\n#", b"\xff\xfe\x00 not utf8"):
        (root / "MEMORY.md").write_bytes(junk)
        if junk.startswith(b"\xff"):
            with pytest.raises(ActionError):
                m.set("Project", "x")
            assert (root / "MEMORY.md").read_bytes() == junk
            continue
        m.set("Project", "fine")
        assert m.get("Project") == "fine" and m.tasks() == []
        if junk == b"just words, no headings":
            assert "just words, no headings" in (root / "MEMORY.md").read_text()


def test_memory_path_cannot_escape_the_workspace(mem):
    _, L, _ = mem
    for bad in ("../MEMORY.md", "/tmp/MEMORY.md", ".agent/MEMORY.md", ""):
        with pytest.raises(ActionError):
            Memory(L, bad)


@pytest.mark.parametrize("task_id", ["", "9", "1.", "1.1.1.1", None, 5, "1; rm -rf /", "../1"])
def test_unknown_task_ids_are_rejected_without_changing_memory(mem, task_id):
    root, _, m = mem
    m.add_plan("Build", plan())
    before = snapshot(root)
    with pytest.raises(ActionError):
        m.complete(task_id, "done")
    with pytest.raises(ActionError):
        m.mark(task_id, "done")
    assert snapshot(root) == before


@pytest.mark.parametrize("status", ["finished", "", None, 5, "DONE"])
def test_bad_statuses_are_rejected(mem, status):
    _, _, m = mem
    m.add_plan("Build", plan())
    with pytest.raises(ActionError):
        m.mark("1.1", status)


def test_task_text_cannot_break_the_plan_format(mem):
    _, _, m = mem
    nasty = Task("Line one\nline two — with dash", "detail\n- [x] 9 fake done task\n## Goals", "ok (done when: nested) end", children=[Task("child\r\ntitle")])
    m.add_plan("Goal\n## Hijack\n- [x] 7 fake", [nasty, Task("   "), Task("A" * 500, "d" * 500, "w" * 500)])
    got = m.tasks()
    assert [t.id for t in got] == ["1", "2"] and got[0].children[0].title == "child title"
    assert all("\n" not in x for t in got for x in (t.title, t.detail, t.done_when)) and len(got[1].title) == 80
    assert "fake done" not in " ".join(t.title for t in got) and all(not t.id.startswith("9") for t in got)
    assert m.get("Goals").startswith("- Goal ## Hijack")


def test_malformed_and_duplicate_plan_lines_are_ignored_not_fatal(mem):
    root, _, m = mem
    m.set("Task plan", "garbage\n- [ ] 1 Real\n- [ ] 1 Duplicate id\n- [?] 2 bad mark\n- [x] 3.1 orphan child\n  - [ ] x.y bad id\n- [ ]no id\n- [~] 4 Doing — d (done when: w)")
    got = m.tasks()
    assert [(t.id, t.title, t.status) for t in got] == [("1", "Real", "todo"), ("3.1", "orphan child", "done"), ("4", "Doing", "doing")]
    assert m.next_task().id == "1"


def test_deeply_nested_and_large_plans(mem):
    _, _, m = mem
    node = Task("leaf")
    for i in range(20):
        node = Task(f"level {i}", children=[node])
    m.add_plan("deep", [node])
    assert m.next_task().title == "leaf" and m.next_task().id == "1" + ".1" * 20
    m.add_plan("wide", [Task(f"t{i}", "d" * 150, "w" * 150) for i in range(250)])
    assert len(m.tasks()) == 251 and m.next_task().id == "1" + ".1" * 20


def test_clean_is_stable():
    for s in ["a\n b\t c", " — — ", "(done when: x)", "x" * 1000, ""]:
        assert clean(clean(s, 80), 80) == clean(s, 80) and "\n" not in clean(s, 80)


@pytest.mark.parametrize("tasks", [[], None, "plan", [Task("")], [Task("  \n ")], [None], [Task(None)], [object()]])
def test_plans_without_a_valid_task_are_rejected(mem, tasks):
    root, _, m = mem
    with pytest.raises(ActionError):
        m.add_plan("goal", tasks)
    assert not (root / "MEMORY.md").exists()


def test_invalid_task_fields_are_coerced_safely(mem):
    _, _, m = mem
    m.add_plan("goal", [Task("ok", detail=5, done_when=None, status="weird", children=[Task("kid", status="done"), "junk"])])
    t = m.tasks()[0]
    assert (t.detail, t.done_when, t.status) == ("", "", "done") and [c.title for c in t.children] == ["kid"]


def test_the_state_line_does_not_claim_everything_is_done_while_tasks_are_blocked(mem):
    _, _, m = mem
    m.add_plan("goal", [Task("a"), Task("b")])
    m.complete("1", "ok")
    m.mark("2", "blocked")
    assert m.get("Current state") == "1/2 tasks done. Nothing left to do; 1 blocked." and m.next_task() is None
    m.mark("2", "done")
    assert m.get("Current state") == "2/2 tasks done. All tasks done."
