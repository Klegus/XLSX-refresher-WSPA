from flask import jsonify, request
from werkzeug.exceptions import BadRequest
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
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
            },
            timeout=10,
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

# Keyed hash: the stored device id cannot be reversed to an IP address by
# hashing the whole IPv4 space (pseudonymisation, GDPR art. 4(5))
_DEVICE_KEY = (os.getenv("DEVICE_ID_SECRET") or secrets.token_hex(32)).encode()


def _device_id():
    """Visitor identity for the daily limit - the real client IP comes from the proxy."""
    # Set by the frontend from the trusted proxy header (not client-controlled)
    client_ip = request.headers.get('X-Client-IP', '').strip()[:64] or request.remote_addr
    user_agent = request.headers.get('User-Agent', '')[:256]
    return hmac.new(_DEVICE_KEY, f"{client_ip}:{user_agent}".encode(), hashlib.sha256).hexdigest()


def _today_count(db, device_id):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.Suggestions.count_documents({
        "device_id": device_id,
        "created_at": {"$gte": today, "$lt": today + timedelta(days=1)}
    })


def init_suggestion_routes(app, db, public=False):
    """public=True registers only submitting (public API); the admin panel gets everything."""

    @app.route('/api/suggestions/count', methods=['GET'])
    def suggestion_count():
        remaining = max(0, 5 - _today_count(db, _device_id()))
        return jsonify({"success": True, "remaining_today": remaining})

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
            content = data.get('content') if isinstance(data, dict) else None
            if not isinstance(content, str):
                raise BadRequest("Content is required")
            # Plain text only: drop control characters, limit length
            content = ''.join(ch for ch in content if ch in '\n\t' or ch.isprintable()).strip()
            if not content or len(content) > 500:
                raise BadRequest("Content must be 1-500 characters")
            
            # Extract client IP and create device identifier
            device_id = _device_id()
            
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
                "content": content,
                "device_id": device_id,
                "created_at": datetime.now(),
                "status": "pending"  # pending, approved, rejected
            }
            
            # Insert into database
            result = db.Suggestions.insert_one(suggestion)
            logger.info(f"New suggestion added, id: {result.inserted_id}")
            
            # Send Pushover notification
            notification_sent = send_pushover_notification(content)
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
    
    if public:
        return

    # Endpoint to get suggestions (admin panel only)
    @app.route('/api/suggestions', methods=['GET'])
    def get_suggestions():
        try:
            # Admin panel only - protected by the panel's login (admin_auth)
            logger.debug("Getting suggestions")
            
            # Get query parameters
            skip = max(0, int(request.args.get('skip', 0)))
            limit = max(1, min(50, int(request.args.get('limit', 20))))
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
            return jsonify({"detail": "Wewnętrzny błąd serwera"}), 500
            
    # Endpoint to update a suggestion status (for approve/reject)
    @app.route('/api/suggestions/<suggestion_id>', methods=['PATCH'])
    def update_suggestion_status(suggestion_id):
        try:
            logger.debug(f"Updating suggestion status for id: {suggestion_id}")
            if not ObjectId.is_valid(suggestion_id):
                raise BadRequest("Invalid suggestion id")

            data = request.get_json(silent=True)
            if not isinstance(data, dict) or 'status' not in data:
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