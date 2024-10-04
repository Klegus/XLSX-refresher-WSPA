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
    def __init__(self, username, password, directory=""):
        self.username = username
        self.password = password
        self.directory = directory
        self.file_save_path = None
        
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
    