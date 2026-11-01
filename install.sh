#!/bin/sh
# apple-code installer. Installs the `apple-code` command globally, so it works from any folder.
#
#   curl -fsSL https://raw.githubusercontent.com/nikhilkulkarni1755/apple-code/main/install.sh | sh
#
# Options (when piping from curl, put them after `sh -s --`):
#   --from SRC        install from a folder, a .tar.gz / .zip file, or a URL (default: the folder this script is in, else GitHub)
#   --home DIR        where the program lives             (default: ~/.apple-code)
#   --prefix DIR      where the apple-code command goes   (default: ~/.local/bin)
#   --no-modify-path  do not edit shell startup files to put the command on your PATH
#   --uninstall       remove apple-code (your projects, and their MEMORY.md files, are left alone)
#   -h, --help        show this help
#
# What it does, and nothing else: creates a private Python environment in the home folder, installs apple-code into it,
# builds the small Swift helper that talks to Apple's on-device model, and writes one launcher script into the prefix folder.
set -eu

REPO="nikhilkulkarni1755/apple-code"
LAUNCHER_MARK="# apple-code launcher"
PATH_MARK="# added by the apple-code installer"
HOME_DIR="$HOME/.apple-code"
PREFIX="$HOME/.local/bin"
SRC=""
MODIFY_PATH=1
UNINSTALL=0

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  if [ -f "$0" ]; then sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  else say "apple-code installer. Options: --from SRC, --home DIR, --prefix DIR, --no-modify-path, --uninstall, --help (see install.sh)"; fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --from) [ $# -ge 2 ] || die "--from needs a value"; SRC="$2"; shift 2 ;;
    --home) [ $# -ge 2 ] || die "--home needs a value"; HOME_DIR="$2"; shift 2 ;;
    --prefix) [ $# -ge 2 ] || die "--prefix needs a value"; PREFIX="$2"; shift 2 ;;
    --no-modify-path) MODIFY_PATH=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

VENV="$HOME_DIR/venv"
MARKER="$HOME_DIR/.apple-code-home"   # proves a folder is ours, so --uninstall can never delete anything else

# ---------------------------------------------------------------------------------------------------------------- uninstall
rc_files() {
  for rc in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc"; do [ -f "$rc" ] && printf '%s\n' "$rc"; done
  return 0
}

if [ "$UNINSTALL" = 1 ]; then
  if [ -f "$PREFIX/apple-code" ] && grep -qF "$LAUNCHER_MARK" "$PREFIX/apple-code"; then
    rm -f "$PREFIX/apple-code"; say "removed $PREFIX/apple-code"
  fi
  if [ -f "$MARKER" ]; then
    rm -rf "$HOME_DIR"; say "removed $HOME_DIR"
  elif [ -e "$HOME_DIR" ]; then
    say "left $HOME_DIR alone: it was not created by this installer"
  fi
  rc_files | while IFS= read -r rc; do
    if grep -qF "$PATH_MARK" "$rc"; then
      tmp=$(mktemp); grep -vF "$PATH_MARK" "$rc" > "$tmp" || true; cat "$tmp" > "$rc"; rm -f "$tmp"
      say "removed the PATH line from $rc"
    fi
  done
  say "apple-code is uninstalled."
  exit 0
fi

# ---------------------------------------------------------------------------------------------------------------- checks
[ "$(uname -s)" = "Darwin" ] || die "apple-code needs macOS: it uses Apple's on-device model."
[ "$(uname -m)" = "arm64" ] || die "apple-code needs a Mac with Apple silicon."
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 26 ] 2>/dev/null || die "apple-code needs macOS 26 or newer (this is $(sw_vers -productVersion))."
have swiftc || die "the Swift compiler is missing. Install Apple's command line tools:  xcode-select --install"

PYBIN=""
if ! have uv; then
  for candidate in python3.13 python3.12 python3; do
    if have "$candidate" && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then PYBIN="$candidate"; break; fi
  done
  [ -n "$PYBIN" ] || die "Python 3.12 or newer is needed. Install uv (https://docs.astral.sh/uv/) or run:  brew install python"
fi

if [ -e "$HOME_DIR" ] && [ ! -f "$MARKER" ] && [ -n "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
  die "$HOME_DIR exists and was not created by this installer; choose another folder with --home"
fi
if [ -e "$PREFIX/apple-code" ] && ! grep -qF "$LAUNCHER_MARK" "$PREFIX/apple-code" 2>/dev/null; then
  die "$PREFIX/apple-code already exists and is not an apple-code launcher; remove it or choose --prefix"
fi

# ---------------------------------------------------------------------------------------------------------------- get the source
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

unpack() { # archive destination
  mkdir -p "$2"
  case "$1" in
    *.zip) have unzip || die "unzip is needed to open $1"; unzip -q "$1" -d "$2" ;;
    *) tar -xzf "$1" -C "$2" ;;
  esac
}

HERE=""
if [ -f "$0" ]; then HERE=$(cd "$(dirname "$0")" && pwd); fi
if [ -z "$SRC" ] && [ -n "$HERE" ] && [ -f "$HERE/pyproject.toml" ] && [ -f "$HERE/agent/helper/main.swift" ]; then
  SRC="$HERE"   # run from an unpacked release (zip) or a checkout: install that, do not download something else
fi

case "$SRC" in
  "")  url="https://github.com/$REPO/archive/refs/heads/main.tar.gz" ;;
  http://*|https://*) url="$SRC" ;;
  *) url="" ;;
esac
if [ -n "$url" ]; then
  have curl || die "curl is needed to download apple-code"
  say "Downloading apple-code..."
  curl -fsSL "$url" -o "$TMP/source.archive" || die "could not download $url"
  unpack "$TMP/source.archive" "$TMP/extracted"
  root=$(dirname "$(find "$TMP/extracted" -maxdepth 3 -name pyproject.toml | head -n 1)")
elif [ -d "$SRC" ]; then
  root="$SRC"
elif [ -f "$SRC" ]; then
  unpack "$SRC" "$TMP/extracted"
  root=$(dirname "$(find "$TMP/extracted" -maxdepth 3 -name pyproject.toml | head -n 1)")
else
  die "cannot find $SRC"
fi
[ -f "$root/pyproject.toml" ] && [ -f "$root/agent/helper/main.swift" ] || die "$root does not look like apple-code source"

# build from a clean copy so nothing is written into the folder the install came from
STAGE="$TMP/stage"
mkdir -p "$STAGE"
cp -R "$root/agent" "$root/pyproject.toml" "$STAGE/"
[ -f "$root/README.md" ] && cp "$root/README.md" "$STAGE/"
find "$STAGE" \( -name __pycache__ -o -name fm-helper -o -name .DS_Store -o -name '._*' \) -prune -exec rm -rf {} + 2>/dev/null || true
xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null || true   # files from a browser download carry a quarantine flag

# ---------------------------------------------------------------------------------------------------------------- install
say "Installing into $HOME_DIR ..."
mkdir -p "$HOME_DIR"
rm -rf "$VENV"
if [ -z "$PYBIN" ]; then
  uv venv --quiet --python ">=3.12" "$VENV" || die "could not create the Python environment"
  uv pip install --quiet --python "$VENV/bin/python" "$STAGE" || die "could not install apple-code (is there an internet connection?)"
else
  "$PYBIN" -m venv "$VENV" || die "could not create the Python environment"
  "$VENV/bin/python" -m pip install --quiet "$STAGE" || die "could not install apple-code (is there an internet connection?)"
fi

PKG=$("$VENV/bin/python" -I -c 'import os, agent; print(os.path.dirname(agent.__file__))')   # -I: ignore the current folder, which may hold its own "agent"
say "Building the model helper..."
swiftc -O "$PKG/helper/main.swift" -o "$PKG/helper/fm-helper" 2> "$TMP/swiftc.log" || { cat "$TMP/swiftc.log" >&2; die "could not build the Swift helper"; }

VERSION=$("$VENV/bin/python" -I -c 'import agent; print(agent.__version__)')
printf '%s\n' "$VERSION" > "$HOME_DIR/VERSION"
printf 'apple-code home. --uninstall only deletes a folder that contains this file.\n' > "$MARKER"
if [ -f "$root/install.sh" ]; then cp "$root/install.sh" "$HOME_DIR/install.sh" && chmod +x "$HOME_DIR/install.sh"; fi   # for `apple-code update` and `uninstall`

mkdir -p "$PREFIX"
cat > "$PREFIX/apple-code" <<EOF
#!/bin/sh
$LAUNCHER_MARK
exec "$VENV/bin/python" -I -m agent "\$@"   # -I: a project folder with its own "agent" package must not shadow apple-code
EOF
chmod +x "$PREFIX/apple-code"

# ---------------------------------------------------------------------------------------------------------------- PATH
on_path=0
case ":$PATH:" in *":$PREFIX:"*) on_path=1 ;; esac
note=""
if [ "$on_path" = 0 ]; then
  if [ "$MODIFY_PATH" = 1 ]; then
    case "$(basename "${SHELL:-sh}")" in
      zsh) rc="$HOME/.zshrc" ;;
      bash) if [ -f "$HOME/.bash_profile" ]; then rc="$HOME/.bash_profile"; else rc="$HOME/.bashrc"; fi ;;
      *) rc="" ;;
    esac
    if [ -n "$rc" ]; then
      if [ -f "$rc" ] && grep -qF "$PATH_MARK" "$rc"; then :; else
        # one line, so that --uninstall can take it out again and leave the file exactly as it was
        if [ -s "$rc" ] && [ "$(tail -c 1 "$rc" | wc -l)" -eq 0 ]; then printf '\n' >> "$rc"; fi
        printf 'export PATH="%s:$PATH" %s\n' "$PREFIX" "$PATH_MARK" >> "$rc"
      fi
      note="Added $PREFIX to your PATH in $rc. Open a new terminal, or run:  export PATH=\"$PREFIX:\$PATH\""
    else
      note="Add $PREFIX to your PATH to use the command from anywhere."
    fi
  else
    note="$PREFIX is not on your PATH. Add it to use the command from anywhere."
  fi
fi

say ""
say "apple-code $VERSION is installed."
[ -n "$note" ] && say "$note"
say ""
"$PREFIX/apple-code" doctor || true
say ""
say "Next: cd into a project folder and run:  apple-code solve \"what you want built\""
