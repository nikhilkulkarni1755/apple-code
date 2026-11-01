#!/usr/bin/env node
// The npm entry point. The first run installs apple-code globally (into ~/.apple-code, with a launcher in ~/.local/bin) by
// running the bundled install.sh, then every run just hands over to the installed program. `apple-code install` reinstalls.
"use strict";
const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const payload = path.join(__dirname, "..", "payload");
const version = require("../package.json").version;
const home = path.join(os.homedir(), ".apple-code");
const python = path.join(home, "venv", "bin", "python");
const args = process.argv.slice(2);

function installedVersion() {
  try { return fs.readFileSync(path.join(home, "VERSION"), "utf8").trim(); } catch { return null; }
}

function olderThan(a, b) { // is version a older than version b?
  const x = a.split(".").map(Number), y = b.split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0);
  }
  return false;
}

function install() {
  if (process.platform !== "darwin") { console.error("apple-code needs macOS: it uses Apple's on-device model."); process.exit(1); }
  const r = spawnSync("sh", [path.join(payload, "install.sh"), "--from", payload], { stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status || 1);
}

if (args[0] === "install") { install(); process.exit(0); }
const have = installedVersion();
if (have === null || !fs.existsSync(python) || olderThan(have, version)) {   // never downgrade an install that is newer than this package
  console.error(have === null ? `Setting up apple-code ${version} (first run)...` : `Updating apple-code ${have} -> ${version}...`);
  install();
}
const r = spawnSync(python, ["-I", "-m", "agent", ...args], { stdio: "inherit" });
process.exit(r.status === null ? 1 : r.status);
