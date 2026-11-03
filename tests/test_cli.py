"""CLI surface checks."""

from __future__ import annotations

import pytest

from jobpilot.ui.cli import build_parser


def test_cli_exposes_init_and_removed_demo_seed():
    parser = build_parser()

    args = parser.parse_args(["init"])
    assert args.command == "init"
    assert callable(args.func)

    with pytest.raises(SystemExit):
        parser.parse_args(["seed"])
