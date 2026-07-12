"""DingTalk IM platform — registers itself with the platform registry."""

from cubeplex.im.dingtalk._platform import DingtalkPlatform
from cubeplex.im.registry import register_platform

register_platform("dingtalk", DingtalkPlatform())
