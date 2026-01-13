from discord.ext import commands


class CogManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _normalize_ext(self, name: str) -> str:
        name = name.strip()
        if name.startswith("cogs."):
            return name
        if name.endswith(".py"):
            name = name[:-3]
        return f"cogs.{name}"

    @commands.command()
    @commands.is_owner()
    async def load(self, ctx: commands.Context, extension: str):
        ext = self._normalize_ext(extension)
        try:
            await self.bot.load_extension(ext)
        except Exception as exc:
            await ctx.send(f"Load failed: {ext} ({exc})")
            return
        await ctx.send(f"Loaded: {ext}")

    @commands.command()
    @commands.is_owner()
    async def unload(self, ctx: commands.Context, extension: str):
        ext = self._normalize_ext(extension)
        try:
            await self.bot.unload_extension(ext)
        except Exception as exc:
            await ctx.send(f"Unload failed: {ext} ({exc})")
            return
        await ctx.send(f"Unloaded: {ext}")

    @commands.command()
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, extension: str):
        ext = self._normalize_ext(extension)
        try:
            await self.bot.reload_extension(ext)
        except Exception as exc:
            await ctx.send(f"Reload failed: {ext} ({exc})")
            return
        await ctx.send(f"Reloaded: {ext}")


async def setup(bot: commands.Bot):
    await bot.add_cog(CogManager(bot))
