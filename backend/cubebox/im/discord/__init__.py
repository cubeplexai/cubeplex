"""Discord IM platform — registers itself with the platform registry."""

from cubebox.im.discord._platform import DiscordPlatform
from cubebox.im.registry import register_platform

register_platform("discord", DiscordPlatform())
