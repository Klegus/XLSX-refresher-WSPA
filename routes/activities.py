from flask import jsonify, request
from werkzeug.exceptions import BadRequest
from datetime import datetime

def init_activity_routes(app, db):
    @app.route('/api/activities', methods=['GET'])
    def read_activities():
        try:
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
                    except ValueError:
                        raise BadRequest("Invalid start_date format")
                
                if end_date:
                    try:
                        end_datetime = datetime.fromisoformat(end_date)
                        date_filter["$lte"] = end_datetime
                    except ValueError:
                        raise BadRequest("Invalid end_date format")
                
                if date_filter:
                    query["created_at"] = date_filter

            activities = list(db.Activities.find(
                query
            ).sort([
                ("position", -1),
                ("created_at", -1)
            ]).skip(skip).limit(limit))

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

            response = {
                "total": db.Activities.count_documents(query),
                "activities": processed_activities
            }

            return jsonify(response)

        except BadRequest as e:
            return jsonify({"detail": str(e)}), 400
        except Exception as e:
            return jsonify({"detail": str(e)}), 500
