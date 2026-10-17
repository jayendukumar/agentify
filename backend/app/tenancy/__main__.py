"""Epic 38: manage tenants and their hosts until the operator console
(US38.8, US38.12) exists. Connects as the tables' owner (DATABASE_OWNER_URL):
the app role may only read the platform tables.

    python -m app.tenancy list
    python -m app.tenancy create northwind --name "Northwind Traders" --host northwind.localhost
    python -m app.tenancy ensure northwind --host northwind.localhost   # create unless it exists
    python -m app.tenancy add-host northwind northwind.example.com
    python -m app.tenancy suspend northwind
    python -m app.tenancy activate northwind

US38.6, a tenant's identity provider -- normally its admins set it on the
Directory page; this is for a new tenant's first IdP and for undoing a
save that locked everyone out:

    python -m app.tenancy identity show northwind
    python -m app.tenancy identity set northwind --issuer http://localhost:8180/realms/northwind
        --client-id axyntro --secret-ref secret://tenant/oidc_client_secret --admin-group northwind-admins
    python -m app.tenancy identity reset northwind     # back to its defaults

US38.14, a tenant in a database of its own (suspend it first; the
reference's secret holds the app role's URL, `<name>_owner` the owner's):

    python -m app.tenancy database attach acme --ref secret://tenant/database_url
    python -m app.tenancy database detach acme         # back to the shared database
    python -m app.tenancy database list
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import GroupRoleMappingModel, TenantDomainModel, TenantIdentityModel, TenantModel
from app.db.session import get_owner_engine
from app.ids import new_id

from . import identity, lifecycle, residency
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


def _identity(session: Session, tenant: TenantModel, args: argparse.Namespace) -> None:
    # As the owner, row-level security doesn't apply: every statement names the tenant.
    row = session.scalar(select(TenantIdentityModel).where(TenantIdentityModel.tenant_id == tenant.id))
    if args.action == "show":
        if row is None:
            print(f"{tenant.slug}: no settings saved -- " + (
                "uses the OIDC_* environment variables" if tenant.slug == get_settings().tenant_default_slug
                else "no single sign-on"))
        else:
            print(json.dumps({k: getattr(row, k) for k in identity.EDITABLE}, indent=2))
        return
    if args.action == "reset":
        session.execute(delete(TenantIdentityModel).where(TenantIdentityModel.tenant_id == tenant.id))
        print(f"{tenant.slug}: identity settings removed")
        return
    values = {
        "auth_mode": args.mode, "issuer": args.issuer, "discovery_url": args.discovery_url, "client_id": args.client_id,
        "client_secret_ref": args.secret_ref, "display_name": args.display_name, "scopes": "openid profile email",
        "acr_values": args.acr_values, "groups_claim": "groups", "jit_provisioning": not args.no_jit,
        "require_mfa": True, "mfa_amr_values": list(identity.DEFAULT_AMR) if args.mfa_amr is None else args.mfa_amr,
        "mfa_acr_values": args.mfa_acr, "scim_link_claim": args.scim_link_claim,
    }
    try:
        identity.validate(values, tenant.slug, get_settings())
    except identity.IdentityError as exc:
        sys.exit(str(exc))
    if row is None:
        row = TenantIdentityModel(id=new_id("tidp"), tenant_id=tenant.id)
        session.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = "python -m app.tenancy"
    session.execute(text("UPDATE tenant_identity SET updated_at = now() WHERE tenant_id = :t"), {"t": tenant.id})
    if args.admin_group:
        has_mappings = session.scalar(
            select(func.count()).select_from(GroupRoleMappingModel).where(GroupRoleMappingModel.tenant_id == tenant.id)
        )
        if not has_mappings:
            session.add(GroupRoleMappingModel(id=new_id("gmap"), tenant_id=tenant.id, group=args.admin_group,
                                              role="admin", created_by="python -m app.tenancy"))
    print(f"{tenant.slug}: identity settings saved (not tested against the IdP -- use the Directory page's test)")


def _database(args: argparse.Namespace) -> None:
    from app.ids import utcnow

    from . import databases

    with Session(get_owner_engine()) as session:
        if args.action == "list":
            for slug, ref in databases.tenant_databases(session.connection()):
                print(f"{slug}	{ref}")
            return
        now = utcnow().replace(tzinfo=None)
        try:
            if args.action == "attach":
                result = databases.attach(session, args.slug, args.ref, now=now)
            else:
                result = databases.detach(session, args.slug, now=now)
        except databases.DedicatedDatabaseError as exc:
            sys.exit(str(exc))
        print(f"Moved {result['rows']} rows. Activate {args.slug} when you're ready.")


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
    idp = commands.add_parser("identity", help="a tenant's identity provider (US38.6)")
    idp_commands = idp.add_subparsers(dest="action", required=True)
    idp_commands.add_parser("show").add_argument("slug")
    idp_commands.add_parser("reset").add_argument("slug")
    idp_set = idp_commands.add_parser("set")
    idp_set.add_argument("slug")
    idp_set.add_argument("--mode", choices=["oidc", "local"], default="oidc")
    idp_set.add_argument("--issuer", default="")
    idp_set.add_argument("--discovery-url", default="")
    idp_set.add_argument("--client-id", default="")
    idp_set.add_argument("--secret-ref", default="", help="secret://tenant/<name> (the TENANT_<SLUG>_<NAME> variable with the env backend)")
    idp_set.add_argument("--display-name", default="single sign-on")
    idp_set.add_argument("--acr-values", default="")
    idp_set.add_argument("--mfa-acr", action="append", default=[], help="an acr value that counts as MFA (repeatable)")
    idp_set.add_argument("--mfa-amr", action="append", default=None, help="an amr value that counts as MFA (repeatable)")
    idp_set.add_argument("--scim-link-claim", default="sub")
    idp_set.add_argument("--no-jit", action="store_true", help="only people provisioned beforehand may sign in")
    idp_set.add_argument("--admin-group", help="map this IdP group to Admin, if the tenant has no mappings yet")
    database = commands.add_parser("database", help="a tenant's own database (US38.14)")
    database_commands = database.add_subparsers(dest="action", required=True)
    database_commands.add_parser("list")
    attach = database_commands.add_parser("attach")
    attach.add_argument("slug")
    attach.add_argument("--ref", default="secret://tenant/database_url")
    database_commands.add_parser("detach").add_argument("slug")
    args = parser.parse_args(argv)

    if args.command == "database":
        _database(args)
        return
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
                try:
                    region = residency.region_for_new_tenant(args.region)
                except residency.ResidencyError as exc:
                    sys.exit(str(exc))
                tenant = TenantModel(
                    id=new_id("ten"), slug=args.slug, display_name=args.name or args.slug.title(), region=region
                )
                session.add(tenant)
                session.flush()
                print(f"Created tenant {tenant.slug} ({tenant.id})")
            _add_hosts(session, tenant, args.host)
        elif args.command == "identity":
            _identity(session, _tenant(session, args.slug), args)
        elif args.command == "add-host":
            _add_hosts(session, _tenant(session, args.slug), [args.host])
        else:
            try:
                lifecycle.change_status(_tenant(session, args.slug), args.command)
            except lifecycle.LifecycleError as exc:
                sys.exit(str(exc))


if __name__ == "__main__":
    main()
