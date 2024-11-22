from flask import Flask, jsonify
from main import db, app
from werkzeug.exceptions import InternalServerError

@app.route('/panel/api/comparisons/<collection_name>/<group_name>', methods=['GET'])
def get_comparisons(collection_name: str, group_name: str):
    """
    Retrieves plan comparisons for a specific collection and group
    """
    try:
        # Fetch comparisons for the specific plan and group
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
            return jsonify([])  # Return empty list if no comparisons found
            
        # Convert ObjectId to string for JSON serialization
        for comparison in comparisons:
            comparison['_id'] = str(comparison['_id'])
        
        return jsonify(comparisons)
    except Exception as e:
        # Log the error if needed
        print(f"Error in get_comparisons: {str(e)}")
        return jsonify([])  # Return empty list on error

# Error handler for 500 errors
@app.errorhandler(500)
def internal_error(error):
    return jsonify({"detail": "Internal server error"}), 500
