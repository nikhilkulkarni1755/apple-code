# apple-code

A coding agent that runs entirely on your Mac, using Apple's on-device Foundation Model. It researches a goal in your project, plans small tasks, writes the code, and checks the work by running your tests itself. Every action is recorded and can be undone.

**Requirements:** a Mac with Apple silicon, macOS 26 or newer, Apple Intelligence turned on, and Apple's command line tools (`xcode-select --install`). Python 3.12+ or [uv](https://docs.astral.sh/uv/).

## Install

Pick one. All three install the same thing: the `apple-code` command, available from any folder.

```sh
# 1. curl
curl -fsSL https://raw.githubusercontent.com/nikhilkulkarni1755/apple-code/main/install.sh | sh

# 2. npm / npx
npx apple-code install        # or: npm install -g apple-code

# 3. zip: on the GitHub page use Code > Download ZIP (or the apple-code-<version>.zip from the releases page),
#    unzip it, then in Terminal, inside the unzipped folder:
sh install.sh                 # (the release zip also has Install.command: right-click > Open)
```

The installer puts a private Python environment in `~/.apple-code` and one launcher in `~/.local/bin`, and adds `~/.local/bin` to your `PATH` if needed (pass `--no-modify-path` to skip that). Check it with `apple-code doctor`.

## Use

```sh
cd ~/my-project              # use a folder that is in git; the agent runs commands there
apple-code solve "Create slug.py with slugify(text), and tests in test_slug.py"
```

It prints each task and each attempt as it goes. A task counts as done only when the harness's own pytest run passes with more passing tests than before, and the original tests still pass against the new code. A task that cannot be verified is rolled back.

```sh
cat MEMORY.md             # project state and the task plan
apple-code trail 1.1      # everything done under task 1.1
apple-code undo           # undo the last change (repeat to go back further); apple-code redo
apple-code --help         # all commands
```

Run `solve` again with a new goal to keep building the same project, or with the same goal to resume after an interruption.

## Update and uninstall

```sh
apple-code update
apple-code uninstall      # removes the program; your projects are left alone
```

## Things to know

- It is a small on-device model: give it small, concrete goals ("add X to file Y, with a test"). On tiny tasks it succeeds fully in roughly one run in three; failed attempts are rolled back, so a failure leaves your project as it was.
- It runs commands in your project folder without asking, behind a denylist that stops obvious accidents (it is not a sandbox). Commands see only an allowlisted environment, so tokens exported in your shell are not visible to them.
- Research sends search queries to DuckDuckGo.
- The agent's state lives in `.agent/` (ledger, git-ignore it) and `MEMORY.md` (commit it).

Design notes and measurements: [PLAN.md](PLAN.md).
