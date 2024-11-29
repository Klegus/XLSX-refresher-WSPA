import discord
from discord.ext import commands
import os
from datetime import datetime
import pytz
import re
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
        self.add_command(commands.Command(self.setup, name='setup'))

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
    def format_category_name(self, collection_name: str) -> str:
        """Formatuje nazwę kolekcji na nazwę kategorii Discord."""
        # Usuń prefix 'plans_'
        name = collection_name[6:]
        
        # Znajdź pierwszy underscore po nazwie wydziału
        faculty_end = name.find('_')
        if faculty_end != -1:
            # Weź tylko część po nazwie wydziału
            name = name[faculty_end + 1:]
        
        # Wyciągnij tryb studiów (st/nst/nst_puw)
        mode = ""
        if "_nst_puw" in name:
            mode = "-nst-puw"
            name = name.replace("_nst_puw", "")
        elif "_nst" in name:
            mode = "-nst"
            name = name.replace("_nst", "")
        elif "_st" in name:
            mode = "-st"
            name = name.replace("_st", "")
            
        # Usuń zbędne części
        name = re.sub(r'_+', '_', name)  # Zamień multiple underscores na pojedynczy
        name = re.sub(r'_?(semestr|sem).*$', '', name, flags=re.IGNORECASE)  # Usuń wszystko po "semestr"
        name = name.strip('_')  # Usuń początkowe i końcowe underscores
        
        # Dodaj tryb studiów na końcu
        result = f"{name}{mode}".lower()
        
        # Zamień underscores na myślniki i ogranicz długość do 50 znaków (limit Discord)
        result = result.replace('_', '-')[:50]
        
        return result

    @commands.has_permissions(administrator=True)
    async def setup(self, ctx):
        """Tworzy kategorie, kanały i role dla każdego planu zajęć"""
        if str(ctx.guild.id) != os.getenv('DISCORD_SERVER_ID'):
            return
            
        try:
            collections = self.get_collections()
            if not collections:
                await ctx.send("Nie znaleziono żadnych planów zajęć.")
                return
                
            for collection_name, data in collections.items():
                category_name = self.format_category_name(collection_name)
                role_name = f"plan-{category_name}"
                
                # Stwórz rolę jeśli nie istnieje
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if not role:
                    role = await ctx.guild.create_role(
                        name=role_name,
                        reason="Automatycznie utworzona rola dla planu zajęć"
                    )
                
                # Stwórz kategorię jeśli nie istnieje
                category = discord.utils.get(ctx.guild.categories, name=category_name)
                if not category:
                    overwrites = {
                        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    }
                    category = await ctx.guild.create_category(
                        name=category_name,
                        overwrites=overwrites
                    )
                
                # Stwórz kanały jeśli nie istnieją
                notifications_channel = discord.utils.get(category.channels, name="powiadomienia-plan")
                if not notifications_channel:
                    notifications_overwrites = {
                        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                    }
                    notifications_channel = await category.create_text_channel(
                        "powiadomienia-plan",
                        overwrites=notifications_overwrites
                    )
                
                chat_channel = discord.utils.get(category.channels, name="czat")
                if not chat_channel:
                    chat_overwrites = {
                        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                    }
                    chat_channel = await category.create_text_channel(
                        "czat",
                        overwrites=chat_overwrites
                    )
                
                # Zapisz ID kanałów i kategorii w bazie danych
                discord_data = {
                    "category_id": str(category.id),
                    "notifications_channel_id": str(notifications_channel.id),
                    "chat_channel_id": str(chat_channel.id),
                    "role_id": str(role.id)
                }
                
                # Zaktualizuj konfigurację planu w bazie danych
                self.get_collections()  # Odśwież kolekcje
                db = self.get_collections().__class__.__module__  # Pobierz obiekt bazy danych
                db.plans_config.update_one(
                    {"_id": "plans_json", f"plans.{collection_name}": {"$exists": True}},
                    {"$set": {f"plans.{collection_name}.discord": discord_data}},
                    upsert=True
                )
            
            await ctx.send("✅ Pomyślnie utworzono wszystkie kategorie, kanały i role!")
            
        except discord.Forbidden:
            await ctx.send("❌ Bot nie ma wystarczających uprawnień do wykonania tej operacji!")
        except Exception as e:
            await ctx.send(f"❌ Wystąpił błąd: {str(e)}")
