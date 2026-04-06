"""
Structured diff for WSPA lesson plan HTML tables.
Compares old vs new plan HTML and extracts specific changes:
- Room changes (sala)
- Date/schedule changes (daty)
- Teacher changes
- Added/removed lessons
- Time slot changes
"""
import re
from bs4 import BeautifulSoup
from datetime import datetime
from shared_utils import get_logger

logger = get_logger('PlanDiffer')


def parse_plan_table(html):
    """
    Parse plan HTML table into structured data.
    Returns dict: {(day_name, time_slot): cell_info}
    where cell_info = {
        'raw': original text,
        'subject': subject name,
        'type': lesson type (laboratorium, wykład, etc.),
        'teacher': teacher name,
        'room': room number,
        'dates': list of dates,
        'location': online/uczelnia,
        'hours': total hours
    }
    """
    if not html or not html.strip():
        return {}, []

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table')
    if not table:
        return {}, []

    rows = table.find_all('tr')
    if len(rows) < 2:
        return {}, []

    # Extract day names from header
    header = rows[0]
    day_names = []
    for th in header.find_all(['th', 'td']):
        text = th.get_text(strip=True)
        if text and text not in ('Godziny', 'GODZ.', 'Godz.'):
            day_names.append(text)

    # Parse each row
    cells = {}
    for row in rows[1:]:
        tds = row.find_all('td')
        if not tds:
            continue

        # First cell = time slot
        time_text = tds[0].get_text(strip=True)
        if not time_text:
            continue

        # Remaining cells = lessons per day
        for i, td in enumerate(tds[1:]):
            if i >= len(day_names):
                break

            day = day_names[i]
            raw = td.get_text(strip=True, separator='\n')

            if raw:
                info = parse_cell_content(raw)
                info['raw'] = raw
                cells[(day, time_text)] = info

    return cells, day_names


def parse_cell_content(text):
    """Parse a cell's text content into structured fields."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    info = {
        'subject': '',
        'type': '',
        'teacher': '',
        'room': '',
        'dates': [],
        'location': '',
        'hours': '',
    }

    if not lines:
        return info

    # First line usually: "Subject name - type Xh" or just "Subject name"
    first_line = lines[0]
    # Match: "Matematyka - laboratorium 30h" or "Matematyka - wykład"
    subject_match = re.match(r'^(.+?)\s*[-–]\s*(laboratorium|wykład|warsztat|ćwiczenia|projekt|seminarium|konwersatorium|lektorat)\s*(\d+h)?', first_line, re.IGNORECASE)
    if subject_match:
        info['subject'] = subject_match.group(1).strip()
        info['type'] = subject_match.group(2).strip()
        if subject_match.group(3):
            info['hours'] = subject_match.group(3)
    else:
        info['subject'] = first_line

    for line in lines[1:]:
        # Teacher: starts with dr, mgr, prof, inż, etc.
        if re.match(r'^(dr|mgr|prof|inż|doc)', line, re.IGNORECASE):
            info['teacher'] = line

        # Room: "sala 207" or "sala XXX"
        room_match = re.search(r'sala\s+(\S+)', line, re.IGNORECASE)
        if room_match:
            info['room'] = room_match.group(1)

        # Dates: "daty: 03.03, 10.03, ..." or "zj.1,2,3,4"
        dates_match = re.search(r'dat[yae]:\s*(.+)', line, re.IGNORECASE)
        if dates_match:
            dates_str = dates_match.group(1).strip()
            info['dates'] = [d.strip() for d in re.split(r'[,;]\s*', dates_str) if d.strip()]

        # Session numbers: "zj.1,2,3,4,5"
        zj_match = re.search(r'zj\.\s*(.+)', line, re.IGNORECASE)
        if zj_match:
            info['dates'] = [f"zj.{d.strip()}" for d in zj_match.group(1).split(',')]

        # Location
        if 'on-line' in line.lower() or 'online' in line.lower():
            info['location'] = 'online'
        elif 'siedzibie uczelni' in line.lower() or 'stacjonarnie' in line.lower():
            info['location'] = 'stacjonarnie'

    return info


def diff_plans(old_html_groups, new_html_groups):
    """
    Compare old and new plan HTML for all groups.
    old_html_groups, new_html_groups: dict {group_name: html_string}
    Returns list of changes.
    """
    changes = []

    all_groups = set(list(old_html_groups.keys()) + list(new_html_groups.keys()))

    for group in all_groups:
        old_html = old_html_groups.get(group, '')
        new_html = new_html_groups.get(group, '')

        old_cells, _ = parse_plan_table(old_html)
        new_cells, _ = parse_plan_table(new_html)

        all_keys = set(list(old_cells.keys()) + list(new_cells.keys()))

        for key in all_keys:
            day, time = key
            old = old_cells.get(key)
            new = new_cells.get(key)

            if old and not new:
                changes.append({
                    'type': 'removed',
                    'group': group,
                    'day': day,
                    'time': time,
                    'subject': old.get('subject', ''),
                    'details': f"Usunięto: {old.get('subject', '')}",
                })

            elif new and not old:
                changes.append({
                    'type': 'added',
                    'group': group,
                    'day': day,
                    'time': time,
                    'subject': new.get('subject', ''),
                    'details': f"Dodano: {new.get('subject', '')}",
                })

            elif old and new and old.get('raw') != new.get('raw'):
                # Something changed - find what specifically
                specific_changes = []

                if old.get('room') != new.get('room') and (old.get('room') or new.get('room')):
                    specific_changes.append({
                        'field': 'room',
                        'old': old.get('room', '—'),
                        'new': new.get('room', '—'),
                        'label': 'Zmiana sali',
                    })

                if old.get('teacher') != new.get('teacher') and (old.get('teacher') or new.get('teacher')):
                    specific_changes.append({
                        'field': 'teacher',
                        'old': old.get('teacher', '—'),
                        'new': new.get('teacher', '—'),
                        'label': 'Zmiana prowadzącego',
                    })

                old_dates = set(old.get('dates', []))
                new_dates = set(new.get('dates', []))
                if old_dates != new_dates:
                    added_dates = new_dates - old_dates
                    removed_dates = old_dates - new_dates
                    if added_dates or removed_dates:
                        specific_changes.append({
                            'field': 'dates',
                            'added': sorted(added_dates),
                            'removed': sorted(removed_dates),
                            'label': 'Zmiana terminów',
                        })

                if old.get('location') != new.get('location') and (old.get('location') or new.get('location')):
                    specific_changes.append({
                        'field': 'location',
                        'old': old.get('location', '—'),
                        'new': new.get('location', '—'),
                        'label': 'Zmiana formy',
                    })

                if old.get('subject') != new.get('subject'):
                    specific_changes.append({
                        'field': 'subject',
                        'old': old.get('subject', '—'),
                        'new': new.get('subject', '—'),
                        'label': 'Zmiana przedmiotu',
                    })

                if not specific_changes:
                    # Generic change (couldn't pinpoint exact field)
                    specific_changes.append({
                        'field': 'other',
                        'label': 'Inne zmiany',
                    })

                changes.append({
                    'type': 'changed',
                    'group': group,
                    'day': day,
                    'time': time,
                    'subject': new.get('subject', old.get('subject', '')),
                    'changes': specific_changes,
                    'details': '; '.join(c['label'] for c in specific_changes),
                })

    return changes


def save_diff_to_db(db, collection_name, plan_name, changes, old_checksum, new_checksum):
    """Save a diff report to the plan_changes collection."""
    if not changes:
        return

    diff_doc = {
        'timestamp': datetime.now().isoformat(),
        'plan_name': plan_name,
        'collection': collection_name,
        'old_checksum': old_checksum,
        'new_checksum': new_checksum,
        'change_count': len(changes),
        'changes': changes,
        'summary': build_summary(changes),
    }

    db.plan_changes.insert_one(diff_doc)
    logger.info(f"Saved {len(changes)} changes for {plan_name}")


def build_summary(changes):
    """Build a human-readable summary of changes."""
    added = [c for c in changes if c['type'] == 'added']
    removed = [c for c in changes if c['type'] == 'removed']
    changed = [c for c in changes if c['type'] == 'changed']

    parts = []
    if added:
        parts.append(f"{len(added)} dodanych zajęć")
    if removed:
        parts.append(f"{len(removed)} usuniętych zajęć")
    if changed:
        # Count specific change types
        room_changes = sum(1 for c in changed if any(sc.get('field') == 'room' for sc in c.get('changes', [])))
        date_changes = sum(1 for c in changed if any(sc.get('field') == 'dates' for sc in c.get('changes', [])))
        teacher_changes = sum(1 for c in changed if any(sc.get('field') == 'teacher' for sc in c.get('changes', [])))

        if room_changes:
            parts.append(f"{room_changes} zmian sal")
        if date_changes:
            parts.append(f"{date_changes} zmian terminów")
        if teacher_changes:
            parts.append(f"{teacher_changes} zmian prowadzących")
        other = len(changed) - room_changes - date_changes - teacher_changes
        if other > 0:
            parts.append(f"{other} innych zmian")

    return ', '.join(parts) if parts else 'Brak zmian'
