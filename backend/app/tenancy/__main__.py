"""Epic 38: manage tenants and their hosts until the operator console
(US38.8, US38.12) exists. Connects as the tables' owner (DATABASE_OWNER_URL):
the app role may only read the platform tables.

    python -m app.tenancy list
    python -m app.tenancy create northwind --name "Northwind Traders" --host northwind.localhost
    python -m app.tenancy ensure northwind --host northwind.localhost   # create unless it exists
    python -m app.tenancy add-host northwind northwind.example.com
    python -m app.tenancy suspend northwind
    python -m app.tenancy activate northwind
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TenantDomainModel, TenantModel
from app.db.session import get_owner_engine
from app.ids import new_id

from .hosts import normalize_host


def _tenant(session: Session, slug: str) -> TenantModel:
    tenant = session.scalar(select(TenantModel).where(TenantModel.slug == slug))
    if tenant is None:
        sys.exit(f"No tenant '{slug}'")
    return tenant


def _add_hosts(session: Session, tenant: TenantModel, hosts: list[str]) -> None:
    for raw in hosts:
        host = normalize_host(raw)
        existing = session.get(TenantDomainModel, host)
        if existing is not None and existing.tenant_id != tenant.id:
            sys.exit(f"Host '{host}' already reaches another tenant")
        if existing is None:
            session.add(TenantDomainModel(host=host, tenant_id=tenant.id))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.tenancy", description="Manage tenants (Epic 38).")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for name in ("create", "ensure"):
        command = commands.add_parser(name)
        command.add_argument("slug")
        command.add_argument("--name", help="display name (default: the slug, capitalized)")
        command.add_argument("--host", action="append", default=[], help="a host that reaches it (repeatable)")
        command.add_argument("--region")
    add_host = commands.add_parser("add-host")
    add_host.add_argument("slug")
    add_host.add_argument("host")
    for name in ("suspend", "activate"):
        commands.add_parser(name).add_argument("slug")
    args = parser.parse_args(argv)

    with Session(get_owner_engine()) as session, session.begin():
        if args.command == "list":
            for tenant in session.scalars(select(TenantModel).order_by(TenantModel.slug)):
                hosts = session.scalars(select(TenantDomainModel.host).where(TenantDomainModel.tenant_id == tenant.id))
                print(f"{tenant.slug}\t{tenant.status}\t{tenant.display_name}\t{', '.join(sorted(hosts))}")
        elif args.command in ("create", "ensure"):
            tenant = session.scalar(select(TenantModel).where(TenantModel.slug == args.slug))
            if tenant is not None and args.command == "create":
                sys.exit(f"Tenant '{args.slug}' already exists")
            if tenant is None:
                tenant = TenantModel(
                    id=new_id("ten"), slug=args.slug, display_name=args.name or args.slug.title(), region=args.region
                )
                session.add(tenant)
                session.flush()
                print(f"Created tenant {tenant.slug} ({tenant.id})")
            _add_hosts(session, tenant, args.host)
        elif args.command == "add-host":
            _add_hosts(session, _tenant(session, args.slug), [args.host])
        else:
            _tenant(session, args.slug).status = "suspended" if args.command == "suspend" else "active"


if __name__ == "__main__":
    main()
