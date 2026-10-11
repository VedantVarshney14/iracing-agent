"""The small expression language rules are written in: conditions and the text they say.

An expression is a restricted subset of Python syntax, parsed once and compiled to closures, so
it is cheap enough to evaluate every frame and can't run arbitrary code:

- numbers, strings, `true`/`false`/`none` (Python spellings work too), lists `[1, 2]`;
- variables (whatever the trigger provides: channels, corner metrics, lap facts);
- `+ - * / // %`, comparisons (chains like `20 < speed_kph < 80` too), `and`, `or`, `not`,
  `x in [..]`, `a if cond else b`;
- the functions in `FUNCTIONS`.

A missing value is `none`, and it doesn't fail the rule: arithmetic on it gives `none`, and a
comparison with it (other than `== none` / `!= none`) is false. So `brake_m < ref_brake_m - 10`
is simply false on a corner taken without braking.

Templates are text with `{expression}` or `{expression:format}` holes, e.g.
`"{name}: braked {round5(-brake_diff_m)} metres early."`; `{{` and `}}` are literal braces.
"""

import ast
import operator
from typing import Any, Callable, Mapping

Vars = Mapping[str, Any]


class ExprError(ValueError):
    pass


def round5(x: float) -> int:
    """To the nearest 5 (at least 5): how distances are said."""
    return max(5, int(round(x / 5.0)) * 5)


def say_time(s: float) -> str:
    """A lap time as said: "1 44.2", or "58.3" under a minute."""
    minutes, seconds = divmod(s, 60)
    return f"{int(minutes)} {seconds:04.1f}" if minutes else f"{seconds:.1f}"


def say_gap(gap: float) -> str:
    """A gap as said: "3 tenths", "1.2 seconds"."""
    gap = abs(gap)
    if gap < 1.0:
        tenths = max(1, round(gap * 10))
        return f"{tenths} tenth{'s' if tenths != 1 else ''}"
    whole = round(gap, 1)
    return f"{whole:.0f} seconds" if whole == int(whole) else f"{whole:.1f} seconds"


FUNCTIONS: dict[str, tuple[Callable, str]] = {
    "abs": (abs, "abs(x): size without sign"),
    "min": (min, "min(a, b, ...)"),
    "max": (max, "max(a, b, ...)"),
    "round": (round, "round(x) or round(x, digits)"),
    "round5": (round5, "round5(x): to the nearest 5, at least 5 (how metres are said)"),
    "say_time": (say_time, 'say_time(lap_time): "1 44.2"'),
    "say_gap": (say_gap, 'say_gap(seconds): "3 tenths", "1.2 seconds"'),
}

_CONSTANTS = {"true": True, "false": False, "none": None, "null": None}

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_COMPARE = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}


class Expr:
    """A compiled expression: call it with the variables to get its value."""

    def __init__(self, text: str):
        self.text = text.strip()
        if not self.text:
            raise ExprError("Empty expression.")
        try:
            tree = ast.parse(self.text, mode="eval")
        except SyntaxError as e:
            raise ExprError(f"Can't read {self.text!r}: {e.msg}.") from None
        self.names: set[str] = set()
        self._fn = self._compile(tree.body)

    def __call__(self, env: Vars) -> Any:
        try:
            return self._fn(env)
        except (TypeError, ZeroDivisionError, ValueError, OverflowError):
            return None  # e.g. a string compared with a number: treated like a missing value

    def __repr__(self) -> str:
        return f"Expr({self.text!r})"

    def _compile(self, node: ast.AST) -> Callable[[Vars], Any]:
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool, type(None))):
                raise ExprError(f"Unsupported value {node.value!r} in {self.text!r}.")
            value = node.value
            return lambda env: value
        if isinstance(node, ast.Name):
            name = node.id
            if name in _CONSTANTS or name in ("True", "False", "None"):
                value = _CONSTANTS.get(name.lower())
                return lambda env: value
            self.names.add(name)
            return lambda env: env.get(name)
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self._compile(e) for e in node.elts]
            return lambda env: [f(env) for f in items]
        if isinstance(node, ast.UnaryOp):
            arg = self._compile(node.operand)
            if isinstance(node.op, ast.Not):
                return lambda env: not arg(env)
            if isinstance(node.op, ast.USub):
                return lambda env: None if (v := arg(env)) is None else -v
            if isinstance(node.op, ast.UAdd):
                return arg
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            op, left, right = _BINARY[type(node.op)], self._compile(node.left), self._compile(node.right)

            def binary(env):
                a, b = left(env), right(env)
                return None if a is None or b is None else op(a, b)
            return binary
        if isinstance(node, ast.BoolOp):
            parts = [self._compile(v) for v in node.values]
            if isinstance(node.op, ast.And):
                def all_of(env):
                    v = True
                    for p in parts:
                        v = p(env)
                        if not v:
                            return v
                    return v
                return all_of

            def any_of(env):
                v = False
                for p in parts:
                    v = p(env)
                    if v:
                        return v
                return v
            return any_of
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.IfExp):
            test, yes, no = self._compile(node.test), self._compile(node.body), self._compile(node.orelse)
            return lambda env: yes(env) if test(env) else no(env)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                called = node.func.id if isinstance(node.func, ast.Name) else ast.unparse(node.func)
                raise ExprError(f"Unknown function {called!r} in {self.text!r}; there are: {', '.join(FUNCTIONS)}.")
            if node.keywords:
                raise ExprError(f"Functions take positional arguments only ({self.text!r}).")
            fn = FUNCTIONS[node.func.id][0]
            args = [self._compile(a) for a in node.args]

            def call(env):
                values = [a(env) for a in args]
                return None if any(v is None for v in values) else fn(*values)
            return call
        raise ExprError(f"Unsupported syntax {ast.unparse(node)!r} in {self.text!r}.")

    def _compare(self, node: ast.Compare) -> Callable[[Vars], Any]:
        first = self._compile(node.left)
        steps = []
        for op, right in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)):
                negate = isinstance(op, ast.NotIn)
                steps.append((lambda a, b, n=negate: (a in b) != n if b is not None else False, self._compile(right), False))
            elif isinstance(op, (ast.Is, ast.IsNot)) or type(op) in (ast.Eq, ast.NotEq):
                fn = operator.eq if isinstance(op, (ast.Eq, ast.Is)) else operator.ne
                steps.append((fn, self._compile(right), True))  # equality is defined for none
            elif type(op) in _COMPARE:
                steps.append((_COMPARE[type(op)], self._compile(right), False))
            else:
                raise ExprError(f"Unsupported comparison in {self.text!r}.")

        def compare(env):
            a = first(env)
            for fn, right, none_ok in steps:
                b = right(env)
                if not none_ok and (a is None or b is None):
                    return False
                try:
                    if not fn(a, b):
                        return False
                except TypeError:  # e.g. a name compared with a number: not a match
                    return False
                a = b
            return True
        return compare


class Template:
    """Text with `{expression[:format]}` holes. Renders to None if a hole has no value, so a rule
    never says "braked None metres early"."""

    def __init__(self, text: str):
        self.text = text
        self.parts: list[str | tuple[Expr, str]] = []
        self.names: set[str] = set()
        i, literal = 0, []
        while i < len(text):
            ch = text[i]
            if ch in "{}" and text[i:i + 2] in ("{{", "}}"):
                literal.append(ch)
                i += 2
                continue
            if ch == "}":
                raise ExprError(f"Unmatched '}}' in {text!r} (write '}}}}' for a brace).")
            if ch != "{":
                literal.append(ch)
                i += 1
                continue
            end, depth = i + 1, 0
            while end < len(text) and not (text[end] == "}" and depth == 0):
                depth += {"(": 1, "[": 1, ")": -1, "]": -1}.get(text[end], 0)
                end += 1
            if end >= len(text):
                raise ExprError(f"Unclosed '{{' in {text!r}.")
            body = text[i + 1:end]
            expr_text, spec = _split_spec(body)
            expr = Expr(expr_text)
            if spec:
                try:
                    format(1.5, spec)
                except ValueError:
                    try:
                        format("x", spec)
                    except ValueError:
                        raise ExprError(f"Bad format {spec!r} in {text!r}.") from None
            if literal:
                self.parts.append("".join(literal))
                literal = []
            self.parts.append((expr, spec))
            self.names |= expr.names
            i = end + 1
        if literal:
            self.parts.append("".join(literal))

    def render(self, env: Vars) -> str | None:
        out = []
        for part in self.parts:
            if isinstance(part, str):
                out.append(part)
                continue
            expr, spec = part
            value = expr(env)
            if value is None:
                return None
            if isinstance(value, float) and not spec:
                value = round(value) if value == int(value) else round(value, 2)
            try:
                out.append(format(value, spec))
            except (ValueError, TypeError):
                out.append(str(value))
        return "".join(out)

    def missing(self, env: Vars) -> list[str]:
        """The holes without a value (to say why a line wasn't said)."""
        return [p[0].text for p in self.parts if not isinstance(p, str) and p[0](env) is None]


def _split_spec(body: str) -> tuple[str, str]:
    """`expr:spec` -> (expr, spec): the last top-level ':' that isn't part of a slice or dict."""
    depth = 0
    for j in range(len(body) - 1, -1, -1):
        ch = body[j]
        if ch in ")]":
            depth += 1
        elif ch in "([":
            depth -= 1
        elif ch == ":" and depth == 0:
            return body[:j], body[j + 1:]
    return body, ""
