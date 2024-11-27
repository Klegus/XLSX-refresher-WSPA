from colorama import init, Fore, Style
init(autoreset=True)  
import requests
from datetime import datetime
from pymongo import MongoClient

class LessonPlanComparator:
    def __init__(self, mongo_uri, openrouter_api_key, selected_model):
        self.client = MongoClient(mongo_uri)
        self.db = self.client['Lesson']
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.selected_model = selected_model

    def get_last_two_plans(self, plan_config):
        collection_name = f"plans_{plan_config['faculty'].replace(' ', '-')}_{plan_config['name'].lower().replace(' ', '_').replace('-', '_')}"
        
        print(f"\n{Fore.CYAN}Debugowanie get_last_two_plans:{Style.RESET_ALL}")
        print(f"- Szukam planów w kolekcji: {collection_name}")
        
        collection = self.db[collection_name]
        plans = list(collection.find().sort("timestamp", -1).limit(2))
        
        print(f"- Znaleziono planów: {len(plans)}")
        if plans:
            print("- Daty znalezionych planów:")
            for i, plan in enumerate(plans):
                print(f"  {i+1}. {plan.get('timestamp', 'brak daty')}")
        
        if len(plans) < 2:
            print(f"{Fore.YELLOW}Nie znaleziono wystarczającej liczby planów do porównania w kolekcji {collection_name}.{Style.RESET_ALL}")
            print(f"- Wymagane są minimum 2 plany, znaleziono: {len(plans)}")
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
            print(f"{Fore.RED}Błąd API dla grupy {group}: {e}{Style.RESET_ALL}")
            return f"Nie udało się porównać planów dla grupy {group} z powodu błędu API."
        except (KeyError, IndexError) as e:
            print(f"Błąd w przetwarzaniu odpowiedzi API dla grupy {group}: {e}")
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
        
        collection_name = f"comparisons_{newer_plan['plan_name'].lower().replace(' ', '_').replace('-', '_')}"
        collection = self.db[collection_name]
        result = collection.insert_one(comparison_document)
        print(f"Wyniki porównania dla {newer_plan['plan_name']} zapisane w bazie danych z ID: {result.inserted_id}")
        return result.inserted_id

    def compare_plans(self, collection_name):
        newer_plan, older_plan = self.get_last_two_plans(collection_name)
        if not newer_plan:
            return f"Nie znaleziono żadnych planów w kolekcji {collection_name}."
        
        if not older_plan:
            return f"Plan w kolekcji {collection_name} został zaktualizowany."

        print(f"Używany model: {self.selected_model}")
        print(f"Porównywanie planów z dat: Nowszy {newer_plan['timestamp']}, Starszy {older_plan['timestamp']}")

        all_groups = set(newer_plan['groups'].keys()) | set(older_plan['groups'].keys())

        comparison_results = {}
        for group in all_groups:
            print(f"Porównywanie planów dla grupy {group}...")
            comparison_results[group] = self.compare_plans_for_group(newer_plan, older_plan, group)

        comparison_id = self.save_comparison_results(newer_plan, older_plan, comparison_results)

        # Filtrowanie i formatowanie wyników
        filtered_output = f"Porównanie planów:\nNowszy z {newer_plan['timestamp']}\nStarszy z {older_plan['timestamp']}\nUżywany model: {self.selected_model}\nID porównania w bazie: {comparison_id}\n\n"
        changes_found = False

        for group, result in comparison_results.items():
            if result.strip() != "Brak różnic":
                changes_found = True
                filtered_output += f"Grupa: {group}\n"
                filtered_output += f"{result}\n\n"
                print(f"\nWynik porównania dla grupy {group}:")
                print(result)

        if not changes_found:
            filtered_output += "Brak różnic dla wszystkich grup.\n"

        # Zapisywanie do pliku
        filename = f"plan_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(filtered_output)

        print(f"\nWyniki porównania zapisano w pliku: {filename}")
        print(f"Wyniki porównania zapisano również w bazie danych z ID: {comparison_id}")

        # Zwracanie przefiltrowanych wyników
        return filtered_output
