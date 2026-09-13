"""受控 OpenAPI/Swagger 导入服务。

该模块只把规范文档解析成“候选接口”和治理元数据，不会直接创建任意代理。
管理员发布前仍需要经过上游白名单、风险、路径冲突和二次确认校验。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

import yaml


SUPPORTED_METHODS = {"get", "post", "put", "patch", "delete", "head"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
MAX_OPENAPI_DOCUMENT_BYTES = 2 * 1024 * 1024
PATH_PARAM_PATTERN = re.compile(r"\{[^/{}]+\}")


class OpenApiImportError(ValueError):
    """OpenAPI/Swagger 文档不符合平台安全导入要求时抛出。"""


def _walk_for_remote_refs(value: object) -> Iterable[str]:
    """遍历规范图并拒绝 YAML 别名形成的对象环。"""
    active: set[int] = set()
    stack: list[tuple[object, bool]] = [(value, False)]
    while stack:
        current, leaving = stack.pop()
        if not isinstance(current, (dict, list)):
            continue
        object_id = id(current)
        if leaving:
            active.discard(object_id)
            continue
        if object_id in active:
            raise OpenApiImportError("接口文档包含循环对象，无法安全导入")
        active.add(object_id)
        stack.append((current, True))
        items = list(current.items()) if isinstance(current, dict) else list(enumerate(current))
        for key, item in reversed(items):
            if key == "$ref" and isinstance(item, str):
                yield item
            if isinstance(item, (dict, list)):
                stack.append((item, False))


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def document_digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def load_openapi_document(raw: bytes | str) -> object:
    """从 JSON/YAML/文本粘贴内容解析规范对象，并限制输入大小。"""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_OPENAPI_DOCUMENT_BYTES:
        raise OpenApiImportError("接口文档不能超过 2 MiB")
    text = data.decode("utf-8-sig")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        try:
            return yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError) as error:
            raise OpenApiImportError("接口文档必须是合法 JSON 或 YAML") from error


def _version_and_kind(payload: dict[str, object]) -> tuple[str, str]:
    openapi = payload.get("openapi")
    if isinstance(openapi, str) and (openapi.startswith("3.0") or openapi.startswith("3.1")):
        return openapi, "openapi3"
    swagger = payload.get("swagger")
    if swagger == "2.0":
        return "2.0", "swagger2"
    raise OpenApiImportError("仅支持 OpenAPI 3.0/3.1 或 Swagger 2.0")


def _validate_common_document(payload: object) -> tuple[dict[str, object], str, str]:
    if not isinstance(payload, dict):
        raise OpenApiImportError("接口文档必须是 JSON/YAML 对象")
    version, kind = _version_and_kind(payload)
    for ref in _walk_for_remote_refs(payload):
        if not ref.startswith("#/"):
            raise OpenApiImportError("不允许远程、文件系统或外部跳转 $ref")
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        raise OpenApiImportError("接口文档缺少 paths")
    return payload, version, kind


def _json_pointer_get(root: dict[str, object], pointer: str) -> object | None:
    """按 JSON Pointer 读取本地引用目标。"""
    if not pointer.startswith("#/"):
        return None
    current: object = root
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _resolve_local_refs(value: object, root: dict[str, object], *, seen: tuple[str, ...] = (), active: frozenset[int] = frozenset()) -> object:
    """展开本地引用；只拒绝当前路径上的循环，合法共享 schema 仍可重复使用。"""
    if isinstance(value, list):
        object_id = id(value)
        if object_id in active:
            raise OpenApiImportError("接口文档包含循环对象，无法安全展开")
        next_active = active | {object_id}
        return [_resolve_local_refs(item, root, seen=seen, active=next_active) for item in value]
    if not isinstance(value, dict):
        return value
    object_id = id(value)
    if object_id in active:
        raise OpenApiImportError("接口文档包含循环对象，无法安全展开")
    next_active = active | {object_id}

    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            raise OpenApiImportError("接口文档包含循环本地引用")
        target = _json_pointer_get(root, ref)
        if target is None:
            raise OpenApiImportError("接口文档包含不存在的本地引用")
        resolved = _resolve_local_refs(target, root, seen=(*seen, ref), active=next_active)
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings and isinstance(resolved, dict):
            sibling_resolved = {key: _resolve_local_refs(item, root, seen=seen, active=next_active) for key, item in siblings.items()}
            return {**resolved, **sibling_resolved}
        return resolved

    return {key: _resolve_local_refs(item, root, seen=seen, active=next_active) for key, item in value.items()}


def _normalize_path(path: str, base_path: str = "") -> str:
    if not path.startswith("/"):
        raise OpenApiImportError("存在无效接口路径")
    if "?" in path or "#" in path or "//" in path or ".." in path:
        raise OpenApiImportError("存在无效接口路径")
    normalized_base = "" if base_path in {"", "/"} else base_path.rstrip("/")
    if normalized_base:
        if not normalized_base.startswith("/") or "?" in normalized_base or "#" in normalized_base or ".." in normalized_base:
            raise OpenApiImportError("Swagger basePath 无效")
        path = f"{normalized_base}{path}"
    return path


def _operation_text(path: str, method: str, operation: dict[str, object]) -> str:
    values: list[str] = [path, method]
    for key in ("operationId", "summary", "description"):
        value = operation.get(key)
        if isinstance(value, str):
            values.append(value)
    tags = operation.get("tags")
    if isinstance(tags, list):
        values.extend(str(tag) for tag in tags if isinstance(tag, str))
    return " ".join(values).lower()


def _content_types_openapi3(operation: dict[str, object]) -> list[str]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return []
    content = request_body.get("content")
    if not isinstance(content, dict):
        return []
    return sorted(str(key).lower() for key in content.keys())


def _content_types_swagger2(payload: dict[str, object], path_item: dict[str, object], operation: dict[str, object]) -> list[str]:
    consumes = operation.get("consumes")
    if consumes is None:
        consumes = path_item.get("consumes")
    if consumes is None:
        consumes = payload.get("consumes")
    if isinstance(consumes, list):
        return sorted(str(item).lower() for item in consumes if isinstance(item, str))
    return []


def _group_name(operation: dict[str, object]) -> str:
    tags = operation.get("tags")
    if isinstance(tags, list) and tags:
        first = tags[0]
        return str(first)[:128] if isinstance(first, str) else ""
    return ""


def _risk_for_operation(path: str, method: str, operation: dict[str, object]) -> tuple[str, list[str], str, str]:
    text = _operation_text(path, method, operation)
    lower_path = path.lower()
    flags: list[str] = []
    status = "candidate"
    exclusion_reason = ""
    risk_level = "info"
    if any(marker in text for marker in ("dataset", "job", "task", "artifact", "file", "result", "log", "workspace", "project")):
        flags.append("resource_operation")
        risk_level = "medium"
    last_segment = lower_path.rstrip("/").rsplit("/", 1)[-1]
    if method == "get" and "{" not in lower_path and last_segment in {"datasets", "jobs", "tasks", "artifacts", "files", "results", "logs", "users"}:
        flags.append("global_list_endpoint")
        risk_level = "blocker"
        status = "blocked"
        exclusion_reason = "默认阻断全局资源列表接口"
    if any(marker in text for marker in ("internal", "private", "debug", "admin", "deprecated")):
        flags.append("internal_or_deprecated" if risk_level == "blocker" else "deprecated" if "deprecated" in text else "internal")
        risk_level = "blocker"
        status = "blocked"
        exclusion_reason = "默认阻断 internal/admin/private/debug/deprecated 接口"
    if operation.get("deprecated") is True and "deprecated" not in flags and risk_level != "blocker":
        flags.append("deprecated")
        risk_level = "blocker"
        status = "blocked"
        exclusion_reason = "默认阻断 deprecated 接口"
    if method == "delete":
        flags.append("destructive_method")
        risk_level = "blocker"
        status = "blocked"
        exclusion_reason = "默认阻断删除类高风险接口"
    if "{path" in path.lower() or PATH_PARAM_PATTERN.search(path) and path.count("{") >= 3:
        flags.append("broad_path_template")
        if risk_level in {"info", "low"}:
            risk_level = "medium"
    return risk_level, sorted(set(flags)), status, exclusion_reason


def _parameters_for(kind: str, path_item: dict[str, object], operation: dict[str, object], root: dict[str, object]) -> list[object]:
    parameters: list[object] = []
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if isinstance(source, list):
            parameters.extend(source)
    return _resolve_local_refs(parameters, root) if parameters else []


def _request_body_for(kind: str, operation: dict[str, object], root: dict[str, object]) -> object:
    if kind == "openapi3":
        request_body = operation.get("requestBody")
        return _resolve_local_refs(request_body, root) if isinstance(request_body, dict) else {}
    parameters = operation.get("parameters")
    if isinstance(parameters, list):
        body_params = [item for item in parameters if isinstance(item, dict) and item.get("in") in {"body", "formData"}]
        return {"parameters": _resolve_local_refs(body_params, root)} if body_params else {}
    return {}


def _responses_for(operation: dict[str, object], root: dict[str, object]) -> object:
    responses = operation.get("responses")
    return _resolve_local_refs(responses, root) if isinstance(responses, dict) else {}


def _operation_hash(operation: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(operation).encode("utf-8")).hexdigest()


def parse_openapi_document_detailed(payload: object, *, gateway_prefix: str = "") -> tuple[dict[str, object], list[dict[str, object]]]:
    """解析成管理员导入和同步差异需要的详细操作列表。"""
    try:
        payload, version, kind = _validate_common_document(payload)
        paths = payload["paths"]
        base_path = str(payload.get("basePath") or "") if kind == "swagger2" else ""
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        operations: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for raw_path, path_item in paths.items():  # type: ignore[union-attr]
            if not isinstance(raw_path, str):
                raise OpenApiImportError("存在无效接口路径")
            if not isinstance(path_item, dict):
                continue
            upstream_path = _normalize_path(raw_path, base_path)
            for method, operation in path_item.items():
                normalized = str(method).lower()
                if normalized not in SUPPORTED_METHODS:
                    if normalized in HTTP_METHODS:
                        raise OpenApiImportError(f"暂不支持 {normalized.upper()} 方法")
                    continue
                if not isinstance(operation, dict):
                    continue
                key = (normalized.upper(), upstream_path)
                if key in seen:
                    raise OpenApiImportError("接口文档存在重复操作")
                seen.add(key)
                content_types = _content_types_openapi3(operation) if kind == "openapi3" else _content_types_swagger2(payload, path_item, operation)
                risk_level, risk_flags, status, exclusion_reason = _risk_for_operation(upstream_path, normalized, operation)
                gateway_path = f"{gateway_prefix.rstrip('/')}{upstream_path}" if gateway_prefix else upstream_path
                operations.append(
                    {
                        "method": normalized.upper(),
                        "upstream_path": upstream_path,
                        "gateway_path": gateway_path,
                        "operation_id": operation.get("operationId") if isinstance(operation.get("operationId"), str) else None,
                        "summary": operation.get("summary") if isinstance(operation.get("summary"), str) else "",
                        "description": operation.get("description") if isinstance(operation.get("description"), str) else "",
                        "content_types": content_types,
                        "group_name": _group_name(operation),
                        "parameters": _parameters_for(kind, path_item, operation, payload),
                        "request_body": _request_body_for(kind, operation, payload),
                        "responses": _responses_for(operation, payload),
                        "risk_level": risk_level,
                        "risk_flags": risk_flags,
                        "status": status,
                        "exclusion_reason": exclusion_reason,
                        "operation_hash": _operation_hash(operation),
                    }
                )
        if not operations:
            raise OpenApiImportError("文档中没有可导入的 GET、POST 或 DELETE 操作")
        digest = document_digest(payload)
        metadata = {
            "version": version,
            "kind": kind,
            "sha256": digest,
            "title": str(info.get("title") or "")[:255],
            "description": str(info.get("description") or "")[:2_000],
            "operation_count": len(operations),
        }
        return metadata, operations
    except RecursionError as error:
        raise OpenApiImportError("接口文档结构过深，无法安全导入") from error
    except ValueError as error:
        if isinstance(error, OpenApiImportError):
            raise
        raise OpenApiImportError("接口文档包含无法安全处理的循环结构") from error


def parse_openapi_document(payload: object) -> tuple[str, str, list[dict[str, object]]]:
    """向后兼容的简化解析接口，保留既有单元测试和调用方返回结构。"""
    metadata, detailed = parse_openapi_document_detailed(payload)
    operations = [
        {
            "method": item["method"],
            "upstream_path": item["upstream_path"],
            "operation_id": item["operation_id"],
            "summary": item["summary"],
            "description": item["description"],
            "content_types": item["content_types"],
        }
        for item in detailed
    ]
    return str(metadata["version"]), str(metadata["sha256"]), operations
