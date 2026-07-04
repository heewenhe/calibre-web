#!/bin/bash
# CWA 裸机启动脚本
set -e

# 关键路径
export CWA_HOME=/opt/cwa-data
export CWA_SRC=/opt/cwa
export VENV=/opt/cwa/venv

# 把 scripts 目录加入 PYTHONPATH（cwa_db 模块在此）
export PYTHONPATH="$CWA_SRC:$CWA_SRC/scripts:$CWA_SRC/cps:${PYTHONPATH:-}"

# CWA 自身路径
export CALIBRE_PORT=8083
export CALIBRE_DBPATH=$CWA_HOME/config
export CACHE_DIRECTORY=$CWA_HOME/cache
export HOME=$CWA_HOME
export CALIBRE_CONFIG_DIR=$CWA_HOME/config/.config/calibre

# 创建必要目录
mkdir -p \
  $CWA_HOME/config \
  $CWA_HOME/library \
  $CWA_HOME/ingest \
  $CWA_HOME/cache \
  $CWA_HOME/logs \
  $CWA_HOME/metadata_change_logs \
  $CWA_HOME/metadata_temp \
  $CWA_HOME/config/.config/calibre

# Calibre 二进制路径
export PATH="/usr/bin:$PATH"

cd $CWA_SRC
exec $VENV/bin/python cps.py
