import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { stripTerminalSequences, type Terminal } from "@earendil-works/pi-tui";
import { ChatUI, SecretInput } from "../src/ui.ts";

const previousTerm = process.env.TERM;
const previousNoColor = process.env.NO_COLOR;
beforeEach(() => {
  process.env.TERM = "xterm-256color";
  delete process.env.NO_COLOR;
});
afterEach(() => {
  if (previousTerm === undefined) delete process.env.TERM;
  else process.env.TERM = previousTerm;
  if (previousNoColor === undefined) delete process.env.NO_COLOR;
  else process.env.NO_COLOR = previousNoColor;
});

class FakeTerminal implements Terminal {
  columns = 80;
  rows = 24;
  kittyProtocolActive = false;
  output = "";
  title = "";
  onResize: (() => void) | undefined;
  start(_input: (data: string) => void, resize: () => void): void { this.onResize = resize; }
  stop(): void {}
  async drainInput(): Promise<void> {}
  write(data: string): void { this.output += data; }
  moveBy(_lines: number): void {}
  hideCursor(): void {}
  showCursor(): void {}
  clearLine(): void {}
  clearFromCursor(): void {}
  clearScreen(): void {}
  setTitle(title: string): void { this.title = title; }
  setProgress(_active: boolean): void {}
  setProgramStatus(): void {}
}

const lines = (ui: ChatUI, width = 80): string => ui.transcript.render(width).map(stripTerminalSequences).join("\n");

test("resumes a session, streams Markdown and expands tool results after resizing", () => {
  const terminal = new FakeTerminal();
  const sent: object[] = [];
  const ui = new ChatUI(terminal, (packet) => sent.push(packet), () => {}, ".");
  ui.start();
  ui.receive({ type: "ready", provider: "groq", model: "test", workspace: ".", session: "s", messages: [
    { role: "user", content: "old question" },
    { role: "assistant", content: [{ type: "text", text: "old answer" }] },
  ] });
  ui.receive({ type: "event", event: { type: "run_start" } });
  const oldMessage = ui.transcript.children[1];
  ui.receive({ type: "event", event: { type: "turn_start" } });
  ui.receive({ type: "event", event: { type: "text_delta", delta: "**Hel" } });
  assert.equal(ui.transcript.children[1], oldMessage);
  assert.match(lines(ui), /Hel/);
  ui.receive({ type: "event", event: { type: "text_delta", delta: "lo**" } });
  assert.match(lines(ui), /Hello/);
  ui.receive({ type: "event", event: { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "**Hello**" }] } } });
  assert.equal((lines(ui).match(/Hello/g) ?? []).length, 1);
  ui.receive({ type: "event", event: { type: "tool_start", call: { id: "1", name: "read_file", arguments: { path: "a" } } } });
  ui.receive({ type: "event", event: { type: "tool_end", call: { id: "1", name: "read_file" }, result: { role: "tool_result", content: "file contents" } } });
  assert.match(lines(ui), /read_file \(done\)/);
  assert.doesNotMatch(lines(ui), /file contents/);
  ui.editor.onSubmit?.("/tools");
  assert.match(lines(ui), /file contents/);
  terminal.columns = 32;
  terminal.onResize?.();
  ui.tui.renderNow();
  assert.match(lines(ui, 32), /old question/);
  assert.match(lines(ui, 32), /Hello/);
  ui.editor.onSubmit?.("new prompt");
  assert.deepEqual(sent.at(-1), { type: "submit", text: "new prompt" });
  ui.stop();
});

test("secret input never renders the key and sends it only on submit", () => {
  let submitted: string | null | undefined;
  const secret = new SecretInput("Key: ", (value) => { submitted = value; });
  secret.focused = true;
  secret.handleInput("fake-secret");
  assert.doesNotMatch(secret.render(80).join(""), /fake-secret/);
  secret.handleInput("\r");
  assert.equal(submitted, "fake-secret");
  assert.doesNotMatch(secret.render(80).join(""), /fake-secret/);
  secret.handleInput("\x1b[200~pasted-secret\x1b[201~");
  secret.handleInput("\x1b");
  assert.equal(submitted, null);
});

test("tool results from a resumed session remain expandable", () => {
  const ui = new ChatUI(new FakeTerminal(), () => {}, () => {}, ".");
  ui.receive({ type: "ready", messages: [
    { role: "assistant", content: [{ type: "tool_call", id: "1", name: "read_file", arguments: { path: "a" } }] },
    { role: "tool_result", tool_call_id: "1", content: "saved contents" },
  ] });
  ui.editor.onSubmit?.("/tools");
  assert.match(lines(ui), /saved contents/);
});

test("code blocks use syntax highlighting when the language is known", () => {
  const ui = new ChatUI(new FakeTerminal(), () => {}, () => {}, ".");
  ui.receive({ type: "event", event: { type: "text_delta", delta: "```js\nconst x = 1;\n```" } });
  const rendered = ui.transcript.render(80).join("\n");
  assert.match(rendered, /\x1b\[[0-9;]+mconst\x1b\[0m/);
});

test("Doom's logo is present before ready, survives theme changes, and has a terminal title", () => {
  const terminal = new FakeTerminal();
  const ui = new ChatUI(terminal, () => {}, () => {}, ".");
  const header = ui.tui.children[0];
  const original = header.render(80);
  assert.match(original.map(stripTerminalSequences).join("\n"), /doom/);
  assert.match(original.map(stripTerminalSequences).join("\n"), /Workspace assistant/);
  assert.match(original.join(""), /[▀▄█]/);
  ui.start();
  assert.equal(terminal.title, "doom");
  ui.receive({ type: "ready", provider: "groq", model: "test", workspace: ".", session: "s" });
  ui.editor.onSubmit?.("/theme");
  assert.equal(ui.tui.children[0], header);
  assert.deepEqual(header.render(80).map(stripTerminalSequences), original.map(stripTerminalSequences));
  assert.match(lines(ui), /Theme: light/);
  assert.match(ui.footer.render(80).map(stripTerminalSequences).join("\n"), /doom.*groq test/);
  ui.stop();
});

test("login disables ordinary input until the masked prompt is ready", () => {
  const packets: object[] = [];
  const ui = new ChatUI(new FakeTerminal(), (packet) => packets.push(packet), () => {}, ".");
  ui.editor.onSubmit?.("/login groq");
  assert.equal(ui.tui.children.includes(ui.editor), false);
  ui.receive({ type: "secret_request", prompt: "Key: " });
  const secret = ui.tui.children.find((child) => child instanceof SecretInput);
  assert.ok(secret instanceof SecretInput);
  secret.handleInput("test-key");
  assert.doesNotMatch(ui.tui.render(80).join("\n"), /test-key/);
  secret.handleInput("\r");
  assert.deepEqual(packets.at(-1), { type: "secret", value: "test-key" });
  assert.ok(ui.tui.children.includes(ui.editor));
});

test("cancelled tools do not remain marked as running", () => {
  const ui = new ChatUI(new FakeTerminal(), () => {}, () => {}, ".");
  ui.receive({ type: "event", event: { type: "tool_start", call: { id: "1", name: "read_file" } } });
  ui.receive({ type: "event", event: { type: "run_end", reason: "cancelled" } });
  assert.match(lines(ui), /read_file \(error\)/);
  ui.editor.onSubmit?.("/tools");
  assert.match(lines(ui), /Not completed: cancelled/);
});

test("tool call identifiers can be reused in a later turn", () => {
  const ui = new ChatUI(new FakeTerminal(), () => {}, () => {}, ".");
  for (const name of ["first", "second"]) {
    ui.receive({ type: "event", event: { type: "turn_start" } });
    ui.receive({ type: "event", event: { type: "tool_start", call: { id: "same", name } } });
    ui.receive({ type: "event", event: { type: "tool_end", call: { id: "same", name }, result: { role: "tool_result", content: `${name} result` } } });
  }
  ui.editor.onSubmit?.("/tools");
  assert.match(lines(ui), /first result/);
  assert.match(lines(ui), /second result/);
});

test("context footer follows backend estimates, warnings, and compaction progress", () => {
  const ui = new ChatUI(new FakeTerminal(), () => {}, () => {}, ".");
  const usage = { tokens: 82000, estimated_tokens: 82000, context_window: 200000, percent: 41, estimated: true, auto_compact: true };
  ui.receive({ type: "ready", context_usage: usage });
  const footer = (): string => ui.footer.render(160).map(stripTerminalSequences).join("\n");
  assert.match(footer(), /context ~82,000\/200,000 \(41.0%\) \[auto\]/);
  for (const [percent, code] of [[75, 33], [95, 31]]) {
    ui.receive({ type: "event", event: { type: "context_update", context_usage: { ...usage, percent } } });
    assert.ok(ui.footer.render(160).join("\n").includes(`\x1b[${code}mcontext`));
  }
  ui.receive({ type: "event", event: { type: "compaction_start" } });
  assert.match(footer(), /compacting/);
  ui.receive({ type: "event", event: { type: "compaction_end" } });
  ui.receive({ type: "event", event: { type: "context_update", context_usage: { ...usage, tokens: null, percent: null } } });
  assert.match(footer(), /context \?\/200,000/);
  assert.match(lines(ui), /Conversation compacted/);
  ui.receive({ type: "event", event: { type: "context_update", context_usage: { ...usage, tokens: 100, estimated: false, percent: 0.05 } } });
  assert.match(footer(), /context 100\/200,000/);
  ui.editor.onSubmit?.("/theme");
  assert.match(footer(), /context 100\/200,000/);
});

test("unknown capacity and failed compaction remain visible without summary answers", () => {
  const ui = new ChatUI(new FakeTerminal(), () => {}, () => {}, ".");
  ui.receive({ type: "ready", context_usage: { tokens: 123, estimated_tokens: 123, context_window: null, percent: null, estimated: true, auto_compact: false } });
  assert.match(ui.footer.render(160).map(stripTerminalSequences).join("\n"), /context ~123\/\? \[auto off\]/);
  ui.receive({ type: "event", event: { type: "compaction_start" } });
  ui.receive({ type: "event", event: { type: "compaction_end", error: "summary request failed" } });
  assert.match(lines(ui), /Compaction failed: summary request failed/);
  assert.doesNotMatch(ui.footer.render(160).map(stripTerminalSequences).join("\n"), /compacting/);
});
