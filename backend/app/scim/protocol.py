"""SCIM 2.0 wire protocol: errors, filters and PATCH, written for what
Entra ID and Okta actually send rather than only for the RFC's examples.

What differs between them, and is handled here:
- Entra ID capitalises operations ("Replace") and sends booleans as
  strings ("False"). Okta sends "replace" and real booleans.
- Entra ID uses paths with filters (`emails[type eq "work"].value`) and
  sends an empty-list Add for multi-valued attributes. Okta often sends no
  path at all, just a value object (`{"op": "replace", "value": {"active":
  false}}`).
- Enterprise extension attributes arrive with their schema URN as a path
  prefix.
Attribute names are case-insensitive (RFC 7643 section 2.1).
"""

from __future__ import annotations

import copy
import re
from typing import Any

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE_USER = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
LIST_RESPONSE = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
MEDIA_TYPE = "application/scim+json"

# Never stored: the IdP owns passwords, and sign-in goes through the IdP.
_DISCARDED = {"password", "id", "meta", "groups", "schemas"}
_BOOLEANS = {"active", "primary"}


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type

    def body(self) -> dict[str, Any]:
        body = {"schemas": [ERROR], "status": str(self.status), "detail": self.detail}
        if self.scim_type:
            body["scimType"] = self.scim_type
        return body


# -- filters -----------------------------------------------------------------------

_FILTER = re.compile(r'^\s*([A-Za-z][\w.:$-]*)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE)


def parse_filter(expression: str | None, allowed: set[str]) -> tuple[str, str] | None:
    """`attribute eq "value"`, the form Entra ID and Okta use. Returns the
    attribute (lower case, without a schema URN prefix) and the value."""
    if not expression:
        return None
    match = _FILTER.match(expression)
    if not match:
        raise ScimError(400, f"Unsupported filter: {expression!r}. Supported: <attribute> eq \"<value>\"", "invalidFilter")
    attribute = match.group(1).lower()
    attribute = attribute.removeprefix(USER_SCHEMA.lower() + ":").removeprefix(GROUP_SCHEMA.lower() + ":")
    if attribute not in allowed:
        raise ScimError(400, f"Filtering on {match.group(1)!r} isn't supported", "invalidFilter")
    return attribute, match.group(2).replace('\\"', '"').replace("\\\\", "\\")


# -- resources as dicts --------------------------------------------------------------


def _coerce(name: str, value: Any) -> Any:
    if name.lower() in _BOOLEANS and isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _key(container: dict[str, Any], name: str) -> str:
    """The existing key matching `name` case-insensitively, or `name`."""
    return next((k for k in container if k.lower() == name.lower()), name)


def clean(resource: dict[str, Any]) -> dict[str, Any]:
    """An incoming resource as stored: no password, no server-owned keys,
    booleans as booleans."""
    out: dict[str, Any] = {}
    for key, value in resource.items():
        if key.lower() in _DISCARDED:
            continue
        if isinstance(value, list):
            value = [{k: _coerce(k, v) for k, v in item.items()} if isinstance(item, dict) else item for item in value]
        out[key] = _coerce(key, value)
    return out


def get(resource: dict[str, Any], name: str) -> Any:
    return resource.get(_key(resource, name))


# -- PATCH ----------------------------------------------------------------------------

_VALUE_FILTER = re.compile(r'^(\w+)\[\s*(\w+)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*\](?:\.(\w+))?$', re.IGNORECASE)


def _split_urn(path: str) -> tuple[str | None, str]:
    """"urn:...:enterprise:2.0:User:department" -> (URN, "department")."""
    for urn in (ENTERPRISE_USER, USER_SCHEMA, GROUP_SCHEMA):
        if path.lower() == urn.lower():
            return urn, ""
        if path.lower().startswith(urn.lower() + ":"):
            return urn, path[len(urn) + 1:]
    return None, path


def _apply_path(resource: dict[str, Any], op: str, path: str, value: Any) -> None:
    urn, path = _split_urn(path)
    target = resource
    if urn == ENTERPRISE_USER:
        target = resource.setdefault(_key(resource, ENTERPRISE_USER), {})
        if not path:  # the whole extension
            if op == "remove":
                resource.pop(_key(resource, ENTERPRISE_USER), None)
            elif isinstance(value, dict):
                for k, v in value.items():
                    _apply_path(target, op, k, v)
            return

    filtered = _VALUE_FILTER.match(path)
    if filtered:  # emails[type eq "work"].value
        attr, sub, match, leaf = filtered.groups()
        items = target.setdefault(_key(target, attr), [])
        hits = [i for i in items if isinstance(i, dict) and str(get(i, sub)).lower() == match.lower()]
        if op == "remove":
            if leaf:
                for item in hits:
                    item.pop(_key(item, leaf), None)
            else:
                items[:] = [i for i in items if i not in hits]
            return
        if not hits:
            hits = [{sub: match}]
            items.append(hits[0])
        for item in hits:
            if leaf:
                item[_key(item, leaf)] = _coerce(leaf, value)
            elif isinstance(value, dict):
                item.update({k: _coerce(k, v) for k, v in value.items()})
        return

    parent, _, leaf = path.rpartition(".")
    if parent:  # name.givenName
        target = target.setdefault(_key(target, parent), {})
    key = _key(target, leaf)
    if op == "remove":
        target.pop(key, None)
    elif op == "add" and isinstance(target.get(key), list):
        additions = value if isinstance(value, list) else [value]
        target[key] = target[key] + [v for v in additions if v not in target[key]]
    elif op == "add" and isinstance(value, dict) and isinstance(target.get(key), dict):
        target[key].update(value)
    else:
        target[key] = _coerce(leaf, copy.deepcopy(value))


def apply_patch(resource: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """A PatchOp request applied to a copy of `resource`."""
    if PATCH_OP not in (body.get("schemas") or []):
        raise ScimError(400, f"A PATCH body must declare {PATCH_OP}", "invalidSyntax")
    resource = copy.deepcopy(resource)
    for operation in body.get("Operations") or body.get("operations") or []:
        op = str(operation.get("op", "")).lower()
        if op not in ("add", "replace", "remove"):
            raise ScimError(400, f"Unknown PATCH operation {operation.get('op')!r}", "invalidSyntax")
        path, value = operation.get("path"), operation.get("value")
        if path:
            _apply_path(resource, op, path, value)
        elif op == "remove":
            raise ScimError(400, "A remove operation needs a path", "noTarget")
        elif isinstance(value, dict):
            for name, item in value.items():
                _apply_path(resource, op, name, item)
        else:
            raise ScimError(400, "An add or replace without a path needs an object value", "invalidValue")
    return clean(resource)


def list_response(resources: list[dict[str, Any]], total: int, start_index: int) -> dict[str, Any]:
    return {"schemas": [LIST_RESPONSE], "totalResults": total, "startIndex": start_index,
            "itemsPerPage": len(resources), "Resources": resources}
