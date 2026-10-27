import { foregroundAnsi, getTerminalColorMode, parseColor, truncateToWidth } from "@earendil-works/pi-tui";
import brand from "../../src/pi_in_py/app/brand.json" with { type: "json" };

export { brand };

export function brandAccent(text: string, light = false): string {
  if (process.env.NO_COLOR !== undefined || process.env.TERM === "dumb") return text;
  const palette = light ? brand.light : brand.dark;
  return `${foregroundAnsi(parseColor(palette.accent), getTerminalColorMode())}${text}\x1b[0m`;
}

export function doomHeader(width: number, light = false): string[] {
  if (process.env.TERM === "dumb" || width < 44) {
    return [truncateToWidth(`${brand.name} - ${brand.tagline}`, width, "")];
  }
  const colored = process.env.NO_COLOR === undefined;
  const palette = light ? brand.light : brand.dark;
  const body = colored ? foregroundAnsi(parseColor(palette.body), getTerminalColorMode()) : "";
  const reset = colored ? "\x1b[0m" : "";
  const details = [brandAccent(brand.name, light), brand.tagline, "", "/help commands", "/theme light / dark", "/quit exit"];
  return brand.logo.map((row, index) => {
    const sprite = row.replace("*", `${brandAccent("▄", light)}${body}`);
    return truncateToWidth(` ${body}${sprite}${reset}   ${details[index] ?? ""}`, width, "");
  });
}
