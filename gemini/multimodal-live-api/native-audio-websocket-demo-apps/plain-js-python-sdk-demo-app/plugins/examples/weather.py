"""
天气查询插件示例（同步函数）

展示如何定义一个简单的 Function Calling 工具。
"""

from google.genai import types


def get_weather(city: str) -> str:
    """
    获取城市天气信息

    Args:
        city: 城市名称

    Returns:
        格式化的天气信息字符串
    """
    # 实际应用中这里可以调用外部天气 API
    weather_data = {
        "北京": "北京今天晴朗，温度 15-25°C，适宜户外活动",
        "上海": "上海今天多云转阴，温度 18-27°C，有轻度污染",
        "深圳": "深圳今天雷阵雨，温度 25-32°C，请带伞出门",
    }

    return weather_data.get(city, f"{city}的天气信息暂不可用")


def register() -> tuple:
    """
    注册天气插件

    Returns:
        (tool_mapping, tools)
    """
    tool_mapping = {
        "get_weather": get_weather,
    }

    tools = [
        types.Tool(
            function_declarations=[
                {
                    "name": "get_weather",
                    "description": "获取指定城市的天气信息，帮助用户了解当前天气状况",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "城市名称，如：北京、上海、深圳",
                            },
                        },
                        "required": ["city"],
                    },
                },
            ]
        ),
    ]

    return tool_mapping, tools
