# -*- coding: utf-8 -*-
"""
AI 精修引擎（由主程序下载的独立 AI 组件环境运行，不在主程序解释器内执行）
- 逐帧读取视频，普通区域用 模糊/马赛克/NS修复（wm_core.apply_region），
  AI 区域合并成掩码交给 LaMa 整帧修复
- 通过 ffmpeg 管道编码输出（保留原音轨）
- 进度输出：stdout 打印 "MODEL_LOADING / MODEL_READY / PROGRESS <i> <total>
  / DONE / ERROR ..."，由主程序解析
- 依赖同目录的 wm_core.py（主程序运行本脚本前会一并复制到运行目录）
"""
import argparse
import json
import subprocess
import sys
import threading

from wm_core import (apply_region, build_encode_cmd, clamp_rect,
                     open_capture, rect_at, region_active)

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--regions", required=True, help="区域 JSON 文件路径")
    ap.add_argument("--ffmpeg", required=True)
    args = ap.parse_args()

    with open(args.regions, encoding="utf-8") as f:
        regions = json.load(f)

    info = open_capture(args.input)
    if info is None:
        print("ERROR 无法打开视频", flush=True)
        return 1
    cap, fps, W, H, total = info

    import cv2
    import numpy as np

    need_ai = any(r.get("mode") == "inpaint_ai" for r in regions)
    lama = None
    Image = None
    if need_ai:
        print("MODEL_LOADING", flush=True)
        from simple_lama_inpainting import SimpleLama
        from PIL import Image as _Image
        Image = _Image
        lama = SimpleLama()   # 首次运行自动下载 big-lama 模型（约 200MB）
        print("MODEL_READY", flush=True)

    cmd = build_encode_cmd(args.ffmpeg, W, H, fps, args.input, args.output)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=_CREATE_NO_WINDOW)

    stderr_tail = []

    def read_stderr():
        try:
            for line in proc.stderr:
                stderr_tail.append(line.decode("utf-8", "replace").rstrip()
                                   if isinstance(line, bytes) else line.rstrip())
                del stderr_tail[:-10]
        except Exception:
            pass

    threading.Thread(target=read_stderr, daemon=True).start()

    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = idx / fps
            ai_mask = None
            for reg in regions:
                if not region_active(reg, t):
                    continue
                mode = reg.get("mode")
                x, y, w, h = clamp_rect(*rect_at(reg, t), W, H)
                if mode == "inpaint_ai":
                    if ai_mask is None:
                        ai_mask = np.zeros((H, W), np.uint8)
                    ai_mask[y:y + h, x:x + w] = 255
                else:
                    frame = apply_region(frame, mode, x, y, w, h)
            if ai_mask is not None:
                # 掩码外扩，交给 LaMa 一起修复（比逐框更连贯）
                ai_mask = cv2.dilate(ai_mask, np.ones((9, 9), np.uint8),
                                     iterations=2)
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                msk = Image.fromarray(ai_mask)
                res = np.array(lama(img, msk))  # RGB
                frame = cv2.cvtColor(res, cv2.COLOR_RGB2BGR)
            proc.stdin.write(frame.tobytes())
            idx += 1
            if idx % 10 == 0:
                print(f"PROGRESS {idx} {total}", flush=True)
    except (BrokenPipeError, OSError):
        pass
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except Exception:
            pass
    print(f"PROGRESS {idx} {total}", flush=True)
    proc.wait()
    if proc.returncode == 0:
        print("DONE", flush=True)
        return 0
    print("ERROR " + "\n".join(stderr_tail[-5:]), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
