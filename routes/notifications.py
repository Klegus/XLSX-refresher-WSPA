from flask import jsonify, request

def init_notification_routes(app, push_manager):
    @app.route('/api/notifications/subscribe', methods=['POST'])
    def subscribe():
        try:
            data = request.get_json()
            subscription = data.get('subscription')
            collection_name = data.get('collectionName')
            
            if not subscription or not collection_name:
                return jsonify({'error': 'Missing required fields'}), 400
                
            success = push_manager.save_subscription(subscription, collection_name)
            
            if success:
                return jsonify({'status': 'success'}), 200
            else:
                return jsonify({'error': 'Failed to save subscription'}), 500
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    @app.route('/api/notifications/test', methods=['POST'])
    def test_notification():
        try:
            data = request.get_json()
            subscription = data.get('subscription')
            
            if not subscription:
                return jsonify({'error': 'Missing subscription'}), 400
                
            success = push_manager.send_notification(
                subscription=subscription,
                message="To jest testowe powiadomienie!",
                url="/panel"
            )
            
            if success:
                return jsonify({'status': 'success'}), 200
            else:
                return jsonify({'error': 'Failed to send test notification'}), 500
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    @app.route('/api/notifications/unsubscribe', methods=['POST'])
    def unsubscribe():
        try:
            data = request.get_json()
            subscription = data.get('subscription')
            collection_name = data.get('collectionName')
            
            if not subscription or not collection_name:
                return jsonify({'error': 'Missing required fields'}), 400
                
            success = push_manager.remove_subscription(subscription, collection_name)
            
            if success:
                return jsonify({'status': 'success'}), 200
            else:
                return jsonify({'error': 'Failed to remove subscription'}), 500
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500
