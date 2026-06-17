"""Slack IM platform — registers itself with the platform registry."""

from cubebox.im.registry import register_platform
from cubebox.im.slack._platform import SlackPlatform

register_platform("slack", SlackPlatform())
