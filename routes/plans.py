from flask import jsonify, request
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

            # Get plans configuration from MongoDB to check for mixed flag
            plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
            plans_config = plans_config_doc.get("plans", {}) if plans_config_doc else {}

            plans = []
            for collection_name, data in collections_data.items():
                if data["category"] == category and data["faculty"] == faculty:
                    # Convert groups to just the keys if it's a dict with HTML values
                    groups = data["groups"]
                    if isinstance(groups, dict):
                        # If values are HTML strings (contain '<table'), just use keys
                        if any(isinstance(v, str) and '<table' in v for v in groups.values()):
                            groups = {k: k for k in groups.keys()}

                    plan_data = {
                        "id": collection_name,
                        "name": data["plan_name"],
                        "groups": groups,
                        # First try to get mixed flag directly from the data
                        "mixed": data.get("mixed", False)
                    }

                    # If mixed flag is not in data, try to find it in plans_config
                    if not data.get("mixed", False):
                        found_mixed = False
                        for plan_id, config in plans_config.items():
                            # Log what we're comparing
                            logger.debug(f"Comparing plan names: config[{plan_id}].name='{config.get('name')}' vs data.plan_name='{data['plan_name']}'")

                            if config.get("name") == data["plan_name"]:
                                plan_data["mixed"] = config.get("mixed", False)
                                logger.debug(f"Found matching plan {plan_id}, mixed={config.get('mixed', False)}")
                                found_mixed = True
                                break

                        if not found_mixed:
                            # Also try matching by plan_id with collection_name
                            for plan_id, config in plans_config.items():
                                if plan_id == collection_name or plan_id in collection_name or collection_name.endswith(plan_id):
                                    plan_data["mixed"] = config.get("mixed", False)
                                    logger.debug(f"Found plan by ID match: {plan_id}, mixed={config.get('mixed', False)}")
                                    found_mixed = True
                                    break

                        if not found_mixed:
                            logger.debug(f"No match found for plan {data['plan_name']}, using mixed={plan_data['mixed']}")

                    plans.append(plan_data)

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

    @app.route('/api/plan/<collection_name>/mixed', methods=['POST'])
    def get_mixed_plan(collection_name: str):
        """
        Returns HTML for multiple groups combined (for mixed plans)
        Request body: {"groups": ["group1", "group2", ...]}
        """
        try:
            data = request.get_json()
            if not data or "groups" not in data:
                return jsonify({"detail": "Groups list is required in request body"}), 400

            requested_groups = data["groups"]
            if not isinstance(requested_groups, list) or len(requested_groups) == 0:
                return jsonify({"detail": "Groups must be a non-empty list"}), 400

            logger.info(f"Getting mixed plan for collection: {collection_name}, groups: {requested_groups}")

            latest_plan = db[collection_name].find_one(sort=[("timestamp", -1)])

            if not latest_plan:
                logger.warning("No plan found in collection")
                return jsonify({"detail": "Plan not found"}), 404

            # Collect HTML for all requested groups
            group_htmls = {}
            missing_groups = []

            for group_name in requested_groups:
                if group_name in latest_plan["groups"]:
                    group_htmls[group_name] = latest_plan["groups"][group_name].replace('\n', ' ')
                else:
                    missing_groups.append(group_name)

            if missing_groups:
                logger.warning(f"Some groups not found: {missing_groups}")
                available_groups = list(latest_plan["groups"].keys())
                return jsonify({
                    "detail": {
                        "message": "Some groups not found",
                        "missing_groups": missing_groups,
                        "available_groups": available_groups
                    }
                }), 404

            response = {
                "plan_name": latest_plan["plan_name"],
                "groups": requested_groups,
                "group_htmls": group_htmls,  # Dictionary of group_name -> HTML
                "timestamp": latest_plan["timestamp"],
                "category": latest_plan.get("category", "st"),
                "url": latest_plan.get("url", "")
            }

            logger.debug(f"Returning HTML for {len(group_htmls)} groups")
            return jsonify(response)

        except Exception as e:
            logger.error(f"Error retrieving mixed plan {collection_name}: {str(e)}")
            return jsonify({"detail": str(e)}), 500
