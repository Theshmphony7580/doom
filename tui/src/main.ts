import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ProcessTerminal } from "@earendil-works/pi-tui";
import { ChatUI } from "./ui.ts";

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  process.stderr.write("The Doom TUI requires an interactive terminal. Use uv run doom for noninteractive use.\n");
  process.exit(1);
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const cwd = process.env.INIT_CWD ?? process.cwd();
const args = process.argv.slice(2);
for (let index = 0; index < args.length; index++) {
  for (const option of ["--workspace", "--session"]) {
    if (args[index] === option && args[index + 1]) args[index + 1] = resolve(cwd, args[index + 1]);
    else if (args[index].startsWith(`${option}=`)) args[index] = `${option}=${resolve(cwd, args[index].slice(option.length + 1))}`;
  }
}
if (!args.some((arg) => arg === "--workspace" || arg.startsWith("--workspace="))) args.push("--workspace", cwd);
const child = spawn("uv", ["run", "--locked", "python", "-m", "pi_in_py", "--ui-protocol", ...args], {
  cwd: root, env: { ...process.env, PYTHONUTF8: "1" }, stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
});
let closing = false;
let stopped = false;
let failed = false;
let diagnostics = "";
let timer: NodeJS.Timeout | undefined;
const send = (message: object): void => {
  if (!child.stdin.destroyed && child.stdin.writable) child.stdin.write(`${JSON.stringify(message)}\n`);
};
const quit = (): void => {
  if (closing) return;
  closing = true;
  send({ type: "quit" });
  child.stdin.end();
  timer = setTimeout(() => child.kill(), 5000);
};
const ui = new ChatUI(new ProcessTerminal(), send, quit, cwd);
const stop = (): void => {
  if (stopped) return;
  stopped = true;
  if (timer) clearTimeout(timer);
  ui.stop();
};

child.stderr.setEncoding("utf8");
child.stderr.on("data", (chunk: string) => { diagnostics += chunk; });
child.stdin.on("error", () => {});
createInterface({ input: child.stdout }).on("line", (line) => {
  try { ui.receive(JSON.parse(line)); }
  catch { failed = true; diagnostics += "Invalid response from the Python agent.\n"; quit(); }
});
child.on("error", (error) => {
  stop();
  process.stderr.write(`Could not start the Python agent: ${error.message}. Install uv and run uv sync --locked.\n`);
  process.exitCode = 1;
});
child.on("close", (code, signal) => {
  stop();
  if (diagnostics) process.stderr.write(diagnostics);
  if (failed || !closing || code !== 0) {
    process.stderr.write(`Python agent exited ${signal ? `on ${signal}` : `with code ${code}`}.\n`);
    process.exitCode = 1;
  }
});
process.on("SIGINT", quit);
process.on("SIGTERM", quit);
process.on("uncaughtException", (error) => {
  failed = true;
  stop();
  process.stderr.write(`TUI failed: ${error.message}\n`);
  quit();
});
ui.start();
