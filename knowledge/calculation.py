"""Arithmetic sandbox: an isolated interpreter for bounded numeric expressions and named columns."""

import ast
import json
import math
import operator
import os
import sys

BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def evaluate(expression, columns):
    tree = ast.parse(expression.strip(), mode="eval")
    if len(expression) > 500 or sum(1 for _ in ast.walk(tree)) > 90:
        raise ValueError("expression_limit")

    def number(value):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or abs(value) > 1e15
        ):
            raise ValueError("number_limit")
        return value

    def visit(node):
        if isinstance(node, ast.Constant):
            return number(node.value)
        if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.USub, ast.UAdd):
            value = number(visit(node.operand))
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            left, right = number(visit(node.left)), number(visit(node.right))
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("power_limit")
            return number(BINARY[type(node.op)](left, right))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and not node.keywords
        ):
            name = node.func.id
            if (
                name == "col"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
            ):
                values = columns[node.args[0].value]
                return [number(v) for v in values]
            if name in ("sum", "mean", "min", "max", "count", "round"):
                values = [visit(arg) for arg in node.args]
                if len(values) == 1 and isinstance(values[0], list):
                    values = values[0]
                values = [number(v) for v in values]
                if not values:
                    raise ValueError("empty")
                if name == "round" and len(values) in (1, 2):
                    places = values[1] if len(values) > 1 else 0
                    if type(places) is int and abs(places) <= 12:
                        return round(values[0], places)
                funcs = {
                    "sum": sum,
                    "mean": lambda xs: sum(xs) / len(xs),
                    "min": min,
                    "max": max,
                    "count": len,
                }
                return number(funcs[name](values))
        raise ValueError("unsupported_expression")

    return number(visit(tree.body))


if __name__ == "__main__":
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_AS, (96 << 20, 96 << 20))
    try:
        data = json.load(sys.stdin)
        print(
            json.dumps(evaluate(data["expression"], data["columns"]), allow_nan=False)
        )
    except (
        ArithmeticError,
        TypeError,
        ValueError,
        KeyError,
        SyntaxError,
        RecursionError,
    ):
        sys.exit(2)
