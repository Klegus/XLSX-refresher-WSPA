from flask import jsonify, request

def init_config_routes(app, get_system_config, get_plans_config, update_system_config, update_plans_config):
    @app.route("/api/config", methods=["GET", "POST", "PUT"])
    def manage_config():
        # Check maintenance mode for POST/PUT requests
        if request.method in ['POST', 'PUT'] and get_system_config().get("maintenance_mode", False):
            # Allow only maintenance mode toggle
            data = request.json
            if not (len(data.get("system_config", {})) == 1 and "maintenance_mode" in data.get("system_config", {})):
                return jsonify({"error": "System is in maintenance mode"}), 403

        if request.method == "GET":
            config = get_system_config()
            plans = get_plans_config()
            return jsonify({
                "system_config": config,
                "plans_config": plans
            })
        
        elif request.method == "POST":
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400
                
            if "system_config" in data:
                try:
                    update_system_config(data["system_config"])
                except Exception as e:
                    return jsonify({"error": f"Failed to update system config: {str(e)}"}), 500
                
            if "plans_config" in data:
                try:
                    # Handle plan deletion
                    if isinstance(data["plans_config"], dict):
                        for plan_name, plan_data in data["plans_config"].items():
                            if plan_data is None:
                                # Delete plan
                                db.plans_config.update_one(
                                    {"_id": "plans_json"},
                                    {"$unset": {f"plans.{plan_name}": ""}}
                                )
                            else:
                                # Update or add plan
                                update_plans_config(data["plans_config"])
                    else:
                        update_plans_config(data["plans_config"])
                except Exception as e:
                    return jsonify({"error": f"Failed to update plans config: {str(e)}"}), 500
                
            return jsonify({"message": "Configuration updated successfully"})

        elif request.method == "PUT":
            data = request.json
            if not data or "plans_config" not in data:
                return jsonify({"error": "No plan data provided"}), 400

            try:
                # Get current plans
                current_plans = get_plans_config()
                if not current_plans:
                    return jsonify({"error": "Could not retrieve current plans"}), 500

                # Update specific plan
                for plan_name, plan_data in data["plans_config"].items():
                    if plan_name in current_plans:
                        current_plans[plan_name].update(plan_data)
                    else:
                        return jsonify({"error": f"Plan {plan_name} not found"}), 404

                # Save updated plans
                update_plans_config(current_plans)
                return jsonify({"message": f"Plan {plan_name} updated successfully"})

            except Exception as e:
                return jsonify({"error": f"Failed to update plan: {str(e)}"}), 500
