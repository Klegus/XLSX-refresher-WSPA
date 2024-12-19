from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Dict
import hashlib, requests
from lxml import html, etree
from datetime import datetime
from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()
import custom_print
@dataclass
class MoodleActivity:
    id: str
    type: str
    title: str
    url: str
    content: str
    images: List[Dict[str, str]]
    position: int
    checksum: str
    
    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'title': self.title,
            'url': self.url,
            'content': self.content,
            'images': self.images,
            'position': self.position,
            'checksum': self.checksum
        }
    
    def __str__(self):
        return f"{self.type.upper()}: {self.title} (ID: {self.id})"

class MoodleFileParser:
    def __init__(self, html_file_path: str, api_key, mongodb_uri="mongodb://localhost:27017/"):
        self.html_file_path = html_file_path
        self.supported_types = ['folder', 'resource', 'page', 'label']
        self.openrouter_api_key = api_key
        self.activities_hierarchy = []
        self.mongo_client = MongoClient(mongodb_uri)
        self.db = self.mongo_client[os.getenv('MONGO_DB', 'Lesson_dev')]
        self.collection = self.db['Activities']
        
    def load_file(self):
        try:
            with open(self.html_file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            self.tree = html.fromstring(content)
            self.soup = BeautifulSoup(content, 'html.parser')
            return True
        except Exception as e:
            print(f"Błąd wczytywania pliku: {str(e)}")
            return False
    
    def calculate_checksum(self, element) -> str:
        element_html = etree.tostring(element, encoding='unicode', method='html')
        hash_obj = hashlib.md5(element_html.encode('utf-8'), usedforsecurity=False)
        return hash_obj.hexdigest()
    
    def _extract_label_content(self, element) -> dict:
        try:
            element_html = etree.tostring(element, encoding='unicode')
            soup_element = BeautifulSoup(element_html, 'html.parser')
            label_content = soup_element.select_one('div > div > div:nth-of-type(2) > div > div > div')
            
            if label_content:
                text_content = label_content.get_text(strip=True, separator='\n')
                html_content = str(label_content)
                images = []
                for img in label_content.find_all('img'):
                    img_info = {
                        'src': img.get('src', ''),
                        'alt': img.get('alt', ''),
                        'width': img.get('width', ''),
                        'height': img.get('height', '')
                    }
                    images.append(img_info)
                
                title = ''
                for p in label_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    if p.get_text(strip=True):
                        title = p.get_text(strip=True)
                        break
                
                return {
                    'title': title,
                    'content': html_content,
                    'images': images
                }
            return None
        except Exception as e:
            print(f"Błąd podczas wyodrębniania zawartości etykiety: {str(e)}")
            return None

    def format_with_openrouter(self, text: str) -> str:
        if not text or not isinstance(text, str):
            return ""
            
        if not text.strip():
            return ""

        try:
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "HTTP-Referer": "http://localhost:8000",
                "Content-Type": "application/json"
            }

            data = {
                "model": "openai/gpt-3.5-turbo-0613t",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an HTML formatter. Format the given text into clean HTML with proper semantic tags, lists, paragraphs. Use <b> for emphasis, create proper <ol> and <ul> lists, and structure text into <p> paragraphs. Preserve all information and structure. Do not add any explanations, just return the formatted HTML."
                    },
                    {
                        "role": "user",
                        "content": f"Format this text into clean HTML:\n\n{text}"
                    }
                ]
            }

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=data
            )

            if response.status_code == 200:
                formatted_text = response.json()['choices'][0]['message']['content'].strip()
                formatted_text = formatted_text.replace('```html', '').replace('```', '')
                return formatted_text
            else:
                print(f"Błąd API OpenRouter: {response.status_code}")
                return text

        except Exception as e:
            print(f"Błąd formatowania OpenRouter: {str(e)}")
            return text

    def _extract_activity_info(self, element, position: int) -> MoodleActivity:
        module_id = element.get('id', '').replace('module-', '')
        activity_type = ''
        classes = element.get('class', '').split()
        checksum = self.calculate_checksum(element)
        
        for class_name in classes:
            if class_name.startswith('modtype_'):
                activity_type = class_name.replace('modtype_', '')
                break
        
        if activity_type == 'label':
            label_data = self._extract_label_content(element)
            if label_data:
                content = label_data['content']
                return MoodleActivity(
                    id=module_id,
                    type='label',
                    title=label_data['title'],
                    url='',
                    content=content,
                    images=label_data['images'],
                    position=position,
                    checksum=checksum
                )
            
        element_html = etree.tostring(element, encoding='unicode')
        soup_element = BeautifulSoup(element_html, 'html.parser')
        
        title = ''
        url = ''
        link = soup_element.select_one('.activityinstance a.aalink')
        if link:
            url = link.get('href', '')
            title_span = link.select_one('span.instancename')
            if title_span:
                accesshide = title_span.select_one('.accesshide')
                if accesshide:
                    accesshide.decompose()
                title = title_span.text.strip()
        
        content = {'html': '', 'text': ''}
        images = []
        content_div = soup_element.select_one('.contentafterlink .no-overflow')
        if content_div:
            content = str(content_div)
            for img in content_div.find_all('img'):
                img_info = {
                    'src': img.get('src', ''),
                    'alt': img.get('alt', ''),
                    'width': img.get('width', ''),
                    'height': img.get('height', '')
                }
                images.append(img_info)
        return MoodleActivity(
            id=module_id,
            type=activity_type,
            title=title,
            url=url,
            content=content,
            images=images,
            position=position,
            checksum=checksum
        )

    def parse_activities(self) -> List[MoodleActivity]:
        if not self.load_file():
            return []
        
        xpath = '//*[@id="region-main"]/div/div[1]/ul'
        main_region = self.tree.xpath(xpath)
        
        if not main_region:
            print(f"Nie znaleziono elementu w ścieżce XPath: {xpath}")
            return []
        
        activities = []
        
        elements = list(main_region[0].findall('.//li[@class]'))
        elements.reverse()  # Odwracamy kolejność elementów
        
        current_position = 0
        for element in elements:
            classes = element.get('class', '').split()
            
            if 'activity' not in classes:
                continue
                
            activity_type = None
            for class_name in classes:
                if class_name.startswith('modtype_'):
                    activity_type = class_name.replace('modtype_', '')
                    break
            
            if activity_type in self.supported_types:
                # Nie formatujemy contentu przez OpenRouter na tym etapie
                activity = self._extract_activity_info(element, current_position)
                activities.append(activity)
                current_position += 1
        
        self.activities_hierarchy = activities
        return activities

    def save_to_mongodb(self):
        try:
            # Najpierw zbierz wszystkie checksumy z aktualnych elementów
            current_checksums = {activity.checksum for activity in self.activities_hierarchy}
            
            # Pobierz istniejące checksumy z bazy
            existing_checksums = {
                act['checksum'] 
                for act in self.collection.find({}, {'checksum': 1})
            }
            
            # Znajdź checksumy których nie ma w bazie
            new_checksums = current_checksums - existing_checksums
            
            if not new_checksums:
                print("Wszystkie aktywności już istnieją w bazie")
                return True
            
            # Filtruj aktywności które trzeba dodać
            activities_to_add = [
                activity for activity in self.activities_hierarchy 
                if activity.checksum in new_checksums
            ]
            
            print(f"Znaleziono {len(activities_to_add)} nowych aktywności do dodania")
            
            # Znajdź najwyższy sequence_number i position
            last_doc = self.collection.find_one(sort=[('sequence_number', -1)])
            next_seq = (last_doc['sequence_number'] + 1) if last_doc else 1
            next_pos = next_seq  # Używamy sequence_number jako position
            
            timestamp = datetime.now().isoformat()
            
            # Przetwarzaj tylko nowe aktywności
            for activity in activities_to_add:
                # Formatuj treść przez OpenRouter
                activity.content = self.format_with_openrouter(activity.content)
                
                # Aktualizuj position na podstawie sequence_number
                activity.position = next_pos
                
                activity_dict = activity.to_dict()
                activity_dict.update({
                    'sequence_number': next_seq,
                    'created_at': timestamp
                })
                
                next_seq += 1
                next_pos = next_seq
                self.collection.insert_one(activity_dict)
                next_seq += 1
                
            print(f"Pomyślnie dodano {len(activities_to_add)} nowych aktywności")
            return True
        except Exception as e:
            print(f"Błąd podczas zapisywania do MongoDB: {str(e)}")
            return False

    def process_and_save(self):
        self.parse_activities()
        self.save_to_mongodb()
