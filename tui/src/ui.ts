import {
  CombinedAutocompleteProvider, Container, CURSOR_MARKER, decodeKittyPrintable, Editor,
  getKeybindings, Markdown, Text, TuiMainScreen, truncateToWidth,
  type Component, type EditorTheme, type MarkdownTheme, type Terminal,
} from "@earendil-works/pi-tui";
import { highlight, supportsLanguage } from "cli-highlight";

type Message = { role: string; content?: unknown; tool_call_id?: string; tool_name?: string; is_error?: boolean };
type Tool = { id: string; name: string; arguments?: unknown; result?: string; error?: boolean };
type Entry = { kind: "user" | "assistant" | "notice" | "error"; text: string } | { kind: "tool"; tool: Tool };
type Event = { type: string; delta?: string; message?: Message; call?: Tool; result?: Message; reason?: string; error?: string };
type Packet = { type: string; event?: Event; messages?: Message[]; workspace?: string; session?: string; provider?: string; model?: string; text?: string; prompt?: string };

const commands = [
  ["help", "Show commands and shortcuts"], ["status", "Show run and queue status"],
  ["steer", "Queue steering input"], ["followup", "Queue a follow-up"],
  ["cancel", "Cancel the current run"], ["login", "Save a Groq API key"],
  ["auth-status", "Show credential source"], ["logout", "Remove saved credential"],
  ["theme", "Switch light and dark themes"], ["tools", "Expand or collapse tool results"],
  ["quit", "Exit"],
].map(([name, description]) => ({ name, description }));

const style = (code: number) => (s: string): string => `\x1b[${code}m${s}\x1b[0m`;
const bold = style(1), dim = style(2), italic = style(3), underline = style(4), strike = style(9);
const help = "Commands: /steer TEXT, /followup TEXT, /cancel, /status, /login groq, /auth-status groq, /logout groq, /theme, /tools, /quit\nEnter submits; Shift+Enter or Ctrl+J adds a line. Escape cancels a run; Ctrl+C cancels a run or exits.";

function themes(light: boolean): { markdown: MarkdownTheme; editor: EditorTheme } {
  const accent = style(light ? 34 : 36);
  const code = style(light ? 35 : 33);
  const select = { selectedPrefix: accent, selectedText: bold, description: dim, scrollInfo: dim, noMatch: dim };
  return {
    markdown: { heading: accent, link: accent, linkUrl: dim, code, codeBlock: code, codeBlockBorder: dim,
      quote: italic, quoteBorder: dim, hr: dim, listBullet: accent, bold, italic, strikethrough: strike, underline,
      highlightCode: (source, language) => {
        if (!language || !supportsLanguage(language)) return source.split("\n").map(code);
        try {
          return highlight(source, { language, ignoreIllegals: true, theme: {
            keyword: accent, string: style(32), number: code, comment: dim,
            title: style(light ? 35 : 33), built_in: accent,
          } }).split("\n");
        } catch { return source.split("\n").map(code); }
      } },
    editor: { borderColor: accent, selectList: select },
  };
}

/** The SDK Input renders its contents, so credentials use a separate component. */
export class SecretInput implements Component {
  focused = false;
  private value = "";
  private paste = false;
  onSubmit: (value: string | null) => void;
  prompt: string;

  constructor(prompt: string, onSubmit: (value: string | null) => void) {
    this.prompt = prompt;
    this.onSubmit = onSubmit;
  }
  invalidate(): void {}
  render(width: number): string[] { return [truncateToWidth(`${this.prompt}${this.focused ? CURSOR_MARKER : ""}`, width)]; }
  handleInput(data: string): void {
    if (data.includes("\x1b[200~")) { this.paste = true; data = data.replaceAll("\x1b[200~", ""); }
    if (this.paste) {
      const end = data.indexOf("\x1b[201~");
      const part = end < 0 ? data : data.slice(0, end);
      this.value += part;
      if (end >= 0) this.paste = false;
      return;
    }
    const keys = getKeybindings();
    if (keys.matches(data, "tui.select.cancel")) { this.value = ""; this.onSubmit(null); return; }
    if (keys.matches(data, "tui.input.submit")) { const value = this.value; this.value = ""; this.onSubmit(value); return; }
    if (keys.matches(data, "tui.editor.deleteCharBackward")) { this.value = this.value.slice(0, -1); return; }
    const printable = decodeKittyPrintable(data) ?? data;
    if (/^[\x21-\x7e]+$/.test(printable)) this.value += printable;
  }
}

export class ChatUI {
  readonly tui: TuiMainScreen;
  readonly transcript = new Container();
  readonly footer = new Text("", 1, 0);
  readonly editor: Editor;
  private entries: Entry[] = [];
  private rendered = new Map<Entry, { component: Text | Markdown; text: string }>();
  private streamed: { kind: "assistant"; text: string } | undefined;
  private tools = new Map<string, Tool>();
  private secret: SecretInput | undefined;
  private loginPending: Text | undefined;
  private light = false;
  private expanded = false;
  private running = false;
  private provider = "";
  private model = "";
  private workspace = "";
  private session = "";
  private send: (payload: object) => void;
  private quit: () => void;

  constructor(terminal: Terminal, send: (payload: object) => void, quit: () => void, cwd: string) {
    this.send = send;
    this.quit = quit;
    this.tui = new TuiMainScreen(terminal);
    this.editor = new Editor(this.tui, themes(false).editor, { paddingX: 1 });
    this.editor.setAutocompleteProvider(new CombinedAutocompleteProvider(commands, cwd));
    this.editor.onSubmit = (text) => this.submit(text);
    this.tui.addChild(this.transcript);
    this.tui.addChild(this.editor);
    this.tui.addChild(this.footer);
    this.tui.setFocus(this.editor);
    this.tui.addInputListener((data) => {
      if (this.secret) return undefined;
      const keys = getKeybindings();
      if (keys.matches(data, "tui.input.copy")) {
        if (this.running) this.send({ type: "submit", text: "/cancel" });
        else this.quit();
        return { consume: true };
      }
      if (keys.matches(data, "tui.select.cancel") && this.running && !this.editor.isShowingAutocomplete()) {
        this.send({ type: "submit", text: "/cancel" });
        return { consume: true };
      }
      return undefined;
    });
    this.refresh();
  }

  start(): void { this.tui.start(); }
  stop(): void { this.tui.stop(); }

  private submit(value: string): void {
    const text = value.trim();
    if (!text) return;
    this.editor.addToHistory(value);
    this.editor.setText("");
    if (text === "/quit") { this.quit(); return; }
    if (text === "/theme") { this.light = !this.light; this.rendered.clear(); this.editor.borderColor = themes(this.light).editor.borderColor; this.editor.invalidate(); this.entries.push({ kind: "notice", text: `Theme: ${this.light ? "light" : "dark"}` }); this.refresh(); return; }
    if (text === "/tools") { this.expanded = !this.expanded; this.refresh(); return; }
    if (text === "/login groq") {
      this.loginPending = new Text("Waiting for credential prompt...", 1, 0);
      this.replaceInput(this.loginPending);
    }
    this.send({ type: "submit", text: value });
  }

  private replaceInput(component: Component): void {
    this.tui.removeChild(this.editor);
    if (this.loginPending) this.tui.removeChild(this.loginPending);
    if (this.secret) this.tui.removeChild(this.secret);
    this.tui.removeChild(this.footer);
    this.tui.addChild(component);
    this.tui.addChild(this.footer);
    this.tui.setFocus("focused" in component ? component as Component & { focused: boolean } : null);
    this.tui.requestRender();
  }

  private showSecret(prompt: string): void {
    if (this.secret) return;
    const secret = new SecretInput(prompt, (value) => {
      this.send({ type: "secret", value });
      this.replaceInput(this.editor);
      this.secret = undefined;
      this.refresh();
    });
    this.replaceInput(secret);
    this.loginPending = undefined;
    this.secret = secret;
  }

  private addMessage(message: Message, history = false): void {
    if (message.role === "user" && typeof message.content === "string") {
      this.entries.push({ kind: "user", text: message.content });
    } else if (message.role === "assistant" && Array.isArray(message.content)) {
      if (history) this.tools.clear();
      const blocks = message.content as Array<{ type: string; text?: string; id?: string; name?: string; arguments?: unknown }>;
      const text = blocks.filter((b) => b.type === "text").map((b) => b.text ?? "").join("\n\n");
      if (this.streamed) { this.streamed.text = text; this.streamed = undefined; }
      else if (text) this.entries.push({ kind: "assistant", text });
      if (history) for (const block of blocks) {
        if (block.type === "tool_call" && block.id && block.name) this.addTool({ id: block.id, name: block.name, arguments: block.arguments });
      }
    } else if (message.role === "tool_result" && message.tool_call_id) {
      const tool = this.tools.get(message.tool_call_id);
      if (tool && typeof message.content === "string") { tool.result = message.content; tool.error = message.is_error; }
    }
  }

  private addTool(call: Tool): void {
    if (this.tools.has(call.id)) return;
    const tool = { ...call };
    this.tools.set(tool.id, tool);
    this.entries.push({ kind: "tool", tool });
  }

  receive(packet: Packet): void {
    if (packet.type === "ready") {
      this.workspace = packet.workspace ?? ""; this.session = packet.session ?? "";
      this.provider = packet.provider ?? ""; this.model = packet.model ?? "";
      for (const message of packet.messages ?? []) this.addMessage(message, true);
    } else if (packet.type === "event" && packet.event) {
      const event = packet.event;
      if (event.type === "run_start") this.running = true;
      else if (event.type === "turn_start") { this.streamed = undefined; this.tools.clear(); }
      else if (event.type === "text_delta") {
        if (!this.streamed) { this.streamed = { kind: "assistant", text: "" }; this.entries.push(this.streamed); }
        this.streamed.text += event.delta ?? "";
      } else if (event.type === "message_end" && event.message) this.addMessage(event.message);
      else if (event.type === "tool_start" && event.call) this.addTool(event.call);
      else if (event.type === "tool_end" && event.call && event.result) {
        const tool = this.tools.get(event.call.id);
        if (tool && typeof event.result.content === "string") { tool.result = event.result.content; tool.error = event.result.is_error; }
      } else if (event.type === "run_end") {
        this.running = false; this.streamed = undefined;
        if (event.reason !== "complete") {
          for (const tool of this.tools.values()) if (tool.result === undefined) {
            tool.result = `Not completed: ${event.reason ?? "error"}`;
            tool.error = true;
          }
          this.entries.push({ kind: "error", text: `${event.reason ?? "error"}: ${event.error ?? ""}` });
        }
      }
    } else if (packet.type === "secret_request") this.showSecret(packet.prompt ?? "API key: ");
    else if (packet.type === "help") this.entries.push({ kind: "notice", text: help });
    else if (packet.type === "notice" || packet.type === "error") {
      if (packet.type === "error" && this.loginPending) { this.replaceInput(this.editor); this.loginPending = undefined; }
      this.entries.push({ kind: packet.type, text: packet.text ?? "" });
    }
    this.refresh();
  }

  private refresh(): void {
    this.transcript.clear();
    const theme = themes(this.light).markdown;
    for (const entry of this.entries) {
      let text: string;
      if (entry.kind === "tool") {
        const tool = entry.tool;
        const state = tool.result === undefined ? "running" : tool.error ? "error" : "done";
        text = `[tool] ${tool.name} (${state})`;
        if (this.expanded) text += `\n${JSON.stringify(tool.arguments ?? {}, null, 2)}${tool.result === undefined ? "" : `\n${tool.result}`}`;
        text = dim(text);
      } else if (entry.kind === "assistant") text = entry.text;
      else if (entry.kind === "user") text = bold(`> ${entry.text}`);
      else text = entry.kind === "error" ? style(31)(entry.text) : dim(entry.text);
      let rendered = this.rendered.get(entry);
      if (!rendered) {
        const component = entry.kind === "assistant" ? new Markdown(text, 1, 1, theme) : new Text(text, 1, entry.kind === "user" ? 1 : 0);
        rendered = { component, text };
        this.rendered.set(entry, rendered);
      } else if (rendered.text !== text) {
        rendered.component.setText(text);
        rendered.text = text;
      }
      this.transcript.addChild(rendered.component);
    }
    this.footer.setText(dim(`${this.provider} ${this.model}  |  ${this.running ? "working" : "ready"}  |  ${this.workspace}\n${this.session}  |  /help`));
    this.tui.requestRender();
  }
}
