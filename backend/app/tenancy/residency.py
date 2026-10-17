"""Epic 38, US38.15: a tenant's data and model calls stay in its region.

One deployment per region, not one database per region per tenant: a
deployment serves the region in DEPLOYMENT_REGION, and its database, files
and workers are there. With it set:

- **Creating a tenant** puts it in this region; a tenant of another region
  is refused, naming that region's console (REGION_DEPLOYMENTS).
- **Serving:** a tenant recorded in another region isn't served here, even
  if a host name was pointed at this deployment by mistake (421).
- **Model calls** go only to a provider hosted in this region: the
  default provider's LLM_PROVIDER_REGION, or a self-hosted endpoint's
  `provider_region` in the model policy. A provider of unknown region
  doesn't count. Business units can still narrow this with their own
  residency rules (US31.8, US38.11).

Unset (the default), nothing here applies: a single-region deployment
behaves as before. Moving a tenant between regions is out of scope.
"""

from __future__ import annotations

from app.config import get_settings


class ResidencyError(ValueError):
    pass


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().upper() == (b or "").strip().upper()


def deployment_region() -> str:
    return get_settings().deployment_region.strip()


def region_for_new_tenant(region: str | None) -> str | None:
    """The region a new tenant gets here, or ResidencyError if it belongs
    to another region's deployment."""
    here = deployment_region()
    region = (region or "").strip() or None
    if not here:
        return region
    if region is None or _same(region, here):
        return here
    elsewhere = {k.upper(): v for k, v in get_settings().region_deployments.items()}.get(region.upper())
    where = f" Create it from that region's console: {elsewhere}" if elsewhere else ""
    raise ResidencyError(f"This deployment serves region {here}; a tenant in {region} lives on its own region's "
                         f"deployment.{where}")


def serves(tenant_region: str | None) -> bool:
    here = deployment_region()
    return not here or not tenant_region or _same(tenant_region, here)


def model_call_refusal(provider_region: str) -> str | None:
    """Why a model call to a provider in `provider_region` may not leave
    this deployment's region, or None."""
    here = deployment_region()
    if not here or _same(provider_region, here):
        return None
    where = f"hosted in {provider_region}" if provider_region else "of unknown hosting region"
    return f"Data stays in region {here}, and the model provider is {where}"
