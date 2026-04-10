"""Dependency analysis utilities for cli-scan."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from owlclaw.cli.scan.models import ImportInfo, ImportType


@dataclass(slots=True)
class Dependency:
    source: str
    target: str
    import_type: ImportType
    lineno: int = 0


@dataclass(slots=True)
class DependencyGraph:
    nodes: list[str] = field(default_factory=list)
    edges: list[Dependency] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)


class CyclicDependencyDetector:
    """Detect cycles in dependency graphs using Tarjan SCC."""

    def detect(self, nodes: list[str], edges: list[Dependency]) -> list[list[str]]:
        adjacency: dict[str, list[str]] = {node: [] for node in nodes}
        for edge in edges:
            adjacency.setdefault(edge.source, []).append(edge.target)
            adjacency.setdefault(edge.target, [])

        index = 0
        indices: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        cycles: list[list[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlink[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for target in adjacency.get(node, []):
                if target not in indices:
                    strongconnect(target)
                    lowlink[node] = min(lowlink[node], lowlink[target])
                elif target in on_stack:
                    lowlink[node] = min(lowlink[node], indices[target])

            if lowlink[node] == indices[node]:
                component: list[str] = []
                while stack:
                    top = stack.pop()
                    on_stack.remove(top)
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1:
                    cycles.append(component)
                elif component:
                    single = component[0]
                    if single in adjacency and single in adjacency[single]:
                        cycles.append(component)

        for node in adjacency:
            if node not in indices:
                strongconnect(node)

        return cycles


class DependencyAnalyzer:
    """Extract imports, function calls, and local dependency graph."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else None
        self.detector = CyclicDependencyDetector()

    def analyze(self, tree: ast.Module, module: str = "") -> DependencyGraph:
        imports = self.extract_imports(tree)
        nodes, node_by_base = self._collect_nodes(tree, module)

        edges: list[Dependency] = []
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                source = node_by_base.get(stmt.name, stmt.name)
                edges.extend(self._collect_local_edges(source, stmt, node_by_base))
            elif isinstance(stmt, ast.ClassDef):
                for child in stmt.body:
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                        source = f"{module}.{stmt.name}.{child.name}" if module else f"{stmt.name}.{child.name}"
                        edges.extend(self._collect_local_edges(source, child, node_by_base))

        cycles = self.detector.detect(nodes, edges)
        return DependencyGraph(nodes=nodes, edges=edges, cycles=cycles, imports=imports)

    def extract_imports(self, tree: ast.Module) -> list[ImportInfo]:
        imports: list[ImportInfo] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    imports.append(
                        ImportInfo(
                            module=module,
                            names=[alias.asname or alias.name],
                            import_type=self.classify_import(module),
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.asname or alias.name for alias in node.names]
                import_type = ImportType.LOCAL if node.level > 0 else self.classify_import(module)
                imports.append(ImportInfo(module=module, names=names, import_type=import_type))
        return imports

    def extract_calls(self, func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        calls: list[str] = []
        for node in self._iter_function_body(func_node):
            if isinstance(node, ast.Call):
                calls.append(ast.unparse(node.func))
        return calls

    def classify_import(self, module: str) -> ImportType:
        root = module.split(".", 1)[0] if module else ""
        if root and root in getattr(sys, "stdlib_module_names", set()):
            return ImportType.STDLIB
        if self.project_root and root:
            package_path = self.project_root / root
            module_path = self.project_root / f"{root}.py"
            if package_path.exists() or module_path.exists():
                return ImportType.LOCAL
        if module:
            return ImportType.THIRD_PARTY
        return ImportType.UNKNOWN

    def _collect_nodes(self, tree: ast.Module, module: str) -> tuple[list[str], dict[str, str]]:
        nodes: list[str] = []
        by_base: dict[str, str] = {}
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{module}.{stmt.name}" if module else stmt.name
                nodes.append(qualname)
                by_base[stmt.name] = qualname
            elif isinstance(stmt, ast.ClassDef):
                for child in stmt.body:
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                        qualname = f"{module}.{stmt.name}.{child.name}" if module else f"{stmt.name}.{child.name}"
                        nodes.append(qualname)
                        by_base[child.name] = qualname
        return nodes, by_base

    def _collect_local_edges(
        self,
        source: str,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        node_by_base: dict[str, str],
    ) -> list[Dependency]:
        edges: list[Dependency] = []
        for call in self.extract_calls(func_node):
            base = call.split(".")[-1]
            target = node_by_base.get(base)
            if target:
                edges.append(
                    Dependency(
                        source=source,
                        target=target,
                        import_type=ImportType.LOCAL,
                        lineno=int(getattr(func_node, "lineno", 0) or 0),
                    )
                )
        return edges

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
