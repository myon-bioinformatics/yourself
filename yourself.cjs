"use strict";
/** Thin Node entry (Node 24+): runs yourself.py through the shared vendor/py_launch.cjs. */
const path = require("node:path");
const { main } = require("./vendor/py_launch.cjs");

const PY_SCRIPT = path.join(__dirname, "yourself.py");
const PY_LAUNCH = { envVar: "YOURSELF_PYTHON", noSite: true };
const NODE_MIN_MAJOR = 24;

/** Reject Node hosts older than NODE_MIN_MAJOR. Pass a version string in tests. */
function assertNodeFloor(version = process.versions.node) {
  const match = /(?:^|\s)v?(\d+)/i.exec(String(version).trim());
  const major = match ? Number.parseInt(match[1], 10) : NaN;
  if (!Number.isInteger(major) || major < NODE_MIN_MAJOR) {
    throw new Error(`yourself.cjs requires Node ${NODE_MIN_MAJOR}+ (found ${version})`);
  }
}

if (require.main === module) {
  try {
    assertNodeFloor();
  } catch (err) {
    process.stderr.write(`${err && err.message ? err.message : err}\n`);
    process.exit(1);
  }
  main(PY_SCRIPT, PY_LAUNCH);
}

module.exports = { PY_SCRIPT, PY_LAUNCH, NODE_MIN_MAJOR, assertNodeFloor };
