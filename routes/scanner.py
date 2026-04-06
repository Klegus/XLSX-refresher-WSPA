"""Routes for Moodle plan scanner - scrape, parse, compare, apply."""
import threading
from datetime import datetime
from flask import jsonify, request
from shared_utils import get_logger

logger = get_logger('routes.scanner')

# Module-level scan state
_scan_state = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "progress": "",
    "result": None,
    "error": None
}


def init_scanner_routes(app, get_plans_config, update_plans_config):

    @app.route("/api/scanner/start", methods=["POST"])
    def start_scan():
        if _scan_state["status"] == "running":
            return jsonify({"success": False, "error": "Skan już trwa"}), 409

        _scan_state["status"] = "running"
        _scan_state["started_at"] = datetime.now().isoformat()
        _scan_state["finished_at"] = None
        _scan_state["progress"] = "Uruchamianie..."
        _scan_state["result"] = None
        _scan_state["error"] = None

        def run_scan():
            try:
                from moodle_scanner import run_full_scan, compare_plans

                def progress_cb(msg):
                    _scan_state["progress"] = msg
                    logger.info(f"Scan progress: {msg}")

                scraped_plans, year_label, semester = run_full_scan(progress_cb)

                progress_cb("Porównywanie z aktualnymi planami...")
                current_plans = get_plans_config() or {}
                diff = compare_plans(scraped_plans, current_plans)

                _scan_state["result"] = {
                    "year": year_label,
                    "semester": semester,
                    "total_scraped": len(scraped_plans),
                    "total_current": len(current_plans),
                    "new": diff["new"],
                    "changed": diff["changed"],
                    "removed": diff["removed"],
                    "scraped_plans": scraped_plans,
                }
                _scan_state["status"] = "done"
                _scan_state["finished_at"] = datetime.now().isoformat()
                _scan_state["progress"] = (
                    f"Gotowe: {len(diff['new'])} nowych, "
                    f"{len(diff['changed'])} zmienionych, "
                    f"{len(diff['removed'])} usuniętych"
                )
                logger.info(f"Scan completed: {_scan_state['progress']}")

            except Exception as e:
                import traceback
                _scan_state["status"] = "error"
                _scan_state["error"] = str(e)
                _scan_state["finished_at"] = datetime.now().isoformat()
                _scan_state["progress"] = f"Błąd: {e}"
                logger.error(f"Scan failed: {traceback.format_exc()}")

        threading.Thread(target=run_scan, daemon=True).start()
        return jsonify({"success": True, "message": "Skan uruchomiony"}), 202

    @app.route("/api/scanner/status", methods=["GET"])
    def scan_status():
        result_summary = None
        if _scan_state["result"]:
            r = _scan_state["result"]
            result_summary = {
                "year": r["year"],
                "semester": r["semester"],
                "total_scraped": r["total_scraped"],
                "total_current": r["total_current"],
                "new_count": len(r["new"]),
                "changed_count": len(r["changed"]),
                "removed_count": len(r["removed"]),
                "new": {k: v.get("name", k) for k, v in r["new"].items()},
                "changed": {k: v["diff_fields"] for k, v in r["changed"].items()},
                "removed": {k: v.get("name", k) for k, v in r["removed"].items()},
            }

        return jsonify({
            "status": _scan_state["status"],
            "started_at": _scan_state["started_at"],
            "finished_at": _scan_state["finished_at"],
            "progress": _scan_state["progress"],
            "result": result_summary,
            "error": _scan_state["error"],
        })

    @app.route("/api/scanner/apply", methods=["POST"])
    def apply_scan():
        if _scan_state["status"] != "done" or not _scan_state["result"]:
            return jsonify({"success": False, "error": "Brak wyników skanu do zastosowania"}), 400

        data = request.json or {}
        keys_to_add = data.get("add", [])
        keys_to_update = data.get("update", [])
        keys_to_remove = data.get("remove", [])

        scan_result = _scan_state["result"]
        current_plans = get_plans_config() or {}
        added, updated, removed = 0, 0, 0

        # Add new plans
        for key in keys_to_add:
            if key in scan_result["new"]:
                current_plans[key] = scan_result["new"][key]
                added += 1

        # Update changed plans
        for key in keys_to_update:
            if key in scan_result["changed"]:
                current_plans[key] = scan_result["changed"][key]["new"]
                updated += 1

        # Also accept plans from scraped_plans (for add/update)
        scraped = scan_result.get("scraped_plans", {})
        for key in keys_to_add:
            if key not in scan_result["new"] and key in scraped:
                current_plans[key] = scraped[key]
                added += 1
        for key in keys_to_update:
            if key not in scan_result["changed"] and key in scraped:
                current_plans[key] = scraped[key]
                updated += 1

        # Remove plans
        for key in keys_to_remove:
            if key in current_plans:
                del current_plans[key]
                removed += 1

        if added + updated + removed > 0:
            update_plans_config(current_plans)
            logger.info(f"Applied scan: +{added} ~{updated} -{removed}")

        return jsonify({
            "success": True,
            "message": f"Zastosowano: {added} dodanych, {updated} zaktualizowanych, {removed} usuniętych",
            "applied": {"added": added, "updated": updated, "removed": removed}
        })
