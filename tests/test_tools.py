import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import JsonValue, ValidationError

from pi_in_py.agent.agent import Agent
from pi_in_py.agent.types import Tool
from pi_in_py.ai.fake import ScriptedProvider
from pi_in_py.ai.types import AssistantMessage, Text, ToolCall, ToolResult
from pi_in_py.app.session import SessionStore
from pi_in_py.app.tools import MAX_ENTRIES, MAX_READ_BYTES, MAX_READ_LINES, filesystem_tools


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


def test_read_rejects_binary_missing_and_non_files(tmp_path: Path) -> None:
    (tmp_path / "binary").write_bytes(b"\xff")
    read, _ = filesystem_tools(tmp_path)
    with pytest.raises(UnicodeDecodeError):
        asyncio.run(execute(read, {"path": "binary"}))
    with pytest.raises(FileNotFoundError):
        asyncio.run(execute(read, {"path": "missing"}))
    with pytest.raises(ValueError, match="regular file"):
        asyncio.run(execute(read, {"path": "."}))


@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_read_ranges_preserve_line_endings(tmp_path: Path, absolute: bool, ending: str) -> None:
    path = tmp_path / "notes.txt"
    content = ending.join(["one", "two", "three", "four", ""])
    path.write_bytes(content.encode("utf-8"))
    read, _ = filesystem_tools(tmp_path)
    name = str(path) if absolute else path.name
    assert asyncio.run(execute(read, {"path": name})) == content
    assert asyncio.run(execute(read, {"path": name, "offset": 2, "limit": 2})) == (
        f"two{ending}three{ending}\n\n[Showing lines 2–3. Use offset=4 to continue.]"
    )
    assert asyncio.run(execute(read, {"path": name, "offset": 4, "limit": 2})) == f"four{ending}"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": ""},
        {"path": 1},
        {"path": "notes.txt", "extra": 1},
        *(
            {"path": "notes.txt", key: value}
            for key in ("offset", "limit")
            for value in (0, -1, True, 1.5, "1", None)
        ),
    ],
)
def test_read_validates_arguments(tmp_path: Path, arguments: dict[str, JsonValue]) -> None:
    read, _ = filesystem_tools(tmp_path)
    with pytest.raises(ValidationError):
        asyncio.run(execute(read, arguments))


@pytest.mark.parametrize("content", [b"", b"one", b"one\n", b"one\r\n"])
def test_read_empty_files_and_eof(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "notes.txt").write_bytes(content)
    read, _ = filesystem_tools(tmp_path)
    assert asyncio.run(execute(read, {"path": "notes.txt", "limit": 1})) == content.decode()
    for offset in (2, 10):
        with pytest.raises(ValueError, match=f"Offset {offset} is beyond end of file"):
            asyncio.run(execute(read, {"path": "notes.txt", "offset": offset}))


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize("limit", [MAX_READ_LINES, MAX_READ_LINES + 100])
def test_read_line_cap_and_exact_eof(tmp_path: Path, extra: bool, limit: int) -> None:
    first_page = "x\n" * MAX_READ_LINES
    (tmp_path / "notes.txt").write_bytes((first_page + ("last" if extra else "")).encode())
    read, _ = filesystem_tools(tmp_path)
    expected = first_page
    if extra:
        expected += (
            f"\n\n[Showing lines 1–{MAX_READ_LINES}. Use offset={MAX_READ_LINES + 1} to continue.]"
        )
        assert (
            asyncio.run(execute(read, {"path": "notes.txt", "offset": MAX_READ_LINES + 1}))
            == "last"
        )
    assert asyncio.run(execute(read, {"path": "notes.txt", "limit": limit})) == expected


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
@pytest.mark.parametrize("character", ["a", "é", "🙂"])
def test_read_exact_byte_cap(tmp_path: Path, ending: bytes, character: str) -> None:
    encoded = character.encode("utf-8")
    length = MAX_READ_BYTES - len(ending)
    content = encoded * (length // len(encoded)) + b"a" * (length % len(encoded)) + ending
    (tmp_path / "notes.txt").write_bytes(content)
    read, _ = filesystem_tools(tmp_path)
    assert asyncio.run(execute(read, {"path": "notes.txt"})) == content.decode()
    if ending:
        (tmp_path / "notes.txt").write_bytes(content + b"last")
        assert asyncio.run(execute(read, {"path": "notes.txt"})) == (
            content.decode() + "\n\n[Showing lines 1–1. Use offset=2 to continue.]"
        )


def test_read_byte_cap_counts_utf8_without_splitting_lines(tmp_path: Path) -> None:
    first = "é" * (MAX_READ_BYTES // 2 - 1) + "\n"
    last = "🙂\n"
    (tmp_path / "notes.txt").write_bytes((first + last).encode("utf-8"))
    read, _ = filesystem_tools(tmp_path)
    assert asyncio.run(execute(read, {"path": "notes.txt"})) == (
        first + "\n\n[Showing lines 1–1. Use offset=2 to continue.]"
    )
    assert asyncio.run(execute(read, {"path": "notes.txt", "offset": 2})) == last


@pytest.mark.parametrize("character", ["a", "é"])
def test_read_reports_oversized_lines_and_can_skip_them(tmp_path: Path, character: str) -> None:
    content = "first\n" + character * (MAX_READ_BYTES + 1) + "\nlast\n"
    (tmp_path / "notes.txt").write_bytes(content.encode("utf-8"))
    read, _ = filesystem_tools(tmp_path)
    assert asyncio.run(execute(read, {"path": "notes.txt"})) == (
        "first\n\n\n[Showing lines 1–1. Use offset=2 to continue.]"
    )
    with pytest.raises(ValueError, match=r"Line 2 exceeds .*50 KiB.*offset=3"):
        asyncio.run(execute(read, {"path": "notes.txt", "offset": 2}))
    assert asyncio.run(execute(read, {"path": "notes.txt", "offset": 3})) == "last\n"


@pytest.mark.parametrize("content", [b"\xff\nlast", b"\xc3"])
def test_read_validates_utf8_while_skipping(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "notes.txt").write_bytes(content)
    read, _ = filesystem_tools(tmp_path)
    with pytest.raises(UnicodeDecodeError):
        asyncio.run(execute(read, {"path": "notes.txt", "offset": 2}))


def test_read_uses_bounded_io_and_stops_after_page(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.touch()
    read, _ = filesystem_tools(tmp_path)
    # An oversized skipped line forces several reads, including a split UTF-8 character.
    skipped = ("é" * (MAX_READ_BYTES * 3) + "\n").encode("utf-8")
    content = skipped + b"wanted\n" + b"\xff" * MAX_READ_BYTES
    stream = BytesIO(content)
    with patch.object(Path, "open") as open_file:
        file = open_file.return_value.__enter__.return_value
        file.readline.side_effect = stream.readline
        file.read.side_effect = stream.read
        output = asyncio.run(execute(read, {"path": path.name, "offset": 2, "limit": 1}))
    assert output == "wanted\n\n\n[Showing lines 2–2. Use offset=3 to continue.]"
    assert stream.tell() == len(skipped) + len(b"wanted\n") + 1
    assert all(0 < call.args[0] <= MAX_READ_BYTES + 1 for call in file.readline.call_args_list)
    file.read.assert_called_once_with(1)


def test_read_rejects_unreadable_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").touch()
    read, _ = filesystem_tools(tmp_path)
    with patch.object(Path, "open", side_effect=PermissionError("Unreadable file")):
        with pytest.raises(PermissionError, match="Unreadable"):
            asyncio.run(execute(read, {"path": "notes.txt"}))


def test_read_rejects_symlinks_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (root / "link").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlinks are unavailable: {error}")
    read, _ = filesystem_tools(root)
    with pytest.raises(PermissionError, match="outside"):
        asyncio.run(execute(read, {"path": "link"}))


def test_read_pages_pass_through_agent_requests_and_sessions(tmp_path: Path) -> None:
    async def case() -> None:
        content = "one\ntwo\nthree\nfour"
        (tmp_path / "notes.txt").write_bytes(content.encode())
        read, _ = filesystem_tools(tmp_path)
        provider = ScriptedProvider(
            [
                AssistantMessage(
                    (ToolCall("first", "read_file", {"path": "notes.txt", "limit": 2}),),
                    "tool_use",
                ),
                AssistantMessage(
                    (
                        ToolCall(
                            "next", "read_file", {"path": "notes.txt", "offset": 3, "limit": 2}
                        ),
                    ),
                    "tool_use",
                ),
                AssistantMessage((Text("Read all four lines."),)),
            ]
        )
        agent = Agent(provider, [read])
        assert (await agent.prompt("Read the file in pages of two lines.")).reason == "complete"
        assert provider.requests[0].tools == (read.spec,)
        schema = provider.requests[0].tools[0].parameters
        assert schema["required"] == ["path"]
        properties = schema["properties"]
        assert isinstance(properties, dict) and set(properties) == {"path", "offset", "limit"}
        assert schema["additionalProperties"] is False
        pages = [message for message in agent.messages if isinstance(message, ToolResult)]
        assert [page.is_error for page in pages] == [False, False]
        assert pages[0].content == "one\ntwo\n\n\n[Showing lines 1–2. Use offset=3 to continue.]"
        assert pages[1].content == "three\nfour"
        assert pages[0] in provider.requests[1].messages
        assert pages[1] in provider.requests[2].messages
        assert pages[0].content.split("\n\n[Showing", 1)[0] + pages[1].content == content
        assert agent.tool_calls_used == 2
        store = SessionStore(tmp_path / "session.jsonl", tmp_path)
        store.append_turn(agent.messages)
        assert SessionStore(store.path, tmp_path).messages == agent.messages

    asyncio.run(case())


def test_listing_is_bounded(tmp_path: Path) -> None:
    for number in range(MAX_ENTRIES + 1):
        (tmp_path / str(number)).touch()
    _, listing = filesystem_tools(tmp_path)
    output = asyncio.run(execute(listing, {}))
    assert len(output.splitlines()) == MAX_ENTRIES + 1
    assert "truncated" in output
