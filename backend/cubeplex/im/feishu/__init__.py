"""Feishu IM platform — registers itself with the platform registry."""

from cubeplex.im.feishu._platform import FeishuPlatform
from cubeplex.im.registry import register_platform

register_platform("feishu", FeishuPlatform())
