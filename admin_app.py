"""
Separate admin panel application.
Runs on a different port (ADMIN_PORT, default 5006) from the main API.
Shares MongoDB connection with the main app.
"""
import os
import threading
from html import escape
from flask import Flask, Response, jsonify, request
from pymongo import MongoClient
from dotenv import load_dotenv
from shared_utils import get_logger, get_system_config, to_iso
from admin_auth import init_admin_auth, csrf_token

load_dotenv()

logger = get_logger('AdminApp')

admin_app = Flask(__name__)

# MongoDB connection (shared DB)
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
mongo_db_name = os.getenv("MONGO_DB", "Lesson_dev")
admin_mongo_client = MongoClient(mongo_uri)
admin_db = admin_mongo_client[mongo_db_name]

# Login, CSRF, security headers and audit log for every route below
init_admin_auth(admin_app, admin_db)


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
    with open("templates/panel.html", encoding="utf-8") as f:
        page = f.read().replace("{{CSRF_TOKEN}}", escape(csrf_token()))
    return Response(page, mimetype="text/html")


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
init_scanner_routes(admin_app, get_admin_plans_config, update_admin_plans_config,
                    request_check_now=lambda: request_check_now())
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
def request_check_now():
    """Ask the backend loop (main.py) to start a check cycle right away.

    The backend polls system_config.force_check_requested while waiting
    for the next cycle, so no direct HTTP call is needed.
    """
    from datetime import datetime
    update_admin_system_config({
        "maintenance_mode": False,
        "maintenance_reason": "",
        "force_check_requested": datetime.now(),
    })


@admin_app.route("/api/force-check", methods=["POST"])
def force_check():
    try:
        cycle = get_admin_system_config().get("cycle") or {}
        request_check_now()
        if cycle.get("running"):
            message = "Cykl już trwa. Kolejny wystartuje zaraz po nim."
        else:
            message = "Sprawdzanie wystartuje w ciągu kilku sekund."
        return jsonify({"success": True, "message": message})
    except Exception as e:
        logger.error(f"Force check failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Dashboard overview ───────────────────────────────
def _plan_collection_name(plan_config):
    # Must match LessonPlan.collection_name
    faculty_name = plan_config.get("faculty", "").replace(" ", "-").replace("_", "-")
    return f"plans_{faculty_name}_{plan_config.get('name', '').lower().replace(' ', '_')}"


@admin_app.route("/api/overview")
def overview():
    """Everything the dashboard needs in one call."""
    import pymongo
    from routes.scanner import get_scan_summary

    config = get_admin_system_config()
    plans = get_admin_plans_config()

    from shared_utils import to_iso as iso

    from plan_reconciler import get_blocks
    from plan_naming import describe_plan
    blocks = get_blocks(admin_db)
    validations = {v["_id"]: v.get("status") for v in admin_db.plan_validation.find({}, {"status": 1})}

    existing = set(admin_db.list_collection_names())
    plan_rows = []
    for plan_id, plan in plans.items():
        col = _plan_collection_name(plan)
        latest = None
        if col in existing:
            latest = admin_db[col].find_one(
                {"groups": {"$exists": True}},
                {"timestamp": 1, "groups": 1},
                sort=[("timestamp", pymongo.DESCENDING)],
            )
        plan_rows.append({
            "id": plan_id,
            "name": plan.get("name", plan_id),
            "display_name": describe_plan(plan.get("name", plan_id))["display_name"],
            "faculty": plan.get("faculty", ""),
            "category": plan.get("category", ""),
            "download_url": plan.get("download_url", ""),
            "groups_config": len(plan.get("groups", {}) or {}),
            "downloaded_at": iso(latest.get("timestamp")) if latest else None,
            "groups_downloaded": len(latest.get("groups") or {}) if latest else 0,
            "validation": validations.get(plan_id),
            "blocked": ("plan" if blocks[col]["plan"] else len(blocks[col]["groups"])) if col in blocks else None,
        })

    last_cycles = list(admin_db.check_cycles.find(
        {"successful_checks": {"$exists": True}},
        {"_id": 0, "timestamp": 1, "successful_checks": 1, "new_plans": 1,
         "has_errors": 1, "execution_time": 1, "updated_plans": 1},
    ).sort("timestamp", pymongo.DESCENDING).limit(5))

    cycle = {k: iso(v) for k, v in (config.get("cycle") or {}).items()}
    for c in last_cycles:
        c["timestamp"] = iso(c.get("timestamp"))

    # Coverage: which years of each programme have a plan (gaps = not published yet)
    typical_years = {"I stopnia": 3, "II stopnia": 2, "jednolite magisterskie": 5}
    coverage = {}
    for plan in plans.values():
        info = describe_plan(plan.get("name", ""))
        if not info["parsed"]:
            continue
        key = (info["faculty"], info["degree"], info["mode"])
        row = coverage.setdefault(key, {"faculty": info["faculty"], "degree": info["degree"],
                                        "mode": info["mode"], "mode_label": info["mode_label"], "years": {}})
        row["years"].setdefault(info["year"], 0)
        row["years"][info["year"]] += 1
    coverage_rows = []
    for row in sorted(coverage.values(), key=lambda r: (r["faculty"], r["degree"], r["mode"])):
        max_year = max(max(row["years"]), typical_years.get(row["degree"], 3))
        row["years"] = [{"year": y, "plans": row["years"].get(y, 0)} for y in range(1, max_year + 1)]
        coverage_rows.append(row)

    auto_scan = {k: iso(v) for k, v in (config.get("auto_scan") or {}).items()}

    return jsonify({
        "now": iso(__import__("datetime").datetime.now()),
        "coverage": coverage_rows,
        "auto_scan": auto_scan,
        "auto_scan_hours": config.get("auto_scan_hours", 24),
        "maintenance_mode": config.get("maintenance_mode", False),
        "maintenance_reason": config.get("maintenance_reason", ""),
        "check_interval": config.get("check_interval", 900),
        "force_check_pending": bool(config.get("force_check_requested")),
        "cycle": cycle,
        "plans": plan_rows,
        "last_cycles": last_cycles,
        "scan": get_scan_summary(),
    })


@admin_app.route("/api/validate", methods=["GET"])
def validate_plans():
    """Run validation on all plans."""
    try:
        from plan_validator import validate_all_plans
        results = validate_all_plans(admin_db)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Source validation (reconciliation with Excel) ────
_deep_state = {"status": "idle", "progress": None, "done": 0, "total": 0,
               "started_at": None, "finished_at": None, "error": None}


@admin_app.route("/api/validate/deep", methods=["POST"])
def start_deep_validation():
    from datetime import datetime
    if _deep_state["status"] == "running":
        return jsonify({"success": False, "error": "Walidacja już trwa"}), 409
    plan_ids = (request.json or {}).get("plan_ids") if request.is_json else None
    _deep_state.update(status="running", progress="Logowanie do PUW...", done=0, total=0,
                       started_at=datetime.now().isoformat(), finished_at=None, error=None)

    def progress(i, total, name):
        _deep_state.update(done=i - 1, total=total, progress=name)

    def run():
        try:
            from plan_reconciler import run_validation
            run_validation(admin_db, plan_ids, progress)
            _deep_state.update(status="done", done=_deep_state["total"], progress=None)
        except Exception as e:
            logger.error(f"Deep validation failed: {e}")
            _deep_state.update(status="error", error=str(e))
        finally:
            _deep_state["finished_at"] = datetime.now().isoformat()

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"success": True}), 202


@admin_app.route("/api/validate/deep", methods=["GET"])
def deep_validation_results():
    import pymongo
    from plan_reconciler import KIND_LABELS, SEVERITY, get_blocks

    config = get_admin_system_config()
    blocks = get_blocks(admin_db)
    reports = []
    for v in admin_db.plan_validation.find().sort("plan", pymongo.ASCENDING):
        ack = v.get("ack") or {}
        acked = bool(ack.get("checksum")) and ack["checksum"] == v.get("checksum")
        block = blocks.get(v.get("collection"))
        reports.append({
            "plan_id": v["_id"],
            "plan": v.get("plan"),
            "collection": v.get("collection"),
            "status": v.get("status"),
            "coverage": v.get("coverage"),
            "counts": v.get("counts", {}),
            "issues": v.get("issues", []),
            "issues_total": v.get("issues_total", 0),
            "blocked_plan": bool(block and block["plan"]),
            "blocked_groups": sorted(block["groups"]) if block else [],
            "has_blocking": bool(v.get("blocked_plan") or v.get("blocked_groups")),
            "acked": acked,
            "ack_at": to_iso(ack.get("at")) if acked else None,
            "validated_at": to_iso(v.get("validated_at")),
        })
    return jsonify({
        "state": _deep_state,
        "blocking_enabled": config.get("validation_blocking", True),
        "kinds": {k: {"label": KIND_LABELS[k], "severity": SEVERITY[k]} for k in KIND_LABELS},
        "reports": reports,
    })


@admin_app.route("/api/validate/ack", methods=["POST"])
def ack_validation():
    """Admin accepts the current file version of a plan despite issues (or revokes it)."""
    from datetime import datetime
    data = request.get_json(silent=True) or {}
    plan_id = data.get("plan_id")
    if not isinstance(plan_id, str):  # never let a JSON object reach the query as an operator
        return jsonify({"success": False, "error": "plan_id must be a string"}), 400
    doc = admin_db.plan_validation.find_one({"_id": plan_id})
    if not doc:
        return jsonify({"success": False, "error": "Brak walidacji dla tego planu"}), 404
    if data.get("revoke"):
        admin_db.plan_validation.update_one({"_id": plan_id}, {"$unset": {"ack": ""}})
        return jsonify({"success": True, "message": "Zatwierdzenie cofnięte – plan znowu zablokowany"})
    admin_db.plan_validation.update_one(
        {"_id": plan_id},
        {"$set": {"ack": {"checksum": doc.get("checksum"), "at": datetime.now()}}})
    return jsonify({"success": True, "message": "Plan zatwierdzony do czasu zmiany pliku"})


@admin_app.route("/api/validate/blocking", methods=["POST"])
def set_validation_blocking():
    enabled = bool((request.json or {}).get("enabled", True))
    update_admin_system_config({"validation_blocking": enabled})
    return jsonify({"success": True, "enabled": enabled})


@admin_app.route("/api/exams/status", methods=["GET"])
def exams_status():
    doc = admin_db.exam_schedule.find_one({"_id": "current"}) or {}
    config = get_admin_system_config()
    return jsonify({
        "available": bool(doc),
        "academic_year": doc.get("academic_year"),
        "is_current_year": doc.get("is_current_year"),
        "available_years": doc.get("available_years", []),
        "forced_year": config.get("exam_schedule_year"),
        "fetched_at": to_iso(doc.get("fetched_at")),
        "entries": len(doc.get("entries", [])),
        "corrected": sum(1 for e in doc.get("entries", []) if e.get("corrections")),
        "requested_year": doc.get("requested_year"),
        "checked_at": to_iso(doc.get("checked_at")),
        "problems": doc.get("problems", []),
        "source_url": doc.get("source_url"),
    })


@admin_app.route("/api/exams/rows", methods=["GET"])
def exams_rows():
    """Every timetable row: raw values next to how they were interpreted."""
    from exam_schedule import ALL
    doc = admin_db.exam_schedule.find_one({"_id": "current"}) or {}
    labels = {"st": "stacjonarny", "nst": "niestacjonarny", "nst_puw": "PUW"}
    rows = []
    for e in doc.get("entries", []):
        rows.append({
            "sheet": e.get("sheet"), "row": e.get("row"), "date": e["date"], "subject": e["subject"],
            "raw": {"faculty": e.get("raw_faculty"), "year": e.get("raw_year"),
                    "mode": e.get("raw_mode"), "group": e.get("raw_group")},
            "parsed": {
                "faculty": "wszystkie" if e["programmes"] == ALL else ", ".join(
                    p["faculty"] + (f" ({p['degree']})" if p.get("degree") else "") for p in e["programmes"]),
                "year": "wszystkie" if e["years"] == ALL else ", ".join(map(str, e["years"])),
                "mode": "wszystkie" if e["modes"] == ALL else ", ".join(labels.get(m, m) for m in e["modes"]),
                "group": e.get("group") or "–",
            },
            "corrections": e.get("corrections", []),
        })
    return jsonify({"rows": rows})


@admin_app.route("/api/exams/refresh", methods=["POST"])
def exams_refresh():
    import requests as req
    from exam_schedule import refresh
    from moodle_scanner import login_puw
    data = request.json or {}
    if "year" in data:
        update_admin_system_config({"exam_schedule_year": data["year"] or None})
    session = req.Session()
    if not login_puw(session):
        return jsonify({"success": False, "error": "Login do PUW nieudany"}), 502
    try:
        doc = refresh(admin_db, session, get_admin_system_config().get("exam_schedule_year"))
        return jsonify({"success": True, "message": f"Terminarz {doc['academic_year']}: {len(doc['entries'])} wpisów"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
    from waitress import serve
    port = int(os.getenv("ADMIN_PORT", "5006"))
    logger.info(f"Starting admin panel on port {port}")
    serve(admin_app, host="0.0.0.0", port=port, threads=4, ident=None)


def start_admin_in_thread():
    """Start admin app in a background thread."""
    thread = threading.Thread(target=run_admin_app, daemon=True)
    thread.start()
    logger.info("Admin panel thread started")
    return thread
