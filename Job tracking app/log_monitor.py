import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os
import time

LOG_FOLDER_PATH = r"C:\Users\Encoding 3\Desktop\Encoding Void Reports"

def process_excel_log(file_path, update_ui_callback):
    df = pd.read_excel(file_path)

    total_labels = df["Tag Write Count"].sum()
    failed_labels = df["Failed Tag Count"].sum()

    print(f"New log detected: {file_path}")
    print(f"Total Labels Printed: {total_labels}, Failed: {failed_labels}")

    # Call the UI function to update
    update_ui_callback(total_labels, failed_labels)

class LogFileHandler(FileSystemEventHandler):
    def __init__(self, update_ui_callback):
        self.update_ui_callback = update_ui_callback

    def on_created(self, event):
        if event.src_path.endswith(".xlsx"):  # Only process Excel files
            process_excel_log(event.src_path, self.update_ui_callback)

def start_monitoring(update_ui_callback):
    """
    This function now takes `update_ui_callback` as an argument,
    which we will pass in from main.py
    """
    observer = Observer()
    event_handler = LogFileHandler(update_ui_callback)
    observer.schedule(event_handler, LOG_FOLDER_PATH, recursive=False)
    observer.start()
    
    print(f"Monitoring folder: {LOG_FOLDER_PATH}")
    try:
        while True:
            time.sleep(5)  # Keep the script running
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
