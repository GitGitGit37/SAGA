"""Load generated seed data into Postgres.

    python -m seed.load            # generate (if needed) and load seed/data/
    python -m seed.load --reset    # wipe all tables first

Files in seed/data/demo_holdout/ are NOT loaded; upload them through the
Ingest page during the demo.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.config import scoring
from app.db.models import Asset, User
from app.db.session import SessionLocal
from app.ingest.parsers import parse_fault_codes_json, parse_notes_json, parse_telematics_csv
from app.ingest.store import store_observations
from seed import generate

TABLES = ["audit_log", "embeddings", "feedback", "evidence_items", "inferences",
          "raw_observations", "assets", "users"]


def load(data_dir: Path, reset: bool = False) -> None:
    if not (data_dir / "assets.json").exists():
        generate.write(data_dir)

    with SessionLocal.begin() as db:
        if reset:
            db.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        elif db.query(Asset).count():
            raise SystemExit("Database already has assets. Re-run with --reset to reload.")

        reliability = scoring()["feedback"]["initial_reliability"]
        db.add_all(User(name=u["name"], role=u["role"], reliability=reliability)
                   for u in _read_json(data_dir / "users.json"))

        for a in _read_json(data_dir / "assets.json"):
            db.add(Asset(
                asset_tag=a["asset_tag"], name=a["name"], asset_type=a["asset_type"],
                model=a["model"], serial_number=a["serial_number"], site=a["site"],
                commissioned_on=datetime.fromisoformat(a["commissioned_on"] + "T00:00:00+00:00"),
                attributes={"start_engine_hours": a["start_hours"]},
            ))
        db.flush()

        counts = {}
        for name, parser in (("telematics.csv", parse_telematics_csv),
                             ("fault_codes.json", parse_fault_codes_json),
                             ("notes.json", parse_notes_json)):
            records = parser((data_dir / name).read_bytes())
            counts[name] = len(store_observations(db, records, source=f"seed/{name}"))

    print(f"Loaded {len(_read_json(data_dir / 'assets.json'))} assets and observations: {counts}")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=generate.OUT_DIR)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    load(args.data, args.reset)


if __name__ == "__main__":
    main()
