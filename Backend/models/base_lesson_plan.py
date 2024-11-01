from abc import ABC, abstractmethod
from datetime import datetime
import hashlib, os, requests

class BaseLessonPlan(ABC):
    def __init__(self, name, url, sheet_name, groups):
        self.name = name
        self.url = url
        self.sheet_name = sheet_name
        self.groups = groups
        self.file_save_path = None
        self._last_checksum = None

    @abstractmethod
    def download_file(self):
        """Download the lesson plan file"""
        pass

    @abstractmethod
    def process_file(self):
        """Process the downloaded file"""
        # Implementacja powinna zawierać:
        # - unmerge_and_fill_data()
        # - clean_excel_file()
        # - find_group_columns()
        pass

    @abstractmethod
    def extract_group_data(self, group_name):
        """Extract data for specific group"""
        # Implementacja powinna zawierać:
        # - get_lessons_for_group()
        # - save_group_lessons()
        # - convert_to_html_and_save_to_db()
        pass

    @abstractmethod
    def process_and_save_plan(self):
        """Process and save the lesson plan"""
        # Główna metoda wykonująca pełny proces:
        # 1. Pobranie pliku i obliczenie checksum
        # 2. Sprawdzenie czy plan się zmienił
        # 3. Przetworzenie pliku
        # 4. Wyodrębnienie danych dla grup
        # 5. Zapisanie do bazy danych
        pass

    @abstractmethod
    def get_groups(self):
        """Get available groups"""
        pass

    @abstractmethod
    def get_converted_lesson_plan(self):
        """Get the converted lesson plan"""
        pass

    def calculate_checksum(self, file_path):
        """Calculate MD5 checksum of file"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def has_changed(self):
        """Check if plan has changed since last check"""
        if not self.file_save_path:
            return True
        new_checksum = self.calculate_checksum(self.file_save_path)
        has_changed = new_checksum != self._last_checksum
        self._last_checksum = new_checksum
        return has_changed

    def get_metadata(self):
        """Get plan metadata"""
        return {
            "name": self.name,
            "url": self.url,
            "sheet_name": self.sheet_name,
            "groups": list(self.groups.keys()),
            "last_update": datetime.now().isoformat()
        }
