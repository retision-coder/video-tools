# -*- coding: utf-8 -*-
"""
AI 精修引擎（由主程序下载的独立 AI 组件环境运行，不在主程序解释器内执行）
- 逐帧读取视频，普通区域用模糊/马赛克/NS 修复，AI 区域合并成掩码交给 LaMa
- 通过 ffmpeg 管道编码输出（保留原音轨）
- 进度输出：stdout 打印 "PROGRESS <i> <total>" 与 "MODEL_READY"
"""
import argparse
import json
import subprocess
import sys
import threading

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def clamp_rect(x, y, w, h, vw, vh):
    w = max(4, min(int(round(w)), vw))
    h = max(4, min(int(round(h)), vh))
    x = max(0, min(int(round(x)), vw - w))
    y = max(0, min(int(round(y)), vh - h))
    return x, y, w, h


def norm_keys(reg):
    """统一成关键帧列表 [(t, (x,y,w,h)), ...]，按时间排序（兼容旧格式）。"""
    ks = reg.get("keys")
    if ks:
        out = [(float(k[0]), tuple(float(v) for v in k[1])) for k in ks]
        out.sort(key=lambda e: e[0])
        return out
    r0 = tuple(float(v) for v in reg["r0"])
    t0 = float(reg.get("t0") or 0.0)
    t1 = reg.get("t1")
    r1 = reg.get("r1")
    if t1 is not None:
        end = tuple(float(v) for v in (r1 if r1 else reg["r0"]))
        return [(t0, r0), (float(t1), end)]
    return [(t0, r0)]


def region_active(reg, t):
    ks = norm_keys(reg)
    if len(ks) == 1:
        return t >= ks[0][0] - 1e-6
    return ks[0][0] - 1e-6 <= t <= ks[-1][0] + 1e-6


def rect_at(reg, t):
    ks = norm_keys(reg)
    if len(ks) == 1 or t <= ks[0][0]:
        return ks[0][1]
    if t >= ks[-1][0]:
        return ks[-1][1]
    for i in range(len(ks) - 1):
        t0, r0 = ks[i]
        t1, r1 = ks[i + 1]
        if t0 - 1e-9 <= t <= t1 + 1e-9:
            k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * k for a, b in zip(r0, r1))
    return ks[-1][1]


def apply_fast(frame, mode, x, y, w, h, cv2, np):
    """非 AI 模式：NS 修复 / 模糊 / 马赛克。"""
    fh, fw = frame.shape[:2]
    x, y, w, h = clamp_rect(x, y, w, h, fw, fh)
    if mode == "inpaint":
        pad = 20
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(fw, x + w + pad), min(fh, y + h + pad)
        roi = frame[y0:y1, x0:x1]
        mask = np.zeros(roi.shape[:2], np.uint8)
        mask[y - y0:y - y0 + h, x - x0:x - x0 + w] = 255
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=2)
        res = cv2.inpaint(roi, mask, 7, cv2.INPAINT_NS)
        soft = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 4)[..., None]
        frame[y0:y1, x0:x1] = (
            res.astype(np.float32) * soft
            + roi.astype(np.float32) * (1 - soft)).astype(np.uint8)
    elif mode == "blur":
        roi = frame[y:y + h, x:x + w]
        frame[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (0, 0), 28)
    elif mode == "mosaic":
        roi = frame[y:y + h, x:x + w]
        small = cv2.resize(roi, (max(2, w // 14), max(2, h // 14)),
                           interpolation=cv2.INTER_LINEAR)
        frame[y:y + h, x:x + w] = cv2.resize(small, (w, h),
                                             interpolation=cv2.INTER_NEAREST)
    return frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--regions", required=True, help="区域 JSON 文件路径")
    ap.add_argument("--ffmpeg", required=True)
    args = ap.parse_args()

    with open(args.regions, encoding="utf-8") as f:
        regions = json.load(f)

    import cv2
    import numpy as np

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        print("ERROR 无法打开视频", flush=True)
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 1:
        fps = 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

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

    cmd = [
        args.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "-",
        "-i", args.input,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart",
        args.output,
    ]
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
                    frame = apply_fast(frame, mode, x, y, w, h, cv2, np)
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
