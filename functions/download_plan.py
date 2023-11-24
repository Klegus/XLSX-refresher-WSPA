from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import configparser, os, requests, urllib.request, time
from functions.log import log
def download_file(username, password, directory):
    options = webdriver.ChromeOptions()
    prefs = {"download.default_directory" : directory}
    options.add_experimental_option("prefs",prefs)
    options.add_argument("--headless")

    # Start a new browser session
    log("Starting a new browser session")
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()

    # Navigate to the login page
    log("Navigating to the login page")
    driver.get('https://puw.wspa.pl/mod/folder/view.php?id=68720')

    # Fill in the login form and submit
    log("Filling in the login form and submitting")
    username_field = driver.find_element(By.ID, 'username')
    username_field.send_keys(username)
    log("Username entered")
    password_field = driver.find_element(By.ID, 'password')
    password_field.send_keys(password)
    log("Password entered")
    password_field.send_keys(Keys.RETURN)
    download_link = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, '//*[@id="ygtvcontentel2"]/span/a'))
    )
    log("Downloading the file")
    download_link.click()
    time.sleep(1)
    log("Closing the browser session")
    driver.close()

    # Close the browser session
    driver.quit()
