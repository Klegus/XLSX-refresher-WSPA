from flask import jsonify
from datetime import datetime

def init_status_routes(app, status_checker, get_system_config):
    @app.route("/api/status")
    def status():
        is_active = status_checker.is_active()
        last_check = status_checker.get_last_activity_datetime()
        config = get_system_config()
        
        response = {
            "status": "active" if is_active else "inactive",
            "last_check": last_check,
            "maintenance_mode": config.get("maintenance_mode", False),
            "check_interval": config.get("check_interval", 900)
        }
        
        if is_active:
            return jsonify(response), 200
        else:
            return jsonify(response), 503
