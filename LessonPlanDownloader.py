from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import configparser, os, requests, urllib.request, time
import pandas as pd
import openpyxl
import csv
import os, re
import pymongo
from urllib.parse import quote_plus
from datetime import datetime
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
        hash_md5 = hashlib.md5()
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
        print("Downloading file from PUW")
        response_login = session.post(url_login, headers=headers, data=payload)
        
        if response_login.ok:
            print(f"{Fore.GREEN}Login successful{Style.RESET_ALL}")
    
            try:
                response_download = session.get(url_download)
            except:
                print(f"{Fore.RED}Error downloading the file{Style.RESET_ALL}")
                return False
    
            if response_download.ok:
                print(f"{Fore.GREEN}File downloaded successfully{Style.RESET_ALL}")
    
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
            print(f"{Fore.RED}Error logging in{Style.RESET_ALL}")
    
        session.close()
        return None
    
