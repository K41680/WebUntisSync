import os
import json
import requests
import sys
from datetime import datetime, timedelta, date
from icalendar import Calendar, Event
import pytz

# --- CONFIGURATION & AUTH ---

def load_config():
    """Load configuration from environment variables or config.json"""
    # Check required base vars
    if all(key in os.environ for key in ['WEBUNTIS_SERVER', 'WEBUNTIS_SCHOOL', 'WEBUNTIS_USERNAME', 'WEBUNTIS_PASSWORD']):
        return {
            'server': os.environ['WEBUNTIS_SERVER'],
            'school': os.environ['WEBUNTIS_SCHOOL'],
            'username': os.environ['WEBUNTIS_USERNAME'],
            'password': os.environ['WEBUNTIS_PASSWORD'],
            'class_id': os.environ.get('WEBUNTIS_CLASS_ID'),
            # Optional: Future semester config
            'future_class_id': os.environ.get('WEBUNTIS_FUTURE_CLASS_ID'),
            'switch_date': os.environ.get('SEMESTER_SWITCH_DATE') # Format YYYY-MM-DD
        }
    
    if os.path.exists('config.json'):
        with open('config.json', 'r') as f:
            return json.load(f)
    return {}

def webuntis_login(config):
    """Authenticate against WebUntis and return session + sessionId"""
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
    """
    Get element ID. 
    1. If override_class_id is provided, use that.
    2. Else if config['class_id'] is provided, use that.
    3. Else auto-detect first class or student.
    """
    # Determine which ID string to use
    target_id_str = override_class_id if override_class_id else config.get('class_id')

    if target_id_str:
        # print(f"📚 Using Class ID: {target_id_str}")
        return int(target_id_str), 1
    
    # Auto-detection fallback
    url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
    headers = {"Cookie": f"JSESSIONID={session_id}"}
    
    # Try fetching classes
    data = {"id": "WebUntisSync", "method": "getKlassen", "params": {}, "jsonrpc": "2.0"}
    response = session.post(url, json=data, headers=headers)
    result = response.json()
    
    if 'result' in result and len(result['result']) > 0:
        first_class = result['result'][0]
        print(f"📚 Auto-detected class: {first_class['name']} (ID: {first_class['id']})")
        return first_class['id'], 1
    
    # Try fetching student
    data = {"id": "WebUntisSync", "method": "getStudents", "params": {}, "jsonrpc": "2.0"}
    response = session.post(url, json=data, headers=headers)
    result = response.json()
    
    if 'result' in result and len(result['result']) > 0:
        student = result['result'][0]
        print(f"👤 Auto-detected student: {student.get('name', 'Unknown')} (ID: {student['id']})")
        return student['id'], 5
    
    raise Exception("Could not find any Class or Student ID.")

# --- TIMETABLE FETCHING ---

def get_timetable_chunked(session, config, session_id, element_id, element_type, start_date, end_date):
    """Fetch timetable data from WebUntis in chunks"""
    full_timetable = []
    chunk_size = 28 # 4 weeks per chunk
    current_start = start_date
    
    while current_start < end_date:
        current_end = min(current_start + timedelta(days=chunk_size), end_date)
        
        url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
        data = {
            "id": "WebUntisSync",
            "method": "getTimetable",
            "params": {
                "options": {
                    "element": {"id": element_id, "type": element_type},
                    "startDate": current_start.strftime("%Y%m%d"),
                    "endDate": current_end.strftime("%Y%m%d"),
                    "showBooking": True, 
                    "showInfo": True,        
                    "showSubstText": True,   
                    "showLsText": True,      
                    "showStudentgroup": True,
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
            
            if 'error' in result:
                print(f"   ⚠️ Error fetching chunk {current_start}: {result['error']['message']}")
            else:
                items = result.get('result', [])
                full_timetable.extend(items)
                
        except Exception as e:
            print(f"   ⚠️ Exception fetching chunk: {e}")

        current_start = current_end + timedelta(days=1)
    
    return full_timetable

def parse_webuntis_time(date_int, time_int):
    """Convert WebUntis date/time ints to datetime object"""
    date_str = str(date_int)
    time_str = str(time_int).zfill(4)
    return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M")

# --- MERGING LOGIC HELPER ---

def merge_unique_text(current_text, new_text):
    if not current_text: return new_text
    if not new_text: return current_text
    parts = [p.strip() for p in current_text.split('|') if p.strip()]
    new_parts = [p.strip() for p in new_text.split('|') if p.strip()]
    for part in new_parts:
        if part not in parts: parts.append(part)
    return ' | '.join(parts)

class ProcessedLesson:
    """Helper class to manage lesson data for merging"""
    def __init__(self, raw_lesson):
        self.id = raw_lesson['id']
        self.date = raw_lesson['date']
        self.start_time = raw_lesson['startTime']
        self.end_time = raw_lesson['endTime']
        
        subjects = raw_lesson.get('su', [])
        self.subject_name = subjects[0].get('longname') or subjects[0].get('name') if subjects else "Lesson"
        
        self.subjects = {su.get('longname') or su.get('name', '') for su in subjects}
        self.teachers = {te.get('longname') or te.get('name', '') for te in raw_lesson.get('te', [])}
        self.rooms = {ro.get('longname') or ro.get('name', '') for ro in raw_lesson.get('ro', [])}
        self.classes = {kl.get('longname') or kl.get('name', '') for kl in raw_lesson.get('kl', [])}
        
        self.info = raw_lesson.get('info', '')
        self.lstext = raw_lesson.get('lstext', '') 
        self.subst_text = raw_lesson.get('substText', '')
        self.code = raw_lesson.get('code', '') 

    @property
    def start_dt(self):
        return parse_webuntis_time(self.date, self.start_time)

    @property
    def end_dt(self):
        return parse_webuntis_time(self.date, self.end_time)

    def merge_with(self, other):
        self.subjects.update(other.subjects)
        self.teachers.update(other.teachers)
        self.rooms.update(other.rooms)
        self.classes.update(other.classes)
        self.info = merge_unique_text(self.info, other.info)
        self.lstext = merge_unique_text(self.lstext, other.lstext)
        self.subst_text = merge_unique_text(self.subst_text, other.subst_text)

def process_timetable(raw_timetable):
    lessons = []
    for raw in raw_timetable:
        if raw.get('code') == 'cancelled': continue
        try: lessons.append(ProcessedLesson(raw))
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
        is_continuous = (previous.end_dt == current.start_dt)
        is_same_content = (
            previous.subject_name == current.subject_name and
            previous.teachers == current.teachers and
            previous.rooms == current.rooms and
            previous.classes == current.classes
        )

        if is_continuous and is_same_content:
            previous.end_time = current.end_time
            previous.info = merge_unique_text(previous.info, current.info)
            previous.lstext = merge_unique_text(previous.lstext, current.lstext)
            previous.subst_text = merge_unique_text(previous.subst_text, current.subst_text)
        else:
            final_lessons.append(current)

    return final_lessons

# --- ICS GENERATION ---

def sync_calendar():
    """Main function"""
    config = load_config()
    if not config: raise Exception("Configuration not found.")

    print("🔐 Logging in...")
    session, session_id = webuntis_login(config)
    
    # --- DATE CALCULATION ---
    today = datetime.now().date()
    
    # 1. Determine Switch Date
    # Default to 4 weeks from now if not set in Secrets
    if config.get('switch_date'):
        try:
            switch_date = datetime.strptime(config['switch_date'], "%Y-%m-%d").date()
        except ValueError:
            print("⚠️ Invalid date format in SEMESTER_SWITCH_DATE. Defaulting to +28 days.")
            switch_date = today + timedelta(days=28)
    else:
        switch_date = today + timedelta(days=28)

    # Define Periods
    # Period 1: Past 60 days (2 months) -> Switch Date
    start_date_current = today - timedelta(days=60)
    end_date_current = switch_date

    # Period 2: Switch Date -> Future (5 months)
    start_date_future = switch_date
    end_date_future = today + timedelta(days=155)

    raw_timetable = []

    # --- FETCH PART 1: CURRENT PERIOD ---
    # Always uses the standard CLASS_ID
    print(f"🔍 Fetching CURRENT period (Element: {config.get('class_id')})")
    print(f"   📅 Range: {start_date_current} to {end_date_current}")
    
    element_id_curr, element_type_curr = get_element_id(session, config, session_id)
    
    if start_date_current < end_date_current:
        data_curr = get_timetable_chunked(session, config, session_id, element_id_curr, element_type_curr, start_date_current, end_date_current)
        raw_timetable.extend(data_curr)
    
    # --- FETCH PART 2: FUTURE PERIOD ---
    # Uses FUTURE_CLASS_ID if set, otherwise falls back to CLASS_ID
    if start_date_future < end_date_future:
        future_class_id = config.get('future_class_id')
        
        # Decide which ID to use for the future
        if future_class_id and future_class_id.strip() != "":
            print(f"🔍 Fetching FUTURE period (Element: {future_class_id})")
            override_id = future_class_id
        else:
            print(f"🔍 Fetching FUTURE period (Continuing with Current Element)")
            override_id = None # Logic inside get_element_id handles this fallback

        print(f"   📅 Range: {start_date_future} to {end_date_future}")

        # Get the ID (either new one or fallback to current)
        element_id_fut, element_type_fut = get_element_id(session, config, session_id, override_class_id=override_id)
        
        data_fut = get_timetable_chunked(session, config, session_id, element_id_fut, element_type_fut, start_date_future, end_date_future)
        raw_timetable.extend(data_fut)

    print(f"⚙️ Processing {len(raw_timetable)} total items...")
    processed_lessons = process_timetable(raw_timetable)
    
    # Setup Calendar
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
        s_classes = sorted(list(lesson.classes))
        s_rooms = sorted(list(lesson.rooms))
        
        summary = ', '.join(s_subjects) if s_subjects else 'Lesson'
        if lesson.subst_text: summary = f"{summary} ({lesson.subst_text})"
        
        event.add('summary', summary)
        event.add('dtstart', timezone.localize(lesson.start_dt))
        event.add('dtend', timezone.localize(lesson.end_dt))
        
        description_parts = []
        if s_teachers: description_parts.append(' / '.join(s_teachers))
        if s_classes: description_parts.append(' / '.join(s_classes))
        if lesson.lstext or lesson.info or lesson.subst_text: description_parts.append("-" * 20)
        if lesson.lstext: description_parts.append(f"ℹ️ {lesson.lstext}")
        if lesson.info: description_parts.append(f"📝 {lesson.info}")
        if lesson.subst_text: description_parts.append(f"🔄 {lesson.subst_text}")
            
        if description_parts: event.add('description', '\n'.join(description_parts))
        if s_rooms: event.add('location', ', '.join(s_rooms))
        
        # UID unique for sync
        uid = f"{lesson.id}-{lesson.date}-{lesson.start_time}@webuntis-sync"
        event.add('uid', uid)
        cal.add_component(event)
    
    os.makedirs('docs', exist_ok=True)
    with open('docs/calendar.ics', 'wb') as f:
        f.write(cal.to_ical())
    
    print(f"✅ Calendar synced: {len(processed_lessons)} events.")

if __name__ == '__main__':
    try:
        sync_calendar()
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
