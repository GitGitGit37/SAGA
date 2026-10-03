"""Run the inference pipeline against the database.

    python -m app.inference.run                    # every asset
    python -m app.inference.run --asset EX-320-A   # one asset
    python -m app.inference.run --rules            # skip Claude, engine only
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset, Inference
from app.db.session import SessionLocal
from app.evidence.base_rates import lookup_base_rate
from app.evidence.context import DEFAULT_WINDOW_DAYS, load_context
from app.evidence.engine import analyze
from app.feedback.reconcile import reconcile_feedback
from app.inference.categorize import categorize_observations
from app.inference.pipeline import build_drafts
from app.inference.proposer import ClaudeProposer, Proposer, RuleProposer
from app.inference.store import save_draft
from app.llm.client import StructuredLLM, get_llm


def run_for_asset(db: Session, asset: Asset, llm: StructuredLLM | None, *,
                  as_of: datetime | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
                  created_by: str = "pipeline", change_reason: str | None = None,
                  actor: str = "system") -> list[Inference]:
    """Categorise new observations, analyse, propose, verify, and store new versions.
    Caller owns the transaction."""
    categorize_observations(db, asset, llm)
    ctx = load_context(db, asset, as_of=as_of, window_days=window_days)
    analysis = analyze(ctx)
    proposer: Proposer = ClaudeProposer(llm) if llm is not None else RuleProposer()

    def base_rate(category: str, hypothesis: str):
        return lookup_base_rate(db, category=category, hypothesis=hypothesis, asset_type=asset.asset_type)

    stored = []
    for draft in build_drafts(analysis, proposer, base_rate):
        inf = save_draft(db, asset, draft, created_by=created_by, change_reason=change_reason, actor=actor)
        if inf is not None:
            stored.append(inf)
    if stored:
        reconcile_feedback(db, asset)
    return stored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--asset", help="asset tag, e.g. EX-320-A (default: all)")
    parser.add_argument("--rules", action="store_true", help="don't call Claude")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    llm = None if args.rules else get_llm()
    if llm is None and not args.rules:
        print("ANTHROPIC_API_KEY not set: using the rule-based proposer.")

    with SessionLocal.begin() as db:
        query = select(Asset).order_by(Asset.asset_tag)
        if args.asset:
            query = query.where(Asset.asset_tag == args.asset)
        assets = db.scalars(query).all()
        if not assets:
            raise SystemExit(f"no asset found{f' with tag {args.asset}' if args.asset else ''}")
        for asset in assets:
            stored = run_for_asset(db, asset, llm, window_days=args.window_days)
            for inf in stored:
                flag = " [SAFETY]" if inf.is_safety_critical else ""
                print(f"{asset.asset_tag:9} v{inf.version} {inf.subsystem:15} {inf.hypothesis:18} "
                      f"{inf.final_confidence:.2f}{flag}  {inf.title}")
            if not stored:
                print(f"{asset.asset_tag:9} no new or changed inferences")


if __name__ == "__main__":
    main()
