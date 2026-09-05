"""WeCom IM platform registration."""

from cubeplex.im.registry import register_platform
from cubeplex.im.wecom._platform import WecomPlatform

register_platform("wecom", WecomPlatform())
