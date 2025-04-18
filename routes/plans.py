from flask import jsonify
from typing import Optional
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.plans')

def init_plan_routes(app, get_semester_collections, db):
    @app.route('/api/faculties/<category>', methods=['GET'])
    def get_faculties(category: str):
        """
        Returns a list of unique faculties for a given category
        """
        try:
            logger.debug(f"Getting faculties for category: {category}")
            collections_data = get_semester_collections()
            faculties = sorted(list(set(
                data["faculty"] 
                for data in collections_data.values()
                if data["category"] == category
            )))
            logger.debug(f"Found {len(faculties)} faculties for category {category}")
            return jsonify({"faculties": faculties})
        except Exception as e:
            logger.error(f"Error getting faculties for category {category}: {str(e)}")
            return jsonify({"detail": str(e)}), 500

    @app.route('/api/plans/<category>/<faculty>', methods=['GET'])
    def get_plans(category: str, faculty: str):
        """
        Returns a list of plans for a given category and faculty
        """
        try:
            logger.debug(f"Getting plans for category: {category}, faculty: {faculty}")
            collections_data = get_semester_collections()
            plans = [
                {
                    "id": collection_name,
                    "name": data["plan_name"],
                    "groups": data["groups"]
                }
                for collection_name, data in collections_data.items()
                if data["category"] == category and data["faculty"] == faculty
            ]
            logger.debug(f"Found {len(plans)} plans for category {category} and faculty {faculty}")
            return jsonify({"plans": plans})
        except Exception as e:
            logger.error(f"Error getting plans for category {category}, faculty {faculty}: {str(e)}")
            return jsonify({"detail": str(e)}), 500

    @app.route('/api/plan/<collection_name>', methods=['GET'])
    @app.route('/api/plan/<collection_name>/<group_name>', methods=['GET'])
    def get_plan(collection_name: str, group_name: Optional[str] = None):
        try:
            logger.info(f"Pobieranie planu dla kolekcji: {collection_name}, grupy: {group_name}")
            latest_plan = db[collection_name].find_one(sort=[("timestamp", -1)])
            
            if not latest_plan:
                logger.warning("Nie znaleziono planu w kolekcji")
                return jsonify({
                    "detail": "Plan not found"
                }), 404
                
            if group_name is not None:
                if group_name not in latest_plan["groups"]:
                    logger.warning(f"Nie znaleziono grupy {group_name} w planie")
                    available_groups = list(latest_plan["groups"].keys())
                    logger.info(f"Dostępne grupy: {available_groups}")
                    return jsonify({
                        "detail": {
                            "message": "Nie znaleziono wybranej grupy",
                            "requested_group": group_name,
                            "available_groups": available_groups
                        }
                    }), 404
            
            if "category" not in latest_plan and "groups" not in latest_plan:
                response = {
                    "plan_name": latest_plan["plan_name"],
                    "timestamp": latest_plan["timestamp"],
                    "url": latest_plan.get("url", ""),
                    "category": None,
                    "groups": None
                }
            else:
                plan_html = latest_plan["groups"][group_name].replace('\n', ' ') if group_name else latest_plan.get("plan_html", "")
                response = {
                    "plan_name": latest_plan["plan_name"],
                    "group_name": group_name,
                    "plan_html": plan_html,
                    "timestamp": latest_plan["timestamp"],
                    "category": latest_plan.get("category", "st"),
                    "url": latest_plan.get("url", "")
                }
            logger.debug("Wysyłanie odpowiedzi")
            return jsonify(response)
        except Exception as e:
            logger.error(f"Error retrieving plan {collection_name}, group {group_name}: {str(e)}")
            return jsonify({"detail": str(e)}), 500
