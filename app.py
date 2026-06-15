from flask import Flask, render_template, jsonify
import requests
from datetime import datetime, timedelta
import os

app = Flask(__name__)

TRELLO_KEY   = os.environ.get('TRELLO_KEY',   '')
TRELLO_TOKEN = os.environ.get('TRELLO_TOKEN', '')
BOARD_ID     = 'BSMbxCEC'

TEAM = {
    'Даша':  {'work': 'Задачи Даша',    'backlog': 'Backlog_Даша'},
    'Алина': {'work': 'Алина_в работе', 'backlog': 'Backlog_Алина'},
    'Елена': {'work': 'Елена_в работе', 'backlog': 'Елена_Backlog'},
    'Люба':  {'work': 'Задачи Люба',    'backlog': None},
}

def trello(path, **kw):
    r = requests.get(
        f'https://api.trello.com/1{path}',
        params={'key': TRELLO_KEY, 'token': TRELLO_TOKEN, **kw},
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

def fetch_data():
    lists = trello(
        f'/boards/{BOARD_ID}/lists',
        cards='open',
        card_fields='name,dueComplete'
    )
    lmap = {l['name']: l for l in lists}

    week_start, week_end = get_week_range()

    # Данные по каждому сотруднику
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

    # Итоги недели — из колонки(ок) "Неделя"
    # Всё что туда перенесли = выполнено командой за неделю
    week_done_cards = []
    week_list_name  = None
    for lname, ldata in lmap.items():
        if 'неделя' in lname.lower():
            week_done_cards += ldata.get('cards', [])
            week_list_name = lname

    # Всего планировалось = все задачи в работе + уже выполненные + перенесённые в неделю
    total_planned = (
        sum(d['total_work'] for d in team.values()) +
        len(week_done_cards)
    )
    week_done_count = len(week_done_cards)
    pct = int(week_done_count / total_planned * 100) if total_planned else 0

    week = {
        'name':     f'{week_start.strftime("%d.%m")} — {week_end.strftime("%d.%m")}',
        'done':     week_done_count,
        'planned':  total_planned,
        'remain':   total_planned - week_done_count,
        'pct':      pct,
        'cards':    [c['name'] for c in week_done_cards],
        'list_name': week_list_name,
    }

    return {
        'team':    team,
        'week':    week,
        'updated': datetime.now().strftime('%d.%m.%Y в %H:%M'),
    }

@app.route('/')
def index():
    try:
        data  = fetch_data()
        error = None
    except Exception as e:
        data  = {}
        error = str(e)
    return render_template('index.html', data=data, error=error)

@app.route('/api')
def api():
    try:
        return jsonify(fetch_data())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
