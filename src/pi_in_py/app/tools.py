import asyncio
from codecs import getincrementaldecoder
from itertools import islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pi_in_py.agent.types import Tool, define_tool

MAX_READ_BYTES = 50 * 1024
MAX_READ_LINES = 2000
MAX_ENTRIES = 200


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(
        min_length=1,
        description="UTF-8 file path, workspace-relative or absolute inside the workspace",
    )
    offset: int = Field(default=1, ge=1, description="First line to read, starting at 1")
    limit: int = Field(
        default=MAX_READ_LINES,
        ge=1,
        description=f"Maximum lines to read; output is capped at {MAX_READ_LINES} lines or 50 KiB",
    )


class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(default=".", min_length=1, description="Directory relative to the workspace")


def filesystem_tools(workspace: Path) -> tuple[Tool, ...]:
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace must be a directory")

    def resolve(path: str) -> Path:
        resolved = (root / path).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise PermissionError("Path is outside the workspace")
        return resolved

    def read_sync(args: ReadFileArgs) -> str:
        path = resolve(args.path)
        if not path.is_file():
            raise ValueError("Path must be a regular file")
        with path.open("rb") as file:
            # Decode skipped fragments incrementally: a chunk may split a UTF-8 character.
            decoder = getincrementaldecoder("utf-8")()
            for _ in range(1, args.offset):
                while True:
                    chunk = file.readline(MAX_READ_BYTES + 1)
                    decoder.decode(chunk, final=not chunk)
                    if not chunk:
                        raise ValueError(f"Offset {args.offset} is beyond end of file")
                    if chunk.endswith(b"\n"):
                        break

            lines: list[str] = []
            output_bytes = 0
            for _ in range(min(args.limit, MAX_READ_LINES)):
                line = file.readline(MAX_READ_BYTES - output_bytes + 1)
                if not line:
                    if not lines and args.offset > 1:
                        raise ValueError(f"Offset {args.offset} is beyond end of file")
                    return "".join(lines)
                if output_bytes + len(line) > MAX_READ_BYTES:
                    if not lines:
                        raise ValueError(
                            f"Line {args.offset} exceeds the {MAX_READ_BYTES}-byte (50 KiB) limit; "
                            f"read_file returns complete lines. Use offset={args.offset + 1} "
                            "to skip it."
                        )
                    break
                lines.append(line.decode("utf-8"))
                output_bytes += len(line)
            else:
                if not file.read(1):
                    return "".join(lines)

        next_offset = args.offset + len(lines)
        return (
            "".join(lines) + f"\n\n[Showing lines {args.offset}–{next_offset - 1}. "
            f"Use offset={next_offset} to continue.]"
        )

    async def read_file(args: ReadFileArgs) -> str:
        return await asyncio.to_thread(read_sync, args)

    def list_sync(args: ListFilesArgs) -> str:
        entries = list(islice(resolve(args.path).iterdir(), MAX_ENTRIES + 1))
        names = sorted(path.name + ("/" if path.is_dir() else "") for path in entries[:MAX_ENTRIES])
        if len(entries) > MAX_ENTRIES:
            names.append(f"[Listing truncated at {MAX_ENTRIES} entries]")
        return "\n".join(names) or "[Empty directory]"

    async def list_files(args: ListFilesArgs) -> str:
        return await asyncio.to_thread(list_sync, args)

    return (
        define_tool(
            "read_file",
            "Read a UTF-8 file using a workspace-relative or absolute path inside the workspace. "
            f"offset starts at line 1; limit defaults to {MAX_READ_LINES}. Return at most "
            f"{MAX_READ_LINES} complete lines or {MAX_READ_BYTES // 1024} KiB of file content, "
            "preserving line endings. Partial results append the range read and the next offset; "
            "call again with that offset to continue. No total line count is calculated. "
            "A first requested line over 50 KiB returns an error; a later oversized line ends "
            "the page before that line. Missing paths, unreadable files, invalid UTF-8 encountered "
            "while reading, non-files, offsets beyond EOF, and paths resolving outside the "
            "workspace return errors; an error is not evidence of absent code. "
            "An empty file returns empty text at offset 1. Separate calls read the current file.",
            ReadFileArgs,
            read_file,
        ),
        define_tool(
            "list_files",
            f"List one directory non-recursively using a workspace-relative path, up to "
            f"{MAX_ENTRIES} entries. A truncation marker means incomplete coverage, not absent "
            "files. Missing paths, unreadable directories, non-directories, and paths resolving "
            "outside the workspace return errors.",
            ListFilesArgs,
            list_files,
        ),
    )
