"""Closed parser and immutable AST for EasyQC Formula expressions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lark import Lark, Token, Transformer, UnexpectedInput, v_args

from models.derived_formula import MAX_FORMULA_LENGTH
from models.formula_function import FORMULA_FUNCTION_NAMES


MAX_AST_NODES: Final = 256
MAX_AST_DEPTH: Final = 32

ALLOWED_FUNCTION_NAMES: Final = FORMULA_FUNCTION_NAMES


class FormulaError(ValueError):
    """Base class for user-visible formula failures."""


class FormulaSyntaxError(FormulaError):
    """Raised when a formula is outside the closed EasyQC grammar."""


class FormulaValidationError(FormulaError):
    """Raised when a parsed formula violates a semantic/resource contract."""


@dataclass(frozen=True, slots=True)
class FormulaNode:
    """One normalized AST node; never carries a Python callable."""

    kind: str
    value: object = None
    children: tuple["FormulaNode", ...] = ()
    line: int = 1
    column: int = 1


@dataclass(frozen=True, slots=True)
class ParsedFormula:
    """A closed AST plus deterministic resource/reference metadata."""

    root: FormulaNode
    referenced_columns: tuple[str, ...]
    node_count: int
    depth: int


_GRAMMAR = r"""
?start: EQUAL? expression

?expression: or_expression
?or_expression: and_expression (OR and_expression)*
?and_expression: not_expression (AND not_expression)*
?not_expression: NOT not_expression                       -> unary_not
               | comparison
?comparison: concatenation (COMPARISON concatenation)?
?concatenation: sum_expression (AMPERSAND sum_expression)*
?sum_expression: term (ADD_OPERATOR term)*
?term: unary_expression (MULTIPLY_OPERATOR unary_expression)*
?unary_expression: ADD_OPERATOR unary_expression          -> unary_numeric
                 | atom
?atom: function_call
     | column_reference
     | STRING                                             -> string
     | NUMBER                                             -> number
     | TRUE                                               -> true
     | FALSE                                              -> false
     | LPAR expression RPAR                               -> grouped

function_call: NAME LPAR [arguments] RPAR
arguments: expression (COMMA expression)*
column_reference: COLUMN

OR.3: /OR/i
AND.3: /AND/i
NOT.3: /NOT/i
TRUE.3: /TRUE/i
FALSE.3: /FALSE/i
NAME.1: /[A-Za-z_][A-Za-z0-9_]*/
COLUMN: /\[(?:[^\]\r\n]|\]\])+\]/
STRING: /"(?:[^"\r\n]|"")*"/
NUMBER: /(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/
COMPARISON: "<>"|"<="|">="|"="|"<"|">"
AMPERSAND: "&"
ADD_OPERATOR: "+"|"-"
MULTIPLY_OPERATOR: "*"|"/"
EQUAL: "="
LPAR: "("
RPAR: ")"
COMMA: ","

%import common.WS
%ignore WS
"""


def _position(item: Token | object) -> tuple[int, int]:
    return int(getattr(item, "line", 1)), int(getattr(item, "column", 1))


def _fold_binary(
    children: tuple[object, ...],
    *,
    token_type: str,
) -> FormulaNode:
    nodes: list[FormulaNode] = []
    operators: list[Token] = []
    for child in children:
        if isinstance(child, Token) and child.type == token_type:
            operators.append(child)
        elif isinstance(child, FormulaNode):
            nodes.append(child)
    if not nodes:
        raise FormulaValidationError("公式缺少运算对象")
    result = nodes[0]
    for operator, right in zip(operators, nodes[1:]):
        line, column = _position(operator)
        result = FormulaNode(
            "binary",
            str(operator).upper(),
            (result, right),
            line,
            column,
        )
    return result


@v_args(inline=True)
class _FormulaAstBuilder(Transformer):
    def string(self, token: Token) -> FormulaNode:
        line, column = _position(token)
        return FormulaNode(
            "literal",
            str(token)[1:-1].replace('""', '"'),
            line=line,
            column=column,
        )

    def number(self, token: Token) -> FormulaNode:
        line, column = _position(token)
        text = str(token)
        value: int | float = (
            float(text) if any(char in text for char in ".eE") else int(text)
        )
        return FormulaNode("literal", value, line=line, column=column)

    def true(self, token: Token) -> FormulaNode:
        line, column = _position(token)
        return FormulaNode("literal", True, line=line, column=column)

    def false(self, token: Token) -> FormulaNode:
        line, column = _position(token)
        return FormulaNode("literal", False, line=line, column=column)

    def column_reference(self, token: Token) -> FormulaNode:
        line, column = _position(token)
        name = str(token)[1:-1].replace("]]", "]")
        return FormulaNode("column", name, line=line, column=column)

    def arguments(self, *children: object) -> tuple[FormulaNode, ...]:
        return tuple(child for child in children if isinstance(child, FormulaNode))

    def function_call(self, *children: object) -> FormulaNode:
        name = next(
            child
            for child in children
            if isinstance(child, Token) and child.type == "NAME"
        )
        args = next(
            (child for child in children if isinstance(child, tuple)),
            (),
        )
        line, column = _position(name)
        return FormulaNode(
            "call",
            str(name).upper(),
            tuple(args),
            line,
            column,
        )

    def grouped(self, *children: object) -> FormulaNode:
        return next(child for child in children if isinstance(child, FormulaNode))

    def unary_not(self, *children: object) -> FormulaNode:
        token = next(child for child in children if isinstance(child, Token))
        operand = next(child for child in children if isinstance(child, FormulaNode))
        line, column = _position(token)
        return FormulaNode("unary", "NOT", (operand,), line, column)

    def unary_numeric(self, *children: object) -> FormulaNode:
        token = next(child for child in children if isinstance(child, Token))
        operand = next(child for child in children if isinstance(child, FormulaNode))
        line, column = _position(token)
        return FormulaNode("unary", str(token), (operand,), line, column)

    def comparison(self, *children: object) -> FormulaNode:
        return _fold_binary(children, token_type="COMPARISON")

    def concatenation(self, *children: object) -> FormulaNode:
        return _fold_binary(children, token_type="AMPERSAND")

    def sum_expression(self, *children: object) -> FormulaNode:
        return _fold_binary(children, token_type="ADD_OPERATOR")

    def term(self, *children: object) -> FormulaNode:
        return _fold_binary(children, token_type="MULTIPLY_OPERATOR")

    def and_expression(self, *children: object) -> FormulaNode:
        return _fold_binary(children, token_type="AND")

    def or_expression(self, *children: object) -> FormulaNode:
        return _fold_binary(children, token_type="OR")

    def start(self, *children: object) -> FormulaNode:
        return next(child for child in children if isinstance(child, FormulaNode))


class FormulaParser:
    """Parse one EasyQC Formula into a resource-bounded closed AST."""

    _parser = Lark(
        _GRAMMAR,
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
    )

    def parse(self, expression: str) -> ParsedFormula:
        if not isinstance(expression, str):
            raise TypeError("公式必须是文本")
        normalized = expression.strip()
        if not normalized:
            raise FormulaSyntaxError("公式不能为空")
        if len(normalized) > MAX_FORMULA_LENGTH:
            raise FormulaValidationError(
                f"公式不能超过 {MAX_FORMULA_LENGTH:,} 个字符"
            )
        try:
            tree = self._parser.parse(normalized)
            root = _FormulaAstBuilder().transform(tree)
        except UnexpectedInput as exc:
            raise FormulaSyntaxError(
                f"公式语法错误（第 {exc.line} 行，第 {exc.column} 列）"
            ) from exc
        except FormulaError:
            raise
        except Exception as exc:
            raise FormulaSyntaxError(f"公式解析失败: {exc}") from exc
        if not isinstance(root, FormulaNode):
            raise FormulaSyntaxError("公式没有产生有效表达式")

        node_count, depth, columns, functions = self._inspect(root)
        unknown_functions = sorted(functions - ALLOWED_FUNCTION_NAMES)
        if unknown_functions:
            raise FormulaValidationError(f"未知函数: {unknown_functions[0]}")
        if node_count > MAX_AST_NODES:
            raise FormulaValidationError(
                f"公式节点不能超过 {MAX_AST_NODES} 个"
            )
        if depth > MAX_AST_DEPTH:
            raise FormulaValidationError(
                f"公式嵌套不能超过 {MAX_AST_DEPTH} 层"
            )
        return ParsedFormula(root, tuple(columns), node_count, depth)

    @staticmethod
    def _inspect(
        root: FormulaNode,
    ) -> tuple[int, int, list[str], set[str]]:
        node_count = 0
        max_depth = 0
        columns: list[str] = []
        seen_columns: set[str] = set()
        functions: set[str] = set()
        stack: list[tuple[FormulaNode, int]] = [(root, 1)]
        while stack:
            node, depth = stack.pop()
            node_count += 1
            max_depth = max(max_depth, depth)
            if node.kind == "column":
                name = str(node.value)
                if name not in seen_columns:
                    seen_columns.add(name)
                    columns.append(name)
            elif node.kind == "call":
                functions.add(str(node.value))
            stack.extend(
                (child, depth + 1)
                for child in reversed(node.children)
            )
        return node_count, max_depth, columns, functions


__all__ = [
    "ALLOWED_FUNCTION_NAMES",
    "FormulaError",
    "FormulaNode",
    "FormulaParser",
    "FormulaSyntaxError",
    "FormulaValidationError",
    "MAX_AST_DEPTH",
    "MAX_AST_NODES",
    "ParsedFormula",
]
