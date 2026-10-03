"""Validateur JSON Schema minimal, en liste blanche de mots-clés.

L'agent ne fournit jamais de code : uniquement des objets JSON contrôlés par ces schémas.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

JsonSchema = dict[str, Any]

FORBIDDEN_KEYS = frozenset({"__proto__", "prototype", "constructor"})
MAX_KEYS = 64
MAX_SAFE_INTEGER = 2**53 - 1


class SchemaValidationError(Exception):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_finite_number(value: Any) -> bool:
    return is_number(value) and math.isfinite(value)


def is_safe_integer(value: Any) -> bool:
    if not is_number(value):
        return False
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer() and abs(value) <= MAX_SAFE_INTEGER
    return abs(value) <= MAX_SAFE_INTEGER


def json_size(value: Any) -> int:
    """Taille en octets de la sérialisation compacte (équivalent de `Buffer.byteLength(JSON.stringify)`)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _same(left: Any, right: Any) -> bool:
    """Égalité stricte à la JavaScript : pas de confusion entre `true` et `1`."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    return left == right and (is_number(left) == is_number(right))


def assert_schema(value: Any, schema: JsonSchema, path: str = "$") -> None:
    if "const" in schema:
        if json.dumps(value, sort_keys=False) != json.dumps(schema["const"], sort_keys=False):
            raise SchemaValidationError(path, "unexpected value")
        return
    if "anyOf" in schema:
        errors: list[str] = []
        for option in schema["anyOf"]:
            try:
                assert_schema(value, option, path)
                return
            except SchemaValidationError as error:
                errors.append(str(error))
        raise SchemaValidationError(path, f"no alternative matched ({'; '.join(errors)})")
    if "enum" in schema and not any(_same(entry, value) for entry in schema["enum"]):
        raise SchemaValidationError(path, "value is not in the enum")

    kind = schema.get("type")
    if kind == "null":
        if value is not None:
            raise SchemaValidationError(path, "expected null")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            raise SchemaValidationError(path, "expected boolean")
        return
    if kind == "integer":
        if not is_safe_integer(value):
            raise SchemaValidationError(path, "expected integer")
        _assert_numeric_bounds(value, schema, path)
        return
    if kind == "number":
        if not is_finite_number(value):
            raise SchemaValidationError(path, "expected number")
        _assert_numeric_bounds(value, schema, path)
        return
    if kind == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(path, "expected string")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaValidationError(path, "string too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaValidationError(path, "string too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaValidationError(path, "string does not match pattern")
        return
    if kind == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(path, "expected array")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaValidationError(path, "array too short")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaValidationError(path, "array too long")
        if "items" in schema:
            for index, entry in enumerate(value):
                assert_schema(entry, schema["items"], f"{path}[{index}]")
        return
    if kind == "object":
        _assert_object(value, schema, path)
        return
    if "properties" in schema or "additionalProperties" in schema:
        _assert_object(value, schema, path)


def _assert_numeric_bounds(value: float, schema: JsonSchema, path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise SchemaValidationError(path, "number below minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise SchemaValidationError(path, "number above maximum")


def _assert_object(value: Any, schema: JsonSchema, path: str) -> None:
    if not isinstance(value, dict):
        raise SchemaValidationError(path, "expected object")
    if len(value) > MAX_KEYS:
        raise SchemaValidationError(path, "too many keys")
    for key in value:
        if key in FORBIDDEN_KEYS:
            raise SchemaValidationError(f"{path}.{key}", "forbidden key")
    for required in schema.get("required", []):
        if required not in value:
            raise SchemaValidationError(path, f"missing {required}")
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties")
    for key, child in value.items():
        if key in properties:
            assert_schema(child, properties[key], f"{path}.{key}")
            continue
        if additional is False:
            raise SchemaValidationError(f"{path}.{key}", "unknown property")
        if isinstance(additional, dict):
            assert_schema(child, additional, f"{path}.{key}")
