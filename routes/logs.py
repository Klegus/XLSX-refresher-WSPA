from flask import jsonify
import pymongo
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.logs')

def init_log_routes(app, db):
    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        """Get check logs from MongoDB"""
        try:
            logger.debug("Fetching logs from database")
            logs = list(db.check_cycles.find(
                {},
                {
                    'timestamp': 1,
                    'successful_checks': 1,
                    'new_plans': 1,
                    'has_errors': 1,
                    'execution_time': 1,
                    'errors': 1,
                    '_id': 0
                }
            ).sort("timestamp", pymongo.DESCENDING).limit(100))
            
            for log in logs:
                if 'timestamp' in log:
                    log['timestamp'] = log['timestamp'].isoformat()
            
            logger.debug(f"Retrieved {len(logs)} log entries")
            return jsonify(logs)
        
        except Exception as e:
            logger.error(f"Error fetching logs: {str(e)}")
            return jsonify({
                "error": "Failed to fetch logs",
                "details": str(e)
            }), 500
