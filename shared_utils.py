from datetime import datetime
from zoneinfo import ZoneInfo
from pymongo import MongoClient
import os
import time

# The whole system runs on Warsaw time regardless of the host/container TZ:
# night pause, scan windows and every timestamp stored in MongoDB are local.
WARSAW = ZoneInfo("Europe/Warsaw")
os.environ["TZ"] = "Europe/Warsaw"
if hasattr(time, "tzset"):
    time.tzset()


def to_iso(value):
    """Datetime -> ISO string with Warsaw offset, so browsers never guess the zone."""
    if value is None or not hasattr(value, "isoformat"):
        return value
    if getattr(value, "tzinfo", None) is None and hasattr(value, "hour"):
        value = value.replace(tzinfo=WARSAW)
    return value.isoformat()
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

# ─── Safe downloads from the university platform ──────────────────────────
# Download URLs come from the plans config (admin panel, scanner): only HTTPS
# on the allowed hosts is fetched, redirects are checked hop by hop and the
# body size is capped, so a bad config entry cannot turn the backend into a
# proxy to internal addresses (SSRF) or fill the disk.
ALLOWED_DOWNLOAD_HOSTS = {h.strip().lower() for h in os.getenv("ALLOWED_DOWNLOAD_HOSTS", "puw.wspa.pl").split(",")
                          if h.strip()}
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "25")) * 1024 * 1024
MAX_REDIRECTS = 5


class UnsafeDownload(Exception):
    pass


def check_download_url(url):
    from urllib.parse import urlparse
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_DOWNLOAD_HOSTS:
        raise UnsafeDownload(f"Download URL not allowed: {parsed.scheme}://{host}")


def fetch_bytes(session, url, timeout=60, require_xlsx=False):
    """GET url with the (logged-in) session and return the body as bytes.
    Raises UnsafeDownload for disallowed hosts, oversized bodies or non-XLSX files
    and requests.HTTPError for error statuses."""
    from urllib.parse import urljoin
    for _ in range(MAX_REDIRECTS + 1):
        check_download_url(url)
        resp = session.get(url, timeout=timeout, stream=True, allow_redirects=False)
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get("Location", ""))
            resp.close()
            continue
        try:
            resp.raise_for_status()
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > MAX_DOWNLOAD_BYTES:
                raise UnsafeDownload(f"File too large: {declared} bytes")
            chunks, size = [], 0
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise UnsafeDownload(f"File larger than {MAX_DOWNLOAD_BYTES} bytes")
                chunks.append(chunk)
        finally:
            resp.close()
        data = b"".join(chunks)
        if require_xlsx and data[:4] != b"PK\x03\x04":
            raise UnsafeDownload("Downloaded file is not an XLSX (ZIP) file")
        return data
    raise UnsafeDownload("Too many redirects")


def plan_collection_name(plan_config):
    """MongoDB collection that stores the versions of one configured plan."""
    faculty = plan_config.get('faculty', '').replace(' ', '-').replace('_', '-')
    return f"plans_{faculty}_{plan_config.get('name', '').lower().replace(' ', '_')}"


def current_plan_collections(db):
    """Names of plan collections in the current configuration - used as an
    allow-list for collection names that arrive in request URLs."""
    config = db.plans_config.find_one({"_id": "plans_json"}, {"plans": 1}) or {}
    return {plan_collection_name(p) for p in (config.get("plans") or {}).values()}


def get_semester_collections():
    """
    Pobiera listę wszystkich kolekcji planów i ich najnowsze dokumenty.
    Zwraca słownik z nazwami kolekcji i odpowiadającymi im informacjami.
    """
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client[os.getenv("MONGO_DB")]
    # Only plans in the current configuration - collections of removed plans
    # (e.g. last semester) stay in the database as history but are not listed
    config = db.plans_config.find_one({"_id": "plans_json"}) or {}
    current = {
        f"plans_{p.get('faculty', '').replace(' ', '-').replace('_', '-')}_{p.get('name', '').lower().replace(' ', '_')}"
        for p in (config.get("plans") or {}).values()
    }
    collections_data = {}
    for collection_name in db.list_collection_names():
        if current and collection_name not in current:
            continue
        if collection_name.startswith("plans_"):
            # Pobierz najnowszy dokument z kolekcji
            latest_plan = db[collection_name].find_one(sort=[("timestamp", -1)])
            if latest_plan and "plan_name" in latest_plan and "groups" in latest_plan:
                faculty = extract_faculty_from_collection(collection_name)
                category = plan_mode_category(latest_plan["plan_name"]) or determine_category(collection_name)

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

def plan_mode_category(plan_name: str):
    """Study mode from the plan's file name ("... - st I - ..." -> "st").

    The collection name also contains the hand-typed sheet name, which can say
    something else (sheet "ARU nst I" in the stacjonarne file), so the file-name
    convention wins; None when the name does not follow it.
    """
    from plan_naming import describe_plan
    mode = describe_plan(plan_name).get("mode")
    return {"st": "st", "nst": "nst", "nst puw": "nst_puw"}.get(mode)


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
