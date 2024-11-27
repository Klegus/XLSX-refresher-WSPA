import time
from datetime import datetime, timedelta
from push_notifications import PushNotificationManager
from LessonPlan import LessonPlan
from comparer import LessonPlanComparator
from ActivityDownloader import WebpageDownloader
from MoodleParserComponent import MoodleFileParser
import os, requests, json, hashlib
from werkzeug.exceptions import BadRequest, InternalServerError
from dotenv import load_dotenv
from pymongo import MongoClient
from typing import Dict
from fastapi import HTTPException
from typing import Optional
import pymongo
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
db = client[os.getenv("MONGO_DB")]


def get_system_config():
    """Get system configuration from MongoDB"""
    config = db.system_config.find_one({"_id": "config"})
    if not config:
        # Initialize default configuration
        config = {
            "_id": "config",
            "check_interval": 900,
            "maintenance_mode": False,
            "last_check_stats": {
                "total_plans": 0,
                "plans_checked": 0,
                "changes_detected": 0,
                "timestamp": datetime.now().isoformat(),
            },
        }
        db.system_config.insert_one(config)
    return config


def extract_faculty_from_collection(collection_name: str) -> str:
    """
    Extracts faculty name from collection name following the pattern:
    plans_faculty_rest_of_name or plans_faculty-name_rest_of_name
    """
    if collection_name.startswith("plans_"):
        # Remove 'plans_' prefix
        name_without_prefix = collection_name[6:]
        # Find the next underscore after the faculty name
        next_underscore = name_without_prefix.find("_")
        if next_underscore != -1:
            # Extract faculty name up to the underscore
            faculty = name_without_prefix[:next_underscore]
        else:
            # If no underscore, take the whole remaining string
            faculty = name_without_prefix

        # Handle both hyphenated and non-hyphenated names
        if "-" in faculty:
            # For hyphenated names, replace hyphens with spaces
            faculty = faculty.replace("-", " ")

        # Capitalize each word
        return faculty.title()
    return "Unknown"


def determine_category(collection_name: str) -> str:
    """
    Determines the study mode category based on collection name.
    Returns: 'nst_puw', 'nst', or 'st'
    """
    if "_nst_puw" in collection_name:
        return "nst_puw"
    elif "_nst" in collection_name:
        return "nst"
    return "st"


def get_semester_collections() -> Dict[str, Dict]:
    """
    Pobiera listę wszystkich kolekcji planów i ich najnowsze dokumenty.
    Zwraca słownik z nazwami kolekcji i odpowiadającymi im informacjami.
    """
    collections_data = {}
    for collection_name in db.list_collection_names():
        if collection_name.startswith("plans_"):
            # Pobierz najnowszy dokument z kolekcji
            latest_plan = db[collection_name].find_one(sort=[("timestamp", -1)])
            if latest_plan and "plan_name" in latest_plan and "groups" in latest_plan:
                faculty = extract_faculty_from_collection(collection_name)
                category = determine_category(collection_name)
                collections_data[collection_name] = {
                    "plan_name": latest_plan["plan_name"],
                    "groups": latest_plan["groups"],
                    "timestamp": latest_plan["timestamp"],
                    "category": category,
                    "faculty": faculty,
                }
    return collections_data


def get_plans_config():
    """Get plans configuration from MongoDB"""
    print("\nAttempting to load plans configuration from MongoDB...")
    config = db.plans_config.find_one({"_id": "plans_json"})

    if not config:
        print("No plans found in MongoDB. Attempting to import from plans.json...")
        try:
            with open("plans.json", "r", encoding="utf-8") as f:
                plans_data = json.load(f)
                config = {
                    "_id": "plans_json",
                    "plans": plans_data,
                    "last_updated": datetime.now().isoformat(),
                }
                print("Successfully loaded plans from plans.json")
                result = db.plans_config.insert_one(config)
                print(
                    f"Successfully inserted plans into MongoDB with ID: {result.inserted_id}"
                )
        except Exception as e:
            print(f"Error loading plans.json: {e}")
            return None
    else:
        print(
            f"Found existing plans configuration in MongoDB, last updated: {config.get('last_updated')}"
        )

    return config.get("plans") if config else None


def update_system_config(updates):
    """Update system configuration in MongoDB"""
    return db.system_config.update_one(
        {"_id": "config"}, {"$set": updates}, upsert=True
    )


def update_plans_config(plans_data):
    """Update plans configuration in MongoDB"""
    # Get current plans configuration
    current_config = db.plans_config.find_one({"_id": "plans_json"})
    if current_config and "plans" in current_config:
        current_plans = current_config["plans"]
        # Update only the plans that are in plans_data
        for plan_id, plan_data in plans_data.items():
            current_plans[plan_id] = plan_data
        plans_to_update = current_plans
    else:
        plans_to_update = plans_data

    return db.plans_config.update_one(
        {"_id": "plans_json"},
        {"$set": {"plans": plans_to_update, "last_updated": datetime.now().isoformat()}},
        upsert=True,
    )


class StatusChecker:
    def __init__(self):
        self.last_activity = time.time()
        self.config = get_system_config()

    def update_activity(self):
        self.last_activity = time.time()

    def is_active(self):
        self.config = get_system_config()  # Refresh config
        if self.config.get("maintenance_mode", False):
            return False
        return True

    def get_last_activity_datetime(self):
        return datetime.fromtimestamp(self.last_activity).isoformat()

    def get_check_interval(self):
        self.config = get_system_config()  # Refresh config
        return self.config.get("check_interval", 900)


status_checker = StatusChecker()


# Import route modules
from routes.status import init_status_routes
from routes.config import init_config_routes
from routes.plans import init_plan_routes
from routes.logs import init_log_routes
from routes.activities import init_activity_routes
from routes.comparisons import init_comparison_routes
from routes.notifications import init_notification_routes


def log_check_cycle(successful_checks=0, new_plans=0, errors=None, execution_time=None):
    """Log check cycle results to MongoDB"""
    timestamp = datetime.now()

    # Create cycle log entry
    cycle_log = {
        "timestamp": timestamp,
        "successful_checks": successful_checks,
        "new_plans": new_plans,
        "has_errors": bool(errors),
        "execution_time": execution_time,  # Time in seconds
        "errors": [],
    }

    # Add error details if any
    if errors:
        for error in errors:
            error_detail = {
                "error_message": str(error.get("error")),
                "traceback": error.get("traceback"),
                "plan_name": error.get("plan_name", "Unknown"),
            }
            cycle_log["errors"].append(error_detail)

    # Add to check cycles collection
    db.check_cycles.insert_one(cycle_log)

    # Update system stats
    stats_update = {
        "last_check": {
            "timestamp": timestamp,
            "successful_checks": successful_checks,
            "new_plans": new_plans,
            "has_errors": bool(errors),
        }
    }

    db.system_config.update_one({"_id": "config"}, {"$set": stats_update})


def log_check_result(total_plans, plans_checked, changes_detected):
    """Log check results to MongoDB"""
    timestamp = datetime.now()
    log_entry = {
        "timestamp": timestamp,
        "total_plans": total_plans,
        "plans_checked": plans_checked,
        "changes_detected": changes_detected,
    }

    # Update last check stats in system config
    db.system_config.update_one(
        {"_id": "config"}, {"$set": {"last_check_stats": log_entry}}
    )

    # Add to logs collection
    db.check_cycles.insert_one(log_entry)


# Initialize push notifications
vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
vapid_public_key = os.getenv("VAPID_PUBLIC_KEY")
vapid_claims = {
    "sub": "mailto:" + os.getenv("VAPID_CONTACT_EMAIL", "admin@wspa.edu.pl")
}

push_manager = PushNotificationManager(
    db=db,
    vapid_private_key=vapid_private_key,
    vapid_public_key=vapid_public_key,
    vapid_claims=vapid_claims
)

# Initialize routes
init_status_routes(app, status_checker, get_system_config)
init_config_routes(
    app,
    get_system_config,
    get_plans_config,
    update_system_config,
    update_plans_config,
    db,
)
init_plan_routes(app, get_semester_collections, db)
init_log_routes(app, db)
init_activity_routes(app, db)
init_comparison_routes(app, db)
init_notification_routes(app, push_manager)


def run_flask_app():
    app.run(host="0.0.0.0", port=80)


class LessonPlanManager:
    def __init__(
        self,
        lesson_plan,
        lesson_plan_comparator,
        check_interval=900,
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
                    #print(f"Usunięto plik: {file}")
                except Exception as e:
                    print(f"Błąd podczas usuwania pliku {file}: {str(e)}")

    def should_send_webhook(self):
        """Sprawdza czy należy wysyłać powiadomienia webhook dla tego planu"""
        plan_config = self.lesson_plan.plan_config
        # Domyślnie notify i compare są False
        return plan_config.get("notify", False) or plan_config.get("compare", False)

    def send_discord_webhook(self, message, force_send=False):
        """
        Wysyła webhook jeśli jest skonfigurowany i dozwolony.
        force_send wymusza wysłanie niezależnie od ustawień notify/compare
        """
        if not self.discord_webhook_url:
            return

        if not force_send and not self.should_send_webhook():
            return

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
        #print("Zaktualizowano pamięć podręczną planów lekcji.")

    def check_once(self):
        """Wykonuje pojedynczy cykl sprawdzania planu"""
        # Reload plan config from DB
        plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
        if plans_config_doc and "plans" in plans_config_doc:
            self.lesson_plan.plan_config = plans_config_doc["plans"].get(self.plan_name, self.lesson_plan.plan_config)
        
        current_time = datetime.now()
        current_hour = current_time.hour

        # Skip checks between 21:00 and 06:00
        if os.getenv("DEV", "false").lower() == "true":
            print("Dev mode is enabled. Skipping time check.")
            is_night_time = False
        else:
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
                    # Send push notifications if manager is available
                    if push_manager:
                        collection_name = f"plans_{self.plan_config['faculty'].replace(' ', '-')}_{self.plan_config['name'].lower().replace(' ', '_').replace('-', '_')}"
                        push_manager.notify_plan_update(
                            collection_name=collection_name,
                            plan_name=self.plan_name
                        )
                    # Sprawdź czy plan ma włączone porównywanie
                    # Check if plan has comparison enabled and comparator is available
                    should_compare = (
                        self.lesson_plan.plan_config.get("compare", False)
                        and self.lesson_plan_comparator is not None
                    )

                    if should_compare:
                        try:
                            print("Comparing plans...")
                            collection_name = (
                                self.plan_name.lower()
                                .replace(" ", "_")
                                .replace("-", "_")
                            )
                            comparison_result = (
                                self.lesson_plan_comparator.compare_plans(
                                    collection_name
                                )
                            )
                            if comparison_result:
                                webhook_message = f"Zmiany w planie dla: {self.plan_name}\n\n{comparison_result}"
                                self.send_discord_webhook(
                                    webhook_message, force_send=True
                                )
                                
                                
                                
                                print("Wykryto i zapisano zmiany w planie.")
                                return True
                        except Exception as e:
                            print(f"Error during plan comparison: {e}")
                            # Fall back to simple notification if comparison fails
                            if self.lesson_plan.plan_config.get("notify", False):
                                webhook_message = f"Plan zajęć został zaktualizowany dla: {self.plan_name}"
                                self.send_discord_webhook(webhook_message)
                    elif self.lesson_plan.plan_config.get("notify", False):
                        # If not comparing but notify is true
                        webhook_message = (
                            f"Plan zajęć został zaktualizowany dla: {self.plan_name}"
                        )
                        self.send_discord_webhook(webhook_message)
                        print("Wykryto i zapisano zmiany w planie.")
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


@app.route("/panel")
def show_panel():
    return Response(open("templates/panel.html").read(), mimetype="text/html")

@app.route("/api/collections")
def get_collections():
    collections = get_semester_collections()
    return jsonify(collections)


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
        
        # Initialize push notifications
        vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
        vapid_public_key = os.getenv("VAPID_PUBLIC_KEY")
        vapid_claims = {
            "sub": "mailto:" + os.getenv("VAPID_CONTACT_EMAIL", "admin@wspia.edu.pl")
        }
        
        push_manager = PushNotificationManager(
            db=db,
            vapid_private_key=vapid_private_key,
            vapid_public_key=vapid_public_key,
            vapid_claims=vapid_claims
        )
        # Check if plans configuration exists in MongoDB
        plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
        
        if plans_config_doc and "plans" in plans_config_doc:
            #print("Loading plans configuration from MongoDB...")
            plans_config = plans_config_doc["plans"]
        else:
            #print("No plans configuration found in MongoDB. Loading from plans.json...")
            # Load from plans.json and store in MongoDB
            with open("plans.json", "r", encoding="utf-8") as f:
                plans_config = json.load(f)
                db.plans_config.insert_one({
                    "_id": "plans_json",
                    "plans": plans_config,
                    "last_updated": datetime.now().isoformat()
                })

        lesson_plans = {}
        lesson_plan_comparators = {}
        lesson_plan_managers = {}

        for plan_id, plan_config in plans_config.items():
            #print(f"Initializing LessonPlan for {plan_config['name']}")
            lesson_plans[plan_id] = LessonPlan(
                username=username,
                password=password,
                mongo_uri=mongo_uri,
                plan_config=plan_config,
            )
            #print(f"LessonPlan for {plan_config['name']} initialized successfully")

            comparator = None

            # Debug info about plan settings
            compare_enabled = plan_config.get("compare", False)
            notify_enabled = plan_config.get("notify", False)
            if compare_enabled or notify_enabled:
                print(f"\nPlan settings for {plan_config['name']}:")
                print(f"- Compare enabled: {compare_enabled}")
                print(f"- Notify enabled: {notify_enabled}")

            if compare_enabled and openrouter_api_key and selected_model:
                print(f"Initializing LessonPlanComparator for {plan_config['name']}")
                try:
                    comparator = LessonPlanComparator(
                        mongo_uri=mongo_uri,
                        openrouter_api_key=openrouter_api_key,
                        selected_model=selected_model,
                    )
                    lesson_plan_comparators[plan_id] = comparator
                    #print(
                    #    f"LessonPlanComparator for {plan_config['name']} initialized successfully"
                    #)
                except Exception as e:
                    print(
                        f"Failed to initialize comparator for {plan_config['name']}: {e}"
                    )
                    comparator = None
            else:
                if compare_enabled:
                    print(
                        f"Cannot initialize comparator for {plan_config['name']} - missing required settings:"
                    )
                    print(
                        f"- OpenRouter API key: {'Present' if openrouter_api_key else 'Missing'}"
                    )
                    print(
                        f"- Selected model: {'Present' if selected_model else 'Missing'}"
                    )
                else:
                    print(
                        f"Skipping LessonPlanComparator initialization for {plan_config['name']} (compare not enabled in plans.json)"
                    )

            #print(f"Initializing LessonPlanManager for {plan_config['name']}")
            lesson_plan_managers[plan_id] = LessonPlanManager(
                lesson_plans[plan_id],
                comparator,
                working_directory=".",
                discord_webhook_url=discord_webhook_url,
            )
            #print(
            #    f"LessonPlanManager for {plan_config['name']} initialized successfully"
            #)

        flask_thread = threading.Thread(target=run_flask_app)
        flask_thread.daemon = True
        flask_thread.start()

        # Run managers sequentially in the main thread
        try:
            while True:
                successful_checks = 0
                new_plans = 0
                errors = []
                cycle_start_time = time.time()

                for plan_id, manager in lesson_plan_managers.items():
                    plan_name = plans_config[plan_id]["name"]
                    print(f"\nStarting check cycle for {plan_name}")
                    try:
                        result = manager.check_once()
                        successful_checks += 1
                        if result:
                            new_plans += 1
                    except Exception as e:
                        error_info = {
                            "error": e,
                            "traceback": traceback.format_exc(),
                            "plan_name": plan_name,
                        }
                        errors.append(error_info)
                        print(f"Error in manager for {plan_name}: {str(e)}")

                # Calculate total execution time for the cycle
                cycle_execution_time = round(time.time() - cycle_start_time, 2)
                print(f"\nTotal cycle execution time: {cycle_execution_time} seconds")

                # Log the check cycle results
                log_check_cycle(
                    successful_checks=successful_checks,
                    new_plans=new_plans,
                    errors=errors if errors else None,
                    execution_time=cycle_execution_time,
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
                            mongodb_uri=mongo_uri,
                        )

                        # Parsuj i zapisz aktywności
                        parser.parse_activities()
                        parser.save_to_mongodb()

                        # Usuń pobrany plik
                        try:
                            os.remove(saved_file)
                            print(f"Usunięto plik tymczasowy: {saved_file}")
                        except Exception as e:
                            print(f"Błąd podczas usuwania pliku {saved_file}: {str(e)}")

                except Exception as e:
                    print(f"Błąd podczas przetwarzania aktywności Moodle: {str(e)}")

                print(
                    f"\nWszystkie zadania zakończone. Oczekiwanie {check_interval} sekund przed następnym cyklem..."
                )
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
