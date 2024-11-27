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

            # Check if this is an Edge/WNS endpoint
            is_edge = "notify.windows.com" in subscription.get('endpoint', '').lower()
            
            if is_edge:
                # Add required WNS headers to subscription
                if 'keys' not in subscription:
                    subscription['keys'] = {}
                subscription['keys'].update({
                    'contentEncoding': 'aes128gcm',
                    'auth': subscription['keys'].get('auth', ''),
                    'p256dh': subscription['keys'].get('p256dh', '')
                })
                
            success = push_manager.save_subscription(subscription, collection_name)
            
            if success:
                return jsonify({
                    'status': 'success',
                    'browser': 'edge' if is_edge else 'other'
                }), 200
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
            print(f"Received unsubscribe request with data: {data}")
            
            subscription = data.get('subscription')
            collection_name = data.get('collectionName') or data.get('planId')  # Accept either parameter
            
            if not subscription:
                print("Missing subscription data")
                return jsonify({'error': 'Missing subscription data'}), 400
                
            if not collection_name:
                print("Missing collection name or plan ID")
                return jsonify({'error': 'Missing collection name or plan ID'}), 400
            
            # Extract endpoint from subscription if it's nested
            if isinstance(subscription, dict) and 'endpoint' in subscription:
                endpoint = subscription['endpoint']
            else:
                endpoint = subscription
                
            print(f"Attempting to remove subscription with endpoint: {endpoint}")
            success = push_manager.remove_subscription({'endpoint': endpoint}, collection_name)
            
            if success:
                print(f"Successfully unsubscribed from {collection_name}")
                return jsonify({'status': 'success'}), 200
            else:
                print(f"Failed to unsubscribe from {collection_name}")
                return jsonify({'error': 'Failed to remove subscription'}), 500
                
        except Exception as e:
            print(f"Error in unsubscribe endpoint: {str(e)}")
            return jsonify({'error': str(e)}), 500
