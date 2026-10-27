"""Doom's shared terminal identity, including the Twin Eye Imp sprite."""

import json
from importlib.resources import files
from typing import TypedDict, cast


class Brand(TypedDict):
    name: str
    tagline: str
    dark: dict[str, str]
    light: dict[str, str]
    logo: list[str]


BRAND = cast(
    Brand, json.loads(files(__package__).joinpath("brand.json").read_text(encoding="utf-8"))
)


def startup_banner(width: int = 80, *, color: bool = False, unicode: bool = True) -> str:
    if not unicode or width < 44:
        return f"{BRAND['name']} - {BRAND['tagline']}"[:width]
    palette = BRAND["dark"]
    styles = {
        key: f"\x1b[38;2;{';'.join(str(value) for value in bytes.fromhex(hex_color[1:]))}m"
        if color
        else ""
        for key, hex_color in palette.items()
    }
    reset = "\x1b[0m" if color else ""
    details = [BRAND["name"], BRAND["tagline"], "", "/help commands", "/quit exit"]
    lines = []
    for index, row in enumerate(BRAND["logo"]):
        sprite = row.replace("*", f"{styles['accent']}▄{styles['body']}")
        detail = details[index][: width - 24] if index < len(details) else ""
        if index == 0:
            detail = f"{styles['accent']}{detail}{reset}"
        lines.append(f" {styles['body']}{sprite}{reset}   {detail}".rstrip())
    return "\n".join(lines)
