from flask import jsonify, request
from werkzeug.exceptions import BadRequest
from datetime import datetime, timedelta
import hashlib
from bson.objectid import ObjectId
import requests
import os
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.suggestions')

def send_pushover_notification(content):
    """Send a notification to Pushover with the provided content"""
    try:
        pushover_key = os.getenv("PUSHOVER_KEY")
        if not pushover_key:
            logger.warning("PUSHOVER_KEY environment variable not set")
            return False
            
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": pushover_key,
                "user": os.getenv("PUSHOVER_USER", pushover_key),  # Using same key as user if not specified
                "message": f"Nowa sugestia: {content}"
            }
        )
        
        if response.status_code == 200:
            logger.info("Pushover notification sent successfully")
            return True
        else:
            logger.error(f"Failed to send Pushover notification: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending Pushover notification: {str(e)}")
        return False

def init_suggestion_routes(app, db):
    # Endpoint to add a new suggestion
    @app.route('/api/suggestions', methods=['POST'])
    def add_suggestion():
        try:
            # Get data from request
            data = request.get_json()
            if not data:
                logger.warning("No data provided in suggestion request")
                raise BadRequest("No data provided")
            
            # Check required fields
            if 'content' not in data:
                logger.warning("Content missing in suggestion request")
                raise BadRequest("Content is required")
            
            # Extract client IP and create device identifier
            client_ip = request.remote_addr
            user_agent = request.headers.get('User-Agent', '')
            device_id = hashlib.md5(f"{client_ip}:{user_agent}".encode()).hexdigest()
            
            # Check daily limit (5 suggestions per device per day)
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today + timedelta(days=1)
            
            daily_count = db.Suggestions.count_documents({
                "device_id": device_id,
                "created_at": {"$gte": today, "$lt": tomorrow}
            })
            
            if daily_count >= 5:
                logger.info(f"Daily suggestion limit reached for device: {device_id}")
                return jsonify({
                    "success": False,
                    "message": "Daily limit reached. You can submit up to 5 suggestions per day."
                }), 429
            
            # Create suggestion document
            suggestion = {
                "content": data['content'],
                "device_id": device_id,
                "created_at": datetime.now(),
                "status": "pending"  # pending, approved, rejected
            }
            
            # Insert into database
            result = db.Suggestions.insert_one(suggestion)
            logger.info(f"New suggestion added, id: {result.inserted_id}")
            
            # Send Pushover notification
            notification_sent = send_pushover_notification(data['content'])
            if notification_sent:
                logger.debug("Pushover notification sent for new suggestion")
            
            # Return success response
            return jsonify({
                "success": True,
                "message": "Suggestion submitted successfully",
                "id": str(result.inserted_id),
                "remaining_today": 5 - (daily_count + 1)
            })
            
        except BadRequest as e:
            logger.warning(f"Bad request in suggestion submission: {str(e)}")
            return jsonify({"success": False, "message": str(e)}), 400
        except Exception as e:
            logger.error(f"Error adding suggestion: {str(e)}")
            return jsonify({"success": False, "message": "An error occurred while processing your request"}), 500
    
    # Endpoint to get suggestions (admin only - should be protected)
    @app.route('/api/suggestions', methods=['GET'])
    def get_suggestions():
        try:
            # In a real app, you would add authentication here
            # For now, just demonstrating the structure
            logger.debug("Getting suggestions")
            
            # Get query parameters
            skip = max(0, int(request.args.get('skip', 0)))
            limit = min(50, int(request.args.get('limit', 20)))
            status = request.args.get('status')
            
            # Build query
            query = {}
            if status and status != 'all':
                query["status"] = status
                logger.debug(f"Filtering suggestions by status: {status}")
            
            # Get suggestions from database
            suggestions = list(db.Suggestions.find(
                query
            ).sort([
                ("created_at", -1)
            ]).skip(skip).limit(limit))
            
            # Process results
            processed_suggestions = []
            for suggestion in suggestions:
                processed_suggestion = {
                    "id": str(suggestion["_id"]),
                    "content": suggestion.get("content"),
                    "status": suggestion.get("status"),
                    "created_at": suggestion.get("created_at")
                }
                processed_suggestions.append(processed_suggestion)
            
            total_count = db.Suggestions.count_documents(query)
            response = {
                "total": total_count,
                "suggestions": processed_suggestions
            }
            
            logger.debug(f"Returning {len(processed_suggestions)} of {total_count} total suggestions")
            return jsonify(response)
            
        except BadRequest as e:
            logger.warning(f"Bad request in get suggestions: {str(e)}")
            return jsonify({"detail": str(e)}), 400
        except Exception as e:
            logger.error(f"Error getting suggestions: {str(e)}")
            return jsonify({"detail": str(e)}), 500
            
    # Endpoint to update a suggestion status (for approve/reject)
    @app.route('/api/suggestions/<suggestion_id>', methods=['PATCH'])
    def update_suggestion_status(suggestion_id):
        try:
            # In a real app, you would add authentication here
            logger.debug(f"Updating suggestion status for id: {suggestion_id}")
            
            data = request.get_json()
            if not data or 'status' not in data:
                logger.warning("Status field missing in update request")
                raise BadRequest("Status field is required")
                
            status = data['status']
            if status not in ['pending', 'approved', 'rejected']:
                logger.warning(f"Invalid status value: {status}")
                raise BadRequest("Invalid status value. Must be 'pending', 'approved', or 'rejected'")
            
            # Update the suggestion status
            result = db.Suggestions.update_one(
                {"_id": ObjectId(suggestion_id)}, 
                {"$set": {"status": status}}
            )
            
            if result.matched_count == 0:
                logger.warning(f"Suggestion not found with id: {suggestion_id}")
                return jsonify({
                    "success": False,
                    "message": "Suggestion not found"
                }), 404
                
            logger.info(f"Suggestion {suggestion_id} status updated to '{status}'")
            return jsonify({
                "success": True,
                "message": f"Suggestion status updated to '{status}'"
            })
            
        except BadRequest as e:
            logger.warning(f"Bad request in update suggestion: {str(e)}")
            return jsonify({"success": False, "message": str(e)}), 400
        except Exception as e:
            logger.error(f"Error updating suggestion status: {str(e)}")
            return jsonify({"success": False, "message": "An error occurred while processing your request"}), 500 