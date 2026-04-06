import time
from datetime import datetime, timedelta
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
import asyncio
import logging
import sys

# Import shared logger
from shared_utils import configure_root_logger, get_system_config, get_semester_collections, log_cycle_summary, log_plan_header

# Setup app-specific logger
logger = configure_root_logger(logging.INFO)

load_dotenv()


app = Flask(__name__)

# Load environment variables

USE_TEST_TIME = False
TEST_TIME = None
mongo_uri = os.getenv("MONGO_URI")
client = MongoClient(mongo_uri)
db = client[os.getenv("MONGO_DB")]
PLANS_JSON_URL = os.getenv("PLANS_JSON_URL")
# Global variable for lesson plan managers
lesson_plan_managers = {}

from shared_utils import get_system_config, get_semester_collections


def get_plans_config():
    """Get plans configuration from MongoDB or URL"""
    logger.info("\nAttempting to load plans configuration from MongoDB...")
    
    # Try to get from MongoDB first
    config = db.plans_config.find_one({"_id": "plans_json"})
    
    if config and "plans" in config:
        logger.info(f"Using plans from MongoDB, last updated: {config.get('last_updated')}")
        return config.get("plans")
    
    # If not found in MongoDB, try to fetch from URL
    logger.info("No plans configuration found in MongoDB, trying URL...")
    if not PLANS_JSON_URL:
        logger.warning("PLANS_JSON_URL environment variable not set.")
        logger.info("Attempting to load from local plans.json file...")

        # Try to load from local file as fallback
        try:
            import json
            local_file = os.path.join(os.path.dirname(__file__), 'plans.json')
            if os.path.exists(local_file):
                with open(local_file, 'r', encoding='utf-8') as f:
                    plans_data = json.load(f)

                # Save to MongoDB for future use
                config = {
                    "_id": "plans_json",
                    "plans": plans_data,
                    "last_updated": datetime.now().isoformat(),
                    "source": "local_file"
                }

                db.plans_config.update_one(
                    {"_id": "plans_json"},
                    {"$set": config},
                    upsert=True
                )

                logger.info(f"Successfully loaded {len(plans_data)} plans from local file")
                return plans_data
            else:
                logger.error(f"Local plans.json file not found at {local_file}")
                update_system_config({"maintenance_mode": True, "maintenance_reason": "No plans.json found"})
                return None
        except Exception as e:
            logger.error(f"Failed to load local plans.json: {e}")
            update_system_config({"maintenance_mode": True, "maintenance_reason": f"Error loading plans.json: {e}"})
            return None
    
    try:
        # Fetch from URL
        logger.info(f"Fetching plans from URL: {PLANS_JSON_URL}")
        response = requests.get(PLANS_JSON_URL, timeout=10)
        response.raise_for_status()  # Raise exception for HTTP errors
        
        plans_data = response.json()
        
        # Save to MongoDB
        config = {
            "_id": "plans_json",
            "plans": plans_data,
            "last_updated": datetime.now().isoformat(),
            "source": "url"
        }
        
        db.plans_config.update_one(
            {"_id": "plans_json"},
            {"$set": config},
            upsert=True
        )
        
        logger.info(f"Successfully fetched and stored plans from URL")
        
        # If we were in maintenance mode due to plans, exit maintenance mode
        current_config = get_system_config()
        if current_config.get("maintenance_mode") and current_config.get("maintenance_reason") == "Plans data unavailable":
            update_system_config({"maintenance_mode": False, "maintenance_reason": None})
        
        return plans_data
        
    except Exception as e:
        logger.error(f"Error fetching plans from URL: {e}")
        
        # Enter maintenance mode if we couldn't get plans data
        logger.warning("No plans data available. Entering maintenance mode.")
        update_system_config({"maintenance_mode": True, "maintenance_reason": "Plans data unavailable"})
        return None


def update_system_config(updates):
    """Update system configuration in MongoDB"""
    return db.system_config.update_one(
        {"_id": "config"}, {"$set": updates}, upsert=True
    )


def update_plans_config(plans_data):
    """Update plans configuration in MongoDB"""
    logger.info(f"Updating plans configuration in MongoDB")
    logger.debug(f"Plans data type: {type(plans_data)}")
    logger.info(f"Plans contains {len(plans_data)} plans")
    
    # Make sure we're storing the plans correctly in the "plans" field
    result = db.plans_config.update_one(
        {"_id": "plans_json"},
        {"$set": {"plans": plans_data, "last_updated": datetime.now().isoformat()}},
        upsert=True
    )
    
    # Verify the update by reading it back
    updated_config = db.plans_config.find_one({"_id": "plans_json"})
    if updated_config and "plans" in updated_config:
        logger.info(f"Updated plans config successfully, now contains {len(updated_config['plans'])} plans")
    else:
        logger.warning("WARNING: Failed to verify updated plans configuration!")
    
    return result


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
from routes.plans import init_plan_routes
from routes.activities import init_activity_routes
from routes.comparisons import init_comparison_routes


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



# Initialize routes (public API only - admin panel runs separately)
init_status_routes(app, status_checker, get_system_config)
init_plan_routes(app, get_semester_collections, db)
init_activity_routes(app, db)
init_comparison_routes(app, db)


def run_flask_app():
    port = int(os.getenv("PORT", "5005"))
    app.run(host="0.0.0.0", port=port)


class LessonPlanManager:
    def __init__(
        self,
        lesson_plan,
        lesson_plan_comparator,
        check_interval=900,
        working_directory=".",
        plan_config=None,
    ):
        self.lesson_plan = lesson_plan
        self.lesson_plan_comparator = lesson_plan_comparator
        self.plan_config = lesson_plan.plan_config  # Store the plan_config from lesson_plan
        self.plan_name = lesson_plan.plan_config["name"]
        self.check_interval = check_interval
        self.working_directory = working_directory
        self.initial_file_structure = set()
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
                    logger.debug(f"Usunięto plik: {file}")
                except Exception as e:
                    logger.error(f"Błąd podczas usuwania pliku {file}: {str(e)}")

    def get_webhook_url(self):
        """Pobiera URL webhooka z konfiguracji Discord w kolekcji planu"""
        try:
            collection = db[self.lesson_plan.collection_name]
            discord_config = collection.find_one({"_id": "discord_config"})
            if discord_config and "webhook_url" in discord_config:
                logger.info("Pobrano URL webhooka z konfiguracji Discord.")
                logger.debug(f"Webhook URL: {discord_config['webhook_url']}")
                return discord_config["webhook_url"]
        except Exception as e:
            logger.error(f"Błąd podczas pobierania webhook URL: {str(e)}")
        return None

    def should_send_webhook(self):
        """Sprawdza czy należy wysyłać powiadomienia webhook dla tego planu"""
        webhook_url = self.get_webhook_url()
        # Wysyłaj powiadomienia zawsze gdy jest webhook URL
        return webhook_url is not None


    def update_cached_plans(self):
        latest_plan = get_latest_lesson_plan()
        if latest_plan:
            for group, html_content in latest_plan["groups"].items():
                self.cached_plans[group] = parse_html_to_dataframe(html_content)
        logger.debug("Zaktualizowano pamięć podręczną planów lekcji.")

    async def check_once(self):
        """Wykonuje pojedynczy cykl sprawdzania planu"""
        # Reload plan config from DB
        plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
        if plans_config_doc and "plans" in plans_config_doc:
            self.lesson_plan.plan_config = plans_config_doc["plans"].get(self.plan_name, self.lesson_plan.plan_config)
        
        current_time = datetime.now()
        current_hour = current_time.hour

        # Skip checks between 21:00 and 06:00
        if os.getenv("DEV", "false").lower() == "true":
            logger.info("Dev mode is enabled. Skipping time check.")
            is_night_time = False
        else:
            is_night_time = current_hour >= 21 or current_hour < 6
        if is_night_time:
            logger.info(
                f"Skipping check at {current_time.strftime('%Y-%m-%d %H:%M:%S')} - night hours (21:00-06:00)"
            )
            return

        try:
            logger.info(
                f"Starting check",
                extra={"plan_name": self.plan_name, "operation": "check"}
            )
            self.status_checker.update_activity()
            new_checksum = self.lesson_plan.process_and_save_plan()

            if new_checksum is None:
                logger.error("Error checking plan", extra={"plan_name": self.plan_name})
            else:
                if new_checksum:
                    logger.info("Plan updated", extra={"plan_name": self.plan_name, "checksum": new_checksum})
                    # Check if plan has comparison enabled and comparator is available
                    should_compare = (
                        self.lesson_plan.plan_config.get("compare", False)
                        and self.lesson_plan_comparator is not None
                    )
                    #check if plan has notify enabled
                    should_notify = self.lesson_plan.plan_config.get("notify", False)
                    webhook_url = None
                    if should_notify:
                        webhook_url = self.get_webhook_url()
                    if webhook_url:
                        try:
                            embed = {
                                "title": f"Aktualizacja planu zajęć: {self.plan_name}",
                                "color": 15158332,  # Czerwony kolor (decimal)
                                "timestamp": datetime.utcnow().isoformat(),
                                "fields": []
                            }

                            if should_compare:
                                try:
                                    logger.info("Comparing plans...", extra={"plan_name": self.plan_name})
                                    comparison_result = (
                                        self.lesson_plan_comparator.compare_plans(
                                            self.plan_config
                                        )
                                    )
                                    if comparison_result:
                                        embed["description"] = "Wykryto zmiany w planie zajęć!"
                                        embed["fields"].append({
                                            "name": "Szczegóły zmian",
                                            "value": comparison_result
                                        })
                                except Exception as e:
                                    logger.error(f"Error during plan comparison: {e}", extra={"plan_name": self.plan_name})
                                    embed["description"] = "Plan zajęć został zaktualizowany.\nWystąpił błąd podczas porównywania zmian."
                            else:
                                embed["description"] = "Plan zajęć został zaktualizowany."

                            webhook_data = {
                                "embeds": [embed]
                            }

                            requests.post(webhook_url, json=webhook_data)
                            logger.info("Webhook sent", extra={"plan_name": self.plan_name})
                        except Exception as e:
                            logger.error(f"Error sending webhook: {str(e)}", extra={"plan_name": self.plan_name})
                    logger.info("Changes detected and saved", extra={"plan_name": self.plan_name})
                    return True
                    self.update_cached_plans()
                    logger.info("Cache updated", extra={"plan_name": self.plan_name})
                else:
                    logger.info("No changes detected", extra={"plan_name": self.plan_name})

            self.clean_new_files()

        except Exception as e:
            logger.error(f"Error during check: {str(e)}", extra={"plan_name": self.plan_name})
            raise

    def start(self):
        """Deprecated - use check_once() instead"""
        logger.warning("Warning: start() is deprecated. Use check_once() instead.")
        self.check_once()


lesson_plan_manager = None
lesson_plan = None


def get_group_key(group_number):
    # Get configuration from URL via get_plans_config
    plans_config = get_plans_config()
    if not plans_config:
        logger.error("Failed to fetch plans data")
        return None

    # Get groups for informatyka2
    if "informatyka2" not in plans_config:
        return None
        
    groups = plans_config["informatyka2"]["groups"]
    group_keys = list(groups.keys())

    if 0 <= group_number < len(group_keys):
        return group_keys[group_number]
    return None


def get_latest_lesson_plan():
    try:
        return db.plans.find_one(sort=[("timestamp", -1)])
    except Exception as e:
        logger.error(f"Error fetching the latest lesson plan: {str(e)}")
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


@app.route("/api/collections")
def get_collections():
    collections = get_semester_collections()
    return jsonify(collections)


def compare_plans_config_with_url():
    """
    Compare plans configuration in MongoDB with the one from the URL.
    If differences are detected, delete the last document for each changed plan to force a fresh check.
    Returns a dict of plan_id: True/False indicating if the plan was changed.
    """
    logger.info("\nComparing plans configuration in MongoDB with URL...")
    
    # Get the current plans from MongoDB
    current_config = db.plans_config.find_one({"_id": "plans_json"})
    if not current_config or "plans" not in current_config:
        logger.warning("No plans configuration found in MongoDB")
        return {}
    
    current_plans = current_config.get("plans", {})
    
    # Skip if URL is not set
    if not PLANS_JSON_URL:
        logger.warning("PLANS_JSON_URL environment variable not set, skipping comparison")
        return {}
    
    try:
        # Fetch the latest plans from URL
        logger.info(f"Fetching plans from URL: {PLANS_JSON_URL}")
        response = requests.get(PLANS_JSON_URL, timeout=10)
        response.raise_for_status()
        
        url_plans = response.json()
        
        # Track which plans changed
        changed_plans = {}
        
        # Compare plans
        for plan_id, url_plan_config in url_plans.items():
            if plan_id not in current_plans:
                logger.info(f"New plan found in URL: {plan_id}")
                changed_plans[plan_id] = True
                continue
                
            current_plan_config = current_plans[plan_id]
            
            # Deep compare the configurations
            if not configs_are_equal(current_plan_config, url_plan_config):
                logger.info(f"Configuration changes detected for plan: {plan_id}")
                
                # Get the collection name for this plan
                if plan_id in lesson_plan_managers:
                    manager = lesson_plan_managers[plan_id]
                    collection_name = manager.lesson_plan.collection_name
                    
                    if collection_name:
                        # Find and delete the last document
                        collection = db[collection_name]
                        last_plan = collection.find_one(
                            {"_id": {"$ne": "discord_config"}}, 
                            sort=[("timestamp", -1)]
                        )
                        
                        if last_plan:
                            collection.delete_one({"_id": last_plan["_id"]})
                            logger.info(f"Deleted last plan document from {collection_name} to force a fresh check")
                        else:
                            logger.info(f"No previous plan documents found in {collection_name}")
                            
                changed_plans[plan_id] = True
            else:
                # Plan configuration is the same
                changed_plans[plan_id] = False
                
        # Handle plans that are in MongoDB but not in URL
        for plan_id in current_plans:
            if plan_id not in url_plans:
                logger.info(f"Plan {plan_id} exists in MongoDB but not in URL")
                # We don't need to force a check here as the plan will be ignored
                changed_plans[plan_id] = False
        
        # Update MongoDB with the new plans configuration
        db.plans_config.update_one(
            {"_id": "plans_json"},
            {"$set": {
                "plans": url_plans,
                "last_updated": datetime.now().isoformat(),
                "source": "url"
            }},
            upsert=True
        )
        
        logger.info(f"Updated plans configuration in MongoDB with data from URL")
        return changed_plans
        
    except Exception as e:
        logger.error(f"Error comparing plans configuration: {e}")
        logger.error(traceback.format_exc())
        return {}


def configs_are_equal(config1, config2):
    """
    Compare two plan configurations deeply.
    Returns True if they are equal, False otherwise.
    """
    # Check if the key structure is the same
    if set(config1.keys()) != set(config2.keys()):
        return False
    
    # Check values for each key
    for key in config1:
        # For nested dictionaries, recursively compare
        if isinstance(config1[key], dict) and isinstance(config2[key], dict):
            if not configs_are_equal(config1[key], config2[key]):
                return False
        # For lists, compare elements
        elif isinstance(config1[key], list) and isinstance(config2[key], list):
            if len(config1[key]) != len(config2[key]):
                return False
            # If it's a list of dictionaries, compare each dictionary
            if all(isinstance(item, dict) for item in config1[key]) and all(isinstance(item, dict) for item in config2[key]):
                for i in range(len(config1[key])):
                    if i >= len(config2[key]) or not configs_are_equal(config1[key][i], config2[key][i]):
                        return False
            # Otherwise compare the lists directly
            elif config1[key] != config2[key]:
                return False
        # For other values, compare directly
        elif config1[key] != config2[key]:
            return False
    
    return True


async def main():
    logger.info("Starting main.py")
    check_interval = 900
    try:
        logger.info("Loading .env file")
        load_dotenv()
        logger.info(".env file loaded successfully")

        global lesson_plan, lesson_plan_manager
        username = os.getenv("EMAIL")
        password = os.getenv("PASSWORD")
        mongo_uri = os.getenv("MONGO_URI")
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        selected_model = os.getenv("SELECTED_MODEL", "openai/chatgpt-4o-latest")

        # Use the configured check interval from MongoDB/system config.
        try:
            check_interval = int(get_system_config().get("check_interval", 900))
        except (TypeError, ValueError):
            check_interval = 900
        
        # Check if plans configuration exists in MongoDB
        plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
        
        if plans_config_doc and "plans" in plans_config_doc:
            logger.debug("Loading plans configuration from MongoDB...")
            plans_config = plans_config_doc["plans"]
        else:
            logger.debug("No plans configuration found in MongoDB. Fetching from URL...")
            plans_config = get_plans_config()
            if not plans_config:
                logger.error("Failed to fetch plans data. Cannot initialize lesson plans.")
                return False

        lesson_plans = {}
        lesson_plan_comparators = {}
        global lesson_plan_managers

        for plan_id, plan_config in plans_config.items():
            logger.debug(f"Initializing LessonPlan for {plan_config['name']}")
            lesson_plans[plan_id] = LessonPlan(
                username=username,
                password=password,
                mongo_uri=mongo_uri,
                plan_config=plan_config,
            )
            logger.debug(f"LessonPlan for {plan_config['name']} initialized successfully")

            comparator = None

            # Debug info about plan settings
            compare_enabled = plan_config.get("compare", False)
            notify_enabled = plan_config.get("notify", False)
            if compare_enabled or notify_enabled:
                logger.info(f"\nPlan settings for {plan_config['name']}:")
                logger.info(f"- Compare enabled: {compare_enabled}")
                logger.info(f"- Notify enabled: {notify_enabled}")

            if compare_enabled and openrouter_api_key and selected_model:
                logger.info(f"Initializing LessonPlanComparator for {plan_config['name']}")
                try:
                    comparator = LessonPlanComparator(
                        mongo_uri=mongo_uri,
                        openrouter_api_key=openrouter_api_key,
                        selected_model=selected_model,
                    )
                    lesson_plan_comparators[plan_id] = comparator
                    logger.debug(
                        f"LessonPlanComparator for {plan_config['name']} initialized successfully"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to initialize comparator for {plan_config['name']}: {e}"
                    )
                    comparator = None
            else:
                if compare_enabled:
                    logger.warning(
                        f"Cannot initialize comparator for {plan_config['name']} - missing required settings:"
                    )
                    logger.warning(
                        f"- OpenRouter API key: {'Present' if openrouter_api_key else 'Missing'}"
                    )
                    logger.warning(
                        f"- Selected model: {'Present' if selected_model else 'Missing'}"
                    )
                else:
                    logger.debug(
                        f"Skipping LessonPlanComparator initialization for {plan_config['name']} (compare not enabled in plans.json)"
                    )

            logger.debug(f"Initializing LessonPlanManager for {plan_config['name']}")
            lesson_plan_managers[plan_id] = LessonPlanManager(
                lesson_plans[plan_id],
                comparator,
                working_directory=".",
                plan_config=plan_config,
            )
            logger.debug(
                f"LessonPlanManager for {plan_config['name']} initialized successfully"
            )

        # Inicjalizacja i uruchomienie Flask
        flask_thread = threading.Thread(target=run_flask_app)
        flask_thread.daemon = True
        flask_thread.start()

        # Run managers sequentially in the main thread
        try:
            while True:
                # Refresh check interval each cycle to reflect runtime config changes.
                try:
                    check_interval = int(get_system_config().get("check_interval", check_interval))
                except (TypeError, ValueError):
                    logger.warning("Invalid check_interval in system config; using previous value.")

                successful_checks = 0
                new_plans = 0
                errors = []
                cycle_start_time = time.time()

                # Start a transaction for the entire check cycle
                with sentry_sdk.start_transaction(op="check_cycle", name="check_plans_cycle") as transaction:
                    logger.info("Started Sentry transaction for check cycle")
                    transaction.set_tag("check_cycle", "true")
                    
                    # Refresh plans config from MongoDB
                    plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
                    if plans_config_doc and "plans" in plans_config_doc:
                        plans_config = plans_config_doc["plans"]
                    
                    # Compare plan configuration with URL and update if needed
                    # This will also delete last documents for changed plans
                    logger.info("\nComparing plan configurations with URL...")
                    with sentry_sdk.start_span(op="compare_configs", description="Compare plans config with URL"):
                        changed_plans = compare_plans_config_with_url()
                    
                    # Reload plans config after potential update
                    plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
                    if plans_config_doc and "plans" in plans_config_doc:
                        plans_config = plans_config_doc["plans"]
                    
                    # Check for new plans and create managers for them
                    for plan_id, plan_config in plans_config.items():
                        if plan_id not in lesson_plan_managers:
                            logger.info(f"\nNew plan detected: {plan_config['name']} ({plan_id})")
                            logger.info(f"Initializing new LessonPlan for {plan_config['name']}")
                            
                            # Create new LessonPlan
                            new_lesson_plan = LessonPlan(
                                username=username,
                                password=password,
                                mongo_uri=mongo_uri,
                                plan_config=plan_config,
                            )
                            
                            # Check if this plan was previously removed and has a collection
                            collection_name = new_lesson_plan.collection_name
                            if collection_name and collection_name in db.list_collection_names():
                                # Check if there's a removal record
                                removal_records = list(db[collection_name].find(
                                    {"event": "plan_removed", "plan_id": plan_id},
                                    sort=[("timestamp", -1)],
                                    limit=1
                                ))
                                
                                if removal_records:
                                    # Plan was previously removed and is now back
                                    logger.info(f"Plan {plan_config['name']} is being restored - it was previously removed")
                                    # Add restoration record
                                    db[collection_name].insert_one({
                                        "_id": f"restored_{datetime.now().isoformat()}",
                                        "timestamp": datetime.now(),
                                        "event": "plan_restored",
                                        "plan_id": plan_id,
                                        "plan_name": plan_config['name'],
                                        "message": "This plan has been restored to the configuration"
                                    })
                                    logger.info(f"Added restoration record to collection {collection_name}")
                            
                            # Check if comparator is needed
                            new_comparator = None
                            compare_enabled = plan_config.get("compare", False)
                            if compare_enabled and openrouter_api_key and selected_model:
                                logger.info(f"Initializing LessonPlanComparator for {plan_config['name']}")
                                try:
                                    new_comparator = LessonPlanComparator(
                                        mongo_uri=mongo_uri,
                                        openrouter_api_key=openrouter_api_key,
                                        selected_model=selected_model,
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to initialize comparator for {plan_config['name']}: {e}")
                                    new_comparator = None
                            
                            # Create and add new manager
                            logger.info(f"Initializing LessonPlanManager for {plan_config['name']}")
                            lesson_plan_managers[plan_id] = LessonPlanManager(
                                new_lesson_plan,
                                new_comparator,
                                working_directory=".",
                                plan_config=plan_config,
                            )
                            logger.info(f"LessonPlanManager for {plan_config['name']} successfully created")
                    
                    logger.info(f"Starting check cycle for {len(lesson_plan_managers)} plans")

                    for plan_id, manager in lesson_plan_managers.items():
                        if plan_id in plans_config:
                            manager.lesson_plan.plan_config = plans_config[plan_id]
                            manager.plan_config = plans_config[plan_id]

                        plans_to_remove = []
                        for plan_id in lesson_plan_managers:
                            if plan_id not in plans_config:
                                plans_to_remove.append(plan_id)

                        for plan_id in plans_to_remove:
                            manager = lesson_plan_managers[plan_id]
                            plan_name = manager.plan_name
                            logger.info(f"\nRemoving plan that no longer exists in config: {plan_name} ({plan_id})")
                            
                            # Get collection name for this plan
                            collection_name = manager.lesson_plan.collection_name
                            if collection_name:
                                # We don't delete the collection to preserve historical data
                                # but we can mark it as inactive or add a log entry
                                try:
                                    db[collection_name].insert_one({
                                        "_id": f"removed_{datetime.now().isoformat()}",
                                        "timestamp": datetime.now(),
                                        "event": "plan_removed",
                                        "plan_id": plan_id,
                                        "plan_name": plan_name,
                                        "message": "This plan was removed from the configuration"
                                    })
                                    logger.info(f"Added removal record to collection {collection_name}")
                                except Exception as e:
                                    logger.error(f"Error adding removal record: {e}")
                            
                            del lesson_plan_managers[plan_id]
                            logger.info(f"Manager for {plan_name} successfully removed")

                        plan_name = plans_config[plan_id]["name"]
                        log_plan_header(plan_name, plan_id, "check")
                        try:
                            with sentry_sdk.start_span(op="check_plan", description=f"Check plan: {plan_name}"):
                                result = await manager.check_once()
                                successful_checks += 1
                                if result:
                                    new_plans += 1
                        except Exception as e:
                            error_info = {
                                "timestamp": datetime.now(),
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                                "plan_name": plan_name,
                                "type": "plan_check_error"
                            }
                            # Store in errors collection
                            db.errors.insert_one(error_info)
                            sentry_sdk.capture_exception(e)
                            errors.append(error_info)
                            logger.error(f"Error in manager for {plan_name}: {str(e)}")

                    cycle_execution_time = round(time.time() - cycle_start_time, 2)

                    total_plans = len(lesson_plan_managers)
                    next_check = datetime.now() + timedelta(seconds=check_interval)

                    log_cycle_summary(
                        duration=cycle_execution_time,
                        plans_checked=successful_checks,
                        total_plans=total_plans,
                        changes_detected=new_plans,
                        errors_count=len(errors),
                        next_check_time=next_check.strftime('%Y-%m-%d %H:%M:%S')
                    )

                    transaction.set_data("successful_checks", successful_checks)
                    transaction.set_data("new_plans", new_plans)
                    transaction.set_data("execution_time", cycle_execution_time)
                    transaction.set_data("has_errors", bool(errors))
                    
                   
                

                    # Log the check cycle results
                    log_check_cycle(
                        successful_checks=successful_checks,
                        new_plans=new_plans,
                        errors=errors if errors else None,
                        execution_time=cycle_execution_time,
                    )

                    try:
                        logger.info("Checking Moodle activities", extra={"operation": "moodle_check"})
                        with sentry_sdk.start_span(op="moodle_check", description="Check Moodle activities"):
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

                                try:
                                    os.remove(saved_file)
                                    logger.debug(f"Removed temporary file", extra={"file_path": saved_file})
                                except Exception as e:
                                    logger.error(f"Error removing file: {str(e)}", extra={"file_path": saved_file})

                    except Exception as e:
                        error_info = {
                            "timestamp": datetime.now(),
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                            "type": "moodle_activity_error"
                        }
                        db.errors.insert_one(error_info)
                        sentry_sdk.capture_exception(e)
                        logger.error(f"Error processing Moodle activities: {str(e)}", extra={"operation": "moodle_check"})

                logger.info(
                    f"All tasks completed. Waiting {check_interval} seconds before next cycle",
                    extra={"check_interval": check_interval}
                )
                time.sleep(check_interval)

        except KeyboardInterrupt:
            logger.warning("\nShutting down gracefully...")
        except Exception as e:
            logger.critical(f"Fatal error: {str(e)}")
            logger.error(traceback.format_exc())

    except Exception as e:
        logger.critical(f"An error occurred in main.py: {str(e)}")
        logger.error("Traceback:")
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
