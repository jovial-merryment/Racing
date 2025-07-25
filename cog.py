import discord
from discord.ext import commands
from discord import app_commands, Interaction
import asyncio
import random
import json
import os

from ballsdex.core.models import BallInstance
from ballsdex.core.utils.transformers import BallInstanceTransform

MAX_HORSES_PER_PLAYER = 10
TURN_DELAY = 7  # seconds between turns
WIN_FILE = "race_wins.json"


class Race(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_races = {}
        self.pending_challenges = {}
        self.win_counts = self.load_win_counts()

    def load_win_counts(self):
        if os.path.exists(WIN_FILE):
            try:
                with open(WIN_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_win_counts(self):
        with open(WIN_FILE, "w") as f:
            json.dump(self.win_counts, f)

    def increment_win(self, user_id: int):
        str_id = str(user_id)
        self.win_counts[str_id] = self.win_counts.get(str_id, 0) + 1
        self.save_win_counts()

    @app_commands.command(name="challenge", description="Challenge another player to a race")
    @app_commands.describe(
        opponent="The user you want to challenge",
        track_length="Length of the race track (1000 to 100000)"
    )
    async def race_challenge(self, interaction: Interaction, opponent: discord.Member, track_length: int):
        if interaction.user.id == opponent.id:
            await interaction.response.send_message("You cannot challenge yourself.", ephemeral=True)
            return

        if track_length < 1000 or track_length > 100000:
            await interaction.response.send_message("Track length must be between 1000 and 100000.", ephemeral=True)
            return

        if interaction.user.id in self.active_races or opponent.id in self.active_races:
            await interaction.response.send_message("Either you or your opponent is already in a race.", ephemeral=True)
            return

        self.pending_challenges[interaction.user.id] = {
            "opponent": opponent.id,
            "track_length": track_length
        }

        await interaction.response.send_message(
            f"{interaction.user.mention} challenged {opponent.mention} to a race of length {track_length} units! "
            f"{opponent.mention}, use /accept to accept."
        )

    @app_commands.command(name="accept", description="Accept a pending race challenge")
    async def race_accept(self, interaction: Interaction):
        user_id = interaction.user.id
        challenge = None

        for challenger_id, data in self.pending_challenges.items():
            if data["opponent"] == user_id:
                challenge = (challenger_id, data)
                break

        if not challenge:
            await interaction.response.send_message("You have no pending race challenges.", ephemeral=True)
            return

        challenger_id, data = challenge
        track_length = data["track_length"]

        self.active_races[user_id] = {
            "opponent": challenger_id,
            "picked_horses": [],
            "locked": False,
            "current_index": 0,
            "distance": 0,
            "health": 0,
            "track_length": track_length,
        }
        self.active_races[challenger_id] = {
            "opponent": user_id,
            "picked_horses": [],
            "locked": False,
            "current_index": 0,
            "distance": 0,
            "health": 0,
            "track_length": track_length,
        }

        del self.pending_challenges[challenger_id]

        await interaction.response.send_message(
            f"{interaction.user.mention} accepted the race from <@{challenger_id}>! Track length: {track_length} units.\n"
            "Both players, pick horses with /pick and lock them with /lock."
        )

    @app_commands.command(name="cancel", description="Cancel a pending race challenge")
    async def race_cancel(self, interaction: Interaction):
        user_id = interaction.user.id
        if user_id in self.pending_challenges:
            challenge = self.pending_challenges.pop(user_id)
            opponent_id = challenge['opponent']
            await interaction.response.send_message(f"Cancelled your challenge to <@{opponent_id}>.")
            return

        for challenger, opponent in list(self.pending_challenges.items()):
            if opponent == user_id:
                del self.pending_challenges[challenger]
                await interaction.response.send_message("Declined the incoming challenge.")
                return

        await interaction.response.send_message("You have no pending challenges to cancel or decline.", ephemeral=True)

    @app_commands.command(name="pick", description="Pick a horse for the race")
    @app_commands.describe(horse="Horse to pick")
    async def race_pick(self, interaction: Interaction, horse: BallInstanceTransform):
        user_id = interaction.user.id
        if user_id not in self.active_races:
            await interaction.response.send_message("You are not currently in a race.", ephemeral=True)
            return

        race = self.active_races[user_id]
        if race["locked"]:
            await interaction.response.send_message("You already locked your horses.", ephemeral=True)
            return

        if not horse:
            await interaction.response.send_message("Invalid horse ID.", ephemeral=True)
            return

        if any(h.id == horse.id for h in race["picked_horses"]):
            await interaction.response.send_message("You already picked this horse.", ephemeral=True)
            return

        if len(race["picked_horses"]) >= MAX_HORSES_PER_PLAYER:
            await interaction.response.send_message(f"You can only pick up to {MAX_HORSES_PER_PLAYER} horses.", ephemeral=True)
            return

        race["picked_horses"].append(horse)
        await interaction.response.send_message(
            f"Picked horse **{horse.countryball.country}** ({horse.id}). You have picked {len(race['picked_horses'])}/{MAX_HORSES_PER_PLAYER} horses."
        )

    @app_commands.command(name="lock", description="Lock your horses and wait for opponent")
    async def race_lock(self, interaction: Interaction):
        user_id = interaction.user.id
        if user_id not in self.active_races:
            await interaction.response.send_message("You are not currently in a race.", ephemeral=True)
            return

        race = self.active_races[user_id]
        if race["locked"]:
            await interaction.response.send_message("You already locked your horses.", ephemeral=True)
            return

        if len(race["picked_horses"]) == 0:
            await interaction.response.send_message("You must pick at least one horse before locking.", ephemeral=True)
            return

        race["locked"] = True
        opponent_id = race["opponent"]

        if opponent_id not in self.active_races:
            await interaction.response.send_message("Opponent left the race.", ephemeral=True)
            del self.active_races[user_id]
            return

        opponent_race = self.active_races[opponent_id]

        if not opponent_race["locked"]:
            await interaction.response.send_message("Locked your horses! Waiting for opponent to lock theirs.")
            return

        # Initialize both players' first horse
        for uid in (user_id, opponent_id):
            data = self.active_races[uid]
            data["current_index"] = 0
            data["distance"] = 0
            first_horse = data["picked_horses"][0]
            data["health"] = first_horse.health

        await interaction.response.send_message("Both players locked horses! 🏁 The race is starting!")

        await self.run_race(interaction.channel, user_id, opponent_id)

    @app_commands.command(name="forfeit", description="Forfeit the current race")
    async def forfeit(self, interaction: Interaction):
        user_id = interaction.user.id
        if user_id not in self.active_races:
            await interaction.response.send_message("You are not currently in a race.", ephemeral=True)
            return

        race = self.active_races[user_id]
        opponent_id = race["opponent"]
        opponent = self.bot.get_user(opponent_id)
        opponent_mention = opponent.mention if opponent else "Your opponent"

        await interaction.response.send_message(
            f"{interaction.user.mention} has forfeited the race. {opponent_mention} wins!"
        )

        self.increment_win(opponent_id)

        self.active_races.pop(user_id, None)
        self.active_races.pop(opponent_id, None)

    async def run_race(self, channel, user1_id, user2_id):
        race1 = self.active_races.get(user1_id)
        race2 = self.active_races.get(user2_id)

        if not race1 or not race2:
            return

        user1 = self.bot.get_user(user1_id) or await self.bot.fetch_user(user1_id)
        user2 = self.bot.get_user(user2_id) or await self.bot.fetch_user(user2_id)

        while True:
            await asyncio.sleep(TURN_DELAY)

            for user_id, race, opponent_race, user in [
                (user1_id, race1, race2, user1),
                (user2_id, race2, race1, user2),
            ]:
                if race["current_index"] >= len(race["picked_horses"]):
                    continue

                horse = race["picked_horses"][race["current_index"]]
                move = random.randint(1, horse.attack)
                health_loss = random.randint(1, 1000)

                race["distance"] += move
                race["health"] -= health_loss

                await channel.send(
                    f"{user.mention}'s horse **{horse.countryball.country}** moved {move} units and lost {health_loss} health "
                    f"(Health left: {max(race['health'], 0)}). Total distance: {race['distance']}/{race['track_length']}."
                )

                if race["health"] <= 0:
                    race["current_index"] += 1
                    if race["current_index"] < len(race["picked_horses"]):
                        next_horse = race["picked_horses"][race["current_index"]]
                        race["health"] = next_horse.health
                        await channel.send(f"{user.mention}'s horse **{next_horse.countryball.country}** takes over!")
                    else:
                        await channel.send(f"{user.mention} has no horses left!")

            if race1["distance"] >= race1["track_length"]:
                await channel.send(f"{user1.mention} reached the finish line and wins the race! 🏆")
                self.increment_win(user1_id)
                break
            if race2["distance"] >= race2["track_length"]:
                await channel.send(f"{user2.mention} reached the finish line and wins the race! 🏆")
                self.increment_win(user2_id)
                break

            both_exhausted = (
                race1["current_index"] >= len(race1["picked_horses"]) and
                race2["current_index"] >= len(race2["picked_horses"])
            )
            if both_exhausted:
                if race1["distance"] > race2["distance"]:
                    winner = user1
                    self.increment_win(user1_id)
                elif race2["distance"] > race1["distance"]:
                    winner = user2
                    self.increment_win(user2_id)
                else:
                    winner = None

                if winner:
                    await channel.send(f"🏁 All horses exhausted. {winner.mention} wins by distance!")
                else:
                    await channel.send("🏁 It's a tie! Both players traveled the same distance.")
                break

            race1 = self.active_races.get(user1_id)
            race2 = self.active_races.get(user2_id)
            if not race1 or not race2:
                break

        self.active_races.pop(user1_id, None)
        self.active_races.pop(user2_id, None)

    @app_commands.command(name="leaderboard", description="View the race leaderboard")
    async def leaderboard(self, interaction: Interaction):
        if not self.win_counts:
            await interaction.response.send_message("🏁 No races have been won yet.", ephemeral=True)
            return

        sorted_leaderboard = sorted(self.win_counts.items(), key=lambda x: x[1], reverse=True)
        lines = []
        for i, (user_id, wins) in enumerate(sorted_leaderboard[:10], start=1):
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
            username = user.name if user else f"User {user_id}"
            lines.append(f"**{i}. {username}** — {wins} win{'s' if wins != 1 else ''}")

        leaderboard_text = "\n".join(lines)
        await interaction.response.send_message(f"🏆 **Top Racers** 🏆\n{leaderboard_text}")

    @app_commands.command(name="mywins", description="See how many races you've won")
    async def mywins(self, interaction: Interaction):
        user_id = str(interaction.user.id)
        count = self.win_counts.get(user_id, 0)
        await interaction.response.send_message(f"🏁 You have won **{count}** race{'s' if count != 1 else ''}.")

async def setup(bot):
    await bot.add_cog(Race(bot))
