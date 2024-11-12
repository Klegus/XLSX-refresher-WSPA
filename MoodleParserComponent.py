from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Dict
import hashlib, requests
from lxml import html, etree
from datetime import datetime
from pymongo import MongoClient

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
        self.db = self.mongo_client['Lesson_dev']
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
                    'content': {
                        'html': html_content,
                        'text': text_content
                    },
                    'images': images
                }
            return None
        except Exception as e:
            print(f"Błąd podczas wyodrębniania zawartości etykiety: {str(e)}")
            return None

    def format_with_openrouter(self, text: str) -> str:
        if isinstance(text, dict):
            text = text.get('text', '')
        
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
            content = self.format_with_openrouter(label_data['content'])
            if label_data:
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
            content = {
                'html': str(content_div),
                'text': content_div.get_text(strip=True, separator='\n')
            }
            for img in content_div.find_all('img'):
                img_info = {
                    'src': img.get('src', ''),
                    'alt': img.get('alt', ''),
                    'width': img.get('width', ''),
                    'height': img.get('height', '')
                }
                images.append(img_info)
        content = self.format_with_openrouter(content)
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
        
        position = 0
        activities = []
        
        for element in main_region[0].findall('.//li[@class]'):
            classes = element.get('class', '').split()
            
            if 'activity' not in classes:
                continue
                
            activity_type = None
            for class_name in classes:
                if class_name.startswith('modtype_'):
                    activity_type = class_name.replace('modtype_', '')
                    break
            
            if activity_type in self.supported_types:
                activity = self._extract_activity_info(element, position)
                activities.append(activity)
                position += 1
        
        self.activities_hierarchy = activities
        return activities

    def save_to_mongodb(self):
        try:
            self.collection.delete_many({})
            timestamp = datetime.now().isoformat()
            
            for idx, activity in enumerate(self.activities_hierarchy, 1):
                activity_dict = activity.to_dict()
                activity_dict.update({
                    'sequence_number': idx,
                    'created_at': timestamp,
                    'checksum': activity_dict['checksum']
                })
                self.collection.insert_one(activity_dict)
            
            print(f"Zapisano {len(self.activities_hierarchy)} aktywności do MongoDB")
            return True
        except Exception as e:
            print(f"Błąd podczas zapisywania do MongoDB: {str(e)}")
            return False

    def process_and_save(self):
        self.parse_activities()
        self.save_to_mongodb()