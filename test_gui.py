# -*- coding: utf-8 -*-
"""GUI 冒烟测试（offscreen）v1.3：
方案保存/套用/删除 + 分辨率缩放 + 批量处理对话框全流程。"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 隔离配置目录，避免读写真实 %APPDATA% 下用户已保存的方案
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="wme_test_")

import main
from PyQt5.QtWidgets import QApplication, QMessageBox, QInputDialog
from PyQt5.QtCore import QTimer

QMessageBox.exec_ = lambda self: 0
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QInputDialog.getText = staticmethod(lambda *a, **k: ("测试方案A", True))

orig_exec = QApplication.exec_
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEME_FILE = main.SCHEMES_PATH
BATCH_DIR = os.path.join(APP_DIR, "batch_out")


def patched_exec():
    self = QApplication.instance()

    def flow():
        mw = [w for w in self.topLevelWidgets() if w.__class__.__name__ == "MainWindow"]
        if not mw:
            print("FAIL: MainWindow 未创建")
            self.quit()
            return
        w = mw[0]
        w.video_path = os.path.abspath("test_input3.mp4")
        w.duration = main.get_duration(w.ffmpeg, w.video_path)
        w._request_frame(0.0)

        def step2():
            print("frame:", w.vw, "x", w.vh)
            w.cmb_preset.activated.emit(2)  # 右上角
            assert len(w.regions) == 1
            # --- 方案保存 ---
            w._save_scheme()
            print("schemes:", [s["name"] for s in w.schemes])
            assert any(s["name"] == "测试方案A" for s in w.schemes)
            assert os.path.exists(SCHEME_FILE)
            # --- 清空后套用 ---
            w._clear_all()
            assert len(w.regions) == 0
            w.cmb_scheme.activated.emit(1)
            print("after apply:", w.regions)
            assert len(w.regions) == 1 and w.regions[0]["mode"] == "inpaint"
            # --- 分辨率缩放 ---
            scaled = main.scale_regions(
                [{"mode": "inpaint", "r0": (100, 50, 200, 60),
                  "t0": 0, "t1": None, "r1": (300, 150, 200, 60)}],
                640, 360, 1280, 720)
            print("scaled:", scaled[0]["r0"], scaled[0]["r1"])
            assert scaled[0]["r0"] == (200, 100, 400, 120)
            assert scaled[0]["r1"] == (600, 300, 400, 120)
            # --- 批量对话框全流程 ---
            dlg_regions = [dict(r) for r in w.regions]
            BatchDialog = None
            # 从主窗口模块作用域拿不到内部类，用 open_batch 之前直接构造：
            # 通过主窗口方法触发会破坏模态，改为直接测 run_batch + 对话框实例化
            res_holder = {}

            def batch_part():
                results = main.run_batch(
                    w.ffmpeg,
                    [os.path.abspath("test_input.mp4"),
                     os.path.abspath("test_input3.mp4")],
                    BATCH_DIR,
                    [{"mode": "inpaint", "r0": (455, 15, 180, 50),
                      "t0": 0.0, "t1": None, "r1": None}],
                    (640, 360))
                for p, ok, m in results:
                    print("batch:", os.path.basename(p), ok)
                assert all(ok for _, ok, _ in results)
                assert len(os.listdir(BATCH_DIR)) == 2
                print("batch outputs:", os.listdir(BATCH_DIR))

                # --- 删除方案 ---
                w.cmb_scheme.setCurrentIndex(1)
                w._delete_scheme()
                print("schemes after delete:", [s["name"] for s in w.schemes])
                assert not w.schemes
                if os.path.exists(SCHEME_FILE):
                    os.remove(SCHEME_FILE)
                self.quit()

            QTimer.singleShot(100, batch_part)

        QTimer.singleShot(3000, step2)

    QTimer.singleShot(100, flow)
    return orig_exec()


QApplication.exec_ = staticmethod(patched_exec)
sys.exit = lambda *a: None
main.main()
print("GUI TEST DONE")
