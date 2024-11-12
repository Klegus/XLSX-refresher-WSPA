import time
from datetime import datetime, timedelta
from LessonPlan import LessonPlan
from comparer import LessonPlanComparator
from ActivityDownloader import WebpageDownloader
from MoodleParserComponent import MoodleFileParser
import os, requests, json, hashlib
from dotenv import load_dotenv
from pymongo import MongoClient
import traceback
from flask import Flask, jsonify, request, Response
import threading
import pytz
import pandas as pd
from bs4 import BeautifulSoup
import sentry_sdk
load_dotenv()
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for tracing.
    traces_sample_rate=1.0,
    # Set profiles_sample_rate to 1.0 to profile 100%
    # of sampled transactions.
    # We recommend adjusting this value in production.
    profiles_sample_rate=1.0,
)

app = Flask(__name__)

# Load environment variables

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
        return True

    def get_last_activity_datetime(self):
        return datetime.fromtimestamp(self.last_activity).isoformat()


status_checker = StatusChecker()


@app.route("/status")
def status():
    is_active = status_checker.is_active()
    last_check = status_checker.get_last_activity_datetime()
    if is_active:
        return jsonify({"status": "active", "last_check": last_check}), 200
    else:
        return jsonify({"status": "inactive", "last_check": last_check}), 503


def run_flask_app():
    app.run(host="0.0.0.0", port=80)


class LessonPlanManager:
    def __init__(
        self,
        lesson_plan,
        lesson_plan_comparator,
        check_interval=600,
        working_directory=".",
        discord_webhook_url=None,
    ):
        self.lesson_plan = lesson_plan
        self.lesson_plan_comparator = lesson_plan_comparator
        self.plan_name = lesson_plan.plan_config["name"]
        self.check_interval = check_interval
        self.working_directory = working_directory
        self.initial_file_structure = set()
        self.discord_webhook_url = discord_webhook_url
        self.status_checker = status_checker
        self.cached_plans = {}

    def get_file_structure(self):
        file_structure = set()
        for root, dirs, files in os.walk(self.working_directory):
            if "__pycache__" in dirs:
                dirs.remove("__pycache__")
            for file in files:
                file_structure.add(os.path.join(root, file))
        return file_structure

    def clean_new_files(self):
        current_structure = self.get_file_structure()
        new_files = current_structure - self.initial_file_structure
        for file in new_files:
            if (
                file.endswith(".xlsx")
                and not file.startswith(".git")
                and not file.endswith(".py")
                and not file.endswith(".env")
            ):
                try:
                    os.remove(file)
                    print(f"Usunięto plik: {file}")
                except Exception as e:
                    print(f"Błąd podczas usuwania pliku {file}: {str(e)}")

    def send_discord_webhook(self, message):
        if self.discord_webhook_url:
            payload = {
                "embeds": [
                    {
                        "title": "Aktualizacja Planu Lekcji",
                        "description": message,
                        "color": 15158332,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ]
            }
            try:
                response = requests.post(
                    self.discord_webhook_url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                print("Webhook Discord wysłany pomyślnie")
            except requests.exceptions.RequestException as e:
                print(f"Błąd podczas wysyłania webhooka Discord: {str(e)}")


    def update_cached_plans(self):
        latest_plan = get_latest_lesson_plan()
        if latest_plan:
            for group, html_content in latest_plan["groups"].items():
                self.cached_plans[group] = parse_html_to_dataframe(html_content)
        print("Zaktualizowano pamięć podręczną planów lekcji.")

    def check_once(self):
        """Wykonuje pojedynczy cykl sprawdzania planu"""
        current_time = datetime.now()
        current_hour = current_time.hour

        # Skip checks between 21:00 and 06:00
        is_night_time = current_hour >= 21 or current_hour < 6
        if is_night_time:
            print(
                f"Skipping check at {current_time.strftime('%Y-%m-%d %H:%M:%S')} - night hours (21:00-06:00)"
            )
            return

        try:
            print(
                f"\n--- Starting new check for {self.plan_name} at {datetime.now()} ---"
            )
            self.status_checker.update_activity()
            new_checksum = self.lesson_plan.process_and_save_plan()

            if new_checksum is None:
                print("Wystąpił błąd podczas sprawdzania planu.")
            else:
                if new_checksum:
                    print("Plan został zaktualizowany")
                    webhook_message = f"Plan zajęć został zaktualizowany dla: {self.plan_name}"
                    
                    # Jeśli comparator jest włączony, dodaj szczegóły zmian
                    if self.lesson_plan_comparator:
                        print("Porównywanie planów...")
                        collection_name = (
                            self.plan_name.lower().replace(" ", "_").replace("-", "_")
                        )
                        comparison_result = self.lesson_plan_comparator.compare_plans(
                            collection_name
                        )
                        if comparison_result:
                            webhook_message += f"\n\nZmiany:\n{comparison_result}"
                            print("Wykryto i zapisano zmiany w planie.")
                    
                    self.send_discord_webhook(webhook_message)
                    self.update_cached_plans()
                    print("Zaktualizowano pamięć podręczną planów.")
                else:
                    print("Nie wykryto zmian w planie.")

            self.clean_new_files()

        except Exception as e:
            print(f"\nWystąpił błąd podczas sprawdzania {self.plan_name}: {str(e)}")
            raise

    def start(self):
        """Deprecated - use check_once() instead"""
        print("Warning: start() is deprecated. Use check_once() instead.")
        self.check_once()


lesson_plan_manager = None
lesson_plan = None


def get_group_key(group_number):
    # Wczytaj konfigurację grup z plans.json
    with open("plans.json", "r", encoding="utf-8") as f:
        plans_config = json.load(f)

    # Pobierz grupy dla informatyka2
    groups = plans_config["informatyka2"]["groups"]
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
    return subject.replace("\n", " ")


def format_time_to_next_lesson(minutes):
    hours, mins = divmod(minutes, 60)
    if hours > 0:
        return f"{hours} godz. {mins} min"
    else:
        return f"{mins} min"


def parse_html_to_dataframe(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.find("table")
    if not table:
        return pd.DataFrame()

    headers = [th.text for th in table.find_all("th")]
    data = []
    for row in table.find_all("tr")[1:]:
        data.append([td.text for td in row.find_all("td")])

    return pd.DataFrame(data, columns=headers)


@app.route("/api/whatnow/<int:group_number>")
def whatnow(group_number):
    global USE_TEST_TIME, TEST_TIME

    poland_tz = pytz.timezone("Europe/Warsaw")

    if USE_TEST_TIME and TEST_TIME:
        if isinstance(TEST_TIME, str):
            now = datetime.strptime(TEST_TIME, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=poland_tz
            )
        else:
            now = TEST_TIME.replace(tzinfo=poland_tz)
    else:
        now = datetime.now(poland_tz)

    # Pobierz najnowszy plan z odpowiedniej kolekcji
    collection = db["plans_informatyka___studia_i_stopnia_st_2"]
    latest_plan = collection.find_one(sort=[("timestamp", -1)])

    if not latest_plan:
        return jsonify({"message": "Brak dostępnego planu lekcji"}), 404

    group_key = get_group_key(group_number)
    if group_key is None:
        return jsonify({"message": "Nieprawidłowy numer grupy"}), 400

    if group_key not in latest_plan["groups"]:
        return jsonify({"message": f"Brak planu dla grupy {group_key}"}), 404

    # Konwertuj HTML na DataFrame
    html_content = latest_plan["groups"][group_key]
    from io import StringIO

    df_group = pd.read_html(StringIO(html_content))[0]
    if df_group is None or df_group.empty:
        return jsonify({"message": "Brak planu lekcji dla tej grupy"}), 404

    day_names = [
        "Poniedziałek",
        "Wtorek",
        "Środa",
        "Czwartek",
        "Piątek",
        "Sobota",
        "Niedziela",
    ]
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
            if (
                day_name in row
                and pd.notna(row[day_name])
                and str(row[day_name]).strip()
            ):  # Sprawdź, czy jest lekcja w danym dniu
                time_range = row["Godziny"].split("-")
                start_time = datetime.strptime(
                    parse_custom_time(time_range[0].strip()), "%H:%M"
                ).replace(
                    year=check_date.year,
                    month=check_date.month,
                    day=check_date.day,
                    tzinfo=poland_tz,
                )
                end_time = datetime.strptime(
                    parse_custom_time(time_range[1].strip()), "%H:%M"
                ).replace(
                    year=check_date.year,
                    month=check_date.month,
                    day=check_date.day,
                    tzinfo=poland_tz,
                )

                if day_offset == 0 and start_time <= now < end_time:
                    current_lesson = {
                        "subject": format_subject(row[day_name]),
                        "start": start_time.strftime("%H:%M"),
                        "end": end_time.strftime("%H:%M"),
                        "time_left": int((end_time - now).total_seconds() // 60),
                    }
                elif now < start_time and not next_lesson:
                    next_lesson = {
                        "subject": format_subject(row[day_name]),
                        "start": start_time.strftime("%H:%M"),
                        "end": end_time.strftime("%H:%M"),
                        "time_to_start": int((start_time - now).total_seconds() // 60),
                        "day": day_name,
                    }
                    days_ahead = day_offset
                    break

        if next_lesson:
            break

    message = f"Grupa: {group_key}\n\n"

    if current_lesson:
        message += "Aktualna lekcja:\n"
        message += f"{current_lesson['subject']}\n"
        message += f"Koniec: {current_lesson['end']}\n"
        if next_lesson:
            message += "\nNastępna lekcja"
            if days_ahead > 0:
                message += f" ({next_lesson['day']})"
            message += ":\n"
            message += f"{next_lesson['subject']}\n"
            message += f"Start: {next_lesson['start']}\n"
    elif next_lesson:
        message += "Następna lekcja"
        if days_ahead > 0:
            message += f" ({next_lesson['day']})"
        message += ":\n"
        message += f"{next_lesson['subject']}\n"
        message += f"Start: {next_lesson['start']}\n"
    else:
        message += "Brak zaplanowanych lekcji w ciągu najbliższych 7 dni.\n"

    # Usuń ewentualne podwójne nowe linie i końcowe białe znaki
    message = "\n".join(line for line in message.split("\n") if line.strip())
    json_response = json.dumps({"message": message}, ensure_ascii=False, indent=2)
    return Response(json_response, content_type="application/json; charset=utf-8")


@app.route("/api/set_test_time", methods=["POST"])
def set_test_time():
    global USE_TEST_TIME, TEST_TIME
    data = request.json
    if "use_test_time" in data:
        USE_TEST_TIME = data["use_test_time"]
    if "test_time" in data:
        try:
            TEST_TIME = datetime.strptime(data["test_time"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return (
                jsonify({"error": "Invalid time format. Use YYYY-MM-DD HH:MM:SS"}),
                400,
            )
    return jsonify(
        {
            "message": "Test time settings updated",
            "use_test_time": USE_TEST_TIME,
            "test_time": TEST_TIME.strftime("%Y-%m-%d %H:%M:%S") if TEST_TIME else None,
        }
    )


def main():
    print("Starting main.py")
    check_interval = 600

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
        # Load plans configuration
        with open("plans.json", "r", encoding="utf-8") as f:
            plans_config = json.load(f)

        lesson_plans = {}
        lesson_plan_comparators = {}
        lesson_plan_managers = {}

        for plan_id, plan_config in plans_config.items():
            print(f"Initializing LessonPlan for {plan_config['name']}")
            lesson_plans[plan_id] = LessonPlan(
                username=username,
                password=password,
                mongo_uri=mongo_uri,
                plan_config=plan_config,
            )
            print(f"LessonPlan for {plan_config['name']} initialized successfully")

            enable_comparer = os.getenv("ENABLE_COMPARER", "true").lower() == "true"
            if enable_comparer:
                print(f"Initializing LessonPlanComparator for {plan_config['name']}")
                lesson_plan_comparators[plan_id] = LessonPlanComparator(
                    mongo_uri=mongo_uri,
                    openrouter_api_key=openrouter_api_key,
                    selected_model=selected_model,
                )
                print(
                    f"LessonPlanComparator for {plan_config['name']} initialized successfully"
                )
            else:
                print(
                    f"Skipping LessonPlanComparator initialization for {plan_config['name']} (disabled in config)"
                )

            print(f"Initializing LessonPlanManager for {plan_config['name']}")
            comparator = (
                lesson_plan_comparators.get(plan_id) if enable_comparer else None
            )
            lesson_plan_managers[plan_id] = LessonPlanManager(
                lesson_plans[plan_id],
                comparator,
                working_directory=".",
                discord_webhook_url=discord_webhook_url,
            )
            print(
                f"LessonPlanManager for {plan_config['name']} initialized successfully"
            )

        flask_thread = threading.Thread(target=run_flask_app)
        flask_thread.daemon = True
        flask_thread.start()

        # Run managers sequentially in the main thread
        try:
            while True:
                for plan_id, manager in lesson_plan_managers.items():
                    print(f"\nStarting check cycle for {plans_config[plan_id]['name']}")
                    try:
                        manager.check_once()
                    except Exception as e:
                        print(
                            f"Error in manager for {plans_config[plan_id]['name']}: {str(e)}"
                        )

                # Po sprawdzeniu wszystkich planów, sprawdź aktywności Moodle
                try:
                    print("\nSprawdzanie aktywności Moodle...")
                    downloader = WebpageDownloader()
                    moodle_url = os.getenv("MOODLE_URL")
                    if not moodle_url:
                        raise ValueError("MOODLE_URL not set in environment variables")
                    
                    saved_file = downloader.save_webpage(moodle_url)
                    if saved_file:
                        parser = MoodleFileParser(
                            saved_file, 
                            api_key=openrouter_api_key,
                            mongodb_uri=mongo_uri
                        )
                        
                        # Parsuj aktywności z nowego pliku
                        new_activities = parser.parse_activities()
                        
                        # Pobierz wszystkie checksums z bazy
                        existing_checksums = {
                            act['checksum'] 
                            for act in db.Activities.find({}, {'checksum': 1})
                        }
                        
                        # Sprawdź które aktywności mają nowe checksums
                        activities_to_update = []
                        for activity in new_activities:
                            if activity.checksum not in existing_checksums:
                                activities_to_update.append(activity)
                        
                        if activities_to_update:
                            print(f"Wykryto {len(activities_to_update)} nowych/zmienionych aktywności - aktualizacja bazy...")
                            parser.save_to_mongodb()
                        else:
                            print("Brak nowych lub zmienionych aktywności")
                        
                        # Usuń pobrany plik
                        try:
                            os.remove(saved_file)
                            print(f"Usunięto plik tymczasowy: {saved_file}")
                        except Exception as e:
                            print(f"Błąd podczas usuwania pliku {saved_file}: {str(e)}")
                            
                except Exception as e:
                    print(f"Błąd podczas przetwarzania aktywności Moodle: {str(e)}")

                print(f"\nWszystkie zadania zakończone. Oczekiwanie {check_interval} sekund przed następnym cyklem...")
                time.sleep(check_interval)

        except KeyboardInterrupt:
            print("\nShutting down gracefully...")
        except Exception as e:
            print(f"Fatal error: {str(e)}")

        print("Initializing LessonPlanComparator")
        lesson_plan_comparator = LessonPlanComparator(
            mongo_uri=mongo_uri,
            openrouter_api_key=openrouter_api_key,
            selected_model=selected_model,
        )
        print("LessonPlanComparator initialized successfully")

        print("Initializing LessonPlanManager")
        lesson_plan_manager = LessonPlanManager(
            lesson_plan,
            lesson_plan_comparator,
            working_directory=".",
            discord_webhook_url=discord_webhook_url,
        )
        print("LessonPlanManager initialized successfully")

        print("Starting LessonPlanManager")
        lesson_plan_manager.start()
    except Exception as e:
        print(f"An error occurred in main.py: {str(e)}")
        print("Traceback:")
        print(traceback.format_exc())


if __name__ == "__main__":
    main()
