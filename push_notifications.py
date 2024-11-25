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

    def save_subscription(self, subscription: Dict[str, Any], plan_id: str) -> bool:
        """Save a new push subscription"""
        try:
            subscription_data = {
                "subscription": subscription,
                "plan_id": plan_id,
                "created_at": datetime.now(),
                "last_notified": None
            }
            self.db.push_subscriptions.insert_one(subscription_data)
            return True
        except Exception as e:
            print(f"Error saving subscription: {e}")
            return False

    def remove_subscription(self, subscription: Dict[str, Any], plan_id: str) -> bool:
        """Remove a push subscription"""
        try:
            self.db.push_subscriptions.delete_one({
                "subscription.endpoint": subscription["endpoint"],
                "plan_id": plan_id
            })
            return True
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
            
            webpush(
                subscription_info=subscription,
                data=json.dumps(data),
                vapid_private_key=self.vapid_private_key,
                vapid_claims=self.vapid_claims
            )
            return True
        except Exception as e:
            print(f"Error sending notification: {e}")
            if "410 Gone" in str(e):
                # Subscription expired/invalid - remove it
                self.db.push_subscriptions.delete_one({"subscription.endpoint": subscription["endpoint"]})
            return False

    def notify_plan_update(self, plan_id: str, plan_name: str) -> None:
        """Send notifications to all subscriptions for a specific plan"""
        subscriptions = self.db.push_subscriptions.find({"plan_id": plan_id})
        
        for sub in subscriptions:
            message = f"Plan zajęć został zaktualizowany: {plan_name}"
            url = f"/plan/{plan_id}"  # Adjust URL format as needed
            
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
