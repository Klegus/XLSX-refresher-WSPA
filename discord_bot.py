import discord
from discord.ext import commands
import os
from datetime import datetime
import pytz
import re
from shared_utils import get_semester_collections, get_system_config
import custom_print

class LessonBot(commands.Bot):
    def __init__(self, get_collections_func, get_config_func):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True  # Dodajemy uprawnienie do zarządzania serwerem
        super().__init__(command_prefix='!', intents=intents)
        
        self.get_collections = get_collections_func
        self.get_config = get_config_func
        self.db = MongoClient(os.getenv('MONGO_URI'))[os.getenv('MONGO_DB')]
        
        # Commands will be registered via decorators

    async def setup_hook(self):
        print(f"{datetime.now()}: Bot is setting up...")

    async def on_ready(self):
        print(f"{datetime.now()}: Bot is ready as {self.user}")
        
    @commands.command()
    async def plan(self, ctx: commands.Context):
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

    @commands.command()
    async def status(self, ctx: commands.Context):
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
    async def send_plan_notification(self, collection_name: str, message: str):
        """Wysyła powiadomienie o zmianie planu na odpowiedni kanał Discord."""
        try:
            # Pobierz konfigurację planów
            config = self.get_config()
            plans_config = config.get('plans_config', {}).get('plans', {})
            
            # Pobierz dane Discord dla danej kolekcji
            discord_data = plans_config.get(collection_name, {}).get('discord', {})
            
            if not discord_data or 'notifications_channel_id' not in discord_data:
                print(f"Brak skonfigurowanego kanału dla kolekcji {collection_name}")
                return
            
            # Pobierz kanał Discord
            channel_id = int(discord_data['notifications_channel_id'])
            channel = self.get_channel(channel_id)
            
            if channel:
                await channel.send(message)
                print(f"Wysłano powiadomienie na kanał {channel.name}")
            else:
                print(f"Nie znaleziono kanału o ID {channel_id}")
                
        except Exception as e:
            print(f"Błąd podczas wysyłania powiadomienia Discord: {str(e)}")

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
    async def setup(self, ctx: commands.Context):
        """Tworzy lub weryfikuje kategorie, kanały i role dla każdego planu zajęć"""
        if str(ctx.guild.id) != os.getenv('DISCORD_SERVER_ID'):
            return
            
        try:
            collections = self.get_collections()
            if not collections:
                await ctx.send("Nie znaleziono żadnych planów zajęć.")
                return

            status_message = await ctx.send("🔄 Rozpoczynam weryfikację struktury Discord...")
            created_count = 0
            verified_count = 0
            error_count = 0
            
            for collection_name, data in collections.items():
                try:
                    # Sprawdź czy istnieją dane w bazie
                    config = self.get_config()
                    plans_config = config.get('plans_config', {}).get('plans', {})
                    discord_data = plans_config.get(collection_name, {}).get('discord', {})
                    
                    category_name = self.format_category_name(collection_name)
                    role_name = f"plan-{category_name}"
                    
                    # Weryfikacja/tworzenie roli
                    role = None
                    if discord_data.get('role_id'):
                        role = ctx.guild.get_role(int(discord_data['role_id']))
                    if not role:
                        role = discord.utils.get(ctx.guild.roles, name=role_name)
                        if not role:
                            role = await ctx.guild.create_role(
                                name=role_name,
                                reason="Automatycznie utworzona rola dla planu zajęć"
                            )
                            created_count += 1
                        else:
                            verified_count += 1
                    else:
                        verified_count += 1

                    # Weryfikacja/tworzenie kategorii
                    category = None
                    if discord_data.get('category_id'):
                        category = ctx.guild.get_channel(int(discord_data['category_id']))
                    if not category:
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
                            created_count += 1
                        else:
                            verified_count += 1
                    else:
                        verified_count += 1

                    # Weryfikacja/tworzenie kanałów
                    notifications_channel = None
                    chat_channel = None
                    
                    if discord_data.get('notifications_channel_id'):
                        notifications_channel = ctx.guild.get_channel(int(discord_data['notifications_channel_id']))
                    if not notifications_channel:
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
                            created_count += 1
                        else:
                            verified_count += 1
                    else:
                        verified_count += 1

                    if discord_data.get('chat_channel_id'):
                        chat_channel = ctx.guild.get_channel(int(discord_data['chat_channel_id']))
                    if not chat_channel:
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
                            created_count += 1
                        else:
                            verified_count += 1
                    else:
                        verified_count += 1

                    # Aktualizacja danych w bazie
                    discord_data = {
                        "category_id": str(category.id),
                        "notifications_channel_id": str(notifications_channel.id),
                        "chat_channel_id": str(chat_channel.id),
                        "role_id": str(role.id)
                    }
                    
                    self.db.plans_config.update_one(
                        {"_id": "plans_json", f"plans.{collection_name}": {"$exists": True}},
                        {"$set": {f"plans.{collection_name}.discord": discord_data}},
                        upsert=True
                    )

                except Exception as e:
                    error_count += 1
                    print(f"Błąd podczas przetwarzania {collection_name}: {str(e)}")
                    continue

            await status_message.edit(content=(
                f"✅ Zakończono weryfikację struktury Discord!\n"
                f"📊 Statystyki:\n"
                f"- Utworzono nowych elementów: {created_count}\n"
                f"- Zweryfikowano istniejących: {verified_count}\n"
                f"- Błędy: {error_count}"
            ))
            
        except discord.Forbidden:
            await ctx.send("❌ Bot nie ma wystarczających uprawnień do wykonania tej operacji!")
        except Exception as e:
            await ctx.send(f"❌ Wystąpił błąd: {str(e)}")
