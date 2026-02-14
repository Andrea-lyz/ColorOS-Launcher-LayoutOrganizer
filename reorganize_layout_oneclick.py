#!/usr/bin/env python3
"""
ColorOS PhoneClone 桌面布局一键整理工具
========================================
功能：将 PhoneClone 备份中的桌面应用自动归入分类文件夹。

分类配置：
  · 从 app_categories.json 读取（可用 fetch_categories.py 生成）
  · 支持自定义分类名、分类顺序、第0屏保留图标

修改对象（同时修改，缺一不可）：
  1. launcher.db  — SQLite 数据库，PhoneClone 恢复的**主数据源**
     · singledesktopitems: 文件夹条目用 itemType=3, 文件夹内应用 container=文件夹ID
     · singledesktopscreens: 屏幕定义
     · _draw 变体表同步
  2. launcher_layout.xml / launcher_draw_layout.xml — XML 布局描述
     · <FOLDERS> 中必须有与数据库匹配的文件夹条目
  3. com.android.launcher.tar — 打包 data/ 目录（含 launcher.db）

使用方法：
  python reorganize_layout_oneclick.py                      # 执行整理
  python reorganize_layout_oneclick.py --restore            # 从 .bak 恢复原始布局
  python reorganize_layout_oneclick.py --config my.json     # 指定分类文件
  python reorganize_layout_oneclick.py --workdir <DIR>      # 指定工作目录（供主控脚本调用）

前置条件：
  · 已用 PhoneClone 导出备份到 Backup/ 目录
  · Backup/Data/<timestamp>/Layout/ 下存在 com.android.launcher.tar
  · 已准备好 app_categories.json（可用 fetch_categories.py 生成）

版本：4.1
"""

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tarfile
import time
import xml.etree.ElementTree as ET

# Windows PowerShell 默认 GBK，强制 UTF-8 输出
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

# ============================================================================
#  路径自动检测
# ============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_backup_layout_dir():
    """自动查找 Backup/Data/<timestamp>/Layout/ 目录"""
    data_dir = os.path.join(SCRIPT_DIR, "Data")
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"未找到 Data 目录: {data_dir}")

    # 枚举 Data/ 下的时间戳子目录
    timestamps = sorted(
        [d for d in os.listdir(data_dir)
         if os.path.isdir(os.path.join(data_dir, d))],
        reverse=True,
    )
    if not timestamps:
        raise FileNotFoundError("Data/ 下没有找到备份目录")

    # 取最新一个
    ts = timestamps[0]
    layout_dir = os.path.join(data_dir, ts, "Layout")
    if not os.path.isdir(layout_dir):
        raise FileNotFoundError(f"未找到 Layout 目录: {layout_dir}")

    print(f"📁 备份目录: Data/{ts}/Layout/")
    return layout_dir


# ============================================================================
#  分类配置加载 (从 app_categories.json)
# ============================================================================

DEFAULT_CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_categories.json")


def load_categories_config(config_path=None):
    """
    从 JSON 文件加载分类配置。
    返回 (app_categories, category_order)
    """
    path = config_path or DEFAULT_CONFIG_FILE
    if not os.path.exists(path):
        print(f"  ❌ 未找到分类配置文件: {path}")
        print(f"     请先运行 fetch_categories.py 生成，或手动创建 app_categories.json")
        raise FileNotFoundError(f"分类配置文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # 去掉行尾 // 注释（app_categories 区域带应用名备注）
    clean_lines = []
    for line in raw.splitlines():
        last_quote = line.rfind('"')
        if last_quote >= 0:
            comment_pos = line.find('//', last_quote + 1)
            if comment_pos >= 0:
                line = line[:comment_pos].rstrip()
        clean_lines.append(line)
    config = json.loads('\n'.join(clean_lines))

    app_categories = config.get("app_categories", {})
    category_order = config.get("category_order", [])

    print(f"  📋 已加载分类配置: {len(app_categories)} 个应用, "
          f"{len(category_order)} 个分类")

    return app_categories, category_order


def _update_config_names(config_path, db_names):
    """
    将数据库中提取的应用名称合并到 app_categories.json 的行尾注释中。
    只补充缺失的名称，不覆盖已有注释。
    """
    path = config_path or DEFAULT_CONFIG_FILE
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = 0
    in_app_cats = False
    new_lines = []

    for line in lines:
        raw = line.rstrip('\n')
        if '"app_categories"' in raw and '{' in raw:
            in_app_cats = True
            new_lines.append(raw)
            continue
        if in_app_cats:
            stripped = raw.strip()
            if stripped.startswith('}'):
                in_app_cats = False
                new_lines.append(raw)
                continue
            # 匹配 "pkg": "cat" 行（可能已有注释也可能没有）
            m = re.match(r'^(\s*"([^"]+)":\s*"[^"]+"[,]?)\s*(//.*)?$', raw)
            if m:
                line_content = m.group(1)
                pkg = m.group(2)
                existing_comment = m.group(3)
                if not existing_comment and pkg in db_names:
                    raw = f"{line_content}  // {db_names[pkg]}"
                    updated += 1
        new_lines.append(raw)

    if updated > 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write('\n'.join(new_lines) + '\n')
        print(f"  🏷️  从数据库补充了 {updated} 个应用名到配置注释")


# ============================================================================
#  辅助函数
# ============================================================================

def escape_xml(s):
    """XML 属性值转义"""
    if not s:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def extract_package_name(intent_str):
    """从 intent 字符串中提取 packageName"""
    if not intent_str or "component=" not in intent_str:
        return None
    comp = intent_str.split("component=")[1].split(";")[0]
    return comp.split("/")[0]


def make_writable(path):
    """递归移除只读属性（Windows 上从 tar 解压的文件常是只读的）"""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            fp = os.path.join(root, name)
            try:
                os.chmod(fp, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass


# ============================================================================
#  Step 1: 解压 tar（如果尚未解压）
# ============================================================================

def ensure_tar_extracted(layout_dir):
    """确保 com.android.launcher.tar 已被解压到 data/ 目录"""
    tar_path = os.path.join(layout_dir, "com.android.launcher.tar")
    data_dir = os.path.join(layout_dir, "data")

    if not os.path.exists(tar_path):
        raise FileNotFoundError(f"未找到 tar 文件: {tar_path}")

    if os.path.isdir(data_dir):
        print("  ✅ data/ 目录已存在，跳过解压")
        return

    print("  📦 正在解压 com.android.launcher.tar …")
    with tarfile.open(tar_path, "r") as tar:
        tar.extractall(layout_dir)
    print(f"  ✅ 已解压到 {data_dir}")

    # 移除只读属性
    make_writable(data_dir)


# ============================================================================
#  Step 2: 读取数据
# ============================================================================

def find_db_path(layout_dir):
    """自动定位 launcher.db"""
    # 通常位于 data/user_de/0/com.android.launcher/databases/launcher.db
    pattern = os.path.join(layout_dir, "data", "**", "launcher.db")
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        raise FileNotFoundError(f"未找到 launcher.db (搜索: {pattern})")
    db_path = matches[0]
    print(f"  🗄️  数据库: {os.path.relpath(db_path, layout_dir)}")
    return db_path


def detect_table_names(db_path):
    """自动检测数据库表名（兼容 _draw 后缀和无后缀两种格式）"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    conn.close()

    has_main = "singledesktopitems" in tables
    has_draw = "singledesktopitems_draw" in tables

    if has_main and has_draw:
        # 两个都有：主表读写 + 同步到 draw
        return "singledesktopitems", "singledesktopscreens", True
    elif has_draw:
        # 只有 _draw 表（新版备份）
        return "singledesktopitems_draw", "singledesktopscreens_draw", False
    elif has_main:
        # 只有主表
        return "singledesktopitems", "singledesktopscreens", False
    else:
        raise RuntimeError(f"数据库中未找到桌面数据表！现有表: {tables}")


def read_db_items(db_path, items_table, screens_table):
    """读取数据库中的所有项目和屏幕"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {items_table} ORDER BY _id")
    items = [dict(row) for row in cur.fetchall()]
    cur.execute(f"SELECT * FROM {screens_table} ORDER BY _id")
    screens = [dict(row) for row in cur.fetchall()]
    conn.close()
    print(f"  📊 表: {items_table} ({len(items)} 条), {screens_table} ({len(screens)} 屏)")
    return items, screens


def extract_app_names_from_db(items):
    """从数据库条目的 title + intent 中提取 {包名: 应用名} 映射"""
    names = {}
    for item in items:
        intent = item.get("intent") or ""
        title = item.get("title") or ""
        if not intent or not title:
            continue
        m = re.search(r'component=([^/]+)/', intent)
        if m:
            pkg = m.group(1)
            if pkg not in names:
                names[pkg] = title.strip()
    return names


def categorize_items(items):
    """按类型分离数据库条目"""
    dock, widgets, cards, old_folders = [], [], [], []
    desktop_apps, in_folder_apps = [], []

    for item in items:
        it = item["itemType"]
        ct = item["container"]
        if it == 5:
            widgets.append(item)
        elif it == 100:
            cards.append(item)
        elif it == 3:
            old_folders.append(item)
        elif ct == -101:
            dock.append(item)
        elif ct == -100:
            desktop_apps.append(item)
        elif ct >= 0:
            in_folder_apps.append(item)

    return dock, widgets, cards, old_folders, desktop_apps, in_folder_apps


def deduplicate_apps(desktop_apps, in_folder_apps):
    """去重：同一 (intent, user_id) 只保留一个"""
    seen = set()
    unique = []
    for app in desktop_apps + in_folder_apps:
        intent = app.get("intent")
        if intent is None:
            continue
        key = (intent, app.get("user_id", 0))
        if key not in seen:
            seen.add(key)
            unique.append(app)
    return unique


# ============================================================================
#  Step 3: 规划新布局
# ============================================================================

def plan_layout(unique_apps, desktop_apps, app_categories, category_order):
    """
    规划新布局：
      · 第 0 屏: Widget + Card + 未分类的原第0屏应用（保留原位）
      · 第 1 屏起: 分类文件夹（4×6 网格）
    返回: (screen0_keep, folders_with_apps, uncategorized)
    """
    # 构建第0屏桌面应用 pkg→原始item 映射（用于保留原位）
    screen0_originals = {}
    for app in desktop_apps:
        if app.get("screen", -1) == 0 and app.get("container", 0) == -100:
            pkg = extract_package_name(app.get("intent", ""))
            if pkg and pkg not in screen0_originals:
                screen0_originals[pkg] = app

    categorized = {}
    uncategorized = []
    screen0_keep = []  # (app, pkg) — 保留在第0屏原位的应用

    for app in unique_apps:
        pkg = extract_package_name(app.get("intent", ""))
        if not pkg:
            continue

        cat = app_categories.get(pkg)
        if cat:
            categorized.setdefault(cat, []).append(app)
        elif pkg in screen0_originals:
            # 未分类但原来在第0屏 → 保留原位
            screen0_keep.append((screen0_originals[pkg], pkg))
        else:
            uncategorized.append(app)
            print(f"  ⚠️  未分类: {app.get('title', '?')} ({pkg})")

    # 按预定义顺序排列
    folders = [(c, categorized[c]) for c in category_order if c in categorized and categorized[c]]
    # 追加不在预定义顺序中的分类
    for c in sorted(categorized):
        if c not in category_order and categorized[c]:
            folders.append((c, categorized[c]))

    return screen0_keep, folders, uncategorized


# ============================================================================
#  Step 4: 分配 ID 和坐标，生成最终数据
# ============================================================================

def generate_layout(items, screens, app_categories, category_order):
    """核心函数：生成完整的新布局数据"""
    print("=" * 60)
    print("  ColorOS 桌面图标分类整理")
    print("=" * 60)

    dock, widgets, cards, old_folders, desktop_apps, in_folder_apps = categorize_items(items)
    print(f"\n  原始数据: {len(items)} 条")
    print(f"    Dock={len(dock)}  Widget={len(widgets)}  Card={len(cards)}")
    print(f"    旧文件夹={len(old_folders)}  桌面应用={len(desktop_apps)}  文件夹内={len(in_folder_apps)}")

    unique_apps = deduplicate_apps(desktop_apps, in_folder_apps)
    print(f"    去重后独立应用: {len(unique_apps)}")

    screen0_solo, folders_with_apps, uncategorized = plan_layout(
        unique_apps, desktop_apps, app_categories, category_order)

    print(f"\n  ===== 分类统计 =====")
    for cat, apps in folders_with_apps:
        print(f"    {cat}: {len(apps)} 个")
    if uncategorized:
        print(f"    未分类: {len(uncategorized)} 个")
        folders_with_apps.append(("其他", uncategorized))

    # ---- 分配 ID ----
    modified_time = int(time.time() * 1000)
    used_ids = set()
    final_items = []
    final_folders_xml = []

    # (a) Widget — 保持原样，screen=0
    for w in widgets:
        w_new = dict(w)
        w_new["screen"] = 0
        final_items.append(w_new)
        used_ids.add(w_new["_id"])

    # (b) Card — 保持原样，screen=0
    for c in cards:
        c_new = dict(c)
        c_new["screen"] = 0
        final_items.append(c_new)
        used_ids.add(c_new["_id"])

    # (c) Dock — 原样
    for d in dock:
        final_items.append(dict(d))
        used_ids.add(d["_id"])

    # 新 ID 从 100 开始，跳过已占用的
    next_id = 100
    def alloc_id():
        nonlocal next_id
        while next_id in used_ids:
            next_id += 1
        _id = next_id
        used_ids.add(_id)
        next_id += 1
        return _id

    # (d) 第 0 屏独立图标 — 保留原始坐标
    for app, pkg in screen0_solo:
        item = dict(app)
        item["_id"] = alloc_id()
        item["container"] = -100
        item["screen"] = 0
        # 保留原始 cellX/cellY（来自数据库原始数据）
        item["spanX"] = 1
        item["spanY"] = 1
        item["itemType"] = 0
        item["rank"] = 0
        item["modified"] = modified_time
        final_items.append(item)

    # (e) 文件夹 + 文件夹内应用（从 screen=1 开始，4×6 网格）
    cur_screen, cur_x, cur_y = 1, 0, 0

    for cat_name, cat_apps in folders_with_apps:
        if not cat_apps:
            continue

        folder_id = alloc_id()

        # 文件夹条目 (itemType=3)
        folder_item = {
            "_id": folder_id,
            "title": f" {cat_name}",
            "intent": None,
            "container": -100,
            "screen": cur_screen,
            "cellX": cur_x,
            "cellY": cur_y,
            "spanX": 1,
            "spanY": 1,
            "itemType": 3,
            "appWidgetId": -1,
            "iconPackage": None,
            "iconResource": None,
            "icon": None,
            "appWidgetProvider": None,
            "modified": modified_time,
            "restored": 0,
            "profileId": 0,
            "rank": 0,
            "options": 0,
            "appWidgetSource": -1,
            "user_id": 0,
            "iconType": None,
            "card_type": -1,
            "card_host_id": 1,
            "service_id": None,
            "card_category": -1,
            "editable_attributes": 0,
            "theme_card_identification": 0,
            "recommendId": -1,
        }
        final_items.append(folder_item)
        final_folders_xml.append({
            "_id": folder_id,
            "title": f" {cat_name}",
            "container": -100,
            "screen": cur_screen,
            "cellX": cur_x,
            "cellY": cur_y,
        })

        # 文件夹内应用
        for rank, app in enumerate(cat_apps):
            a = dict(app)
            a["_id"] = alloc_id()
            a["container"] = folder_id
            a["screen"] = 0          # 文件夹内部页 = 0
            a["cellX"] = rank % 3    # 文件夹 3 列网格
            a["cellY"] = rank // 3
            a["spanX"] = 1
            a["spanY"] = 1
            a["itemType"] = 0
            a["rank"] = rank
            a["modified"] = modified_time
            final_items.append(a)

        # 移至下一个格子
        cur_x += 1
        if cur_x >= 4:
            cur_x = 0
            cur_y += 1
            if cur_y >= 6:
                cur_y = 0
                cur_screen += 1

    # ---- 计算屏幕数 ----
    max_screen = max(
        (i["screen"] for i in final_items if i["container"] == -100 and i["itemType"] in (0, 3)),
        default=0,
    )
    total_screens = max_screen + 1

    new_screens = [{"_id": i, "screenRank": i, "modified": modified_time}
                   for i in range(total_screens)]

    return final_items, new_screens, final_folders_xml, total_screens


# ============================================================================
#  Step 5: 写入数据库
# ============================================================================


def write_database(db_path, final_items, new_screens, items_table, screens_table):
    """清空并重写指定的 items 和 screens 表"""
    print(f"\n--- 写入数据库 ({items_table}) ---")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 获取目标表的实际列名（兼容不同版本的列差异）
    cur.execute(f"SELECT * FROM {items_table} LIMIT 0")
    actual_cols = [d[0] for d in cur.description]

    cur.execute(f"DELETE FROM {items_table}")
    placeholders = ", ".join(["?"] * len(actual_cols))
    col_str = ", ".join(actual_cols)
    for item in final_items:
        vals = [item.get(c) for c in actual_cols]
        cur.execute(f"INSERT INTO {items_table} ({col_str}) VALUES ({placeholders})", vals)

    # 屏幕表
    cur.execute(f"DELETE FROM {screens_table}")
    for s in new_screens:
        cur.execute(f"INSERT INTO {screens_table} (_id, screenRank, modified) VALUES (?,?,?)",
                    (s["_id"], s["screenRank"], s["modified"]))

    conn.commit()

    # 统计
    cur.execute(f"SELECT COUNT(*) FROM {items_table}")
    total = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {items_table} WHERE itemType=3")
    folders = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {screens_table}")
    scr = cur.fetchone()[0]
    print(f"  ✅ {items_table}: {total} 条, {folders} 个文件夹, {scr} 个屏幕")

    conn.close()


def sync_draw_tables(db_path, need_sync):
    """将主表数据同步到 _draw 表（仅当主表和 draw 表同时存在时需要）"""
    if not need_sync:
        print("\n--- 同步 draw 表: 跳过（数据库仅有单套表）---")
        return

    print("\n--- 同步 draw 表 ---")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 读主表
    cur.execute("SELECT * FROM singledesktopitems")
    main_cols = [d[0] for d in cur.description]
    main_rows = cur.fetchall()

    # draw 表列名
    cur.execute("SELECT * FROM singledesktopitems_draw LIMIT 0")
    draw_cols = [d[0] for d in cur.description]

    cur.execute("DELETE FROM singledesktopitems_draw")
    placeholders = ", ".join(["?"] * len(draw_cols))
    col_str = ", ".join(draw_cols)
    for row in main_rows:
        item = dict(zip(main_cols, row))
        vals = [item.get(c) for c in draw_cols]
        cur.execute(f"INSERT INTO singledesktopitems_draw ({col_str}) VALUES ({placeholders})", vals)

    # 屏幕表
    cur.execute("SELECT * FROM singledesktopscreens")
    scr_rows = cur.fetchall()
    scr_cols = [d[0] for d in cur.description]

    cur.execute("DELETE FROM singledesktopscreens_draw")
    for row in scr_rows:
        vals = list(row)
        placeholders2 = ", ".join(["?"] * len(scr_cols))
        col_str2 = ", ".join(scr_cols)
        cur.execute(f"INSERT INTO singledesktopscreens_draw ({col_str2}) VALUES ({placeholders2})", vals)

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM singledesktopitems_draw")
    cnt = cur.fetchone()[0]
    print(f"  ✅ draw 表已同步: {cnt} 条")
    conn.close()


# ============================================================================
#  Step 6: 写入 XML
# ============================================================================

def build_xml_app_attrs(item, xml_screen_id, all_items):
    """构建单个 <application> 的 XML 属性"""
    intent_str = item.get("intent") or ""
    pkg, cls = "", ""
    if "component=" in intent_str:
        comp = intent_str.split("component=")[1].split(";")[0]
        if "/" in comp:
            pkg, cls = comp.split("/", 1)

    container = item["container"]
    screen = item["screen"]

    # 文件夹内应用的 screenId 应该是其所属文件夹所在屏幕的 screenId
    if container >= 0:
        for fi in all_items:
            if fi["_id"] == container and fi["itemType"] == 3:
                xml_screen_id = 1000 + fi["screen"]
                break

    new_screen = (item["screen"] if container == -101 else
                  0 if container >= 0 else screen)

    parts = [
        f'_id="{item["_id"]}"',
        f'title="{escape_xml(item.get("title") or "")}"',
        f'packageName="{pkg}"',
        f'className="{cls}"',
        f'container="{container}"',
        f'screenId="{xml_screen_id}"',
        f'screen="{screen}"',
        f'cellX="{item["cellX"]}"',
        f'cellY="{item["cellY"]}"',
        f'new_container="{container}"',
        f'new_screen="{new_screen}"',
        f'new_cellX="{item["cellX"]}"',
        f'new_cellY="{item["cellY"]}"',
        f'new_rank="{item.get("rank", 0)}"',
        f'curSpanX="1"', f'curSpanY="1"', f'spanX="1"', f'spanY="1"',
        f'rank="{item.get("rank", 0)}"',
        f'user_id="{item.get("user_id", 0)}"',
        f'intent="{escape_xml(intent_str)}"',
        f'restored="{item.get("restored", 0)}"',
        f'profileId="{item.get("profileId", 0)}"',
        f'options="{item.get("options", 0)}"',
    ]
    return " ".join(parts)


def write_xml(layout_dir, final_items, new_screens, final_folders_xml, total_screens, is_drawer=False):
    """生成 launcher_layout.xml 或 launcher_draw_layout.xml"""
    filename = "launcher_draw_layout.xml" if is_drawer else "launcher_layout.xml"
    output_path = os.path.join(layout_dir, filename)

    lines = [
        "<?xml version='1.0' encoding='UTF-8' standalone='no' ?>",
        '<LAYOUT dbVersion="85" minDowngradeVersion="28" isExpVersion="false">',
    ]

    if is_drawer:
        lines.append(
            '<DRAWER_MODE_SETTING show_indicate_app="true" add_app_to_workspace="true" '
            'drawer_layout_columns="4" drawer_default_page_view="0">'
            '<CATEGORY_ORDER category="other" order="11" />'
            '<CATEGORY_ORDER category="communicate" order="1" />'
            '<CATEGORY_ORDER category="education" order="10" />'
            '<CATEGORY_ORDER category="entertainment" order="4" />'
            '<CATEGORY_ORDER category="work" order="9" />'
            '<CATEGORY_ORDER category="suggestion" order="0" />'
            '<CATEGORY_ORDER category="games" order="6" />'
            '<CATEGORY_ORDER category="health" order="8" />'
            '<CATEGORY_ORDER category="travel" order="7" />'
            '<CATEGORY_ORDER category="tools" order="2" />'
            '<CATEGORY_ORDER category="photos" order="3" />'
            '<CATEGORY_ORDER category="shopping" order="5" />'
            '</DRAWER_MODE_SETTING>'
        )

    lines.append('<MODE_PARAMETERS cellCountX="4" cellCountY="6" />')

    # SCREENS
    lines.append("  <SCREENS>")
    for i in range(total_screens):
        lines.append(f'    <screen _id="{i + 1}" screenId="{1000 + i}" '
                     f'screenNum="{i}" new_id="{i}" screenRank="{i}" />')
    lines.append("  </SCREENS>")

    # APPLICATIONS（不含 Widget/Card/文件夹条目本身）
    lines.append("  <APPLICATIONS>")
    for item in final_items:
        if item["itemType"] in (5, 100, 3):
            continue
        scr_rank = item["screen"]
        xml_sid = (999 if item["container"] == -101 else 1000 + scr_rank)
        attrs = build_xml_app_attrs(item, xml_sid, final_items)
        lines.append(f"    <application {attrs} />")
    lines.append("  </APPLICATIONS>")

    # FOLDERS
    lines.append("  <FOLDERS>")
    for f in final_folders_xml:
        sid = 1000 + f["screen"]
        lines.append(
            f'    <folder _id="{f["_id"]}" title="{escape_xml(f["title"])}" '
            f'container="-100" screenId="{sid}" screen="{f["screen"]}" '
            f'cellX="{f["cellX"]}" cellY="{f["cellY"]}" '
            f'new_container="-100" new_screen="{f["screen"]}" '
            f'new_cellX="{f["cellX"]}" new_cellY="{f["cellY"]}" '
            f'new_rank="0" curSpanX="1" curSpanY="1" spanX="1" spanY="1" '
            f'recommendId="-1" options="0" />'
        )
    lines.append("  </FOLDERS>")

    # WIDGETS
    lines.append("  <WIDGETS>")
    for w in (i for i in final_items if i["itemType"] == 5):
        provider = w.get("appWidgetProvider") or ""
        pkg = cls = ""
        if "/" in provider:
            pkg, cls = provider.split("/", 1)
        lines.append(
            f'    <widget _id="{w["_id"]}" intent="{pkg}" '
            f'packageName="{pkg}" className="{cls}" '
            f'container="-100" screenId="1000" screen="0" '
            f'cellX="{w["cellX"]}" cellY="{w["cellY"]}" '
            f'new_container="-100" new_screen="0" '
            f'new_cellX="{w["cellX"]}" new_cellY="{w["cellY"]}" '
            f'new_rank="0" spanX="{w["spanX"]}" spanY="{w["spanY"]}" '
            f'appWidgetId="{w["appWidgetId"]}" restored="0" '
            f'appWidgetProvider="{provider}" />'
        )
    lines.append("  </WIDGETS>")

    # CARD
    lines.append("  <CARD>")
    for c in (i for i in final_items if i["itemType"] == 100):
        provider = c.get("appWidgetProvider") or ""
        lines.append(
            f'    <card _id="{c["_id"]}" title="{escape_xml(c.get("title") or "")}" '
            f'container="-100" screenId="1000" screen="0" '
            f'cellX="{c["cellX"]}" cellY="{c["cellY"]}" '
            f'new_container="-100" new_screen="0" '
            f'new_cellX="{c["cellX"]}" new_cellY="{c["cellY"]}" '
            f'new_rank="0" user_id="0" '
            f'spanX="{c["spanX"]}" spanY="{c["spanY"]}" '
            f'appWidgetId="{c["appWidgetId"]}" '
            f'card_type="{c.get("card_type", -1)}" '
            f'service_id="{c.get("service_id") or ""}" '
            f'editable_attributes="{c.get("editable_attributes", 0)}" '
            f'theme_card_identification="{c.get("theme_card_identification", 0)}" '
            f'card_category="{c.get("card_category", -1)}" '
            f'appWidgetProvider="{provider}" />'
        )
    lines.append("  </CARD>")

    lines.append("</LAYOUT>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  ✅ 已写入: {filename}")


# ============================================================================
#  Step 7: 重新打包 tar（精确匹配原始格式）
# ============================================================================

def repack_tar(layout_dir):
    """
    重新打包 com.android.launcher.tar。
    关键：遍历原始 tar 的成员列表，逐一用修改后的本地文件替换内容，
    保留原始 uid=0, gid=0, mode=0, GNU_FORMAT 格式。
    """
    tar_path = os.path.join(layout_dir, "com.android.launcher.tar")
    tar_bak = tar_path + ".bak"
    data_dir = os.path.join(layout_dir, "data")

    if not os.path.isdir(data_dir):
        print("  ⚠️  data/ 目录不存在，跳过 tar 打包")
        return

    if not os.path.exists(tar_bak):
        print("  ⚠️  com.android.launcher.tar.bak 不存在，无法获取原始成员列表")
        print("     将使用简化打包（可能与原始格式不完全一致）")
        _repack_tar_simple(tar_path, data_dir)
        return

    # 删除 db-journal（如果存在）
    db_dir = os.path.dirname(find_db_path_fast(layout_dir))
    journal = os.path.join(db_dir, "launcher.db-journal")
    if os.path.exists(journal):
        os.remove(journal)

    print("  📦 正在打包 com.android.launcher.tar (精确格式) …")

    # 读取原始 tar 的成员列表
    with tarfile.open(tar_bak, "r") as old_tar:
        old_members = old_tar.getmembers()

    # 新建 tar，遍历原始成员
    tar_tmp = tar_path + ".tmp"
    with tarfile.open(tar_tmp, "w", format=tarfile.GNU_FORMAT) as new_tar:
        for member in old_members:
            local_path = os.path.join(layout_dir, member.name)

            # 复制成员元数据并清零
            info = tarfile.TarInfo(name=member.name)
            info.type = member.type
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0
            info.mtime = member.mtime

            if member.isdir():
                info.type = tarfile.DIRTYPE
                info.size = 0
                new_tar.addfile(info)
            elif member.isfile():
                if os.path.exists(local_path):
                    info.size = os.path.getsize(local_path)
                    with open(local_path, "rb") as fh:
                        new_tar.addfile(info, fh)
                else:
                    # 文件在原始 tar 中存在但本地没有，跳过
                    print(f"    ⚠️  跳过缺失文件: {member.name}")
            else:
                # 链接等其他类型，原样添加
                new_tar.addfile(info)

    # 替换
    if os.path.exists(tar_path):
        os.remove(tar_path)
    os.rename(tar_tmp, tar_path)

    # 统计
    with tarfile.open(tar_path, "r") as check:
        count = len(check.getmembers())
    orig_count = len(old_members)
    size = os.path.getsize(tar_path)
    print(f"  ✅ 已打包: {count}/{orig_count} 条目, {size:,} 字节")


def find_db_path_fast(layout_dir):
    """快速定位 launcher.db (不打印)"""
    pattern = os.path.join(layout_dir, "data", "**", "launcher.db")
    matches = glob.glob(pattern, recursive=True)
    return matches[0] if matches else ""


def _repack_tar_simple(tar_path, data_dir):
    """降级方案：简单打包"""
    print("  📦 正在打包 com.android.launcher.tar (简化模式) …")
    with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tar:
        tar.add(data_dir, arcname="data")
    print(f"  ✅ 已打包: {os.path.getsize(tar_path):,} 字节")


# ============================================================================
#  Step 8: 验证
# ============================================================================

def verify(db_path, layout_dir, items_table, screens_table, need_draw_sync):
    """验证数据一致性"""
    print("\n" + "=" * 60)
    print("  验证")
    print("=" * 60)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 文件夹
    cur.execute(f"SELECT _id, title, screen, cellX, cellY FROM {items_table} WHERE itemType=3")
    folders = cur.fetchall()
    print(f"\n  数据库文件夹 ({len(folders)} 个):")
    for f in folders:
        cur.execute(f"SELECT COUNT(*) FROM {items_table} WHERE container=?", (f[0],))
        n = cur.fetchone()[0]
        print(f"    ID={f[0]:3d}  {f[1].strip():12s}  screen={f[2]} ({f[3]},{f[4]})  {n} 个应用")

    # 孤立应用
    cur.execute(f"""
        SELECT COUNT(*) FROM {items_table} s1
        WHERE s1.container >= 0
        AND NOT EXISTS (SELECT 1 FROM {items_table} s2 WHERE s2._id = s1.container AND s2.itemType=3)
    """)
    orphans = cur.fetchone()[0]
    print(f"\n  {'⚠️  孤立应用: ' + str(orphans) if orphans else '✅ 无孤立应用'}")

    # 坐标冲突
    cur.execute(f"""
        SELECT screen, cellX, cellY, COUNT(*) as cnt
        FROM {items_table}
        WHERE container=-100 AND itemType IN (0,3,5,100)
        GROUP BY screen, cellX, cellY HAVING cnt > 1
    """)
    conflicts = cur.fetchall()
    print(f"  {'⚠️  坐标冲突: ' + str(len(conflicts)) + ' 处' if conflicts else '✅ 无坐标冲突'}")

    # XML
    xml_path = os.path.join(layout_dir, "launcher_layout.xml")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    xml_folders = root.findall(".//FOLDERS/folder")
    xml_apps = root.findall(".//APPLICATIONS/application")
    print(f"  XML: {len(xml_apps)} 个应用, {len(xml_folders)} 个文件夹")

    # draw 表同步验证
    if need_draw_sync:
        cur.execute("SELECT COUNT(*) FROM singledesktopitems_draw")
        draw_cnt = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {items_table}")
        main_cnt = cur.fetchone()[0]
        match = "✅" if draw_cnt == main_cnt else "⚠️"
        print(f"  {match} 主表={main_cnt}, draw表={draw_cnt}")
    else:
        cur.execute(f"SELECT COUNT(*) FROM {items_table}")
        cnt = cur.fetchone()[0]
        print(f"  ✅ {items_table}: {cnt} 条")

    conn.close()


# ============================================================================
#  备份 / 恢复
# ============================================================================

def backup_originals(layout_dir, db_path):
    """备份原始文件（仅在 .bak 不存在时）"""
    pairs = [
        (db_path, db_path + ".bak"),
        (os.path.join(layout_dir, "launcher_layout.xml"),
         os.path.join(layout_dir, "launcher_layout.xml.bak")),
        (os.path.join(layout_dir, "launcher_draw_layout.xml"),
         os.path.join(layout_dir, "launcher_draw_layout.xml.bak")),
        (os.path.join(layout_dir, "com.android.launcher.tar"),
         os.path.join(layout_dir, "com.android.launcher.tar.bak")),
    ]
    for src, dst in pairs:
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f"  📋 已备份: {os.path.basename(src)}")


def restore_from_backup(layout_dir):
    """从 .bak 文件恢复原始状态"""
    print("\n🔄 正在从 .bak 恢复原始文件 …\n")

    # 通过 glob 搜索 Layout 目录下所有 .bak 文件（包括 data/ 子目录中的 db.bak）
    bak_pattern = os.path.join(layout_dir, "**", "*.bak")
    all_baks = glob.glob(bak_pattern, recursive=True)

    if not all_baks:
        print("  ⚠️  未找到任何 .bak 文件。")
        print("  💡 说明: .bak 文件在步骤 5（生成新布局）时自动创建。")
        print("     如果您尚未执行过步骤 5，则无需恢复，备份文件就是原始状态。")
        return

    restored = 0
    for bak_path in all_baks:
        # .bak 文件对应的原始文件: 去掉末尾的 .bak
        orig_path = bak_path[:-4]  # 去掉 ".bak"
        try:
            if os.path.exists(orig_path):
                os.chmod(orig_path, stat.S_IWRITE | stat.S_IREAD)
            shutil.copy2(bak_path, orig_path)
            print(f"  ✅ 已恢复: {os.path.relpath(orig_path, layout_dir)}")
            restored += 1
        except Exception as e:
            print(f"  ❌ 恢复失败: {os.path.basename(bak_path)} — {e}")

    if restored:
        print(f"\n✅ 已恢复 {restored} 个文件到原始状态。")
    else:
        print("\n⚠️  恢复操作未成功。")


# ============================================================================
#  主流程
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="ColorOS PhoneClone 桌面布局一键整理",
        epilog="分类配置从 app_categories.json 读取，可用 fetch_categories.py 生成。",
    )
    parser.add_argument("--restore", action="store_true", help="从 .bak 恢复原始布局")
    parser.add_argument("--config", type=str, metavar="JSON",
                        help="指定分类配置文件路径（默认: app_categories.json）")
    parser.add_argument("--workdir", type=str, metavar="DIR",
                        help="指定工作目录（覆盖脚本所在目录，供外部调用）")
    args = parser.parse_args()

    # 支持外部指定工作目录
    global SCRIPT_DIR, DEFAULT_CONFIG_FILE
    if args.workdir and os.path.isdir(args.workdir):
        SCRIPT_DIR = os.path.abspath(args.workdir)
        DEFAULT_CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_categories.json")

    # 自动查找备份目录
    layout_dir = find_backup_layout_dir()

    if args.restore:
        restore_from_backup(layout_dir)
        return

    # 加载分类配置
    print("\n📋 Step 0: 加载分类配置")
    app_categories, category_order = load_categories_config(args.config)

    print("\n📋 Step 1: 检查 tar 解压状态")
    ensure_tar_extracted(layout_dir)

    # 移除 data/ 下的只读属性
    data_dir = os.path.join(layout_dir, "data")
    if os.path.isdir(data_dir):
        make_writable(data_dir)

    db_path = find_db_path(layout_dir)

    print("\n📋 Step 2: 读取数据库")
    items_table, screens_table, need_draw_sync = detect_table_names(db_path)
    print(f"  📊 检测到表: {items_table}, {screens_table}" +
          (f" (需同步 draw 表)" if need_draw_sync else ""))
    items, screens = read_db_items(db_path, items_table, screens_table)

    # 从数据库中提取应用名称映射，回写到配置文件的注释中
    db_names = extract_app_names_from_db(items)
    if db_names:
        config_path = args.config or "app_categories.json"
        _update_config_names(config_path, db_names)

    print("\n📋 Step 3: 规划新布局")
    final_items, new_screens, final_folders_xml, total_screens = generate_layout(
        items, screens, app_categories, category_order)

    print(f"\n  ===== 布局计划 =====")
    print(f"    总条目: {len(final_items)}")
    print(f"    文件夹: {len(final_folders_xml)}")
    print(f"    总屏幕: {total_screens}")

    print("\n📋 Step 4: 备份原始文件")
    backup_originals(layout_dir, db_path)

    print("\n📋 Step 5: 写入数据库")
    write_database(db_path, final_items, new_screens, items_table, screens_table)
    sync_draw_tables(db_path, need_draw_sync)

    print("\n📋 Step 6: 写入 XML")
    write_xml(layout_dir, final_items, new_screens, final_folders_xml, total_screens, is_drawer=False)
    write_xml(layout_dir, final_items, new_screens, final_folders_xml, total_screens, is_drawer=True)

    print("\n📋 Step 7: 重新打包 tar")
    repack_tar(layout_dir)

    print("\n📋 Step 8: 验证")
    verify(db_path, layout_dir, items_table, screens_table, need_draw_sync)

    print("\n" + "=" * 60)
    print("  ✅ 全部完成！")
    print("=" * 60)
    print("\n  📱 请将整个 Backup 目录放回手机，使用 PhoneClone 恢复。")
    print("  🔄 如需恢复原始布局: python reorganize_layout_oneclick.py --restore")
    print("  📝 如需自定义分类: 编辑 app_categories.json 或使用 fetch_categories.py")


if __name__ == "__main__":
    main()
