# -*- coding: utf-8 -*-
"""在线升级模块测试：本地 mock 服务器模拟 GitHub Releases API。"""
import json
import os
import sys
import threading
import http.server
import functools

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
MOCK = os.path.join(HERE, "mock_server")
os.makedirs(MOCK, exist_ok=True)

# 假的新版本安装包（小文件即可，测试下载链路）
with open(os.path.join(MOCK, "视频水印擦除工具_Setup_v9.9.9.exe"), "wb") as f:
    f.write(b"FAKE-SETUP" * 20000)  # ~200KB

PORT = 18923
RELEASE = {
    "tag_name": "v9.9.9",
    "body": "更新内容：\n- 测试升级链路",
    "assets": [{
        "name": "视频水印擦除工具_Setup_v9.9.9.exe",
        "browser_download_url": f"http://127.0.0.1:{PORT}/视频水印擦除工具_Setup_v9.9.9.exe",
        "size": 200000,
    }],
}
with open(os.path.join(MOCK, "latest.json"), "w", encoding="utf-8") as f:
    json.dump(RELEASE, f, ensure_ascii=False)

os.environ["WATERMARK_UPDATE_API"] = f"http://127.0.0.1:{PORT}/latest.json"

import main  # noqa: E402  （须在设置 env 之后导入）

# ---- 单元：版本比较 ----
assert main.parse_version("v1.4.0") == (1, 4, 0)
assert main.parse_version("1.10.0") > main.parse_version("v1.9.9")
assert main.parse_version("") == (0, 0, 0)
print("版本比较 OK")

# ---- 启动 mock 服务器 ----
handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                            directory=MOCK)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# ---- 获取 release + 挑资产 ----
rel = main.fetch_latest_release()
assert rel["tag_name"] == "v9.9.9"
asset = main.pick_asset(rel)
assert asset and "Setup" in asset["name"]
print("release 获取 + 资产选择 OK")

# ---- 下载 ----
dst = os.path.join(MOCK, "downloaded.exe")
pcts = []
main.download_file(asset["browser_download_url"], dst,
                   progress_cb=pcts.append)
assert os.path.getsize(dst) == os.path.getsize(
    os.path.join(MOCK, "视频水印擦除工具_Setup_v9.9.9.exe"))
assert pcts and pcts[-1] == 100
print("下载 OK，进度回调", len(pcts), "次")

# ---- 升级脚本 ----
bat = main.build_update_bat(dst, r"C:\Users\a\AppData\Local\Programs\videotools",
                            r"C:\...\videotools\视频水印擦除工具.exe")
content = open(bat, encoding="gbk").read()
assert "/S" in content and '/D="' in content
print("升级 bat OK")

# ---- GUI offscreen：检查更新全流程 ----
from PyQt5.QtWidgets import QApplication, QMessageBox

QMessageBox.exec_ = lambda self: 0
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

app = QApplication.instance() or QApplication(sys.argv)

# 直接实例化主窗口走 check_update
from PyQt5.QtCore import QTimer

wins = []


def go():
    import main as m
    # 通过 gui_main 的内部类不可达，改为模拟：直接调用函数级流程
    rel2 = m.fetch_latest_release()
    latest = m.parse_version(rel2["tag_name"])
    current = m.parse_version(m.APP_VERSION)
    assert latest > current, "应检测到新版本"
    a = m.pick_asset(rel2)
    d2 = os.path.join(MOCK, "dl2.exe")
    m.download_file(a["browser_download_url"], d2)
    print("GUI 流程等效路径 OK：检测到新版本并可下载")
    app.quit()


QTimer.singleShot(200, go)
app.exec_()

srv.shutdown()
import shutil as _sh
_sh.rmtree(MOCK, ignore_errors=True)
print("UPDATER TEST DONE")
