# LOL ARAM Mayhem Hextech Helper 项目规则

本文件是本项目的 Codex 规则入口。全局安全约束仍然有效；本文件只补充本项目特有的约定。

## 项目目标

这是一个 Windows 桌面工具：通过 LCU/Live Client API 获取当前英雄，使用屏幕截图与 RapidOCR 识别大乱斗海克斯选项，再从本地数据中匹配并推荐海克斯。

当前运行环境要求 Python 3.9～3.12，推荐 Python 3.12。项目面向 Windows，依赖管理员权限、全局热键和英雄联盟客户端本地接口。

## 目录与职责

- `gui_launcher.py`：当前推荐入口，负责 Tkinter 主界面、系统托盘、权限提示和后台引擎生命周期。
- `main.py`：数据管理、OCR/截图分析、悬浮遮罩，以及旧的控制台入口。
- `scripts/`：LCU 连接、OP.GG 抓取、数据更新、路径配置等可复用模块。
- `data/`：运行时数据。`hero_augments.csv` 是主数据，`champions.json` 是英雄名称映射，`pinyin_map.json` 是搜索索引。
- `assets/`：图标等打包资源。
- `docs/`：项目说明和演示图片。
- `runtime_hooks/`：PyInstaller 运行时 hook，只放打包所需的 hook。
- `build.py`：PyInstaller 打包脚本。

本地虚拟环境、构建产物和缓存（例如 `.venv/`、`dist/`、`build/`、`*.spec`、`__pycache__/`）默认不提交。不要把临时脚本、日志或下载文件放入源码目录；确需保留时先约定目录和清理时机。

## 常用命令

首次准备环境时，在项目根目录执行：

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

环境已经创建后，直接激活即可：

```powershell
.\.venv\Scripts\Activate.ps1
python gui_launcher.py
python build.py
```

`gui_launcher.py` 是开发运行入口。运行真实功能时需要英雄联盟客户端、无边框游戏窗口以及管理员权限；没有游戏客户端时只能做静态检查或有限的启动检查。

当前没有自动化测试套件。完成 Python 改动后至少执行一次只读语法检查：

```powershell
python -c "import ast,pathlib; files=[p for p in pathlib.Path('.').rglob('*.py') if '.git' not in p.parts]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print('AST_PARSE_OK', len(files))"
```

不要把“语法解析通过”描述成 OCR、LCU 或 GUI 功能已验证。功能验证必须明确记录所需的 Python 版本、依赖、游戏客户端状态和实际结果。

## 数据约定

- 不要为临时测试直接覆盖 `data/` 中的正式数据。
- 数据更新会访问 Data Dragon、OP.GG 或 GitHub，并可能改写 CSV/JSON；执行全量更新前先确认目标和备份策略。
- 修改数据格式时，必须同步检查 `main.py` 的加载逻辑、`scripts/updater.py` 的读写逻辑和打包后的外部数据路径。
- LCU token、密码和其他本地凭据只能驻留在运行时，不得写入代码、数据文件、日志或提交记录。

## 修改与交付纪律

- 先读取本文件和 `ROADMAP.md`，再修改代码。
- 只修改完成当前需求所需的文件，不顺手重构相邻代码。
- 修复缺陷时先建立可复现证据，并在产生错误语义的责任层修复。
- 每次完成代码、文档或重要调研后更新 `ROADMAP.md`；只有已经实现并验证的事项才能放入“已完成”。
- 新增依赖、修改打包参数、改变数据格式或改变用户操作方式时，在交付说明中写明影响和验证结果。
