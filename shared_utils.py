from datetime import datetime
from pymongo import MongoClient
import os
import boto3
import watchtower
import logging
from logging.handlers import RotatingFileHandler
import sys
# Setup logging
class ColoredStructuredFormatter(logging.Formatter):
    """Custom formatter with colors AND structured fields for console output"""
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'

    STANDARD_ATTRS = {
        'name', 'msg', 'args', 'created', 'filename', 'funcName', 'levelname',
        'levelno', 'lineno', 'module', 'msecs', 'message', 'pathname', 'process',
        'processName', 'relativeCreated', 'thread', 'threadName', 'exc_info',
        'exc_text', 'stack_info', 'taskName', 'asctime'
    }

    def format(self, record):
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{self.BOLD}{levelname}{self.RESET}"

        result = super().format(record)

        context_parts = []
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_ATTRS:
                if isinstance(value, (list, dict)):
                    context_parts.append(f"{key}={value}")
                else:
                    context_parts.append(f"{key}={value}")

        if context_parts:
            result = f"{result} [{', '.join(context_parts)}]"

        return result


class StructuredFormatter(logging.Formatter):
    """Formatter that includes structured context from extra fields"""

    STANDARD_ATTRS = {
        'name', 'msg', 'args', 'created', 'filename', 'funcName', 'levelname',
        'levelno', 'lineno', 'module', 'msecs', 'message', 'pathname', 'process',
        'processName', 'relativeCreated', 'thread', 'threadName', 'exc_info',
        'exc_text', 'stack_info', 'taskName', 'asctime'
    }

    def format(self, record):
        result = super().format(record)

        context_parts = []
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_ATTRS:
                if isinstance(value, (list, dict)):
                    context_parts.append(f"{key}={value}")
                else:
                    context_parts.append(f"{key}={value}")

        if context_parts:
            result = f"{result} [{', '.join(context_parts)}]"

        return result


def configure_root_logger(log_level=logging.INFO):
    """Configure the root logger with colors and structured logging"""
    root_logger = logging.getLogger()

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    root_logger.setLevel(log_level)

    logging.getLogger('pymongo').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('selenium').setLevel(logging.WARNING)

    console_format = '%(asctime)s - %(name)-20s - %(levelname)-8s - %(message)s'
    file_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    if sys.stdout.isatty():
        colored_formatter = ColoredStructuredFormatter(console_format, datefmt='%H:%M:%S')
        console_handler.setFormatter(colored_formatter)
    else:
        plain_formatter = StructuredFormatter(console_format, datefmt='%H:%M:%S')
        console_handler.setFormatter(plain_formatter)

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
        cloudwatch_formatter = StructuredFormatter(
            file_format,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        cloudwatch_handler.setFormatter(cloudwatch_formatter)
        root_logger.addHandler(cloudwatch_handler)
    
    if os.getenv("LOG_TO_FILE", "true").lower() == "true":
        log_dir = os.getenv("LOG_DIR", "logs")
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, "backend.log")

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)

        file_formatter = StructuredFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    return root_logger

def get_logger(name):
    """Get a logger with the given name"""
    logger = logging.getLogger(name)
    return logger


def log_cycle_summary(duration, plans_checked, total_plans, changes_detected, errors_count, next_check_time=None):
    """Print a beautiful summary of check cycle"""
    logger = get_logger('CycleSummary')

    summary_lines = [
        "",
        "╔" + "═" * 60 + "╗",
        "║" + " Check Cycle Summary ".center(60) + "║",
        "╠" + "═" * 60 + "╣",
        f"║ Duration: {duration:.1f}s".ljust(61) + "║",
        f"║ Plans checked: {plans_checked}/{total_plans}".ljust(61) + "║",
        f"║ Changes detected: {changes_detected}".ljust(61) + "║",
        f"║ Errors: {errors_count}".ljust(61) + "║",
    ]

    if next_check_time:
        summary_lines.append(f"║ Next check: {next_check_time}".ljust(61) + "║")

    summary_lines.append("╚" + "═" * 60 + "╝")

    for line in summary_lines:
        logger.info(line)


def log_plan_header(plan_name, plan_id, operation="check"):
    """Print a header for plan processing"""
    logger = get_logger('PlanProcessor')
    logger.info("")
    logger.info("─" * 70)
    logger.info(f"  {operation.upper()}: {plan_name} (ID: {plan_id})")
    logger.info("─" * 70)
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

                # Convert mixed to boolean (handle string "true"/"false" from MongoDB)
                mixed_value = latest_plan.get("mixed", False)
                if isinstance(mixed_value, str):
                    mixed_bool = mixed_value.lower() == "true"
                else:
                    mixed_bool = bool(mixed_value)

                collections_data[collection_name] = {
                    "plan_name": latest_plan["plan_name"],
                    "groups": latest_plan["groups"],
                    "timestamp": latest_plan["timestamp"],
                    "category": category,
                    "faculty": faculty,
                    "mixed": mixed_bool,
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
