"""
统一路径常量与配置 (兼容 PyInstaller 打包)
所有模块应从此文件导入 BASE_DIR, DATA_DIR 等路径常量，避免重复定义。
"""
import os
import sys
import json
from datetime import datetime, timedelta


def get_base_dir():
    """获取应用根目录 (兼容 PyInstaller 打包)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # scripts/config.py -> scripts/ -> 项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = get_base_dir()
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

# 数据文件路径常量
CHAMPION_ID_FILE = os.path.join(DATA_DIR, "champions.json")
PINYIN_FILE      = os.path.join(DATA_DIR, "pinyin_map.json")
CSV_FILE         = os.path.join(DATA_DIR, "hero_augments.csv")

# 全量更新推荐间隔（天）
FULL_UPDATE_INTERVAL_DAYS = 14


def get_config():
    """读取配置文件"""
    default_config = {
        "last_full_update": None,  # ISO格式时间字符串，如 "2026-08-04T12:00:00"
        "update_count": 0
    }
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 合并默认配置，确保所有字段存在
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
        except Exception as e:
            print(f"读取配置文件失败: {e}，使用默认配置")
    
    return default_config


def save_config(config):
    """保存配置文件"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"保存配置文件失败: {e}")
        return False


def update_full_update_time():
    """更新全量更新时间"""
    config = get_config()
    config["last_full_update"] = datetime.now().isoformat()
    config["update_count"] = config.get("update_count", 0) + 1
    return save_config(config)


def get_full_update_status():
    """获取全量更新状态信息"""
    config = get_config()
    last_update_str = config.get("last_full_update")
    
    if not last_update_str:
        return {
            "last_update": None,
            "days_since": None,
            "days_remaining": None,
            "overdue": True,
            "message": "从未执行全量更新"
        }
    
    try:
        last_update = datetime.fromisoformat(last_update_str)
        now = datetime.now()
        days_since = (now - last_update).days
        days_remaining = FULL_UPDATE_INTERVAL_DAYS - days_since
        overdue = days_remaining <= 0
        
        if days_since == 0:
            message = "今天已执行全量更新"
        elif days_since == 1:
            message = "昨天执行了全量更新"
        elif days_since < FULL_UPDATE_INTERVAL_DAYS:
            message = f"{days_since} 天前执行了全量更新"
        else:
            message = f"{days_since} 天前执行了全量更新（建议更新）"
        
        return {
            "last_update": last_update,
            "days_since": days_since,
            "days_remaining": days_remaining,
            "overdue": overdue,
            "message": message
        }
    except Exception as e:
        print(f"解析全量更新时间失败: {e}")
        return {
            "last_update": None,
            "days_since": None,
            "days_remaining": None,
            "overdue": True,
            "message": "时间记录异常"
        }
