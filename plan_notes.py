"""
Extra information printed around the schedule table in the Excel sheets.

Above the table (row under the title):
    EGZAMINY:
    Rachunek prawdopodobieństwa i statystyka, Podstawy teorii grafów
    Sp.: Sztuczna inteligencja: Statystyka matematyczna

Below the table:
    Program studiów zawiera także:
    * Język angielski - lektorat on-line 15h
    On-line:
    * Edukacja obywatelska - warsztat on-line 10h - dr ...
    *Seminarium i przygotowanie pracy dyplomowej ...

Sheets are edited by hand, so everything here is tolerant: unknown text is
kept as a generic note instead of being dropped, and spreadsheet garbage
(formulas, stray numbers) is ignored.
"""
import re

EXAMS_RE = re.compile(r'^\s*egzaminy\s*:?', re.I)
SPEC_LINE_RE = re.compile(r'^\s*(sp\.?\s*:?\s*[^:]+?)\s*:\s*(.+)$', re.I)
HEADING_RE = re.compile(r'^\s*([^*\n][^\n]{0,60}?)\s*:\s*$')
TITLE_RE = re.compile(r'\bsemestr\b.*\brok\s+akad', re.I | re.S)
GARBAGE_RE = re.compile(r'^\s*(=|[-+]?\d+([.,]\d+)?\s*$)')


def _clean(text):
    return re.sub(r'[ \t]+', ' ', str(text or '')).strip()


def _split_subjects(text):
    """Split "A, B, C" into subjects, but keep "Prawo X, system Y" together.

    A comma starts a new subject only when the next word is capitalised.
    """
    text = re.sub(r'\s*,?\s*oraz\s*:?\s*$', '', _clean(text).replace('\n', ' '))
    parts, current = [], ''
    for chunk in re.split(r',\s*', text):
        if current and chunk[:1].isupper():
            parts.append(current)
            current = chunk
        else:
            current = f"{current}, {chunk}" if current else chunk
    if current:
        parts.append(current)
    return [p.strip(' .,;') for p in parts if p.strip(' .,;')]


def parse_exams(text):
    """'EGZAMINY: ...' cell -> [{"scope": None | "Sp.: X", "subjects": [...]}]."""
    body = EXAMS_RE.sub('', str(text or ''), count=1)
    groups = []
    general = []
    for line in body.split('\n'):
        line = _clean(line)
        if not line:
            continue
        spec = SPEC_LINE_RE.match(line)
        if spec:
            groups.append({"scope": _clean(spec.group(1)), "subjects": _split_subjects(spec.group(2))})
        else:
            general.append(line)
    # Each line is its own list; a line starting lowercase continues the previous one
    lines = []
    for line in general:
        if lines and line[:1].islower():
            lines[-1] += ' ' + line
        else:
            lines.append(line)
    subjects = [subject for line in lines for subject in _split_subjects(line)]
    if subjects:
        groups.insert(0, {"scope": None, "subjects": subjects})
    return [g for g in groups if g["subjects"]]


def parse_info_blocks(texts):
    """Cells below the table -> [{"title": str | None, "items": [str]}]."""
    blocks = []
    for text in texts:
        text = str(text or '').strip()
        if not text or GARBAGE_RE.match(text):
            continue
        block = {"title": None, "items": []}
        for line in text.split('\n'):
            line = _clean(line)
            if not line:
                continue
            heading = HEADING_RE.match(line)
            if heading and not block["items"] and block["title"] is None:
                block["title"] = heading.group(1)
            elif line.startswith('*') or line.startswith('•'):
                item = line.lstrip('*• ').strip(' -')
                if item:
                    block["items"].append(item)
            elif block["items"]:
                # Continuation of the previous bullet
                block["items"][-1] += ' ' + line
            else:
                block["items"].append(line)
        if block["items"]:
            blocks.append(block)
    return blocks


def parse_notes(above_texts, below_texts):
    """Build the notes structure stored with a plan.

    above_texts: distinct cell texts above the day header row
    below_texts: distinct cell texts below the last time slot row
    """
    exams, extra_above = [], []
    for text in above_texts:
        if EXAMS_RE.match(str(text or '')):
            exams.extend(parse_exams(text))
        elif not TITLE_RE.search(str(text or '')) and not GARBAGE_RE.match(str(text or '')):
            extra_above.append(text)
    # Merged cells span several rows, so the same text can arrive more than once
    info, seen = [], set()
    for block in parse_info_blocks(list(dict.fromkeys(extra_above + list(below_texts)))):
        key = (block["title"], tuple(block["items"]))
        if key not in seen:
            seen.add(key)
            info.append(block)
    unique_exams, seen = [], set()
    for exam in exams:
        key = (exam["scope"], tuple(exam["subjects"]))
        if key not in seen:
            seen.add(key)
            unique_exams.append(exam)
    return {"exams": unique_exams, "info": info}


def _squash(text):
    import unicodedata
    t = unicodedata.normalize('NFKD', str(text or '').lower().replace('ł', 'l'))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in t if not unicodedata.combining(c)))


def notes_for_groups(notes, group_names, all_groups=None):
    """Keep general exams plus the ones scoped to the selected groups.

    "Sp.: Sztuczna inteligencja" matches groups "Sp.: Sztuczna inteligencja gr.1"
    etc. Scopes matching no group at all are kept (better to show than to lose).
    """
    if not notes:
        return None
    keys = [_squash(g) for g in group_names if g]
    plan_keys = [_squash(g) for g in (all_groups or []) if g]
    exams = []
    for exam in notes.get("exams", []):
        scope = _squash(exam.get("scope"))
        matches_selected = any(k.startswith(scope) for k in keys)
        matches_any = any(k.startswith(scope) for k in plan_keys)
        if not scope or not keys or matches_selected or not matches_any:
            exams.append(exam)
    if not exams and not notes.get("info"):
        return None
    return {"exams": exams, "info": notes.get("info", [])}
