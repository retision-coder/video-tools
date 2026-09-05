# -*- coding: utf-8 -*-
"""
核心共享模块：区域关键帧模型 + 帧处理 + 编码命令
--------------------------------------------------
被 main.py（主程序）和 ai_engine.py（AI 组件环境里的独立脚本）共同引用，
避免两处各抄一份。注意：本模块不得依赖 PyQt5 等 GUI 库，
OpenCV / numpy 在使用时延迟导入，保证 AI 环境和主程序环境都能跑。

区域数据结构（关键帧制）：
    {"mode": "inpaint|inpaint_ai|blur|mosaic",
     "keys": [(t秒, (x, y, w, h)), ...]  按时间排序}
    - 1 个关键帧：从该时刻起到结尾生效，位置固定
    - ≥2 个关键帧：首末关键帧之间生效，位置逐帧线性插值
    旧格式 {"r0","t0","t1","r1"} 由 norm_keys() 自动等价迁移。
"""


def clamp_rect(x, y, w, h, vw, vh):
    """把矩形约束在画面内，并保证最小 4×4。"""
    w = max(4, min(int(round(w)), vw))
    h = max(4, min(int(round(h)), vh))
    x = max(0, min(int(round(x)), vw - w))
    y = max(0, min(int(round(y)), vh - h))
    return x, y, w, h


def norm_keys(reg):
    """统一成关键帧列表 [(t, (x,y,w,h)), ...]，按时间排序。
    兼容旧格式 r0/t0/t1/r1（等价于 1~2 个关键帧）。"""
    ks = reg.get("keys")
    if ks:
        out = [(float(k[0]), tuple(float(v) for v in k[1])) for k in ks]
        out.sort(key=lambda e: e[0])
        return out
    r0 = tuple(float(v) for v in reg["r0"])
    t0 = float(reg.get("t0") or 0.0)
    t1 = reg.get("t1")
    if t1 is not None:
        end = tuple(float(v) for v in (reg["r1"] if reg.get("r1") else reg["r0"]))
        return [(t0, r0), (float(t1), end)]
    return [(t0, r0)]


def region_active(reg, t):
    """该区域在 t 秒是否生效：1 个关键帧从该帧到结尾；≥2 个取首末之间。"""
    ks = norm_keys(reg)
    if len(ks) == 1:
        return t >= ks[0][0] - 1e-6
    return ks[0][0] - 1e-6 <= t <= ks[-1][0] + 1e-6


def rect_at(reg, t):
    """t 时刻区域的 (x, y, w, h) 浮点值；相邻关键帧之间线性插值。"""
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


def apply_region(frame, mode, x, y, w, h):
    """非 AI 模式的单区域帧处理：智能修复（NS inpaint）/ 高斯模糊 / 马赛克。
    直接在 frame 上原地修改并返回。"""
    import cv2
    import numpy as np
    fh, fw = frame.shape[:2]
    x, y, w, h = clamp_rect(x, y, w, h, fw, fh)
    if mode == "inpaint":
        pad = 20
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(fw, x + w + pad), min(fh, y + h + pad)
        roi = frame[y0:y1, x0:x1]
        mask = np.zeros(roi.shape[:2], np.uint8)
        mask[y - y0:y - y0 + h, x - x0:x - x0 + w] = 255
        # 掩码外扩，吃掉水印边缘残留
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=2)
        # Navier-Stokes 修复：大块区域融合比 Telea 更平滑
        res = cv2.inpaint(roi, mask, 7, cv2.INPAINT_NS)
        # 接缝羽化：软掩码混合，消除修复区与周边的边界痕
        soft = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 4)[..., None]
        frame[y0:y1, x0:x1] = (
            res.astype(np.float32) * soft
            + roi.astype(np.float32) * (1 - soft)
        ).astype(np.uint8)
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


def build_encode_cmd(ffmpeg, width, height, fps, inp, outp):
    """构造 ffmpeg 编码命令：stdin 喂 rawvideo，音轨从原视频复制。"""
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
        "-i", inp,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart",
        outp,
    ]


def open_capture(path):
    """打开视频并返回 (cap, fps, 宽, 高, 总帧数)；打不开返回 None。"""
    import cv2
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps <= 1:
            fps = 25.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        return cap, fps, w, h, total
    except Exception:
        return None


def write_preview_frames(preview_dir, src, dst, max_w=640):
    """把源帧/处理帧缩略成 JPEG 写入预览目录（src.jpg / dst.jpg）。

    供处理界面定时轮询显示「原视频 vs 处理后」实时对比。
    先写临时文件再改名，避免界面读到写了一半的图；任何失败静默忽略，
    预览不能影响处理主流程。
    """
    import os
    import cv2
    try:
        os.makedirs(preview_dir, exist_ok=True)
        for name, fr in (("src", src), ("dst", dst)):
            h, w = fr.shape[:2]
            if w > max_w:
                fr = cv2.resize(fr, (max_w, int(h * max_w / w)),
                                interpolation=cv2.INTER_AREA)
            tmp = os.path.join(preview_dir, name + "_tmp.jpg")  # 后缀决定编码格式
            if cv2.imwrite(tmp, fr, [cv2.IMWRITE_JPEG_QUALITY, 70]):
                os.replace(tmp, os.path.join(preview_dir, name + ".jpg"))
    except Exception:
        pass
