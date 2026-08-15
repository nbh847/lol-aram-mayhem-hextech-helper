import time
import json
import csv
import os
import sys
import threading
import queue
import tkinter as tk
import ctypes
import msvcrt  # 用于清除输入缓冲区
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
import mss
import keyboard
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from thefuzz import process, fuzz
from scripts.config import BASE_DIR, DATA_DIR
from scripts.lcu_connector import LCUConnector
from rapidocr_onnxruntime import RapidOCR

# ================= 配置与常量 =================

REFERENCE_WIDTH = 2560
REFERENCE_HEIGHT = 1440
REFERENCE_REGIONS = {
    "hex_1": {'top': 540, 'left': 650,  'width': 320, 'height': 60},
    "hex_2": {'top': 540, 'left': 1130, 'width': 320, 'height': 60},
    "hex_3": {'top': 540, 'left': 1600, 'width': 320, 'height': 60},
}
REFERENCE_OVERLAY_TOP = 742
REFERENCE_OVERLAY_WIDTH = 248
REFERENCE_OVERLAY_HEIGHT = 130
REFERENCE_TEXT_TOP = 506
RECOMMENDATION_TEMPLATE_SIZE = (1740, 904)
RECOMMENDATION_CROP = (120, 50, 1620, 840)
ANALYSIS_SIGNATURE_SIZE = (96, 18)
ANALYSIS_CHANGE_THRESHOLD = 0.20
ANALYSIS_CHANGED_REGION_COUNT = 2
SELECTION_CONFIRMATION_POLLS = 2


def get_ui_transform(width, height):
    """返回参考画布到当前屏幕的统一缩放和居中偏移。"""
    scale = min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)
    offset_x = (width - REFERENCE_WIDTH * scale) / 2
    offset_y = (height - REFERENCE_HEIGHT * scale) / 2
    return scale, offset_x, offset_y


def _scale_reference_region(region, scale, offset_x, offset_y):
    return {
        'top': int(round(offset_y + region['top'] * scale)),
        'left': int(round(offset_x + region['left'] * scale)),
        'width': int(round(region['width'] * scale)),
        'height': int(round(region['height'] * scale)),
    }


def calculate_regions(width, height):
    """按 2560x1440 参考画布统一缩放并水平/垂直居中。"""
    scale, offset_x, offset_y = get_ui_transform(width, height)
    return {
        key: _scale_reference_region(region, scale, offset_x, offset_y)
        for key, region in REFERENCE_REGIONS.items()
    }


def get_regions():
    """计算当前主屏幕上的海克斯文字截取区域。"""
    with mss.mss() as sct:
        mon = sct.monitors[1]
    return calculate_regions(mon['width'], mon['height'])

REGIONS = get_regions()

def calculate_overlay_regions(width, height):
    """
    计算海克斯下方图片区域，与 OCR 区共用同一参考画布变换。
    """
    scale, _, offset_y = get_ui_transform(width, height)
    regions = calculate_regions(width, height)
    overlay_width = int(round(REFERENCE_OVERLAY_WIDTH * scale))
    return {
        key: {
            'left': int(round(region['left'] + (region['width'] - overlay_width) / 2)),
            'top': int(round(offset_y + REFERENCE_OVERLAY_TOP * scale)),
            'width': overlay_width,
            'height': int(round(REFERENCE_OVERLAY_HEIGHT * scale)),
        }
        for key, region in regions.items()
    }


def calculate_text_top(width, height):
    """计算推荐文字顶部，与 OCR/图片区共用同一参考画布变换。"""
    scale, _, offset_y = get_ui_transform(width, height)
    return int(round(offset_y + REFERENCE_TEXT_TOP * scale))


def get_overlay_regions():
    with mss.mss() as sct:
        mon = sct.monitors[1]
    return calculate_overlay_regions(mon['width'], mon['height'])

OVERLAY_REGIONS = get_overlay_regions()

COLORS = {
    "normal": "#38D15A",  # 低饱和绿色
    "best":   "#FF6A2A",  # 高饱和橙色
    "status": "yellow",   # 黄色
    "error":  "#FF3333",  # 红色
    "bg":     "#000000"   # 背景黑
}

RECOMMENDATION_TEXT_COLORS = {
    "normal": (56, 209, 90, 255),
    "best": (255, 106, 42, 255),
    "error": (255, 51, 51, 255),
}


def get_recommendation_state(info):
    """返回推荐状态，供正式覆盖层和静态预览共用。"""
    if info.get("error"):
        return "error"
    if info.get("highlight"):
        return "best"
    return "normal"


def get_recommendation_text(info):
    """为最优推荐增加明确的首选文字标识。"""
    text = info.get("text", "")
    if not info.get("highlight") or not text:
        return text
    lines = text.splitlines()
    if lines:
        lines[0] = f"★ 首选 · {lines[0].replace('【', '').replace('】', '')}"
    return "\n".join(lines)


def _load_recommendation_font(size):
    """加载用于首选徽章的 Windows 中文粗体。"""
    candidates = (
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def decorate_recommendation_card(card, state):
    """裁掉模板外围留白、统一圆角，并强化最优推荐。"""
    card = card.convert("RGBA")
    source_width, source_height = RECOMMENDATION_TEMPLATE_SIZE
    crop_left, crop_top, crop_right, crop_bottom = RECOMMENDATION_CROP
    width, height = card.size
    card = card.crop(
        (
            int(round(crop_left * width / source_width)),
            int(round(crop_top * height / source_height)),
            int(round(crop_right * width / source_width)),
            int(round(crop_bottom * height / source_height)),
        )
    )
    width, height = card.size
    corner_radius = max(20, int(min(width, height) * 0.12))
    mask = Image.new("L", card.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=corner_radius,
        fill=255,
    )
    card.putalpha(mask)
    if state != "best":
        return card

    accent = (255, 106, 42, 255)
    highlight = (255, 235, 196, 255)
    navy = (10, 22, 32, 255)
    draw = ImageDraw.Draw(card)

    banner_width = int(width * 0.52)
    banner_height = int(height * 0.18)
    banner_left = (width - banner_width) // 2
    banner_top = max(12, int(height * 0.02))
    banner_box = (
        banner_left,
        banner_top,
        banner_left + banner_width,
        banner_top + banner_height,
    )
    banner_glow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    banner_glow_draw = ImageDraw.Draw(banner_glow)
    banner_glow_draw.rounded_rectangle(
        banner_box,
        radius=max(18, banner_height // 3),
        outline=(255, 77, 22, 210),
        width=max(12, width // 80),
    )
    banner_glow = banner_glow.filter(ImageFilter.GaussianBlur(max(8, width // 100)))
    card = Image.alpha_composite(card, banner_glow)
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        banner_box,
        radius=max(18, banner_height // 3),
        fill=accent,
        outline=highlight,
        width=max(4, width // 250),
    )
    banner_font = _load_recommendation_font(max(46, int(height * 0.10)))
    text = "首选推荐"
    bbox = draw.textbbox((0, 0), text, font=banner_font, stroke_width=2)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = banner_left + (banner_width - text_width) // 2
    text_y = banner_top + (banner_height - text_height) // 2 - bbox[1]
    draw.text(
        (text_x, text_y),
        text,
        font=banner_font,
        fill=(255, 255, 255, 255),
        stroke_width=max(2, width // 250),
        stroke_fill=navy,
    )
    card.putalpha(mask)
    return card

# ================= 1. 数据管理 (Model) =================

class DataManager:
    """负责加载和管理静态数据"""
    def __init__(self):
        self.hero_data = {}
        # 拼音映射改为 defaultdict(list)，支持一个拼音对应多个英雄
        self.pinyin_map = defaultdict(list)

        self.base_dir = BASE_DIR
        self.data_dir = DATA_DIR
        self._load_data()

    def _load_data(self):
        print("--- 正在加载数据资源 ---")



        # 2. 加载英雄数据 (CSV)
        csv_path = os.path.join(self.data_dir, 'hero_augments.csv')
        if not os.path.exists(csv_path):
            print(f"❌ 错误: 找不到文件 {csv_path}")
            print(f"   请确认该文件位于: {self.data_dir}")
        else:
            try:
                encoding = 'utf-8-sig'
                
                raw_hero_list = defaultdict(list)
                with open(csv_path, 'r', encoding=encoding) as f:
                    reader = csv.reader(f)
                    header = next(reader, None) # 跳过表头
                    is_new_format = header and "等级" in header
                    has_overall_rank = header and "总排名" in header
                    
                    for row in reader:
                        if not row: continue
                        hero = row[0].strip()
                        
                        if has_overall_rank and len(row) >= 6:
                            # 最新格式: 中文名,英文名,等级,总排名,等级内序号,海克斯名称
                            tier = row[2].strip()
                            try: overall_rank = int(row[3])
                            except (ValueError, IndexError): overall_rank = 999
                            try: t_rank = int(row[4])
                            except (ValueError, IndexError): t_rank = 999
                            name = row[5].strip()
                            
                            if hero not in self.hero_data: self.hero_data[hero] = {}
                            self.hero_data[hero][name] = {
                                "tier": tier,
                                "overall_rank": overall_rank,
                                "t_rank": t_rank
                            }
                        elif is_new_format and len(row) >= 5:
                            # 旧新格式: 中文名,英文名,等级,等级内序号,海克斯名称 (无总排名)
                            tier = row[2].strip()
                            try: t_rank = int(row[3])
                            except (ValueError, IndexError): t_rank = 999
                            name = row[4].strip()
                            
                            if hero not in self.hero_data: self.hero_data[hero] = {}
                            self.hero_data[hero][name] = {
                                "tier": tier,
                                "overall_rank": 999,
                                "t_rank": t_rank
                            }
                        elif not is_new_format and len(row) >= 4:
                            try: rank = int(row[2])
                            except (ValueError, IndexError): rank = 999
                            aug = row[3].strip()
                            raw_hero_list[hero].append((rank, aug))
                
                # 如果存在旧格式的数据，走旧的合并逻辑
                if raw_hero_list:
                    for hero, aug_list in raw_hero_list.items():
                        if hero in self.hero_data: continue # 跳过已被新格式处理的
                        aug_list.sort(key=lambda x: x[0])
                        counters = {"白银": 1, "黄金": 1, "棱彩": 1, "未知": 1}
                        h_dict = {}
                        for rank, name in aug_list:
                            tier = "未知"
                            h_dict[name] = {
                                "tier": tier, 
                                "overall_rank": 999,
                                "t_rank": counters.get(tier, 1)
                            }
                            if tier in counters: counters[tier] += 1
                        self.hero_data[hero] = h_dict
                
                print(f"✅ 英雄数据加载完毕: 共 {len(self.hero_data)} 个英雄")
            except Exception as e:
                print(f"❌ CSV 读取严重失败: {e}")

        # 3. 加载拼音映射 (构建一对多关系)
        pinyin_file = os.path.join(self.data_dir, 'pinyin_map.json')
        if os.path.exists(pinyin_file):
            try:
                with open(pinyin_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for cn, py in data.items():
                        if cn not in self.pinyin_map[py]:
                            self.pinyin_map[py].append(cn)
                        if cn not in self.pinyin_map[cn]:
                            self.pinyin_map[cn].append(cn)
            except Exception as e:
                print(f"⚠️ {pinyin_file} 加载异常: {e}")
        
        print("-> 数据初始化完成")

    def search_hero(self, query):
        """
        英雄搜索逻辑 (增强模糊匹配)
        返回: (匹配列表, 是否精确匹配)
        """
        query = query.strip().lower()
        
        # 1. 尝试拼音/中文直接匹配 (O(1))，返回的是一个列表
        if query in self.pinyin_map:
            return self.pinyin_map[query], True
        
        # 2. 如果没找到，在数据Key中模糊搜索
        if self.hero_data:
            result = process.extractOne(query, list(self.hero_data.keys()))
            if result and result[1] > 60:
                return [result[0]], False

        return [], False

    def validate_hero(self, name, threshold=80):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        if name in self.hero_data:
            return name
        if not self.hero_data:
            return None
        result = process.extractOne(name, list(self.hero_data.keys()))
        if result and result[1] > threshold:
            return result[0]
        return None

# ================= 2. 图像分析 (Core Logic) =================

class GameAnalyzer:
    """负责 OCR 和 图像处理"""
    TIER_PRIORITY = {"棱彩": 0, "黄金": 1, "白银": 2, "未知": 3}

    def __init__(self, data_manager):
        self.dm = data_manager
        # OCR 引擎: 降低 det_limit_side_len (默认736→480)
        # 截取区域 2x 上采样后最大 640px, 480 足以覆盖, 减少检测模型不必要计算
        try:
            self.ocr = RapidOCR(use_angle_cls=False, det_limit_side_len=480)
        except (KeyError, TypeError, Exception) as e:
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            print(f"\n❌ OCR 引擎初始化失败: {e}")
            print(f"   当前 Python 版本: {py_ver}")
            if sys.version_info >= (3, 13):
                print(f"   ⚠ rapidocr_onnxruntime 仅支持 Python <3.13，请使用 Python 3.10~3.12")
            else:
                print(f"   请尝试: pip install rapidocr_onnxruntime<=1.4.4")
            raise
        # 根据 CPU 逻辑核心数决定并发策略
        self._cpu_count = os.cpu_count() or 4
        self._use_parallel = self._cpu_count >= 12
        self.executor = ThreadPoolExecutor(max_workers=3)
        # 识别缓存只在当前海克斯界面轮次内有效。每个位置独立缓存，
        # 某一张卡 OCR 失败时不会覆盖其他位置已经识别成功的结果。
        self._cache_hero = None
        self._analysis_cache = {}
        self._round_signatures = {}
        self._display_signatures = {}
        self._selection_change_polls = 0
        # 预热 OCR 引擎 (消除首次推理的模型加载和内存分配延迟)
        self._warmup()

    def clear_analysis_cache(self):
        """清除当前轮次的 OCR 结果及界面变化基准。"""
        self._cache_hero = None
        self._analysis_cache.clear()
        self._round_signatures.clear()
        self._display_signatures.clear()
        self._selection_change_polls = 0

    @staticmethod
    def _image_signature(img):
        """生成对轻微动画/抗锯齿变化不敏感的低分辨率灰度签名。"""
        if img is None:
            return None
        try:
            gray = Image.fromarray(np.asarray(img, dtype=np.uint8)).convert("L")
            small = gray.resize(ANALYSIS_SIGNATURE_SIZE, Image.BILINEAR)
            values = np.asarray(small, dtype=np.float32) / 255.0
            # 量化并去除整体亮度差，保留文字轮廓变化。
            values = np.round(values * 16.0) / 16.0
            values -= values.mean()
            std = values.std()
            if std < 1e-6:
                return np.zeros_like(values)
            return values / std
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def _signature_distance(first, second):
        if first is None or second is None:
            return None
        if first.shape != second.shape:
            return None
        return float(np.mean(np.abs(first - second)))

    def _signatures_for_images(self, images):
        signatures = {}
        for key, image in images.items():
            signature = self._image_signature(image)
            if signature is not None:
                signatures[key] = signature
        return signatures

    def _changed_region_count(self, current, reference):
        """统计发生明显变化的 OCR 区域，截图失败的区域不参与判断。"""
        changed = 0
        for key, old_signature in reference.items():
            if key not in current:
                continue
            distance = self._signature_distance(current[key], old_signature)
            if distance is not None and distance >= ANALYSIS_CHANGE_THRESHOLD:
                changed += 1
        return changed

    def _is_new_round(self, signatures):
        return (
            bool(self._round_signatures)
            and self._changed_region_count(signatures, self._round_signatures)
            >= ANALYSIS_CHANGED_REGION_COUNT
        )

    def check_selection_completed(self):
        """检查 F6 后海克斯界面是否已消失，连续确认后返回 True。"""
        if not self._display_signatures:
            return False

        images = self.capture_all_regions()
        signatures = self._signatures_for_images(images)
        changed = self._changed_region_count(signatures, self._display_signatures)
        if changed >= ANALYSIS_CHANGED_REGION_COUNT:
            self._selection_change_polls += 1
        else:
            self._selection_change_polls = 0
        return self._selection_change_polls >= SELECTION_CONFIRMATION_POLLS

    def _warmup(self):
        """用小图预热 OCR 引擎, 消除首次 F6 的冷启动延迟"""
        try:
            dummy = np.zeros((48, 320), dtype=np.uint8)
            self.ocr(dummy)
            print(f"OCR 引擎预热完成 (CPU: {self._cpu_count} 线程, {'并发' if self._use_parallel else '串行'}模式)")
        except Exception:
            pass

    def capture_all_regions(self):
        """批量截图: 复用单个 mss 上下文, 避免重复初始化开销"""
        images = {}
        try:
            with mss.mss() as sct:
                for key, region in REGIONS.items():
                    monitor = {
                        "top": int(region["top"]),
                        "left": int(region["left"]),
                        "width": int(region["width"]),
                        "height": int(region["height"]),
                        "mon": 0
                    }
                    raw = sct.grab(monitor)
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
                    gray = img.convert("L")
                    w, h = gray.size
                    # 2倍上采样提高文字清晰度
                    resized = gray.resize((w * 2, h * 2), Image.BICUBIC)
                    images[key] = np.array(resized)
        except Exception as e:
            print(f"批量截图失败: {e}")
        return images

    def _ocr_and_match(self, key, img, hero_cn):
        """对单张已截取的图片执行 OCR 识别 + 数据匹配"""
        try:
            if img is None:
                return {"key": key, "text": "截图错误", "error": True}

            res_ocr, _ = self.ocr(img)
            txt = "".join([line[1] for line in res_ocr]) if res_ocr else ""
            txt = txt.replace(" ", "").replace(".", "")

            res = {
                "key": key, "valid": False, "rank": 999, 
                "text": "", "highlight": False, "error": False
            }

            if not txt:
                res["text"] = "❌ 无文字"
                res["error"] = True
                return res

            hero_augments = self.dm.hero_data.get(hero_cn, {})
            if not hero_augments:
                res["text"] = "无数据"
                res["error"] = True
                return res

            match_name = None
            
            # 1. 精确匹配 (O(1))
            if txt in hero_augments:
                match_name = txt
            else:
                # 2. 模糊匹配 (使用精确比例 fuzz.ratio，避免子串过分匹配)
                match, score = process.extractOne(txt, list(hero_augments.keys()), scorer=fuzz.ratio)
                if score > 60:
                    match_name = match

            if match_name:
                info = hero_augments[match_name]
                tier = info.get('tier', '?')
                t_rank = info.get('t_rank', '?')
                overall_rank = info.get('overall_rank', '?')
                # 格式化显示内容: 方案A
                res["text"] = f"【{match_name}】\n总No.{overall_rank} | {tier} No.{t_rank}"
                res["valid"] = True
                res["tier"] = tier
                res["t_rank"] = info.get('t_rank', 999)
                res["overall_rank"] = info.get('overall_rank', 999)
            else:
                res["text"] = "❌ 未识别"
                res["error"] = True
            
            return res
            
        except Exception as e:
            print(f"处理异常 ({key}): {e}")
            return {"key": key, "text": "Error", "error": True}

    def analyze(self, hero_cn):
        if not hero_cn: return {}
        print(f"正在分析: {hero_cn}...")

        if self._cache_hero != hero_cn:
            self.clear_analysis_cache()
            self._cache_hero = hero_cn

        # 阶段1: 批量截图 (复用 mss 上下文, 总耗时 ~18ms)
        images = self.capture_all_regions()
        if not images:
            return {}

        signatures = self._signatures_for_images(images)
        round_changed = self._is_new_round(signatures)
        if round_changed:
            print("检测到新的海克斯界面轮次，清除上一轮识别缓存")
            self._analysis_cache.clear()
            self._round_signatures.clear()
        if not self._round_signatures:
            self._round_signatures.update(signatures)
        self._display_signatures = dict(signatures)
        self._selection_change_polls = 0

        # 阶段2: OCR 识别 + 匹配 (自适应并发策略)
        results = {}
        valid_matches = []

        def analyze_region(key, img):
            cached = self._analysis_cache.get(key)
            signature = signatures.get(key)
            distance = self._signature_distance(
                signature,
                cached["signature"] if cached else None,
            )
            if cached and distance is not None and distance < ANALYSIS_CHANGE_THRESHOLD:
                return dict(cached["result"])

            data = self._ocr_and_match(key, img, hero_cn)
            if data.get("valid"):
                self._analysis_cache[key] = {
                    "signature": signature,
                    "result": dict(data),
                }
            elif cached and not round_changed:
                # OCR 短暂漏检时保留本轮之前已经确认成功的结果。
                return dict(cached["result"])
            return data

        if self._use_parallel:
            # 高端 CPU (>=12 线程): 并发 OCR, 充分利用多核
            futures = []
            for key in images:
                futures.append(self.executor.submit(analyze_region, key, images[key]))
            for f in futures:
                try:
                    data = f.result()
                    results[data["key"]] = data
                    if data.get("valid"): valid_matches.append(data)
                except Exception as e:
                    print(f"并发任务异常: {e}")
        else:
            # 低端 CPU (<12 线程): 串行 OCR, 避免缓存争抢和线程切换开销
            for key, img in images.items():
                data = analyze_region(key, img)
                results[data["key"]] = data
                if data.get("valid"): valid_matches.append(data)

        # 计算最优推荐：总排名优先（越小越好），总排名相同则按等级排序
        if valid_matches:
            def sort_key(item):
                o_rank = item.get('overall_rank', 999)
                tp = self.TIER_PRIORITY.get(item.get('tier', '未知'), 3)
                tr = item.get('t_rank', 999)
                return (o_rank, tp, tr)
            
            best = min(valid_matches, key=sort_key)
            best_key = sort_key(best)
            for item in valid_matches:
                if sort_key(item) == best_key:
                    results[item['key']]["highlight"] = True
        
        return results

# ================= 3. UI 界面 (View) =================

class OverlayApp:
    def __init__(self, root, queue):
        self.root = root
        self.queue = queue
        self.labels = {}
        self.image_labels = {}      # 图片UI专用 Label (与文字 Label 分离)
        self.image_photos = {}      # 保持 PhotoImage 引用, 防止 GC 回收
        self.templates = {}         # 缓存: {模板名: PIL.Image}
        self.hide_timer = None

        # 先隐藏窗口，避免配置透明前闪白框
        self.root.withdraw()
        self._setup_window()
        self._load_templates()
        self._setup_labels()
        self.root.deiconify()

        # 启动队列消息监听
        self.root.after(100, self.process_queue)

    def _setup_window(self):
        self.root.title("ARAM Overlay")
        self.root.overrideredirect(True) # 无边框
        self.root.attributes("-topmost", True) # 置顶
        self.root.config(bg=COLORS["bg"])
        self.root.attributes("-transparentcolor", COLORS["bg"]) # 背景透明
        
        # 鼠标穿透设置 (Windows API)
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            old_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            # WS_EX_LAYERED | WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, old_style | 0x80000 | 0x20)
        except Exception as e:
            print(f"穿透设置警告: {e}")

        # 获取主屏幕坐标，用于相对定位
        with mss.mss() as sct:
            m = sct.monitors[0]
            self.offset_x, self.offset_y = m['left'], m['top']
            self.root.geometry(f"{m['width']}x{m['height']}+{m['left']}+{m['top']}")

    def _setup_labels(self):
        with mss.mss() as sct:
            monitor = sct.monitors[1]
        scale, _, _ = get_ui_transform(monitor['width'], monitor['height'])
        self.font_style = ("Microsoft YaHei", max(9, int(round(14 * scale))), "bold")
        self.best_font_style = ("Microsoft YaHei", max(10, int(round(17 * scale))), "bold")
        for key in REGIONS:
            lbl = tk.Label(self.root, text="", font=self.font_style, bg=COLORS["bg"], justify="center")
            self.labels[key] = lbl
            # 图片UI专用 Label
            img_lbl = tk.Label(self.root, bg=COLORS["bg"])
            self.image_labels[key] = img_lbl

    def _load_templates(self):
        """从 assets/templates/ 加载三种推荐状态模板。"""
        templates_dir = os.path.join(BASE_DIR, "assets", "templates")
        if not os.path.isdir(templates_dir):
            return
        for name in os.listdir(templates_dir):
            if not name.lower().endswith('.png'):
                continue
            key = os.path.splitext(name)[0]  # tier_棱彩 / tier_黄金 / ...
            try:
                self.templates[key] = Image.open(os.path.join(templates_dir, name)).convert("RGBA")
            except Exception as e:
                print(f"模板加载失败 {name}: {e}")

    def _select_template(self, info):
        """按推荐状态选择模板: 普通、最优或异常。"""
        if info.get("error"):
            return "recommend_error"
        if info.get("highlight"):
            return "recommend_best"
        return "recommend_normal"

    def _render_image_card(self, key, info):
        """渲染下方推荐状态图片。模板缺失时静默跳过。"""
        tpl_name = self._select_template(info)
        if not tpl_name or tpl_name not in self.templates:
            return  # 无模板, 不显示图片UI

        img_lbl = self.image_labels[key]
        region = OVERLAY_REGIONS[key]

        try:
            # 复制模板 (避免污染缓存), 缩放到实机截图校准后的目标区域尺寸
            card = self.templates[tpl_name].copy()
            card = decorate_recommendation_card(card, get_recommendation_state(info))
            card = card.resize((region['width'], region['height']), Image.LANCZOS)

            photo = ImageTk.PhotoImage(card)
            self.image_photos[key] = photo  # 防 GC
            img_lbl.config(image=photo, text="")
            img_lbl.place(x=region['left'] - self.offset_x, y=region['top'] - self.offset_y, anchor="nw")
            img_lbl.lift()
        except Exception as e:
            print(f"图片UI渲染失败 ({key}): {e}")

    def process_queue(self):
        """主线程轮询：处理来自后台线程的指令"""
        try:
            while True:
                msg = self.queue.get_nowait()
                cmd = msg.get("cmd")
                data = msg.get("data")
                
                if cmd == "UPDATE":
                    self.update_display(data)
                elif cmd == "STATUS":
                    self.show_status(data)
                elif cmd == "CLEAR":
                    self.clear_display()
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self.process_queue)

    def clear_display(self):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        for lbl in self.labels.values():
            lbl.place_forget()
        for img_lbl in self.image_labels.values():
            img_lbl.place_forget()

    def show_status(self, text):
        self.clear_display()
        lbl = self.labels['hex_2']
        lbl.config(text=text, fg=COLORS["status"])
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        # 状态提示2秒后消失
        self.hide_timer = self.root.after(2000, self.clear_display)

    def update_display(self, results):
        self.clear_display()

        # 文字位置与 OCR/图片区共用参考画布变换，避免非 16:9 分辨率漂移。
        with mss.mss() as sct:
            monitor = sct.monitors[1]
        text_top_abs = calculate_text_top(monitor['width'], monitor['height'])
        fixed_rel_y = text_top_abs - self.offset_y

        for key, info in results.items():
            if not info.get("text"): continue

            lbl = self.labels[key]
            # 颜色逻辑
            if info["error"]:
                fg = COLORS["error"]
            elif info["highlight"]:
                fg = COLORS["best"]
            else:
                fg = COLORS["normal"]

            lbl.config(
                text=get_recommendation_text(info),
                fg=fg,
                font=self.best_font_style if info["highlight"] else self.font_style,
            )

            region = REGIONS[key]
            text_center_x = region['left'] - self.offset_x + region['width'] // 2
            lbl.place(x=text_center_x, y=fixed_rel_y, anchor="n")
            lbl.lift()

            # 渲染下方图片UI
            self._render_image_card(key, info)

        # 结果显示5秒后消失
        self.hide_timer = self.root.after(5000, self.clear_display)

# ================= 4. 控制逻辑 (Controller) =================

class InputController(threading.Thread):
    def __init__(self, app_queue, data_manager, analyzer, lcu_connector=None):
        super().__init__(daemon=True)
        self.queue = app_queue
        self.dm = data_manager
        self.analyzer = analyzer
        self.lcu = lcu_connector
        self.current_hero = None
        self._last_f6 = 0
        self._last_f7 = 0
        self._last_f8 = 0

    def run(self):
        while True:
            self.select_hero_phase()
            self.listening_phase()

    def flush_input(self):
        """强制清空标准输入缓冲区"""
        while msvcrt.kbhit():
            msvcrt.getch()

    def _validate_hero(self, name):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        return self.dm.validate_hero(name)

    def _try_auto_detect(self):
        """使用 LCU 统一接口自动获取英雄，返回 (英雄中文名|None, 来源)"""
        if not self.lcu:
            return None, ""
        hero, source = self.lcu.get_champion_auto()
        if hero:
            validated = self._validate_hero(hero)
            if validated:
                return validated, source
        return None, source

    # ==========================================
    # 阶段1: 选择英雄 (自动轮询 + 手动备用)
    # ==========================================

    def select_hero_phase(self):
        self.queue.put({"cmd": "CLEAR"})
        self.show_console_window()

        time.sleep(0.1)
        os.system('cls')
        self.flush_input()

        print("=== ARAM Hextech Helper ===")
        print("    F6=分析 | F7=刷新英雄 | F8=手动输入\n")

        # ====== 尝试自动检测 (轮询最多30秒) ======
        if self.lcu:
            print("[Auto] 正在连接英雄联盟客户端...")

            for attempt in range(15):  # 每2秒检测，共30秒
                hero, source = self._try_auto_detect()
                if hero:
                    self.current_hero = hero
                    print(f"\n>>> 自动识别到英雄: [{hero}] (数据源: {source})")
                    print(f">>> F6=分析 | F7=刷新 | F8=手动")
                    self.queue.put({"cmd": "STATUS", "data": f"当前: {hero}\n按 F6 分析 | F7 刷新"})
                    self.hide_console_window()
                    return

                # F8 跳过自动检测并手动输入
                if keyboard.is_pressed('f8'):
                    print("\n[F8] 切换至手动输入...")
                    time.sleep(0.5)
                    break

                dots = "." * ((attempt % 3) + 1)
                print(f"\r[Auto] 等待选取英雄{dots}   ", end="", flush=True)
                time.sleep(2)
            else:
                # 30秒后没搜到，不锁在死循环里，直接进入监听模式
                print("\n[Auto] 暂未自动识别到英雄。将切入后台继续运行。")
                print(">>> 随时按 [F7] 重新获取，或按 [F8] 呼出控制台手动输入。\n")
                self.current_hero = None
                self.queue.put({"cmd": "STATUS", "data": "暂无英雄\n按 F7 自动获取本局英雄"})
                self.hide_console_window()
                return

        # ====== 手动输入 (仅在按下 F8 时，或 LCU 完全异常时进入) ======
        print(">>> 请输入英雄名称 (拼音/中文):")

        while True:
            try:
                self.flush_input()
                raw = input("Input: ").strip()
            except EOFError:
                continue
            if not raw:
                continue

            matches, is_exact = self.dm.search_hero(raw)
            selected_name = None

            if not matches:
                print("❌ 未找到，请重试")
                continue

            if len(matches) > 1:
                print(f"🤔 发现多个匹配项，请选择:")
                for idx, name in enumerate(matches):
                    print(f"   {idx + 1}. {name}")
                print(">>> 请输入序号:")
                self.flush_input()
                try:
                    idx = int(input("Select: ").strip()) - 1
                    if 0 <= idx < len(matches):
                        selected_name = matches[idx]
                    else:
                        print("无效选项，请重试")
                        continue
                except ValueError:
                    print("无效输入，请重试")
                    continue
            else:
                candidate = matches[0]
                if is_exact:
                    selected_name = candidate
                else:
                    print(f"   猜你是: {candidate}? (Enter确认 / n重输)")
                    self.flush_input()
                    if input().strip().lower() == 'n':
                        continue
                    selected_name = candidate

            if selected_name:
                validated = self._validate_hero(selected_name)
                if not validated:
                    print(f"数据库暂无【{selected_name}】的数据")
                    continue
                self.current_hero = validated
                print(f">>> 已锁定: {validated}")
                print(f">>> F6=分析 | F7=刷新 | F8=手动")
                self.queue.put({"cmd": "STATUS", "data": f"当前: {validated}\n按 F6 分析 | F7 刷新"})
                self.hide_console_window()
                break

    # ==========================================
    # 阶段2: 监听热键
    # ==========================================

    def listening_phase(self):
        self.flush_input()
        print(f"[监听中...] 当前英雄: {self.current_hero} | F6分析 / F7刷新 / F8手动")

        while True:
            now = time.time()

            if keyboard.is_pressed('f6') and now - self._last_f6 > 1.0:
                self._last_f6 = now
                if not self.current_hero:
                    self.queue.put({"cmd": "STATUS", "data": "⚠ 尚未锁定英雄\n请按 F7 自动获取或 F8 手动输入"})
                    continue
                
                self.queue.put({"cmd": "STATUS", "data": f"🔎 正在分析 [{self.current_hero}]..."})
                results = self.analyzer.analyze(self.current_hero)
                self.queue.put({"cmd": "UPDATE", "data": results})

            if keyboard.is_pressed('f7') and now - self._last_f7 > 1.0:
                self._last_f7 = now
                # F7: 全阶段刷新英雄 (ChampSelect / InProgress / LiveAPI)
                self.queue.put({"cmd": "STATUS", "data": "刷新英雄..."})
                hero, source = self._try_auto_detect()
                if hero and hero != self.current_hero:
                    old = self.current_hero
                    self.current_hero = hero
                    print(f">>> 英雄已切换 ({source}): {old} -> {hero}")
                    self.queue.put({"cmd": "STATUS", "data": f"已切换: {hero}\n按 F6 分析"})
                elif hero:
                    self.queue.put({"cmd": "STATUS", "data": f"当前英雄: {hero}\n按 F6 分析"})
                else:
                    self.queue.put({"cmd": "STATUS", "data": f"当前: {self.current_hero}\n按 F6 分析"})

            if keyboard.is_pressed('f8') and now - self._last_f8 > 1.0:
                self._last_f8 = now
                time.sleep(0.5)
                return  # 退出监听，回到 select_hero_phase

            time.sleep(0.05)


    @staticmethod
    def show_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def hide_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass

# ================= 5. 主入口 =================

def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)
    
    # 强制设置工作目录为应用根目录 (兼容打包)
    os.chdir(BASE_DIR)
    os.system('title ARAM 海克斯助手')
    os.system('chcp 65001 >nul')
    print(f"Working Directory: {BASE_DIR}")

    # 1. 初始化核心数据与逻辑
    dm = DataManager()
    
    if not dm.hero_data:
        print("❌ 警告: 未加载到任何英雄数据，请检查CSV文件。")
        input("按任意键退出...")
        return

    analyzer = GameAnalyzer(dm)
    
    # 2. 初始化 LCU 客户端连接器
    champions_json = os.path.join(dm.data_dir, 'champions.json')
    lcu = LCUConnector(champions_json)
    
    # 3. 初始化 UI 与 通信队列
    root = tk.Tk()
    msg_queue = queue.Queue()
    app = OverlayApp(root, msg_queue)
    
    # 4. 启动后台控制线程
    controller = InputController(msg_queue, dm, analyzer, lcu_connector=lcu)
    controller.start()
    
    # 4. 进入 UI 主循环
    print("程序已启动...")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        os._exit(0)

if __name__ == "__main__":
    main()
