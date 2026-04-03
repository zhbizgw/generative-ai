"""
计算器插件示例（异步函数）

展示如何定义一个异步 Function Calling 工具。
"""

import asyncio
from google.genai import types


async def calculate(expression: str) -> str:
    """
    执行数学计算

    Args:
        expression: 数学表达式，如 "2+2", "sin(pi/2)", "sqrt(16)"

    Returns:
        计算结果字符串
    """
    try:
        # 安全的数学计算（不使用 eval）
        allowed_names = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
            "pow": pow,
        }

        # 使用 ast 安全地解析和计算数学表达式
        import ast
        import operator

        binary_ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
        }

        unary_ops = {
            ast.UAdd: operator.pos,
            ast.USub: operator.neg,
        }

        def eval_expr(node):
            if isinstance(node, ast.Constant):
                return node.value
            elif isinstance(node, ast.BinOp):
                left = eval_expr(node.left)
                right = eval_expr(node.right)
                return binary_ops[type(node.op)](left, right)
            elif isinstance(node, ast.UnaryOp):
                return unary_ops[type(node.op)](eval_expr(node.operand))
            elif isinstance(node, ast.Name):
                if node.id in allowed_names:
                    return allowed_names[node.id]
                raise ValueError(f"不支持的函数: {node.id}")
            else:
                raise ValueError(f"不支持的表达式类型: {type(node).__name__}")

        # 模拟异步操作（如调用外部计算服务）
        await asyncio.sleep(0.1)

        tree = ast.parse(expression, mode="eval")
        result = eval_expr(tree.body)

        return f"计算结果: {expression} = {result}"

    except Exception as e:
        return f"计算错误: {str(e)}"


def register() -> tuple:
    """
    注册计算器插件

    Returns:
        (tool_mapping, tools)
    """
    tool_mapping = {
        "calculate": calculate,
    }

    tools = [
        types.Tool(
            function_declarations=[
                {
                    "name": "calculate",
                    "description": "执行数学计算，支持加减乘除和幂运算",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "数学表达式，如 '2+2'、'10*5'、'2**3'（2的3次方）",
                            },
                        },
                        "required": ["expression"],
                    },
                },
            ]
        ),
    ]

    return tool_mapping, tools
