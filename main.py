"""IRC适配器插件主文件"""
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig


class IRCAdapterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        # 导入适配器模块，装饰器会自动注册
        from .irc_adapter import IRCPlatformAdapter  # noqa: F401
