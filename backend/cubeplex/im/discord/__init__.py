"""Discord IM platform — registers itself with the platform registry."""

from cubeplex.im.discord._platform import DiscordPlatform
from cubeplex.im.registry import register_platform

register_platform("discord", DiscordPlatform())
