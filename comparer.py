from colorama import init, Fore, Style
init(autoreset=True)  
import requests
from datetime import datetime
from pymongo import MongoClient
from shared_utils import get_logger

# Setup logger
logger = get_logger('comparer')

class LessonPlanComparator:
    def __init__(self, mongo_uri, openrouter_api_key, selected_model):
        self.client = MongoClient(mongo_uri)
        self.db = self.client['Lesson']
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.selected_model = selected_model

    def get_last_two_plans(self, plan_config):
        # Replace both spaces and underscores in faculty with hyphens
        faculty_name = plan_config['faculty'].replace(' ', '-').replace('_', '-')
        collection_name = f"plans_{faculty_name}_{plan_config['name'].lower().replace(' ', '_')}"
        
        logger.debug(f"\nDebugging get_last_two_plans:")
        logger.debug(f"- Szukam planów w kolekcji: {collection_name}")
        
        collection = self.db[collection_name]
        plans = list(collection.find().sort("timestamp", -1).limit(2))
        
        logger.debug(f"- Znaleziono planów: {len(plans)}")
        if plans:
            logger.debug("- Daty znalezionych planów:")
            for i, plan in enumerate(plans):
                logger.debug(f"  {i+1}. {plan.get('timestamp', 'brak daty')}")
        
        if len(plans) < 2:
            logger.warning(f"Nie znaleziono wystarczającej liczby planów do porównania w kolekcji {collection_name}.")
            logger.debug(f"- Wymagane są minimum 2 plany, znaleziono: {len(plans)}")
            if plans:  # Jeśli jest przynajmniej jeden plan
                return plans[0], None
            return None, None
            
        # Upewnij się, że plans[0] to najnowszy plan, a plans[1] to poprzedni
        return plans[0], plans[1]  # plans[0] jest najnowszy dzięki sort("timestamp", -1)

    def format_plan_for_group(self, plan, group):
        if group not in plan['groups']:
            return f"Brak danych dla grupy {group} w planie z dnia {plan['timestamp']}"
        html_content = plan['groups'][group]
        return f"Plan z dnia {plan['timestamp']} dla grupy {group}:\n{html_content}\n\n"

    def compare_plans_for_group(self, plan1, plan2, group):
        formatted_plan1 = self.format_plan_for_group(plan1, group)
        formatted_plan2 = self.format_plan_for_group(plan2, group)

        prompt = f"""Porównaj poniższe dwa plany lekcji dla grupy {group} i opisz różnice między nimi. 
        Skup się tylko na istotnych zmianach w godzinach zajęć, przedmiotach i salach.
        Jeśli nie ma żadnych różnic, napisz tylko "Brak różnic".
        Jeśli są różnice, przedstaw je krótko i konkretnie, bez zbędnych szczegółów.

        Plan 1 - nowy:
        {formatted_plan1}

        Plan 2 - stary:
        {formatted_plan2}

        Różnice (lub "Brak różnic"):
        """

        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://your-app-domain.com",  # Zastąp swoją domeną
            "X-Title": "Plan Lekcji Comparison"
        }

        data = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "Jesteś asystentem specjalizującym się w zwięzłej analizie i porównywaniu planów lekcji."},
                {"role": "user", "content": prompt}
            ]
        }

        try:
            response = requests.post(self.openrouter_api_url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content'].strip()
        except requests.exceptions.RequestException as e:
            logger.error(f"Błąd API dla grupy {group}: {e}")
            return f"Nie udało się porównać planów dla grupy {group} z powodu błędu API."
        except (KeyError, IndexError) as e:
            logger.error(f"Błąd w przetwarzaniu odpowiedzi API dla grupy {group}: {e}")
            return f"Wystąpił problem z przetwarzaniem odpowiedzi dla grupy {group}."

    def save_comparison_results(self, newer_plan, older_plan, comparison_results):
        comparison_document = {
            "timestamp": datetime.now(),
            "newer_plan_id": newer_plan['_id'],
            "newer_plan_timestamp": newer_plan['timestamp'],
            "older_plan_id": older_plan['_id'],
            "older_plan_timestamp": older_plan['timestamp'],
            "plan_name": newer_plan['plan_name'],
            "model_used": self.selected_model,
            "results": comparison_results
        }
        
        collection_name = f"comparisons_{newer_plan['plan_name'].lower().replace(' ', '_')}"
        collection = self.db[collection_name]
        result = collection.insert_one(comparison_document)
        logger.info(f"Wyniki porównania dla {newer_plan['plan_name']} zapisane w bazie danych z ID: {result.inserted_id}")
        return result.inserted_id

    def compare_plans(self, plan_config):
        """Porównuje obecny plan z poprzednim planem z bazy danych"""
        # Pobierz dwa ostatnie plany z bazy danych
        # Replace both spaces and underscores in faculty with hyphens
        faculty_name = plan_config['faculty'].replace(' ', '-').replace('_', '-')
        collection_name = f"plans_{faculty_name}_{plan_config['name'].lower().replace(' ', '_')}"
        
        try:
            logger.debug(f"- Szukam planów w kolekcji: {collection_name}")
            
            collection = self.db[collection_name]
            
            # Pobierz dwa ostatnie plany
            plans = list(collection.find({"_id": {"$ne": "discord_config"}}).sort("timestamp", -1).limit(2))
            
            if len(plans) < 2:
                logger.warning(f"Nie znaleziono wystarczającej liczby planów do porównania w kolekcji {collection_name}.")
                return None

            # Nowszy plan to pierwszy element (sortowanie malejące po timestamp)
            newer_plan = plans[0]
            older_plan = plans[1]

            logger.debug(f"- Porównuję plany z {newer_plan['timestamp']} i {older_plan['timestamp']}")

            # Znajdź różnice
            return self._find_differences(newer_plan, older_plan)
        except Exception as e:
            logger.error(f"Błąd podczas porównywania planów: {str(e)}")
            return None

    def save_comparison(self, newer_plan, older_plan, comparison):
        """Zapisuje wynik porównania do bazy danych"""
        try:
            comparison_doc = {
                "timestamp": datetime.now(),
                "newer_plan_id": str(newer_plan["_id"]),
                "older_plan_id": str(older_plan["_id"]),
                "newer_plan_timestamp": newer_plan["timestamp"],
                "older_plan_timestamp": older_plan["timestamp"],
                "comparison": comparison
            }
            
            # Zapisz w kolekcji dla tego konkretnego planu
            collection_name = f"comparisons_{newer_plan['plan_name'].lower().replace(' ', '_')}"
            collection = self.db[collection_name]
            
            result = collection.insert_one(comparison_doc)
            logger.info(f"Zapisano porównanie planów do bazy danych z ID: {result.inserted_id}")
        except Exception as e:
            logger.error(f"Błąd podczas zapisywania porównania planów: {str(e)}")

    def get_last_comparison(self, plan_config):
        """Pobiera ostatnie porównanie z bazy danych"""
        try:
            # Sprawdź czy istnieją przynajmniej dwa plany
            # Replace both spaces and underscores in faculty with hyphens
            faculty_name = plan_config['faculty'].replace(' ', '-').replace('_', '-')
            collection_name = f"plans_{faculty_name}_{plan_config['name'].lower().replace(' ', '_')}"
            return f"Nie znaleziono żadnych planów w kolekcji {collection_name}."
        except Exception as e:
            logger.error(f"Błąd podczas pobierania porównania planów: {str(e)}")
            return None
