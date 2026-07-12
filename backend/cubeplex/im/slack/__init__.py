"""Slack IM platform — registers itself with the platform registry."""

from cubeplex.im.registry import register_platform
from cubeplex.im.slack._platform import SlackPlatform

register_platform("slack", SlackPlatform())
