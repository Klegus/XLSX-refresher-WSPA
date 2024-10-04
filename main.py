import time
from datetime import datetime, timedelta
from LessonPlan import LessonPlan
from comparer import LessonPlanComparator
import os, shutil, requests, json
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId
import traceback
from flask import Flask, jsonify, request, Response
import threading
import pytz
import pandas as pd
from bs4 import BeautifulSoup

app = Flask(__name__)

# Load environment variables
load_dotenv()
USE_TEST_TIME = False
TEST_TIME = None
mongo_uri = os.getenv("MONGO_URI")
client = MongoClient(mongo_uri)
db = client.Lesson

class StatusChecker:
    def __init__(self):
        self.last_activity = time.time()

    def update_activity(self):
        self.last_activity = time.time()

    def is_active(self):
        return time.time() - self.last_activity < 600  # 10 minutes

    def get_last_activity_datetime(self):
        return datetime.fromtimestamp(self.last_activity).isoformat()

status_checker = StatusChecker()

@app.route('/status')
def status():
    is_active = status_checker.is_active()
    last_check = status_checker.get_last_activity_datetime()
    if is_active:
        return jsonify({
            "status": "active",
            "last_check": last_check
        }), 200
    else:
        return jsonify({
            "status": "inactive",
            "last_check": last_check
        }), 503
    


def run_flask_app():
    app.run(host='0.0.0.0', port=80)

class LessonPlanManager:
    def __init__(self, lesson_plan, lesson_plan_comparator, check_interval=600, working_directory=".", discord_webhook_url=None):
        self.lesson_plan = lesson_plan
        self.lesson_plan_comparator = lesson_plan_comparator
        self.check_interval = check_interval
        self.working_directory = working_directory
        self.initial_file_structure = set()
        self.discord_webhook_url = discord_webhook_url
        self.status_checker = status_checker
        self.cached_plans = {}

    def get_file_structure(self):
        file_structure = set()
        for root, dirs, files in os.walk(self.working_directory):
            if '__pycache__' in dirs:
                dirs.remove('__pycache__')
            for file in files:
                file_structure.add(os.path.join(root, file))
        return file_structure

    def clean_new_files(self):
        current_structure = self.get_file_structure()
        new_files = current_structure - self.initial_file_structure
        for file in new_files:
            if not file.endswith('.py') and not file.endswith('.env'):
                try:
                    os.remove(file)
                    print(f"Usunięto plik: {file}")
                except Exception as e:
                    print(f"Błąd podczas usuwania pliku {file}: {str(e)}")

    def send_discord_webhook(self, message):
        if self.discord_webhook_url:
            payload = {
                "embeds": [{
                    "title": "Aktualizacja Planu Lekcji",
                    "description": message,
                    "color": 15158332,
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            try:
                response = requests.post(self.discord_webhook_url, data=json.dumps(payload), headers={"Content-Type": "application/json"})
                response.raise_for_status()
                print("Webhook Discord wysłany pomyślnie")
            except requests.exceptions.RequestException as e:
                print(f"Błąd podczas wysyłania webhooka Discord: {str(e)}")

    def run(self):
        while True:
            print(f"\n--- Rozpoczęcie nowego sprawdzenia o {datetime.now()} ---")
            self.status_checker.update_activity()
            new_checksum = self.lesson_plan.process_and_save_plan()

            if new_checksum:
                print("Plan lekcji się zmienił. Porównywanie planów...")
                comparison_result = self.lesson_plan_comparator.compare_plans()
                
                webhook_message = f"Plan lekcji został zaktualizowany. Zmiany:\n\n{comparison_result}"
                self.send_discord_webhook(webhook_message)

                self.update_cached_plans()
            else:
                print("Brak zmian w planie lekcji.")

            self.clean_new_files()

            print(f"Oczekiwanie {self.check_interval} sekund przed następnym sprawdzeniem...")
            time.sleep(self.check_interval)

    def update_cached_plans(self):
        latest_plan = get_latest_lesson_plan()
        if latest_plan:
            for group, html_content in latest_plan['groups'].items():
                self.cached_plans[group] = parse_html_to_dataframe(html_content)
        print("Zaktualizowano pamięć podręczną planów lekcji.")



    def start(self):
        print("Uruchamianie LessonPlanManager...")
        self.update_cached_plans()
        flask_thread = threading.Thread(target=run_flask_app)
        flask_thread.start()
        try:
            self.run()
        except KeyboardInterrupt:
            print("\nLessonPlanManager zatrzymany przez użytkownika.")
        except Exception as e:
            print(f"Wystąpił błąd: {str(e)}")
        finally:
            print("LessonPlanManager zatrzymany.")

lesson_plan_manager = None
lesson_plan = None

def get_group_key(group_number):
    groups = lesson_plan.get_groups()
    group_keys = list(groups.keys())
    if 0 <= group_number < len(group_keys):
        return group_keys[group_number]
    return None

def get_latest_lesson_plan():
    try:
        return db.plans.find_one(sort=[("timestamp", -1)])
    except Exception as e:
        print(f"Error fetching the latest lesson plan: {str(e)}")
        return None
    
def parse_custom_time(time_str):
    """
    Parse time strings in the format '815' or '1005' to datetime.time objects.
    """
    if len(time_str) == 3:
        hours = int(time_str[0])
        minutes = int(time_str[1:])
    elif len(time_str) == 4:
        hours = int(time_str[:2])
        minutes = int(time_str[2:])
    else:
        raise ValueError(f"Invalid time format: {time_str}")
    
    return f"{hours:02d}:{minutes:02d}"
def format_subject(subject):
    if not subject:
        return "Brak informacji o przedmiocie"
    # Zwracamy pełną informację o przedmiocie
    return subject.replace('\n', ' ')
def format_time_to_next_lesson(minutes):
    hours, mins = divmod(minutes, 60)
    if hours > 0:
        return f"{hours} godz. {mins} min"
    else:
        return f"{mins} min"

def parse_html_to_dataframe(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    table = soup.find('table')
    if not table:
        return pd.DataFrame()

    headers = [th.text for th in table.find_all('th')]
    data = []
    for row in table.find_all('tr')[1:]:
        data.append([td.text for td in row.find_all('td')])

    return pd.DataFrame(data, columns=headers)

@app.route('/api/whatnow/<int:group_number>')
def whatnow(group_number):
    global lesson_plan_manager, USE_TEST_TIME, TEST_TIME
    
    poland_tz = pytz.timezone('Europe/Warsaw')
    
    if USE_TEST_TIME and TEST_TIME:
        if isinstance(TEST_TIME, str):
            now = datetime.strptime(TEST_TIME, '%Y-%m-%d %H:%M:%S').replace(tzinfo=poland_tz)
        else:
            now = TEST_TIME.replace(tzinfo=poland_tz)
    else:
        now = datetime.now(poland_tz)
    
    group_key = get_group_key(group_number)
    if group_key is None:
        return jsonify({"message": "Nieprawidłowy numer grupy"}), 400
    
    df_group = lesson_plan_manager.cached_plans.get(group_key)
    if df_group is None or df_group.empty:
        return jsonify({"message": "Brak planu lekcji dla tej grupy"}), 404
    
    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
    current_day = now.weekday()
    
    current_lesson = None
    next_lesson = None
    days_ahead = 0
    
    # Sprawdź lekcje na najbliższe 7 dni
    for day_offset in range(7):
        check_day = (current_day + day_offset) % 7
        check_date = now.date() + timedelta(days=day_offset)
        day_name = day_names[check_day]
        
        for _, row in df_group.iterrows():
            if day_name in row and row[day_name].strip():  # Sprawdź, czy jest lekcja w danym dniu
                time_range = row['Godziny'].split('-')
                start_time = datetime.strptime(parse_custom_time(time_range[0].strip()), '%H:%M').replace(year=check_date.year, month=check_date.month, day=check_date.day, tzinfo=poland_tz)
                end_time = datetime.strptime(parse_custom_time(time_range[1].strip()), '%H:%M').replace(year=check_date.year, month=check_date.month, day=check_date.day, tzinfo=poland_tz)
                
                if day_offset == 0 and start_time <= now < end_time:
                    current_lesson = {
                        'subject': format_subject(row[day_name]),
                        'start': start_time.strftime('%H:%M'),
                        'end': end_time.strftime('%H:%M'),
                        'time_left': int((end_time - now).total_seconds() // 60)
                    }
                elif now < start_time and not next_lesson:
                    next_lesson = {
                        'subject': format_subject(row[day_name]),
                        'start': start_time.strftime('%H:%M'),
                        'end': end_time.strftime('%H:%M'),
                        'time_to_start': int((start_time - now).total_seconds() // 60),
                        'day': day_name
                    }
                    days_ahead = day_offset
                    break
        
        if next_lesson:
            break
    
    message = f"Grupa: {group_key}\n\n"
    
    if current_lesson:
        message += f"Aktualna lekcja:\n"
        message += f"{current_lesson['subject']}\n"
        message += f"Koniec: {current_lesson['end']}\n"
        message += f"Pozostało: {format_time_to_next_lesson(current_lesson['time_left'])}\n"
        
        if next_lesson:
            message += f"\nNastępna lekcja"
            if days_ahead > 0:
                message += f" ({next_lesson['day']})"
            message += f":\n"
            message += f"{next_lesson['subject']}\n"
            message += f"Start: {next_lesson['start']}\n"
            message += f"Za: {format_time_to_next_lesson(next_lesson['time_to_start'])}\n"
    elif next_lesson:
        message += f"Następna lekcja"
        if days_ahead > 0:
            message += f" ({next_lesson['day']})"
        message += f":\n"
        message += f"{next_lesson['subject']}\n"
        message += f"Start: {next_lesson['start']}\n"
        message += f"Za: {format_time_to_next_lesson(next_lesson['time_to_start'])}\n"
    else:
        message += "Brak zaplanowanych lekcji w ciągu najbliższych 7 dni.\n"
    
    # Usuń ewentualne podwójne nowe linie i końcowe białe znaki
    message = '\n'.join(line for line in message.split('\n') if line.strip())
    
    # Użyj json.dumps() z odpowiednimi parametrami
    json_response = json.dumps({"message": message}, ensure_ascii=False, indent=2)
    
    # Zwróć odpowiedź jako application/json z prawidłowym kodowaniem
    return Response(json_response, content_type="application/json; charset=utf-8")

@app.route('/api/set_test_time', methods=['POST'])
def set_test_time():
    global USE_TEST_TIME, TEST_TIME
    data = request.json
    if 'use_test_time' in data:
        USE_TEST_TIME = data['use_test_time']
    if 'test_time' in data:
        try:
            TEST_TIME = datetime.strptime(data['test_time'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return jsonify({"error": "Invalid time format. Use YYYY-MM-DD HH:MM:SS"}), 400
    return jsonify({"message": "Test time settings updated", "use_test_time": USE_TEST_TIME, "test_time": TEST_TIME.strftime('%Y-%m-%d %H:%M:%S') if TEST_TIME else None})
def main():
    print("Starting main.py")
    
    try:
        print("Loading .env file")
        load_dotenv()
        print(".env file loaded successfully")

        global lesson_plan, lesson_plan_manager
        username = os.getenv("EMAIL")
        password = os.getenv("PASSWORD")
        mongo_uri = os.getenv("MONGO_URI")
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        selected_model = os.getenv("SELECTED_MODEL")
        discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        print("Initializing LessonPlan")
        lesson_plan = LessonPlan(username=username, password=password, mongo_uri=mongo_uri)
        print("LessonPlan initialized successfully")

        print("Initializing LessonPlanComparator")
        lesson_plan_comparator = LessonPlanComparator(
            mongo_uri=mongo_uri,
            openrouter_api_key=openrouter_api_key,
            selected_model=selected_model
        )
        print("LessonPlanComparator initialized successfully")

        print("Initializing LessonPlanManager")
        lesson_plan_manager = LessonPlanManager(lesson_plan, lesson_plan_comparator, working_directory=".", discord_webhook_url=discord_webhook_url)
        print("LessonPlanManager initialized successfully")

        print("Starting LessonPlanManager")
        lesson_plan_manager.start()
    except Exception as e:
        print(f"An error occurred in main.py: {str(e)}")
        print("Traceback:")
        print(traceback.format_exc())

if __name__ == "__main__":
    main()