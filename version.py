# -*- coding: utf-8 -*-
"""全局唯一版本信息：所有脚本/安装包/更新检测都从这里取，改版本只改这里。

同时提供 scrub_bootloader_env()，供主程序与安装程序两个入口共用。
"""
import os
import sys

APP_NAME = "视频水印擦除工具"
APP_VERSION = "1.5.6"

# 在线升级：GitHub Releases 仓库
UPDATE_REPO = "retision-coder/video-tools"


def scrub_bootloader_env():
    """清除 PyInstaller onefile 注入的 _PYI* 环境变量。

    打包版运行时，引导进程会把 _PYI_ARCHIVE_FILE 等变量留在进程环境块里；
    若不清理，经 cmd/bat/资源管理器等中间进程启动的本程序（或其他
    PyInstaller 程序）会误以为自己是「父程序派生的子进程」，触发
    PyInstaller >= 6.22.1 的安全校验误报
    "Security validation failure: parent process has different executable!"。
    在入口清理一次，之后派生的所有子进程继承的都是干净环境；
    仅影响此后新建的子进程，对当前进程无害。
    """
    if not getattr(sys, "frozen", False):
        return
    for k in [k for k in os.environ if k.startswith("_PYI")]:
        del os.environ[k]
