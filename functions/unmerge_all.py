import pyautogui as pg
import pygetwindow as gw
from functions.log import log
import time
# necessary imports
import os, sys
import win32com.client
import  jpype     
import  asposecells     
jpype.startJVM(jvmpath="C:\\Program Files\\Java\\jdk-21\\bin\\server\\jvm.dll")
from asposecells.api import Workbook

def convert_to_xlsm(excel_name):
    workbook = Workbook(excel_name)
    workbook.save("swiezy.xlsm")
    jpype.shutdownJVM()
def unmerge_all(excel_path):
    # get directory where the script is located
    _file = os.path.abspath(sys.argv[0])
    path = os.path.dirname(_file)

    # set file paths and macro name accordingly - here we assume that the files are located in the same folder as the Python script
    pathToExcelFile = excel_path
    pathToMacro = "C:\\Users\\Administrator\\Downloads\\WSPA\\XLSX-Refresher\\macro.txt"
    myMacroName = 'UnmergeAndFillData'

    # read the textfile holding the excel macro into a string
    with open (pathToMacro, "r") as myfile:
        print('reading macro into string from: ' + str(myfile))
        macro=myfile.read()

    # open up an instance of Excel with the win32com driver
    excel = win32com.client.Dispatch("Excel.Application")

    # do the operation in background without actually opening Excel
    excel.Visible = False

    # open the excel workbook from the specified file
    workbook = excel.Workbooks.Open(Filename=pathToExcelFile)

    # insert the macro-string into the excel file
    excelModule = workbook.VBProject.VBComponents.Add(1)
    excelModule.CodeModule.AddFromString(macro)

    # run the macro
    excel.Application.Run(myMacroName)

    # save the workbook and close
    excel.Workbooks(1).Close(SaveChanges=1)
    excel.Application.Quit()

    # garbage collection
    del excel