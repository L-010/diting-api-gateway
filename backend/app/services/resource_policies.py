"""通用接口访问策略、资源归属规则和执行器。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from jsonpath_ng.ext import parse as parse_jsonpath
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApiEndpoint, EndpointAccessPolicy, ExternalResource, PublishedRoute


ACCESS_MODES = {"authenticated", "owner", "shared", "admin_only"}
RULE_ACTIONS = {"require_owner", "require_parent_owner", "register_owner", "mark_deleted"}
RESOURCE_MARKERS = ("dataset", "job", "task", "artifact", "file", "result", "log", "workspace", "project")
PATH_PARAM_PATTERN = re.compile(r"\{([^/{}]+)\}")


def empty_resource_rules() -> dict[str, object]:
    return {"version": 1, "pre_checks": [], "post_actions": [], "list_filter": None}


def _singular(value: str) -> str:
    value = value.strip("_-").lower()
    if value.endswith("ies") and len(value) > 3:
        return f"{value[:-3]}y"
    if value.endswith("s") and len(value) > 1:
        return value[:-1]
    return value or "resource"


def _resource_kind_from_param(name: str) -> str:
    return _singular(name[:-3] if name.lower().endswith("_id") else name)


def _collection_kind(path: str) -> str:
    segments = [segment for segment in path.lower().split("/") if segment and not segment.startswith("{")]
    return _singular(segments[-1]) if segments else "resource"


def _has_admin_marker(path: str, summary: str, risk_flags: set[str]) -> bool:
    text = f"{path} {summary}".lower()
    return "internal" in risk_flags or "internal_or_deprecated" in risk_flags or any(marker in text for marker in ("/admin", " admin ", "/internal", "/debug", "/private"))


def suggest_access_policy(
    *,
    method: str,
    path: str,
    summary: str = "",
    risk_flags: set[str] | None = None,
    request_body: object | None = None,
    responses: object | None = None,
) -> dict[str, object]:
    """根据 OpenAPI 元数据生成可由管理员直接确认的最小安全策略。"""
    method = method.upper()
    flags = risk_flags or set()
    rules = empty_resource_rules()
    pre_checks = rules["pre_checks"]
    post_actions = rules["post_actions"]
    if not isinstance(pre_checks, list) or not isinstance(post_actions, list):
        raise RuntimeError("默认资源策略格式错误")

    if _has_admin_marker(path, summary, flags):
        return {"access_mode": "admin_only", "rules": rules, "complete": True, "missing_fields": [], "reason": "识别为管理或内部接口"}

    path_params = PATH_PARAM_PATTERN.findall(path)
    id_params = [item for item in path_params if item.lower().endswith("_id")]
    for param in id_params:
        pre_checks.append({"action": "require_owner", "resource_kind": _resource_kind_from_param(param), "source": "path", "selector": param})

    lowered_path = path.lower().rstrip("/")
    last_segment = lowered_path.rsplit("/", 1)[-1]
    is_global_list = method == "GET" and not path_params and (_singular(last_segment) != last_segment or "global_list_endpoint" in flags)
    if is_global_list and any(marker in lowered_path for marker in RESOURCE_MARKERS):
        kind = _collection_kind(path)
        rules["list_filter"] = {
            "items_selectors": ["$", "$.items", f"$.{last_segment}"],
            "id_selectors": [f"$.{kind}_id", "$.id"],
            "resource_kind": kind,
        }
        return {
            "access_mode": "owner",
            "rules": rules,
            "complete": True,
            "missing_fields": [],
            "reason": "识别为用户资源列表，按当前用户归属过滤",
        }

    if method == "POST" and not path_params:
        kind = _collection_kind(path)
        if _singular(last_segment) != last_segment and any(marker in kind for marker in RESOURCE_MARKERS):
            post_actions.append({"action": "register_owner", "resource_kind": kind, "source": "response_json", "selectors": [f"$.{kind}_id", "$.id"]})

    if method == "DELETE" and id_params:
        param = id_params[-1]
        post_actions.append({"action": "mark_deleted", "resource_kind": _resource_kind_from_param(param), "source": "path", "selector": param})

    request_text = json.dumps(request_body or {}, ensure_ascii=False).lower()
    body_resource_ids = sorted(set(re.findall(r'"([a-z][a-z0-9_]*_id)"', request_text)))
    for field in body_resource_ids:
        kind = _resource_kind_from_param(field)
        if any(marker in kind for marker in RESOURCE_MARKERS):
            pre_checks.append({"action": "require_parent_owner", "resource_kind": kind, "source": "request_json", "selector": f"$.{field}", "optional": True})

    response_text = json.dumps(responses or {}, ensure_ascii=False).lower()
    response_resource_ids = sorted(set(re.findall(r'"([a-z][a-z0-9_]*_id)"', response_text)))
    for field in response_resource_ids:
        kind = _resource_kind_from_param(field)
        if any(marker in kind for marker in RESOURCE_MARKERS) and not any(item.get("resource_kind") == kind for item in post_actions if isinstance(item, dict)):
            post_actions.append({"action": "register_owner", "resource_kind": kind, "source": "response_json", "selector": f"$..{field}", "many": True, "optional": True})

    if pre_checks or post_actions:
        return {"access_mode": "owner", "rules": rules, "complete": True, "missing_fields": [], "reason": "识别到用户资源 ID 或资源创建响应"}
    return {"access_mode": "authenticated", "rules": rules, "complete": True, "missing_fields": [], "reason": "未识别到用户资源，使用平台身份鉴权"}


def _selectors(rule: dict[str, object]) -> list[str]:
    values = rule.get("selectors")
    if isinstance(values, list):
        return [str(item) for item in values if str(item)]
    selector = str(rule.get("selector") or "")
    return [selector] if selector else []


def validate_access_policy(access_mode: str, rules: object, *, shared_confirm: bool = False) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    if access_mode not in ACCESS_MODES:
        errors.append("访问模式不合法")
    normalized = rules if isinstance(rules, dict) else empty_resource_rules()
    normalized.setdefault("version", 1)
    normalized.setdefault("pre_checks", [])
    normalized.setdefault("post_actions", [])
    normalized.setdefault("list_filter", None)
    if access_mode == "shared" and normalized.get("requires_shared_confirmation") and not shared_confirm:
        errors.append("全局列表必须明确确认共享")
    for section in ("pre_checks", "post_actions"):
        items = normalized.get(section)
        if not isinstance(items, list):
            errors.append(f"{section} 必须是数组")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{section}[{index}] 必须是对象")
                continue
            if item.get("action") not in RULE_ACTIONS:
                errors.append(f"{section}[{index}] 动作不支持")
            if not str(item.get("resource_kind") or "").strip():
                errors.append(f"{section}[{index}] 缺少资源类型")
            source = str(item.get("source") or "")
            if source not in {"path", "query", "request_json", "response_json", "response_header"}:
                errors.append(f"{section}[{index}] 数据来源不支持")
            selectors = _selectors(item)
            if not selectors:
                errors.append(f"{section}[{index}] 缺少选择器")
            if source in {"request_json", "response_json"}:
                for selector in selectors:
                    try:
                        parse_jsonpath(selector)
                    except Exception:
                        errors.append(f"{section}[{index}] JSONPath 不合法: {selector}")
    list_filter = normalized.get("list_filter")
    if list_filter is not None:
        if not isinstance(list_filter, dict):
            errors.append("list_filter 必须是对象")
        else:
            items_selectors = list_filter.get("items_selectors") or [list_filter.get("items_selector")]
            id_selectors = list_filter.get("id_selectors") or [list_filter.get("id_selector")]
            if not isinstance(items_selectors, list) or not any(str(item or "") for item in items_selectors):
                errors.append("list_filter 缺少 items_selector")
            if not isinstance(id_selectors, list) or not any(str(item or "") for item in id_selectors):
                errors.append("list_filter 缺少 id_selector")
            if not str(list_filter.get("resource_kind") or ""):
                errors.append("list_filter 缺少 resource_kind")
            for selector in [*items_selectors, *id_selectors] if isinstance(items_selectors, list) and isinstance(id_selectors, list) else []:
                if selector:
                    try:
                        parse_jsonpath(str(selector))
                    except Exception:
                        errors.append(f"list_filter JSONPath 不合法: {selector}")
    if access_mode == "owner" and not normalized.get("pre_checks") and not normalized.get("post_actions") and not normalized.get("list_filter"):
        errors.append("创建者资源策略至少需要一条归属规则")
    return normalized, errors


def upsert_endpoint_access_policy(
    session: Session,
    *,
    endpoint: ApiEndpoint,
    access_mode: str,
    rules: dict[str, object],
    source: str,
    confirmed_by_user_id: str | None,
    confirmed: bool,
) -> EndpointAccessPolicy:
    policy = session.scalar(select(EndpointAccessPolicy).where(EndpointAccessPolicy.endpoint_id == endpoint.id))
    if not policy:
        policy = EndpointAccessPolicy(tool_id=endpoint.tool_id, endpoint_id=endpoint.id)
        session.add(policy)
    policy.access_mode = access_mode
    policy.rules_json = json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
    policy.source = source
    policy.confirmed_by_user_id = confirmed_by_user_id if confirmed else None
    policy.confirmed_at = datetime.now(timezone.utc) if confirmed else None
    policy.version = (policy.version or 0) + 1
    policy.updated_at = datetime.now(timezone.utc)
    return policy


def access_policy_view(policy: EndpointAccessPolicy | None, suggestion: dict[str, object]) -> dict[str, object]:
    if not policy:
        return {"access_mode": suggestion["access_mode"], "rules": suggestion["rules"], "source": "auto", "confirmed": False, "complete": suggestion["complete"], "missing_fields": suggestion["missing_fields"], "reason": suggestion["reason"]}
    try:
        rules = json.loads(policy.rules_json)
    except json.JSONDecodeError:
        rules = empty_resource_rules()
    _, errors = validate_access_policy(policy.access_mode, rules, shared_confirm=policy.confirmed_at is not None)
    return {"access_mode": policy.access_mode, "rules": rules, "source": policy.source, "confirmed": policy.confirmed_at is not None, "complete": not errors, "missing_fields": errors, "reason": "已保存的接口访问策略"}


def _json_values(payload: object, selectors: list[str]) -> list[object]:
    for selector in selectors:
        values = [match.value for match in parse_jsonpath(selector).find(payload)]
        values = [item for item in values if item is not None and item != ""]
        if values:
            return values
    return []


def _extract_values(
    rule: dict[str, object],
    *,
    path_params: dict[str, str],
    query_params: object,
    request_json: object,
    response_json: object,
    response_headers: dict[str, str] | None = None,
) -> list[object]:
    source = str(rule.get("source") or "")
    selectors = _selectors(rule)
    if source == "path":
        return [path_params[item] for item in selectors if item in path_params]
    if source == "query":
        getter = getattr(query_params, "get", None)
        return [value for item in selectors if callable(getter) and (value := getter(item)) not in {None, ""}]
    if source == "request_json":
        return _json_values(request_json, selectors)
    if source == "response_json":
        return _json_values(response_json, selectors)
    if source == "response_header":
        headers = {key.lower(): value for key, value in (response_headers or {}).items()}
        return [headers[item.lower()] for item in selectors if item.lower() in headers]
    return []


def _owned_resource(session: Session, tool_id: str, user_id: str, kind: str, upstream_id: str) -> ExternalResource | None:
    return session.scalar(
        select(ExternalResource).where(
            ExternalResource.tool_id == tool_id,
            ExternalResource.owner_user_id == user_id,
            ExternalResource.kind == kind,
            ExternalResource.upstream_id == upstream_id,
            ExternalResource.deleted_at.is_(None),
        )
    )


def execute_pre_checks(
    session: Session,
    *,
    route: PublishedRoute,
    user_id: str,
    path_params: dict[str, str],
    query_params: object,
    request_json: object,
) -> None:
    if route.access_mode != "owner":
        return
    try:
        policy = json.loads(route.resource_policy_json or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=502, detail={"code": "RESOURCE_POLICY_INVALID", "message": "资源策略不可用"}) from error
    for rule in policy.get("pre_checks", []):
        if not isinstance(rule, dict):
            continue
        values = _extract_values(rule, path_params=path_params, query_params=query_params, request_json=request_json, response_json={})
        if not values and rule.get("optional"):
            continue
        if not values:
            raise HTTPException(status_code=404, detail="资源不存在")
        kind = str(rule.get("resource_kind") or "resource")
        if any(_owned_resource(session, route.tool_id, user_id, kind, str(value)) is None for value in values):
            raise HTTPException(status_code=404, detail="资源不存在")


def _register_owner(
    session: Session,
    *,
    route: PublishedRoute,
    user_id: str,
    kind: str,
    upstream_id: str,
    parent_upstream_id: str | None,
    source_request_id: str | None,
) -> None:
    existing = session.scalar(select(ExternalResource).where(ExternalResource.tool_id == route.tool_id, ExternalResource.kind == kind, ExternalResource.upstream_id == upstream_id))
    if existing:
        if existing.owner_user_id != user_id:
            raise HTTPException(status_code=502, detail={"code": "RESOURCE_OWNERSHIP_CONFLICT", "message": "上游资源归属冲突"})
        existing.deleted_at = None
        existing.status = "active"
        existing.parent_upstream_id = parent_upstream_id or existing.parent_upstream_id
        existing.source_route_id = route.id
        existing.source_request_id = existing.source_request_id or source_request_id
        if kind == "job":
            from .remote_files import queue_resource_sync

            queue_resource_sync(session, existing)
        return
    resource = ExternalResource(tool_id=route.tool_id, owner_user_id=user_id, kind=kind, upstream_id=upstream_id, parent_upstream_id=parent_upstream_id, source_route_id=route.id, source_request_id=source_request_id, status="active")
    session.add(resource)
    session.flush()
    if kind == "job":
        from .remote_files import queue_resource_sync

        queue_resource_sync(session, resource)


def execute_post_actions(
    session: Session,
    *,
    route: PublishedRoute,
    user_id: str,
    path_params: dict[str, str],
    query_params: object,
    request_json: object,
    response_json: object,
    response_headers: dict[str, str],
    source_request_id: str | None = None,
) -> None:
    if route.access_mode != "owner":
        return
    try:
        policy = json.loads(route.resource_policy_json or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=502, detail={"code": "RESOURCE_POLICY_INVALID", "message": "资源策略不可用"}) from error
    for rule in policy.get("post_actions", []):
        if not isinstance(rule, dict):
            continue
        values = _extract_values(rule, path_params=path_params, query_params=query_params, request_json=request_json, response_json=response_json, response_headers=response_headers)
        if not values and rule.get("optional"):
            continue
        if not values:
            raise HTTPException(status_code=502, detail={"code": "RESOURCE_REGISTRATION_FAILED", "message": "未能从上游响应提取资源标识"})
        kind = str(rule.get("resource_kind") or "resource")
        action = str(rule.get("action") or "")
        parent_id: str | None = None
        parent = rule.get("parent")
        if isinstance(parent, dict):
            parent_values = _extract_values(parent, path_params=path_params, query_params=query_params, request_json=request_json, response_json=response_json, response_headers=response_headers)
            parent_id = str(parent_values[0]) if parent_values else None
        for value in values:
            upstream_id = str(value)
            if action == "register_owner":
                _register_owner(session, route=route, user_id=user_id, kind=kind, upstream_id=upstream_id, parent_upstream_id=parent_id, source_request_id=source_request_id)
            elif action == "mark_deleted":
                resource = _owned_resource(session, route.tool_id, user_id, kind, upstream_id)
                if resource:
                    resource.deleted_at = datetime.now(timezone.utc)
                    resource.status = "deleted"


def filter_owned_list(session: Session, *, route: PublishedRoute, user_id: str, payload: object) -> object:
    if route.access_mode != "owner":
        return payload
    try:
        policy = json.loads(route.resource_policy_json or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=502, detail={"code": "RESOURCE_POLICY_INVALID", "message": "资源策略不可用"}) from error
    config = policy.get("list_filter")
    if not config:
        return payload
    if not isinstance(config, dict):
        raise HTTPException(status_code=502, detail={"code": "RESOURCE_LIST_FILTER_FAILED", "message": "列表过滤规则不可用"})
    raw_items_selectors = config.get("items_selectors") or [config.get("items_selector")]
    raw_id_selectors = config.get("id_selectors") or [config.get("id_selector")]
    items_selectors = [str(item) for item in raw_items_selectors if item] if isinstance(raw_items_selectors, list) else []
    id_selectors = [str(item) for item in raw_id_selectors if item] if isinstance(raw_id_selectors, list) else []
    kind = str(config.get("resource_kind") or "resource")
    matches = []
    for selector in items_selectors:
        candidate = parse_jsonpath(selector).find(payload)
        if len(candidate) == 1 and isinstance(candidate[0].value, list):
            matches = candidate
            break
    if not matches:
        raise HTTPException(status_code=502, detail={"code": "RESOURCE_LIST_FILTER_FAILED", "message": "未能定位列表数据"})
    owned: list[object] = []
    for item in matches[0].value:
        ids = _json_values(item, id_selectors)
        if len(ids) != 1:
            raise HTTPException(status_code=502, detail={"code": "RESOURCE_LIST_FILTER_FAILED", "message": "列表项缺少资源标识"})
        if _owned_resource(session, route.tool_id, user_id, kind, str(ids[0])):
            owned.append(item)
    if isinstance(payload, list) and matches[0].value is payload:
        return owned
    full_path = matches[0].full_path
    full_path.update(payload, owned)
    return payload
