from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Dict
import hashlib, re
from lxml import html, etree
from datetime import datetime
from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()
from shared_utils import get_logger

logger = get_logger('MoodleParser')


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
    def __init__(self, html_file_path: str, api_key=None, mongodb_uri="mongodb://localhost:27017/"):
        self.html_file_path = html_file_path
        self.supported_types = ['folder', 'resource', 'page', 'label']
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
            logger.error(f"Błąd wczytywania pliku: {str(e)}")
            return False

    @staticmethod
    def _clean_html(html_str: str) -> str:
        """Clean Word/Moodle cruft from HTML content."""
        if not html_str:
            return ""
        html_str = re.sub(r'<!--\[if.*?\]>.*?<!\[endif\]-->', '', html_str, flags=re.DOTALL)
        html_str = re.sub(r'<!--.*?-->', '', html_str, flags=re.DOTALL)
        html_str = re.sub(r'<(h[1-6]|p|div|span)>\s*</\1>', '', html_str)
        html_str = html_str.replace('&nbsp;', ' ')
        html_str = re.sub(r'\n\s*\n', '\n', html_str)
        return html_str.strip()

    @staticmethod
    def _extract_title_from_content(html_str: str) -> str:
        """Extract a meaningful title from HTML content."""
        if not html_str:
            return ""
        soup = BeautifulSoup(html_str, 'html.parser')
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            text = tag.get_text(strip=True)
            if text and len(text) > 3:
                return text[:120]
        for tag in soup.find_all(['b', 'strong']):
            text = tag.get_text(strip=True)
            if text and len(text) > 3:
                return text[:120]
        for tag in soup.find_all(['p', 'div']):
            text = tag.get_text(strip=True)
            if text and len(text) > 3:
                return text[:120]
        text = soup.get_text(strip=True)
        return text[:120] if text else ""

    def _extract_label_content(self, element) -> dict:
        try:
            element_html = etree.tostring(element, encoding='unicode')
            soup_element = BeautifulSoup(element_html, 'html.parser')
            label_content = (
                soup_element.select_one('div > div > div:nth-of-type(2) > div > div > div') or
                soup_element.select_one('.contentafterlink .no-overflow') or
                soup_element.select_one('.contentwithoutlink .no-overflow') or
                soup_element.select_one('.no-overflow')
            )

            if label_content:
                html_content = self._clean_html(str(label_content))
                text_content = label_content.get_text(strip=True)

                if not text_content and not label_content.find('img'):
                    return None

                images = []
                for img in label_content.find_all('img'):
                    images.append({
                        'src': img.get('src', ''),
                        'alt': img.get('alt', ''),
                        'width': img.get('width', ''),
                        'height': img.get('height', '')
                    })

                title = self._extract_title_from_content(html_content)
                if not title and images:
                    title = next((img['alt'] for img in images if img.get('alt')), 'Obraz')

                return {
                    'title': title,
                    'content': html_content,
                    'images': images
                }
            return None
        except Exception as e:
            logger.error(f"Błąd wyodrębniania etykiety: {str(e)}")
            return None

    def _extract_activity_info(self, element, position: int) -> MoodleActivity:
        module_id = element.get('id', '').replace('module-', '')
        activity_type = ''
        classes = element.get('class', '').split()
        checksum = hashlib.md5(module_id.encode('utf-8'), usedforsecurity=False).hexdigest()

        for class_name in classes:
            if class_name.startswith('modtype_'):
                activity_type = class_name.replace('modtype_', '')
                break

        if activity_type == 'label':
            label_data = self._extract_label_content(element)
            if label_data:
                return MoodleActivity(
                    id=module_id,
                    type='label',
                    title=label_data['title'],
                    url='',
                    content=label_data['content'],
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

        content = ''
        images = []
        content_div = soup_element.select_one('.contentafterlink .no-overflow')
        if content_div:
            content = self._clean_html(str(content_div))
            for img in content_div.find_all('img'):
                images.append({
                    'src': img.get('src', ''),
                    'alt': img.get('alt', ''),
                    'width': img.get('width', ''),
                    'height': img.get('height', '')
                })

        if not title and content:
            title = self._extract_title_from_content(content)

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
            logger.warning(f"Nie znaleziono elementu w ścieżce XPath: {xpath}")
            return []

        activities = []
        elements = list(main_region[0].findall('.//li[@class]'))
        elements.reverse()

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
                activity = self._extract_activity_info(element, current_position)
                activities.append(activity)
                current_position += 1

        self.activities_hierarchy = activities
        return activities

    def save_to_mongodb(self):
        try:
            current_checksums = {a.checksum for a in self.activities_hierarchy}
            existing_checksums = {
                act['checksum']
                for act in self.collection.find({}, {'checksum': 1})
            }

            new_checksums = current_checksums - existing_checksums
            if not new_checksums:
                logger.info("Wszystkie aktywności już istnieją w bazie")
                return True

            activities_to_add = [
                a for a in self.activities_hierarchy
                if a.checksum in new_checksums
            ]

            logger.info(f"Znaleziono {len(activities_to_add)} nowych aktywności do dodania")

            last_doc = self.collection.find_one(sort=[('sequence_number', -1)])
            next_seq = (last_doc['sequence_number'] + 1) if last_doc else 1
            timestamp = datetime.now().isoformat()

            for activity in activities_to_add:
                activity.position = next_seq
                activity_dict = activity.to_dict()
                activity_dict.update({
                    'sequence_number': next_seq,
                    'created_at': timestamp
                })
                self.collection.insert_one(activity_dict)
                next_seq += 1

            logger.info(f"Pomyślnie dodano {len(activities_to_add)} nowych aktywności")
            return True
        except Exception as e:
            logger.error(f"Błąd podczas zapisywania do MongoDB: {str(e)}")
            return False

    def process_and_save(self):
        self.parse_activities()
        self.save_to_mongodb()
