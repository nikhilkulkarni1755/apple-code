#!/bin/sh
# Double-click to install apple-code (the same as running ./install.sh in Terminal).
cd "$(dirname "$0")" || exit 1
xattr -dr com.apple.quarantine . 2>/dev/null
sh ./install.sh
printf '\nPress return to close this window. '
read _
