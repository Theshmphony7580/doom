import asyncio
from pathlib import Path

import pytest
from pydantic import JsonValue

from pi_in_py.agent.types import Tool
from pi_in_py.app.tools import MAX_ENTRIES, MAX_FILE_BYTES, filesystem_tools


async def execute(tool: Tool, arguments: dict[str, JsonValue]) -> str:
    return await tool.execute(arguments)


def test_read_and_list_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello\nworld", encoding="utf-8", newline="\n")
    (tmp_path / "folder").mkdir()
    read, listing = filesystem_tools(tmp_path)
    assert asyncio.run(execute(read, {"path": "notes.txt"})) == "hello\nworld"
    assert asyncio.run(execute(listing, {})) == "folder/\nnotes.txt"


@pytest.mark.parametrize("absolute", [False, True])
def test_tools_reject_paths_outside_workspace(tmp_path: Path, absolute: bool) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    read, _ = filesystem_tools(root)
    path = str(outside) if absolute else "../outside.txt"
    with pytest.raises(PermissionError, match="outside"):
        asyncio.run(execute(read, {"path": path}))


def test_read_rejects_large_binary_and_missing_files(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
    (tmp_path / "binary").write_bytes(b"\xff")
    read, _ = filesystem_tools(tmp_path)
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(execute(read, {"path": "large.txt"}))
    with pytest.raises(UnicodeDecodeError):
        asyncio.run(execute(read, {"path": "binary"}))
    with pytest.raises(FileNotFoundError):
        asyncio.run(execute(read, {"path": "missing"}))


def test_listing_is_bounded(tmp_path: Path) -> None:
    for number in range(MAX_ENTRIES + 1):
        (tmp_path / str(number)).touch()
    _, listing = filesystem_tools(tmp_path)
    output = asyncio.run(execute(listing, {}))
    assert len(output.splitlines()) == MAX_ENTRIES + 1
    assert "truncated" in output
