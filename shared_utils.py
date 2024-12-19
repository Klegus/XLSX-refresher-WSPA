from datetime import datetime
from pymongo import MongoClient
import os

def get_system_config():
    """Get system configuration from MongoDB"""
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client[os.getenv("MONGO_DB")]
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

def get_semester_collections():
    """
    Pobiera listę wszystkich kolekcji planów i ich najnowsze dokumenty.
    Zwraca słownik z nazwami kolekcji i odpowiadającymi im informacjami.
    """
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client[os.getenv("MONGO_DB")]
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
