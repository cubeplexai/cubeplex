"""Microsoft Teams IM connector."""

from cubebox.im.registry import register_platform
from cubebox.im.teams._platform import TeamsPlatform

register_platform("teams", TeamsPlatform())
