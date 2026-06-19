"""DingTalk IM platform — registers itself with the platform registry."""

from cubebox.im.dingtalk._platform import DingtalkPlatform
from cubebox.im.registry import register_platform

register_platform("dingtalk", DingtalkPlatform())
