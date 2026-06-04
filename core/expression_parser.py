from __future__ import annotations

import ast
import operator
from typing import Any

import pandas as pd


class ExpressionError(ValueError):
    """Raised when a table expression is not allowed or cannot be evaluated."""


class ExpressionParser:
    ALLOWED_FUNCTIONS = {
        "abs",
        "round",
        "isna",
        "notna",
        "fillna",
        "contains",
        "startswith",
        "endswith",
        "isin",
    }

    _BIN_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
    }

    _COMPARE_OPS = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
    }

    def parse(self, expression: str) -> ast.Expression:
        try:
            parsed = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"表达式语法错误: {expression}") from exc

        self._validate(parsed)
        return parsed

    def evaluate(self, expression: str, df: pd.DataFrame) -> pd.Series:
        parsed = self.parse(expression)
        value = self._eval(parsed.body, df)
        if isinstance(value, pd.Series):
            return value
        return pd.Series([value] * len(df), index=df.index)

    def _validate(self, node: ast.AST) -> None:
        allowed_nodes = (
            ast.Expression,
            ast.BoolOp,
            ast.BinOp,
            ast.UnaryOp,
            ast.Compare,
            ast.Call,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.List,
            ast.Tuple,
            ast.And,
            ast.Or,
            ast.Not,
            ast.USub,
            ast.UAdd,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Eq,
            ast.NotEq,
            ast.Gt,
            ast.GtE,
            ast.Lt,
            ast.LtE,
            ast.In,
            ast.NotIn,
        )

        for child in ast.walk(node):
            if not isinstance(child, allowed_nodes):
                raise ExpressionError(f"不允许的表达式节点: {type(child).__name__}")
            if isinstance(child, ast.Call):
                if not isinstance(child.func, ast.Name):
                    raise ExpressionError("只允许调用白名单函数")
                if child.func.id not in self.ALLOWED_FUNCTIONS:
                    raise ExpressionError(f"函数不在白名单中: {child.func.id}")

    def _eval(self, node: ast.AST, df: pd.DataFrame) -> Any:
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.List):
            return [self._eval(item, df) for item in node.elts]

        if isinstance(node, ast.Tuple):
            return tuple(self._eval(item, df) for item in node.elts)

        if isinstance(node, ast.Name):
            if node.id == "True":
                return True
            if node.id == "False":
                return False
            if node.id == "None":
                return None
            if node.id not in df.columns:
                raise ExpressionError(f"未知列名: {node.id}")
            return df[node.id]

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in self._BIN_OPS:
                raise ExpressionError(f"不允许的运算符: {op_type.__name__}")
            return self._BIN_OPS[op_type](self._eval(node.left, df), self._eval(node.right, df))

        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, df)
            if isinstance(node.op, ast.Not):
                return ~value if isinstance(value, pd.Series) else not value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            raise ExpressionError(f"不允许的一元运算符: {type(node.op).__name__}")

        if isinstance(node, ast.BoolOp):
            values = [self._eval(value, df) for value in node.values]
            result = values[0]
            for value in values[1:]:
                if isinstance(node.op, ast.And):
                    result = result & value
                elif isinstance(node.op, ast.Or):
                    result = result | value
                else:
                    raise ExpressionError(f"不允许的布尔运算符: {type(node.op).__name__}")
            return result

        if isinstance(node, ast.Compare):
            left = self._eval(node.left, df)
            result = None
            for op_node, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, df)
                current = self._compare(left, op_node, right)
                result = current if result is None else result & current
                left = right
            return result

        if isinstance(node, ast.Call):
            args = [self._eval(arg, df) for arg in node.args]
            kwargs = {kw.arg: self._eval(kw.value, df) for kw in node.keywords}
            return self._call(node.func.id, args, kwargs)

        raise ExpressionError(f"不支持的表达式节点: {type(node).__name__}")

    def _compare(self, left: Any, op_node: ast.cmpop, right: Any) -> Any:
        if isinstance(op_node, ast.In):
            if isinstance(left, pd.Series):
                return left.isin(right)
            return left in right
        if isinstance(op_node, ast.NotIn):
            if isinstance(left, pd.Series):
                return ~left.isin(right)
            return left not in right

        op_type = type(op_node)
        if op_type not in self._COMPARE_OPS:
            raise ExpressionError(f"不允许的比较运算符: {op_type.__name__}")
        return self._COMPARE_OPS[op_type](left, right)

    def _call(self, name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if name == "abs":
            return abs(args[0])
        if name == "round":
            return args[0].round(*args[1:], **kwargs) if isinstance(args[0], pd.Series) else round(*args, **kwargs)
        if name == "isna":
            return pd.isna(args[0])
        if name == "notna":
            return pd.notna(args[0])
        if name == "fillna":
            return args[0].fillna(args[1])
        if name == "contains":
            return args[0].astype(str).str.contains(args[1], regex=False, na=False)
        if name == "startswith":
            return args[0].astype(str).str.startswith(args[1], na=False)
        if name == "endswith":
            return args[0].astype(str).str.endswith(args[1], na=False)
        if name == "isin":
            return args[0].isin(args[1])
        raise ExpressionError(f"函数不在白名单中: {name}")
