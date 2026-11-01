"""Operator-configurable, bounded capacities; model context and action budgets stay separate."""
import os
from dataclasses import dataclass

MIB = 1024 * 1024


def positive_env(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer; zero does not disable the limit")
    return value


@dataclass(frozen=True)
class RepositoryLimits:
    download_bytes: int = 256 * MIB
    expanded_bytes: int = 1024 * MIB
    extracted_bytes: int = 768 * MIB
    file_bytes: int = 256 * MIB
    members: int = 50_000

    @classmethod
    def from_env(cls):
        limits = cls(
            download_bytes=positive_env("REPORUNNER_MAX_DOWNLOAD_MB", 256) * MIB,
            expanded_bytes=positive_env("REPORUNNER_MAX_ARCHIVE_MB", 1024) * MIB,
            extracted_bytes=positive_env("REPORUNNER_MAX_SOURCE_MB", 768) * MIB,
            file_bytes=positive_env("REPORUNNER_MAX_FILE_MB", 256) * MIB,
            members=positive_env("REPORUNNER_MAX_MEMBERS", 50_000))
        if limits.file_bytes > limits.extracted_bytes or limits.extracted_bytes > limits.expanded_bytes:
            raise ValueError("Repository limits must satisfy MAX_FILE_MB <= MAX_SOURCE_MB <= MAX_ARCHIVE_MB")
        return limits

    def describe(self):
        return {"download_mb": self.download_bytes // MIB, "expanded_archive_mb": self.expanded_bytes // MIB,
                "source_mb": self.extracted_bytes // MIB, "file_mb": self.file_bytes // MIB,
                "members": self.members, "unit": "MiB (1,048,576 bytes)"}


@dataclass(frozen=True)
class SandboxLimits:
    memory_mb: int = 2048
    workspace_mb: int = 1024
    temporary_mb: int = 192

    @classmethod
    def from_env(cls):
        limits = cls(positive_env("REPORUNNER_MEMORY_MB", 2048),
                     positive_env("REPORUNNER_WORKSPACE_MB", 1024),
                     positive_env("REPORUNNER_TMP_MB", 192))
        if limits.workspace_mb + limits.temporary_mb >= limits.memory_mb:
            raise ValueError("REPORUNNER_MEMORY_MB must exceed WORKSPACE_MB + TMP_MB to leave room for the runtime")
        if limits.workspace_mb * MIB < RepositoryLimits.from_env().extracted_bytes:
            raise ValueError("REPORUNNER_WORKSPACE_MB must hold REPORUNNER_MAX_SOURCE_MB plus dependency/build space")
        return limits
