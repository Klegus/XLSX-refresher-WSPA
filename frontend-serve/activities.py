from fastapi import FastAPI, HTTPException
from typing import Optional
from datetime import datetime
from pymongo import MongoClient
from frontend-serve import app
from main import db
from flask import jsonify, request

@app.route('/api/activities', methods=['GET'])
def read_activities():
    try:
        # Get query parameters with defaults
        skip = max(0, int(request.args.get('skip', 0)))
        limit = min(50, int(request.args.get('limit', 20)))
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Prepare time filters
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

        # Fetch documents with pagination and sorting
        activities = list(db.Activities.find(
            query
        ).sort([
            ("position", -1),  # Primary sort by position descending
            ("created_at", -1)  # Secondary sort by creation date
        ]).skip(skip).limit(limit))

        # Process and structure the activities
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

        # Prepare the response
        response = {
            "total": db.Activities.count_documents(query),
            "activities": processed_activities
        }

        return jsonify(response)

    except BadRequest as e:
        return jsonify({"detail": str(e)}), 400
    except Exception as e:
        return jsonify({"detail": str(e)}), 500
