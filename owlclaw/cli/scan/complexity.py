"""Complexity calculation utilities for cli-scan."""

from __future__ import annotations

import ast

from owlclaw.cli.scan.models import ComplexityLevel, ComplexityScore


class ComplexityCalculator:
    """Compute complexity metrics for function nodes."""

    def calculate(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> ComplexityScore:
        cyclomatic = self.cyclomatic_complexity(func_node)
        cognitive = self.cognitive_complexity(func_node)
        loc = self._loc(func_node)
        sloc = self._sloc(func_node, source)
        parameters = self._parameter_count(func_node.args)
        nesting_depth = self._max_nesting_depth(func_node)
        return ComplexityScore(
            cyclomatic=cyclomatic,
            cognitive=cognitive,
            loc=loc,
            sloc=sloc,
            parameters=parameters,
            nesting_depth=nesting_depth,
            level=self._level_from_cyclomatic(cyclomatic),
        )

    def cyclomatic_complexity(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        complexity = 1
        for node in self._iter_function_body(func_node):
            if isinstance(node, ast.If | ast.For | ast.AsyncFor | ast.While | ast.ExceptHandler | ast.IfExp):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                complexity += max(len(node.values) - 1, 0)
            elif isinstance(node, ast.comprehension):
                complexity += 1 + len(node.ifs)
            elif isinstance(node, ast.Match):
                complexity += len(node.cases)
        return complexity

    def cognitive_complexity(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        return self._cognitive_visit(func_node, 0)

    def _cognitive_visit(self, node: ast.AST, nesting: int) -> int:
        score = 0
        controls = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
                continue
            if isinstance(child, controls):
                score += 1 + nesting
                score += self._cognitive_visit(child, nesting + 1)
            else:
                if isinstance(child, ast.Break | ast.Continue):
                    score += 1
                score += self._cognitive_visit(child, nesting)
        return score

    def _loc(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        end_lineno = int(getattr(func_node, "end_lineno", getattr(func_node, "lineno", 1)) or 1)
        lineno = int(getattr(func_node, "lineno", 1) or 1)
        return max(end_lineno - lineno + 1, 1)

    def _sloc(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> int:
        lines = source.splitlines()
        lineno = int(getattr(func_node, "lineno", 1) or 1)
        end_lineno = int(getattr(func_node, "end_lineno", lineno) or lineno)
        code_lines = lines[lineno - 1 : end_lineno]
        sloc = 0
        for line in code_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                sloc += 1
        return sloc

    def _parameter_count(self, args: ast.arguments) -> int:
        count = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
        if args.vararg:
            count += 1
        if args.kwarg:
            count += 1
        return count

    def _max_nesting_depth(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        controls = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)

        def _depth(node: ast.AST, level: int) -> int:
            max_depth = level
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
                    continue
                next_level = level + 1 if isinstance(child, controls) else level
                child_depth = _depth(child, next_level)
                if child_depth > max_depth:
                    max_depth = child_depth
            return max_depth

        return _depth(func_node, 0)

    def _level_from_cyclomatic(self, value: int) -> ComplexityLevel:
        if value <= 5:
            return ComplexityLevel.SIMPLE
        if value <= 10:
            return ComplexityLevel.MEDIUM
        return ComplexityLevel.COMPLEX

    def _iter_function_body(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
        stack: list[ast.AST] = list(func_node.body)
        visited: list[ast.AST] = []
        while stack:
            current = stack.pop()
            visited.append(current)
            for child in ast.iter_child_nodes(current):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
                    continue
                stack.append(child)
        return visited
