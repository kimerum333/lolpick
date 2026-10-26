"""Diagnostic locations/codes without SQL parameters, source text or secrets."""
from pathlib import Path
import traceback


def error_details(exc):
    frames = traceback.extract_tb(exc.__traceback__)[-20:]
    result = {"type": type(exc).__name__, "stack": [
        {"file": "/".join(Path(f.filename).parts[-3:]), "function": f.name, "line": f.lineno} for f in frames]}
    if getattr(exc, "code", None):
        result["code"] = str(exc.code)[:100]
    original = getattr(exc, "orig", None)
    if getattr(original, "sqlstate", None):
        result["sqlstate"] = original.sqlstate
    diagnostic = getattr(original, "diag", None)
    for name in ("constraint_name", "table_name", "column_name"):
        value = getattr(diagnostic, name, None)
        if value:
            result[name] = str(value)[:160]
    return result
