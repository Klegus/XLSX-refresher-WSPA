"""
Post-processing validator for WSPA lesson plans.
Checks all plans in MongoDB for structural issues after each check cycle.
"""
import re
from shared_utils import get_logger

logger = get_logger('PlanValidator')

# Expected columns per category (excluding time column)
EXPECTED_DAYS = {
    'st': 5,       # pon-pt
    'nst': 3,      # pt-sob-nd
    'nst-online': 2,  # sob-nd
}


def validate_plan_html(html, category, group_name):
    """
    Validate a single plan's HTML table.
    Returns list of issues found (empty = OK).
    """
    issues = []

    if not html or not html.strip():
        issues.append("HTML pusty")
        return issues

    # Check for table
    if '<table' not in html:
        issues.append("Brak tabeli <table>")
        return issues

    # Count headers (days)
    th_count = len(re.findall(r'<th', html))
    expected_th = EXPECTED_DAYS.get(category, 5) + 1  # +1 for "Godziny" column

    if th_count == 0:
        issues.append("Brak nagłówków (0 <th>)")
    elif th_count < expected_th - 1:
        # Allow ±1 column tolerance (some plans have mixed day structures)
        issues.append(f"Za mało kolumn: {th_count} (oczekiwano min {expected_th - 1} dla {category})")

    # Count data rows (tr minus header)
    tr_count = len(re.findall(r'<tr', html))
    if tr_count <= 1:
        issues.append(f"Za mało wierszy: {tr_count}")

    # Count filled cells (non-empty td)
    filled = len(re.findall(r'<td[^>]*>[^<\s]', html))
    total_td = len(re.findall(r'<td', html))

    if filled == 0:
        issues.append("Wszystkie komórki puste (0 wypełnionych)")
    elif total_td > 0 and filled / total_td < 0.05:
        issues.append(f"Prawie pusty plan: {filled}/{total_td} komórek wypełnionych ({filled*100//total_td}%)")

    # Check for time slots in first column
    time_pattern = re.findall(r'<td[^>]*>\d+.*?-.*?\d+', html)
    if not time_pattern:
        issues.append("Brak slotów czasowych w pierwszej kolumnie")

    return issues


def validate_all_plans(db):
    """
    Validate all plans in MongoDB.
    Returns dict with results: {collection_name: {group: [issues]}}
    """
    results = {
        'ok': 0,
        'warnings': 0,
        'errors': 0,
        'details': []
    }

    collections = [c for c in db.list_collection_names() if c.startswith('plans_')]

    for col_name in collections:
        doc = db[col_name].find_one(sort=[("_id", -1)])
        if not doc or not doc.get('groups'):
            continue

        category = doc.get('category', 'st')
        plan_name = doc.get('plan_name', col_name)

        for group_name, html in doc['groups'].items():
            issues = validate_plan_html(html, category, group_name)

            if issues:
                results['errors'] += 1
                results['details'].append({
                    'plan': plan_name,
                    'group': group_name,
                    'category': category,
                    'collection': col_name,
                    'issues': issues,
                })
            else:
                results['ok'] += 1

    results['warnings'] = len([d for d in results['details'] if len(d['issues']) == 1 and 'prawie pusty' in d['issues'][0].lower()])

    return results


def log_validation_results(db):
    """Run validation and log results. Called after each check cycle."""
    results = validate_all_plans(db)

    if results['errors'] == 0:
        logger.info(f"Plan validation OK: {results['ok']} plans valid")
        return results

    logger.warning(
        f"Plan validation: {results['ok']} OK, {results['errors']} problems"
    )

    for detail in results['details']:
        logger.warning(
            f"  [{detail['category']}] {detail['plan']} / {detail['group']}: "
            f"{'; '.join(detail['issues'])}"
        )

    return results
