import re
from flask import jsonify, request
from typing import Optional
from shared_utils import get_logger, current_plan_collections, plan_collection_name
from plan_naming import describe_plan
from plan_notes import notes_for_groups

# Setup logger
logger = get_logger('routes.plans')

BLOCKED_MESSAGE = "Plan jest w trakcie weryfikacji – wkrótce będzie dostępny."
MAX_MIXED_GROUPS = 20

# Weekend studies are published as two sheets: classes on site (per group) and on-line
# lectures (whole year). A student needs both, so the on-line sheet is attached to the
# on-site plan as its companion and not listed separately.
# "... - zajęcia w siedzibie" / "... - zajęcia on-line" (nursing), "... - z stacjonarne" /
# "... - z on-line" (part-time and e-learning programmes)
_ONSITE_RE = re.compile(r'\s*-\s*(?:zaj[eę]cia\s+w\s+siedzibie|z\.?\s*stac(?:jonarn[eya])?\.?)\s*$', re.I)
_ONLINE_RE = re.compile(r'\s*-\s*(?:zaj[eę]cia|z\.?)\s+on-?\s?line\s*$', re.I)
COMPANION_LABEL = "zajęcia on-line"


def natural_key(text):
    """Sort key: numbers compared as numbers ("Grupa 2" before "Grupa 10")."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', text)]


# Some programmes publish one sheet per meeting ("... - zj3", "... - zj5") plus a sheet with
# the on-line meetings ("... - PIE st II zj on-line"). They are listed as one plan: the first
# meeting's sheet, with the others attached; a cell of the "zj5" sheet without its own
# meeting numbers belongs to meeting 5.
_MEETING_RE = re.compile(r'\s*-\s*zj\.?\s*(\d{1,2})\s*$', re.I)
_MEETING_ONLINE_RE = re.compile(r'\s*-\s*[^-]*?\bzj\.?\s*on-?\s?line\s*$', re.I)


def companion_sets(db, allowed=None):
    """Plans published in parts and shown as one plan:
    {listed collection: {"base": name without the part, "meeting": its meeting or None,
                         "parts": [{"label", "collection", "meeting"}]}}.
    allowed - collections that may take part (e.g. not quarantined)."""
    config = db.plans_config.find_one({"_id": "plans_json"}, {"plans": 1}) or {}
    onsite, online, meetings, meetings_online = {}, {}, {}, {}
    for plan in (config.get("plans") or {}).values():
        name, collection = plan.get("name", ""), plan_collection_name(plan)
        if allowed is not None and collection not in allowed:
            continue
        meeting = _MEETING_RE.search(name)
        if _ONSITE_RE.search(name):
            onsite[_ONSITE_RE.sub("", name)] = collection
        elif _ONLINE_RE.search(name):
            online[_ONLINE_RE.sub("", name)] = collection
        elif meeting:
            meetings.setdefault(_MEETING_RE.sub("", name), {})[str(int(meeting.group(1)))] = collection
        elif _MEETING_ONLINE_RE.search(name):
            meetings_online[_MEETING_ONLINE_RE.sub("", name)] = collection
    sets = {}
    for base, collection in onsite.items():
        if base in online:
            sets[collection] = {"base": base, "meeting": None,
                                "parts": [{"label": COMPANION_LABEL, "collection": online[base], "meeting": None}]}
    for base, by_number in meetings.items():
        first, *rest = sorted(by_number, key=int)
        parts = [{"label": f"zjazd {n}", "collection": by_number[n], "meeting": n} for n in rest]
        if base in meetings_online:
            parts.append({"label": COMPANION_LABEL, "collection": meetings_online[base], "meeting": None})
        if parts:
            sets[by_number[first]] = {"base": base, "meeting": first, "parts": parts}
    return sets


def companion_pairs(db):
    """{on-site / first meeting collection: on-line collection} (kept for callers that need
    only the on-line half)."""
    return {k: p["collection"] for k, s in companion_sets(db).items()
            for p in s["parts"] if p["label"] == COMPANION_LABEL}


def init_plan_routes(app, get_semester_collections, db):
    from plan_reconciler import get_blocks

    def visible_collections():
        """Semester collections without plans/groups quarantined by validation."""
        blocks = get_blocks(db)
        visible = {}
        for name, data in get_semester_collections().items():
            block = blocks.get(name)
            if block and block["plan"]:
                continue
            if block and block["groups"] and isinstance(data.get("groups"), dict):
                data = dict(data, groups={g: v for g, v in data["groups"].items() if g not in block["groups"]})
                if not data["groups"]:
                    continue
            visible[name] = data
        return visible

    def blocked_response(collection_name, group_names):
        block = get_blocks(db).get(collection_name)
        if not block:
            return None
        if block["plan"] or any(g in block["groups"] for g in group_names):
            return jsonify({"detail": {"message": BLOCKED_MESSAGE, "blocked": True}}), 423
        return None

    def plan_meetings(doc):
        """Dates of the meetings ("zj.3" -> dates) for plans that list meeting numbers:
        the calendar in the sheet header first, else the programme's PDF calendar."""
        if not doc:
            return None
        if doc.get("zjazdy"):
            return doc["zjazdy"]
        plan_name = doc.get("plan_name")
        if not plan_name:
            return None
        from zjazdy import calendar_for, calendars
        try:
            return calendar_for(plan_name, calendars(db))
        except Exception as e:
            logger.warning(f"Meeting calendar lookup failed for {plan_name}: {e}")
            return None

    def unblocked_sets():
        blocks = get_blocks(db)
        allowed = {c for c in current_plan_collections(db) if not (blocks.get(c) or {}).get("plan")}
        return companion_sets(db, allowed), blocks

    def plan_parts(collection_name, group_name=None):
        """The other sheets of a plan published in parts: [{label, collection, groups: {name: html},
        zjazdy, meeting}]. When a sheet has the student's group only that group is attached."""
        sets, blocks = unblocked_sets()
        entry = sets.get(collection_name)
        if not entry:
            return None, None
        parts = []
        for part in entry["parts"]:
            block = blocks.get(part["collection"])
            doc = db[part["collection"]].find_one({"groups": {"$exists": True}}, sort=[("timestamp", -1)])
            if not doc or not isinstance(doc.get("groups"), dict):
                continue
            groups = {g: html.replace('\n', ' ') for g, html in doc["groups"].items()
                      if not (block and g in block["groups"])}
            if group_name in groups:
                groups = {group_name: groups[group_name]}
            elif part["meeting"]:
                continue  # a meeting sheet without the student's group has nothing for them
            if not groups:
                continue
            # every sheet numbers its meetings on its own
            parts.append({"label": part["label"], "collection": part["collection"], "groups": groups,
                          "zjazdy": plan_meetings(doc), "meeting": part["meeting"]})
        return parts or None, entry["meeting"]

    @app.route('/api/faculties/<category>', methods=['GET'])
    def get_faculties(category: str):
        """
        Returns a list of unique faculties for a given category
        """
        try:
            logger.debug(f"Getting faculties for category: {category}")
            collections_data = visible_collections()
            faculties = sorted(list(set(
                data["faculty"] 
                for data in collections_data.values()
                if data["category"] == category
            )))
            logger.debug(f"Found {len(faculties)} faculties for category {category}")
            return jsonify({"faculties": faculties})
        except Exception as e:
            logger.error(f"Error getting faculties for category {category}: {str(e)}")
            return jsonify({"detail": "Wewnętrzny błąd serwera"}), 500

    @app.route('/api/plans/<category>/<faculty>', methods=['GET'])
    def get_plans(category: str, faculty: str):
        """
        Returns a list of plans for a given category and faculty
        """
        try:
            logger.debug(f"Getting plans for category: {category}, faculty: {faculty}")
            collections_data = visible_collections()

            # Get plans configuration from MongoDB to check for mixed flag
            plans_config_doc = db.plans_config.find_one({"_id": "plans_json"})
            plans_config = plans_config_doc.get("plans", {}) if plans_config_doc else {}

            sets = companion_sets(db, set(collections_data))
            hidden = {part["collection"] for entry in sets.values() for part in entry["parts"]}

            plans = []
            for collection_name, data in collections_data.items():
                if collection_name in hidden:
                    continue
                if data["category"] == category and data["faculty"] == faculty:
                    # Convert groups to just the keys if it's a dict with HTML values
                    groups = data["groups"]
                    if isinstance(groups, dict):
                        # If values are HTML strings (contain '<table'), just use keys
                        if any(isinstance(v, str) and '<table' in v for v in groups.values()):
                            groups = {k: k for k in groups.keys()}
                        groups = {k: groups[k] for k in sorted(groups, key=natural_key)}

                    # Convert mixed to boolean (handle string "true"/"false" from MongoDB)
                    mixed_value = data.get("mixed", False)
                    if isinstance(mixed_value, str):
                        mixed_bool = mixed_value.lower() == "true"
                    else:
                        mixed_bool = bool(mixed_value)

                    # a plan published in parts is named without the part ("zjazd 3", "w siedzibie")
                    naming = describe_plan(sets[collection_name]["base"] if collection_name in sets
                                           else data["plan_name"])
                    plan_data = {
                        "id": collection_name,
                        "name": data["plan_name"],
                        "display_name": naming["display_name"],
                        "short_name": naming["short_name"],
                        "year": naming["year"],
                        "semester": naming["semester"],
                        "degree": naming["degree"],
                        "variant": naming["variant"],
                        "groups": groups,
                        "mixed": mixed_bool
                    }
                    if collection_name in sets:
                        plan_data["companion"] = True

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

            # Readable order: degree, year, then variant
            degree_order = {"I stopnia": 0, "jednolite magisterskie": 1, "II stopnia": 2}
            plans.sort(key=lambda p: (degree_order.get(p["degree"], 9), p["year"] or 99,
                                      natural_key(p["variant"] or ""), natural_key(p["name"])))
            logger.debug(f"Found {len(plans)} plans for category {category} and faculty {faculty}")
            return jsonify({"plans": plans})
        except Exception as e:
            logger.error(f"Error getting plans for category {category}, faculty {faculty}: {str(e)}")
            return jsonify({"detail": "Wewnętrzny błąd serwera"}), 500

    @app.route('/api/plan/<collection_name>', methods=['GET'])
    @app.route('/api/plan/<collection_name>/<group_name>', methods=['GET'])
    def get_plan(collection_name: str, group_name: Optional[str] = None):
        try:
            logger.info(f"Pobieranie planu dla kolekcji: {collection_name}, grupy: {group_name}")
            if collection_name not in current_plan_collections(db):
                return jsonify({"detail": "Plan not found"}), 404
            blocked = blocked_response(collection_name, [group_name] if group_name else [])
            if blocked:
                return blocked
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
                    "notes": notes_for_groups(latest_plan.get("notes"), [group_name],
                                              list(latest_plan.get("groups") or {})),
                    "timestamp": latest_plan["timestamp"],
                    "category": latest_plan.get("category", "st"),
                    "url": latest_plan.get("url", "")
                }
            if group_name is not None:
                parts, meeting = plan_parts(collection_name, group_name)
                if parts:
                    response["parts"] = parts
                    # older frontends read only the on-line half
                    online = next((p for p in parts if p["label"] == COMPANION_LABEL), None)
                    if online:
                        response["companion"] = online
                if meeting:
                    response["meeting"] = meeting
            meetings = plan_meetings(latest_plan)
            if meetings:
                response["zjazdy"] = meetings
            logger.debug("Wysyłanie odpowiedzi")
            return jsonify(response)
        except Exception as e:
            logger.error(f"Error retrieving plan {collection_name}, group {group_name}: {str(e)}")
            return jsonify({"detail": "Wewnętrzny błąd serwera"}), 500

    @app.route('/api/plan/<collection_name>/mixed', methods=['POST'])
    def get_mixed_plan(collection_name: str):
        """
        Returns HTML for multiple groups combined (for mixed plans)
        Request body: {"groups": ["group1", "group2", ...]}
        """
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or "groups" not in data:
                return jsonify({"detail": "Groups list is required in request body"}), 400

            requested_groups = data["groups"]
            if (not isinstance(requested_groups, list) or not requested_groups
                    or len(requested_groups) > MAX_MIXED_GROUPS
                    or not all(isinstance(g, str) for g in requested_groups)):
                return jsonify({"detail": "Groups must be a non-empty list of names"}), 400
            if collection_name not in current_plan_collections(db):
                return jsonify({"detail": "Plan not found"}), 404

            logger.info(f"Getting mixed plan for collection: {collection_name}, groups: {requested_groups}")
            blocked = blocked_response(collection_name, requested_groups)
            if blocked:
                return blocked

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
                "notes": notes_for_groups(latest_plan.get("notes"), requested_groups,
                                          list(latest_plan.get("groups") or {})),
                "timestamp": latest_plan["timestamp"],
                "category": latest_plan.get("category", "st"),
                "url": latest_plan.get("url", "")
            }

            meetings = plan_meetings(latest_plan)
            if meetings:
                response["zjazdy"] = meetings
            logger.debug(f"Returning HTML for {len(group_htmls)} groups")
            return jsonify(response)

        except Exception as e:
            logger.error(f"Error retrieving mixed plan {collection_name}: {str(e)}")
            return jsonify({"detail": "Wewnętrzny błąd serwera"}), 500
