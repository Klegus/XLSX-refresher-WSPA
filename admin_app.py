"""
Separate admin panel application.
Runs on a different port (ADMIN_PORT, default 5006) from the main API.
Shares MongoDB connection with the main app.
"""
import os
import threading
from flask import Flask, Response, jsonify, request
from pymongo import MongoClient
from dotenv import load_dotenv
from shared_utils import get_logger, get_system_config

load_dotenv()

logger = get_logger('AdminApp')

admin_app = Flask(__name__)

# MongoDB connection (shared DB)
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
mongo_db_name = os.getenv("MONGO_DB", "Lesson_dev")
admin_mongo_client = MongoClient(mongo_uri)
admin_db = admin_mongo_client[mongo_db_name]


def get_admin_system_config():
    config = admin_db.system_config.find_one({"_id": "config"})
    return config or {}


def get_admin_plans_config():
    config = admin_db.plans_config.find_one({"_id": "plans_json"})
    if config and "plans" in config:
        return config["plans"]
    return {}


def update_admin_system_config(updates):
    admin_db.system_config.update_one(
        {"_id": "config"},
        {"$set": updates},
        upsert=True
    )


def update_admin_plans_config(plans):
    from datetime import datetime
    return admin_db.plans_config.update_one(
        {"_id": "plans_json"},
        {"$set": {
            "plans": plans,
            "last_updated": datetime.now().isoformat(),
            "source": "admin_panel"
        }},
        upsert=True
    )


# ─── Admin panel page ─────────────────────────────────────
@admin_app.route("/")
@admin_app.route("/panel")
def show_panel():
    return Response(open("templates/panel.html").read(), mimetype="text/html")


# ─── Status (read-only, for panel display) ────────────────
@admin_app.route("/api/status")
def admin_status():
    config = get_admin_system_config()
    return jsonify({
        "active": not config.get("maintenance_mode", False),
        "check_interval": config.get("check_interval", 900),
        "last_activity": config.get("last_activity", ""),
        "last_check": config.get("last_check_stats", {}),
        "maintenance_mode": config.get("maintenance_mode", False),
        "maintenance_reason": config.get("maintenance_reason", ""),
    })


# ─── Collections endpoint ─────────────────────────────────
@admin_app.route("/api/collections")
def get_collections():
    from shared_utils import get_semester_collections
    collections = get_semester_collections()
    return jsonify(collections)


# ─── Init route modules ───────────────────────────────────
from routes.config import init_config_routes
from routes.logs import init_log_routes
from routes.scanner import init_scanner_routes
from routes.suggestions import init_suggestion_routes

# StatusChecker stub for admin (reads from DB)
class AdminStatusChecker:
    def __init__(self):
        pass
    def is_active(self):
        config = get_admin_system_config()
        return not config.get("maintenance_mode", False)
    def get_last_activity_datetime(self):
        config = get_admin_system_config()
        return config.get("last_activity", "")
    def get_check_interval(self):
        config = get_admin_system_config()
        return config.get("check_interval", 900)

admin_status_checker = AdminStatusChecker()

init_config_routes(
    admin_app,
    get_admin_system_config,
    get_admin_plans_config,
    update_admin_system_config,
    update_admin_plans_config,
    admin_db,
)
init_log_routes(admin_app, admin_db)
init_scanner_routes(admin_app, get_admin_plans_config, update_admin_plans_config)
init_suggestion_routes(admin_app, admin_db)


# ─── Purge database endpoint ───────────────────────────
@admin_app.route("/api/purge", methods=["POST"])
def purge_database():
    """Purge all plan data from MongoDB (for semester reset)."""
    try:
        data = request.json or {}
        purge_plans = data.get("plans", True)
        purge_activities = data.get("activities", True)
        purge_config = data.get("config", False)

        results = {}

        if purge_plans:
            # Drop all plan collections (start with "plans_")
            dropped = 0
            for col_name in admin_db.list_collection_names():
                if col_name.startswith("plans_"):
                    admin_db.drop_collection(col_name)
                    dropped += 1
            results["plan_collections_dropped"] = dropped

        if purge_activities:
            admin_db.Activities.drop()
            results["activities_dropped"] = True

        if purge_config:
            admin_db.plans_config.delete_one({"_id": "plans_json"})
            results["plans_config_cleared"] = True

        # Clear logs
        admin_db.check_cycles.drop()
        results["logs_cleared"] = True

        logger.info(f"Database purged: {results}")
        return jsonify({"success": True, "message": "Baza wyczyszczona", "details": results})

    except Exception as e:
        logger.error(f"Purge failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Force check cycle endpoint ───────────────────────
@admin_app.route("/api/force-check", methods=["POST"])
def force_check():
    """Trigger a force check on the main backend (via its API)."""
    try:
        # The main backend doesn't have a direct "force check" endpoint,
        # but we can set check_interval to 1 temporarily to trigger immediate check
        import requests as req
        backend_url = os.getenv("BACKEND_URL", "http://backend:5005")

        # Just reset maintenance mode to false to ensure checks run
        update_admin_system_config({"maintenance_mode": False, "maintenance_reason": ""})

        return jsonify({
            "success": True,
            "message": "Maintenance mode wyłączony. Backend sprawdzi plany w następnym cyklu."
        })
    except Exception as e:
        logger.error(f"Force check failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_app.route("/api/validate", methods=["GET"])
def validate_plans():
    """Run validation on all plans."""
    try:
        from plan_validator import validate_all_plans
        results = validate_all_plans(admin_db)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_app.route("/api/reprocess-invalid", methods=["POST"])
def reprocess_invalid():
    """Delete invalid plan documents so backend re-processes them on next cycle."""
    try:
        from plan_validator import validate_all_plans
        results = validate_all_plans(admin_db)

        cleared = 0
        cleared_names = []
        for detail in results.get('details', []):
            col_name = detail.get('collection')
            if col_name:
                admin_db[col_name].delete_many({})
                cleared += 1
                cleared_names.append(detail.get('plan', col_name))

        return jsonify({
            "success": True,
            "message": f"Wyczyszczono {cleared} kolekcji. Backend przetworzy je ponownie w następnym cyklu.",
            "cleared": cleared,
            "plans": cleared_names
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def run_admin_app():
    port = int(os.getenv("ADMIN_PORT", "5006"))
    logger.info(f"Starting admin panel on port {port}")
    admin_app.run(host="0.0.0.0", port=port)


def start_admin_in_thread():
    """Start admin app in a background thread."""
    thread = threading.Thread(target=run_admin_app, daemon=True)
    thread.start()
    logger.info("Admin panel thread started")
    return thread
