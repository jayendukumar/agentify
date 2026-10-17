"""Epic 37, US37.1/US37.2: roles, the permissions each grants, and how a
grant's scope is matched against what is being changed.

Route guards ask for a *permission* (`require(Permission.AGENT_APPROVE)` in
app/api/deps.py), never a role name, so changing what a role may do is an
edit to ROLE_PERMISSIONS below and nothing else.

Scopes run coarse to fine: organization -> business unit -> (later) a
single process. A grant covers its scope and everything beneath it, so a
check walks the target's scope chain and accepts a grant at any level.
Per-process ownership (deferred, see the epic's Notes) is one more entry in
`scope_chain` plus accepting `process` in VALID_SCOPE_TYPES.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.db.models import ProcessModel, UserModel


class Role(StrEnum):
    ANALYST = "analyst"
    ARCHITECT = "architect"
    PROCESS_OWNER = "process_owner"
    APPROVER = "approver"
    AUDITOR = "auditor"
    ADMIN = "admin"


class Permission(StrEnum):
    PROCESS_EDIT = "process.edit"  # documents, BPMN, chat edits, gap answers
    PROCESS_FINALIZE = "process.finalize"  # submit a baseline for approval
    PROCESS_APPROVE = "process.approve"
    PROCESS_DELETE = "process.delete"
    BLUEPRINT_EDIT = "blueprint.edit"
    AGENT_BUILD = "agent.build"  # generate / edit agent definitions
    AGENT_TEST = "agent.test"  # environments, datasets, scenarios, runs, batches
    AGENT_RELEASE_CANDIDATE = "agent.release_candidate"
    AGENT_APPROVE = "agent.approve"
    RELEASE_CRITERIA_SET = "agent.release_criteria"
    AGENT_PUBLISH = "agent.publish"
    AGENT_DEPLOY = "agent.deploy"  # Epic 39: set up a deployment and its bindings
    RUNTIME_STOP = "runtime.stop"  # Epic 39, US39.11: kill switch
    RUNTIME_REVIEW = "runtime.review"  # Epic 39, US39.10: decide live cases in the inbox
    CONTEXT_EDIT = "context.edit"
    CONTEXT_CONFIRM = "context.confirm"
    CATALOG_EDIT = "catalog.edit"
    ORG_ADMIN = "org.admin"  # organization settings, model policy, connectors, memberships, approval policy
    USERS_MANAGE = "users.manage"
    AUDIT_READ = "audit.read"


P = Permission

# The admin role deliberately has no build or approve permission: running
# the platform and approving what runs on it are separate duties.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ANALYST: frozenset({P.PROCESS_EDIT, P.PROCESS_FINALIZE, P.CONTEXT_EDIT}),
    Role.ARCHITECT: frozenset(
        {
            P.PROCESS_EDIT,
            P.BLUEPRINT_EDIT,
            P.AGENT_BUILD,
            P.AGENT_TEST,
            P.AGENT_RELEASE_CANDIDATE,
            P.AGENT_PUBLISH,
            P.AGENT_DEPLOY,
            P.CATALOG_EDIT,
        }
    ),
    Role.PROCESS_OWNER: frozenset(
        {
            P.PROCESS_EDIT,
            P.PROCESS_FINALIZE,
            P.PROCESS_APPROVE,
            P.PROCESS_DELETE,
            P.CONTEXT_EDIT,
            P.CONTEXT_CONFIRM,
            P.RUNTIME_STOP,
            P.RUNTIME_REVIEW,
        }
    ),
    Role.APPROVER: frozenset(
        {P.AGENT_APPROVE, P.RELEASE_CRITERIA_SET, P.AUDIT_READ, P.RUNTIME_STOP, P.RUNTIME_REVIEW}
    ),
    Role.AUDITOR: frozenset({P.AUDIT_READ}),
    # Stopping an agent is a safety action, so admins may; starting one isn't.
    Role.ADMIN: frozenset({P.ORG_ADMIN, P.USERS_MANAGE, P.AUDIT_READ, P.RUNTIME_STOP}),
}

# Organization-wide by nature: a business-unit grant never satisfies them.
ORGANIZATION_ONLY: frozenset[Permission] = frozenset({P.ORG_ADMIN, P.USERS_MANAGE, P.AUDIT_READ, P.CATALOG_EDIT})

ROLE_LABELS: dict[Role, str] = {
    Role.ANALYST: "Process Analyst",
    Role.ARCHITECT: "Automation Architect",
    Role.PROCESS_OWNER: "Process Owner",
    Role.APPROVER: "Approver",
    Role.AUDITOR: "Auditor",
    Role.ADMIN: "Admin",
}

PERMISSION_LABELS: dict[Permission, str] = {
    P.PROCESS_EDIT: "editing processes",
    P.PROCESS_FINALIZE: "finalizing a baseline",
    P.PROCESS_APPROVE: "approving baselines",
    P.PROCESS_DELETE: "deleting processes",
    P.BLUEPRINT_EDIT: "editing the blueprint",
    P.AGENT_BUILD: "building agents",
    P.AGENT_TEST: "testing agents",
    P.AGENT_RELEASE_CANDIDATE: "marking release candidates",
    P.AGENT_APPROVE: "approving agent releases",
    P.RELEASE_CRITERIA_SET: "setting release criteria",
    P.AGENT_PUBLISH: "publishing agents",
    P.AGENT_DEPLOY: "deploying agents",
    P.RUNTIME_STOP: "stopping running agents",
    P.RUNTIME_REVIEW: "reviewing live cases",
    P.CONTEXT_EDIT: "editing organization context",
    P.CONTEXT_CONFIRM: "confirming organization context",
    P.CATALOG_EDIT: "editing the application catalog",
    P.ORG_ADMIN: "administering the organization",
    P.USERS_MANAGE: "managing users",
    P.AUDIT_READ: "reading the audit log",
}

ORGANIZATION = "organization"
BUSINESS_UNIT = "business_unit"
VALID_SCOPE_TYPES = frozenset({ORGANIZATION, BUSINESS_UNIT})


@dataclass(frozen=True)
class Grant:
    role: Role
    scope_type: str
    scope_id: str


def grants_for(user: UserModel) -> list[Grant]:
    if not user.active:
        return []
    out = []
    for row in user.roles:
        try:
            out.append(Grant(Role(row.role), row.scope_type, row.scope_id))
        except ValueError:
            continue  # a role this build doesn't know grants nothing
    return out


def scope_chain(session: Session, process_id: str | None) -> list[tuple[str, str]]:
    """Every scope that contains the target, coarsest first. No process
    (or an unknown one) is organization-level only."""
    chain = [(ORGANIZATION, "")]
    if process_id is not None:
        process = session.get(ProcessModel, process_id)
        if process is not None and process.business_unit_id:
            chain.append((BUSINESS_UNIT, process.business_unit_id))
    return chain


def _grants_permission(grant: Grant, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[grant.role]


def has_permission(
    session: Session, user: UserModel, permission: Permission, *, process_id: str | None = None
) -> bool:
    grants = [g for g in grants_for(user) if _grants_permission(g, permission)]
    if permission in ORGANIZATION_ONLY:
        return any(g.scope_type == ORGANIZATION for g in grants)
    if process_id is None:
        # Not about one process (e.g. creating one, editing org context):
        # a grant at any scope will do; routes that place something into a
        # scope check that scope themselves (see may_act_in_unit).
        return bool(grants)
    chain = set(scope_chain(session, process_id))
    return any((g.scope_type, g.scope_id) in chain for g in grants)


def may_act_in_unit(user: UserModel, permission: Permission, business_unit_id: str | None) -> bool:
    """Whether `user` may put something into `business_unit_id` (None =
    organization-wide) -- e.g. create a process there or move one there."""
    for grant in grants_for(user):
        if not _grants_permission(grant, permission):
            continue
        if grant.scope_type == ORGANIZATION:
            return True
        if business_unit_id is not None and grant.scope_type == BUSINESS_UNIT and grant.scope_id == business_unit_id:
            return True
    return False


def effective_permissions(user: UserModel) -> list[str]:
    """Permissions held at *some* scope -- what the UI uses to show or hide
    actions. The server still checks the scope on every request."""
    held: set[str] = set()
    for grant in grants_for(user):
        held |= {p.value for p in ROLE_PERMISSIONS[grant.role]}
    return sorted(held)


def missing_permission_message(permission: Permission) -> str:
    roles = [ROLE_LABELS[r] for r, perms in ROLE_PERMISSIONS.items() if permission in perms]
    who = roles[0] if len(roles) == 1 else ", ".join(roles[:-1]) + " or " + roles[-1]
    where = "organization-wide" if permission in ORGANIZATION_ONLY else "for this process's business unit"
    return f"{PERMISSION_LABELS[permission].capitalize()} needs the {who} role {where}"
