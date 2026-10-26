import asyncio
from itertools import islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pi_in_py.agent.types import Tool, define_tool

MAX_FILE_BYTES = 64 * 1024
MAX_ENTRIES = 200


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, description="UTF-8 file path relative to the workspace")


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
            data = file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the {MAX_FILE_BYTES}-byte v1 limit")
        return data.decode("utf-8")

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
        define_tool("read_file", "Read a UTF-8 file, up to 64 KiB.", ReadFileArgs, read_file),
        define_tool(
            "list_files", "List one directory, up to 200 entries.", ListFilesArgs, list_files
        ),
    )
