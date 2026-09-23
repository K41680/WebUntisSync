import os
import json
import requests
import sys
from datetime import datetime, timedelta, date
from icalendar import Calendar, Event
import pytz

# --- CONFIGURATION & AUTH ---

def load_config():
    if all(key in os.environ for key in ['WEBUNTIS_SERVER', 'WEBUNTIS_SCHOOL', 'WEBUNTIS_USERNAME', 'WEBUNTIS_PASSWORD']):
        return {
            'server': os.environ['WEBUNTIS_SERVER'],
            'school': os.environ['WEBUNTIS_SCHOOL'],
            'username': os.environ['WEBUNTIS_USERNAME'],
            'password': os.environ['WEBUNTIS_PASSWORD'],
            'class_id': os.environ.get('WEBUNTIS_CLASS_ID'),
            'future_class_id': os.environ.get('WEBUNTIS_FUTURE_CLASS_ID'),
            'switch_date': os.environ.get('SEMESTER_SWITCH_DATE'),
            'ignored_subjects': os.environ.get('WEBUNTIS_IGNORED_SUBJECTS'),
            'extra_class_id': os.environ.get('WEBUNTIS_EXTRA_CLASS_ID'),
            'extra_subjects': os.environ.get('WEBUNTIS_EXTRA_SUBJECTS')
        }
    
    if os.path.exists('config.json'):
        with open('config.json', 'r') as f:
            return json.load(f)
    return {}

def webuntis_login(config):
    session = requests.Session()
    login_url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
    
    login_data = {
        "id": "WebUntisSync",
        "method": "authenticate",
        "params": {
            "user": config['username'],
            "password": config['password'],
            "client": "WebUntisSync"
        },
        "jsonrpc": "2.0"
    }
    
    try:
        response = session.post(login_url, json=login_data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Connection failed: {e}")
    
    result = response.json()
    if 'error' in result:
        raise Exception(f"Login failed: {result['error']}")
    
    return session, result['result']['sessionId']

def get_element_id(session, config, session_id, override_class_id=None):
    target_id_str = override_class_id if override_class_id else config.get('class_id')
    
    url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
    headers = {"Cookie": f"JSESSIONID={session_id}"}
    
    data = {"id": "WebUntisSync", "method": "getKlassen", "params": {}, "jsonrpc": "2.0"}
    response = session.post(url, json=data, headers=headers)
    result = response.json()
    
    if target_id_str and 'result' in result:
        target_str_lower = str(target_id_str).strip().lower()
        for c in result['result']:
            if str(c['id']) == target_str_lower or \
               c.get('name', '').lower() == target_str_lower or \
               c.get('longName', '').lower() == target_str_lower:
                print(f"📚 Resolved Class '{c.get('name')}' to ID: {c['id']}")
                return c['id'], 1

    if 'result' in result and len(result['result']) > 0:
        first_class = result['result'][0]
        print(f"⚠️ Could not exact match '{target_id_str}'. Auto-detecting first available: {first_class['name']} (ID: {first_class['id']})")
        return first_class['id'], 1
    
    raise Exception(f"Could not find any Class ID for {target_id_str}.")

# --- API CALLS: TIMETABLE & HOLIDAYS ---

def get_timetable_chunked(session, config, session_id, element_id, element_type, start_date, end_date):
    full_timetable = []
    chunk_days = 7
    current_start = start_date
    
    while current_start <= end_date:
        current_end = min(current_start + timedelta(days=chunk_days - 1), end_date)
        url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
        data = {
            "id": "WebUntisSync",
            "method": "getTimetable",
            "params": {
                "options": {
                    "element": {"id": element_id, "type": element_type},
                    "startDate": current_start.strftime("%Y%m%d"),
                    "endDate": current_end.strftime("%Y%m%d"),
                    "showBooking": True, "showInfo": True, "showSubstText": True,   
                    "showLsText": True, "showStudentgroup": True,
                    "klasseFields": ["id", "name", "longname"],
                    "roomFields": ["id", "name", "longname"],
                    "subjectFields": ["id", "name", "longname"],
                    "teacherFields": ["id", "name", "longname"]
                }
            },
            "jsonrpc": "2.0"
        }
        headers = {"Cookie": f"JSESSIONID={session_id}"}
        try:
            response = session.post(url, json=data, headers=headers)
            result = response.json()
            if 'error' not in result:
                items = result.get('result', [])
                full_timetable.extend(items)
        except Exception as e:
            print(f"   ⚠️ Exception fetching chunk: {e}")

        current_start = current_end + timedelta(days=1)
    return full_timetable

def get_holidays(session, config, session_id):
    url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
    data = {"id": "WebUntisSync", "method": "getHolidays", "params": {}, "jsonrpc": "2.0"}
    headers = {"Cookie": f"JSESSIONID={session_id}"}
    try:
        response = session.post(url, json=data, headers=headers)
        result = response.json()
        if 'result' in result:
            return result['result']
    except Exception as e:
        print(f"⚠️ Error fetching holidays: {e}")
    return []

# --- MERGING & PROCESSING ---

def parse_webuntis_time(date_int, time_int):
    return datetime.strptime(f"{date_int}{str(time_int).zfill(4)}", "%Y%m%d%H%M")

def merge_unique_text(current_text, new_text):
    if not current_text: return new_text
    if not new_text: return current_text
    parts = [p.strip() for p in current_text.split('|') if p.strip()]
    new_parts = [p.strip() for p in new_text.split('|') if p.strip()]
    for part in new_parts:
        if part not in parts: parts.append(part)
    return ' | '.join(parts)

class ProcessedLesson:
    def __init__(self, raw_lesson):
        self.id = raw_lesson['id']
        self.date = raw_lesson['date']
        self.start_time = raw_lesson['startTime']
        self.end_time = raw_lesson['endTime']
        
        subjects = raw_lesson.get('su', [])
        self.subject_name = subjects[0].get('longname') or subjects[0].get('name') if subjects else "Lesson"
        self.subjects = {su.get('longname') or su.get('name', '') for su in subjects}
        self.subject_names_lower = {su.get('name', '').lower() for su in subjects if su.get('name')} | \
                                   {su.get('longname', '').lower() for su in subjects if su.get('longname')}
                                   
        self.teachers = {te.get('longname') or te.get('name', '') for te in raw_lesson.get('te', [])}
        self.rooms = {ro.get('longname') or ro.get('name', '') for ro in raw_lesson.get('ro', [])}
        self.classes = {kl.get('longname') or kl.get('name', '') for kl in raw_lesson.get('kl', [])}
        
        self.info = raw_lesson.get('info', '')
        self.lstext = raw_lesson.get('lstext', '') 
        self.subst_text = raw_lesson.get('substText', '')
        self.code = raw_lesson.get('code', '') 

    @property
    def start_dt(self): return parse_webuntis_time(self.date, self.start_time)
    @property
    def end_dt(self): return parse_webuntis_time(self.date, self.end_time)

    def merge_with(self, other):
        self.subjects.update(other.subjects)
        self.teachers.update(other.teachers)
        self.rooms.update(other.rooms)
        self.classes.update(other.classes)
        self.info = merge_unique_text(self.info, other.info)
        self.lstext = merge_unique_text(self.lstext, other.lstext)
        self.subst_text = merge_unique_text(self.subst_text, other.subst_text)

def process_timetable(raw_timetable, ignored_subjects_str):
    lessons = []
    ignored_list = [s.strip().lower() for s in ignored_subjects_str.split(',')] if ignored_subjects_str else []

    for raw in raw_timetable:
        if raw.get('code') == 'cancelled': continue
        try: 
            lesson = ProcessedLesson(raw)
            if ignored_list and any(ignored in lesson.subject_names_lower for ignored in ignored_list):
                continue
            lessons.append(lesson)
        except ValueError: continue

    if not lessons: return []
    lessons.sort(key=lambda x: (x.start_dt, x.subject_name))
    merged_overlaps = {}
    
    for lesson in lessons:
        key = (lesson.start_dt, lesson.end_dt, lesson.subject_name)
        if key in merged_overlaps: merged_overlaps[key].merge_with(lesson)
        else: merged_overlaps[key] = lesson

    consolidated_list = sorted(merged_overlaps.values(), key=lambda x: x.start_dt)
    if not consolidated_list: return []

    final_lessons = [consolidated_list[0]]
    for current in consolidated_list[1:]:
        previous = final_lessons[-1]
        if previous.end_dt == current.start_dt and \
           previous.subject_name == current.subject_name and \
           previous.teachers == current.teachers and \
           previous.rooms == current.rooms and \
           previous.classes == current.classes:
            previous.end_time = current.end_time
            previous.info = merge_unique_text(previous.info, current.info)
            previous.lstext = merge_unique_text(previous.lstext, current.lstext)
            previous.subst_text = merge_unique_text(previous.subst_text, current.subst_text)
        else:
            final_lessons.append(current)
    return final_lessons

# --- ICS GENERATION ---

def sync_calendar():
    config = load_config()
    if not config: raise Exception("Configuration not found.")

    print("🔐 Logging in...")
    session, session_id = webuntis_login(config)
    today = datetime.now().date()
    
    start_year = today.year if today.month >= 9 else today.year - 1
    schoolyear_start = date(start_year, 9, 1)
    schoolyear_end = date(start_year + 1, 9, 30)
    
    if config.get('switch_date'):
        try: switch_date = datetime.strptime(config['switch_date'], "%Y-%m-%d").date()
        except ValueError: switch_date = schoolyear_end
    else:
        switch_date = schoolyear_end

    start_date_current = schoolyear_start
    end_date_current = switch_date
    start_date_future = switch_date
    end_date_future = schoolyear_end

    raw_timetable = []

    print(f"🔍 Fetching CURRENT period ({start_date_current} to {end_date_current}) for main class...")
    element_id_curr, element_type_curr = get_element_id(session, config, session_id)
    if start_date_current < end_date_current:
        raw_timetable.extend(get_timetable_chunked(session, config, session_id, element_id_curr, element_type_curr, start_date_current, end_date_current))
    
    if start_date_future < end_date_future and config.get('future_class_id'):
        future_class_id = config.get('future_class_id')
        override_id = future_class_id if future_class_id.strip() != "" else None
        print(f"🔍 Fetching FUTURE period ({start_date_future} to {end_date_future}) for main class...")
        element_id_fut, element_type_fut = get_element_id(session, config, session_id, override_class_id=override_id)
        raw_timetable.extend(get_timetable_chunked(session, config, session_id, element_id_fut, element_type_fut, start_date_future, end_date_future))

    extra_class_id = config.get('extra_class_id')
    extra_subjects = config.get('extra_subjects')
    
    if extra_class_id and extra_subjects:
        print(f"🔍 Fetching EXTRA class ({extra_class_id}) for specific subjects...")
        extra_list = [s.strip().lower() for s in extra_subjects.split(',')]
        
        extra_id, extra_type = get_element_id(session, config, session_id, override_class_id=extra_class_id)
        raw_extra = get_timetable_chunked(session, config, session_id, extra_id, extra_type, schoolyear_start, schoolyear_end)
        
        for raw in raw_extra:
            su = raw.get('su', [])
            names = {s.get('name', '').lower() for s in su if s.get('name')} | \
                    {s.get('longname', '').lower() for s in su if s.get('longname')}
            
            if any(ext in names for ext in extra_list):
                raw_timetable.append(raw)

    print(f"🌴 Fetching holidays...")
    holidays_data = get_holidays(session, config, session_id)

    print(f"⚙️ Processing {len(raw_timetable)} total items...")
    processed_lessons = process_timetable(raw_timetable, config.get('ignored_subjects'))
    
    cal = Calendar()
    cal.add('prodid', '-//WebUntis Sync//webuntis-sync//EN')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'WebUntis Timetable')
    cal.add('x-wr-timezone', 'Europe/Brussels')
    timezone = pytz.timezone('Europe/Brussels')
    
    for lesson in processed_lessons:
        event = Event()
        s_subjects = sorted(list(lesson.subjects))
        s_teachers = sorted(list(lesson.teachers))
        s_rooms = sorted(list(lesson.rooms))
        
        summary = ', '.join(s_subjects) if s_subjects else 'Lesson'
        if lesson.subst_text: summary = f"{summary} ({lesson.subst_text})"
        
        event.add('summary', summary)
        event.add('dtstart', timezone.localize(lesson.start_dt))
        event.add('dtend', timezone.localize(lesson.end_dt))
        
        desc = []
        if s_teachers: desc.append(' / '.join(s_teachers))
        if lesson.lstext or lesson.info or lesson.subst_text: desc.append("-" * 20)
        if lesson.lstext: desc.append(f"ℹ️ {lesson.lstext}")
        if lesson.info: desc.append(f"📝 {lesson.info}")
        if lesson.subst_text: desc.append(f"🔄 {lesson.subst_text}")
            
        if desc: event.add('description', '\n'.join(desc))
        if s_rooms: event.add('location', ', '.join(s_rooms))
        event.add('uid', f"{lesson.id}-{lesson.date}-{lesson.start_time}@webuntis-sync")
        cal.add_component(event)

    for holiday in holidays_data:
        try:
            h_start = datetime.strptime(str(holiday['startDate']), "%Y%m%d").date()
            h_end = datetime.strptime(str(holiday['endDate']), "%Y%m%d").date() + timedelta(days=1)
            
            if h_end < schoolyear_start or h_start > schoolyear_end: continue

            event = Event()
            name = holiday.get('longName') or holiday.get('name', 'Feestdag')
            event.add('summary', f"🏖️ {name}")
            event.add('dtstart', h_start)
            event.add('dtend', h_end)
            event.add('description', "Geïmporteerd uit WebUntis")
            event.add('uid', f"holiday-{holiday['id']}@webuntis-sync")
            cal.add_component(event)
        except Exception as e:
            print(f"⚠️ Could not parse holiday: {holiday} - {e}")

    os.makedirs('docs', exist_ok=True)
    with open('docs/calendar.ics', 'wb') as f:
        f.write(cal.to_ical())
    
    print(f"✅ Calendar synced: {len(processed_lessons)} events and {len(holidays_data)} holidays.")

if __name__ == '__main__':
    try:
        sync_calendar()
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)