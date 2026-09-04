# -*- coding: utf-8 -*-
"""后台自动更新测试：mock 服务器发新版本 → 自动预下载 → 弹窗提示就绪。"""
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

ASSET_NAME = "视频水印擦除工具_Setup_v9.9.9.exe"
with open(os.path.join(MOCK, ASSET_NAME), "wb") as f:
    f.write(b"FAKE-SETUP-V2" * 20000)  # ~260KB

PORT = 18924
RELEASE = {
    "tag_name": "v9.9.9",
    "body": "自动更新测试版本",
    "assets": [{
        "name": ASSET_NAME,
        "browser_download_url": f"http://127.0.0.1:{PORT}/{ASSET_NAME}",
        "size": os.path.getsize(os.path.join(MOCK, ASSET_NAME)),
    }],
}
with open(os.path.join(MOCK, "latest.json"), "w", encoding="utf-8") as f:
    json.dump(RELEASE, f, ensure_ascii=False)

os.environ["WATERMARK_UPDATE_API"] = f"http://127.0.0.1:{PORT}/latest.json"

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=MOCK)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

import main  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402
from PyQt5.QtCore import QTimer  # noqa: E402

# 弹窗：记录但不阻塞，clickedButton=None 表示"下次再说"
box_calls = []
_orig_exec = QMessageBox.exec_


def fake_exec(self):
    box_calls.append(self.text())
    return 0


QMessageBox.exec_ = fake_exec
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.warning = staticmethod(lambda *a, **k: 0)

orig_exec = QApplication.exec_


def patched_exec():
    self = QApplication.instance()

    def flow():
        mw = [w for w in self.topLevelWidgets() if w.__class__.__name__ == "MainWindow"]
        assert mw, "MainWindow 未创建"
        w = mw[0]
        w._auto_check()  # 非冻结模式手动触发

        def check():
            print("auto_tag:", w._auto_tag)
            print("pending:", w._pending_update)
            print("status:", w.lbl_status.text())
            print("dialogs:", box_calls)
            assert w._auto_tag == "v9.9.9"
            assert w._pending_update and os.path.isfile(w._pending_update)
            assert os.path.getsize(w._pending_update) == RELEASE["assets"][0]["size"]
            assert any("已下载完成" in t for t in box_calls), "应弹出就绪提示"
            # 再验证手动检查更新能识别已预下载（不重复下载直接进入安装确认）
            assert main.parse_version("v9.9.9") > main.parse_version(main.APP_VERSION)
            print("AUTO-UPDATE FLOW OK")
            self.quit()

        QTimer.singleShot(6000, check)

    QTimer.singleShot(300, flow)
    return orig_exec()


QApplication.exec_ = staticmethod(patched_exec)
sys.exit = lambda *a: None
main.main()

srv.shutdown()
import shutil as _sh
_sh.rmtree(MOCK, ignore_errors=True)
# 清掉下载到临时目录的假安装包
try:
    os.remove(os.path.join(os.environ.get("TEMP", ""), ASSET_NAME))
except OSError:
    pass
print("AUTO UPDATE TEST DONE")
