"""Reading hand-written YAML question files strictly, with errors a non-programmer can act on.

Shared by evals/retrieval.yaml (recall) and evals/golden.yaml (answer evaluation).
"""

from pathlib import Path

import yaml
from pydantic import ValidationError


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects a key repeated within one question (safe_load keeps the last)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:  # point at the second copy: that's usually the leftover line
                raise yaml.constructor.ConstructorError(
                    None, None, f"'{key}' appears twice in one question", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def read_yaml_list(path: Path, error: type[Exception]) -> list:
    """The file's top-level list; `error` is raised with a readable message on any problem."""
    if not path.exists():
        raise error(f"{path.name} not found: Ben writes it (see docs/HANDOVER.md)")
    try:
        text = path.read_text(encoding="utf-8")
        rows = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - a SafeLoader subclass
    except UnicodeDecodeError:
        raise error(f"{path.name} is not UTF-8 text") from None
    except yaml.YAMLError as err:
        raise error(_yaml_error(path.name, text, err)) from None
    if rows is None:  # only comments so far
        return []
    if not isinstance(rows, list):
        raise error(f"{path.name} must be a list: start each question with '- id:'")
    return rows


def _yaml_error(name: str, text: str, err: yaml.YAMLError) -> str:
    mark = getattr(err, "problem_mark", None)
    problem = getattr(err, "problem", None) or "unreadable"
    if mark is None:
        return f"{name} is not valid YAML: {problem}"
    lines = text.splitlines()
    shown = lines[mark.line].strip() if mark.line < len(lines) else ""
    where = f"{name} line {mark.line + 1}: {problem}\n  > {shown}"
    if isinstance(err, yaml.constructor.ConstructorError):
        return where  # the problem text already says what's wrong
    return (
        f"{where}\n"
        "  Common causes: a tab instead of spaces, a missing '- ' before id, a colon or # in "
        "text that isn't in double quotes, or curly quotes pasted from Word."
    )


def first_error(err: ValidationError, fields: str) -> str:
    """The most useful single message from a row's validation errors."""
    errors = err.errors()
    # A misspelt field shows up twice (extra "expectd", missing "expected"): name the typo.
    first = next((e for e in errors if e["type"] == "extra_forbidden"), errors[0])
    where = ".".join(str(p) for p in first["loc"]) or "entry"
    if first["type"] == "extra_forbidden":
        return f"unknown field {where!r} (allowed: {fields})"
    if first["type"] == "string_type":
        return f"{where} must be text: put it in double quotes"
    if first["type"] == "model_type":
        return "not a question: each one starts with '- id:'"
    return f"{where}: {first['msg'].removeprefix('Value error, ')}"
