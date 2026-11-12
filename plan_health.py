"""State of the last check of every plan (collection ``plan_health``, one document per
plan collection): does the published version still match the file on PUW.

A plan is never replaced by a version that cannot be trusted - the last good one stays
published. This module records why (``withheld``: the new file failed the comparison
with its source, ``failed``: it could not be downloaded or read), since when, and tells
the API and the admin panel when students should be warned that what they see may be
out of date."""
from datetime import datetime

OK, WITHHELD, FAILED = "ok", "withheld", "failed"
# A download that fails once says nothing (PUW hiccup, session); after this many checks
# in a row the plan counts as out of date. Counted in checks, not hours: nothing is
# checked at night.
FAILS_BEFORE_STALE = 3


def record(db, collection, plan_name, outcome):
    """Store the outcome of one check ({"state", "reason", "changed", "checksum", "issues"})
    and return the plan's health document."""
    now = datetime.now()
    prev = db.plan_health.find_one({"_id": collection}) or {}
    state = outcome.get("state") or OK
    doc = {"plan": plan_name, "state": state, "checked_at": now,
           "reason": outcome.get("reason"), "changed": bool(outcome.get("changed")),
           "unreachable": bool(outcome.get("unreachable")),
           "checksum": outcome.get("checksum"), "issues": (outcome.get("issues") or [])[:50]}
    if state == OK:
        doc.update(last_ok_at=now, fails=0, since=None, alerted=False)
    else:
        running = prev.get("state") in (WITHHELD, FAILED)
        doc["fails"] = (prev.get("fails") or 0) + 1 if running else 1
        doc["since"] = prev.get("since") or now if running else now
    db.plan_health.update_one({"_id": collection}, {"$set": doc}, upsert=True)
    return {**prev, **doc, "_id": collection}


def stale(health):
    """{"since", "kind"} when the published version may differ from the file on PUW, else None.
    "changed": the file is known to be different; "unreachable": it could not be fetched
    for several checks in a row."""
    if not health or health.get("state") in (None, OK):
        return None
    if health.get("changed"):
        return {"since": health.get("since"), "kind": "changed"}
    if health.get("unreachable") and (health.get("fails") or 0) >= FAILS_BEFORE_STALE:
        return {"since": health.get("since"), "kind": "unreachable"}
    return None


def stale_plans(db):
    """{collection: {"since", "kind"}} of every plan that is out of date."""
    found = {}
    for health in db.plan_health.find({"state": {"$in": [WITHHELD, FAILED]}}):
        info = stale(health)
        if info:
            found[health["_id"]] = info
    return found


def take_alerts(db, current=None):
    """Plans that became out of date and were not reported yet - each failing streak is
    reported once. Marks them as reported."""
    query = {"state": {"$in": [WITHHELD, FAILED]}, "alerted": {"$ne": True}}
    if current is not None:
        query["_id"] = {"$in": list(current)}
    fresh = [h for h in db.plan_health.find(query) if stale(h)]
    if fresh:
        db.plan_health.update_many({"_id": {"$in": [h["_id"] for h in fresh]}}, {"$set": {"alerted": True}})
    return fresh


def accepted(db, collection, checksum):
    """An admin accepted this exact file version despite the detected problems."""
    health = db.plan_health.find_one({"_id": collection}, {"accept_checksum": 1}) or {}
    return bool(checksum) and health.get("accept_checksum") == checksum
