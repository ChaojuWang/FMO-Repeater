#!/usr/bin/env bash
# FMO Repeater 一键测试入口
#
# 用法:
#   ./run_tests.sh                        # 运行全部单元测试（不含集成）
#   ./run_tests.sh --integration          # 追加真实 MQTT 集成测试
#   ./run_tests.sh -q                     # 安静模式
#   ./run_tests.sh tests/test_codec_radpcm.py   # 运行指定测试
#
# 集成测试凭据（按优先级）:
#   1. 环境变量 FMO_TEST_BROKER=host:port:user:pass
#   2. 仓库根 config.yaml 的 mqtt 节
#   两者皆无时集成用例自动 skip。
#
# 依赖: python3 + pytest（缺失时提示安装命令后退出）

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
    echo "❌ 未安装 pytest" >&2
    echo "   安装方式: pip install -r requirements-dev.txt" >&2
    exit 1
fi

if ! "$PYTHON" -c "import paho.mqtt, yaml" >/dev/null 2>&1; then
    echo "❌ 缺少运行依赖 (paho-mqtt / PyYAML)" >&2
    echo "   安装方式: pip install -r requirements.txt" >&2
    exit 1
fi

# OPUS 为可选依赖，缺失时相关测试自动 skip（不算失败）
if ! "$PYTHON" -c "import opuslib" >/dev/null 2>&1; then
    echo "⚠️  opuslib 未安装，OPUS 测试将跳过（pip install opuslib 可启用）"
fi

# --integration: 追加 integration marker（pytest.ini 默认排除 not integration）
EXTRA_OPTS=()
if [[ "${1:-}" == "--integration" ]]; then
    shift
    EXTRA_OPTS+=(-m "not integration or integration")
    echo "🔗 已启用真实 MQTT 集成测试（凭据: FMO_TEST_BROKER 或 config.yaml）"
fi

echo "=========================================="
echo " FMO Repeater 测试套件 (pytest)"
echo "=========================================="
exec "$PYTHON" -m pytest tests/ -v "${EXTRA_OPTS[@]}" "$@"
