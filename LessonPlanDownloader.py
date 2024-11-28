import os, requests
import os
import hashlib

class LessonPlanDownloader:
    def __init__(self, username, password, directory="", download_url=None):
        self.username = username
        self.password = password
        self.directory = directory
        self.file_save_path = None
        self.download_url = download_url
        
    def get_file_save_path(self):
        return self.file_save_path
    
    def calculate_checksum(self, file_path):
        hash_md5 = hashlib.new('md5', usedforsecurity=False)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def download_file(self):
        url_login = "https://puw.wspa.pl/login/index.php"
        if not self.download_url:
            raise ValueError("Download URL not provided")
        url_download = self.download_url
        file_save_path = os.path.join(self.directory, "downloaded_file.xlsx")
    
        payload = {'password': self.password, 'username': self.username}
        headers = {'anchor': ''}
    
        session = requests.Session()
        #print("Downloading file from PUW")
        response_login = session.post(url_login, headers=headers, data=payload)
        
        if response_login.ok:
            #print("Login successful")
    
            try:
                response_download = session.get(url_download)
            except requests.exceptions.RequestException as e:
                print("Error downloading the file")
                return False
    
            if response_download.ok:
                #print("File downloaded successfully")
    
                with open(file_save_path, 'wb') as file:
                    file.write(response_download.content)
                self.file_save_path = os.path.abspath(file_save_path)
                #print(f"File saved path = {self.file_save_path}")
                
                # Calculate and return checksum
                checksum = self.calculate_checksum(self.file_save_path)
                return checksum
            else:
                print("Error downloading the file")
        else:
            print("Error logging in")
    
        session.close()
        return None
    
