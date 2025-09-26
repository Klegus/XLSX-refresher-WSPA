from datetime import datetime
from pymongo import MongoClient
import os,boto3, watchtower
import logging
# Setup logging
def configure_root_logger(log_level=logging.INFO):
    """Configure the root logger once"""
    # Skonfiguruj główny logger
    root_logger = logging.getLogger()
    
    # Usuń wszystkie istniejące handlery
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    root_logger.setLevel(log_level)
    
    # Formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # CloudWatch handler (tylko raz!)
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-1")
        )
        
        cloudwatch_handler = watchtower.CloudWatchLogHandler(
            log_group='planinf.pl-backend',
            stream_name='backend-logs',
            boto3_client=session.client('logs'),
            send_interval=10,
            max_batch_size=1000
        )
        cloudwatch_handler.setFormatter(formatter)
        root_logger.addHandler(cloudwatch_handler)
    
    # File handler
    if os.getenv("LOG_TO_FILE", "false").lower() == "true":
        log_dir = os.getenv("LOG_DIR", "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, f"app_{datetime.now().strftime('%Y%m%d')}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    return root_logger

def get_logger(name):
    """Get a logger with the given name"""
    logger = logging.getLogger(name)
    # logger.propagate = False  # Zapobiega propagacji logów - This prevents logs from reaching root handlers
    return logger
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
                    # Include mixed flag if it exists in the document
                    "mixed": latest_plan.get("mixed", False),
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

        # Handle hyphenated names by replacing hyphens with spaces
        # This is for display purposes in the UI
        if "-" in faculty:
            faculty = faculty.replace("-", " ")

        # Capitalize each word for display
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
