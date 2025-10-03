#!/usr/bin/env bash
set -e
DEFAULT_VENV_NAME=".venv"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH=""
PYTHON_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    -e|--venv)
      VENV_PATH="$2"
      shift
      shift
      ;;
    *)
      PYTHON_ARGS+=("$1")
      shift
      ;;
  esac
done
if [ -z "$VENV_PATH" ]; then
  VENV_PATH="$SCRIPT_DIR/$DEFAULT_VENV_NAME"
else
  if [[ ! "$VENV_PATH" = /* ]]; then
    VENV_PATH="$(pwd)/$VENV_PATH"
  fi
fi
if [ ! -d "$VENV_PATH" ] || [ ! -f "$VENV_PATH/bin/python" ]; then
  echo "错误：虚拟环境 $VENV_PATH 未找到或无效。请先创建该虚拟环境或传入正确的虚拟环境路径参数"
  exit 1
fi
PYTHON_EXEC="$VENV_PATH/bin/python"
"$PYTHON_EXEC" "$SCRIPT_DIR/scroll_stitch.py" "${PYTHON_ARGS[@]}"
