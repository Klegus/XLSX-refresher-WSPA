"""
Exam and credit timetable ("Terminarz zaliczeń i egzaminów").

The university publishes one Excel file per academic year on PUW:
    Terminarze -> Rok akademicki 2025/2026 -> "Terminarz zaliczeń i egzaminów ..."
with a sheet per semester and rows like
    Data | Nazwisko wykładowcy | Przedmiot | Forma | Godzina | Sala | Kierunek | Grupa | Rok studiów | Tryb studiów

The file is typed by hand and updated all semester, so every column is
normalised defensively ("Informatyla", "stacjonarmy", "II i III", "sem.2",
"Media i Dziennikarstwo/Zarządzanie", "wszytskie kierunki", ...). Rows whose
programme cannot be recognised are kept and listed for the admin instead of
being dropped.
"""
import difflib
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime, time

import openpyxl
from bs4 import BeautifulSoup

from shared_utils import get_logger

logger = get_logger('ExamSchedule')

PUW = "https://puw.wspa.pl"
TIMETABLE_CATEGORY = f"{PUW}/course/index.php?categoryid=1324"  # "Terminarze"
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6}
ALL = "*"

# Filled while parsing one row: every guess the parser made, shown to the admin
CORRECTIONS = []

HEADER_ALIASES = {
    'date': ['data'],
    'lecturer': ['nazwisko wykładowcy', 'wykładowca', 'prowadzący'],
    'subject': ['przedmiot'],
    'form': ['forma'],
    'time': ['godzina'],
    'room': ['sala'],
    'faculty': ['kierunek'],
    'group': ['grupa'],
    'year': ['rok studiów', 'rok'],
    'mode': ['tryb studiów', 'tryb'],
}


def squash(text):
    t = unicodedata.normalize('NFKD', str(text or '').lower().replace('ł', 'l'))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in t if not unicodedata.combining(c)))


def current_academic_year(today=None):
    today = today or date.today()
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}/{start + 1}"


# ─── Locating the file on PUW ──────────────────────────────

YEAR_RE = re.compile(r'(?<!\d)(\d{2}|\d{4})\s*[/\-–.]\s*(\d{2}|\d{4})(?!\d)')


def academic_year_from_text(text):
    """'Rok akademicki 2026/2027', '2026-27', '26/27' -> '2026/2027' (or None)."""
    for m in YEAR_RE.finditer(str(text or '')):
        a, b = m.group(1), m.group(2)
        start = int(a) if len(a) == 4 else 2000 + int(a)
        end = int(b) if len(b) == 4 else (start // 100) * 100 + int(b)
        if end == start + 1 and 2000 < start < 2100:
            return f"{start}/{end}"
    return None


def _timetable_resource(session, course_url):
    page = BeautifulSoup(session.get(course_url, timeout=30).text, 'html.parser')
    resources = page.select('a[href*="mod/resource/view.php"]')
    for res in resources:
        if 'terminarz' in res.get_text(' ', strip=True).lower():
            return res['href']
    return resources[0]['href'] if len(resources) == 1 else None


def find_timetable_files(session):
    """{"2025/2026": resource_url, ...} for every academic year found on PUW.

    Looks for "Terminarz ..." courses under the Terminarze category - in year
    sub-categories ("Rok akademicki 2026/2027") or directly - and falls back to
    the PUW course search, so a reorganised category still gets found.
    """
    courses = {}  # course_url -> academic year

    def collect(url, depth=0):
        soup = BeautifulSoup(session.get(url, timeout=30).text, 'html.parser')
        category_year = academic_year_from_text(soup.title.get_text(' ', strip=True) if soup.title else '')
        for a in soup.select('a[href*="course/view.php?id="]'):
            title = a.get_text(' ', strip=True)
            if 'terminarz' in title.lower():
                year = academic_year_from_text(title) or category_year
                if year:
                    courses.setdefault(a['href'], year)
        if depth < 1:
            for a in soup.select('a[href*="course/index.php?categoryid="]'):
                if '&' not in a['href'] and academic_year_from_text(a.get_text(' ', strip=True)):
                    collect(a['href'], depth + 1)

    try:
        collect(TIMETABLE_CATEGORY)
    except Exception as e:
        logger.warning(f"Browsing Terminarze category failed: {e}")
    try:
        search = BeautifulSoup(session.get(f"{PUW}/course/search.php",
                                           params={"search": "terminarz", "perpage": "all"}, timeout=30).text,
                               'html.parser')
        for a in search.select('a[href*="course/view.php?id="]'):
            title = a.get_text(' ', strip=True)
            year = academic_year_from_text(title)
            if 'terminarz' in title.lower() and 'egzamin' in title.lower() and year:
                courses.setdefault(a['href'], year)
    except Exception as e:
        logger.warning(f"PUW search for timetable failed: {e}")

    found = {}
    for course_url, year in sorted(courses.items(), key=lambda kv: kv[1]):
        if year in found:
            continue
        resource = _timetable_resource(session, course_url)
        if resource:
            found[year] = resource
    return found


# ─── Normalising the rows ──────────────────────────────────

def _header_map(row):
    mapping = {}
    for idx, value in enumerate(row):
        key = str(value or '').strip().lower()
        for field, aliases in HEADER_ALIASES.items():
            if field not in mapping and any(key == a or key.startswith(a) for a in aliases):
                mapping[field] = idx
    return mapping


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.search(r'(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})', str(value or ''))
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_time(value):
    """-> (start "HH:MM" or None, label shown to students)."""
    if isinstance(value, time):
        label = value.strftime('%H:%M')
        return label, label
    if isinstance(value, datetime):
        label = value.strftime('%H:%M')
        return label, label
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if not text:
        return None, None
    m = re.match(r'(\d{1,2})[:.](\d{2})', text)
    start = f"{int(m.group(1)):02d}:{m.group(2)}" if m else None
    text = re.sub(r'(\d{1,2}):(\d{2}):00\b', r'\1:\2', text)
    return start, text


def parse_form(value):
    text = str(value or '').strip()
    low = text.lower()
    kinds = []
    if 'egzamin' in low:
        kinds.append('exam')
    if 'zalicz' in low:
        kinds.append('credit')
    return {'label': text, 'kinds': kinds or ['other'], 'retake': 'poprawk' in low}


def parse_years(value):
    """'I' -> [1]; 'II i III' -> [2, 3]; 'sem.2' -> [1] (semester -> year); empty -> ALL."""
    text = str(value or '').strip()
    if not text:
        return ALL
    years = set()
    for m in re.finditer(r'sem\.?\s*(\d{1,2})', text, re.I):
        years.add((int(m.group(1)) + 1) // 2)
        CORRECTIONS.append(f"„{m.group(0)}” → rok {(int(m.group(1)) + 1) // 2}")
    text = re.sub(r'sem\.?\s*\d{1,2}', '', text, flags=re.I)
    for token in re.findall(r'\b([IVX]+|\d)\b', text):
        years.add(ROMAN.get(token.upper(), int(token) if token.isdigit() else 0))
    years.discard(0)
    return sorted(years) or ALL


def parse_modes(value):
    """Tryb column -> {"st", "nst", "nst_puw"} or ALL."""
    text = str(value or '').lower()
    if not text.strip() or 'wszystk' in text or 'wszytsk' in text:
        return ALL
    modes = set()
    for part in re.split(r'[/,;]|\boraz\b|\bi\b', text):
        key = squash(part)
        if not key:
            continue
        if 'wykorzyst' in key or 'puw' in key or 'odleglosc' in key:
            modes.add('nst_puw')
            continue
        # "stacjonarny" is 88% similar to "niestacjonarny" - pick the closer one
        to_st = difflib.SequenceMatcher(None, key, 'stacjonarny').ratio()
        to_nst = difflib.SequenceMatcher(None, key, 'niestacjonarny').ratio()
        if max(to_st, to_nst) < 0.75:
            continue
        mode = 'nst' if key.startswith('nie') or to_nst > to_st else 'st'
        if key not in ('stacjonarny', 'niestacjonarny', 'stacjonarne', 'niestacjonarne'):
            CORRECTIONS.append(f"tryb „{part.strip()}” → {'niestacjonarny' if mode == 'nst' else 'stacjonarny'}")
        modes.add(mode)
    return sorted(modes) or ALL


def parse_programmes(value, known_faculties):
    """Kierunek column -> ([{"faculty", "degree"}], unrecognised parts) or ALL.

    Tolerates typos ("Informatyla", "Administarcja"), lists joined with "/",
    and degree written as "II stopnia" / "- studia II stopnia".
    """
    text = str(value or '').strip()
    if not text or re.search(r'wsz[yt]{1,2}s?t?kie\s+kierunki', text, re.I) or squash(text) in ('wszystkiekierunki', 'wszytskiekierunki'):
        return ALL, []
    keys = {squash(f): f for f in known_faculties}
    programmes, unknown = [], []
    for part in re.split(r'\s*/\s*', text):
        degree = 'II stopnia' if re.search(r'\bII\s+stop', part, re.I) else None
        if re.search(r'jednolit', part, re.I):
            degree = 'jednolite magisterskie'
        name = re.sub(r'[-–]?\s*(studia\s+)?(II|I)\s+stopnia.*$', '', part, flags=re.I).strip(' -–')
        key = squash(name)
        match = keys.get(key)
        if not match:
            # "Pedagogika" -> "Pedagogika przedszkolna i wczesnoszkolna"
            starts = [f for k, f in keys.items() if k.startswith(key) and len(key) >= 5]
            match = starts[0] if len(starts) == 1 else None
        if not match:
            close = difflib.get_close_matches(key, keys.keys(), n=1, cutoff=0.8)
            match = keys[close[0]] if close else None
        if match and squash(match) != key:
            CORRECTIONS.append(f"kierunek „{name}” → {match}")
        if match:
            programmes.append({'faculty': match, 'degree': degree})
        else:
            unknown.append(part)
    return programmes, unknown


def parse_workbook(content, known_faculties):
    """Excel bytes -> (entries, problems)."""
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    entries, problems = [], []
    for ws in wb.worksheets:
        semester = 'letni' if 'letn' in ws.title.lower() else 'zimowy' if 'zim' in ws.title.lower() else ws.title.strip()
        header, cols = None, {}
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            if header is None:
                cols = _header_map(row)
                if {'date', 'subject'} <= set(cols):
                    header = row_idx
                continue
            get = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None  # noqa: E731
            if not any(v not in (None, '') for v in row):
                continue
            day = parse_date(get('date'))
            subject = re.sub(r'\s+', ' ', str(get('subject') or '')).strip()
            if not day or not subject:
                problems.append({'sheet': ws.title, 'row': row_idx, 'reason': 'brak daty lub przedmiotu',
                                 'raw': [str(v) for v in row if v is not None][:6]})
                continue
            CORRECTIONS.clear()
            programmes, unknown = parse_programmes(get('faculty'), known_faculties)
            start, time_label = parse_time(get('time'))
            group = str(get('group') or '').strip()
            entry = {
                'date': day.isoformat(),
                'time': start,
                'time_label': time_label,
                'subject': subject,
                'lecturer': re.sub(r'\s+', ' ', str(get('lecturer') or '')).strip() or None,
                'form': parse_form(get('form')),
                'room': re.sub(r'\s+', ' ', str(get('room') or '')).strip() or None,
                'programmes': programmes,
                'years': parse_years(get('year')),
                'modes': parse_modes(get('mode')),
                'group': None if not group or 'nie dotyczy' in group.lower() else group,
                'semester': semester,
                'raw_faculty': str(get('faculty') or '').strip(),
                'raw_mode': str(get('mode') or '').strip(),
                'raw_year': str(get('year') or '').strip(),
                'raw_group': group,
                'row': row_idx,
                'sheet': ws.title,
            }
            entry['corrections'] = list(CORRECTIONS)
            entries.append(entry)
            if unknown:
                problems.append({'sheet': ws.title, 'row': row_idx, 'reason': 'nierozpoznany kierunek: ' + ', '.join(unknown),
                                 'raw': [subject, entry['raw_faculty']]})
        if header is None:
            problems.append({'sheet': ws.title, 'row': None, 'reason': 'nie znaleziono nagłówka tabeli', 'raw': []})
    entries.sort(key=lambda e: (e['date'], e['time'] or '99:99'))
    return entries, problems


# ─── Matching entries to a student's plan ──────────────────

def entry_matches(entry, faculty, degree, year, mode, groups=None):
    """Does this row concern a student of (faculty, degree, year, mode, groups)?"""
    if entry['programmes'] != ALL:
        ok = False
        for p in entry['programmes']:
            if squash(p['faculty']) != squash(faculty):
                continue
            # Unspecified degree means the first-cycle / uniform programme
            want = p['degree'] or ('jednolite magisterskie' if degree == 'jednolite magisterskie' else 'I stopnia')
            if want == degree:
                ok = True
        if not ok:
            return False
    if entry['years'] != ALL and year and year not in entry['years']:
        return False
    if entry['modes'] != ALL and mode and mode not in entry['modes']:
        return False
    if entry['group'] and groups:
        scope = squash(entry['group'])
        if not any(squash(g).startswith(scope) or scope.startswith(squash(g)) for g in groups):
            return False
    return True


# ─── Refresh (called from the backend loop and the admin panel) ─────

def refresh(db, session, year=None):
    """Download and parse the timetable; store it in db.exam_schedule.

    year: "2025/2026" to force a specific year, otherwise the current academic
    year, falling back to the newest one published (marked as previous year).
    """
    wanted = year or current_academic_year()
    files = find_timetable_files(session)
    if not files:
        raise RuntimeError("Nie znaleziono terminarza na PUW (kategoria Terminarze)")
    chosen = wanted if wanted in files else sorted(files)[-1]
    resp = session.get(files[chosen], timeout=60, allow_redirects=True)
    resp.raise_for_status()
    if resp.content[:2] != b'PK':
        raise RuntimeError(f"Terminarz {chosen} nie jest plikiem .xlsx ({resp.headers.get('content-type')})")
    checksum = hashlib.md5(resp.content, usedforsecurity=False).hexdigest()

    # Same file as last cycle: only note that it was checked
    previous = db.exam_schedule.find_one({"_id": "current"}, {"checksum": 1, "academic_year": 1, "requested_year": 1})
    if previous and previous.get("checksum") == checksum and previous.get("academic_year") == chosen \
            and previous.get("requested_year") == wanted:
        db.exam_schedule.update_one({"_id": "current"}, {"$set": {
            "checked_at": datetime.now(), "available_years": sorted(files),
            "is_current_year": chosen == current_academic_year()}})
        return db.exam_schedule.find_one({"_id": "current"})

    config = db.plans_config.find_one({"_id": "plans_json"}) or {}
    faculties = sorted({p.get('name', '').split(' - ')[0].strip() for p in (config.get('plans') or {}).values()} - {''})
    entries, problems = parse_workbook(resp.content, faculties)

    doc = {
        "_id": "current",
        "academic_year": chosen,
        "requested_year": wanted,
        "checked_at": datetime.now(),
        "is_current_year": chosen == current_academic_year(),
        "available_years": sorted(files),
        "source_url": files[chosen],
        "checksum": checksum,
        "fetched_at": datetime.now(),
        "entries": entries,
        "problems": problems,
    }
    db.exam_schedule.replace_one({"_id": "current"}, doc, upsert=True)
    logger.info(f"Exam timetable {chosen}: {len(entries)} entries, {len(problems)} problems")
    return doc
