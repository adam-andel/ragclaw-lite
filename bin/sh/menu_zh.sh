#!/usr/bin/env bash
# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# RAGClaw 控制菜单（中文版，适用于 macOS / Linux）
# 用法: bash bin/sh/menu_zh.sh
#
# 本脚本是 bin/sh/menu.sh 的中文版本，菜单说明全部使用中文。
# 与 menu.sh 不同的是：本脚本会在调用子脚本时统一带上三个镜像源参数，
# 通过脚本顶部下方的 SOURCE_* 变量指定，把原来的 3 个源（registry / apt / pypi）
# 全部传入底层的 start.sh / backend.sh，无需每次手动输入。
#
# 三个镜像源默认留空（即使用官方源）。如需使用镜像，请修改下方
# SOURCE_REGISTRY / SOURCE_APT / SOURCE_PYPI 三个变量即可。空字符串表示
# 使用官方默认源（docker.io / 发行版官方源 / pypi.org）。
#
# 示例（使用清华镜像）：
#   SOURCE_REGISTRY="docker.m.daocloud.io"
#   SOURCE_APT="mirrors.tuna.tsinghua.edu.cn"
#   SOURCE_PYPI="https://pypi.tuna.tsinghua.edu.cn/simple"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- 三个镜像源（全部传入底层脚本；留空即官方源）----
SOURCE_REGISTRY="docker.1ms.run"   # Docker 基础镜像仓库域名（FROM ${REGISTRY}/...），空 -> docker.io（官方源）。注意: docker.m.daocloud.io 在部分环境拉取官方镜像会报 credentials 错误，若遇此问题请留空或换其它可用代理。
SOURCE_APT="mirrors.tuna.tsinghua.edu.cn"        # Debian apt 镜像（主机名即可，带 https:// 也可，Dockerfile 会自动剥离），空 -> 发行版官方源
SOURCE_PYPI="https://pypi.tuna.tsinghua.edu.cn/simple"       # pip 安装的 PyPI 镜像地址，空 -> 官方 pypi.org

# 把三个源拼装成参数数组（仅在非空时添加对应参数）
build_source_args() {
  SRC_ARGS=()
  [ -n "$SOURCE_REGISTRY" ] && SRC_ARGS+=( --registry "$SOURCE_REGISTRY" )
  [ -n "$SOURCE_APT" ]       && SRC_ARGS+=( --apt "$SOURCE_APT" )
  [ -n "$SOURCE_PYPI" ]      && SRC_ARGS+=( --pypi "$SOURCE_PYPI" )
}

while true; do
  echo
  echo "  ==================================="
  echo "    RAGClaw-Lite 控制菜单"
  echo "  ==================================="
  echo
  echo "    当前镜像源设置："
  echo "      registry : ${SOURCE_REGISTRY:-<官方源 docker.io>}"
  echo "      apt      : ${SOURCE_APT:-<发行版官方源>}"
  echo "      pypi     : ${SOURCE_PYPI:-<官方源 pypi.org>}"
  echo
  echo "    [1] 启动全部（生产环境）"
  echo "        -> 构建镜像（ragclaw mcp-repl ragclaw-egress nginx）+ 启动容器"
  echo "    [2] 重新加载全部（生产环境）"
  echo "        -> 仅重建容器（compose up -d --force-recreate），不重新构建镜像"
  echo "    [3] 启动全部（开发环境：热更新 HMR + --reload）"
  echo "        -> 构建全部镜像（含 frontend-dev）+ 启动容器 [dev 叠加层]"
  echo "    [4] 重新加载全部（开发环境）"
  echo "        -> 仅重建容器（不重新构建镜像）[dev 叠加层]"
  echo "    [5] 停止全部"
  echo "        -> 暂停所有容器（镜像与数据卷保留）"
  echo "    [6] 查看状态"
  echo "        -> 列出服务 / 对外端口 / 健康状态"
  echo "    [7] 仅后端（生产环境）"
  echo "        -> 仅构建并启动后端服务（ragclaw）"
  echo "    [8] 仅后端（开发环境）"
  echo "        -> 仅构建并启动后端服务（dev：目录挂载 + uvicorn --reload）"
  echo "    [0] 退出"
  echo
  printf "请选择: "
  read -r choice
  case "$choice" in
    1) build_source_args; bash "$SCRIPT_DIR/start.sh" "${SRC_ARGS[@]}" start ;;
    2) build_source_args; bash "$SCRIPT_DIR/start.sh" "${SRC_ARGS[@]}" reload ;;
    3) build_source_args; bash "$SCRIPT_DIR/start.sh" --dev "${SRC_ARGS[@]}" start ;;
    4) build_source_args; bash "$SCRIPT_DIR/start.sh" --dev "${SRC_ARGS[@]}" reload ;;
    5) bash "$SCRIPT_DIR/start.sh" stop ;;
    6) bash "$SCRIPT_DIR/start.sh" status ;;
    7) build_source_args; bash "$SCRIPT_DIR/backend.sh" "${SRC_ARGS[@]}" start ;;
    8) build_source_args; bash "$SCRIPT_DIR/backend.sh" --dev "${SRC_ARGS[@]}" start ;;
    0) echo "再见。"; exit 0 ;;
    *) echo "无效的选择。" ;;
  esac
done
