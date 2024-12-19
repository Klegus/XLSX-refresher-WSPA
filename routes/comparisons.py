from flask import jsonify
import custom_print
def init_comparison_routes(app, db):
    @app.route('/api/comparisons/<collection_name>/<group_name>', methods=['GET'])
    def get_comparisons(collection_name: str, group_name: str):
        """
        Retrieves plan comparisons for a specific collection and group
        """
        try:
            comparisons = list(db.plan_comparisons.find(
                {
                    "collection_name": collection_name,
                    f"results.{group_name}": {"$exists": True}
                },
                {
                    "timestamp": 1,
                    "newer_plan_timestamp": 1,
                    "older_plan_timestamp": 1,
                    "model_used": 1,
                    f"results.{group_name}": 1
                }
            ).sort("timestamp", -1))
            
            if not comparisons:
                return jsonify([])
                
            for comparison in comparisons:
                comparison['_id'] = str(comparison['_id'])
            
            return jsonify(comparisons)
        except Exception as e:
            print(f"Error in get_comparisons: {str(e)}")
            return jsonify([])
