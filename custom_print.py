import sys
import time
import inspect
import os

def get_caller_info():
    frame = inspect.currentframe()
    caller_frame = frame.f_back.f_back
    filename = os.path.basename(caller_frame.f_code.co_filename)
    return filename

original_print = print

def custom_print(*args, **kwargs):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    filename = get_caller_info()
    message = ' '.join(map(str, args))
    original_print(f"{timestamp} [{filename}] {message}", **kwargs) 
    
sys.modules['builtins'].print = custom_print