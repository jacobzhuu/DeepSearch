#!/usr/bin/env python3
# ruff: noqa: E402
"""
Operator helper: acquisition funnel metrics and optional none vs Playwright comparison.

Does not create research tasks. After running paired tasks under different
``BROWSER_FETCH_BACKEND`` server settings, pass task UUIDs here.

Environment (optional if flags provide paths):
  DATABASE_URL   SQLAlchemy URL (default: from ``.env`` or ``sqlite:///./data/dev.db``)

Flags:
  --task-ids UUID,UUID,...     Same as env ``TASK_IDS`` (raw funnel JSON list).
  --pairs A:B,A:B,...          Paired none-backend vs playwright-backend task ids.
  --format json|comparison|both
  --output PATH                Write the selected format (comparison is text; json is JSON).

Examples::

  export DATABASE_URL=sqlite:////abs/path/data/dev.db
  python scripts/benchmark_acquisition_yield.py \\
    --pairs $TASK_NONE_LANGGRAPH:$TASK_PW_LANGGRAPH \\
    --format both --output /tmp/acq-bench.json

  python scripts/benchmark_acquisition_yield.py --task-ids <uuid>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.db.models import (
    CandidateUrl,
    Claim,
    ClaimEvidence,
    FetchAttempt,
    FetchJob,
    ReportArtifact,
    ResearchTask,
)
from packages.db.session import build_engine, build_session_factory
from services.orchestrator.app.services.acquisition_diagnostics import (
    compute_acquisition_funnel_diagnostics,
)
from services.orchestrator.app.settings import Settings, get_settings
from services.orchestrator.app.storage import build_snapshot_object_store


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _parse_uuids(raw: str) -> list[UUID]:
    out: list[UUID] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(UUID(part))
    return out


def _parse_pairs(raw: str) -> list[tuple[UUID, UUID]]:
    pairs: list[tuple[UUID, UUID]] = []
    for segment in raw.split(","):
        segment = segment.strip()
        if not segment:
            continue
        if ":" not in segment:
            raise ValueError(f"invalid pair (expected left:right): {segment!r}")
        left, right = segment.split(":", 1)
        pairs.append((UUID(left.strip()), UUID(right.strip())))
    return pairs


def _settings_snapshot(settings: Settings) -> dict[str, Any]:
    return {
        "acquisition_max_candidates_per_request": settings.acquisition_max_candidates_per_request,
        "acquisition_target_successful_snapshots": settings.acquisition_target_successful_snapshots,
        "acquisition_max_response_bytes": settings.acquisition_max_response_bytes,
        "research_acquisition_max_must_fetch_per_round": (
            settings.research_acquisition_max_must_fetch_per_round
        ),
    }


def _claim_evidence_count(session: Session, task_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count(ClaimEvidence.id))
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .where(Claim.task_id == task_id)
        )
        or 0
    )


def _body_too_large_cases(session: Session, task_id: UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            CandidateUrl.canonical_url,
            CandidateUrl.domain,
            FetchAttempt.trace_json,
        )
        .select_from(FetchAttempt)
        .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
        .join(CandidateUrl, CandidateUrl.id == FetchJob.candidate_url_id)
        .where(FetchJob.task_id == task_id, FetchAttempt.error_code == "body_too_large")
    ).all()
    out: list[dict[str, Any]] = []
    for canonical_url, domain, trace_json in rows:
        trace = trace_json if isinstance(trace_json, dict) else {}
        out.append(
            {
                "canonical_url": str(canonical_url),
                "domain": str(domain),
                "mime_type": trace.get("content_type") or trace.get("mime_type"),
                "observed_response_bytes": trace.get("response_bytes"),
                "max_response_bytes": trace.get("max_response_bytes"),
                "final_url": trace.get("final_url"),
                "trusted_docs_heuristic": _trusted_docs_domain(str(domain)),
                "domain_cap_likely_helpful": _trusted_docs_domain(str(domain)),
            }
        )
    return out


def _trusted_docs_domain(domain: str) -> bool:
    d = domain.lower()
    if d.startswith("docs.") or ".docs." in d:
        return True
    if d.endswith("readthedocs.io") or d.endswith("rtfd.io"):
        return True
    if d.startswith("developer."):
        return True
    if d in {"langchain.com", "python.langchain.com", "js.langchain.com"}:
        return True
    if "openai.com" in d and ("platform" in d or "cookbook" in d or "help" in d):
        return True
    return False


def _latest_report_markdown_chars(
    session: Session,
    task_id: UUID,
    settings: Settings,
) -> int | None:
    artifact = session.scalars(
        select(ReportArtifact)
        .where(ReportArtifact.task_id == task_id, ReportArtifact.format == "markdown")
        .order_by(ReportArtifact.version.desc())
        .limit(1)
    ).first()
    if artifact is None:
        return None
    try:
        store = build_snapshot_object_store(
            backend=settings.snapshot_storage_backend,
            root_directory=str(Path(settings.snapshot_storage_root).expanduser().resolve()),
            minio_endpoint=settings.minio_endpoint,
            minio_access_key=settings.minio_access_key,
            minio_secret_key=settings.minio_secret_key,
            minio_secure=settings.minio_secure,
            minio_region=settings.minio_region,
            required_buckets=[settings.snapshot_storage_bucket, settings.report_storage_bucket],
        )
        raw = store.get_bytes(bucket=artifact.storage_bucket, key=artifact.storage_key)
        return len(raw.decode("utf-8"))
    except OSError:
        return None


def _enriched_funnel(
    session: Session,
    task_id: UUID,
    *,
    settings: Settings,
) -> dict[str, Any]:
    snap = _settings_snapshot(settings)
    task = session.get(ResearchTask, task_id)
    query = task.query if task is not None else None
    base = compute_acquisition_funnel_diagnostics(
        session,
        task_id,
        task_query=query,
        settings_snapshot=snap,
    )
    base["task_query"] = query
    base["claim_evidence_count"] = _claim_evidence_count(session, task_id)
    base["report_markdown_character_count"] = _latest_report_markdown_chars(
        session, task_id, settings
    )
    base["body_too_large_cases"] = _body_too_large_cases(session, task_id)
    return base


def _int_at(path: Mapping[str, Any], *keys: str) -> int:
    cur: Any = path
    for key in keys:
        if not isinstance(cur, Mapping):
            return 0
        cur = cur.get(key)
    return int(cur) if isinstance(cur, int) else 0


def _comparison_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| query (trunc) | mode_a | mode_b | Δsnap | Δdocs | Δchunks | Δclaims | Δclaim_ev | "
        "Δreport_chars | bf_att_a | bf_att_b | bf_ok_b | bf_skip_b |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {query} | {a} | {b} | {dsnap} | {ddoc} | {dchunk} | {dclaim} | {dev} | {drep} | "
            "{bfa} | {bfb} | {bfs} | {bfk} |".format(**row)
        )
    return "\n".join(lines) + "\n"


def _build_comparison_rows(
    session: Session,
    pairs: list[tuple[UUID, UUID]],
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (flat_json_rows_for_stdout, markdown_row_dicts)."""
    flat: list[dict[str, Any]] = []
    md_rows: list[dict[str, Any]] = []
    for left, right in pairs:
        fl = _enriched_funnel(session, left, settings=settings)
        fr = _enriched_funnel(session, right, settings=settings)
        pair_payload = {
            "pair": {"none_task_id": str(left), "playwright_task_id": str(right)},
            "none": fl,
            "playwright": fr,
        }
        flat.append(pair_payload)

        q = (fl.get("task_query") or fr.get("task_query") or "")[:56]
        snap_l = _int_at(fl, "counts", "content_snapshot")
        snap_r = _int_at(fr, "counts", "content_snapshot")
        doc_l = _int_at(fl, "counts", "source_document")
        doc_r = _int_at(fr, "counts", "source_document")
        ch_l = _int_at(fl, "counts", "source_chunk")
        ch_r = _int_at(fr, "counts", "source_chunk")
        cl_l = _int_at(fl, "counts", "claim")
        cl_r = _int_at(fr, "counts", "claim")
        ev_l = int(fl.get("claim_evidence_count") or 0)
        ev_r = int(fr.get("claim_evidence_count") or 0)
        rep_l = fl.get("report_markdown_character_count")
        rep_r = fr.get("report_markdown_character_count")
        d_rep = ""
        if isinstance(rep_l, int) and isinstance(rep_r, int):
            d_rep = str(rep_r - rep_l)

        bf_l = fl.get("browser_fallback_task_metrics") or {}
        bf_r = fr.get("browser_fallback_task_metrics") or {}
        q_full = str(fl.get("task_query") or "")
        q_disp = q.replace("|", "/") + ("…" if len(q_full) > 56 else "")
        md_rows.append(
            {
                "query": q_disp,
                "a": str(left)[:8],
                "b": str(right)[:8],
                "dsnap": str(snap_r - snap_l),
                "ddoc": str(doc_r - doc_l),
                "dchunk": str(ch_r - ch_l),
                "dclaim": str(cl_r - cl_l),
                "dev": str(ev_r - ev_l),
                "drep": d_rep or "n/a",
                "bfa": str(bf_l.get("browser_fallback_attempted", 0)),
                "bfb": str(bf_r.get("browser_fallback_attempted", 0)),
                "bfs": str(bf_r.get("browser_fallback_succeeded", 0)),
                "bfk": str(bf_r.get("browser_fallback_skipped_after_consideration", 0)),
            }
        )
    return flat, md_rows


def main() -> int:
    _load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task-ids",
        default=os.environ.get("TASK_IDS", "").strip(),
        help="Comma-separated task UUIDs (raw per-task funnel JSON array).",
    )
    parser.add_argument(
        "--pairs",
        default=os.environ.get("BENCHMARK_PAIRS", "").strip(),
        help="Comma-separated pairs left:right (none-mode task vs playwright-mode task).",
    )
    parser.add_argument(
        "--format",
        choices=("json", "comparison", "both"),
        default=os.environ.get("BENCHMARK_FORMAT", "json"),
    )
    parser.add_argument("--output", default=os.environ.get("BENCHMARK_OUTPUT", "").strip())
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "").strip()
    settings = get_settings()
    if not database_url:
        database_url = settings.database_url

    engine = build_engine(database_url)
    factory = build_session_factory(engine)
    out_json: dict[str, Any] | list[Any] | None = None
    out_text = ""

    with factory() as session:
        assert isinstance(session, Session)
        if args.pairs:
            pairs = _parse_pairs(args.pairs)
            flat, md_rows = _build_comparison_rows(session, pairs, settings)
            out_json = {"pairs": flat}
            out_text = _comparison_table(md_rows)
            if args.format in {"json", "both"}:
                print(json.dumps(flat, indent=2, sort_keys=True))
            if args.format in {"comparison", "both"}:
                if args.format == "comparison":
                    print(out_text, end="")
                else:
                    print("\n--- comparison (markdown table) ---\n")
                    print(out_text, end="")
        elif args.task_ids:
            task_ids = _parse_uuids(args.task_ids)
            rows = [_enriched_funnel(session, tid, settings=settings) for tid in task_ids]
            out_json = rows
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            parser.error("Provide --task-ids or --pairs (or set TASK_IDS / BENCHMARK_PAIRS).")

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "comparison":
            path.write_text(out_text, encoding="utf-8")
        elif out_json is not None:
            path.write_text(json.dumps(out_json, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
