export type DeveloperRoutePolicy = {
  route_version: number;
  allowed_content_types: string[];
  max_request_bytes: number;
  max_response_bytes: number;
  request_timeout_seconds: number;
  allow_stream_upload: boolean;
  allow_stream_download: boolean;
  access_mode: "authenticated" | "owner" | "shared" | "admin_only";
  resource_policy: Record<string, unknown>;
  policy_version: number;
  require_idempotency_key: boolean;
  allow_retry: boolean;
  risk_level: string;
  rate_limit_per_minute?: number;
};

export type DeveloperEndpointDoc = {
  id: string;
  tool_slug: string;
  tool_name: string;
  method: string;
  gateway_path: string;
  operation_id: string | null;
  summary: string;
  description: string;
  content_types: string[];
  parameters: unknown[];
  request_body: Record<string, unknown>;
  responses: Record<string, unknown>;
  route_policy: DeveloperRoutePolicy;
};

export type DeveloperToolDoc = {
  id: string;
  slug: string;
  name: string;
  description: string;
  gateway_base_path: string;
  endpoints: DeveloperEndpointDoc[];
};

export type ErrorCodeGuide = {
  http_status: number;
  code: string;
  message: string;
};

export type DeveloperApiDocs = {
  gateway_base_url: string;
  gateway_pattern: string;
  auth_header: string;
  tools: DeveloperToolDoc[];
  total_tools: number;
  total_endpoints: number;
  error_codes: ErrorCodeGuide[];
};

export function formatBytes(value: number) {
  if (!value) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

export function methodClass(method: string) {
  return method === "GET" ? "status info" : method === "DELETE" ? "status warn" : "status good";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function firstJsonContent(content: unknown): Record<string, unknown> {
  const contentMap = asRecord(content);
  const jsonKey = Object.keys(contentMap).find((key) => key.toLowerCase().includes("json")) || Object.keys(contentMap)[0];
  return jsonKey ? asRecord(contentMap[jsonKey]) : {};
}

function schemaType(schema: Record<string, unknown>): string {
  const explicitType = schema.type;
  if (Array.isArray(explicitType)) return explicitType.map(String).join(" | ");
  if (typeof explicitType === "string") {
    if (explicitType === "array") {
      const itemType = schemaType(asRecord(schema.items));
      return itemType ? `array<${itemType}>` : "array";
    }
    return explicitType;
  }
  const allOf = asArray(schema.allOf).map((item) => schemaType(asRecord(item))).filter(Boolean);
  if (allOf.length) return allOf.join(" & ");
  const oneOf = asArray(schema.oneOf).map((item) => schemaType(asRecord(item))).filter(Boolean);
  if (oneOf.length) return oneOf.join(" | ");
  const anyOf = asArray(schema.anyOf).map((item) => schemaType(asRecord(item))).filter(Boolean);
  if (anyOf.length) return anyOf.join(" | ");
  if (Object.keys(asRecord(schema.properties)).length) return "object";
  if (Object.keys(asRecord(schema.items)).length) return "array";
  return typeof schema.$ref === "string" ? "object" : "";
}

function mergedCompositionExample(items: unknown[]): unknown {
  const merged: Record<string, unknown> = {};
  for (const item of items) {
    const example = schemaExample(item);
    if (example && typeof example === "object" && !Array.isArray(example)) {
      Object.assign(merged, example);
    }
  }
  return Object.keys(merged).length ? merged : schemaExample(items[0]);
}

function schemaExample(schema: unknown): unknown {
  const item = asRecord(schema);
  if ("example" in item) return item.example;
  if ("default" in item) return item.default;
  const enumValues = asArray(item.enum);
  if (enumValues.length) return enumValues[0];
  const allOf = asArray(item.allOf);
  if (allOf.length) return mergedCompositionExample(allOf);
  const oneOf = asArray(item.oneOf);
  if (oneOf.length) return schemaExample(oneOf[0]);
  const anyOf = asArray(item.anyOf);
  if (anyOf.length) return schemaExample(anyOf[0]);
  if (typeof item.$ref === "string") {
    return {
      $ref: item.$ref,
      说明: "本地 schema 引用尚未展开，请管理员重新导入接口文档。",
    };
  }
  const type = String(item.type || "");
  if (type === "string") return "string";
  if (type === "integer" || type === "number") return 0;
  if (type === "boolean") return true;
  if (type === "array") return [schemaExample(item.items)];
  const properties = asRecord(item.properties);
  if (type === "object" || Object.keys(properties).length) {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(properties).slice(0, 8)) {
      result[key] = schemaExample(value);
    }
    return Object.keys(result).length ? result : {};
  }
  return {};
}

function requestBodySchema(endpoint: DeveloperEndpointDoc): Record<string, unknown> {
  const body = asRecord(endpoint.request_body);
  const content = firstJsonContent(body.content);
  if (Object.keys(content).length) return asRecord(content.schema);
  const parameters = asArray(body.parameters).map(asRecord);
  const bodyParam = parameters.find((item) => item.in === "body" || item.in === "formData");
  return bodyParam ? asRecord(bodyParam.schema || bodyParam) : {};
}

export function requestExample(endpoint: DeveloperEndpointDoc): unknown | null {
  const body = asRecord(endpoint.request_body);
  const content = firstJsonContent(body.content);
  if (Object.keys(content).length) {
    if ("example" in content) return content.example;
    return schemaExample(content.schema);
  }
  const parameters = asArray(body.parameters).map(asRecord);
  const bodyParam = parameters.find((item) => item.in === "body" || item.in === "formData");
  if (bodyParam) return schemaExample(bodyParam.schema || bodyParam);
  return null;
}

export function responseExample(endpoint: DeveloperEndpointDoc): unknown {
  const responses = asRecord(endpoint.responses);
  const preferredKey = ["200", "201", "default"].find((key) => key in responses) || Object.keys(responses)[0];
  const selected = asRecord(preferredKey ? responses[preferredKey] : {});
  const content = firstJsonContent(selected.content);
  if (Object.keys(content).length) {
    if ("example" in content) return content.example;
    return schemaExample(content.schema);
  }
  if ("description" in selected) return { message: selected.description };
  return { message: "上游未在 OpenAPI/Swagger 中提供响应示例" };
}

export function parameterRows(endpoint: DeveloperEndpointDoc) {
  const explicitRows = asArray(endpoint.parameters)
    .map(asRecord)
    .map((item) => ({
      name: String(item.name || ""),
      location: String(item.in || ""),
      required: Boolean(item.required),
      type: schemaType(asRecord(item.schema)) || String(item.type || ""),
      description: String(item.description || ""),
    }))
    .filter((item) => item.name);
  const schema = requestBodySchema(endpoint);
  const required = new Set(asArray(schema.required).map(String));
  const properties = asRecord(schema.properties);
  const bodyRows = Object.entries(properties).map(([name, value]) => {
    const field = asRecord(value);
    return {
      name,
      location: "body",
      required: required.has(name),
      type: schemaType(field),
      description: String(field.description || field.title || ""),
    };
  });
  if (!bodyRows.length && Object.keys(schema).length) {
    bodyRows.push({
      name: "<body>",
      location: "body",
      required: Boolean(asRecord(endpoint.request_body).required),
      type: schemaType(schema),
      description: String(schema.description || schema.title || "请求体结构请参考下方示例。"),
    });
  }
  return [...explicitRows, ...bodyRows];
}

export function endpointUrl(baseUrl: string, endpoint: DeveloperEndpointDoc | null) {
  if (!endpoint) return `${baseUrl}/gateway/{tool_slug}/...`;
  return `${baseUrl}/gateway/${endpoint.tool_slug}${endpoint.gateway_path}`;
}

export function endpointContentTypes(endpoint: DeveloperEndpointDoc | null) {
  if (!endpoint) return [];
  return endpoint.route_policy.allowed_content_types.length ? endpoint.route_policy.allowed_content_types : endpoint.content_types;
}

function hasJsonBody(endpoint: DeveloperEndpointDoc | null) {
  if (!endpoint || endpoint.method === "GET" || endpoint.method === "DELETE") return false;
  return endpointContentTypes(endpoint).some((item) => item.toLowerCase().includes("application/json")) || requestExample(endpoint) !== null;
}

function exampleJson(endpoint: DeveloperEndpointDoc | null) {
  if (!endpoint) return "";
  const example = requestExample(endpoint);
  return example === null ? "" : JSON.stringify(example, null, 2);
}

export function buildCurl(baseUrl: string, authHeader: string, endpoint: DeveloperEndpointDoc | null) {
  const method = endpoint?.method ?? "GET";
  const url = endpointUrl(baseUrl, endpoint);
  const jsonBody = hasJsonBody(endpoint);
  const body = exampleJson(endpoint);
  return [
    "curl",
    method === "GET" ? "" : `-X ${method}`,
    `"${url}"`,
    `-H "${authHeader}: agw_xxx"`,
    jsonBody ? `-H "Content-Type: application/json"` : "",
    jsonBody && body ? `-d '${body.replaceAll("'", "'\\''")}'` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export function buildPython(baseUrl: string, authHeader: string, endpoint: DeveloperEndpointDoc | null) {
  const method = endpoint?.method ?? "GET";
  const url = endpointUrl(baseUrl, endpoint);
  const jsonBody = hasJsonBody(endpoint);
  const body = exampleJson(endpoint);
  return `import os
import requests

api_key = os.environ["API_GATEWAY_KEY"]
response = requests.request(
    "${method}",
    "${url}",
    headers={"${authHeader}": api_key${jsonBody ? ', "Content-Type": "application/json"' : ""}},
    timeout=${endpoint?.route_policy.request_timeout_seconds || 60}${jsonBody && body ? `,
    json=${body.replace(/\btrue\b/g, "True").replace(/\bfalse\b/g, "False").replace(/\bnull\b/g, "None")}` : ""},
)

request_id = response.headers.get("x-request-id")
if response.status_code >= 400:
    raise SystemExit(f"调用失败 status={response.status_code} request_id={request_id} body={response.text}")

print(response.text)`;
}

export function buildJavaScript(baseUrl: string, authHeader: string, endpoint: DeveloperEndpointDoc | null) {
  const method = endpoint?.method ?? "GET";
  const url = endpointUrl(baseUrl, endpoint);
  const jsonBody = hasJsonBody(endpoint);
  const body = exampleJson(endpoint);
  return `const apiKey = process.env.API_GATEWAY_KEY;
const response = await fetch("${url}", {
  method: "${method}",
  headers: {
    "${authHeader}": apiKey${jsonBody ? ',\n    "Content-Type": "application/json"' : ""}
  }${jsonBody && body ? `,
  body: JSON.stringify(${body})` : ""}
});

const requestId = response.headers.get("x-request-id");
const text = await response.text();
if (!response.ok) {
  throw new Error(\`Gateway 调用失败 status=\${response.status} request_id=\${requestId} body=\${text}\`);
}

console.log(text);`;
}
