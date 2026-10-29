import asyncio
import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest
from filelock import FileLock

from pi_in_py.ai.auth import ApiKeyCredential, AuthError
from pi_in_py.app.auth_store import FileCredentialStore


def increment_credentials(path: str, count: int) -> None:
    async def case() -> None:
        store = FileCredentialStore(Path(path))
        for _ in range(count):

            def increment(current: ApiKeyCredential | None) -> ApiKeyCredential:
                value = int(current.key) if current is not None else 0
                time.sleep(0.01)
                return ApiKeyCredential(str(value + 1))

            await store.modify("groq", increment)

    asyncio.run(case())


def test_missing_store_is_side_effect_free_and_roundtrip_preserves_other_providers(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        store = FileCredentialStore(tmp_path / "config" / "auth.json")
        assert await store.read("groq") is None
        await store.delete("groq")
        assert not store.path.parent.exists()
        await store.modify("other", lambda current: ApiKeyCredential("other-key"))
        await store.modify("groq", lambda current: ApiKeyCredential("groq-key"))
        assert await FileCredentialStore(store.path).read("groq") == ApiKeyCredential("groq-key")
        await store.delete("groq")
        assert json.loads(store.path.read_text(encoding="utf-8")) == {
            "other": {"type": "api_key", "key": "other-key"},
        }

    asyncio.run(case())


@pytest.mark.parametrize(
    "content",
    [
        "broken-secret-value",
        "[]",
        '{"groq":null}',
        '{"groq":{"key":"fake-secret"}}',
        '{"groq":{"type":"oauth","key":"fake-secret"}}',
        '{"groq":{"type":"api_key","key":123}}',
        '{"groq":{"type":"api_key","key":""}}',
    ],
)
def test_corrupt_storage_is_reported_without_overwriting_or_exposing_it(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "auth.json"
    path.write_text(content, encoding="utf-8")
    store = FileCredentialStore(path)

    async def case() -> None:
        for operation in (
            store.read("groq"),
            store.modify("groq", lambda current: ApiKeyCredential("replacement")),
            store.delete("groq"),
        ):
            with pytest.raises(AuthError) as failure:
                await operation
            assert "fake-secret" not in str(failure.value)
            assert "broken-secret-value" not in str(failure.value)
            assert path.read_text(encoding="utf-8") == content

    asyncio.run(case())


def test_failed_replacement_preserves_the_file_and_cleans_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "auth.json"
    content = '{"groq":{"type":"api_key","key":"old-key"}}'
    path.write_text(content, encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        asyncio.run(
            FileCredentialStore(path).modify("groq", lambda current: ApiKeyCredential("new-key"))
        )
    assert path.read_text(encoding="utf-8") == content
    assert not list(tmp_path.glob(".auth-*.tmp"))


def test_busy_store_reports_a_bounded_timeout(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{}", encoding="utf-8")
    with FileLock(str(path) + ".lock"):
        with pytest.raises(AuthError, match="busy"):
            asyncio.run(FileCredentialStore(path, lock_timeout=0.05).read("groq"))


def test_concurrent_process_updates_do_not_lose_changes(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=increment_credentials, args=(str(path), 10)) for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        assert asyncio.run(FileCredentialStore(path).read("groq")) == ApiKeyCredential("20")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="Windows inherits access control from the user profile")
def test_new_auth_file_and_directory_have_private_posix_modes(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path / "config" / "auth.json")
    asyncio.run(store.modify("groq", lambda current: ApiKeyCredential("fake-key")))
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
