"""Leaderboard query logic — no FastAPI dependency."""

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from glokta.infrastructure.db.orm import Model, ProbeResult, Run
from glokta.domain.risks import ACTIVE_RISKS, compute_risk_pass_rates

logger = logging.getLogger(__name__)


class LeaderboardService:
    def __init__(self, session: Session) -> None:
        self._db = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _latest_run_subquery(self):
        max_ts = (
            select(
                Run.model_id,
                func.max(Run.created_at).label("max_created_at"),
            )
            .where(Run.status == "complete")
            .group_by(Run.model_id)
            .subquery()
        )
        return (
            select(Run.id.label("id"), Run.model_id)
            .join(
                max_ts,
                (Run.model_id == max_ts.c.model_id)
                & (Run.created_at == max_ts.c.max_created_at),
            )
            .where(Run.status == "complete")
            .subquery()
        )

    def _apply_filters(self, stmt, probe_category: str | None, model_id: UUID | None):
        if probe_category:
            stmt = stmt.where(ProbeResult.probe_category == probe_category)
        if model_id:
            stmt = stmt.where(Model.id == model_id)
        return stmt

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_leaderboard(
        self,
        probe_category: str | None = None,
        model_id: UUID | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        """Paginated leaderboard — one row per (model, probe_category) from each model's most recent complete run.

        Returns a dict with keys: rows, total, page, page_size, total_pages.
        Each row is a dict matching the LeaderboardRow schema field names.
        """
        latest_run = self._latest_run_subquery()

        latest_origin = (
            select(Run.triggered_by)
            .where(Run.model_id == Model.id, Run.status == "complete")
            .order_by(Run.completed_at.desc())
            .limit(1)
            .correlate(Model)
            .scalar_subquery()
        )

        base = (
            select(
                Model.id.label("model_id"),
                Model.name.label("model_name"),
                Model.provider.label("provider"),
                ProbeResult.probe_category.label("probe_category"),
                func.sum(ProbeResult.pass_count).label("total_pass"),
                func.sum(ProbeResult.fail_count).label("total_fail"),
                func.coalesce(func.avg(ProbeResult.score), 0.0).label("score"),
                latest_origin.label("origin"),
            )
            .join(latest_run, ProbeResult.run_id == latest_run.c.id)
            .join(Model, latest_run.c.model_id == Model.id)
            .group_by(Model.id, Model.name, Model.provider, ProbeResult.probe_category)
            .order_by(func.coalesce(func.avg(ProbeResult.score), 0.0).asc())
        )
        base = self._apply_filters(base, probe_category, model_id)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = self._db.execute(count_stmt).scalar() or 0

        offset = (page - 1) * page_size
        results = self._db.execute(base.offset(offset).limit(page_size)).all()

        rows = [
            {
                "model_id": row.model_id,
                "model_name": row.model_name,
                "provider": row.provider,
                "probe_category": row.probe_category,
                "total_pass": row.total_pass or 0,
                "total_fail": row.total_fail or 0,
                "score": row.score or 0.0,
                "origin": row.origin or "api",
            }
            for row in results
        ]

        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        return {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    def get_model_detail(self, model_id: UUID) -> dict | None:
        """Per-model detail with all probe results from most recent complete run.

        Returns None if model not found. Returns a dict with keys:
        model_id, model_name, provider, run_id, probe_results, summary.
        """
        model = self._db.query(Model).filter(Model.id == model_id).first()
        if not model:
            return None

        run = (
            self._db.query(Run)
            .filter(Run.model_id == model_id, Run.status == "complete")
            .order_by(Run.created_at.desc())
            .first()
        )

        probe_results = (
            self._db.query(ProbeResult).filter(ProbeResult.run_id == run.id).all()
            if run
            else []
        )

        probe_result_details = [
            {
                "probe_name": pr.probe_name,
                "probe_category": pr.probe_category,
                "detector": pr.detector,
                "pass_count": pr.pass_count,
                "fail_count": pr.fail_count,
                "score": pr.score,
            }
            for pr in probe_results
        ]

        summary = None
        if probe_results:
            total_pass = sum(pr.pass_count for pr in probe_results)
            total_fail = sum(pr.fail_count for pr in probe_results)
            avg_score = sum(pr.score or 0.0 for pr in probe_results) / len(probe_results)
            summary = {
                "model_id": model.id,
                "model_name": model.name,
                "provider": model.provider,
                "probe_category": "overall",
                "total_pass": total_pass,
                "total_fail": total_fail,
                "score": avg_score,
            }

        return {
            "model_id": model.id,
            "model_name": model.name,
            "provider": model.provider,
            "run_id": run.id if run else None,
            "probe_results": probe_result_details,
            "summary": summary,
        }

    def get_risk_leaderboard(self, included_risks: list[str] | None = None) -> dict:
        """Risk-based leaderboard — one row per model, scored by mean pass rate across risk categories.

        Returns a dict with keys: models (list of dicts), included_risks (list of str).
        """
        risks = included_risks if included_risks else ACTIVE_RISKS

        latest_run = self._latest_run_subquery()

        rows = (
            self._db.execute(
                select(
                    Model.id.label("model_id"),
                    Model.name.label("model_name"),
                    Model.provider.label("provider"),
                    ProbeResult.probe_category.label("probe_category"),
                    func.sum(ProbeResult.pass_count).label("pass_count"),
                    func.sum(ProbeResult.fail_count).label("fail_count"),
                )
                .join(latest_run, ProbeResult.run_id == latest_run.c.id)
                .join(Model, latest_run.c.model_id == Model.id)
                .where(ProbeResult.probe_category.in_(risks))
                .group_by(Model.id, Model.name, Model.provider, ProbeResult.probe_category)
            )
            .all()
        )

        models_data: dict = {}
        for row in rows:
            mid = row.model_id
            if mid not in models_data:
                models_data[mid] = {
                    "model_id": mid,
                    "model_name": row.model_name,
                    "provider": row.provider,
                    "probe_results": [],
                }
            models_data[mid]["probe_results"].append({
                "probe_category": row.probe_category,
                "pass_count": row.pass_count or 0,
                "fail_count": row.fail_count or 0,
            })

        model_rows = []
        for data in models_data.values():
            per_risk, overall = compute_risk_pass_rates(data["probe_results"], included_risks=risks)
            model_rows.append({
                "model_id": data["model_id"],
                "model_name": data["model_name"],
                "provider": data["provider"],
                "overall_pass_rate": overall,
                "per_risk": per_risk,
            })

        model_rows.sort(
            key=lambda r: r["overall_pass_rate"] if r["overall_pass_rate"] is not None else -1,
            reverse=True,
        )

        return {"models": model_rows, "included_risks": risks}

    def get_trends(self, model_id: UUID, included_risks: list[str] | None = None) -> dict | None:
        """All historical scan results for a model, ordered by completion date.

        Returns None if model not found. Returns a dict with keys:
        model_id, model_name, points (list of TrendPoint dicts).
        """
        model = self._db.query(Model).filter(Model.id == model_id).first()
        if not model:
            return None

        risks = included_risks if included_risks else ACTIVE_RISKS

        runs = (
            self._db.query(Run)
            .filter(Run.model_id == model_id, Run.status == "complete")
            .order_by(Run.completed_at.asc())
            .all()
        )

        points = []
        for run in runs:
            probe_results = self._db.query(ProbeResult).filter(ProbeResult.run_id == run.id).all()
            pr_dicts = [
                {
                    "probe_category": pr.probe_category,
                    "pass_count": pr.pass_count,
                    "fail_count": pr.fail_count,
                }
                for pr in probe_results
            ]
            per_risk, overall = compute_risk_pass_rates(pr_dicts, included_risks=risks)
            points.append({
                "run_id": run.id,
                "completed_at": run.completed_at,
                "per_risk": per_risk,
                "overall_pass_rate": overall,
            })

        return {"model_id": model.id, "model_name": model.name, "points": points}
