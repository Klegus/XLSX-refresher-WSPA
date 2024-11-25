from flask import jsonify, request

def init_notification_routes(app, push_manager):
    @app.route('/api/notifications/subscribe', methods=['POST'])
    def subscribe():
        try:
            data = request.get_json()
            subscription = data.get('subscription')
            plan_id = data.get('planId')
            
            if not subscription or not plan_id:
                return jsonify({'error': 'Missing required fields'}), 400
                
            success = push_manager.save_subscription(subscription, plan_id)
            
            if success:
                return jsonify({'status': 'success'}), 200
            else:
                return jsonify({'error': 'Failed to save subscription'}), 500
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    @app.route('/api/notifications/unsubscribe', methods=['POST'])
    def unsubscribe():
        try:
            data = request.get_json()
            subscription = data.get('subscription')
            plan_id = data.get('planId')
            
            if not subscription or not plan_id:
                return jsonify({'error': 'Missing required fields'}), 400
                
            success = push_manager.remove_subscription(subscription, plan_id)
            
            if success:
                return jsonify({'status': 'success'}), 200
            else:
                return jsonify({'error': 'Failed to remove subscription'}), 500
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500
