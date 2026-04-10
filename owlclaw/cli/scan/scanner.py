"""Project scanning orchestration for cli-scan."""

from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from owlclaw.cli.scan.complexity import ComplexityCalculator
from owlclaw.cli.scan.dependency import DependencyAnalyzer
from owlclaw.cli.scan.discovery import FileDiscovery
from owlclaw.cli.scan.docstring import DocstringParser
from owlclaw.cli.scan.extractor import SignatureExtractor
from owlclaw.cli.scan.models import (
    ComplexityScore,
    FileScanResult,
    FunctionScanResult,
    ParsedDocstring,
    ScanMetadata,
    ScanResult,
)
from owlclaw.cli.scan.parser import ASTParser
from owlclaw.cli.scan.type_inference import TypeInferencer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanConfig:
    project_path: Path
    include_patterns: list[str] = field(default_factory=lambda: ["*.py"])
    exclude_patterns: list[str] = field(default_factory=list)
    incremental: bool = False
    workers: int = 1
    extract_docstrings: bool = True
    calculate_complexity: bool = True
    analyze_dependencies: bool = True
    min_complexity_threshold: int = 0


class ProjectScanner:
    """Scan a Python project and aggregate file-level scan results."""

    def __init__(self, config: ScanConfig) -> None:
        self.config = config
        self.file_discovery = FileDiscovery(config.include_patterns, config.exclude_patterns)
        self.parser = ASTParser()
        self.signature_extractor = SignatureExtractor()
        self.docstring_parser = DocstringParser()
        self.complexity_calculator = ComplexityCalculator()
        self.dependency_analyzer = DependencyAnalyzer(project_root=config.project_path)
        self.type_inferencer = TypeInferencer()

    def scan(self) -> ScanResult:
        start = time.perf_counter()
        files = self.file_discovery.discover(self.config.project_path)
        output: dict[str, FileScanResult] = {}
        failed = 0
        for file_path in files:
            result = self._scan_file(file_path)
            key = str(file_path.relative_to(self.config.project_path)).replace("\\", "/")
            output[key] = result
            if result.errors:
                failed += 1
        duration = time.perf_counter() - start
        metadata = ScanMetadata(
            project_path=str(self.config.project_path),
            scanned_files=len(files),
            failed_files=failed,
            scan_time_seconds=duration,
        )
        return ScanResult(metadata=metadata, files=output)

    def _scan_file(self, file_path: Path) -> FileScanResult:
        module = self._module_from_path(file_path)
        tree = self.parser.parse_file(file_path)
        if tree is None:
            message = "parse failed"
            if self.parser.errors:
                latest = self.parser.errors[-1]
                message = (
                    f"{latest.get('error_type', 'error')}: {latest.get('message', 'parse failed')} "
                    f"(line={latest.get('lineno', 0)}, offset={latest.get('offset', 0)})"
                )
            logger.warning("scan failed file=%s reason=%s", file_path, message)
            return FileScanResult(file_path=str(file_path), errors=[message])

        imports = self.dependency_analyzer.extract_imports(tree) if self.config.analyze_dependencies else []
        functions: list[FunctionScanResult] = []
        errors: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                try:
                    functions.extend(self._scan_function(node, module))
                except Exception as exc:  # pragma: no cover - defensive boundary
                    errors.append(f"function_scan_error:{node.name}:{exc}")
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                        qualname = f"{node.name}.{member.name}"
                        try:
                            functions.extend(self._scan_function(member, module, qualname=qualname))
                        except Exception as exc:  # pragma: no cover - defensive boundary
                            errors.append(f"method_scan_error:{qualname}:{exc}")

        return FileScanResult(file_path=str(file_path), functions=functions, imports=imports, errors=errors)

    def _scan_function(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        module: str,
        qualname: str | None = None,
    ) -> list[FunctionScanResult]:
        signature = self.signature_extractor.extract_signature(
            func_node,
            module=module,
            qualname=f"{module}.{qualname}" if qualname else f"{module}.{func_node.name}",
        )

        docstring = (
            self.docstring_parser.parse(ast.get_docstring(func_node, clean=False) or "")
            if self.config.extract_docstrings
            else ParsedDocstring()
        )
        complexity = (
            self.complexity_calculator.calculate(func_node, ast.unparse(func_node))
            if self.config.calculate_complexity
            else ComplexityScore()
        )
        if self.config.calculate_complexity and complexity.cyclomatic < self.config.min_complexity_threshold:
            return []

        inferred_types = {}
        for arg, default in self._iter_arg_defaults(func_node.args):
            inferred_types[arg.arg] = self.type_inferencer.infer_parameter_type(arg, default)
        inferred_types["return"] = self.type_inferencer.infer_return_type(func_node)

        dependencies = self.dependency_analyzer.extract_calls(func_node) if self.config.analyze_dependencies else []

        return [
            FunctionScanResult(
                signature=signature,
                docstring=docstring,
                complexity=complexity,
                inferred_types=inferred_types,
                dependencies=dependencies,
            )
        ]

    def _module_from_path(self, file_path: Path) -> str:
        rel = file_path.relative_to(self.config.project_path).with_suffix("")
        return ".".join(rel.parts)

    def _iter_arg_defaults(self, args: ast.arguments) -> list[tuple[ast.arg, ast.expr | None]]:
        positional = [*args.posonlyargs, *args.args]
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        pairs: list[tuple[ast.arg, ast.expr | None]] = list(zip(positional, defaults, strict=True))
        pairs.extend(zip(args.kwonlyargs, args.kw_defaults, strict=True))
        return pairs
