from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import configparser, os, requests, urllib.request, time
from functions.log import log
def download_file(username, password, directory):
    url_login = "https://puw.wspa.pl/login/index.php"
    url_download = "https://puw.wspa.pl/pluginfile.php/97278/mod_folder/content/0/Informatyka%20-%20studia%20I%20stopnia%20-%20st%20I%20-%20semestr%20zimowy.xlsx?forcedownload=1"
    file_save_path = "downloaded_file.xlsx"  # Specify the path where you want to save the downloaded file

    payload = {'password': password, 'username': username}
    headers = {'anchor': ''}

    # Create a session
    session = requests.Session()
    log("Downloading file from PUW")
    # Make a POST request to login
    response_login = session.post(url_login, headers=headers, data=payload)
    
    # Check if login was successful
    if response_login.ok:
        log("Login successful")

        # Now, you can make a GET request to download the file
        try:
            response_download = session.get(url_download)
        except:
            log("Error downloading the file")
            return False

        # Check if the download request was successful
        if response_download.ok:
            log("File downloaded successfully")

            # Save the file to the specified path
            with open(file_save_path, 'wb') as file:
                file.write(response_download.content)
        else:
            log("Error downloading the file")
    else:
        log("Error logging in")

    # Close the session when you're done
    session.close()
