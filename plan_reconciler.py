"""
Source reconciliation validator for WSPA lesson plans.

Reads the original Excel file independently of the LessonPlan parser
(own merged-cell handling, own day/group/time detection) and checks that
every lesson in the source ended up in the right group, day and time slot
of the HTML stored in MongoDB - and that nothing foreign got in.

Because it compares against the source instead of checking the HTML shape,
it also catches edge cases nobody has seen yet (new mixed layouts, split
sub-columns, rows dropped by keyword filters, ...).
"""
import difflib
import io
import re
import unicodedata
from html import unescape

import openpyxl

from shared_utils import get_logger

logger = get_logger('PlanReconciler')

DAY_PATTERNS = {
    'PONIEDZIAŁEK': 'Poniedziałek', 'PONIEDZIALEK': 'Poniedziałek',
    'WTOREK': 'Wtorek',
    'ŚRODA': 'Środa', 'SRODA': 'Środa',
    'CZWARTEK': 'Czwartek',
    'PIĄTEK': 'Piątek', 'PIATEK': 'Piątek',
    'SOBOTA': 'Sobota',
    'NIEDZIELA': 'Niedziela',
}
DAY_ORDER = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
# Keep in sync with LessonPlan: filters that intentionally drop content.
# The sheet generator keeps all lessons; only the legacy pandas fallback drops
# whole rows mentioning these words.
GROUP_ROW_FILTER = re.compile(r'semestr|zjazd', re.I)  # get_lessons_for_group


def parser_filter(text, whole_programme, legacy=False):
    """Name of the parser filter that drops this text, or None."""
    if not legacy or whole_programme:
        return None
    m = GROUP_ROW_FILTER.search(text)
    return m.group(0).lower() if m else None


TIME_RE = re.compile(r'^\s*(\d{3,4})\s*[-–—]\s*(\d{3,4})')

# Issue kinds, ordered by severity
WRONG_DAYS = 'days'          # HTML day headers differ from the days in Excel
MISSING = 'missing'          # lesson from Excel not in the group's HTML at all
MISPLACED = 'misplaced'      # lesson present, but in another day
TRUNCATED = 'truncated'      # multi-slot lesson present only in some of its time slots
SUBCOLUMN_LOST = 'subcolumn'  # group has several sub-columns with different lessons; some lost
FOREIGN = 'foreign'          # HTML cell has text the source does not have for this group/slot
ORPHAN = 'orphan'            # Excel content in columns that belong to no configured group
SKIPPED = 'skipped'          # dropped on purpose by a parser filter (info only)
STRUCTURE = 'structure'      # could not map days / groups / times at all

SEVERITY = {SKIPPED: 1, TRUNCATED: 3, WRONG_DAYS: 3, STRUCTURE: 3, MISSING: 3, SUBCOLUMN_LOST: 3, MISPLACED: 2, FOREIGN: 2, ORPHAN: 1}
KIND_LABELS = {
    STRUCTURE: 'Nie rozpoznano struktury',
    WRONG_DAYS: 'Złe nazwy dni',
    MISSING: 'Zgubione zajęcia',
    SUBCOLUMN_LOST: 'Konflikt podkolumn grupy',
    MISPLACED: 'Zajęcia w złym dniu',
    TRUNCATED: 'Ucięte zajęcia wielogodzinne',
    FOREIGN: 'Obce zajęcia w planie',
    ORPHAN: 'Treść poza grupami',
    SKIPPED: 'Pominięte celowo przez parser',
}


def norm(text):
    """Normalize lesson text for comparison."""
    if text is None:
        return ''
    text = unescape(re.sub(r'<[^>]+>', ' ', str(text)))
    return re.sub(r'\s+', ' ', text).strip().lower()


def norm_header(text):
    return norm(text).rstrip(':').strip()


def squash(text):
    """Header key tolerant to manual typing: no spaces, punctuation or diacritics."""
    t = unicodedata.normalize('NFKD', norm(text))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in t if not unicodedata.combining(c)))


_DAY_KEYS = {}
for _k, _v in DAY_PATTERNS.items():
    _DAY_KEYS[squash(_k)] = _v


def match_day(value):
    """Weekday name for a header cell, tolerating typos ("PIATEK", "Sobota.", "NIEDZELA")."""
    key = squash(value)
    if not key or len(key) > 14:
        return None
    if key in _DAY_KEYS:
        return _DAY_KEYS[key]
    close = difflib.get_close_matches(key, _DAY_KEYS.keys(), n=1, cutoff=0.8)
    return _DAY_KEYS[close[0]] if close else None


def time_key(text):
    """Start of a time slot ("8:15 - 9.00", "815- 900", "8<sup>15</sup>") as "815"."""
    raw = re.sub(r'<[^>]+>', '', str(text or ''))
    raw = re.sub(r'(?<=\d)[.:,;](?=\d{2})', '', raw).replace(' ', '')
    m = TIME_RE.match(raw)
    return m.group(1) if m else None


def short(text, n=70):
    t = re.sub(r'\s+', ' ', str(text)).strip()
    return t if len(t) <= n else t[:n - 1] + '…'


# ─── Source (Excel) side ───────────────────────────────────

def load_grid(xlsx_bytes, sheet_name):
    """Return 2D dict {(row, col): value} with merged cells filled from top-left."""
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    if sheet_name not in wb.sheetnames:
        raise KeyError(f"Brak arkusza '{sheet_name}' (są: {', '.join(wb.sheetnames)})")
    ws = wb[sheet_name]
    grid = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None and str(cell.value).strip():
                grid[(cell.row, cell.column)] = cell.value
    for rng in ws.merged_cells.ranges:
        top_left = ws.cell(rng.min_row, rng.min_col).value
        if top_left is None or not str(top_left).strip():
            continue
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                grid[(r, c)] = top_left
    max_row, max_col = ws.max_row, ws.max_column
    wb.close()
    return grid, max_row, max_col


def find_days(grid, max_col):
    """Find the row with weekday names and each day's column span."""
    best = None
    for r in range(1, 13):
        found = []
        for c in range(1, max_col + 1):
            day = match_day(grid.get((r, c)))
            if day and (not found or found[-1][1] != day):
                found.append((c, day))
        if len(found) >= 1 and (best is None or len(found) > len(best[1])):
            best = (r, found)
    if not best:
        return None, []
    row, found = best
    spans = []
    for i, (c, day) in enumerate(found):
        end = found[i + 1][0] - 1 if i + 1 < len(found) else max_col
        spans.append((day, c, end))
    return row, spans


def find_group_columns(grid, days_row, max_col, groups_cfg, aliases=None):
    """Map configured groups to Excel columns using the header row under the days."""
    aliases = aliases or {}
    wanted = {name: {squash(header)} | {squash(a) for a in aliases.get(name, [])}
              for name, header in groups_cfg.items()}
    best_row, best_hits = None, -1
    for r in range(days_row + 1, days_row + 5):
        values = {squash(grid.get((r, c))) for c in range(2, max_col + 1)}
        hits = sum(1 for h in wanted.values() if h & values)
        if hits > best_hits:
            best_row, best_hits = r, hits
    columns = {name: [] for name in groups_cfg}
    if best_hits <= 0:
        return None, columns
    for c in range(2, max_col + 1):
        header = squash(grid.get((best_row, c)))
        owners = [name for name, h in wanted.items() if header and header in h]
        # A column may belong to several groups (shared header aliased to sub-groups)
        for owner in owners:
            columns[owner].append(c)
    return best_row, columns


def find_time_rows(grid, start_row, max_row):
    rows = {}
    for r in range(start_row, max_row + 1):
        key = time_key(grid.get((r, 1)))
        if key:
            rows[r] = key
    return rows


# ─── Output (HTML) side ────────────────────────────────────

def parse_html_table(html):
    """Return (headers, {time_key: {day: text}})."""
    rows = re.findall(r'<tr>(.*?)</tr>', html or '', flags=re.S)
    if not rows:
        return [], {}
    headers = [norm(h).title() for h in re.findall(r'<th>(.*?)</th>', rows[0], flags=re.S)]
    days = headers[1:]
    table = {}
    for row in rows[1:]:
        cells = re.findall(r'<td>(.*?)</td>', row, flags=re.S)
        if not cells:
            continue
        key = time_key(cells[0])
        if not key:
            continue
        slot = table.setdefault(key, {})
        for day, cell in zip(days, cells[1:]):
            text = norm(cell)
            if text:
                slot[day] = (slot.get(day, '') + ' ' + text).strip()
    return days, table


# ─── Reconciliation ────────────────────────────────────────

def reconcile_plan(plan_id, plan_cfg, xlsx_bytes, stored_doc):
    """Compare one plan's source Excel with its stored HTML.

    Returns {"issues": [...], "expected": n, "matched": n, "groups": {...}}.
    """
    result = {"plan_id": plan_id, "plan": plan_cfg.get('name', plan_id),
              "category": plan_cfg.get('category'), "mixed": bool(plan_cfg.get('mixed')),
              "issues": [], "expected": 0, "matched": 0}
    issues = result["issues"]

    def add(kind, group=None, day=None, time=None, text=None, detail=None):
        issues.append({"kind": kind, "group": group, "day": day, "time": time,
                       "text": short(text) if text else None, "detail": detail})

    try:
        grid, max_row, max_col = load_grid(xlsx_bytes, plan_cfg['sheet_name'])
    except Exception as e:
        add(STRUCTURE, detail=f"Nie da się odczytać Excela: {e}")
        return result

    days_row, day_spans = find_days(grid, max_col)
    if not days_row:
        add(STRUCTURE, detail="Nie znaleziono wiersza z dniami tygodnia")
        return result

    groups_cfg = plan_cfg.get('groups') or {}

    # Same group written two ways in Excel ("Grupy 1-18" vs "Grupy 1 - 18") ends up split
    squashed = {}
    for name, header in groups_cfg.items():
        squashed.setdefault(re.sub(r'[\s:.]', '', norm(header)), []).append(name)
    for names in squashed.values():
        if len(names) > 1:
            add(STRUCTURE, group=' / '.join(names),
                detail="Ta sama grupa ma w Excelu różnie zapisane nagłówki – plan jest rozbity na części")
    group_row, group_cols = find_group_columns(grid, days_row, max_col, groups_cfg,
                                               plan_cfg.get('group_aliases'))
    time_rows = find_time_rows(grid, (group_row or days_row) + 1, max_row)
    if not time_rows:
        add(STRUCTURE, detail="Nie znaleziono wierszy z godzinami")
        return result

    stored_groups = (stored_doc or {}).get('groups') or {}
    result["checksum"] = (stored_doc or {}).get('checksum')
    result["groups_available"] = list(stored_groups)
    if not stored_doc:
        add(STRUCTURE, detail="Plan nie został jeszcze pobrany do bazy")
        return result

    # "Cały kierunek" plans (no groups in config) - every day column belongs to the plan
    whole_programme = not groups_cfg or group_row is None
    if whole_programme:
        if len(stored_groups) != 1 and len(groups_cfg) != 1:
            add(STRUCTURE, detail="Nie znaleziono nagłówków grup w Excelu")
            return result
        only = next(iter(groups_cfg or stored_groups))
        group_cols = {only: [c for _, s, e in day_spans for c in range(s, e + 1)]}

    used_cols = set()
    for group, cols in group_cols.items():
        used_cols.update(cols)
        html = stored_groups.get(group)
        if not cols:
            add(STRUCTURE, group=group, detail="Grupy nie ma w nagłówkach Excela (zmieniona nazwa?)")
            continue
        if html is None:
            add(MISSING, group=group, detail="Grupa nie ma zapisanego planu")
            continue

        html_days, html_table = parse_html_table(html)
        result["issues"].extend(_reconcile_group(
            group, cols, grid, time_rows, day_spans, html_days, html_table, result,
            whole_programme, legacy=(stored_doc or {}).get("parser_version", 0) < 2))

    # Content in schedule rows that no configured group owns
    if not whole_programme:
        orphan_cells = {}
        for r in time_rows:
            for day, start, end in day_spans:
                for c in range(start, end + 1):
                    if c in used_cols:
                        continue
                    t = norm(grid.get((r, c)))
                    if t:
                        header = str(grid.get((group_row, c)) or f'kolumna {c}')
                        orphan_cells.setdefault((short(header, 40), t), (day, time_rows[r]))
        for (header, t), (day, tkey) in orphan_cells.items():
            add(ORPHAN, group=header, day=day, time=tkey, text=t,
                detail="Kolumna z zajęciami nie jest przypisana do żadnej grupy w konfiguracji")

    return result


def _expected_for_group(cols, grid, time_rows, day_spans):
    """{(time_key, excel_day): [distinct lesson texts]} for one group's columns."""
    expected = {}
    for r, tkey in time_rows.items():
        for day, start, end in day_spans:
            day_cols = [c for c in cols if start <= c <= end]
            texts = []
            for c in day_cols:
                t = norm(grid.get((r, c)))
                if t and t not in texts:
                    texts.append(t)
            if texts:
                expected[(tkey, day)] = (texts, len(day_cols))
    return expected


def _map_days(expected, html_table, group_days, html_days):
    """Find which HTML column each Excel day actually landed in.

    Votes per (excel_day, html_day) on lessons found at the same time.
    Falls back to the same-named column, then to position.
    """
    votes = {}
    for (tkey, day), (texts, _) in expected.items():
        for html_day, actual in html_table.get(tkey, {}).items():
            hits = sum(1 for t in texts if t in actual)
            if hits:
                votes[(day, html_day)] = votes.get((day, html_day), 0) + hits
    mapping, taken = {}, set()
    for (day, html_day), _ in sorted(votes.items(), key=lambda kv: -kv[1]):
        if day not in mapping and html_day not in taken:
            mapping[day] = html_day
            taken.add(html_day)
    for i, day in enumerate(group_days):
        if day in mapping:
            continue
        if day in html_days and day not in taken:
            mapping[day] = day
        elif i < len(html_days) and html_days[i] not in taken:
            mapping[day] = html_days[i]
        else:
            continue
        taken.add(mapping[day])
    return mapping


def _reconcile_group(group, cols, grid, time_rows, day_spans, html_days, html_table, result,
                     whole_programme=False, legacy=False):
    issues = []

    def add(kind, day=None, time=None, text=None, detail=None):
        issues.append({"kind": kind, "group": group, "day": day, "time": time,
                       "text": short(text) if text else None, "detail": detail})

    expected = _expected_for_group(cols, grid, time_rows, day_spans)
    group_days = [d for d, s, e in day_spans if any(s <= c <= e for c in cols)]
    day_map = _map_days(expected, html_table, group_days, html_days)

    wrong = [(d, day_map[d]) for d in group_days if d in day_map and day_map[d] != d]
    if wrong:
        add(WRONG_DAYS, detail="Zajęcia pokazane pod złym dniem: " +
            ', '.join(f"{a} → {b}" for a, b in wrong))
    lost_days = [d for d in group_days if d not in day_map and any(k[1] == d for k in expected)]
    for d in lost_days:
        add(MISSING, day=d, detail=f"Cały dzień ({d}) nie ma kolumny w planie")

    truncated = {}  # (day, text) -> [times missing]
    skipped = {}    # (filter, text) -> count
    for (tkey, day), (texts, n_cols) in expected.items():
        if day in lost_days:
            continue
        actual = html_table.get(tkey, {}).get(day_map.get(day), '')
        html_day = day_map.get(day)
        day_text = ' | '.join(html_table.get(k, {}).get(html_day, '') for k in html_table)
        for t in texts:
            dropped_by = parser_filter(t, whole_programme, legacy)
            if dropped_by and t not in actual:
                skipped[(dropped_by, t)] = skipped.get((dropped_by, t), 0) + 1
                continue
            result["expected"] += 1
            if t in actual:
                result["matched"] += 1
            elif len(texts) > 1:
                add(SUBCOLUMN_LOST, day, tkey, t,
                    f"Grupa ma {n_cols} podkolumny z różnymi zajęciami, w planie jest tylko część")
            elif t in day_text:
                truncated.setdefault((day, t), []).append(tkey)
            elif any(t in txt for slot in html_table.values() for txt in slot.values()):
                add(MISPLACED, day, tkey, t, "Jest w planie, ale w innym dniu")
            else:
                add(MISSING, day, tkey, t)

    for (day, t), times in truncated.items():
        add(TRUNCATED, day, times[0], t,
            f"Brakuje w {len(times)} slotach ({', '.join(fmt_time(x) for x in times[:6])}"
            f"{'…' if len(times) > 6 else ''}) – zajęcia wielogodzinne ucięte")

    for (word, t), n in skipped.items():
        add(SKIPPED, text=t, detail=f"Filtr parsera „{word}” – pominięte w {n} slotach")

    # Text in HTML that the source does not have anywhere for this group
    group_texts = {t for texts, _ in expected.values() for t in texts}
    reverse = {v: k for k, v in day_map.items()}
    for tkey, slot in html_table.items():
        for html_day, actual in slot.items():
            leftover = actual
            for t in expected.get((tkey, reverse.get(html_day)), ([], 0))[0]:
                leftover = leftover.replace(t, '')
            leftover = leftover.strip(' |')
            if leftover and not any(leftover in t for t in group_texts):
                add(FOREIGN, html_day, tkey, leftover,
                    "Tekst nie występuje w Excelu dla tej grupy (zajęcia innej grupy?)")
    return issues


def fmt_time(key):
    return f"{key[:-2]}:{key[-2:]}"


def summarize(result):
    kinds = {}
    for i in result["issues"]:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    worst = max((SEVERITY[k] for k in kinds), default=0)
    result["counts"] = kinds
    result["status"] = {0: 'ok', 1: 'info', 2: 'warning', 3: 'error'}[worst]
    result["coverage"] = round(100 * result["matched"] / result["expected"], 1) if result["expected"] else None
    return result


# ─── Full run over all plans ───────────────────────────────

def plan_collection_name(plan_cfg):
    # Must match LessonPlan.collection_name
    faculty = plan_cfg.get('faculty', '').replace(' ', '-').replace('_', '-')
    return f"plans_{faculty}_{plan_cfg.get('name', '').lower().replace(' ', '_')}"


def reconcile_all(db, session, plans, progress_cb=None):  # noqa: C901
    """Reconcile every configured plan. Downloads each Excel file once."""
    import pymongo

    files, results = {}, []
    for i, (plan_id, plan_cfg) in enumerate(plans.items(), 1):
        if progress_cb:
            progress_cb(i, len(plans), plan_cfg.get('name', plan_id))
        url = plan_cfg.get('download_url')
        try:
            if url not in files:
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
                files[url] = resp.content
            doc = db[plan_collection_name(plan_cfg)].find_one(
                {"groups": {"$exists": True}}, sort=[("timestamp", pymongo.DESCENDING)])
            result = reconcile_plan(plan_id, plan_cfg, files[url], doc)
        except Exception as e:
            logger.error(f"Reconcile failed for {plan_id}: {e}")
            result = {"plan_id": plan_id, "plan": plan_cfg.get('name', plan_id),
                      "category": plan_cfg.get('category'), "mixed": bool(plan_cfg.get('mixed')),
                      "issues": [{"kind": STRUCTURE, "group": None, "day": None, "time": None,
                                  "text": None, "detail": f"Błąd walidacji: {e}"}],
                      "expected": 0, "matched": 0}
        results.append(summarize(result))
    return results


# ─── Blocking (quarantine) ─────────────────────────────────
# Plans/groups with a blocking issue are hidden from the public API until the
# parser is fixed (issue disappears on the next validation) or an admin
# accepts the current file version (ack is tied to the stored checksum).

BLOCKING_SEVERITY = 3


def compute_blocks(result):
    """Return (whole_plan_blocked, [blocked group names]) for one result."""
    groups = set(result.get("groups_available") or [])
    blocked_groups, whole = set(), False
    for issue in result["issues"]:
        if SEVERITY.get(issue["kind"], 0) < BLOCKING_SEVERITY:
            continue
        names = [g for g in (issue.get("group") or '').split(' / ') if g in groups]
        if names:
            blocked_groups.update(names)
        else:
            whole = True
    if groups and blocked_groups >= groups:
        whole = True
    return whole, sorted(blocked_groups)


def save_results(db, results):
    from datetime import datetime
    now = datetime.now()
    for r in results:
        plan_blocked, groups_blocked = compute_blocks(r)
        db.plan_validation.update_one(
            {"_id": r["plan_id"]},
            {"$set": {
                "plan": r["plan"],
                "collection": r.get("collection"),
                "checksum": r.get("checksum"),
                "status": r["status"],
                "coverage": r.get("coverage"),
                "counts": r.get("counts", {}),
                "issues": r["issues"][:300],
                "issues_total": len(r["issues"]),
                "blocked_plan": plan_blocked,
                "blocked_groups": groups_blocked,
                "validated_at": now,
            }},
            upsert=True,
        )


def run_validation(db, plan_ids=None, progress_cb=None):
    """Download sources, reconcile, persist. Returns the list of results."""
    import requests
    from moodle_scanner import login_puw

    config = db.plans_config.find_one({"_id": "plans_json"}) or {}
    plans = config.get("plans") or {}
    if plan_ids is not None:
        plans = {k: v for k, v in plans.items() if k in set(plan_ids)}
    if not plans:
        return []

    session = requests.Session()
    if not login_puw(session):
        raise RuntimeError("Login do PUW nieudany – nie można pobrać plików do walidacji")

    results = reconcile_all(db, session, plans, progress_cb)
    for r in results:
        r["collection"] = plan_collection_name(plans[r["plan_id"]])
    save_results(db, results)

    # Forget validations of plans no longer in config
    if plan_ids is None:
        db.plan_validation.delete_many({"_id": {"$nin": list(plans)}})

    blocked = sum(1 for r in results if compute_blocks(r)[0] or compute_blocks(r)[1])
    logger.info(f"Validation done: {len(results)} plans, {blocked} with blocked content")
    return results


def get_blocks(db):
    """{collection: {"plan": bool, "groups": set, "reason": str}} for currently blocked content."""
    config = db.system_config.find_one({"_id": "config"}) or {}
    if not config.get("validation_blocking", True):
        return {}
    blocks = {}
    for v in db.plan_validation.find(
            {"$or": [{"blocked_plan": True}, {"blocked_groups.0": {"$exists": True}}]},
            {"collection": 1, "checksum": 1, "blocked_plan": 1, "blocked_groups": 1, "ack": 1}):
        ack = v.get("ack") or {}
        if ack.get("checksum") and ack["checksum"] == v.get("checksum"):
            continue
        blocks[v["collection"]] = {
            "plan": bool(v.get("blocked_plan")),
            "groups": set(v.get("blocked_groups") or []),
        }
    return blocks
