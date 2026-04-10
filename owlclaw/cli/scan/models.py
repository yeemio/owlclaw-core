"""Serializable data models for cli-scan."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class ParameterKind(str, Enum):
    POSITIONAL = "positional"
    KEYWORD = "keyword"
    VAR_POSITIONAL = "var_positional"
    VAR_KEYWORD = "var_keyword"


class DocstringStyle(str, Enum):
    GOOGLE = "google"
    NUMPY = "numpy"
    RESTRUCTUREDTEXT = "restructuredtext"
    UNKNOWN = "unknown"


class ComplexityLevel(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ImportType(str, Enum):
    STDLIB = "stdlib"
    THIRD_PARTY = "third_party"
    LOCAL = "local"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TypeSource(str, Enum):
    ANNOTATION = "annotation"
    DEFAULT_VALUE = "default_value"
    ASSIGNMENT = "assignment"
    RETURN_STMT = "return_stmt"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class Parameter:
    name: str
    annotation: str | None = None
    default: str | None = None
    kind: ParameterKind = ParameterKind.POSITIONAL


@dataclass(slots=True)
class DecoratorInfo:
    name: str
    arguments: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FunctionSignature:
    name: str
    module: str
    qualname: str
    parameters: list[Parameter] = field(default_factory=list)
    return_type: str | None = None
    decorators: list[DecoratorInfo] = field(default_factory=list)
    is_async: bool = False
    is_generator: bool = False
    lineno: int = 1


@dataclass(slots=True)
class ParsedDocstring:
    summary: str = ""
    description: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    returns: str | None = None
    raises: dict[str, str] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    style: DocstringStyle = DocstringStyle.UNKNOWN
    raw: str = ""


@dataclass(slots=True)
class ComplexityScore:
    cyclomatic: int = 1
    cognitive: int = 0
    loc: int = 0
    sloc: int = 0
    parameters: int = 0
    nesting_depth: int = 0
    level: ComplexityLevel = ComplexityLevel.SIMPLE


@dataclass(slots=True)
class InferredType:
    type_str: str
    confidence: Confidence = Confidence.LOW
    source: TypeSource = TypeSource.UNKNOWN


@dataclass(slots=True)
class ImportInfo:
    module: str
    names: list[str] = field(default_factory=list)
    import_type: ImportType = ImportType.UNKNOWN


@dataclass(slots=True)
class FunctionScanResult:
    signature: FunctionSignature
    docstring: ParsedDocstring = field(default_factory=ParsedDocstring)
    complexity: ComplexityScore = field(default_factory=ComplexityScore)
    inferred_types: dict[str, InferredType] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FileScanResult:
    file_path: str
    functions: list[FunctionScanResult] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScanMetadata:
    project_path: str
    scanned_files: int = 0
    failed_files: int = 0
    scan_time_seconds: float = 0.0


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


@dataclass(slots=True)
class ScanResult:
    metadata: ScanMetadata
    files: dict[str, FileScanResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _serialize(asdict(self)))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanResult:
        metadata_data = data.get("metadata", {})
        metadata = ScanMetadata(
            project_path=str(metadata_data.get("project_path", "")),
            scanned_files=int(metadata_data.get("scanned_files", 0)),
            failed_files=int(metadata_data.get("failed_files", 0)),
            scan_time_seconds=float(metadata_data.get("scan_time_seconds", 0.0)),
        )

        files_data = data.get("files", {})
        files: dict[str, FileScanResult] = {}
        for key, value in files_data.items():
            signatures = []
            for item in value.get("functions", []):
                params = [
                    Parameter(
                        name=str(p.get("name", "")),
                        annotation=p.get("annotation"),
                        default=p.get("default"),
                        kind=ParameterKind(p.get("kind", ParameterKind.POSITIONAL.value)),
                    )
                    for p in item.get("signature", {}).get("parameters", [])
                ]
                decorators = [
                    DecoratorInfo(name=str(d.get("name", "")), arguments=[str(a) for a in d.get("arguments", [])])
                    for d in item.get("signature", {}).get("decorators", [])
                ]
                signature = FunctionSignature(
                    name=str(item.get("signature", {}).get("name", "")),
                    module=str(item.get("signature", {}).get("module", "")),
                    qualname=str(item.get("signature", {}).get("qualname", "")),
                    parameters=params,
                    return_type=item.get("signature", {}).get("return_type"),
                    decorators=decorators,
                    is_async=bool(item.get("signature", {}).get("is_async", False)),
                    is_generator=bool(item.get("signature", {}).get("is_generator", False)),
                    lineno=int(item.get("signature", {}).get("lineno", 1)),
                )
                doc = item.get("docstring", {})
                parsed_doc = ParsedDocstring(
                    summary=str(doc.get("summary", "")),
                    description=str(doc.get("description", "")),
                    parameters={str(k): str(v) for k, v in doc.get("parameters", {}).items()},
                    returns=doc.get("returns"),
                    raises={str(k): str(v) for k, v in doc.get("raises", {}).items()},
                    examples=[str(e) for e in doc.get("examples", [])],
                    style=DocstringStyle(doc.get("style", DocstringStyle.UNKNOWN.value)),
                    raw=str(doc.get("raw", "")),
                )
                complexity = item.get("complexity", {})
                complexity_score = ComplexityScore(
                    cyclomatic=int(complexity.get("cyclomatic", 1)),
                    cognitive=int(complexity.get("cognitive", 0)),
                    loc=int(complexity.get("loc", 0)),
                    sloc=int(complexity.get("sloc", 0)),
                    parameters=int(complexity.get("parameters", 0)),
                    nesting_depth=int(complexity.get("nesting_depth", 0)),
                    level=ComplexityLevel(complexity.get("level", ComplexityLevel.SIMPLE.value)),
                )
                inferred = {
                    str(name): InferredType(
                        type_str=str(payload.get("type_str", "unknown")),
                        confidence=Confidence(payload.get("confidence", Confidence.LOW.value)),
                        source=TypeSource(payload.get("source", TypeSource.UNKNOWN.value)),
                    )
                    for name, payload in item.get("inferred_types", {}).items()
                }
                signatures.append(
                    FunctionScanResult(
                        signature=signature,
                        docstring=parsed_doc,
                        complexity=complexity_score,
                        inferred_types=inferred,
                        dependencies=[str(dep) for dep in item.get("dependencies", [])],
                    )
                )

            imports = [
                ImportInfo(
                    module=str(imp.get("module", "")),
                    names=[str(n) for n in imp.get("names", [])],
                    import_type=ImportType(imp.get("import_type", ImportType.UNKNOWN.value)),
                )
                for imp in value.get("imports", [])
            ]
            files[str(key)] = FileScanResult(
                file_path=str(value.get("file_path", key)),
                functions=signatures,
                imports=imports,
                errors=[str(e) for e in value.get("errors", [])],
            )
        return cls(metadata=metadata, files=files)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> ScanResult:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("json payload must deserialize to object")
        return cls.from_dict(data)

    def to_yaml(self) -> str:
        return cast(str, yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=True))

    @classmethod
    def from_yaml(cls, payload: str) -> ScanResult:
        data = yaml.safe_load(payload)
        if not isinstance(data, dict):
            raise ValueError("yaml payload must deserialize to object")
        return cls.from_dict(data)
