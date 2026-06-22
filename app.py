from flask import Flask, render_template, jsonify
import requests
from datetime import datetime, timedelta
import os

app = Flask(__name__)

TRELLO_KEY   = os.environ.get('TRELLO_KEY',   '')
TRELLO_TOKEN = os.environ.get('TRELLO_TOKEN', '')
BOARD_ID     = 'BSMbxCEC'

AMO_TOKEN    = os.environ.get('AMO_TOKEN', '')
AMO_DOMAIN   = 'infogrowbccom.amocrm.ru'
AMO_PIPELINE = 7514314  # Продажи

TEAM = {
    'Даша':  {'work': 'Задачи Даша',    'backlog': 'Backlog_Даша'},
    'Алина': {'work': 'Алина_в работе', 'backlog': 'Backlog_Алина'},
    'Елена': {'work': 'Елена_в работе', 'backlog': 'Елена_Backlog'},
    'Люба':  {'work': 'Задачи Люба',    'backlog': None},
}

# Системные статусы AmoCRM (закрытые сделки — есть в каждой воронке)
AMO_CLOSED_STATUSES = {142: 'Успешно реализовано', 143: 'Закрыто и не реализовано'}

def trello(path, **kw):
    r = requests.get(
        f'https://api.trello.com/1{path}',
        params={'key': TRELLO_KEY, 'token': TRELLO_TOKEN, **kw},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def amo(path, **params):
    r = requests.get(
        f'https://{AMO_DOMAIN}/api/v4{path}',
        headers={'Authorization': f'Bearer {AMO_TOKEN}'},
        params=params,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def get_week_range():
    today  = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return (
        monday.replace(hour=0,  minute=0,  second=0,  microsecond=0),
        sunday.replace(hour=23, minute=59, second=59, microsecond=999999),
    )

# ── Trello ────────────────────────────────────────────────
def fetch_trello():
    lists = trello(
        f'/boards/{BOARD_ID}/lists',
        cards='open',
        card_fields='name,dueComplete'
    )
    lmap = {l['name']: l for l in lists}

    week_start, week_end = get_week_range()

    team = {}
    for name, cfg in TEAM.items():
        wl = lmap.get(cfg['work'], {})
        bl = lmap.get(cfg['backlog'] or '', {})
        wc = wl.get('cards', [])
        bc = bl.get('cards', [])

        done_cards   = [c for c in wc if c.get('dueComplete')]
        active_cards = [c for c in wc if not c.get('dueComplete')]

        team[name] = {
            'done':          [c['name'] for c in done_cards],
            'done_count':    len(done_cards),
            'work':          [c['name'] for c in active_cards],
            'work_count':    len(active_cards),
            'total_work':    len(wc),
            'backlog':       [c['name'] for c in bc],
            'backlog_count': len(bc),
        }

    # Колонка "Неделя"
    week_done_cards = []
    week_list_name  = None
    for lname, ldata in lmap.items():
        if 'неделя' in lname.lower():
            week_done_cards += ldata.get('cards', [])
            week_list_name = lname

    total_planned = (
        sum(d['total_work'] for d in team.values()) +
        len(week_done_cards)
    )
    week_done_count = len(week_done_cards)
    pct = int(week_done_count / total_planned * 100) if total_planned else 0

    week = {
        'name':      f'{week_start.strftime("%d.%m")} — {week_end.strftime("%d.%m")}',
        'done':      week_done_count,
        'planned':   total_planned,
        'remain':    total_planned - week_done_count,
        'pct':       pct,
        'cards':     [c['name'] for c in week_done_cards],
        'list_name': week_list_name,
    }

    return team, week

# ── AmoCRM ────────────────────────────────────────────────
def amo_get_all(path, **params):
    """Загружает все страницы из AmoCRM API"""
    all_items = []
    page = 1
    while True:
        try:
            data  = amo(path, page=page, limit=250, **params)
            items = data.get('_embedded', {}).get(path.strip('/').split('/')[-1], [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < 250:
                break
            page += 1
        except Exception:
            break
    return all_items

def fetch_pipelines():
    """Загружает все воронки и их этапы, возвращает карту status_id -> info"""
    status_map = {}      # status_id -> {'name', 'pipeline_id', 'pipeline_name', 'sort'}
    pipeline_list = []   # [(pipeline_id, pipeline_name, sort)]

    page = 1
    while True:
        try:
            data      = amo('/leads/pipelines', page=page, limit=250)
            pipelines = data.get('_embedded', {}).get('pipelines', [])
            if not pipelines:
                break
            for p in pipelines:
                pid   = p['id']
                pname = p['name']
                psort = p.get('sort', 999)
                pipeline_list.append((pid, pname, psort))
                for s in p.get('_embedded', {}).get('statuses', []):
                    status_map[s['id']] = {
                        'name':          s['name'],
                        'pipeline_id':   pid,
                        'pipeline_name': pname,
                        'sort':          s.get('sort', 999),
                    }
            if len(pipelines) < 250:
                break
            page += 1
        except Exception:
            break

    # Добавляем системные статусы (закрытые), они не всегда приходят в списке
    for sid, sname in AMO_CLOSED_STATUSES.items():
        if sid not in status_map:
            status_map[sid] = {
                'name': sname, 'pipeline_id': None,
                'pipeline_name': 'Системные', 'sort': 9999,
            }

    pipeline_list.sort(key=lambda x: x[2])
    return status_map, pipeline_list


def fetch_crm():
    if not AMO_TOKEN:
        return None

    try:
        from collections import Counter, defaultdict

        now_ts      = int(datetime.now().timestamp())
        month_start = int(datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

        # 1. Загружаем карту всех этапов из AmoCRM
        status_map, pipeline_list = fetch_pipelines()

        # 2. Все лиды по всем воронкам
        all_leads   = amo_get_all('/leads')
        grand_total = len(all_leads)

        # 3. Активные в воронке Продажи
        active_total = sum(1 for l in all_leads if l.get('pipeline_id') == AMO_PIPELINE)

        # 4. Новые за текущий месяц
        new_this_month = sum(
            1 for l in all_leads
            if l.get('created_at', 0) >= month_start
        )

        # 5. Воронка — все лиды по всем воронкам, сгруппированные по этапам
        counts_by_status = Counter(l['status_id'] for l in all_leads)

        # Строим секции по воронкам
        CLOSED = {142, 143}
        funnel_sections = []
        for pid, pname, _ in pipeline_list:
            # Все статусы этой воронки с лидами
            stages = []
            for sid, info in sorted(
                ((k, v) for k, v in status_map.items() if v['pipeline_id'] == pid),
                key=lambda x: x[1]['sort']
            ):
                cnt = counts_by_status.get(sid, 0)
                if cnt > 0:
                    stages.append({'name': info['name'], 'count': cnt, 'closed': sid in CLOSED})
            if stages:
                max_cnt = max(s['count'] for s in stages)
                for s in stages:
                    s['pct'] = int(s['count'] / max_cnt * 100)
                total_in_pipeline = sum(s['count'] for s in stages)
                funnel_sections.append({
                    'pipeline': pname,
                    'stages':   stages,
                    'total':    total_in_pipeline,
                })

        # Лиды с неизвестным статусом (если есть)
        unknown_count = sum(
            cnt for sid, cnt in counts_by_status.items()
            if sid not in status_map
        )

        # 6. Зависшие лиды — только по релевантным этапам
        # Исключаем архивные и технические этапы
        STUCK_EXCLUDE = {
            'перенесла в битрикс',
            'архив',
            'резидент',
            '3 касания без вовлечения',
        }

        stuck_by_stage = defaultdict(list)
        for l in all_leads:
            if l['status_id'] in CLOSED:
                continue
            days = (now_ts - l.get('updated_at', now_ts)) // 86400
            if days >= 7:
                info       = status_map.get(l['status_id'])
                stage_name = info['name'] if info else f'Этап {l["status_id"]}'
                if stage_name.lower() in STUCK_EXCLUDE:
                    continue
                stuck_by_stage[stage_name].append(days)

        stuck_summary = []
        total_stuck   = 0
        for stage_name, days_list in sorted(stuck_by_stage.items(), key=lambda x: -len(x[1])):
            cnt      = len(days_list)
            total_stuck += cnt
            min_days, max_days = min(days_list), max(days_list)
            days_str = f'{min_days} дней' if min_days == max_days else f'{min_days}–{max_days} дней'
            stuck_summary.append({'stage': stage_name, 'count': cnt, 'days': days_str})

        return {
            'grand_total':     grand_total,
            'active_total':    active_total,
            'new_month':       new_this_month,
            'stuck_count':     total_stuck,
            'stuck_summary':   stuck_summary,
            'funnel_sections': funnel_sections,
            'unknown_count':   unknown_count,
            'month_name':      ['январе','феврале','марте','апреле','мае','июне',
                                'июле','августе','сентябре','октябре','ноябре','декабре'][datetime.now().month-1],
        }
    except Exception as e:
        return {'error': str(e)}

# ── Routes ────────────────────────────────────────────────
@app.route('/')
def index():
    try:
        team, week = fetch_trello()
        crm        = fetch_crm()
        error      = None
    except Exception as e:
        team, week, crm = {}, {}, None
        error = str(e)
    return render_template('index.html',
        team=team, week=week, crm=crm,
        error=error,
        updated=datetime.now().strftime('%d.%m.%Y в %H:%M')
    )

@app.route('/api')
def api():
    try:
        team, week = fetch_trello()
        crm        = fetch_crm()
        return jsonify({'team': team, 'week': week, 'crm': crm})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
