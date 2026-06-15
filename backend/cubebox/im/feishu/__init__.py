"""Feishu IM platform — registers itself with the platform registry."""

from cubebox.im.feishu._platform import FeishuPlatform
from cubebox.im.registry import register_platform

register_platform("feishu", FeishuPlatform())
