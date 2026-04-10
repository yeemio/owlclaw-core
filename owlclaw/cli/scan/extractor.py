"""Signature extraction utilities for cli-scan."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from owlclaw.cli.scan.models import DecoratorInfo, FunctionSignature, Parameter, ParameterKind


@dataclass(slots=True)
class SignatureExtractor:
    """Extract structured signatures from AST function nodes."""

    def extract_signature(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        module: str,
        qualname: str | None = None,
    ) -> FunctionSignature:
        return FunctionSignature(
            name=func_node.name,
            module=module,
            qualname=qualname or func_node.name,
            parameters=self._extract_parameters(func_node.args),
            return_type=ast.unparse(func_node.returns) if func_node.returns else None,
            decorators=self._extract_decorators(func_node.decorator_list),
            is_async=isinstance(func_node, ast.AsyncFunctionDef),
            is_generator=self._contains_yield(func_node),
            lineno=int(getattr(func_node, "lineno", 1) or 1),
        )

    def _extract_parameters(self, args: ast.arguments) -> list[Parameter]:
        parameters: list[Parameter] = []

        positional = [*args.posonlyargs, *args.args]
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)

        for arg_node, default_node in zip(positional, defaults, strict=True):
            parameters.append(
                Parameter(
                    name=arg_node.arg,
                    annotation=ast.unparse(arg_node.annotation) if arg_node.annotation else None,
                    default=ast.unparse(default_node) if default_node is not None else None,
                    kind=ParameterKind.POSITIONAL,
                )
            )

        if args.vararg:
            parameters.append(
                Parameter(
                    name=args.vararg.arg,
                    annotation=ast.unparse(args.vararg.annotation) if args.vararg.annotation else None,
                    default=None,
                    kind=ParameterKind.VAR_POSITIONAL,
                )
            )

        kw_defaults = list(args.kw_defaults)
        for kw_arg, default_node in zip(args.kwonlyargs, kw_defaults, strict=True):
            parameters.append(
                Parameter(
                    name=kw_arg.arg,
                    annotation=ast.unparse(kw_arg.annotation) if kw_arg.annotation else None,
                    default=ast.unparse(default_node) if default_node is not None else None,
                    kind=ParameterKind.KEYWORD,
                )
            )

        if args.kwarg:
            parameters.append(
                Parameter(
                    name=args.kwarg.arg,
                    annotation=ast.unparse(args.kwarg.annotation) if args.kwarg.annotation else None,
                    default=None,
                    kind=ParameterKind.VAR_KEYWORD,
                )
            )

        return parameters

    @staticmethod
    def _extract_decorators(decorator_nodes: list[ast.expr]) -> list[DecoratorInfo]:
        decorators: list[DecoratorInfo] = []
        for node in decorator_nodes:
            if isinstance(node, ast.Call):
                call_name = ast.unparse(node.func)
                call_args = [ast.unparse(item) for item in node.args]
                call_args.extend(
                    f"{item.arg}={ast.unparse(item.value)}" if item.arg else ast.unparse(item.value)
                    for item in node.keywords
                )
                decorators.append(DecoratorInfo(name=call_name, arguments=call_args))
            else:
                decorators.append(DecoratorInfo(name=ast.unparse(node), arguments=[]))
        return decorators

    def _contains_yield(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return any(isinstance(node, ast.Yield | ast.YieldFrom) for node in self._iter_function_body(func_node))

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
