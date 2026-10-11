#!/usr/bin/env python3
"""2d (issue #46/MOLI-37): Markdown compliance report for a rendered clip.

Re-runs the post-render verifier for one clip (verify_clip, #43) or prints the
report already stored on the clip, as a human-readable Markdown table:
rule, expected, actual (with failure detail) and how it was checked.

Read-only on the clip by default. --rerun recomputes; --store then persists it
(the same write POST /api/clips/{id}/verify does). Publishing itself is NOT
touched here: the gate stays in publish_gate.py.

In the container:
    docker exec clipping-system-vps-api-1 python scripts/e2e_compliance_report.py --clip-id <uuid>
    docker exec clipping-system-vps-api-1 python scripts/e2e_compliance_report.py --latest --rerun
    docker exec clipping-system-vps-api-1 python scripts/e2e_compliance_report.py --latest --campaign-id 18
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

# Words mirror the Mission Control dialog (frontend ComplianceReport.tsx).
_BADGE = {"pass": "✅ cumple", "fail": "❌ no cumple", "review": "🟡 revisión humana",
          "pending": "⏳ pendiente", "n/a": "— no aplica"}


def _cell(v, limit: int = 120) -> str:
    """Markdown-safe, compact cell."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    s = json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else str(v)
    s = s.replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    return s[:limit]


def render_report(report: dict) -> str:
    """compliance_report (verifier.verify) -> Markdown document."""
    checks = [c for c in (report.get("checks") or []) if isinstance(c, dict)]
    n_fail = len(report.get("failed") or [])
    n_rev = len(report.get("review") or [])
    n_na = sum(1 for c in checks if c.get("status") == "n/a")
    n_pass = len(checks) - n_fail - n_rev - n_na
    lines = [
        "# Informe de cumplimiento del clip",
        "",
        f"- **Resultado global:** {_BADGE.get(str(report.get('status')), str(report.get('status')))}",
        f"- **Verificador:** v{report.get('version')} · ruleset v{report.get('ruleset_version')}"
        f" · verificado {report.get('checked_at') or '—'}",
        f"- **Checks:** {n_pass} pasa / {n_fail} falla / {n_rev} revisión humana / {n_na} n/a",
        "",
    ]
    if not checks:
        lines += ["_Sin checks: el clip aún no tiene informe de verificación._", ""]
        return "\n".join(lines)
    lines += [
        "| Regla | Resultado | Esperado | Real | Cómo se verificó |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        rule = str(c.get("rule") or "")
        if c.get("platform"):
            rule = f"{rule} ({c['platform']})"
        actual = _cell(c.get("actual"))
        if c.get("detail"):
            actual = f"{actual} — {_cell(c['detail'], 80)}"
        lines.append("| " + " | ".join([
            f"`{rule}`",
            _BADGE.get(str(c.get("status")), str(c.get("status"))),
            _cell(c.get("expected")),
            actual,
            _cell(c.get("how")),
        ]) + " |")
    if review := (report.get("review") or []):
        lines += ["", "**Pendiente de confirmación humana al aprobar el publish:** "
                  + ", ".join(f"`{r}`" for r in review)]
    lines.append("")
    return "\n".join(lines)


def _fetch_report(db, clip, *, rerun: bool, store: bool) -> dict:
    if not rerun:
        stored = clip.compliance_report if isinstance(clip.compliance_report, dict) else {}
        if not stored.get("checks"):
            print(f"aviso: el clip {clip.id} no tiene informe guardado; usa --rerun", file=sys.stderr)
        return stored
    from app.services.rules.verifier import verify_clip

    report = verify_clip(db, clip)
    if store:
        db.commit()
        db.refresh(clip)
    else:
        db.rollback()
    return report


def run(args, db) -> tuple[int, str, dict]:
    """Resolve the clip, build the Markdown. Returns (exit_code, markdown, meta)."""
    if args.campaign_id:
        from app.models.campaign import Campaign

        if db.get(Campaign, args.campaign_id) is None:
            return 1, "", {"error": f"campaign {args.campaign_id} not found"}

    from app.models.clip import Clip

    if args.clip_id:
        clip = db.get(Clip, args.clip_id)
        if clip is None:
            return 1, "", {"error": f"clip {args.clip_id} not found"}
    else:
        q = db.query(Clip)
        if args.campaign_id:
            q = q.filter(Clip.campaign_id == args.campaign_id)
        clip = q.order_by(Clip.updated_at.desc(), Clip.created_at.desc()).first()
        if clip is None:
            where = f" in campaign {args.campaign_id}" if args.campaign_id else ""
            return 1, "", {"error": f"no clips found{where}"}

    report = _fetch_report(db, clip, rerun=args.rerun, store=args.store)
    meta = {
        "clip_id": str(clip.id),
        "campaign_id": clip.campaign_id,
        "file_path": clip.file_path,
        "status": (report or {}).get("status"),
        "failed": list((report or {}).get("failed") or []),
        "review": list((report or {}).get("review") or []),
        "compliance_status": clip.compliance_status,
    }
    if not (report or {}).get("checks"):
        return 1, "", {**meta, "error": "empty compliance report (run with --rerun)"}
    return 0, render_report(report), meta


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Markdown compliance report for a rendered clip (2d)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--clip-id", type=uuid.UUID, help="clip UUID")
    g.add_argument("--latest", action="store_true", help="most recently updated clip")
    p.add_argument("--campaign-id", type=int, help="filter --latest by campaign")
    p.add_argument("--rerun", action="store_true", help="run verify_clip now instead of the stored report")
    p.add_argument("--store", action="store_true", help="with --rerun: persist the fresh report on the clip")
    p.add_argument("--out", type=Path, default=None, help="also write the Markdown to this file")
    return p


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    if args.store and not args.rerun:
        print("e2e_compliance_report: --store solo tiene sentido junto a --rerun", file=sys.stderr)
        return 2
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        code, md, meta = run(args, db)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"e2e_compliance_report ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()
    if code != 0:
        print(f"e2e_compliance_report: {meta.get('error', 'unknown error')}", file=sys.stderr)
        return code
    print(f"clip={meta['clip_id']} campaign={meta['campaign_id']} "
          f"compliance={meta['compliance_status']} failed={len(meta['failed'])} review={len(meta['review'])}")
    print()
    print(md)
    if args.out:
        args.out.write_text(md + "\n", encoding="utf-8")
        print(f"\ninforme escrito en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())