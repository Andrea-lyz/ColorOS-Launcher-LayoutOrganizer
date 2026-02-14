# 快速开始指南

## 对于普通用户（最简单）

### 1. 下载 EXE
从 [Releases](https://github.com/Andrea-lyz/ColorOS-Launcher-LayoutOrganizer/releases) 页面下载最新的 `ColorOS布局修补工具.exe`

### 2. 准备手机
- 用 OPPO 手机自带的**手机搬家**或 **PhoneClone** 应用导出完整备份
- 将备份复制到电脑的 `Backup/` 目录（或运行 exe 时自动拉取）
- 开启 **USB 调试**

### 3. 运行工具
```
双击运行 ColorOS布局修补工具.exe
```

**优势：**
- ✅ 无需安装 Python、ADB、依赖库
- ✅ 单文件即用（下载即运行）
- ✅ 完全离线运行（除了网络分类功能）

### 4. 按菜单顺序操作
```
1 → 2 → 3(或3A) → 4 → 5 → 6
```

每步说明：
- **1. 拉取备份** — 自动从手机复制最新备份
- **2. 提取应用** — 读取数据库中所有已安装应用
- **3. 云端分类** — Google Play + 应用宝自动识别
- **3A. AI 分类** — 可选，用 AI 进一步精细分类
- **4. 交互处理** — 手动调整未自动分类的应用
- **5. 生成布局** — 修改备份文件
- **6. 推回手机** — 覆盖手机上的备份

### 5. 在手机上恢复
打开 **手机搬家 / PhoneClone**，恢复修改后的备份，完成！

---

## 对于开发者（Python + BAT）

### 1. 克隆仓库
```bash
git clone https://github.com/Andrea-lyz/ColorOS-Launcher-LayoutOrganizer.git
cd ColorOS-Launcher-LayoutOrganizer
```

### 2. 安装依赖（仅需一次）
```bash
pip install -r requirements.txt
```

### 3. 直接双击 layout_patch.bat 运行
- 或者 `python layout_patch_main.py` 直接运行
- 如果 adb 不在 PATH 中，需要从 [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) 下载并解压到项目的 `platform-tools/` 目录

### 4. 确保 ADB 在 PATH 中或已配置
```bash
adb version  # 确保能找到 adb
```

### 4. 运行主程序
```bash
python layout_patch_main.py
```

### 5. 或分别调用各模块
```bash
# 从备份数据库提取包名
python fetch_categories.py --from-db

# 多渠道自动分类
python fetch_categories.py --classify

# AI 分类（需配置 API）
python fetch_categories.py --classify-ai

# 交互式处理
python fetch_categories.py --interactive

# 补充应用名
python fetch_categories.py --enrich-names

# 一步到位
python fetch_categories.py --all

# 查看分类统计
python fetch_categories.py --stats

# 生成新布局
python reorganize_layout_oneclick.py

# 恢复到原始状态
python reorganize_layout_oneclick.py --restore
```

---

## 配置 AI 分类（可选）

工具支持兼容 **OpenAI API** 的任何服务。推荐可联网大模型。选择一个即可：

| API 提供商 | Base URL | 推荐度 |
|-----------|----------|--------|
| **DeepSeek** | https://api.deepseek.com/v1 | ⭐⭐⭐（免费额度充足） |
| **OpenAI** | https://api.openai.com/v1 | ChatGPT（需付费） |
| **通义千问** | https://dashscope.aliyuncs.com/compatible-mode/v1 | 免费试用 |
| **本地 Ollama** | http://localhost:11434/v1 | 本地离线部署 |

### 快速配置步骤

1. 在主菜单选择 **C**（AI 分类设置）

2. 按提示输入三项参数：
   - Base URL（API 地址）
   - API Key（从服务商获取）
   - Model（模型名称）

3. 配置自动保存到 `ai_config.json`

### API 参数示例

**DeepSeek（推荐）**
```
Base URL: https://api.deepseek.com/v1
API Key: sk-[从 dashboard 获取]
Model: deepseek-chat
```

**OpenAI**
```
Base URL: https://api.openai.com/v1
API Key: sk-[从 dashboard 获取]
Model: gpt-4-turbo
```

**本地 Ollama（离线）**
```
Base URL: http://localhost:11434/v1
API Key: ollama
Model: llama2
```

**通义千问**
```
Base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
API Key: sk-[从 dashboard 获取]
Model: qwen-turbo
```

### 修改 AI 分类提示词

如需自定义 AI 分类的行为，可以编辑 `fetch_categories.py` 中的 `AI_SYSTEM_PROMPT` 变量（约第 930 行），调整分类逻辑和示例。

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

---

## 故障排除

### ADB 相关
**问题**：`❌ 未找到 adb`
```bash
# 解决：下载 Android SDK Platform-Tools
# https://developer.android.com/tools/releases/platform-tools

# 方案 1：添加到 PATH（Windows）
setx PATH "%PATH%;C:\path\to\platform-tools"

# 方案 2：直接复制到项目 platform-tools/ 文件夹中
# ColorOS-Launcher-LayoutOrganizer/
# ├── platform-tools/
# │   └── adb.exe
# └── layout_patch.bat
```

### 网络相关
**问题**：云端分类超时或无响应
- 检查网络连接
- Google Play 和应用宝都需要国际网络访问
- 考虑改用 AI 分类（更快且更准）

### 中断重启
**问题**：中途关闭或中断后如何继续？
- 已查询的结果会自动保存
- 下次运行相同的步骤会自动跳过已处理的应用
- 配置文件保存在工作目录的 `app_categories.json` 中

### 恢复原状
如果分类结果不满意，可以：
```bash
# 选择菜单 R 恢复原始备份
# 或手动删除生成的 .bak 文件后重新操作
```

---

## 常见问题

**Q：为什么要用 PhoneClone 备份？**  
A：只有 PhoneClone 备份才包含完整的启动器数据库和布局信息。普通备份无法修改。

**Q：修改后的布局能否撤销？**  
A：可以。工具会自动创建 `.bak` 备份文件，选择菜单 **R** 随时恢复。

**Q：支持所有 OPPO 手机吗？**  
A：支持使用 ColorOS 6.0+ 的 OPPO、OnePlus、Realme 手机。其他品牌的 Android 手机可能数据库结构不同，不保证兼容。

**Q：隐私安全吗？**  
A：本工具完全离线，不会上传任何数据到云端。所有操作都在本地计算机进行。唯一的网络请求是：
- 查询 Google Play 和应用宝的公开信息
- 调用用户自己配置的 AI API（用户全程控制）

---

## 报告问题

发现 Bug 或有改进建议？请提交 [Issue](https://github.com/Andrea-lyz/ColorOS-Launcher-LayoutOrganizer/issues)

提供以下信息会更有帮助：
- 手机型号和 ColorOS 版本
- 错误信息和堆栈跟踪
- 重现步骤
- `app_categories.json` 中的相关条目（如有）
