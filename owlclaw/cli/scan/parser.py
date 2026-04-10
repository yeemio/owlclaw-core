"""AST parser utilities for cli-scan."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ASTParser:
    """Parse Python files and extract top-level symbols."""

    def __init__(self) -> None:
        self.errors: list[dict[str, str | int]] = []

    def parse_file(self, file_path: Path) -> ast.Module | None:
        path = Path(file_path)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._record_error(path, "io_error", str(exc), 0, 0)
            return None
        try:
            return ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            self._record_error(
                path,
                "syntax_error",
                str(exc.msg or "invalid syntax"),
                int(exc.lineno or 0),
                int(exc.offset or 0),
            )
            return None

    @staticmethod
    def extract_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        return [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]

    @staticmethod
    def extract_classes(tree: ast.Module) -> list[ast.ClassDef]:
        return [node for node in tree.body if isinstance(node, ast.ClassDef)]

    @staticmethod
    def extract_methods(class_node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
        return [node for node in class_node.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]

    def _record_error(
        self,
        path: Path,
        error_type: str,
        message: str,
        lineno: int,
        offset: int,
    ) -> None:
        self.errors.append(
            {
                "file_path": str(path),
                "error_type": error_type,
                "message": message,
                "lineno": lineno,
                "offset": offset,
            }
        )
        logger.warning("cli-scan parse error file=%s type=%s line=%s: %s", path, error_type, lineno, message)
