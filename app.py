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

# Этапы воронки в нужном порядке
AMO_STAGES = [
    (64714326, 'Новая заявка'),
    (62300030, 'Первичный контакт'),
    (85067538, '3 касания без вовлечения'),
    (83725106, 'Заполнил анкету / на МП'),
    (83229010, 'Назначен созвон'),
    (62300034, 'Проведён созвон'),
    (86126246, 'Записан на МП'),
    (63785746, 'Посетил МП'),
    (63785750, 'Посетил 2-е МП'),
    (69216118, 'Думает / завис'),
    (63785754, 'Вступление / счёт'),
]
STAGE_IDS = {sid: name for sid, name in AMO_STAGES}

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
def fetch_crm():
    if not AMO_TOKEN:
        return None

    try:
        # Получаем все активные лиды из воронки Продажи
        data  = amo('/leads', **{
            'filter[pipeline_id]': AMO_PIPELINE,
            'limit': 250,
        })
        leads = data.get('_embedded', {}).get('leads', [])
        total = data.get('_total_items', 0)

        # Считаем по этапам
        from collections import Counter
        counts = Counter(l['status_id'] for l in leads)

        # Строим воронку
        funnel = []
        max_count = max(counts.values()) if counts else 1
        for sid, sname in AMO_STAGES:
            cnt = counts.get(sid, 0)
            funnel.append({
                'name':  sname,
                'count': cnt,
                'pct':   int(cnt / max_count * 100) if max_count else 0,
            })

        # Зависшие лиды (более 7 дней без изменений)
        week_ago = int((datetime.now() - timedelta(days=7)).timestamp())
        stuck = [
            l for l in leads
            if l.get('updated_at', 0) < week_ago
            and l['status_id'] not in (143, 142)  # не закрытые
        ]

        # Новые лиды за текущий месяц
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        new_this_month = [
            l for l in leads
            if l.get('created_at', 0) >= int(month_start.timestamp())
        ]

        return {
            'total':          total,
            'active':         len(leads),
            'new_month':      len(new_this_month),
            'stuck_count':    len(stuck),
            'funnel':         funnel,
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
