import assert from "node:assert/strict";
import { test } from "node:test";
import { stripTerminalSequences } from "@earendil-works/pi-tui";
import { doomHeader } from "../src/branding.ts";

test("the Doom header fits narrow and wide terminals without losing its name", () => {
  for (const width of [12, 20, 43, 44, 80, 120]) {
    const rows = doomHeader(width).map(stripTerminalSequences);
    assert.match(rows.join("\n"), /doom/);
    assert.ok(rows.every((row) => row.length <= width));
  }
});

test("the imp keeps its geometry across themes and changes colors", () => {
  const previousNoColor = process.env.NO_COLOR;
  const previousTerm = process.env.TERM;
  delete process.env.NO_COLOR;
  process.env.TERM = "xterm-256color";
  try {
    const dark = doomHeader(80);
    const light = doomHeader(80, true);
    assert.equal(dark.length, 7);
    assert.deepEqual(dark.map(stripTerminalSequences), light.map(stripTerminalSequences));
    assert.notDeepEqual(dark, light);
    assert.doesNotMatch(dark.join(""), /\*/);
  } finally {
    if (previousNoColor === undefined) delete process.env.NO_COLOR;
    else process.env.NO_COLOR = previousNoColor;
    if (previousTerm === undefined) delete process.env.TERM;
    else process.env.TERM = previousTerm;
  }
});

test("NO_COLOR keeps the imp monochrome and dumb terminals get a text identity", () => {
  const previousNoColor = process.env.NO_COLOR;
  const previousTerm = process.env.TERM;
  process.env.NO_COLOR = "1";
  process.env.TERM = "xterm";
  try {
    assert.doesNotMatch(doomHeader(80).join(""), /\x1b/);
    assert.match(doomHeader(80).join(""), /[▀▄█]/);
    process.env.TERM = "dumb";
    assert.deepEqual(doomHeader(80), ["doom - Workspace assistant"]);
  } finally {
    if (previousNoColor === undefined) delete process.env.NO_COLOR;
    else process.env.NO_COLOR = previousNoColor;
    if (previousTerm === undefined) delete process.env.TERM;
    else process.env.TERM = previousTerm;
  }
});
