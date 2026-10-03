"""#31: downloads only for workable campaigns."""
from __future__ import annotations


def cancel_unworkable(db, dry_run: bool = False) -> int:
    """Cancel *pending* download jobs whose campaign is not workable (#31)."""
    from datetime import datetime, timezone

    from app.models.campaign import Campaign
    from app.models.job import Job
    from app.services.campaign_transitions import WORKABLE_STATUSES

    jobs = db.query(Job).filter(Job.job_type == "download", Job.status == "pending").all()
    ids = {str((j.payload or {}).get("campaign_id") or "") for j in jobs}
    status_by_id = {
        str(cid): st
        for cid, st in db.query(Campaign.id, Campaign.status).filter(
            Campaign.id.in_([int(i) for i in ids if i.isdigit()])
        )
    }
    n = 0
    for j in jobs:
        cid = str((j.payload or {}).get("campaign_id") or "")
        st = status_by_id.get(cid)
        if st in WORKABLE_STATUSES:
            continue
        n += 1
        print(f"download_enqueue_tick cancel job={j.id} campaign={cid} status={st}")
        if dry_run:
            continue
        j.status = "cancelled"
        j.lease_until = None
        j.completed_at = datetime.now(timezone.utc)
        j.error_message = f"cancelled: campaign {cid} not workable (status={st})"
    if n and not dry_run:
        db.commit()
    return n
