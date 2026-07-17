"""Microsoft Teams IM connector."""

from cubeplex.im.registry import register_platform
from cubeplex.im.teams._platform import TeamsPlatform

register_platform("teams", TeamsPlatform())
