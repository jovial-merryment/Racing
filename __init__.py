from typing import TYPE_CHECKING
from ballsdex.core.models import BallInstance
from ballsdex.packages.race.cog import Race

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    cog = Race(bot)
    await bot.add_cog(cog)
