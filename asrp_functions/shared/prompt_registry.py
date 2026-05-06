"""Static, versioned per-slide prompt registry.

Prompts are checked into the repo under ``prompts/`` as YAML files named
``{slide_id}.{version}.yaml``. The registry loads them at construction
time, validates each against the :class:`Prompt` pydantic model, and
serves them through a small typed API.

Phase 1 keeps the registry filesystem-backed: no remote fetch, no
hot-reload. ``validate_all`` is intended to be invoked once during
Function App startup so a malformed prompt fails fast rather than at
slide-generation time.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.logging import get_logger

logger = get_logger(__name__)

# {slide_id}.{version}.yaml — slide_id and version are conservative
# slugs to avoid filesystem ambiguity on Windows / Linux.
_FILENAME_RE: Final = re.compile(
    r"^(?P<slide_id>[A-Za-z0-9_\-]+)\.(?P<version>[A-Za-z0-9_\-.]+)\.ya?ml$"
)
_DEFAULT_PROMPTS_DIR: Final = (
    Path(__file__).resolve().parent.parent / "prompts"
)


class Prompt(BaseModel):
    """Typed representation of a per-slide prompt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slide_id: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    system: str = Field(..., min_length=1)
    task: str = Field(..., min_length=1)
    constraints: list[str] = Field(default_factory=list)
    schema_ref: str = Field(..., min_length=1)
    language: str = Field(default="en-GB", min_length=2)


class PromptRegistryError(RuntimeError):
    """Raised when the registry cannot be constructed or validated."""


class PromptNotFoundError(KeyError):
    """Raised when a (slide_id, version) tuple is not registered."""


class PromptRegistry:
    """Loads and serves versioned per-slide prompts from disk."""

    def __init__(self, prompts_dir: Path | str | None = None) -> None:
        self._prompts_dir: Path = (
            Path(prompts_dir) if prompts_dir is not None else _DEFAULT_PROMPTS_DIR
        )
        if not self._prompts_dir.is_dir():
            raise PromptRegistryError(
                f"Prompts directory does not exist: {self._prompts_dir}"
            )
        self._prompts: dict[tuple[str, str], Prompt] = {}
        self._load_all()

    # ----------------------------------------------------------------- public

    def get(self, slide_id: str, version: str) -> Prompt:
        """Return the prompt for ``(slide_id, version)``.

        Raises:
            PromptNotFoundError: if no matching prompt is registered.
        """
        key = (slide_id, version)
        try:
            return self._prompts[key]
        except KeyError as exc:
            raise PromptNotFoundError(
                f"No prompt registered for slide_id={slide_id!r} version={version!r}"
            ) from exc

    def list_slide_ids(self) -> list[str]:
        """Return the unique slide ids known to the registry, sorted."""
        return sorted({slide_id for slide_id, _ in self._prompts})

    def validate_all(self) -> None:
        """Re-validate every prompt on disk; raise on the first failure.

        Intended for invocation during Function App startup. Models are
        re-parsed from disk so out-of-band edits cannot hide behind the
        in-memory cache.
        """
        try:
            self._load_all()
        except (ValidationError, PromptRegistryError, yaml.YAMLError) as exc:
            raise PromptRegistryError(
                f"Prompt validation failed: {exc}"
            ) from exc

    # ----------------------------------------------------------------- private

    def _load_all(self) -> None:
        loaded: dict[tuple[str, str], Prompt] = {}
        files = sorted(
            p
            for p in self._prompts_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}
        )

        for path in files:
            match = _FILENAME_RE.match(path.name)
            if match is None:
                raise PromptRegistryError(
                    f"Prompt filename does not match "
                    f"'{{slide_id}}.{{version}}.yaml': {path.name}"
                )
            file_slide_id = match.group("slide_id")
            file_version = match.group("version")

            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise PromptRegistryError(
                    f"Invalid YAML in {path.name}: {exc}"
                ) from exc

            if not isinstance(raw, dict):
                raise PromptRegistryError(
                    f"Prompt file must contain a YAML mapping: {path.name}"
                )

            try:
                prompt = Prompt.model_validate(raw)
            except ValidationError as exc:
                raise PromptRegistryError(
                    f"Prompt content invalid in {path.name}: {exc}"
                ) from exc

            if prompt.slide_id != file_slide_id or prompt.version != file_version:
                raise PromptRegistryError(
                    f"Filename/content mismatch in {path.name}: "
                    f"file=({file_slide_id}, {file_version}) "
                    f"content=({prompt.slide_id}, {prompt.version})"
                )

            key = (prompt.slide_id, prompt.version)
            if key in loaded:
                raise PromptRegistryError(
                    f"Duplicate prompt for slide_id={prompt.slide_id!r} "
                    f"version={prompt.version!r}"
                )
            loaded[key] = prompt

        self._prompts = loaded
        logger.info(
            "prompt_registry.loaded",
            extra={
                "prompts_dir": str(self._prompts_dir),
                "prompt_count": len(loaded),
                "slide_ids": sorted({sid for sid, _ in loaded}),
            },
        )


__all__ = [
    "Prompt",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptNotFoundError",
]
