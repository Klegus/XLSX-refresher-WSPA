import time
from datetime import datetime, timedelta
import os
import requests
import json
import traceback
import threading
import pytz
import re
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pymongo import MongoClient
from flask import Flask, jsonify, request, Response
from LessonPlan import LessonPlan
from comparer import LessonPlanComparator

class Config:
    def __init__(self):
        load_dotenv()
        self.username = os.getenv("EMAIL")
        self.password = os.getenv("PASSWORD")
        self.mongo_uri = os.getenv("MONGO_URI")
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.selected_model = os.getenv("SELECTED_MODEL")
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.use_test_time = False
        self.test_time = None

class Database:
    def __init__(self, mongo_uri):
        self.client = MongoClient(mongo_uri)
        self.db = self.client.Lesson

    def get_latest_lesson_plan(self):
        try:
            return self.db.plans.find_one(sort=[("timestamp", -1)])
        except Exception as e:
            print(f"Error fetching the latest lesson plan: {str(e)}")
            return None

class StatusChecker:
    def __init__(self):
        self.last_activity = time.time()

    def update_activity(self):
        self.last_activity = time.time()

    def is_active(self):
        return time.time() - self.last_activity < 600  

    def get_last_activity_datetime(self):
        return datetime.fromtimestamp(self.last_activity).isoformat()

class FileManager:
    def __init__(self, working_directory):
        self.working_directory = working_directory
        self.initial_file_structure = self.get_file_structure()

    def get_file_structure(self):
        file_structure = set()
        for root, dirs, files in os.walk(self.working_directory):
            if '__pycache__' in dirs:
                dirs.remove('__pycache__')
            for file in files:
                file_structure.add(os.path.join(root, file))
        return file_structure

    def clean_new_files(self):
        current_structure = self.get_file_structure()
        new_files = current_structure - self.initial_file_structure
        for file in new_files:
            if not file.endswith('.py') and not file.endswith('.env'):
                try:
                    os.remove(file)
                    print(f"Removed file: {file}")
                except Exception as e:
                    print(f"Error while removing file {file}: {str(e)}")

class DiscordNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def send_webhook(self, message):
        if self.webhook_url:
            payload = {
                "embeds": [{
                    "title": "Lesson Plan Update",
                    "description": message,
                    "color": 15158332,
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            try:
                response = requests.post(self.webhook_url, data=json.dumps(payload), headers={"Content-Type": "application/json"})
                response.raise_for_status()
                print("Discord webhook sent successfully")
            except requests.exceptions.RequestException as e:
                print(f"Error while sending Discord webhook: {str(e)}")

class LessonPlanManager:
    def __init__(self, config, lesson_plan, lesson_plan_comparator, file_manager, discord_notifier, status_checker, database):
        self.config = config
        self.lesson_plan = lesson_plan
        self.lesson_plan_comparator = lesson_plan_comparator
        self.file_manager = file_manager
        self.discord_notifier = discord_notifier
        self.status_checker = status_checker
        self.database = database
        self.cached_plans = {}
        self.check_interval = 600  

    def start(self):
        print("Starting LessonPlanManager...")
        self.update_cached_plans()
        flask_thread = threading.Thread(target=self.run_flask_app)
        flask_thread.start()
        try:
            self.run()
        except KeyboardInterrupt:
            print("\nLessonPlanManager stopped by user.")
        except Exception as e:
            print(f"An error occurred: {str(e)}")
        finally:
            print("LessonPlanManager stopped.")

    def run(self):
        while True:
            print(f"\n--- Starting new check at {datetime.now()} ---")
            self.status_checker.update_activity()
            new_checksum = self.lesson_plan.process_and_save_plan()

            if new_checksum:
                print("Lesson plan has changed. Comparing plans...")
                comparison_result = self.lesson_plan_comparator.compare_plans()
                
                webhook_message = f"Lesson plan has been updated. Changes:\n\n{comparison_result}"
                self.discord_notifier.send_webhook(webhook_message)

                self.update_cached_plans()
            else:
                print("No changes in the lesson plan.")

            self.file_manager.clean_new_files()

            print(f"Waiting {self.check_interval} seconds before the next check...")
            time.sleep(self.check_interval)

    def parse_html_to_dataframe(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table')
        if not table:
            return pd.DataFrame()

        headers = [th.text for th in table.find_all('th')]
        data = []
        for row in table.find_all('tr')[1:]:
            data.append([td.text for td in row.find_all('td')])

        return pd.DataFrame(data, columns=headers)
    def update_cached_plans(self):
        latest_plan = self.database.get_latest_lesson_plan()
        if latest_plan:
            for group, html_content in latest_plan['groups'].items():
                self.cached_plans[group] = self.parse_html_to_dataframe(html_content)
        print("Updated lesson plan cache.")

    def run_flask_app(self):
        app = Flask(__name__)
        
        @app.route('/status')
        def status():
            is_active = self.status_checker.is_active()
            last_check = self.status_checker.get_last_activity_datetime()
            if is_active:
                return jsonify({
                    "status": "active",
                    "last_check": last_check
                }), 200
            else:
                return jsonify({
                    "status": "inactive",
                    "last_check": last_check
                }), 503

        @app.route('/api/whatnow/<int:group_number>')
        def whatnow(group_number):
            group_key = self.get_group_key(group_number)
            if group_key is None:
                return jsonify({"message": "Nieprawidłowy numer grupy"}), 400
            
            latest_plan = self.database.get_latest_lesson_plan()
            if not latest_plan or group_key not in latest_plan['groups']:
                return jsonify({"message": "Brak planu lekcji dla tej grupy"}), 404
            
            df_group = self.parse_html_to_dataframe(latest_plan['groups'][group_key])
            now = self.get_current_time()
            message = self.generate_whatnow_message(group_key, df_group, now)
            
            json_response = json.dumps({"message": message}, ensure_ascii=False, indent=2)
            return Response(json_response, content_type="application/json; charset=utf-8")

        @app.route('/api/set_test_time', methods=['POST'])
        def set_test_time():
            data = request.json
            if 'use_test_time' in data:
                self.config.use_test_time = data['use_test_time']
            if 'test_time' in data:
                try:
                    self.config.test_time = datetime.strptime(data['test_time'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return jsonify({"error": "Invalid time format. Use YYYY-MM-DD HH:MM:SS"}), 400
            return jsonify({
                "message": "Test time settings updated",
                "use_test_time": self.config.use_test_time,
                "test_time": self.config.test_time.strftime('%Y-%m-%d %H:%M:%S') if self.config.test_time else None
            })

        app.run(host='0.0.0.0', port=80)
    def get_group_key(self, group_number):
        groups = self.lesson_plan.get_groups()
        group_keys = list(groups.keys())
        if 0 <= group_number < len(group_keys):
            return group_keys[group_number]
        return None

    def get_current_time(self):
        poland_tz = pytz.timezone('Europe/Warsaw')
        if self.config.use_test_time and self.config.test_time:
            if isinstance(self.config.test_time, str):
                return datetime.strptime(self.config.test_time, '%Y-%m-%d %H:%M:%S').replace(tzinfo=poland_tz)
            else:
                return self.config.test_time.replace(tzinfo=poland_tz)
        else:
            return datetime.now(poland_tz)

    def generate_whatnow_message(self, group_key, df_group, now):
        warsaw_tz = pytz.timezone('Europe/Warsaw')
        now = now.astimezone(warsaw_tz)
        
        day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
        current_day = now.weekday()
        
        current_lesson = None
        next_lessons = []
        
        for day_offset in range(60):
            check_day = (current_day + day_offset) % 7
            check_date = now.date() + timedelta(days=day_offset)
            day_name = day_names[check_day]
            
            for _, row in df_group.iterrows():
                if day_name in row and row[day_name].strip():  # Sprawdź, czy jest lekcja w danym dniu
                    time_range = row['Godziny'].split('-')
                    start_time = datetime.strptime(self.parse_custom_time(time_range[0].strip()), '%H:%M').replace(year=check_date.year, month=check_date.month, day=check_date.day, tzinfo=warsaw_tz)
                    end_time = datetime.strptime(self.parse_custom_time(time_range[1].strip()), '%H:%M').replace(year=check_date.year, month=check_date.month, day=check_date.day, tzinfo=warsaw_tz)
                    
                    if day_offset == 0 and start_time <= now < end_time:
                        current_lesson = {
                            'subject': self.format_subject(row[day_name]),
                            'start': start_time.strftime('%H:%M'),
                            'end': end_time.strftime('%H:%M'),
                            'time_left': int((end_time - now).total_seconds() // 60)
                        }
                    elif now < start_time:
                        next_lessons.append({
                            'subject': self.format_subject(row[day_name]),
                            'start': start_time.strftime('%H:%M'),
                            'end': end_time.strftime('%H:%M'),
                            'time_to_start': int((start_time - now).total_seconds() // 60),
                            'day': day_name,
                            'date': start_time.date()
                        })
        
        message = f"Grupa: {group_key}\n\n"
        
        if current_lesson:
            message += f"Aktualna lekcja:\n"
            message += f"{current_lesson['subject']}\n"
            message += f"Koniec: {current_lesson['end']}\n"
            message += f"Pozostało: {self.format_time_to_next_lesson(current_lesson['time_left'])}\n\n"
        
        next_lesson_with_valid_date = self.find_next_lesson_with_valid_date(next_lessons)
        if next_lesson_with_valid_date:
            message += self.format_next_lesson_message(next_lesson_with_valid_date)
        else:
            message += "Brak zaplanowanych lekcji z poprawną datą w ciągu najbliższych 60 dni.\n"
        
        # Usuń ewentualne podwójne nowe linie i końcowe białe znaki
        message = '\n'.join(line for line in message.split('\n') if line.strip())
        
        return message
    
    def find_next_lesson_with_valid_date(self, next_lessons):
        for lesson in next_lessons:
            dates_match = re.search(r'daty: ([\d., ]+)', lesson['subject'])
            if dates_match:
                dates = dates_match.group(1).split(', ')
                lesson_date = lesson['date'].strftime('%d.%m')
                if lesson_date in dates:
                    return lesson
        return None
    
    def format_next_lesson_message(self, next_lesson):
        message = f"Następna lekcja ({next_lesson['day']}):\n"
        message += f"{next_lesson['subject']}\n"
        message += f"Start: {next_lesson['start']}\n"
        message += f"Za: {self.format_time_to_next_lesson(next_lesson['time_to_start'])}\n"

        dates_match = re.search(r'daty: ([\d., ]+)', next_lesson['subject'])
        if dates_match:
            dates = dates_match.group(1).split(', ')
            next_lesson_date = next_lesson['date'].strftime('%d.%m')
            message += f"Data zajęć potwierdzona: {next_lesson_date}\n"

        return message

    def parse_custom_time(self, time_str):
        if len(time_str) == 3:
            hours = int(time_str[0])
            minutes = int(time_str[1:])
        elif len(time_str) == 4:
            hours = int(time_str[:2])
            minutes = int(time_str[2:])
        else:
            raise ValueError(f"Invalid time format: {time_str}")
        
        return f"{hours:02d}:{minutes:02d}"

    def format_subject(self, subject):
        if not subject:
            return "Brak informacji o przedmiocie"
        return subject.replace('\n', ' ')

    def format_time_to_next_lesson(self, minutes):
        hours, mins = divmod(minutes, 60)
        if hours > 0:
            return f"{hours} godz. {mins} min"
        else:
            return f"{mins} min"

def main():
    print("Starting main.py")
    
    try:
        config = Config()
        database = Database(config.mongo_uri)
        status_checker = StatusChecker()
        
        lesson_plan = LessonPlan(
            username=config.username,
            password=config.password,
            mongo_uri=config.mongo_uri
        )
        
        lesson_plan_comparator = LessonPlanComparator(
            mongo_uri=config.mongo_uri,
            openrouter_api_key=config.openrouter_api_key,
            selected_model=config.selected_model
        )
        
        file_manager = FileManager(".")
        discord_notifier = DiscordNotifier(config.discord_webhook_url)

        lesson_plan_manager = LessonPlanManager(
            config,
            lesson_plan,
            lesson_plan_comparator,
            file_manager,
            discord_notifier,
            status_checker,
            database
        )

        print("Starting LessonPlanManager")
        lesson_plan_manager.start()
    except Exception as e:
        print(f"An error occurred in main.py: {str(e)}")
        print("Traceback:")
        print(traceback.format_exc())

if __name__ == "__main__":
    main()