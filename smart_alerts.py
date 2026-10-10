"""Pure Smart Alert rules and simulator for AI Stock Intelligence.

V1.8.29 adds a deterministic alert policy that can be reused by the worker and
by the dashboard simulator without changing the real alert-state database.
"""
from typing import Any, Dict, List

TOP_N = 5
DEFAULT_SCORE_DELTA = 7.0
DEFAULT_OUTSIDE_SCORE = 75.0


def _s(v, default=''):
    if v is None:
        return default
    return str(v)


def _f(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except Exception:
        return None


def _i(v):
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def evaluate_transition(old: Dict[str, Any] | None, cur: Dict[str, Any], *,
                        top_n: int = TOP_N,
                        score_delta: float = DEFAULT_SCORE_DELTA,
                        outside_score: float = DEFAULT_OUTSIDE_SCORE) -> List[Dict[str, Any]]:
    """Compare two ticker snapshots and return material alert events.

    This function is intentionally side-effect free. No database writes, no
    notification delivery, and no market calls happen here.
    """
    if not old:
        return []

    t = _s(cur.get('ticker')).upper()
    changes: List[Dict[str, Any]] = []

    old_dec, cur_dec = _s(old.get('decision')), _s(cur.get('decision'))
    old_risk, cur_risk = _s(old.get('risk')), _s(cur.get('risk'))
    old_entry, cur_entry = _s(old.get('entry_status'), 'NEUTRAL'), _s(cur.get('entry_status'), 'NEUTRAL')
    old_hold, cur_hold = _s(old.get('hold_state'), 'UNKNOWN'), _s(cur.get('hold_state'), 'UNKNOWN')
    old_ov, cur_ov = _s(old.get('overnight_state'), 'UNKNOWN'), _s(cur.get('overnight_state'), 'UNKNOWN')
    old_next, cur_next = _s(old.get('next_session_bias'), 'UNKNOWN'), _s(cur.get('next_session_bias'), 'UNKNOWN')
    old_rank, cur_rank = _i(old.get('opportunity_rank')), _i(cur.get('opportunity_rank'))
    old_rel, cur_rel = _i(old.get('relative_rank')), _i(cur.get('relative_rank'))
    old_score, cur_score = _f(old.get('ai_score')), _f(cur.get('ai_score'))
    old_opp, cur_opp = _f(old.get('opportunity_score')), _f(cur.get('opportunity_score'))

    def add(alert_type, title, detail, old_value, new_value, severity='MEDIUM'):
        changes.append({
            'ticker': t,
            'company': _s(cur.get('company'), t),
            'severity': severity,
            'alert_type': alert_type,
            'title': title,
            'detail': detail,
            'old_value': _s(old_value),
            'new_value': _s(new_value),
        })

    # 1) Dynamic Top-5 membership. Top-5 means opportunity rank, not relative universe rank.
    entered_top5 = cur_rank is not None and cur_rank <= top_n and (old_rank is None or old_rank > top_n)
    if entered_top5:
        sev = 'HIGH' if cur_entry in ('ACTIVE', 'INVALIDATED') or (cur_opp is not None and cur_opp >= outside_score) else 'MEDIUM'
        add('TOP_5_ENTRY', 'Masuk Dynamic Top-5',
            f'Opportunity Rank #{old_rank if old_rank is not None else "—"} → #{cur_rank}',
            f'#{old_rank if old_rank is not None else "—"}', f'#{cur_rank}', sev)

    # 2) Material opportunity outside Top-5. A stock can alert even when rank > 5.
    outside_now = cur_rank is not None and cur_rank > top_n
    if outside_now:
        crossed_score = cur_opp is not None and cur_opp >= outside_score and (old_opp is None or old_opp < outside_score)
        entry_breakout = cur_entry == 'ACTIVE' and old_entry != 'ACTIVE' and (cur_opp is None or cur_opp >= outside_score - 3)
        score_jump = cur_opp is not None and old_opp is not None and (cur_opp - old_opp) >= 10 and cur_opp >= outside_score - 3
        if crossed_score or entry_breakout or score_jump:
            reason = []
            if crossed_score: reason.append(f'Opportunity Score menembus {outside_score:.0f}')
            if entry_breakout: reason.append('Entry Window menjadi ACTIVE')
            if score_jump: reason.append('kenaikan Opportunity Score ≥ 10')
            add('OUTSIDE_TOP5_OPPORTUNITY', 'Peluang kuat di luar Top-5',
                f'Rank #{cur_rank} • Opportunity Score {cur_opp:.1f} • ' + ' • '.join(reason),
                f'{old_opp:.1f}' if old_opp is not None else '—',
                f'{cur_opp:.1f}' if cur_opp is not None else '—', 'HIGH')

    # 3) Entry window / decision / risk changes.
    if old_entry != cur_entry:
        sev = 'HIGH' if cur_entry in ('ACTIVE', 'INVALIDATED') or old_entry == 'ACTIVE' else 'MEDIUM'
        add('ENTRY_WINDOW', 'Entry Window berubah', f'{old_entry} → {cur_entry}', old_entry, cur_entry, sev)

    if old_dec != cur_dec:
        sev = 'HIGH' if cur_dec in ('BUY', 'SELL/AVOID') or old_dec in ('BUY', 'SELL/AVOID') else 'MEDIUM'
        add('DECISION', 'Decision berubah', f'{old_dec} → {cur_dec}', old_dec, cur_dec, sev)

    if old_risk != cur_risk:
        worsening = (old_risk, cur_risk) in {('LOW', 'MEDIUM'), ('LOW', 'HIGH'), ('MEDIUM', 'HIGH')}
        sev = 'HIGH' if worsening else 'MEDIUM'
        add('RISK', 'Risk berubah', f'{old_risk} → {cur_risk}', old_risk, cur_risk, sev)

    # 4) Material AI score change.
    if old_score is not None and cur_score is not None and abs(cur_score - old_score) >= score_delta:
        d = cur_score - old_score
        add('AI_SCORE', 'AI Score berubah signifikan',
            f'{old_score:.1f} → {cur_score:.1f} ({d:+.1f})', f'{old_score:.1f}', f'{cur_score:.1f}',
            'HIGH' if abs(d) >= 10 else 'MEDIUM')

    # 5) Large universe-relative rank jump.
    if old_rel is not None and cur_rel is not None and abs(cur_rel - old_rel) >= 50:
        add('RANK', 'Relative Rank berubah signifikan',
            f'#{old_rel} → #{cur_rel}', f'#{old_rel}', f'#{cur_rel}', 'MEDIUM')

    if old_hold != cur_hold and cur_hold != 'UNKNOWN':
        sev = 'HIGH' if cur_hold in ('EXIT WATCH', 'INVALIDATED', 'REDUCE', 'SELL') else 'MEDIUM'
        add('HOLD', 'Hold State berubah', f'{old_hold} → {cur_hold}', old_hold, cur_hold, sev)

    if old_ov != cur_ov and cur_ov != 'UNKNOWN':
        add('OVERNIGHT', 'Overnight state berubah', f'{old_ov} → {cur_ov}', old_ov, cur_ov,
            'HIGH' if cur_ov == 'INVALIDATED' else 'MEDIUM')

    if old_next != cur_next and cur_next != 'UNKNOWN':
        add('NEXT_SESSION', 'Next Session berubah', f'{old_next} → {cur_next}', old_next, cur_next,
            'HIGH' if cur_next in ('NEGATIVE', 'BEARISH') else 'MEDIUM')

    return changes


def simulator_cases() -> List[Dict[str, Any]]:
    """Three acceptance tests agreed for V1.8.29."""
    base = {
        'company': 'Synthetic Test Stock', 'decision': 'WATCH', 'ai_score': 64.0,
        'risk': 'HIGH', 'entry_status': 'WAIT', 'hold_state': 'REVIEW',
        'overnight_state': 'PENDING_CLOSE', 'next_session_bias': 'NEUTRAL',
        'relative_rank': 8, 'opportunity_rank': 8, 'opportunity_score': 64.0,
        'invalidation': 'None', 'ticker': 'SIM1'
    }
    cases = []
    old = dict(base)
    cur = dict(base, ticker='KDTN', opportunity_rank=3, ai_score=79.0,
               opportunity_score=79.0, entry_status='ACTIVE', risk='MEDIUM', relative_rank=3)
    cases.append({'name': 'TOP_5_ENTRY', 'old': dict(old, ticker='KDTN'), 'cur': cur,
                  'expected': True, 'expected_type': 'TOP_5_ENTRY'})

    old2 = dict(base, ticker='SIM2', relative_rank=37, opportunity_rank=37,
                ai_score=58.0, opportunity_score=58.0)
    cur2 = dict(old2, ai_score=81.0, opportunity_score=81.0, entry_status='ACTIVE', risk='MEDIUM')
    cases.append({'name': 'OUTSIDE_TOP5_OPPORTUNITY', 'old': old2, 'cur': cur2,
                  'expected': True, 'expected_type': 'OUTSIDE_TOP5_OPPORTUNITY'})

    old3 = dict(base, ticker='SIM3')
    cur3 = dict(old3, ticker='SIM3', opportunity_rank=7, ai_score=65.0,
                opportunity_score=65.0, relative_rank=7)
    cases.append({'name': 'MINOR_CHANGE', 'old': old3, 'cur': cur3,
                  'expected': False, 'expected_type': 'NO_ALERT'})
    return cases


def run_simulator() -> List[Dict[str, Any]]:
    rows = []
    for case in simulator_cases():
        alerts = evaluate_transition(case['old'], case['cur'])
        types = [a['alert_type'] for a in alerts]
        passed = (bool(alerts) == case['expected']) and (
            case['expected'] is False or case['expected_type'] in types
        )
        rows.append({
            'Scenario': case['name'],
            'Expected': case['expected_type'],
            'Actual': ', '.join(types) if types else 'NO_ALERT',
            'Alert Count': len(alerts),
            'Result': 'PASS' if passed else 'FAIL',
            'Priority': ', '.join(sorted({a['severity'] for a in alerts})) if alerts else '—'
        })
    return rows
