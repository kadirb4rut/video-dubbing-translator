"""Delete soft-deleted/pending objects after the configured retention window."""

from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import MediaAsset, VoiceProfile, now
from app.storage import object_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cutoff = now() - timedelta(days=settings.retention_days)
    db = SessionLocal()
    store = object_store()
    removed = 0
    try:
        assets = db.scalars(select(MediaAsset).where(MediaAsset.deleted_at.is_not(None), MediaAsset.deleted_at < cutoff)).all()
        voices = db.scalars(select(VoiceProfile).where(VoiceProfile.deleted_at.is_not(None), VoiceProfile.deleted_at < cutoff)).all()
        for row in [*assets, *voices]:
            removed += 1
            if not args.dry_run:
                store.delete(row.object_key if isinstance(row, MediaAsset) else row.reference_object_key)
                db.delete(row)
        if not args.dry_run:
            db.commit()
        print({"removed": removed, "dry_run": args.dry_run})
    finally:
        db.close()


if __name__ == "__main__":
    main()
