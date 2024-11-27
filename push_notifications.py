from pywebpush import webpush
import json
import os
from datetime import datetime
from typing import Dict, Any

class PushNotificationManager:
    def __init__(self, db, vapid_private_key, vapid_public_key, vapid_claims):
        self.db = db
        self.vapid_private_key = vapid_private_key
        self.vapid_public_key = vapid_public_key
        self.vapid_claims = vapid_claims

    def save_subscription(self, subscription: Dict[str, Any], collection_name: str) -> bool:
        """Save a new push subscription for a plan collection"""
        try:
            # Check if subscription already exists
            existing = self.db.push_subscriptions.find_one({
                "subscription.endpoint": subscription["endpoint"],
                "collection_name": collection_name
            })
            
            if existing:
                print(f"Subscription already exists for {collection_name}")
                return True
                
            subscription_data = {
                "subscription": subscription,
                "collection_name": collection_name,
                "created_at": datetime.now(),
                "last_notified": None
            }
            self.db.push_subscriptions.insert_one(subscription_data)
            return True
        except Exception as e:
            print(f"Error saving subscription: {e}")
            return False

    def remove_subscription(self, subscription: Dict[str, Any], collection_name: str) -> bool:
        """Remove a push subscription from a plan collection"""
        try:
            print(f"Attempting to remove subscription: {subscription}")
            print(f"From collection: {collection_name}")
            
            # Try to find the subscription first
            existing = self.db.push_subscriptions.find_one({
                "subscription.endpoint": subscription["endpoint"],
                "collection_name": collection_name
            })
            
            if existing:
                print(f"Found existing subscription: {existing}")
                result = self.db.push_subscriptions.delete_one({
                    "subscription.endpoint": subscription["endpoint"],
                    "collection_name": collection_name
                })
                if result.deleted_count > 0:
                    print(f"Successfully removed subscription for {collection_name}")
                    return True
            else:
                print(f"No subscription found with endpoint {subscription['endpoint']}")
                return False
        except Exception as e:
            print(f"Error removing subscription: {e}")
            return False

    def send_notification(self, subscription: Dict[str, Any], message: str, url: str = None) -> bool:
        """Send a push notification to a single subscription"""
        try:
            data = {
                "message": message,
                "url": url
            }
            
            print(f"\nDebug - Processing subscription:")
            print(f"Endpoint: {subscription['endpoint']}")
            print(f"Original claims: {self.vapid_claims}")
            
            # Format subscription data
            subscription_info = {
                "endpoint": subscription["endpoint"],
                "keys": {
                    "p256dh": subscription["keys"]["p256dh"],
                    "auth": subscription["keys"]["auth"]
                }
            }
            
            # Adjust claims based on endpoint
            adjusted_claims = self.vapid_claims.copy()
            is_edge = "notify.windows.com" in subscription["endpoint"].lower()
            
            if is_edge:
                print("Detected Edge browser endpoint")
                # Extract the actual endpoint domain
                endpoint_domain = subscription["endpoint"].split("/", 3)[2]
                adjusted_claims["aud"] = f"https://{endpoint_domain}"
                print(f"Adjusted aud claim to: {adjusted_claims['aud']}")
            
            try:
                print(f"Sending push with claims: {adjusted_claims}")
                webpush(
                    subscription_info=subscription_info,
                    data=json.dumps(data),
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims=adjusted_claims,
                    timeout=10
                )
                print("Push sent successfully")
            except Exception as e:
                print(f"Push failed with error: {str(e)}")
                print(f"Response details: {getattr(e, 'response', 'No response')}")
                print(f"Response body: {getattr(e, 'response_body', 'No response body')}")
                raise
            return True
        except Exception as e:
            print(f"Error sending notification: {e}")
            error_str = str(e)
            
            if "401" in error_str:
                print(f"Authorization error for endpoint: {subscription.get('endpoint', 'unknown')}")
                print(f"Using claims: {adjusted_claims}")
            elif "410 Gone" in error_str:
                # Subscription expired/invalid - remove it
                print(f"Removing expired subscription: {subscription.get('endpoint', 'unknown')}")
                self.db.push_subscriptions.delete_one({"subscription.endpoint": subscription["endpoint"]})
            
            return False

    def notify_plan_update(self, collection_name: str, plan_name: str) -> None:
        """Send notifications to all subscriptions for a plan collection"""
        print(f"\nNotifying subscribers for collection: {collection_name}")
        
        # Find all subscriptions
        subscriptions = list(self.db.push_subscriptions.find({"collection_name": collection_name}))
        print(f"Found {len(subscriptions)} subscription(s)")
        
        if not subscriptions:
            print("No subscriptions found for this collection")
            return
            
        successful_notifications = 0
        failed_notifications = 0
        
        for sub in subscriptions:
            print(f"\nProcessing subscription: {sub.get('_id')}")
            print(f"Endpoint: {sub.get('subscription', {}).get('endpoint', 'No endpoint')}")
            
            message = f"Plan zajęć został zaktualizowany: {plan_name}"
            url = f"/plan/{collection_name}"
            
            try:
                success = self.send_notification(
                    subscription=sub["subscription"],
                    message=message,
                    url=url
                )
                
                if success:
                    # Update last_notified timestamp
                    self.db.push_subscriptions.update_one(
                        {"_id": sub["_id"]},
                        {"$set": {"last_notified": datetime.now()}}
                    )
                    successful_notifications += 1
                    print("Notification sent successfully")
                else:
                    failed_notifications += 1
                    print("Failed to send notification")
                    
            except Exception as e:
                failed_notifications += 1
                print(f"Error sending notification: {str(e)}")
                
        print(f"\nNotification summary:")
        print(f"- Successful: {successful_notifications}")
        print(f"- Failed: {failed_notifications}")
