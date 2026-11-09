"""Kleiner JSON-Schema-Prüfer für die Werkzeug-Parameter (nur die benötigten Schlüsselwörter, bewusst streng)."""
from __future__ import annotations

import re

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def validate(args, schema: dict) -> list[str]:
    """Gibt eine Liste von Fehlern zurück (leer = gültig). Unbekannte Felder sind ein Fehler."""
    if not isinstance(args, dict):
        return ["Die Argumente müssen ein Objekt sein."]
    errors: list[str] = []
    props = schema.get("properties", {})
    for key in args:
        if key not in props:
            errors.append(f"Unbekanntes Argument: {key}")
    for key in schema.get("required", []):
        if key not in args:
            errors.append(f"Pflichtargument fehlt: {key}")
    for key, spec in props.items():
        if key not in args:
            continue
        value = args[key]
        expected = _TYPES[spec["type"]]
        if isinstance(value, bool) and spec["type"] != "boolean":
            errors.append(f"{key}: falscher Typ")
            continue
        if not isinstance(value, expected):
            errors.append(f"{key}: erwartet {spec['type']}")
            continue
        if spec["type"] == "string":
            if "maxLength" in spec and len(value) > spec["maxLength"]:
                errors.append(f"{key}: zu lang (max. {spec['maxLength']})")
            if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
                errors.append(f"{key}: ungültiges Format")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{key}: erlaubt sind {', '.join(map(str, spec['enum']))}")
        if spec["type"] in ("integer", "number"):
            if "minimum" in spec and value < spec["minimum"]:
                errors.append(f"{key}: zu klein")
            if "maximum" in spec and value > spec["maximum"]:
                errors.append(f"{key}: zu groß")
    return errors
