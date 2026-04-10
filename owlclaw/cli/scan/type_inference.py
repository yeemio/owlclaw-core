"""Type inference utilities for cli-scan."""

from __future__ import annotations

import ast

from owlclaw.cli.scan.models import Confidence, InferredType, TypeSource


class TypeInferencer:
    """Infer missing parameter and return types from AST nodes."""

    def infer_parameter_type(self, param: ast.arg, default: ast.expr | None) -> InferredType:
        if param.annotation is not None:
            return InferredType(
                type_str=ast.unparse(param.annotation),
                confidence=Confidence.HIGH,
                source=TypeSource.ANNOTATION,
            )
        if default is None:
            return InferredType(type_str="unknown", confidence=Confidence.LOW, source=TypeSource.UNKNOWN)
        inferred = self._infer_expr_type(default)
        return InferredType(
            type_str=inferred.type_str,
            confidence=inferred.confidence,
            source=TypeSource.DEFAULT_VALUE,
        )

    def infer_return_type(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> InferredType:
        if func_node.returns is not None:
            return InferredType(
                type_str=ast.unparse(func_node.returns),
                confidence=Confidence.HIGH,
                source=TypeSource.ANNOTATION,
            )

        inferred_returns: list[InferredType] = []
        for node in self._iter_function_body(func_node):
            if isinstance(node, ast.Return):
                if node.value is None:
                    inferred_returns.append(
                        InferredType(type_str="None", confidence=Confidence.HIGH, source=TypeSource.RETURN_STMT)
                    )
                else:
                    inferred = self._infer_expr_type(node.value)
                    inferred_returns.append(
                        InferredType(
                            type_str=inferred.type_str,
                            confidence=inferred.confidence,
                            source=TypeSource.RETURN_STMT,
                        )
                    )

        if not inferred_returns:
            return InferredType(type_str="None", confidence=Confidence.HIGH, source=TypeSource.RETURN_STMT)

        unique_types = {item.type_str for item in inferred_returns}
        if len(unique_types) == 1:
            first = inferred_returns[0]
            return InferredType(type_str=first.type_str, confidence=first.confidence, source=TypeSource.RETURN_STMT)

        non_none_types = unique_types - {"None"}
        if len(non_none_types) == 1 and "None" in unique_types:
            value_type = non_none_types.pop()
            return InferredType(type_str=f"Optional[{value_type}]", confidence=Confidence.MEDIUM, source=TypeSource.RETURN_STMT)

        return InferredType(type_str="unknown", confidence=Confidence.LOW, source=TypeSource.RETURN_STMT)

    def _infer_expr_type(self, expr: ast.expr) -> InferredType:
        if isinstance(expr, ast.Constant):
            if expr.value is None:
                return InferredType(type_str="None", confidence=Confidence.HIGH, source=TypeSource.DEFAULT_VALUE)
            return InferredType(
                type_str=type(expr.value).__name__,
                confidence=Confidence.HIGH,
                source=TypeSource.DEFAULT_VALUE,
            )
        if (
            isinstance(expr, ast.UnaryOp)
            and isinstance(expr.operand, ast.Constant)
            and isinstance(expr.op, ast.USub | ast.UAdd)
            and isinstance(expr.operand.value, int | float)
        ):
            return InferredType(
                type_str=type(expr.operand.value).__name__,
                confidence=Confidence.HIGH,
                source=TypeSource.DEFAULT_VALUE,
            )
        if isinstance(expr, ast.List):
            return InferredType(type_str="list", confidence=Confidence.HIGH, source=TypeSource.DEFAULT_VALUE)
        if isinstance(expr, ast.Tuple):
            return InferredType(type_str="tuple", confidence=Confidence.HIGH, source=TypeSource.DEFAULT_VALUE)
        if isinstance(expr, ast.Set):
            return InferredType(type_str="set", confidence=Confidence.HIGH, source=TypeSource.DEFAULT_VALUE)
        if isinstance(expr, ast.Dict):
            return InferredType(type_str="dict", confidence=Confidence.HIGH, source=TypeSource.DEFAULT_VALUE)
        if isinstance(expr, ast.Call):
            return InferredType(type_str="unknown", confidence=Confidence.MEDIUM, source=TypeSource.DEFAULT_VALUE)
        return InferredType(type_str="unknown", confidence=Confidence.LOW, source=TypeSource.UNKNOWN)

    def _iter_function_body(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
        stack: list[ast.AST] = list(func_node.body)
        visited: list[ast.AST] = []
        while stack:
            current = stack.pop()
            visited.append(current)
            for child in ast.iter_child_nodes(current):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                    continue
                stack.append(child)
        return visited
