from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from core.config import load_config
from app import Application
from modules.preflight import run_preflight


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance Alpha grid bot skeleton")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/strategy.example.toml"),
        help="Path to strategy TOML config",
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "paper", "live"),
        default=None,
        help="Override runtime mode from config.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of loop iterations. Use 0 for infinite mode.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override simulation seed from config.",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Skip waiting between iterations.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run live/paper connectivity and wallet preflight checks, then exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.mode is not None:
        config = replace(
            config,
            runtime=replace(
                config.runtime,
                mode=args.mode,
                dry_run=args.mode != "live",
            ),
        )
    if args.seed is not None:
        config = replace(config, simulation=replace(config.simulation, seed=args.seed))

    if args.preflight:
        return run_preflight(config)

    app = Application(config=config, no_sleep=args.no_sleep)
    return app.run(iterations=args.iterations)
