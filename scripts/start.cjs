const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const CONFIG_FILE = path.join(ROOT, "repository.json");
const DEFAULT_SUCCESS_EXIT_CODE = 0;
const FAILURE_EXIT_CODE = 1;
const configuration = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
const child = spawn(process.execPath, [path.join(ROOT, configuration.primary, "server.js")], {
  cwd: path.join(ROOT, configuration.primary),
  env: process.env,
  stdio: "inherit",
  windowsHide: true,
});
child.on("error", (error) => {
  console.error(error.message);
  process.exitCode = FAILURE_EXIT_CODE;
});
child.on("exit", (code) => { process.exitCode = code ?? DEFAULT_SUCCESS_EXIT_CODE; });
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
