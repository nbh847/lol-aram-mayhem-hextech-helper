"""用静态游戏截图预览 OCR 与海克斯覆盖层，不连接游戏客户端。"""

import argparse
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as core
from main import DataManager, GameAnalyzer


DEFAULT_SCREENSHOT = PROJECT_ROOT / "data" / "lol-hex-view.png"


def get_monitor():
    with mss.mss() as sct:
        return sct.monitors[1]


def get_source_regions(width, height):
    return core.calculate_regions(width, height)


def get_display_overlay_regions(width, height):
    return core.calculate_overlay_regions(width, height)


def crop_for_ocr(image, region):
    box = (
        region["left"],
        region["top"],
        region["left"] + region["width"],
        region["top"] + region["height"],
    )
    gray = image.crop(box).convert("L")
    width, height = gray.size
    return np.array(gray.resize((width * 2, height * 2), Image.Resampling.BICUBIC))


def recognize_text(analyzer, image):
    result, _ = analyzer.ocr(image)
    text = "".join(line[1] for line in result or [])
    return text.replace(" ", "").replace(".", "")


def analyze_screenshot(image, analyzer, data_manager, hero=None):
    regions = get_source_regions(*image.size)
    results = {}

    for key, region in regions.items():
        ocr_image = crop_for_ocr(image, region)
        if hero:
            results[key] = analyzer._ocr_and_match(key, ocr_image, hero)
            continue

        text = recognize_text(analyzer, ocr_image)
        preview_status = {
            "hex_1": ("普通推荐", False, False),
            "hex_2": ("最优推荐", True, False),
            "hex_3": ("异常/错误", False, True),
        }[key]
        status_text, highlight, error = preview_status
        results[key] = {
            "key": key,
            "valid": bool(text),
            "rank": 999,
            "text": f"【{text or '❌ 无文字'}】\n{status_text}",
            "highlight": highlight,
            "error": error,
        }

    if hero:
        valid_matches = [result for result in results.values() if result.get("valid")]
        if valid_matches:
            def sort_key(item):
                priority = analyzer.TIER_PRIORITY.get(item.get("tier", "未知"), 3)
                return (
                    item.get("overall_rank", 999),
                    priority,
                    item.get("t_rank", 999),
                )

            best_key = sort_key(min(valid_matches, key=sort_key))
            for result in valid_matches:
                result["highlight"] = sort_key(result) == best_key

    return results


def resolve_hero(data_manager, query):
    if not query:
        return None
    matches, _ = data_manager.search_hero(query)
    if not matches:
        raise ValueError(f"找不到英雄: {query}")
    return data_manager.validate_hero(matches[0])


def _load_preview_font(size):
    """加载与 Tk 覆盖层接近的微软雅黑粗体。"""
    candidates = (
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _draw_overlay_text(canvas, region, text, color, font):
    """按 OverlayApp 的锚点把多行文字画到透明覆盖层。"""
    draw = ImageDraw.Draw(canvas)
    lines = text.splitlines() or [text]
    spacing = max(2, font.size // 5) if hasattr(font, "size") else 2
    line_sizes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    block_width = max((box[2] - box[0] for box in line_sizes), default=0)
    line_height = max((box[3] - box[1] for box in line_sizes), default=0)
    x = region["left"] + (region["width"] - block_width) // 2
    y = region["top"]
    for index, line in enumerate(lines):
        draw.text((x, y + index * (line_height + spacing)), line, fill=color, font=font)


def detect_source_transform(source, monitor):
    """从当前桌面定位静态截图，允许右侧窗口遮挡部分画面。"""
    with mss.mss() as sct:
        raw = sct.grab(monitor)
    desktop = Image.frombytes("RGB", raw.size, raw.rgb)
    source_gray = cv2.cvtColor(np.array(source), cv2.COLOR_RGB2GRAY)
    desktop_gray = cv2.cvtColor(np.array(desktop), cv2.COLOR_RGB2GRAY)
    detector = cv2.ORB_create(5000)
    source_points, source_desc = detector.detectAndCompute(source_gray, None)
    desktop_points, desktop_desc = detector.detectAndCompute(desktop_gray, None)
    if source_desc is None or desktop_desc is None:
        raise RuntimeError("当前桌面未找到足够的截图特征")

    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        source_desc, desktop_desc, k=2
    )
    good = [first for first, second in matches if first.distance < 0.7 * second.distance]
    if len(good) < 20:
        raise RuntimeError(f"当前桌面未定位到静态截图（有效特征点 {len(good)}）")

    source_xy = np.float32([source_points[item.queryIdx].pt for item in good])
    desktop_xy = np.float32([desktop_points[item.trainIdx].pt for item in good])
    matrix, inliers = cv2.estimateAffinePartial2D(
        source_xy,
        desktop_xy,
        method=cv2.RANSAC,
        ransacReprojThreshold=3,
    )
    inlier_count = int(inliers.sum()) if inliers is not None else 0
    if matrix is None or inlier_count < 20:
        raise RuntimeError(f"静态截图定位不稳定（内点 {inlier_count}）")
    print(f"桌面定位: {len(good)} 个有效特征，{inlier_count} 个内点")
    return matrix


def _transform_region(region, matrix):
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    point = matrix @ np.array([region["left"], region["top"], 1.0])
    return {
        "left": int(round(point[0])),
        "top": int(round(point[1])),
        "width": max(1, int(round(region["width"] * scale))),
        "height": max(1, int(round(region.get("height", 0) * scale))),
    }


def compose_overlay(source, results, monitor):
    """生成只含文字和推荐图片的透明覆盖层。"""
    matrix = detect_source_transform(source, monitor)
    canvas = Image.new("RGBA", (monitor["width"], monitor["height"]), (0, 0, 0, 0))
    font = _load_preview_font(14)
    source_regions = get_source_regions(source.width, source.height)
    source_overlay_regions = get_display_overlay_regions(source.width, source.height)
    text_y = core.calculate_text_top(source.width, source.height)

    templates_dir = PROJECT_ROOT / "assets" / "templates"
    templates = {}
    for name in ("recommend_normal", "recommend_best", "recommend_error"):
        path = templates_dir / f"{name}.png"
        if path.exists():
            templates[name] = Image.open(path).convert("RGBA")

    color_map = {
        "normal": (0, 255, 0, 255),
        "best": (255, 215, 0, 255),
        "error": (255, 51, 51, 255),
    }
    for key, info in results.items():
        if not info.get("text"):
            continue

        region = _transform_region(source_regions[key], matrix)
        if info.get("error"):
            state = "error"
            template_name = "recommend_error"
        elif info.get("highlight"):
            state = "best"
            template_name = "recommend_best"
        else:
            state = "normal"
            template_name = "recommend_normal"

        image_region = _transform_region(source_overlay_regions[key], matrix)
        template = templates.get(template_name)
        if template is not None:
            resized = template.resize(
                (image_region["width"], image_region["height"]),
                Image.Resampling.LANCZOS,
            )
            canvas.alpha_composite(resized, (image_region["left"], image_region["top"]))

        text_point = matrix @ np.array(
            [source_regions[key]["left"], text_y, 1.0]
        )
        text_region = {
            "left": int(round(text_point[0])),
            "top": int(round(text_point[1])),
            "width": region["width"],
        }
        _draw_overlay_text(canvas, text_region, info["text"], color_map[state], font)

    return canvas


class Win32OverlayPreview:
    """用 Win32 分层窗口把 RGBA 内容真正叠加到当前桌面。"""

    WM_HOTKEY = 0x0312
    WM_DESTROY = 0x0002
    VK_ESCAPE = 0x1B
    WS_POPUP = 0x80000000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    SW_SHOWNOACTIVATE = 4
    ULW_ALPHA = 0x00000002
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    AC_SRC_OVER = 0
    AC_SRC_ALPHA = 1

    def __init__(self, image, monitor):
        self.image = image
        self.width = monitor["width"]
        self.height = monitor["height"]
        self.left = monitor["left"]
        self.top = monitor["top"]
        self.hwnd = None
        self._class_name = f"LolHexTransparentOverlay_{id(self)}"
        self._wnd_proc = self._make_wnd_proc()

    def _make_wnd_proc(self):
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == self.WM_HOTKEY and wparam == 1:
                ctypes.windll.user32.DestroyWindow(hwnd)
                return 0
            if msg == self.WM_DESTROY:
                ctypes.windll.user32.UnregisterHotKey(hwnd, 1)
                ctypes.windll.user32.PostQuitMessage(0)
                return 0
            return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        return callback_type(wnd_proc)

    def show(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hinstance = kernel32.GetModuleHandleW(None)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [
                ("BlendOp", ctypes.c_ubyte),
                ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte),
            ]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        wnd_class = WNDCLASSW(
            0,
            ctypes.cast(self._wnd_proc, ctypes.c_void_p),
            0,
            0,
            hinstance,
            None,
            None,
            None,
            None,
            self._class_name,
        )
        user32.RegisterClassW(ctypes.byref(wnd_class))

        user32.CreateWindowExW.restype = wintypes.HWND
        self.hwnd = user32.CreateWindowExW(
            self.WS_EX_TOPMOST
            | self.WS_EX_TRANSPARENT
            | self.WS_EX_TOOLWINDOW
            | self.WS_EX_LAYERED
            | self.WS_EX_NOACTIVATE,
            self._class_name,
            "ARAM Hextech Overlay Preview",
            self.WS_POPUP,
            self.left,
            self.top,
            self.width,
            self.height,
            None,
            None,
            hinstance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError()

        gdi32 = ctypes.windll.gdi32
        user32.GetDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.restype = ctypes.c_void_p

        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap_info = BITMAPINFO(
            BITMAPINFOHEADER(
                ctypes.sizeof(BITMAPINFOHEADER),
                self.width,
                -self.height,
                1,
                32,
                self.BI_RGB,
                0,
                0,
                0,
                0,
                0,
            ),
            (wintypes.DWORD * 3)(),
        )
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            screen_dc,
            ctypes.byref(bitmap_info),
            self.DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits:
            raise ctypes.WinError()

        rgba = np.array(self.image, dtype=np.uint8)
        alpha = rgba[:, :, 3:4].astype(np.uint16)
        premultiplied = rgba.copy()
        premultiplied[:, :, :3] = (
            rgba[:, :, :3].astype(np.uint16) * alpha // 255
        ).astype(np.uint8)
        bgra = np.ascontiguousarray(premultiplied[:, :, [2, 1, 0, 3]])
        ctypes.memmove(bits, bgra.ctypes.data, bgra.nbytes)

        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        destination = POINT(self.left, self.top)
        size = SIZE(self.width, self.height)
        source = POINT(0, 0)
        blend = BLENDFUNCTION(self.AC_SRC_OVER, 0, 255, self.AC_SRC_ALPHA)
        if not user32.UpdateLayeredWindow(
            self.hwnd,
            screen_dc,
            ctypes.byref(destination),
            ctypes.byref(size),
            memory_dc,
            ctypes.byref(source),
            0,
            ctypes.byref(blend),
            self.ULW_ALPHA,
        ):
            raise ctypes.WinError()

        if not user32.RegisterHotKey(self.hwnd, 1, 0, self.VK_ESCAPE):
            print("警告：Esc 全局退出热键注册失败，请在终端按 Ctrl+C 退出")
        user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)

        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            gdi32.SelectObject(memory_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)


def run_preview(screenshot_path, hero_query=None):
    source = Image.open(screenshot_path).convert("RGB")
    monitor = get_monitor()
    core.REGIONS = get_source_regions(monitor["width"], monitor["height"])
    core.OVERLAY_REGIONS = get_display_overlay_regions(
        monitor["width"], monitor["height"]
    )
    data_manager = DataManager()
    analyzer = GameAnalyzer(data_manager)
    hero = resolve_hero(data_manager, hero_query)
    results = analyze_screenshot(source, analyzer, data_manager, hero)

    print("截图预览模式：已跳过 LCU、游戏进程和管理员权限检查。")
    print(f"截图: {screenshot_path} ({source.width}x{source.height})")
    print(f"英雄匹配: {hero or '未指定，仅显示 OCR 预览状态'}")
    for key, result in results.items():
        print(f"{key}: {result.get('text', '')}")
    preview = compose_overlay(source, results, monitor)
    print("透明覆盖层已显示；当前桌面内容保持不变，按 Esc 退出。")
    try:
        Win32OverlayPreview(preview, monitor).show()
    finally:
        analyzer.executor.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser(
        description="在静态游戏截图上预览 OCR 和海克斯覆盖层，不启动游戏。"
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=DEFAULT_SCREENSHOT,
        help=f"截图路径，默认: {DEFAULT_SCREENSHOT}",
    )
    parser.add_argument(
        "--hero",
        help="可选：英雄中文名、拼音或简拼；指定后会使用本地数据进行真实匹配。",
    )
    args = parser.parse_args()

    if not args.screenshot.exists():
        parser.error(f"截图不存在: {args.screenshot}")
    try:
        run_preview(args.screenshot, args.hero)
    except KeyboardInterrupt:
        print("预览已退出")
    except Exception as exc:
        parser.exit(1, f"预览失败: {exc}\n")


if __name__ == "__main__":
    main()
