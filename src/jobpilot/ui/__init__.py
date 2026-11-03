"""Flask UI, Jinja templates and the ``jobpilot`` command line entry point."""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(test_config: dict | None = None):
    from jobpilot.ui.app import create_app as factory

    return factory(test_config)
