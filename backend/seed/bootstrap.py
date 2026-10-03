"""First-run setup: if the database is empty, load the synthetic fleet, run the pipeline,
and build the recall index. Safe to run on every start.

    python -m seed.bootstrap                 # rules-based pipeline (fast, free)
    SEED_WITH_CLAUDE=1 python -m seed.bootstrap   # use Claude for the initial inferences
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.db.models import Asset
from app.db.session import SessionLocal
from app.inference.run import run_for_asset
from app.llm.client import get_llm
from app.recall.embeddings import get_embedder, sync_embeddings
from seed import generate
from seed.load import load


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with SessionLocal() as db:
        if db.scalar(select(Asset.id).limit(1)) is not None:
            print("bootstrap: database already seeded")
            return

    load(generate.OUT_DIR, reset=True)
    llm = get_llm() if os.environ.get("SEED_WITH_CLAUDE") == "1" else None
    with SessionLocal.begin() as db:
        for asset in db.scalars(select(Asset).order_by(Asset.asset_tag)):
            run_for_asset(db, asset, llm)
        n = sync_embeddings(db, get_embedder())
    print(f"bootstrap: seeded fleet, ran pipeline ({'claude' if llm else 'rules'}), embedded {n} memories")


if __name__ == "__main__":
    main()
