# 插件开发指南

本目录用于存放 Gemini Live API 的自定义插件，支持两种格式：
1. **Python 函数格式** - 传统的 Function Calling 工具
2. **Agent Skills 格式** - 标准的 `SKILL.md` 指令文件

## 目录结构

```
plugins/
├── __init__.py              # 插件加载器核心
├── README.md                # 本文档
├── examples/                # Python 函数插件示例
│   ├── weather.py           # 天气查询（同步）
│   └── calculator.py        # 计算器（异步）
└── obsidian-cli/            # Agent Skill 示例
    └── SKILL.md             # Skill 定义
```

---

## 格式 1：Python 函数插件

### 插件接口

每个 `.py` 文件定义 `register()` 函数，返回 `(tool_mapping, tools)`:

```python
def register() -> tuple[dict, list]:
    """返回 (函数名->函数对象的字典, types.Tool列表)"""
    return tool_mapping, tools
```

### 示例

```python
from google.genai import types

def get_weather(city: str) -> str:
    """获取城市天气"""
    return f"{city} 今天晴天，25度"

def register() -> tuple:
    tool_mapping = {"get_weather": get_weather}
    tools = [
        types.Tool(function_declarations=[{
            "name": "get_weather",
            "description": "获取指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }])
    ]
    return tool_mapping, tools
```

---

## 格式 2：Agent Skills（SKILL.md）

这是 [Agent Skills](https://agentskills.io) 开放标准的格式，已被 Claude Code、Cursor 等工具支持。

### 目录结构

```
skill-name/
└── SKILL.md          # 必需：YAML frontmatter + 指令
├── scripts/          # 可选：可执行脚本
├── references/       # 可选：参考文档
└── assets/           # 可选：模板/资源
```

### SKILL.md 格式

```yaml
---
name: my-skill
description: 技能描述，说明何时使用此技能
---

# 指令内容

这里是 AI 遵循的指令...
```

### frontmatter 字段

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | 是 | 技能名称（小写，连字符） |
| `description` | 是 | 描述技能用途和适用场景 |

### 示例：obsidian-cli

```
plugins/obsidian-cli/
└── SKILL.md
```

```yaml
---
name: obsidian-cli
description: 通过 CLI 操作 Obsidian vault。当用户询问个人知识、笔记内容时使用。
---

# Obsidian CLI Skill

此 skill 允许 AI 通过 Obsidian CLI 操作用户的第二大脑。

## 可用操作

### 1. 搜索笔记
```bash
obsidian search query="关键词"
```

### 2. 读取笔记内容
```bash
obsidian read file="path/to/note.md"
```

## 使用指南

1. 按需检索，不要一次性加载整个 vault
2. 先搜索后读取
3. 总结而非全文复制
```

### 使用 AI 调用 Skill

当 AI 需要使用某个 Skill 时，会调用对应的工具：

```
tool_call: obsidian-cli({"action": "get_content"})
```

返回的是 SKILL.md 的完整内容，AI 会根据这些指令来执行任务。

---

## 加载优先级

工具映射合并规则：
- `tool_mapping = {**plugin_tool_mapping, **builtin_tool_mapping}`
- 后加载的内置工具会覆盖同名的插件工具

---

## 验证

```bash
source venv/bin/activate
python -c "from plugins import load_plugins; tm, t = load_plugins(); print('Tools:', list(tm.keys()))"
```
