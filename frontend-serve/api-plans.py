from flask import Flask, jsonify, request
from addons.helper import get_semester_collections
from main import db, app
from typing import Optional
from werkzeug.exceptions import NotFound, InternalServerError

@app.route('/panel/api/plans/<category>/<faculty>', methods=['GET'])
def get_plans(category: str, faculty: str):
    """
    Returns a list of plans for a given category and faculty
    """
    try:
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
        return jsonify({"plans": plans})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500

@app.route('/panel/api/faculties/<category>', methods=['GET'])
def get_faculties(category: str):
    """
    Returns a list of unique faculties for a given category
    """
    try:
        collections_data = get_semester_collections()
        faculties = sorted(list(set(
            data["faculty"] 
            for data in collections_data.values()
            if data["category"] == category
        )))
        return jsonify({"faculties": faculties})
    except Exception as e:
        return jsonify({"detail": str(e)}), 500

@app.route('/panel/api/plan/<collection_name>', methods=['GET'])
@app.route('/panel/api/plan/<collection_name>/<group_name>', methods=['GET'])
def get_plan(collection_name: str, group_name: Optional[str] = None):
    try:
        print(f"Pobieranie planu dla kolekcji: {collection_name}, grupy: {group_name}")
        latest_plan = db[collection_name].find_one(sort=[("timestamp", -1)])
        
        if not latest_plan:
            print("Nie znaleziono planu w kolekcji")
            return jsonify({
                "detail": "Plan not found"
            }), 404
            
        if group_name is not None:
            if group_name not in latest_plan["groups"]:
                print(f"Nie znaleziono grupy {group_name} w planie")
                available_groups = list(latest_plan["groups"].keys())
                print(f"Dostępne grupy: {available_groups}")
                return jsonify({
                    "detail": {
                        "message": "Nie znaleziono wybranej grupy",
                        "requested_group": group_name,
                        "available_groups": available_groups
                    }
                }), 404
        
        # Sprawdź czy plan jest złożony (brak kategorii i grup)
        if "category" not in latest_plan and "groups" not in latest_plan:
            response = {
                "plan_name": latest_plan["plan_name"],
                "timestamp": latest_plan["timestamp"],
                "url": latest_plan.get("url", ""),  # URL do oryginalnego planu
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
                "category": latest_plan.get("category", "st"),  # domyślnie "st" jeśli nie określono
                "url": latest_plan.get("url", "")  # URL do oryginalnego planu
            }
        print("Wysyłanie odpowiedzi:", response)
        return jsonify(response)
    except Exception as e:
        return jsonify({"detail": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"detail": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"detail": "Internal server error"}), 500
