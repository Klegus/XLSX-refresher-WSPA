import pyautogui as pg
import pygetwindow as gw
from functions.log import log
import time
def unmerge_all(excel_path, macro_path):
    start_time = time.time()
    pg.hotkey('win', 'r')
    time.sleep(1)
    pg.write(excel_path)
    log("Opening the file")
    pg.press('enter')
    time.sleep(5)
    window = gw.getWindowsWithTitle('Excel')[0]  # replace 'Excel' with the title of your Excel window
    if not window.isMaximized:
        # If not, maximize it
        pg.click(1140,95)
        window.maximize()
    pg.click(1140,95)
    time.sleep(1)
    pg.click(650,60)
    time.sleep(1)
    pg.click(36, 120)
    time.sleep(1)
    pg.click(32, 37)
    time.sleep(1)
    pg.click(52, 90)
    time.sleep(1)
    pg.write(macro_path)
    time.sleep(1)
    pg.press('enter')
    time.sleep(1)
    pg.hotkey('alt','f4')
    time.sleep(1)
    pg.hotkey('alt','f8')
    time.sleep(1)
    pg.press('enter')
    time.sleep(6)
    pg.hotkey('ctrl','s')
    time.sleep(1)
    pg.press('enter')
    time.sleep(1)
    pg.hotkey('alt','f4')
    log("Sucessfully unmerged all cells and saved the file")
    # Record the end time
    end_time = time.time()

    # Calculate and print the time it took to run the script
    log("Time taken for the unmerge process: {} seconds".format(end_time - start_time))