"""Shared helpers for the fixture-based tests (no PUW, no MongoDB, no network)."""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
GOLDEN_PATH = os.path.join(FIXTURES, 'golden.json')


def load_plans():
    with open(os.path.join(FIXTURES, 'plans.json'), encoding='utf-8') as f:
        return json.load(f)


def fixture_path(plan_cfg):
    return os.path.join(FIXTURES, 'plans', plan_cfg['download_url'].replace('fixture://', ''))


def fixture_bytes(plan_cfg):
    with open(fixture_path(plan_cfg), 'rb') as f:
        return f.read()


def load_golden():
    with open(GOLDEN_PATH, encoding='utf-8') as f:
        return json.load(f)


def generate(plan_cfg):
    """Run the sheet generator exactly like the backend does, without DB/network."""
    from LessonPlan import LessonPlan
    lp = LessonPlan.__new__(LessonPlan)
    lp.plan_config = plan_cfg
    lp.sheet_name = plan_cfg['sheet_name']
    groups = plan_cfg.get('groups') or {}
    lp.groups = {k: (v['identifier'] if isinstance(v, dict) else v) for k, v in groups.items()} or {'cały kierunek': 'all'}
    lp.last_notes = None
    html = lp._generate_groups_html(fixture_path(plan_cfg))
    return html, lp.last_notes


def summarize(plan_cfg):
    """Compact, stable description of the generated plan - what golden.json stores."""
    import plan_reconciler as pr
    from plan_naming import describe_plan

    groups, notes = generate(plan_cfg)
    result = pr.summarize(pr.reconcile_plan(
        'fixture', plan_cfg, fixture_bytes(plan_cfg), {'groups': groups or {}, 'checksum': 'fixture', 'parser_version': 99}))
    out_groups = {}
    for name, html in sorted((groups or {}).items()):
        days = re.findall(r'<th><b>(.*?)</b></th>', html)[1:]
        out_groups[name] = {
            'days': days,
            'rows': html.count('<tr>') - 1,
            'lessons': sum(1 for c in re.findall(r'<td>(.*?)</td>', html, re.S) if c.strip() and '<sup>' not in c),
            'sha1': hashlib.sha1(html.encode()).hexdigest(),
        }
    naming = describe_plan(plan_cfg['name'])
    return {
        'short_name': naming['short_name'],
        'validation': result['status'],
        'coverage': result['coverage'],
        'groups': out_groups,
        'exams': (notes or {}).get('exams', []),
        'info_blocks': len((notes or {}).get('info', [])),
    }
