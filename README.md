# ColorOS 桌面图标分类整理工具

[English](#english) | [中文](#中文)

## 中文

### 功能简介

ColorOS 桌面图标分类整理工具是为 OPPO/一加手机用户设计的自动化工具，可以通过 官方手机备份应用 本地备份桌面图标快速整理桌面应用，并按自定义分类自动生成文件夹布局。

**主要功能：**
- 🔌 **ADB 连接手机** — 自动拉取备份文件到PC本地
- 📊 **智能应用分类** — 三层分类引擎：
  - Google Play 云端查询（国际应用）
  - 腾讯应用宝查询（中国应用）
  - AI 智能分类（OpenAI 兼容 API）
- ⚙️ **自动整理生成** — 根据分类修改 SQLite 数据库 + XML + tar 打包
- 🔄 **推回手机** — 完整替换手机上的备份，恢复即可应用
- 💾 **增量保存** — 支持中途中断，下次继续

### 系统要求

- Python 3.8+
- Windows / macOS / Linux
- ADB（安卓调试桥）
- Android 手机 + 官方手机搬家 应用(ColorOS/Oxygen版本通杀)

### 安装与使用

#### 方式一：BAT 快速启动（推荐，Python 用户）

项目中包含 `layout_patch.bat` 脚本，双击即可快速启动：

```
ColorOS-Layout-Patcher/
├── layout_patch.bat          ← 双击运行
├── layout_patch_main.py
└── ...
```

**首次使用需要准备环境：**

```bash
# 1. 克隆仓库
git clone https://github.com/Andrea-lyz/ColorOS-Launcher-LayoutOrganizer.git
cd ColorOS-Launcher-LayoutOrganizer

# 2. 安装依赖（仅需一次）
pip install -r requirements.txt

# 3. 之后直接双击 layout_patch.bat 运行
```

**关于 ADB：**
- 如果 adb 已在 PATH 中，BAT 脚本会自动找到
- 否则，从 [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) 下载并解压到项目的 `platform-tools/` 目录

#### 方式二：Python 脚本（完全控制）

```bash
# 克隆仓库
git clone https://github.com/Andrea-lyz/ColorOS-Launcher-LayoutOrganizer.git
cd ColorOS-Launcher-LayoutOrganizer

# 安装依赖
pip install -r requirements.txt

# 运行主程序
python layout_patch_main.py

# 或分别运行各模块
python fetch_categories.py --from-db       # 提取包名
python fetch_categories.py --classify      # 云端分类
python fetch_categories.py --classify-ai   # AI 分类
python fetch_categories.py --interactive   # 交互式处理
python reorganize_layout_oneclick.py       # 生成布局
```

#### 方式三：单文件 EXE（普通用户，仅 Windows）

**前置准备（手机上操作）：**
- 用 OPPO 手机自带的**手机搬家**应用导出单独的**桌面布局**完整备份
- 将备份复制到电脑的 `Backup/` 目录（推荐运行 exe 时自动拉取）
- 开启 **USB 调试**

**使用步骤：**
1. 从 Releases 页面下载 `ColorOS图标分类整理工具.exe`（ADB、Python 已内置）
2. 确保手机已连接 USB，USB 调试已开启
3. 直接运行 exe，按菜单提示操作（1 → 2 → 3/3A → 4 → 5 → 6）
4. 完成后，打开手机搬家，本地备份恢复修改后的备份

**EXE 优势：**
- ✅ 无需安装 Python、ADB、依赖库
- ✅ 单文件即用（下载即运行）
- ✅ 完全离线运行（除了网络分类功能）

### 菜单结构

```
0.  环境检测
1.  从手机拉取备份到本地
2.  从备份数据库提取应用列表
3.  多渠道云端自动分类 (Google Play + 应用宝)
3A. AI 智能分类 (OpenAI 兼容 API)
4.  交互式处理未分类应用
5.  生成新布局 → 写入 DB + XML + tar
6.  推回手机 (完整替换备份)

S.  查看分类统计
C.  AI 分类设置 (URL / Key / Model)
R.  恢复手机上备份为原始状态
```

### 配置 AI 分类（可选）

工具支持**任何兼容 OpenAI API 的服务**，推荐可联网大模型

**配置步骤：**
1. 在主菜单选择 **C**
2. 输入 API 的 Base URL、API Key、Model 名称
3. 配置自动保存到 `ai_config.json`
4. 选择 **3A** 开始 AI 分类

### 打包为 EXE

如果你要修改代码后重新打包：

```bash
# 1. 下载 Android SDK Platform-Tools 并解压到项目目录
# https://developer.android.com/tools/releases/platform-tools

# 2. 安装 PyInstaller
pip install pyinstaller

# 3. 运行打包脚本
python build_exe.py

# 4. 输出文件位置: dist/ColorOS图标分类整理工具.exe
```

### 分类标签说明

工具支持以下 18 个应用分类：

| 分类 | 说明 |
|------|------|
| 社交通讯 | 微信、QQ、Telegram、Discord 等即时通讯 |
| 影音娱乐 | 视频、音乐、直播、漫画、小说等 |
| 购物电商 | 淘宝、京东、拼多多、亚马逊等 |
| 金融支付 | 支付宝、银行、理财、股票等 |
| 出行旅行 | 地图、导航、打车、机票、酒店等 |
| 外卖生活 | 美团、饿了么、菜谱等 |
| 生活服务 | 健康、快递、天气、物业等 |
| 系统工具 | 输入法、文件管理、计算器等 |
| 系统应用 | 手机厂商预装应用（设置、日历、天气等） |
| 代理工具 | VPN、代理、翻墙工具等 |
| Root工具 | Magisk、LSPosed、MT管理器等 |
| AI工具 | ChatGPT、DeepSeek、Gemini 等 AI 助手 |
| 学校学习 | 教育、翻译、词典、考试等 |
| 媒体工具 | 拍照、修图、壁纸等 |
| 资讯社区 | 新闻、论坛、知乎、微博等 |
| 浏览器 | Chrome、Firefox、Edge 等 |
| 智能家居 | 米家、HomeKit 等智能硬件控制 |
| 游戏 | 所有游戏应用 |

可通过 'fetch_categories.py' 修改AI提示词 
### 常见问题

**Q：运行时提示 "未找到 adb"**  
A：需要安装 Android SDK Platform-Tools，或将 adb 添加到 PATH。

**Q：云端分类很慢**  
A：受网络限制，Google Play 和应用宝查询需要联网。可以用 AI 分类加速（推荐！但需配置 API）。

**Q：中途按了键，是否会丢失已查询的结果？**  
A：不会。所有长时间操作都支持中断保存，下次运行会继续。

**Q：修改后的布局不生效**  
A：确保已完成步骤 6（推回手机），然后在手机端用 本地备份 恢复备份。

### 开发与贡献

欢迎提交 Issue 和 Pull Request！主要代码模块：

- `layout_patch_main.py` (1000+ 行) — 主控脚本和菜单
- `fetch_categories.py` (1600+ 行) — 应用分类引擎
- `reorganize_layout_oneclick.py` (1100+ 行) — 布局数据库操作
- `build_exe.py` — PyInstaller 打包脚本

### 许可证

MIT License — 见 [LICENSE](LICENSE) 文件

---

## English

### Overview

ColorOS Layout Patcher is an automated tool for OPPO/OnePlus users to quickly organize desktop applications through PhoneClone backups and auto-generate custom folder layouts by classification.

**Key Features:**
- 🔌 **ADB Device Connection** — Auto-pull PhoneClone backups
- 📊 **Intelligent App Classification** — Three-layer engine:
  - Google Play Cloud Lookup (International Apps)
  - Tencent AppBao Lookup (Chinese Apps)
  - AI Classification (OpenAI-compatible API)
- ⚙️ **Auto Icon Organization** — Modify SQLite DB + XML + tar based on classification
- 🔄 **Push Back to Phone** — Full replacement, restore and apply
- 💾 **Incremental Save** — Interrupt-safe, resume support

### Requirements

- Python 3.8+
- Windows / macOS / Linux
- ADB (Android Debug Bridge)
- Android Phone + Official Phone Clone App (supports ColorOS/Oxygen)

### Installation & Usage

#### Method 1: Python Script (Developers)

```bash
git clone https://github.com/Andrea-lyz/ColorOS-Launcher-LayoutOrganizer.git
cd ColorOS-Launcher-LayoutOrganizer
pip install requests openai
python layout_patch_main.py
```

#### Method 2: Single EXE File (Windows Users)

**Phone Preparation (on your phone):**
- Use OPPO's built-in **Phone Clone** app to export a complete **Desktop Layout** backup
- Copy backup to your computer's `Backup/` directory (recommended: auto-fetch during exe run)
- Enable **USB Debugging**

**Usage Steps:**
1. Download `ColorOS图标分类整理工具.exe` from Releases (ADB + Python included)
2. Connect phone via USB with debugging enabled
3. Run exe and follow menu prompts (1 → 2 → 3/3A → 4 → 5 → 6)
4. After completion, open Phone Clone and restore the modified backup locally
2. Ensure USB debugging is enabled on phone
3. Run the exe and follow menu prompts

### License

MIT License — See [LICENSE](LICENSE)
