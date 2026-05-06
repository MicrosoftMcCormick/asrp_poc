"""Tests for :mod:`shared.prompt_registry`."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.prompt_registry import PromptRegistry, PromptRegistryError


_VALID_YAML = """\
slide_id: footprint_v1
version: v1
language: en-GB
schema_ref: schemas/footprint.v1.json
system: |
  Generate the HSBC footprint slide using only grounded context.
task: |
  Produce a JSON object describing customer footprint.
constraints:
  - Use only grounded data.
"""


def _write_prompt(dir_path: Path, filename: str, content: str) -> None:
    (dir_path / filename).write_text(content, encoding="utf-8")


def test_validate_all_passes_for_well_formed_prompt(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "footprint_v1.v1.yaml", _VALID_YAML)
    registry = PromptRegistry(prompts_dir=tmp_path)
    registry.validate_all()
    assert registry.list_slide_ids() == ["footprint_v1"]
    prompt = registry.get("footprint_v1", "v1")
    assert prompt.language == "en-GB"


def test_validate_all_fails_on_malformed_yaml(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "broken.v1.yaml", "this: is: not: valid: yaml: [\n")

    with pytest.raises(PromptRegistryError):
        PromptRegistry(prompts_dir=tmp_path)


def test_validate_all_fails_on_filename_content_mismatch(tmp_path: Path) -> None:
    bad = _VALID_YAML.replace("slide_id: footprint_v1", "slide_id: payments_stp_v1")
    _write_prompt(tmp_path, "footprint_v1.v1.yaml", bad)
    with pytest.raises(PromptRegistryError):
        PromptRegistry(prompts_dir=tmp_path)


def test_validate_all_fails_on_missing_required_key(tmp_path: Path) -> None:
    incomplete = """
slide_id: footprint_v1
version: v1
system: hello
task: do the thing
"""
    _write_prompt(tmp_path, "footprint_v1.v1.yaml", incomplete)
    with pytest.raises(PromptRegistryError):
        PromptRegistry(prompts_dir=tmp_path)
