"""Shared `--provider` plumbing for the perception evals.

Holds one rule that matters more than the plumbing: **the oracle provider is
refused by every eval.** The oracle replays `data/ground_truth.json`, so
scoring it against `data/ground_truth.json` is circular — it would always
report 100% and that number would mean nothing. Refusing it by name is
cheaper than trusting everyone downstream to notice.
"""

import argparse
import os

from engine.perception.providers import (
    DEFAULT_PROVIDER,
    ENV_VAR,
    available_providers,
    resolve_provider_name,
)

REFUSED_PROVIDERS = {"oracle"}

REFUSAL_MESSAGE = (
    "REFUSED: provider 'oracle' cannot be evaluated.\n"
    "  The oracle provider replays data/ground_truth.json verbatim, so scoring it "
    "against data/ground_truth.json is circular - it would report 100% by "
    "construction and measure nothing.\n"
    "  The oracle exists for scripted demo beats only. Evaluate 'heuristic' (the "
    "free offline rules baseline) or an LLM provider instead."
)


class CircularEvalRefused(ValueError):
    """Raised when an eval is pointed at a ground-truth replay provider."""


def check_provider(name: str) -> str:
    """Resolve a provider name for eval use, refusing circular ones."""
    resolved = resolve_provider_name(name)
    if resolved in REFUSED_PROVIDERS:
        raise CircularEvalRefused(REFUSAL_MESSAGE)
    return resolved


def add_provider_arg(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            f"perception provider to score (known: {', '.join(available_providers())}; "
            f"default: ${ENV_VAR} or '{DEFAULT_PROVIDER}'). "
            f"'oracle' is refused — it replays the labels being scored."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass .cache/perception and recompute every call",
    )
    return parser


def apply_cache_flag(no_cache: bool) -> None:
    if no_cache:
        os.environ["PK_PERCEPTION_CACHE"] = "0"
