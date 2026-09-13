import pytest

from app.services.openapi_import import OpenApiImportError, load_openapi_document, parse_openapi_document, parse_openapi_document_detailed


def test_parse_supported_openapi_operations() -> None:
    version, digest, operations = parse_openapi_document(
        {
            "openapi": "3.1.0",
            "paths": {
                "/api/v1/jobs/{job_id}": {
                    "get": {"operationId": "getJob", "summary": "查询任务", "description": "查询当前用户可访问的单个任务。"},
                    "post": {"operationId": "unsupportedUpdate"},
                    "delete": {"operationId": "deleteJob"},
                },
            },
        }
    )
    assert version == "3.1.0"
    assert len(digest) == 64
    assert operations == [
        {
            "method": "GET",
            "upstream_path": "/api/v1/jobs/{job_id}",
            "operation_id": "getJob",
            "summary": "查询任务",
            "description": "查询当前用户可访问的单个任务。",
            "content_types": [],
        },
        {"method": "POST", "upstream_path": "/api/v1/jobs/{job_id}", "operation_id": "unsupportedUpdate", "summary": "", "description": "", "content_types": []},
        {"method": "DELETE", "upstream_path": "/api/v1/jobs/{job_id}", "operation_id": "deleteJob", "summary": "", "description": "", "content_types": []},
    ]


def test_support_put_patch_head_and_reject_trace() -> None:
    _, _, operations = parse_openapi_document(
        {
            "openapi": "3.1.0",
            "paths": {
                "/api/v1/jobs": {
                    "put": {"summary": "整体更新"},
                    "patch": {"summary": "局部更新"},
                    "head": {"summary": "检查资源"},
                }
            },
        }
    )
    assert [item["method"] for item in operations] == ["PUT", "PATCH", "HEAD"]
    with pytest.raises(OpenApiImportError, match="TRACE"):
        parse_openapi_document({"openapi": "3.1.0", "paths": {"/api/v1/jobs": {"trace": {"summary": "不导入"}}}})


def test_reject_remote_reference() -> None:
    with pytest.raises(OpenApiImportError):
        parse_openapi_document({"openapi": "3.0.3", "paths": {"/health": {"get": {"responses": {"200": {"$ref": "https://evil.example/schema"}}}}}})


def test_parse_swagger2_yaml_document() -> None:
    document = load_openapi_document(
        """
swagger: "2.0"
info:
  title: Demo API
basePath: /api/v1
consumes:
  - application/json
paths:
  /datasets:
    post:
      operationId: createDataset
      summary: 创建数据集
      responses:
        "200":
          description: ok
"""
    )
    version, digest, operations = parse_openapi_document(document)
    assert version == "2.0"
    assert len(digest) == 64
    assert operations[0]["method"] == "POST"
    assert operations[0]["upstream_path"] == "/api/v1/datasets"
    assert operations[0]["content_types"] == ["application/json"]


def test_openapi_description_is_preserved_for_portal_docs() -> None:
    metadata, operations = parse_openapi_document_detailed(
        {
            "openapi": "3.1.0",
            "info": {"title": "说明测试"},
            "paths": {
                "/api/v1/datasets": {
                    "post": {
                        "operationId": "createDataset",
                        "summary": "Create Dataset",
                        "description": "创建新数据集。",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    assert metadata["operation_count"] == 1
    assert operations[0]["summary"] == "Create Dataset"
    assert operations[0]["description"] == "创建新数据集。"


def test_local_schema_refs_are_expanded_for_portal_examples() -> None:
    metadata, operations = parse_openapi_document_detailed(
        {
            "openapi": "3.1.0",
            "info": {"title": "引用展开测试"},
            "paths": {
                "/api/v1/jobs/lcurve": {
                    "post": {
                        "operationId": "createLcurve",
                        "summary": "Create Lcurve",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/LcurveFormalRequest"},
                                }
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "created",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/JobRecord"},
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "LcurveFormalRequest": {
                        "type": "object",
                        "required": ["dataset_id"],
                        "properties": {
                            "dataset_id": {"type": "string", "description": "数据集 ID"},
                            "max_workers": {"type": "integer", "description": "最大并发数"},
                        },
                    },
                    "JobRecord": {
                        "type": "object",
                        "properties": {
                            "job_id": {"type": "string", "description": "任务 ID"},
                            "status": {"type": "string", "description": "任务状态"},
                        },
                    },
                }
            },
        }
    )
    assert metadata["operation_count"] == 1
    request_schema = operations[0]["request_body"]["content"]["application/json"]["schema"]  # type: ignore[index]
    response_schema = operations[0]["responses"]["201"]["content"]["application/json"]["schema"]  # type: ignore[index]
    assert "$ref" not in request_schema
    assert request_schema["properties"]["dataset_id"]["description"] == "数据集 ID"
    assert response_schema["properties"]["job_id"]["description"] == "任务 ID"


def test_direct_self_reference_is_rejected_without_recursion_error() -> None:
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}},
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {"schemas": {"Node": {"$ref": "#/components/schemas/Node"}}},
    }
    with pytest.raises(OpenApiImportError, match="循环"):
        parse_openapi_document_detailed(document)


def test_mutual_local_references_are_rejected() -> None:
    document = {
        "openapi": "3.1.0",
        "paths": {"/items": {"get": {"responses": {"200": {"$ref": "#/components/responses/A"}}}}},
        "components": {
            "responses": {
                "A": {"description": "a", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/B"}}}},
            },
            "schemas": {"B": {"type": "object", "properties": {"next": {"$ref": "#/components/responses/A"}}}},
        },
    }
    with pytest.raises(OpenApiImportError, match="循环"):
        parse_openapi_document_detailed(document)


def test_invalid_local_reference_is_rejected() -> None:
    document = {
        "openapi": "3.1.0",
        "paths": {"/items": {"get": {"responses": {"200": {"$ref": "#/components/responses/Missing"}}}}},
    }
    with pytest.raises(OpenApiImportError, match="不存在"):
        parse_openapi_document_detailed(document)


def test_shared_nested_schema_is_not_mistaken_for_a_cycle() -> None:
    shared = {"type": "string", "minLength": 1}
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"a": shared, "b": shared}}}}},
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    _, operations = parse_openapi_document_detailed(document)
    schema = operations[0]["request_body"]["content"]["application/json"]["schema"]
    assert schema["properties"]["a"] == schema["properties"]["b"]


def test_large_legal_schema_is_accepted() -> None:
    properties = {f"field_{index}": {"type": "string"} for index in range(1_000)}
    document = {
        "openapi": "3.1.0",
        "paths": {"/large": {"post": {"requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": properties}}}}}}},
    }
    _, operations = parse_openapi_document_detailed(document)
    assert len(operations[0]["request_body"]["content"]["application/json"]["schema"]["properties"]) == 1_000


def test_internal_and_deprecated_operations_are_blocked_by_default() -> None:
    metadata, operations = parse_openapi_document_detailed(
        {
            "openapi": "3.1.0",
            "info": {"title": "治理测试"},
            "paths": {
                "/api/v1/admin/users": {"get": {"summary": "内部管理员接口"}},
                "/api/v1/old": {"get": {"summary": "旧接口", "deprecated": True}},
                "/api/v1/jobs/{job_id}": {"delete": {"summary": "删除任务"}},
            },
        }
    )
    assert metadata["kind"] == "openapi3"
    by_path = {item["upstream_path"]: item for item in operations}
    assert by_path["/api/v1/admin/users"]["status"] == "blocked"
    assert by_path["/api/v1/old"]["status"] == "blocked"
    assert by_path["/api/v1/jobs/{job_id}"]["risk_level"] == "blocker"
    assert by_path["/api/v1/jobs/{job_id}"]["status"] == "blocked"
    assert "destructive_method" in by_path["/api/v1/jobs/{job_id}"]["risk_flags"]


def test_global_list_and_resource_operations_are_flagged() -> None:
    metadata, operations = parse_openapi_document_detailed(
        {
            "openapi": "3.1.0",
            "info": {"title": "资源治理"},
            "paths": {
                "/api/v1/jobs": {"get": {"summary": "列出全部任务"}},
                "/api/v1/jobs/{job_id}": {"get": {"summary": "查询任务"}},
            },
        }
    )
    assert metadata["operation_count"] == 2
    by_path = {item["upstream_path"]: item for item in operations}
    assert by_path["/api/v1/jobs"]["status"] == "blocked"
    assert "global_list_endpoint" in by_path["/api/v1/jobs"]["risk_flags"]
    assert "resource_operation" in by_path["/api/v1/jobs/{job_id}"]["risk_flags"]
