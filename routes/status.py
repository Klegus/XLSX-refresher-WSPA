from flask import jsonify
import time
from datetime import datetime
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.status')

def init_status_routes(app, status_checker, get_system_config):
    @app.route('/api/status', methods=['GET'])
    def get_status():
        logger.debug('Status API called')
        status_checker.update_activity()
        config = get_system_config()
        
        status_data = {
            "active": status_checker.is_active(),
            "last_activity": status_checker.get_last_activity_datetime(),
            "check_interval": status_checker.get_check_interval(),
            "maintenance_mode": config.get("maintenance_mode", False),
            "maintenance_reason": config.get("maintenance_reason", ""),
        }
        
        if "last_check_stats" in config:
            status_data["last_check"] = config["last_check_stats"]
        
        logger.debug(f"Returning status: active={status_data['active']}, maintenance={status_data['maintenance_mode']}")
        return jsonify(status_data)
