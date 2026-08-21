"""Safe, duplicate-key rejecting YAML loading for experiment plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .models import ExperimentConfig


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader which retains SafeLoader semantics and rejects ambiguity."""


def _unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConstructorError(
                "while constructing an experiment mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    """Load one regular YAML file without following a final symlink."""

    supplied = Path(path)
    if supplied.is_symlink():
        raise ValueError("experiment configuration cannot be a symlink")
    resolved = supplied.resolve(strict=True)
    if resolved.suffix.casefold() not in {".yaml", ".yml"} or not resolved.is_file():
        raise ValueError("experiment configuration must be a YAML file")
    payload = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(payload, dict):
        raise ValueError("experiment configuration must contain one mapping")
    return ExperimentConfig.model_validate(payload)
