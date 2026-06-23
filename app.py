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

RESIDENTS_SHEET_ID = '1FCRtmU9D9YeT9xHRAwEqiW5jgA-EcjyE7YBW3-E71qs'
PAYMENTS_SHEET_ID  = '1h4zh7mFTKyfGUWOsyk1us9EfP379qd2P'
PAYMENTS_GID       = '1169641494'

MEMBERSHIP_WEIGHTS = {
    'Резидент':          1.0,
    '1/2 Резидент':      0.5,
    '1/2 Резидент Women': 0.5,
}

# Квартальные цели по платящим резидентам
QUARTERLY_GOALS = {
    1: 45,   # Q1: январь–март
    2: 55,   # Q2: апрель–июнь
    3: 62,   # Q3: июль–сентябрь
    4: 72,   # Q4: октябрь–декабрь
}

# Посещаемость — ежемесячные файлы (сводная вкладка gid)
ATTENDANCE_SHEETS = {
    'Январь':  {'id': '1wWrJNqfCvmWecNHFOHQ8Gksft5SCQsogdDMy8d3VwIM', 'gid': '411480352'},
    'Февраль': {'id': '1XKCtDfw2obueBK3HJ9UXlc-5siKE4mphfiXYyixWHo8', 'gid': '411480352'},
    'Март':    {'id': '1R5q0WvnrsS6q8aucal3PGK8KQCNeVFHFQ9xENctgNPQ', 'gid': '411480352'},
    'Апрель':  {'id': '135D51Qubab4lGFy9Vp5a41-ziX_YlLC5nG-xE6_X2NA', 'gid': '411480352'},
    'Май':     {'id': '15__VbyyNTHXFWufA6pS_BOj6TtKSD2ERYnUUkm6xeck', 'gid': '411480352'},
    'Июнь':    {'id': '18TCohHjM2GPkn430uc4h0A0AGMRRYS8MUTT65JJiBDI', 'gid': '411480352'},
    'Июль':    {'id': '18uIkcV9pRs7-xvSFdMBc8KRQS5ymkmQ_PTnqFHehrnk', 'gid': '1364402557'},
}
ANNUAL_SHEET_ID = '1P-r6Q7uZ9aovbBFZqV17iHsRqTdT3m3BHxg6_BMY3Y8'
ANNUAL_GID      = '2092514904'
MONTH_NAMES_RU  = ['Январь','Февраль','Март','Апрель','Май','Июнь',
                   'Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']

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
        card_fields='name,dueComplete,due'
    )
    lmap = {l['name']: l for l in lists}

    week_start, week_end = get_week_range()

    # Помощник: карточка попадает в эту неделю по дедлайну?
    def due_this_week(card):
        due = card.get('due')
        if not due:
            return False
        try:
            due_dt = datetime.fromisoformat(due.replace('Z', '+00:00')).replace(tzinfo=None)
            return week_start <= due_dt <= week_end
        except Exception:
            return False

    team = {}
    for name, cfg in TEAM.items():
        wl = lmap.get(cfg['work'], {})
        bl = lmap.get(cfg['backlog'] or '', {})
        wc = wl.get('cards', [])
        bc = bl.get('cards', [])

        # Статус по сотруднику — ВСЕ карточки в рабочей колонке
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

    # Прогресс недели — только карточки с дедлайном НА ЭТУ НЕДЕЛЮ
    week_work_cards = []
    for name, cfg in TEAM.items():
        wl = lmap.get(cfg['work'], {})
        for c in wl.get('cards', []):
            if due_this_week(c):
                week_work_cards.append(c)

    # Колонка "Неделя" — перенесённые завершённые
    week_done_cards = []
    week_list_name  = None
    for lname, ldata in lmap.items():
        if 'неделя' in lname.lower():
            week_done_cards += ldata.get('cards', [])
            week_list_name = lname

    # Подсчёт прогресса
    done_in_work    = [c for c in week_work_cards if c.get('dueComplete')]
    week_done_count = len(done_in_work) + len(week_done_cards)
    week_total      = len(week_work_cards) + len(week_done_cards)
    pct = int(week_done_count / week_total * 100) if week_total else 0

    week = {
        'name':      f'{week_start.strftime("%d.%m")} — {week_end.strftime("%d.%m")}',
        'done':      week_done_count,
        'planned':   week_total,
        'remain':    week_total - week_done_count,
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

        # 1. Загружаем карту этапов
        status_map, _ = fetch_pipelines()

        # 2. Только лиды из воронки Продажи
        leads  = amo_get_all('/leads', **{'filter[pipeline_id]': AMO_PIPELINE})
        CLOSED = {142, 143}

        # Активные (без закрытых)
        active_leads = [l for l in leads if l['status_id'] not in CLOSED]
        active_total = len(active_leads)

        # 3. Новые за текущий месяц
        new_this_month = sum(
            1 for l in active_leads
            if l.get('created_at', 0) >= month_start
        )

        # 4. Воронка — только этапы Продажи
        counts_by_status = Counter(l['status_id'] for l in active_leads)

        prodazhi_stages = []
        for sid, info in sorted(
            ((k, v) for k, v in status_map.items() if v['pipeline_id'] == AMO_PIPELINE),
            key=lambda x: x[1]['sort']
        ):
            cnt = counts_by_status.get(sid, 0)
            if cnt > 0:
                prodazhi_stages.append({'name': info['name'], 'count': cnt, 'closed': False})

        if prodazhi_stages:
            max_cnt = max(s['count'] for s in prodazhi_stages)
            for s in prodazhi_stages:
                s['pct'] = int(s['count'] / max_cnt * 100)

        funnel_sections = [{
            'pipeline': 'Воронка продаж',
            'stages':   prodazhi_stages,
            'total':    active_total,
        }]

        # 5. Зависшие — только из Продажи
        STUCK_EXCLUDE = {
            'резидент',
            '3 касания без вовлечения',
        }

        stuck_by_stage = defaultdict(list)
        for l in active_leads:
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
            'grand_total':     active_total,
            'active_total':    active_total,
            'new_month':       new_this_month,
            'stuck_count':     total_stuck,
            'stuck_summary':   stuck_summary,
            'funnel_sections': funnel_sections,
            'unknown_count':   0,
            'month_name':      ['январе','феврале','марте','апреле','мае','июне',
                                'июле','августе','сентябре','октябре','ноябре','декабре'][datetime.now().month-1],
        }
    except Exception as e:
        return {'error': str(e)}

# ── Residents ─────────────────────────────────────────────
def fetch_gsheet_csv(sheet_id, sheet_name=None, gid=None):
    import csv, io
    params = {'tqx': 'out:csv'}
    if sheet_name:
        params['sheet'] = sheet_name
    if gid:
        params['gid'] = gid
    r = requests.get(
        f'https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq',
        params=params,
        timeout=15
    )
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    return list(reader)

def fetch_residents():
    try:
        from collections import Counter

        # ── Резиденты ──
        rows = fetch_gsheet_csv(RESIDENTS_SHEET_ID, 'Резиденты')

        # Найдём колонку со статусом (содержит слово «статус»)
        status_col = next(
            (k for k in (rows[0].keys() if rows else []) if 'статус' in k.lower()),
            None
        )
        name_col = next(
            (k for k in (rows[0].keys() if rows else []) if 'имя' in k.lower() or 'фамили' in k.lower()),
            None
        )

        # Считаем резидентов, исключая «Вышел»
        active_rows = [r for r in rows if r.get(status_col, '').strip() != 'Вышел' and r.get(name_col, '').strip()]
        total = len(active_rows)

        # Разбивка по статусам
        status_counts = Counter(r.get(status_col, '').strip() for r in active_rows)

        # ── Дни рождения этой недели ──
        bday_rows = fetch_gsheet_csv(RESIDENTS_SHEET_ID, 'Д/Р')

        today      = datetime.now()
        week_start = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0)
        week_end   = week_start + timedelta(days=6, hours=23, minutes=59)

        birthdays_week = []
        for b in bday_rows:
            day_month = b.get('День.Месяц', '').strip()
            name      = b.get('Фамилия и Имя', '').strip()
            if not day_month or not name or '.' not in day_month:
                continue
            try:
                parts = day_month.split('.')
                day   = int(parts[0])
                month = int(parts[1])
                bday  = datetime(today.year, month, day)
                if week_start.date() <= bday.date() <= week_end.date():
                    months_ru = ['января','февраля','марта','апреля','мая','июня',
                                 'июля','августа','сентября','октября','ноября','декабря']
                    birthdays_week.append({
                        'name': name,
                        'day':  day,
                        'date': f'{day} {months_ru[month-1]}',
                    })
            except Exception:
                continue

        birthdays_week.sort(key=lambda x: x['day'])

        # ── Платящие резиденты (из таблицы оплат) ──
        pay_rows = fetch_gsheet_csv(PAYMENTS_SHEET_ID, gid=PAYMENTS_GID)

        total_paying  = 0.0
        total_paid_ok = 0.0

        for row in pay_rows:
            status     = row.get('Статус', '').strip()
            membership = row.get('Членство', '').strip()
            pay_status = row.get('Статус оплаты', '').strip().upper()

            if status != 'Active':
                continue

            weight = MEMBERSHIP_WEIGHTS.get(membership, 0)
            if weight == 0:
                continue

            total_paying += weight
            if pay_status == 'OK':
                total_paid_ok += weight

        def fmt(n):
            return int(n) if n == int(n) else n

        # ── KPI по кварталам ──
        current_q    = (datetime.now().month - 1) // 3 + 1
        current_goal = QUARTERLY_GOALS[current_q]
        gap          = current_goal - total_paid_ok
        pct_to_goal  = int(min(total_paid_ok / current_goal * 100, 100)) if current_goal else 0

        quarters = [
            {'q': q, 'name': name, 'goal': QUARTERLY_GOALS[q],
             'current': q == current_q}
            for q, name in [
                (1, 'Q1 · янв–мар'), (2, 'Q2 · апр–июн'),
                (3, 'Q3 · июл–сен'), (4, 'Q4 · окт–дек'),
            ]
        ]

        return {
            'total':          total,
            'status_counts':  dict(status_counts),
            'birthdays_week': birthdays_week,
            'paying':         fmt(total_paying),
            'paid_ok':        fmt(total_paid_ok),
            'not_paid':       fmt(total_paying - total_paid_ok),
            'current_q':      current_q,
            'current_goal':   current_goal,
            'gap_to_goal':    fmt(gap) if gap > 0 else 0,
            'pct_to_goal':    pct_to_goal,
            'quarters':       quarters,
        }
    except Exception as e:
        return {'error': str(e)}

# ── Attendance ────────────────────────────────────────────
def fetch_attendance():
    try:
        import csv as _csv, io
        from collections import defaultdict

        def raw_csv(sheet_id, gid):
            r = requests.get(
                f'https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq',
                params={'tqx': 'out:csv', 'gid': gid}, timeout=15
            )
            r.raise_for_status()
            return list(_csv.reader(io.StringIO(r.text)))

        # Текущий и предыдущий месяц
        today           = datetime.now()
        curr_month_name = MONTH_NAMES_RU[today.month - 1]
        prev_month_name = MONTH_NAMES_RU[today.month - 2] if today.month > 1 else None

        def load_summary(month_name):
            info = ATTENDANCE_SHEETS.get(month_name)
            if not info:
                return {}
            rows = fetch_gsheet_csv(info['id'], gid=info['gid'])
            result = {}
            for row in rows:
                name = row.get('ФИО', '').strip()
                if not name:
                    continue
                try:
                    presence = int(row.get('Присутствие', '0') or 0)
                except (ValueError, TypeError):
                    presence = 0
                result[name] = {
                    'presence': presence,
                    'tariff':   row.get('Тариф', '').strip(),
                }
            return result

        curr_data = load_summary(curr_month_name)
        prev_data = load_summary(prev_month_name) if prev_month_name else {}

        # Вышедшие из основной таблицы
        res_rows   = fetch_gsheet_csv(RESIDENTS_SHEET_ID, 'Резиденты')
        status_col = next((k for k in (res_rows[0].keys() if res_rows else []) if 'статус' in k.lower()), None)
        name_col   = next((k for k in (res_rows[0].keys() if res_rows else []) if 'имя' in k.lower() or 'фамили' in k.lower()), None)
        exited     = {r.get(name_col,'').strip() for r in res_rows if r.get(status_col,'').strip() == 'Вышел'}
        exited_count = len(exited)

        # Статус каждого резидента
        all_names     = set(curr_data) | set(prev_data)
        status_counts = defaultdict(int)
        residents_out = []

        for name in sorted(all_names):
            if not name:
                continue
            curr   = curr_data.get(name, {})
            prev   = prev_data.get(name, {})
            tariff = (curr.get('tariff') or prev.get('tariff') or '').strip()

            if tariff == 'Deactive' or name in exited:
                continue   # Вышедших считаем отдельно из основной таблицы

            p_curr = curr.get('presence', 0)
            p_prev = prev.get('presence', 0)
            total  = p_curr + p_prev

            if tariff == 'Амбассадор':
                status = 'Амбассадор'
            elif name not in prev_data:
                status = 'Новый'
            elif p_curr >= 3:
                status = 'Активный'
            elif p_curr >= 1:
                status = 'Выпал'
            else:
                status = 'Под риском'

            status_counts[status] += 1
            residents_out.append({
                'name':   name,
                'tariff': tariff,
                'p_curr': p_curr,
                'p_prev': p_prev,
                'total':  total,
                'status': status,
            })

        status_counts['Вышел'] = exited_count

        STATUS_ORDER = {'Активный':0,'Выпал':1,'Под риском':2,'Новый':3,'Амбассадор':4,'Вышел':5}
        residents_out.sort(key=lambda x: (STATUS_ORDER.get(x['status'], 9), x['name']))

        # Агрегат текущего месяца (без Амбассадоров)
        tracked       = [r for r in residents_out if r['status'] != 'Амбассадор']
        total_tracked = len(tracked)
        attended_any  = sum(1 for r in tracked if r['p_curr'] > 0)
        total_att     = sum(r['p_curr'] for r in tracked)
        avg_att       = round(total_att / total_tracked, 1) if total_tracked else 0

        # Годовая статистика (raw CSV, у файла нет нормального заголовка ФИО)
        annual_rows      = raw_csv(ANNUAL_SHEET_ID, ANNUAL_GID)
        annual_total_reg = annual_total_conf = annual_total_pres = 0
        annual_top = []

        for row in annual_rows[1:]:   # пропускаем заголовок
            if len(row) < 5:
                continue
            name_val = row[1].strip()
            if not name_val:
                continue
            try:
                reg  = int(row[2]) if row[2].strip() else 0
                conf = int(row[3]) if row[3].strip() else 0
                pres = int(row[4]) if row[4].strip() else 0
            except (ValueError, IndexError):
                continue
            annual_total_reg  += reg
            annual_total_conf += conf
            annual_total_pres += pres
            if pres > 0:
                annual_top.append({'name': name_val, 'reg': reg, 'conf': conf, 'pres': pres})

        annual_top.sort(key=lambda x: -x['pres'])

        return {
            'curr_month':    curr_month_name,
            'prev_month':    prev_month_name or '—',
            'status_counts': dict(status_counts),
            'residents':     residents_out,
            'total_tracked': total_tracked,
            'attended_any':  attended_any,
            'pct_attended':  int(attended_any / total_tracked * 100) if total_tracked else 0,
            'avg_att':       avg_att,
            'annual_reg':    annual_total_reg,
            'annual_conf':   annual_total_conf,
            'annual_pres':   annual_total_pres,
            'annual_top':    annual_top[:10],
        }
    except Exception as e:
        return {'error': str(e)}

# ── Routes ────────────────────────────────────────────────
@app.route('/')
def index():
    try:
        team, week = fetch_trello()
        crm        = fetch_crm()
        residents  = fetch_residents()
        attendance = fetch_attendance()
        error      = None
    except Exception as e:
        team, week, crm, residents, attendance = {}, {}, None, None, None
        error = str(e)
    return render_template('index.html',
        team=team, week=week, crm=crm, residents=residents,
        attendance=attendance,
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
