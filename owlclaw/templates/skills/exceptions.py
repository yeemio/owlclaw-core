"""Custom exceptions for the SKILL.md template library."""


class TemplateError(Exception):
    """Base exception for template library errors."""


class TemplateNotFoundError(TemplateError):
    """Raised when a template ID is not found in the registry."""


class MissingParameterError(TemplateError):
    """Raised when required template parameters are missing."""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


class ParameterTypeError(TemplateError):
    """Raised when a parameter value has the wrong type."""

    def __init__(
        self,
        message: str,
        param_name: str | None = None,
        expected: str | None = None,
        got: str | None = None,
    ) -> None:
        super().__init__(message)
        self.param_name = param_name
        self.expected = expected
        self.got = got


class ParameterValueError(TemplateError):
    """Raised when a parameter value is invalid (e.g. not in choices)."""

    def __init__(
        self,
        message: str,
        param_name: str | None = None,
        value: str | None = None,
        choices: list | None = None,
    ) -> None:
        super().__init__(message)
        self.param_name = param_name
        self.value = value
        self.choices = choices or []


class TemplateRenderError(TemplateError):
    """Raised when template rendering fails (e.g. Jinja2 error)."""
