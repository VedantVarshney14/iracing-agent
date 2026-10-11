"""Every non-trivial class, function and method has a docstring.

Trivial: a function of at most two statements (a property, a one-line helper). An override of a
documented method of a base class in the same module inherits its docstring. Tests are named for
what they check, so test functions are exempt; their helpers and fixtures aren't.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _size(node: ast.AST) -> int:
    """Statements in NODE's body, nested ones included."""
    return sum(1 for n in ast.walk(node) if isinstance(n, ast.stmt)) - 1


def _undocumented(path: Path) -> list[str]:
    """Non-trivial definitions in PATH without a docstring, as "path:line name"."""
    tree = ast.parse(path.read_text())
    classes = {c.name: c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)}

    def documented_in_base(cls: ast.ClassDef, name: str, seen: tuple = ()) -> bool:
        for b in cls.bases:
            base = classes.get(getattr(b, "id", None))
            if base is None or base.name in seen:
                continue
            if any(isinstance(f, ast.FunctionDef) and f.name == name and ast.get_docstring(f) for f in base.body):
                return True
            if documented_in_base(base, name, (*seen, base.name)):
                return True
        return False

    out = []

    def visit(node: ast.AST, cls: ast.ClassDef | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            is_class = isinstance(child, ast.ClassDef)
            exempt = (child.name == "__init__" or child.name.startswith("test_")
                      or (not is_class and _size(child) <= 2)
                      or (not is_class and cls is not None and documented_in_base(cls, child.name)))
            if not exempt and ast.get_docstring(child) is None:
                out.append(f"{path.relative_to(ROOT)}:{child.lineno} {child.name}")
            if is_class:
                visit(child, child)

    visit(tree)
    return out


def test_every_non_trivial_definition_has_a_docstring():
    missing = [m for folder in ("iagent", "tests") for p in sorted((ROOT / folder).rglob("*.py")) for m in _undocumented(p)]
    assert not missing, "Add docstrings:\n" + "\n".join(missing)
