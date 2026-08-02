#!/usr/bin/env python3
"""Re-examines the sales the last round declared, and records what it changed.

Runs after a full round rather than inside a brand sweep: the closure rule
needs a dealer's whole stock, and a dealer sells across brands swept hours
apart.

`--dry-run` reports without writing, which is how the thresholds were checked
against live data before this was first let loose on the database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, "/app/src")

from autosmart24.db.models import ScrapeEvent, ScrapeRun  # noqa: E402
from autosmart24.removal import reclassify_removals, resolve_quarantine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=float, default=None,
                    help="window to re-examine; defaults to the last round's start")
    args = ap.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        if args.hours is not None:
            since = dt.datetime.utcnow() - dt.timedelta(hours=args.hours)
        else:
            # The round's own start, so the window matches exactly what was
            # judged rather than a wall-clock guess that drifts when a round
            # runs long.
            since = session.execute(
                select(ScrapeRun.started_at).order_by(ScrapeRun.started_at.desc()).limit(26)
            ).scalars().all()
            since = min(since) if since else dt.datetime.utcnow() - dt.timedelta(hours=12)

        print(f"riesamino le vendite dichiarate dopo {since:%d/%m %H:%M} UTC", flush=True)
        # Resolved before this round's holds are added, so the two never mix:
        # what is confirmed here has been absent for the full window, not for
        # the few minutes since reclassification put it there.
        confirmed = resolve_quarantine(session, now=dt.datetime.utcnow())
        changed = reclassify_removals(session, since=since)

        total = sum(changed.values())
        for reason, n in changed.most_common():
            print(f"  {reason:16} {n:>6}", flush=True)
        print(f"  {'TOTALE':16} {total:>6}", flush=True)
        print(f"  quarantene scadute confermate come vendite: {confirmed}", flush=True)

        if args.dry_run:
            session.rollback()
            print("dry-run: nessuna modifica scritta", flush=True)
            return 0

        if total:
            # On the dashboard, which is this project's only monitoring
            # channel. A step that quietly removes a fifth of the sales figure
            # has to announce itself.
            session.add(ScrapeEvent(
                run_id=None, brand=None, level="info",
                message=("Riclassificate " + str(total) + " sparizioni non attribuibili a vendita: "
                         + ", ".join(f"{k} {v}" for k, v in changed.most_common())),
                url=None, created_at=dt.datetime.utcnow(),
            ))
        session.commit()
        print("scritto", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
