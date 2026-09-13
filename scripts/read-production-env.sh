#!/usr/bin/env bash

# 安全读取单个生产配置项，不执行配置文件中的任何内容。
production_env_value() {
  local key="$1"
  local file="${2:-.env.production}"
  local line value

  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || return 2
  [[ -r "$file" ]] || return 1
  line="$(awk -v wanted="$key" 'index($0, wanted "=") == 1 { value = substr($0, length(wanted) + 2) } END { if (value != "") print value }' "$file")"
  [[ -n "$line" ]] || return 1
  value="${line%$'\r'}"

  # 支持 Compose 常见的整值单引号或双引号写法，保留值内部的特殊字符。
  if [[ "${value:0:1}" == "\"" && "${value: -1}" == "\"" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}
