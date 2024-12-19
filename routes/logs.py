from flask import jsonify
import pymongo

def init_log_routes(app, db):
    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        """Get check logs from MongoDB"""
        try:
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
            
            return jsonify(logs)
        
        except Exception as e:
            print(f"Error fetching logs: {str(e)}")
            return jsonify({
                "error": "Failed to fetch logs",
                "details": str(e)
            }), 500
