import datetime
def log(txt):
    f = open("logs\\" + datetime.datetime.now().strftime("%d-%m_%H-%M") + '.log', "a")
    formattedText = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] " + txt 
    print(formattedText)
    f.write(formattedText + '\r\n')
    f.close()