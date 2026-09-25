"""
Calendar of weekend meetings (zjazdy).

Most part-time and weekend plans do not list dates of classes, only meeting numbers
("zj.2,3,5"). The dates of every meeting are published separately, as a PDF
"<mode> - organizacja roku akademickiego <year>" in the "Strefa studenta" course of
each programme:

    PIĄTEK      SOBOTA      NIEDZIELA   NR ZJAZDU
    2026-10-02  2026-10-03  2026-10-04  1

This module finds those PDFs, turns them into {season: {meeting: [ISO dates]}} and
picks the calendar that belongs to a plan, so the week filter and the calendar
subscription can show classes on their real dates.
"""
import hashlib
import io
import re
from datetime import datetime

from bs4 import BeautifulSoup

from plan_naming import describe_plan
from shared_utils import get_logger, fetch_bytes, UnsafeDownload

logger = get_logger('Zjazdy')

# bumped when parsing changes, so stored calendars are parsed again
CALENDAR_PARSER_VERSION = 2

_ROW_RE = re.compile(r'((?:\d{4}-\d{2}-\d{2}\s+)+)(\d{1,2})\s*$')
_TITLE_RE = re.compile(r'organizacja\s+roku\s+akademickiego', re.I)


def _season(dates):
    """Winter semester: October-February, summer: March-September."""
    month = int(dates[0][5:7])
    return 'zimowy' if month >= 9 or month <= 2 else 'letni'


def parse_calendar(pdf_bytes):
    """{"zimowy": {"1": ["2026-10-02", ...]}, "letni": {...}} from the PDF text."""
    from pypdf import PdfReader
    return parse_calendar_text('\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(pdf_bytes)).pages))


def parse_calendar_text(text):
    """One table per semester, each numbering its meetings from 1. A repeated number starts
    the next table - the summer semester may start in February, so the month alone is not
    enough to tell the tables apart."""
    seasons, current, season = {}, None, None
    for line in text.splitlines():
        m = _ROW_RE.search(line.strip())
        if not m:
            continue
        dates, number = m.group(1).split(), m.group(2)
        if current is None or number in current:
            season = _season(dates) if season is None else ('letni' if season == 'zimowy' else 'zimowy')
            current = seasons.setdefault(season, {})
        current[number] = dates
    return seasons


_MONTHS = {'stycz': 1, 'lut': 2, 'marc': 3, 'kwie': 4, 'maj': 5, 'czerw': 6, 'lip': 7, 'sierp': 8,
           'wrze': 9, 'pa': 10, 'listop': 11, 'grud': 12}
_HEADER_RE = re.compile(
    r'zjazd\w*\s*(?:nr\.?\s*)?(\d{1,2})\s*[:.]\s*'
    r'(\d{1,2})(?:\s+([a-ząćęłńóśźż]+))?(?:\s+(\d{4}))?\s*(?:r\.?)?\s*[–—-]\s*'
    r'(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})', re.I)


def _month(word):
    word = (word or '').lower()
    return next((n for prefix, n in _MONTHS.items() if word.startswith(prefix)), None)


def parse_header_calendar(text):
    """Meetings listed above the timetable of a sheet (e-learning programmes):
    "• zjazd nr 1: 16 października 2026 r. – 18 października 2026 r." -> {"1": [ISO dates]}.
    Every sheet numbers its own meetings (on-site and on-line sheets differ)."""
    from datetime import date, timedelta
    meetings = {}
    for m in _HEADER_RE.finditer(text or ''):
        num, d1, mon1, y1, d2, mon2, y2 = m.groups()
        end_month = _month(mon2)
        if not end_month:
            continue
        try:
            end = date(int(y2), end_month, int(d2))
            start = date(int(y1 or y2), _month(mon1) or end_month, int(d1))
        except ValueError:
            continue
        if start > end or (end - start).days > 6:
            continue
        meetings[str(int(num))] = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    return meetings or None


def _title_info(title):
    """What a calendar PDF applies to, read from its title."""
    t = title.lower()
    sem = re.search(r'sem\.?\s*(\d+)', t)
    return {
        'mode': 'nst' if re.search(r'\bnst\b', t) else 'st',
        'semester': int(sem.group(1)) if sem else None,
        'weekend': 'weekend' in t,
    }


def _course_matches(course, faculty, degree):
    name = course.lower()
    if faculty.lower() not in name:
        return False
    # "Informatyka - studia I stopnia" vs "... II stopnia"; courses without a degree fit all
    for d in ('ii stopnia', 'i stopnia'):
        if d in name:
            return d == (degree or '').lower()
    return True


def calendar_for(plan_name, calendars):
    """Meetings {number: [dates]} of the plan's semester, or None if no calendar fits."""
    info = describe_plan(plan_name)
    if not info.get('faculty') or info.get('mode') not in ('st', 'nst'):
        return None  # e-learning studies (nst puw) publish no meeting calendar
    weekend = 'weekend' in (info.get('variant') or '') or 'weekend' in plan_name.lower()
    best, best_score = None, -1
    for cal in calendars:
        if not _course_matches(cal['course'], info['faculty'], info.get('degree')):
            continue
        t = _title_info(cal['title'])
        if t['mode'] != info['mode']:
            continue
        if t['semester'] is not None and t['semester'] != info.get('semester'):
            continue
        score = (2 if t['semester'] is not None else 0) + (1 if t['weekend'] == weekend else 0)
        if t['weekend'] and not weekend and t['semester'] is None:
            continue
        if score > best_score:
            best, best_score = cal, score
    if not best:
        return None
    return (best.get('seasons') or {}).get(info.get('season') or 'zimowy') or None


def find_calendar_files(session):
    """[(course name, title, url)] of the organisation-of-year PDFs on PUW."""
    from moodle_scanner import list_strefa_courses
    found = []
    for course, url in list_strefa_courses(session):
        page = BeautifulSoup(session.get(url, timeout=30).text, 'html.parser')
        for a in page.find_all('a', href=True):
            title = a.get_text(' ', strip=True).replace(' Plik', '').strip()
            if 'mod/resource' in a['href'] and _TITLE_RE.search(title):
                found.append((course, title, a['href']))
    return found


def refresh(db, session):
    """Download and parse every calendar; store them in the zjazd_calendars collection."""
    stored = 0
    for course, title, url in find_calendar_files(session):
        try:
            pdf = fetch_bytes(session, url, timeout=60)
            if not pdf.startswith(b'%PDF'):
                logger.warning(f"Not a PDF: {title}")
                continue
            seasons = parse_calendar(pdf)
        except (UnsafeDownload, Exception) as e:
            logger.error(f"Calendar {title} failed: {e}")
            continue
        if not seasons:
            logger.warning(f"No meetings found in {title}")
            continue
        db.zjazd_calendars.update_one(
            {"_id": hashlib.sha1(url.encode()).hexdigest()},
            {"$set": {"course": course, "title": title, "url": url, "seasons": seasons,
                      "checksum": hashlib.md5(pdf, usedforsecurity=False).hexdigest(),
                      "fetched_at": datetime.now()}},
            upsert=True)
        stored += 1
    logger.info(f"Meeting calendars refreshed: {stored}")
    return stored


def calendars(db):
    return list(db.zjazd_calendars.find({}, {"course": 1, "title": 1, "seasons": 1}))
