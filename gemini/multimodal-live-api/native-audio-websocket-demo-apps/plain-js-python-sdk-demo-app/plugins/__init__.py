"""
插件系统核心模块

支持两种格式：
1. SKILL.md 格式（Agent Skills 标准）- 纯文本指令，作为 system_instruction 注入
2. Python 函数格式（Function Calling）- 可执行函数

SKILL.md 作为 AI 的行为准则直接注入 system_instruction，
AI 理解指令后直接执行操作，不通过 Function Calling 获取。
"""

import importlib.util
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Skill:
    """
    Agent Skill 封装类

    SKILL.md 格式的指令文档，AI 直接遵循执行。
    不再作为 Function Calling 工具。
    """

    def __init__(self, skill_dir: Path):
        """
        初始化 Skill

        Args:
            skill_dir: skill 目录路径（包含 SKILL.md）
        """
        self.dir = skill_dir
        self.name = skill_dir.name
        self.skill_md_path = skill_dir / "SKILL.md"
        self.description = ""
        self.content = ""
        self.scripts_dir = skill_dir / "scripts"
        self._load()

    def _load(self):
        """加载 SKILL.md 文件"""
        if not self.skill_md_path.exists():
            raise FileNotFoundError(f"SKILL.md not found: {self.skill_md_path}")

        with open(self.skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 解析 frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1].strip()
                self.content = parts[2].strip()

                # 解析 YAML frontmatter
                for line in frontmatter.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        key = key.strip()
                        value = value.strip()
                        if key == "description":
                            self.description = value
            else:
                self.content = content
        else:
            self.content = content

        if not self.description:
            # 从第一段提取 description
            first_para = self.content.split("\n\n")[0]
            self.description = first_para[:200]

    def get_instruction(self) -> str:
        """
        获取此 skill 的完整指令内容

        用于作为 system_instruction 的一部分注入给 AI。

        Returns:
            SKILL.md 的完整内容
        """
        return self.content

    def execute_script(self, script_path: str, args: dict = None) -> str:
        """
        执行 skill 中的脚本

        Args:
            script_path: 脚本路径（相对于 scripts_dir）
            args: 脚本参数

        Returns:
            脚本输出
        """
        full_path = self.scripts_dir / script_path
        if not full_path.exists():
            return f"Script not found: {script_path}"

        try:
            # 根据脚本类型执行
            ext = full_path.suffix.lower()
            if ext == ".py":
                cmd = ["python", str(full_path)]
            elif ext == ".sh":
                cmd = ["bash", str(full_path)]
            else:
                return f"Unsupported script type: {ext}"

            if args:
                for key, value in args.items():
                    cmd.extend([f"--{key}", str(value)])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return f"Error: {result.stderr.strip()}"

        except subprocess.TimeoutExpired:
            return "Script timed out"
        except Exception as e:
            return f"Script error: {str(e)}"


class PluginLoader:
    """插件加载器，支持 SKILL.md 和 Python 函数两种格式"""

    def __init__(self, plugins_dir: str = None):
        """
        初始化插件加载器

        Args:
            plugins_dir: 插件目录路径，默认为当前目录（即 plugins/）
        """
        if plugins_dir is None:
            plugins_dir = os.path.dirname(__file__)
        self.plugins_dir = Path(plugins_dir)
        self.skills = []  # Agent Skills
        self.py_plugins = []  # Python 函数插件

    def _discover_skills(self) -> list[Path]:
        """发现所有 SKILL.md 文件"""
        skills = []

        if not self.plugins_dir.exists():
            logger.warning(f"插件目录不存在: {self.plugins_dir}")
            return skills

        # 扫描所有子目录，查找 SKILL.md
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    skills.append(item)

        return skills

    def _discover_py_plugins(self) -> list[Path]:
        """发现所有 Python 插件文件（.py，排除 __init__ 和 SKILL.md）"""
        plugins = []

        for file_path in self.plugins_dir.glob("*.py"):
            if file_path.name.startswith("__"):
                continue
            plugins.append(file_path)

        # 递归发现子目录中的插件
        for sub_dir in self.plugins_dir.iterdir():
            if sub_dir.is_dir() and not sub_dir.name.startswith("__"):
                for file_path in sub_dir.glob("*.py"):
                    if file_path.name.startswith("__"):
                        continue
                    # 排除包含 SKILL.md 的目录中的 .py 文件
                    if (file_path.parent / "SKILL.md").exists():
                        continue
                    plugins.append(file_path)

        return plugins

    def _load_py_plugin(self, file_path: Path) -> tuple[str, Any] | None:
        """加载单个 Python 插件模块"""
        module_name = file_path.stem

        # 根据文件位置判断模块名前缀
        if file_path.parent.name == "plugins":
            full_name = f"plugins.{module_name}"
        else:
            # 子目录中的插件
            subdir = file_path.parent.name
            full_name = f"plugins.{subdir}.{module_name}"

        try:
            spec = importlib.util.spec_from_file_location(full_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return full_name, module
        except Exception as e:
            logger.error(f"加载插件模块 {file_path} 失败: {e}")
            return None

    def _validate_py_plugin(self, module: Any, module_name: str) -> bool:
        """验证 Python 插件是否符合规范（有 register 函数）"""
        if not hasattr(module, "register"):
            logger.warning(f"插件 {module_name} 缺少 register() 函数")
            return False
        if not callable(module.register):
            logger.warning(f"插件 {module_name} 的 register 不是可调用对象")
            return False
        return True

    def _load_py_plugins(self) -> tuple[dict, list]:
        """加载所有 Python 函数插件"""
        combined_tool_mapping = {}
        combined_tools = []

        plugins = self._discover_py_plugins()
        logger.info(f"发现 {len(plugins)} 个 Python 插件文件")

        for file_path in plugins:
            result = self._load_py_plugin(file_path)
            if result is None:
                continue

            full_name, module = result

            if not self._validate_py_plugin(module, full_name):
                continue

            try:
                tool_mapping, tools = module.register()

                # 验证返回格式
                if not isinstance(tool_mapping, dict):
                    logger.warning(f"插件 {full_name} 的 register() 应返回 dict 作为第一个元素")
                    continue
                if not isinstance(tools, list):
                    logger.warning(f"插件 {full_name} 的 register() 应返回 list 作为第二个元素")
                    continue

                # 合并到总工具集
                combined_tool_mapping.update(tool_mapping)
                combined_tools.extend(tools)

                self.py_plugins.append(full_name)
                logger.info(f"Python 插件加载成功: {full_name}, 包含 {len(tool_mapping)} 个工具")

            except Exception as e:
                logger.error(f"调用插件 {full_name} 的 register() 失败: {e}")

        return combined_tool_mapping, combined_tools

    def _load_skills(self) -> tuple[dict, list]:
        """
        加载所有 Agent Skills

        Returns:
            tuple: (skill_mapping, skills_list)
                - skill_mapping: dict {skill_name: Skill对象}
                  用于按需获取 Skill 指令
                - skills_list: Skill 对象列表
        """
        skill_mapping = {}
        skills_list = []

        skill_dirs = self._discover_skills()
        logger.info(f"发现 {len(skill_dirs)} 个 Agent Skills")

        for skill_dir in skill_dirs:
            try:
                skill = Skill(skill_dir)

                # 保存 Skill 对象映射
                skill_mapping[skill.name] = skill
                skills_list.append(skill)
                self.skills.append(skill)

                logger.info(f"Skill 加载成功: {skill.name}")

            except Exception as e:
                logger.error(f"加载 Skill {skill_dir} 失败: {e}")

        return skill_mapping, skills_list

    def load_plugins(self) -> tuple[dict, list, dict]:
        """
        加载所有插件（SKILL.md 和 Python 函数）

        Returns:
            tuple: (py_tool_mapping, py_tools, skill_mapping)
                - py_tool_mapping: Python 函数名 -> 函数对象
                - py_tools: types.Tool 列表（Python 函数工具）
                - skill_mapping: dict {skill_name: Skill对象}
                  用于按需获取 Skill 指令
        """
        # 加载 Python 函数插件
        py_tool_mapping, py_tools = self._load_py_plugins()

        # 加载 Agent Skills
        skill_mapping, _ = self._load_skills()

        logger.info(f"插件加载完成: {len(self.py_plugins)} 个 Python 插件, {len(self.skills)} 个 Skills")
        logger.info(f"Python 工具映射表: {list(py_tool_mapping.keys())}")
        logger.info(f"Skill 列表: {list(skill_mapping.keys())}")

        return py_tool_mapping, py_tools, skill_mapping


def load_plugins(plugins_dir: str = None) -> tuple[dict, list, dict]:
    """
    便捷函数：加载所有插件

    Args:
        plugins_dir: 插件目录路径

    Returns:
        tuple: (py_tool_mapping, py_tools, skill_mapping)
    """
    loader = PluginLoader(plugins_dir)
    return loader.load_plugins()
