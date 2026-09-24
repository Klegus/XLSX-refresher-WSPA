"""Download real plans from PUW and turn them into anonymised test fixtures.

Run once in a while (needs PUW credentials in the environment):
    python tests/build_fixtures.py

Writes tests/fixtures/:
    plans/<file>.xlsx     every plan file currently configured, lecturer names replaced
    timetable.xlsx        exam timetable, lecturer names replaced
    plans.json            plan configuration (as produced by the scanner)

Names are replaced consistently across all files ("dr Jan Kowalski" becomes
the same "dr Jan Nazwiskoab" everywhere), titles and layout stay intact, so
merged cells, sub-columns and typos in the sheets are preserved.
Then run tests/update_golden.py to refresh the expected results.
"""
import io
import json
import os
import re
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import openpyxl  # noqa: E402
import requests  # noqa: E402
from pymongo import MongoClient  # noqa: E402

import exam_schedule  # noqa: E402
from moodle_scanner import login_puw  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')

# Titles are typed with or without the dot ("mgr inż arch")
TITLE = r'(?:prof|dr hab|dr n\. med|hab|dr|mgr|inż|arch|lek)\.?'
UPPER = 'A-ZĄĆĘŁŃÓŚŹŻ'
LOWER = 'a-ząćęłńóśźż'
NAME = rf'[{UPPER}][{LOWER}]+(?:-[{UPPER}][{LOWER}]+)?'
PERSON = re.compile(rf'((?:{TITLE}\s+)+)((?:{NAME}\s+){{1,2}}{NAME})')

_aliases = {}


def _pseudonym(full_name):
    if full_name not in _aliases:
        n = len(_aliases)
        suffix = ''
        while True:
            suffix = chr(ord('a') + n % 26) + suffix
            n //= 26
            if not n:
                break
        _aliases[full_name] = f'Jan Nazwisko{suffix}'
    return _aliases[full_name]


def anonymise(text):
    return PERSON.sub(lambda m: m.group(1) + _pseudonym(m.group(2)), text)


def anonymise_workbook(content):
    wb = openpyxl.load_workbook(io.BytesIO(content))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and not cell.value.startswith('='):
                    cell.value = anonymise(cell.value)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def main():
    db = MongoClient(os.getenv('MONGO_URI'))[os.getenv('MONGO_DB')]
    plans = (db.plans_config.find_one({'_id': 'plans_json'}) or {}).get('plans') or {}
    if not plans:
        sys.exit('No plans configured - run the Moodle scanner first')

    session = requests.Session()
    if not login_puw(session):
        sys.exit('PUW login failed - check EMAIL/PASSWORD')

    os.makedirs(os.path.join(FIXTURES, 'plans'), exist_ok=True)
    files, config = {}, {}
    for plan_id, plan in sorted(plans.items()):
        url = plan['download_url']
        filename = unquote(url.split('/')[-1].split('?')[0])
        if url not in files:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            with open(os.path.join(FIXTURES, 'plans', filename), 'wb') as f:
                f.write(anonymise_workbook(resp.content))
            files[url] = filename
            print(f'  {filename}')
        config[plan_id] = {**plan, 'download_url': f'fixture://{filename}'}

    with open(os.path.join(FIXTURES, 'plans.json'), 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=1, sort_keys=True)

    timetables = exam_schedule.find_timetable_files(session)
    if timetables:
        year = sorted(timetables)[-1]
        resp = session.get(timetables[year], timeout=60)
        with open(os.path.join(FIXTURES, 'timetable.xlsx'), 'wb') as f:
            f.write(anonymise_workbook(resp.content))
        print(f'  timetable {year}')

    print(f'{len(files)} files, {len(config)} plans, {len(_aliases)} people anonymised')


if __name__ == '__main__':
    main()
