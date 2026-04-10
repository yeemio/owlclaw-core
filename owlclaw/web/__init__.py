"""Web console backend package."""

from owlclaw.web.app import create_console_app
from owlclaw.web.mount import mount_console

__all__ = ["create_console_app", "mount_console"]

