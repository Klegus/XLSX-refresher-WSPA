from flask import jsonify, request
from werkzeug.exceptions import BadRequest
from datetime import datetime
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.activities')

def init_activity_routes(app, db):
    @app.route('/api/activities', methods=['GET'])
    def read_activities():
        try:
            logger.debug("Reading activities with query parameters")
            skip = max(0, int(request.args.get('skip', 0)))
            limit = min(50, int(request.args.get('limit', 20)))
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            
            query = {}
            if start_date or end_date:
                date_filter = {}
                
                if start_date:
                    try:
                        start_datetime = datetime.fromisoformat(start_date)
                        date_filter["$gte"] = start_datetime
                        logger.debug(f"Filtering activities from {start_datetime}")
                    except ValueError:
                        logger.warning(f"Invalid start_date format: {start_date}")
                        raise BadRequest("Invalid start_date format")
                
                if end_date:
                    try:
                        end_datetime = datetime.fromisoformat(end_date)
                        date_filter["$lte"] = end_datetime
                        logger.debug(f"Filtering activities until {end_datetime}")
                    except ValueError:
                        logger.warning(f"Invalid end_date format: {end_date}")
                        raise BadRequest("Invalid end_date format")
                
                if date_filter:
                    query["created_at"] = date_filter

            activities = list(db.Activities.find(
                query
            ).sort([
                ("position", -1),
                ("created_at", -1)
            ]).skip(skip).limit(limit))

            logger.debug(f"Found {len(activities)} activities")

            processed_activities = []
            for activity in activities:
                processed_activity = {
                    "id": str(activity["_id"]),
                    "resource_id": activity.get("id"),
                    "type": activity.get("type"),
                    "title": activity.get("title"),
                    "url": activity.get("url"),
                    "sequence_number": activity.get("sequence_number"),
                    "created_at": activity.get("created_at"),
                    "position": activity.get("position"),
                    "checksum": activity.get("checksum"),
                    "content": activity.get("content", ""),
                    "images": activity.get("content", {}).get("images", []) if isinstance(activity.get("content"), dict) else []
                }
                processed_activities.append(processed_activity)

            total_count = db.Activities.count_documents(query)
            response = {
                "total": total_count,
                "activities": processed_activities
            }

            logger.debug(f"Returning {len(processed_activities)} of {total_count} total activities")
            return jsonify(response)

        except BadRequest as e:
            logger.warning(f"Bad request: {str(e)}")
            return jsonify({"detail": str(e)}), 400
        except Exception as e:
            logger.error(f"Error retrieving activities: {str(e)}")
            return jsonify({"detail": str(e)}), 500
