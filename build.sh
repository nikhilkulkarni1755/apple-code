#!/bin/sh
# Build the Swift helper, and remove macOS Finder metadata (.DS_Store, ._* files) so none ends up in a build.
set -e
cd "$(dirname "$0")"
swiftc -O helper/main.swift -o helper/fm-helper
find . \( -name .DS_Store -o -name '._*' \) -not -path './.venv/*' -delete
