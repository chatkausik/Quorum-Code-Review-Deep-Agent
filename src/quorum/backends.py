"""Backend adapters that enforce Quorum's filesystem trust boundaries."""

from __future__ import annotations

from pathlib import PurePosixPath

from deepagents.backends import FilesystemBackend, StateBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileUploadResponse,
    WriteResult,
)

READ_ONLY_ERROR = "permission_denied: this filesystem mount is read-only"
ARTIFACT_ONLY_ERROR = (
    "permission_denied: review state is immutable except for /findings/ artifacts"
)


def _is_finding_artifact(path: str) -> bool:
    candidate = PurePosixPath(path)
    return bool(
        path.startswith("/findings/")
        and path.endswith(".json")
        and "\x00" not in path
        and all(part not in ("", ".", "..") for part in candidate.parts[1:])
    )


class ReviewStateBackend(StateBackend):
    """Ephemeral state where agents may only write JSON finding artifacts.

    Pull-request source and patches are preloaded by trusted Python before the
    graph starts. Preventing agent writes to those mounts keeps prompt content
    from changing the evidence that reviewers and scanners inspect.
    """

    def write(self, file_path: str, content: str) -> WriteResult:
        if not _is_finding_artifact(file_path):
            return WriteResult(error=ARTIFACT_ONLY_ERROR)
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        if not _is_finding_artifact(file_path):
            return EditResult(error=ARTIFACT_ONLY_ERROR)
        return super().edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        if not _is_finding_artifact(file_path):
            return DeleteResult(error=ARTIFACT_ONLY_ERROR)
        return super().delete(file_path)

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        if any(not _is_finding_artifact(path) for path, _ in files):
            return [
                FileUploadResponse(path=path, error=ARTIFACT_ONLY_ERROR)
                for path, _ in files
            ]
        return super().upload_files(files)


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """A filesystem backend that permits reads but rejects every mutation."""

    def write(self, file_path: str, content: str) -> WriteResult:  # noqa: ARG002
        return WriteResult(error=READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:  # noqa: ARG002
        return WriteResult(error=READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:  # noqa: ARG002
        return EditResult(error=READ_ONLY_ERROR)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:  # noqa: ARG002
        return EditResult(error=READ_ONLY_ERROR)

    def delete(self, file_path: str) -> DeleteResult:  # noqa: ARG002
        return DeleteResult(error=READ_ONLY_ERROR)

    async def adelete(self, file_path: str) -> DeleteResult:  # noqa: ARG002
        return DeleteResult(error=READ_ONLY_ERROR)

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=READ_ONLY_ERROR) for path, _ in files]

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=READ_ONLY_ERROR) for path, _ in files]
