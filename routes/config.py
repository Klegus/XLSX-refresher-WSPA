from flask import jsonify, request
import asyncio
from shared_utils import get_logger

# Setup logger
logger = get_logger('routes.config')

def init_config_routes(app, get_system_config, get_plans_config, update_system_config, update_plans_config, db):
    @app.route("/api/config", methods=["GET", "POST", "PUT"])
    def manage_config():
        # Check maintenance mode for POST/PUT requests
        if request.method in ['POST', 'PUT'] and get_system_config().get("maintenance_mode", False):
            logger.warning("Config update attempted while in maintenance mode")
            return jsonify({"error": "System is in maintenance mode"}), 503

        if request.method == "GET":
            logger.debug("Getting system and plans configuration")
            system_config = get_system_config()
            plans_config = get_plans_config()
            return jsonify({
                "system_config": system_config,
                "plans_config": plans_config
            })
        
        elif request.method == "POST":
            logger.debug("Updating configuration via POST")
            data = request.json
            if not data:
                logger.warning("No data provided in config update request")
                return jsonify({"error": "No data provided"}), 400
                
            if "system_config" in data:
                try:
                    logger.info("Updating system configuration")
                    update_system_config(data["system_config"])
                except Exception as e:
                    logger.error(f"Failed to update system config: {str(e)}")
                    return jsonify({"error": f"Failed to update system config: {str(e)}"}), 500
                
            if "plans_config" in data:
                try:
                    # Handle plan deletion
                    if isinstance(data["plans_config"], dict):
                        # Get current plans first
                        current_plans = get_plans_config()
                        if not current_plans:
                            current_plans = {}
                            logger.info("No existing plans found, creating new plans config")
                        
                        plans_updated = False
                        updated_plan_name = None
                        for plan_name, plan_data in data["plans_config"].items():
                            if plan_data is None:
                                # Delete plan
                                if plan_name in current_plans:
                                    del current_plans[plan_name]
                                    plans_updated = True
                                    logger.info(f"Deleted plan '{plan_name}' from configuration")
                            else:
                                # Update or add this specific plan
                                logger.info(f"Updating plan '{plan_name}' in configuration")
                                current_plans[plan_name] = plan_data
                                plans_updated = True
                                updated_plan_name = plan_name
                        
                        # Save all plans back to database only if changes were made
                        if plans_updated:
                            update_result = update_plans_config(current_plans)
                            logger.info(f"Plan update result: {update_result.modified_count} documents modified")
                            
                            # Return the updated plan name so frontend can trigger a check
                            if updated_plan_name:
                                return jsonify({
                                    "message": "Configuration updated successfully", 
                                    "updated_plan": updated_plan_name
                                })
                    else:
                        logger.info("Updating entire plans configuration")
                        update_plans_config(data["plans_config"])
                except Exception as e:
                    import traceback
                    error = f"Failed to update plans config: {str(e)}\n{traceback.format_exc()}"
                    logger.error(error)
                    return jsonify({"error": error}), 500
                
            logger.info("Configuration updated successfully")
            return jsonify({"message": "Configuration updated successfully"})

        elif request.method == "PUT":
            logger.debug("Updating plans via PUT")
            data = request.json
            if not data or "plans_config" not in data:
                logger.warning("No plan data provided in PUT request")
                return jsonify({"error": "No plan data provided"}), 400

            try:
                # Get current plans
                current_plans = get_plans_config()
                if not current_plans:
                    logger.error("Could not retrieve current plans")
                    return jsonify({"error": "Could not retrieve current plans"}), 500

                # Update specific plan
                for plan_name, plan_data in data["plans_config"].items():
                    if plan_name in current_plans:
                        logger.info(f"Updating plan {plan_name} with PUT")
                        current_plans[plan_name].update(plan_data)
                    else:
                        logger.warning(f"Plan {plan_name} not found in PUT request")
                        return jsonify({"error": f"Plan {plan_name} not found"}), 404

                # Save updated plans
                logger.info(f"Saving updated plan {plan_name}")
                update_plans_config(current_plans)
                return jsonify({"message": f"Plan {plan_name} updated successfully"})

            except Exception as e:
                logger.error(f"Failed to update plan: {str(e)}")
                return jsonify({"error": f"Failed to update plan: {str(e)}"}), 500

    # Add new endpoint for toggling maintenance mode that bypasses the maintenance check
    @app.route("/api/maintenance-toggle", methods=["POST"])
    def toggle_maintenance_mode():
        """
        Special endpoint that allows toggling maintenance mode even when the system is in maintenance mode.
        This ensures we can exit maintenance mode when needed.
        """
        try:
            logger.debug("Maintenance mode toggle request received")
            data = request.json
            if data is None:
                logger.warning("No data provided in maintenance toggle request")
                return jsonify({"error": "No data provided"}), 400
            
            if "maintenance_mode" not in data:
                logger.warning("maintenance_mode field missing in toggle request")
                return jsonify({"error": "maintenance_mode field is required"}), 400
            
            # Get current state
            current_config = get_system_config()
            current_state = current_config.get("maintenance_mode", False)
            new_state = data["maintenance_mode"]
            
            # Only update if state is changing
            if current_state != new_state:
                logger.info(f"Changing maintenance mode from {current_state} to {new_state}")
                update_data = {"maintenance_mode": new_state}
                
                # If turning off maintenance mode, clear the reason
                if not new_state:
                    update_data["maintenance_reason"] = None
                
                # Update system config
                update_system_config(update_data)
                
                return jsonify({
                    "success": True, 
                    "previous_state": current_state,
                    "new_state": new_state,
                    "message": f"Maintenance mode {'enabled' if new_state else 'disabled'}"
                })
            else:
                logger.info(f"Maintenance mode already {new_state}, no change needed")
                return jsonify({
                    "success": True,
                    "state": new_state,
                    "message": f"Maintenance mode already {'enabled' if new_state else 'disabled'}"
                })
            
        except Exception as e:
            import traceback
            error = f"Failed to toggle maintenance mode: {str(e)}\n{traceback.format_exc()}"
            logger.error(error)
            return jsonify({"error": error, "success": False}), 500

    def create_lesson_plan_manager(plan_id, plan_config):
        """Create a lesson plan manager for a plan that doesn't have one yet"""
        from main import lesson_plan_managers, db
        import os
        from LessonPlan import LessonPlan
        from comparer import LessonPlanComparator
        
        try:
            logger.info(f"Attempting to create a new lesson plan manager for {plan_id}")
            
            username = os.getenv("EMAIL")
            password = os.getenv("PASSWORD")
            mongo_uri = os.getenv("MONGO_URI")
            openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
            selected_model = os.getenv("SELECTED_MODEL", "openai/chatgpt-4o-latest")
            
            # Create lesson plan
            lesson_plan = LessonPlan(
                username=username,
                password=password,
                mongo_uri=mongo_uri,
                plan_config=plan_config,
            )
            
            # Create comparator if needed
            comparator = None
            compare_enabled = plan_config.get("compare", False)
            if compare_enabled and openrouter_api_key:
                try:
                    logger.info(f"Creating comparator for {plan_id}")
                    comparator = LessonPlanComparator(
                        mongo_uri=mongo_uri,
                        openrouter_api_key=openrouter_api_key,
                        selected_model=selected_model,
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize comparator: {e}")
            
            # Create manager
            from main import LessonPlanManager
            manager = LessonPlanManager(
                lesson_plan,
                comparator,
                working_directory=".",
                plan_config=plan_config,
            )
            
            # Add to global managers dictionary
            lesson_plan_managers[plan_id] = manager
            logger.info(f"Successfully created lesson plan manager for {plan_id}")
            return manager
        except Exception as e:
            logger.error(f"Failed to create lesson plan manager: {str(e)}")
            return None
    
    @app.route("/api/check-plan/<plan_id>", methods=["POST"])
    def check_specific_plan(plan_id):
        """
        Trigger a check for a specific plan
        """
        try:
            logger.info(f"Check requested for plan: {plan_id}")
            # Import after function definition to avoid circular imports
            from main import lesson_plan_managers
            
            # Check if plan_id exists in managers
            if plan_id not in lesson_plan_managers:
                logger.warning(f"Plan {plan_id} not found in managers")
                
                # Try to get the plan configuration
                plans_config = get_plans_config()
                if not plans_config or plan_id not in plans_config:
                    logger.error(f"Plan {plan_id} not found in configuration")
                    return jsonify({
                        "success": False,
                        "error": f"Plan {plan_id} not found in configuration"
                    }), 404
                
                # Create a new manager for this plan
                logger.info(f"Creating a new manager for plan {plan_id}")
                plan_config = plans_config[plan_id]
                manager = create_lesson_plan_manager(plan_id, plan_config)
                
                if not manager:
                    logger.error(f"Failed to create manager for plan {plan_id}")
                    return jsonify({
                        "success": False,
                        "error": f"Failed to create manager for plan {plan_id}"
                    }), 500
            else:
                manager = lesson_plan_managers[plan_id]
            
            # Run check in background
            logger.info(f"Starting check for plan {plan_id}")
            asyncio.create_task(run_check())
            
            # Return immediate success response
            return jsonify({
                "success": True,
                "message": f"Check initiated for plan {plan_id}"
            })
            
            # Define async function for running check
            async def run_check():
                try:
                    logger.info(f"Executing check for plan {plan_id}")
                    result = await manager.check_once()
                    if result:
                        logger.info(f"Plan check for {plan_id} completed with changes detected")
                    else:
                        logger.info(f"Plan check for {plan_id} completed with no changes")
                except Exception as e:
                    logger.error(f"Error during plan check: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error initiating plan check: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
