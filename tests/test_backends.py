from __future__ import annotations

import asyncio

from quorum.backends import ReadOnlyFilesystemBackend, ReviewStateBackend


def test_read_only_backend_reads_but_rejects_mutations(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("trusted guidance", encoding="utf-8")
    backend = ReadOnlyFilesystemBackend(root_dir=tmp_path)

    assert backend.read("/skill.md").file_data["content"] == "trusted guidance"
    assert backend.write("/skill.md", "poisoned").error
    assert backend.edit("/skill.md", "trusted", "poisoned").error
    assert backend.delete("/skill.md").error
    assert backend.upload_files([("/new.md", b"poisoned")])[0].error

    assert skill.read_text(encoding="utf-8") == "trusted guidance"
    assert not (tmp_path / "new.md").exists()


def test_async_mutations_are_also_rejected(tmp_path):
    backend = ReadOnlyFilesystemBackend(root_dir=tmp_path)

    result = asyncio.run(backend.awrite("/new.md", "poisoned"))

    assert result.error
    assert not (tmp_path / "new.md").exists()


def test_review_state_rejects_source_and_patch_mutations():
    backend = ReviewStateBackend()

    assert backend.write("/pr/src/app.py", "poisoned").error
    assert backend.edit("/patches/src/app.py.patch", "a", "b").error
    assert backend.delete("/pr/src/app.py").error
    assert backend.upload_files([("/pr/src/app.py", b"poisoned")])[0].error
    assert backend.write("/findings/../pr/src/app.py.json", "{}").error
