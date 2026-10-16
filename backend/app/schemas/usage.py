"""Epic 38, US38.9: what a tenant has used, and its limits."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TenantLimitsOut(BaseModel):
    """In effect: the tenant's own, or the deployment's default. None is no limit."""

    max_concurrent_jobs: int | None
    llm_requests_per_minute: int | None
    llm_monthly_budget_usd: float | None


class TenantLimitsIn(BaseModel):
    """An operator's limits for one tenant. None falls back to the deployment's default."""

    max_concurrent_jobs: int | None = Field(default=None, ge=1)
    llm_requests_per_minute: int | None = Field(default=None, ge=1)
    llm_monthly_budget_usd: float | None = Field(default=None, gt=0)


class BudgetOut(BaseModel):
    month: str
    spent_usd: float
    budget_usd: float | None
    warn_at_usd: float | None
    status: Literal["unlimited", "ok", "warning", "stopped"]
    # US38.11: calls to the default provider use the organization's own key,
    # which the budget (on the hosting company's key) doesn't limit.
    own_key: bool = False


class OperationUsage(BaseModel):
    operation: str
    model: str
    key_source: Literal["platform", "tenant"] = "platform"
    calls: int
    total_tokens: int
    cost_usd: float
    calls_without_cost: int


class MonthUsage(BaseModel):
    month: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    calls_without_cost: int
    # US38.11: the part of cost_usd on the organization's own key or endpoint.
    own_key_cost_usd: float = 0.0
    operations: list[OperationUsage]


class UsageOverview(BaseModel):
    limits: TenantLimitsOut
    budget: BudgetOut
    jobs_running: int
    jobs_queued: int
    months: list[MonthUsage]
