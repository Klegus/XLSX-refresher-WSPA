from flask import jsonify
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.comparisons')

def init_comparison_routes(app, db):
    @app.route('/api/comparisons/<collection_name>/<group_name>', methods=['GET'])
    def get_comparisons(collection_name: str, group_name: str):
        """
        Retrieves plan comparisons for a specific collection and group
        """
        try:
            logger.debug(f"Fetching comparisons for collection: {collection_name}, group: {group_name}")
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
                logger.debug(f"No comparisons found for collection: {collection_name}, group: {group_name}")
                return jsonify([])
                
            for comparison in comparisons:
                comparison['_id'] = str(comparison['_id'])
            
            logger.debug(f"Retrieved {len(comparisons)} comparisons")
            return jsonify(comparisons)
        except Exception as e:
            logger.error(f"Error in get_comparisons: {str(e)}")
            return jsonify([])
