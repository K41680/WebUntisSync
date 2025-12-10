import os
import json
import requests
import sys
from datetime import datetime, timedelta
from icalendar import Calendar, Event
import pytz

def load_config():
    """Load configuration from environment variables or config.json"""
    if all(key in os.environ for key in ['WEBUNTIS_SERVER', 'WEBUNTIS_SCHOOL', 'WEBUNTIS_USERNAME', 'WEBUNTIS_PASSWORD']):
        return {
            'server': os.environ['WEBUNTIS_SERVER'],
            'school': os.environ['WEBUNTIS_SCHOOL'],
            'username': os.environ['WEBUNTIS_USERNAME'],
            'password': os.environ['WEBUNTIS_PASSWORD'],
            'class_id': os.environ.get('WEBUNTIS_CLASS_ID')
        }
    
    # Fallback for local testing
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

def get_element_id(session, config, session_id):
    """Get element ID (class or student)."""
    # If a specific class ID is provided in secrets, use it directly
    if config.get('class_id'):
        print(f"📚 Using configured class ID: {config['class_id']}")
        return int(config['class_id']), 1
    
    url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
    headers = {"Cookie": f"JSESSIONID={session_id}"}
    
    # 1. Try fetching classes
    data = {
        "id": "WebUntisSync", 
        "method": "getKlassen", 
        "params": {}, 
        "jsonrpc": "2.0"
    }
    response = session.post(url, json=data, headers=headers)
    result = response.json()
    
    if 'result' in result and len(result['result']) > 0:
        classes = result['result']
        first_class = classes[0]
        print(f"📚 Found class: {first_class['name']} (ID: {first_class['id']})")
        return first_class['id'], 1
    
    # 2. If no classes found, try fetching student ID
    data = {
        "id": "WebUntisSync", 
        "method": "getStudents", 
        "params": {}, 
        "jsonrpc": "2.0"
    }
    response = session.post(url, json=data, headers=headers)
    result = response.json()
    
    if 'result' in result and len(result['result']) > 0:
        student = result['result'][0]
        print(f"👤 Found student: {student.get('name', 'Unknown')} (ID: {student['id']})")
        return student['id'], 5
    
    raise Exception("Could not find any Class or Student ID.")

def get_timetable(session, config, session_id, element_id, element_type, start_date, end_date):
    """Fetch timetable data from WebUntis in chunks to avoid school year errors"""
    full_timetable = []
    
    # We split the request into chunks of 28 days (4 weeks)
    # This prevents the 'startDate and endDate are not within a single school year' error
    chunk_size = 28
    current_start = start_date
    
    print(f"🔄 Fetching timetable in chunks from {start_date} to {end_date}...")

    while current_start < end_date:
        current_end = min(current_start + timedelta(days=chunk_size), end_date)
        
        # print(f"   ⬇️ Fetching chunk: {current_start} to {current_end}...")
        
        url = f"https://{config['server']}/WebUntis/jsonrpc.do?school={config['school']}"
        data = {
            "id": "WebUntisSync",
            "method": "getTimetable",
            "params": {
                "options": {
                    "element": {
                        "id": element_id,
                        "type": element_type
                    },
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
                # Log error but try to continue with next chunk (might be a specific week issue)
                print(f"   ⚠️ Error fetching chunk {current_start} to {current_end}: {result['error']['message']}")
            else:
                items = result.get('result', [])
                full_timetable.extend(items)
                
        except Exception as e:
            print(f"   ⚠️ Exception fetching chunk: {e}")

        # Move to next chunk (start date is end date + 1 day)
        current_start = current_end + timedelta(days=1)
    
    return full_timetable

def parse_webuntis_time(date_int, time_int):
    """Convert WebUntis date (int) and time (int) format to a datetime object"""
    date_str = str(date_int)
    time_str = str(time_int).zfill(4)
    return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M")

def sync_calendar():
    """Main function to sync WebUntis timetable to an ICS file"""
    config = load_config()
    
    if not config:
        raise Exception("Configuration not found. Check environment variables.")

    print("🔐 Logging in to WebUntis...")
    session, session_id = webuntis_login(config)
    
    print("🔍 Finding timetable element...")
    element_id, element_type = get_element_id(session, config, session_id)
    
    # --- DATE CONFIGURATION ---
    today = datetime.now().date()
    # Fetch 90 days into the past (approx 3 months)
    start_date = today - timedelta(days=90)
    # Fetch 180 days into the future (approx 6 months)
    end_date = today + timedelta(days=180)
    
    print(f"📅 Requesting timetable from {start_date} to {end_date}...")
    timetable = get_timetable(session, config, session_id, element_id, element_type, start_date, end_date)
    
    # ICS Calendar setup
    cal = Calendar()
    cal.add('prodid', '-//WebUntis Sync//webuntis-sync//EN')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'WebUntis Timetable')
    cal.add('x-wr-timezone', 'Europe/Brussels')
    
    timezone = pytz.timezone('Europe/Brussels')
    
    event_count = 0
    for lesson in timetable:
        if lesson.get('code') == 'cancelled':
            continue
        
        event = Event()
        
        # Parse times
        try:
            start_dt = parse_webuntis_time(lesson['date'], lesson['startTime'])
            end_dt = parse_webuntis_time(lesson['date'], lesson['endTime'])
        except ValueError:
            continue
        
        # Extract Data
        subjects = [su.get('longname') or su.get('name', '') for su in lesson.get('su', [])]
        teachers = [te.get('longname') or te.get('name', '') for te in lesson.get('te', [])]
        rooms = [ro.get('longname') or ro.get('name', '') for ro in lesson.get('ro', [])]
        classes = [kl.get('longname') or kl.get('name', '') for kl in lesson.get('kl', [])]
        
        # Construct Summary
        summary = ', '.join(subjects) if subjects else 'Lesson'
        if lesson.get('substText'):
            summary = f"{summary} ({lesson['substText']})"
        
        event.add('summary', summary)
        event.add('dtstart', timezone.localize(start_dt))
        event.add('dtend', timezone.localize(end_dt))
        
        # --- DESCRIPTION FORMATTING ---
        # 1. Teachers / 2. Classes / 3. Info
        description_parts = []
        
        if teachers:
            description_parts.append(' / '.join(teachers))
        if classes:
            description_parts.append(' / '.join(classes))
        if lesson.get('info'):
            description_parts.append(str(lesson['info']))
        if lesson.get('substText'):
            description_parts.append(str(lesson['substText']))
        
        if description_parts:
            event.add('description', '\n'.join(description_parts))
        
        if rooms:
            event.add('location', ', '.join(rooms))
        
        # Unique ID
        event.add('uid', f"{lesson['id']}-{lesson['date']}-{lesson['startTime']}@webuntis-sync")
        
        cal.add_component(event)
        event_count += 1
    
    # Save file
    os.makedirs('docs', exist_ok=True)
    output_path = 'docs/calendar.ics'
    with open(output_path, 'wb') as f:
        f.write(cal.to_ical())
    
    print(f"✅ Calendar synced successfully: {event_count} events added to {output_path}")

if __name__ == '__main__':
    try:
        sync_calendar()
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
