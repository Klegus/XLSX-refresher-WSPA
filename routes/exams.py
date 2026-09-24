from datetime import date

from flask import jsonify, request

from exam_schedule import ALL, entry_matches, squash
from plan_naming import describe_plan
from shared_utils import get_logger, to_iso

logger = get_logger('routes.exams')

MODES = {"st": "st", "nst": "nst", "nst puw": "nst_puw"}


def _meta(doc):
    return {
        "academic_year": doc.get("academic_year"),
        "is_current_year": doc.get("is_current_year", False),
        "fetched_at": to_iso(doc.get("fetched_at")),
        "source_url": doc.get("source_url"),
    }


def _years_behind(doc):
    from exam_schedule import current_academic_year
    try:
        return int(current_academic_year()[:4]) - int(str(doc.get("academic_year"))[:4])
    except ValueError:
        return 0


def _public(entry):
    keep = ("date", "time", "time_label", "subject", "lecturer", "form", "room", "semester", "group")
    out = {k: entry.get(k) for k in keep}
    out["programmes"] = None if entry["programmes"] == ALL else entry["programmes"]
    out["years"] = None if entry["years"] == ALL else entry["years"]
    out["modes"] = None if entry["modes"] == ALL else entry["modes"]
    return out


def init_exam_routes(app, db):
    @app.route('/api/exams', methods=['GET'])
    def get_exams():
        """Exam/credit timetable.

        ?plan=<collection>&groups=a,b  -> entries for that student's plan
        ?faculty=&degree=&year=&mode=  -> filtered list for the timetable page
        (no parameters -> everything)
        """
        try:
            doc = db.exam_schedule.find_one({"_id": "current"})
            if not doc:
                return jsonify({"available": False, "entries": []})

            faculty = request.args.get("faculty")
            degree = request.args.get("degree")
            year = request.args.get("year", type=int)
            mode = request.args.get("mode")
            groups = [g for g in (request.args.get("groups") or "").split(",") if g]

            plan_collection = request.args.get("plan")
            if plan_collection:
                latest = db[plan_collection].find_one({"plan_name": {"$exists": True}}, sort=[("timestamp", -1)])
                if latest:
                    info = describe_plan(latest["plan_name"])
                    faculty, degree, year = info["faculty"], info["degree"], info["year"]
                    mode = MODES.get(info["mode"])
                    # A timetable from an earlier academic year: the student was
                    # N years lower back then (shown for reference until the new one appears)
                    year_offset = _years_behind(doc)
                    if year and year_offset:
                        year = year - year_offset if year - year_offset >= 1 else None
                        if year is None:
                            return jsonify({"available": True, **_meta(doc), "today": date.today().isoformat(),
                                            "filter": {"faculty": faculty}, "entries": [],
                                            "year_offset": year_offset})

            entries = doc.get("entries", [])
            if faculty:
                entries = [e for e in entries
                           if entry_matches(e, faculty, degree or "I stopnia", year, mode, groups)]
            elif year or mode:
                entries = [e for e in entries
                           if (e["years"] == ALL or not year or year in e["years"])
                           and (e["modes"] == ALL or not mode or mode in e["modes"])]

            today = date.today().isoformat()
            return jsonify({
                "available": True,
                **_meta(doc),
                "year_offset": _years_behind(doc) if plan_collection else 0,
                "today": today,
                "filter": {"faculty": faculty, "degree": degree, "year": year, "mode": mode},
                "entries": [_public(e) for e in entries],
            })
        except Exception as e:
            logger.error(f"Error getting exams: {e}")
            return jsonify({"detail": str(e)}), 500

    @app.route('/api/exams/faculties', methods=['GET'])
    def get_exam_faculties():
        """Programmes present in the timetable, for the page filters."""
        doc = db.exam_schedule.find_one({"_id": "current"}) or {}
        seen = {}
        for e in doc.get("entries", []):
            if e["programmes"] == ALL:
                continue
            for p in e["programmes"]:
                key = (p["faculty"], p["degree"] or "I stopnia")
                seen[squash(key[0]) + key[1]] = {"faculty": key[0], "degree": key[1]}
        return jsonify({"programmes": sorted(seen.values(), key=lambda p: (p["faculty"], p["degree"]))})
