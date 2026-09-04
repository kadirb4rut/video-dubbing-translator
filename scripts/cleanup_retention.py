"""Reconcile expired private objects with database tombstones.

Run this as a scheduled operator task (for example, once daily). It is dry-run
by default so a deployment must explicitly opt into deletion.
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Job, JobArtifact, MediaAsset, VoiceConsent, VoiceProfile, now
from app.storage import object_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete expired objects and mark records deleted")
    args = parser.parse_args()
    cutoff = now() - timedelta(days=settings.retention_days)
    store = object_store()
    deleted = {"media": 0, "voices": 0, "artifacts": 0}
    with SessionLocal() as db:
        assets = db.scalars(select(MediaAsset).where(MediaAsset.created_at < cutoff, MediaAsset.deleted_at.is_(None))).all()
        voices = db.scalars(select(VoiceProfile).where(VoiceProfile.created_at < cutoff, VoiceProfile.deleted_at.is_(None))).all()
        artifacts = db.scalars(select(JobArtifact).where(JobArtifact.created_at < cutoff)).all()
        print(f"retention_cutoff={cutoff.isoformat()} media={len(assets)} voices={len(voices)} artifacts={len(artifacts)} apply={args.apply}")
        if not args.apply:
            return
        for asset in assets:
            store.delete(asset.object_key)
            asset.deleted_at = now()
            deleted["media"] += 1
        for profile in voices:
            store.delete(profile.reference_object_key)
            profile.deleted_at = now()
            profile.status = "revoked"
            consent = db.get(VoiceConsent, profile.consent_id)
            if consent:
                consent.revoked_at = now()
            deleted["voices"] += 1
        for artifact in artifacts:
            store.delete(artifact.object_key)
            job = db.get(Job, artifact.job_id)
            if job and job.output_object_key == artifact.object_key:
                job.output_object_key = None
            db.delete(artifact)
            deleted["artifacts"] += 1
        db.commit()
    print(f"deleted={deleted}")


if __name__ == "__main__":
    main()
