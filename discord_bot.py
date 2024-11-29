import discord
from discord.ext import commands
import os
from datetime import datetime
import pytz
from main import get_semester_collections, get_system_config

class LessonBot(commands.Bot):
    def __init__(self, get_collections_func, get_config_func):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        
        self.get_collections = get_collections_func
        self.get_config = get_config_func
        
        # Dodaj podstawowe komendy
        self.add_command(commands.Command(self.plan, name='plan'))
        self.add_command(commands.Command(self.status, name='status'))

    async def setup_hook(self):
        print(f"{datetime.now()}: Bot is setting up...")

    async def on_ready(self):
        print(f"{datetime.now()}: Bot is ready as {self.user}")
        
    async def plan(self, ctx):
        """Pokazuje aktualny plan zajęć"""
        # Sprawdź czy komenda jest wykonywana na dozwolonym serwerze
        if str(ctx.guild.id) != os.getenv('DISCORD_SERVER_ID'):
            return
            
        try:
            collections = self.get_collections()
            if not collections:
                await ctx.send("Nie znaleziono żadnych planów zajęć.")
                return
                
            response = "Dostępne plany zajęć:\n"
            for collection_name, data in collections.items():
                response += f"\n**{data['plan_name']}**"
                response += f"\nOstatnia aktualizacja: {data['timestamp']}"
                response += "\n---"
                
            await ctx.send(response)
        except Exception as e:
            await ctx.send(f"Wystąpił błąd: {str(e)}")

    async def status(self, ctx):
        """Pokazuje status systemu"""
        if str(ctx.guild.id) != os.getenv('DISCORD_SERVER_ID'):
            return
            
        try:
            config = self.get_config()
            stats = config.get('last_check_stats', {})
            
            response = "**Status systemu:**\n"
            response += f"Ostatnie sprawdzenie: {stats.get('timestamp', 'brak')}\n"
            response += f"Sprawdzone plany: {stats.get('plans_checked', 0)}\n"
            response += f"Wykryte zmiany: {stats.get('changes_detected', 0)}\n"
            
            await ctx.send(response)
        except Exception as e:
            await ctx.send(f"Wystąpił błąd: {str(e)}")

def init_discord_bot(get_collections_func, get_config_func):
    """Inicjalizuje bota Discord"""
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("DISCORD_BOT_TOKEN nie został ustawiony w zmiennych środowiskowych")
        return None
        
    if not os.getenv('DISCORD_SERVER_ID'):
        print("DISCORD_SERVER_ID nie został ustawiony w zmiennych środowiskowych")
        return None
        
    return LessonBot(get_collections_func, get_config_func)
