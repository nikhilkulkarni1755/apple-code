#!/bin/sh
# Build the Swift helper in place (for working on the code), and remove macOS Finder metadata (.DS_Store, ._* files).
set -e
cd "$(dirname "$0")"
swiftc -O agent/helper/main.swift -o agent/helper/fm-helper
find . \( -name .DS_Store -o -name '._*' \) -not -path './.venv/*' -delete
