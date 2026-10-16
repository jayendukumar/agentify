"""Epic 38, US38.8: the operator console -- the hosting company's own view
of the deployment, separate from every tenant.

- It answers only on the operator hosts (OPERATOR_HOSTS), which reach no
  tenant (app/tenancy/middleware.py), and its routes don't exist on any
  tenant's host.
- Operators sign in through the platform's own IdP with MFA, and must be
  in OPERATOR_GROUP (`auth.py`). Their sessions and the platform audit log
  are platform tables the app role can't touch.
- It connects as the platform role (DATABASE_PLATFORM_URL), which may
  write `tenants` and `tenant_domains` and has no privilege on any tenant
  table: listing tenants, creating, suspending and deleting them can't
  read tenant data even by mistake.
- Health and usage are counts, computed per tenant by the app role under
  that tenant's row-level security (`tenants.usage`), never rows.
- Inside a tenant, an operator only goes as support, under a grant the
  tenant's admin gave (app/auth/support.py).
"""
