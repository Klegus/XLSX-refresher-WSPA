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
        url_login = "https://puw.wspa.pl/login/index.php"
        url_download = "https://puw.wspa.pl/pluginfile.php/194281/mod_folder/content/0/Informatyka%20-%20studia%20I%20stopnia%20-%20st%20II%20-%20semestr%20zimowy.xlsx?forcedownload=1"
        file_save_path = os.path.join(self.directory, "downloaded_file.xlsx")
    
        payload = {'password': self.password, 'username': self.username}
        headers = {'anchor': ''}
    
        session = requests.Session()
        print("Downloading file from PUW")
        response_login = session.post(url_login, headers=headers, data=payload)
        
        if response_login.ok:
            print("Login successful")
    
            try:
                response_download = session.get(url_download)
            except:
                print("Error downloading the file")
                return False
    
            if response_download.ok:
                print("File downloaded successfully")
    
                with open(file_save_path, 'wb') as file:
                    file.write(response_download.content)
                self.file_save_path = os.path.abspath(file_save_path)
                print(f"File saved path = {self.file_save_path}")
                
                # Calculate and return checksum
                checksum = self.calculate_checksum(self.file_save_path)
                return checksum
            else:
                print("Error downloading the file")
        else:
            print("Error logging in")
    
        session.close()
        return None

    @abstractmethod
    def process_file(self):
        """Process the downloaded file"""
        pass

    @abstractmethod
    def extract_group_data(self, group_name):
        """Extract data for specific group"""
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
