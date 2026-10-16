"""Epic 38, US38.9: the tenant's LLM usage and cost per month, against its
limits. Admins only; the limits themselves are set by an operator."""

from collections import defaultdict

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.db.models import JobModel, LlmUsageMonthlyModel
from app.llm import metering
from app.schemas.usage import BudgetOut, MonthUsage, OperationUsage, TenantLimitsOut, UsageOverview
from app.tenancy.context import current_tenant_id
from app.tenancy.limits import BUDGET_WARN_RATIO, limits_for

from .deps import DbDep, OrgAdminDep

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("", response_model=UsageOverview)
def usage_overview(db: DbDep, user: OrgAdminDep, months: int = Query(default=12, ge=1, le=36)) -> UsageOverview:
    del user
    limits = limits_for(current_tenant_id() or "")
    state = metering.budget_state(db, limits.llm_monthly_budget_usd)
    rows = db.scalars(select(LlmUsageMonthlyModel).order_by(LlmUsageMonthlyModel.month.desc(),
                                                            LlmUsageMonthlyModel.cost_usd.desc()))
    by_month: dict[str, list[LlmUsageMonthlyModel]] = defaultdict(list)
    for row in rows:
        by_month[row.month].append(row)
    jobs = dict(db.execute(select(JobModel.status, func.count()).where(JobModel.status.in_(("queued", "running")))
                           .group_by(JobModel.status)).all())
    return UsageOverview(
        limits=TenantLimitsOut(max_concurrent_jobs=limits.max_concurrent_jobs,
                               llm_requests_per_minute=limits.llm_requests_per_minute,
                               llm_monthly_budget_usd=limits.llm_monthly_budget_usd),
        budget=BudgetOut(
            month=metering.month_of(metering.utcnow()), spent_usd=round(state.spent_usd, 6),
            budget_usd=state.budget_usd, status=state.status,
            warn_at_usd=round(BUDGET_WARN_RATIO * state.budget_usd, 2) if state.budget_usd is not None else None,
        ),
        jobs_running=jobs.get("running", 0),
        jobs_queued=jobs.get("queued", 0),
        months=[
            MonthUsage(
                month=month,
                calls=sum(r.calls for r in group),
                input_tokens=sum(r.input_tokens for r in group),
                output_tokens=sum(r.output_tokens for r in group),
                total_tokens=sum(r.total_tokens for r in group),
                cost_usd=round(sum(r.cost_usd for r in group), 6),
                calls_without_cost=sum(r.calls_without_cost for r in group),
                operations=[OperationUsage(operation=r.operation, model=r.model, calls=r.calls,
                                           total_tokens=r.total_tokens, cost_usd=round(r.cost_usd, 6),
                                           calls_without_cost=r.calls_without_cost) for r in group],
            )
            for month, group in list(by_month.items())[:months]
        ],
    )
