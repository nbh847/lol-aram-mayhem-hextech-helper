# 项目规则入口

本项目的完整项目规则以根目录 `AGENTS.md` 为唯一来源。使用 Claude Code 时，必须先读取并遵守 `AGENTS.md`，不要自行创建第二套相互冲突的项目约定。

关键入口和命令：

- 开发入口：`gui_launcher.py`
- 核心逻辑：`main.py`
- 数据与连接模块：`scripts/`
- 打包命令：`python build.py`
- 运行前置：Windows、Python 3.9～3.12、项目依赖；真实功能还需要英雄联盟客户端与管理员权限。
- 完成改动后更新 `ROADMAP.md`，并区分静态检查与真实功能验证。

