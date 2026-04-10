from flask import jsonify, request
import pymongo
from shared_utils import get_logger

logger = get_logger('routes.logs')


def init_log_routes(app, db):
    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        """Get check logs from MongoDB. By default only shows logs with changes or errors."""
        try:
            show_all = request.args.get('all', 'false').lower() == 'true'
            limit = int(request.args.get('limit', 100))

            query = {}
            if not show_all:
                # Only show interesting logs: new plans or errors
                query = {"$or": [
                    {"new_plans": {"$gt": 0}},
                    {"has_errors": True},
                ]}

            logs = list(db.check_cycles.find(
                query,
                {
                    'timestamp': 1,
                    'successful_checks': 1,
                    'new_plans': 1,
                    'has_errors': 1,
                    'execution_time': 1,
                    'errors': 1,
                    'updated_plans': 1,
                    '_id': 0
                }
            ).sort("timestamp", pymongo.DESCENDING).limit(limit))

            for log in logs:
                if 'timestamp' in log:
                    log['timestamp'] = log['timestamp'].isoformat()

            return jsonify(logs)

        except Exception as e:
            logger.error(f"Error fetching logs: {str(e)}")
            return jsonify({"error": "Failed to fetch logs"}), 500
