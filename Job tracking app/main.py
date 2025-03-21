import sys
import os
import csv
import math
import sqlite3
from datetime import datetime
import threading

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QDialog, QFormLayout, QDialogButtonBox,
    QMessageBox, QProgressBar, QSpinBox, QScrollArea, QStackedWidget, QTabWidget,QLineEdit
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject

# Import Watchdog modules
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# =============================================================================
# Database Manager: Handles creation and access to the local SQL database.
# =============================================================================
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        """Create necessary tables if they do not exist."""
        cursor = self.connection.cursor()
        # Added 'completed' flag to mark finished jobs.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT,
                job_ticket TEXT,
                inlay_type TEXT,
                quantity INTEGER,
                labels_per_roll INTEGER,
                printer_name TEXT,
                created_at TEXT,
                completed INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS roll_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                roll_number INTEGER,
                action TEXT,
                note TEXT,
                timestamp TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        ''')
        self.connection.commit()

    def add_job(self, customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name):
        cursor = self.connection.cursor()
        created_at = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO jobs (customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name, created_at))
        self.connection.commit()
        return cursor.lastrowid

    def get_active_jobs(self):
        cursor = self.connection.cursor()
        cursor.execute('SELECT id, customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name, created_at FROM jobs WHERE completed=0')
        return cursor.fetchall()

    def get_completed_jobs(self):
        cursor = self.connection.cursor()
        cursor.execute('SELECT id, customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name, created_at FROM jobs WHERE completed=1')
        return cursor.fetchall()

    def log_roll_action(self, job_id, roll_number, action, note=""):
        cursor = self.connection.cursor()
        timestamp = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO roll_tracking (job_id, roll_number, action, note, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (job_id, roll_number, action, note, timestamp))
        self.connection.commit()

    def update_job_completion(self, job_id, completed=1):
        cursor = self.connection.cursor()
        cursor.execute('UPDATE jobs SET completed=? WHERE id=?', (completed, job_id))
        self.connection.commit()

    def update_job(self, job_id, customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name):
        cursor = self.connection.cursor()
        cursor.execute('''
            UPDATE jobs
            SET customer=?, job_ticket=?, inlay_type=?, quantity=?, labels_per_roll=?, printer_name=?
            WHERE id=?
        ''', (customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name, job_id))
        self.connection.commit()

# =============================================================================
# Watchdog CSV Event Handler: Monitors CSV file changes.
# =============================================================================
class CSVEventHandler(FileSystemEventHandler):
    def __init__(self, monitor):
        self.monitor = monitor

    def on_modified(self, event):
        if not event.is_directory and event.src_path.lower().endswith('.csv'):
            self.monitor.process_csv(event.src_path)

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith('.csv'):
            self.monitor.file_row_counts[event.src_path] = 0
            self.monitor.process_csv(event.src_path)

# =============================================================================
# Watchdog CSV Monitor: Uses Watchdog to monitor a directory for CSV updates.
# =============================================================================
class WatchdogCSVMonitor(QObject):
    update_signal = pyqtSignal(dict)  # {printer_name: {"pass": cumulative_pass, "fail": cumulative_fail}}

    def __init__(self, directory):
        super().__init__()
        self.directory = directory
        self.file_row_counts = {}
        self.cumulative_counts = {}
        self.observer = Observer()
        self.event_handler = CSVEventHandler(self)

    def start(self):
        self.observer.schedule(self.event_handler, self.directory, recursive=False)
        threading.Thread(target=self.observer.start, daemon=True).start()

    def stop(self):
        self.observer.stop()
        self.observer.join()

    def process_csv(self, csv_path):
        new_counts = {}
        if csv_path not in self.file_row_counts:
            self.file_row_counts[csv_path] = 0

        try:
            with open(csv_path, "r", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                rows = list(reader)
                total_rows = len(rows)
                last_count = self.file_row_counts[csv_path]
                new_rows = rows[last_count:]
                for row in new_rows:
                    failure_msg = row.get("Failure Message", "").strip()
                    printer = row.get("Printer Name", "Printer_1").strip()
                    if printer not in new_counts:
                        new_counts[printer] = {"pass": 0, "fail": 0}
                    if failure_msg == "Pass (Label)":
                        new_counts[printer]["pass"] += 1
                    elif failure_msg == "Fail (Label)":
                        new_counts[printer]["fail"] += 1
                self.file_row_counts[csv_path] = total_rows
        except Exception as e:
            print("Error processing CSV:", e)
            return

        for printer, counts in new_counts.items():
            if printer not in self.cumulative_counts:
                self.cumulative_counts[printer] = {"pass": 0, "fail": 0}
            self.cumulative_counts[printer]["pass"] += counts["pass"]
            self.cumulative_counts[printer]["fail"] += counts["fail"]

        if new_counts:
            self.update_signal.emit(self.cumulative_counts)

# =============================================================================
# RollWidget: Represents a single roll with controls, progress, and a dynamic notes section.
# =============================================================================
class RollWidget(QWidget):
    def __init__(self, job_id, roll_number, labels_goal, printer_name, db_manager):
        super().__init__()
        self.job_id = job_id
        self.roll_number = roll_number
        self.labels_goal = labels_goal
        self.printer_name = printer_name
        self.db_manager = db_manager

        self.current_progress = 0
        self.state = "idle"  # idle, running, paused, stopped, completed
        self.baseline_pass = None
        self.baseline_fail = None

        # Store submitted pause notes as a list of (timestamp, note, progress)
        self.notes_history = []

        self.init_ui()

    def init_ui(self):
        # Main layout: vertical to allow adding a dynamic notes section
        self.main_layout = QVBoxLayout()

        # Controls layout (first row)
        self.controls_layout = QHBoxLayout()
        self.label = QLabel(f"Roll {self.roll_number}")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(self.labels_goal)
        self.progress_bar.setValue(self.current_progress)
        self.pass_count_label = QLabel("Pass: 0")
        self.fail_count_label = QLabel("Fail: 0")
        self.start_btn = QPushButton("Start")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")
        self.start_btn.clicked.connect(self.start_roll)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.confirm_stop)

        for widget in [self.label, self.progress_bar, self.pass_count_label, self.fail_count_label,
                       self.start_btn, self.pause_btn, self.stop_btn]:
            self.controls_layout.addWidget(widget)
        self.main_layout.addLayout(self.controls_layout)

        # Dynamic Notes Section (initially hidden)
        self.notes_section = QWidget()
        self.notes_section_layout = QVBoxLayout()
        self.note_input = QTextEdit()
        self.note_input.setFixedHeight(50)
        self.notes_buttons_layout = QHBoxLayout()
        self.submit_note_btn = QPushButton("Submit Note")
        self.discard_note_btn = QPushButton("Discard Note")
        self.notes_buttons_layout.addWidget(self.submit_note_btn)
        self.notes_buttons_layout.addWidget(self.discard_note_btn)
        self.submit_note_btn.clicked.connect(self.submit_note)
        self.discard_note_btn.clicked.connect(self.discard_note)
        # History area for previously submitted notes
        self.notes_history_label = QLabel("Notes History:")
        self.notes_history_container = QWidget()
        self.notes_history_layout = QVBoxLayout()
        self.notes_history_container.setLayout(self.notes_history_layout)
        self.notes_section_layout.addWidget(self.note_input)
        self.notes_section_layout.addLayout(self.notes_buttons_layout)
        self.notes_section_layout.addWidget(self.notes_history_label)
        self.notes_section_layout.addWidget(self.notes_history_container)
        self.notes_section.setLayout(self.notes_section_layout)
        self.notes_section.setVisible(False)
        self.main_layout.addWidget(self.notes_section)

        self.setLayout(self.main_layout)

    def start_roll(self):
        if self.state != "running":
            self.state = "running"
            self.baseline_pass = None
            self.baseline_fail = None
            self.start_btn.setEnabled(False)
            self.pause_btn.setText("Pause")
            self.db_manager.log_roll_action(self.job_id, self.roll_number, "start")
            print(f"Roll {self.roll_number} started for Job {self.job_id}")

    def toggle_pause(self):
        if self.state == "running":
            self.state = "paused"
            self.pause_btn.setText("Unpause")
            # Show dynamic notes section when pausing
            self.notes_section.setVisible(True)
            print(f"Roll {self.roll_number} paused for Job {self.job_id} at label count {self.current_progress}")
        elif self.state == "paused":
            self.state = "running"
            self.pause_btn.setText("Pause")
            self.notes_section.setVisible(False)
            self.db_manager.log_roll_action(self.job_id, self.roll_number, "resume")
            print(f"Roll {self.roll_number} resumed for Job {self.job_id}")

    def submit_note(self):
        note_text = self.note_input.toPlainText().strip()
        if note_text:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            full_note = f"[{timestamp}] Paused at {self.current_progress}: {note_text}"
            self.notes_history.append(full_note)
            # Display the note in the notes history area
            note_label = QLabel(full_note)
            self.notes_history_layout.addWidget(note_label)
            self.db_manager.log_roll_action(self.job_id, self.roll_number, "pause note", full_note)
            self.note_input.clear()
        # Optionally hide the note section after submission (or leave it open)
        # self.notes_section.setVisible(False)

    def discard_note(self):
        self.note_input.clear()
        # Hide the notes section if no note is entered
        self.notes_section.setVisible(False)

    def confirm_stop(self):
        reply = QMessageBox.question(self, "Confirm Stop",
                                     "Are you sure you want to stop this roll? This action cannot be undone.",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.stop_roll()

    def stop_roll(self):
        if self.state in ["running", "paused"]:
            self.state = "stopped"
            self.db_manager.log_roll_action(self.job_id, self.roll_number, "stop")
            print(f"Roll {self.roll_number} stopped for Job {self.job_id}")
            self.start_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.notes_section.setVisible(False)

    def update_progress(self, cumulative_data):
        if self.state != "running":
            return
        if self.baseline_pass is None:
            self.baseline_pass = cumulative_data.get("pass", 0)
            self.baseline_fail = cumulative_data.get("fail", 0)
            return
        delta_pass = cumulative_data.get("pass", 0) - self.baseline_pass
        delta_fail = cumulative_data.get("fail", 0) - self.baseline_fail
        self.current_progress = min(delta_pass, self.labels_goal)
        self.progress_bar.setValue(self.current_progress)
        self.pass_count_label.setText(f"Pass: {delta_pass}")
        self.fail_count_label.setText(f"Fail: {delta_fail}")
        if self.current_progress >= self.labels_goal:
            self.state = "completed"
            self.db_manager.log_roll_action(self.job_id, self.roll_number, "completed", "Roll complete")
            print(f"Roll {self.roll_number} completed for Job {self.job_id}")
            self.start_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.notes_section.setVisible(False)

# =============================================================================
# JobDetailWidget: Displays roll tracking for a job; includes a "Complete Job" button.
# =============================================================================
class JobDetailWidget(QWidget):
    def __init__(self, job, db_manager):
        super().__init__()
        self.job = job  # (id, customer, job_ticket, inlay_type, quantity, labels_per_roll, printer_name, created_at)
        self.db_manager = db_manager
        self.roll_widgets = []
        self.init_ui()

    def init_ui(self):
        self.main_layout = QVBoxLayout()
        details = QLabel(f"Customer: {self.job[1]} | Job Ticket: {self.job[2]} | Inlay: {self.job[3]} | "
                         f"Quantity: {self.job[4]} | Labels/Roll: {self.job[5]} | Printer: {self.job[6]}")
        self.main_layout.addWidget(details)
        total_rolls = math.ceil(self.job[4] / self.job[5])
        self.main_layout.addWidget(QLabel(f"Total Rolls: {total_rolls}"))
        self.roll_container = QVBoxLayout()
        for roll_num in range(1, total_rolls + 1):
            roll_widget = RollWidget(self.job[0], roll_num, self.job[5], self.job[6], self.db_manager)
            self.roll_widgets.append(roll_widget)
            self.roll_container.addWidget(roll_widget)
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        scroll_widget.setLayout(self.roll_container)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        self.main_layout.addWidget(scroll_area)
        # Complete Job button at the bottom
        self.complete_job_btn = QPushButton("Complete Job")
        self.complete_job_btn.clicked.connect(self.complete_job)
        self.main_layout.addWidget(self.complete_job_btn)
        self.setLayout(self.main_layout)

    def update_rolls(self, update_data):
        printer_name = self.job[6]
        if printer_name in update_data:
            cumulative = update_data[printer_name]
            for roll in self.roll_widgets:
                if roll.state == "running":
                    roll.update_progress(cumulative)
                    break

    def complete_job(self):
        reply = QMessageBox.question(self, "Confirm Completion",
                                     "Are you sure you want to mark this job as complete?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db_manager.update_job_completion(self.job[0], completed=1)
            self.db_manager.log_roll_action(self.job[0], 0, "job completed", "Job marked as complete")
            QMessageBox.information(self, "Job Completed", "This job has been marked as complete.")
            # Optionally, disable the complete job button after completion.
            self.complete_job_btn.setEnabled(False)
            # You may also trigger a refresh in the main window to update the active/completed lists.

# =============================================================================
# EditJobDialog: Allows editing an active job.
# =============================================================================
class EditJobDialog(QDialog):
    def __init__(self, job, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.job = job  # tuple with job details
        self.job_data = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Edit Job")
        layout = QFormLayout(self)
        self.customer_input = QLineEdit(self.job[1])
        self.job_ticket_input = QLineEdit(self.job[2])
        self.inlay_type_input = QLineEdit(self.job[3])
        self.quantity_input = QSpinBox()
        self.quantity_input.setMaximum(100000)
        self.quantity_input.setValue(self.job[4])
        self.labels_per_roll_input = QSpinBox()
        self.labels_per_roll_input.setMaximum(10000)
        self.labels_per_roll_input.setValue(self.job[5])
        self.printer_name_input = QLineEdit(self.job[6])
        layout.addRow("Customer:", self.customer_input)
        layout.addRow("Job Ticket #:", self.job_ticket_input)
        layout.addRow("Inlay Type:", self.inlay_type_input)
        layout.addRow("Quantity:", self.quantity_input)
        layout.addRow("Labels Per Roll:", self.labels_per_roll_input)
        layout.addRow("Printer Name:", self.printer_name_input)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        self.job_data = {
            "customer": self.customer_input.text(),
            "job_ticket": self.job_ticket_input.text(),
            "inlay_type": self.inlay_type_input.text(),
            "quantity": self.quantity_input.value(),
            "labels_per_roll": self.labels_per_roll_input.value(),
            "printer_name": self.printer_name_input.text() or "Printer_1"
        }
        super().accept()

# =============================================================================
# CompletedJobsWidget: Lists completed jobs.
# =============================================================================
class CompletedJobsWidget(QWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.db_manager = db_manager
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Completed Jobs"))
        self.jobs_list = QListWidget()
        self.jobs_list.itemClicked.connect(self.show_job_details)
        layout.addWidget(self.jobs_list)
        self.detail_area = QStackedWidget()
        self.detail_area.addWidget(QLabel("Select a completed job to view details."))
        layout.addWidget(self.detail_area)
        self.setLayout(layout)
        self.load_completed_jobs()

    def load_completed_jobs(self):
        self.jobs_list.clear()
        jobs = self.db_manager.get_completed_jobs()
        for job in jobs:
            item_text = f"{job[2]} - {job[1]} (Printer: {job[6]})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, job)
            self.jobs_list.addItem(item)

    def show_job_details(self, item):
        job = item.data(Qt.UserRole)
        job_detail = JobDetailWidget(job, self.db_manager)
        self.detail_area.addWidget(job_detail)
        self.detail_area.setCurrentWidget(job_detail)

# =============================================================================
# JobFormDialog: A dialog to add a new job.
# =============================================================================
class JobFormDialog(QDialog):
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.job_data = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Add New Job")
        layout = QFormLayout(self)
        self.customer_input = QLineEdit()
        self.job_ticket_input = QLineEdit()
        self.inlay_type_input = QLineEdit()
        self.quantity_input = QSpinBox()
        self.quantity_input.setMaximum(100000)
        self.labels_per_roll_input = QSpinBox()
        self.labels_per_roll_input.setMaximum(10000)
        self.printer_name_input = QLineEdit()
        self.printer_name_input.setPlaceholderText("Default: Printer_1")
        layout.addRow("Customer:", self.customer_input)
        layout.addRow("Job Ticket #:", self.job_ticket_input)
        layout.addRow("Inlay Type:", self.inlay_type_input)
        layout.addRow("Quantity:", self.quantity_input)
        layout.addRow("Labels Per Roll:", self.labels_per_roll_input)
        layout.addRow("Printer Name:", self.printer_name_input)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        if not self.customer_input.text() or not self.job_ticket_input.text():
            QMessageBox.warning(self, "Input Error", "Customer and Job Ticket # are required.")
            return
        printer_name = self.printer_name_input.text() or "Printer_1"
        self.job_data = {
            "customer": self.customer_input.text(),
            "job_ticket": self.job_ticket_input.text(),
            "inlay_type": self.inlay_type_input.text(),
            "quantity": self.quantity_input.value(),
            "labels_per_roll": self.labels_per_roll_input.value(),
            "printer_name": printer_name
        }
        super().accept()

# =============================================================================
# MainWindow: Main UI with two tabs for Active and Completed Jobs, and editing support.
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self, db_manager, csv_monitor):
        super().__init__()
        self.db_manager = db_manager
        self.csv_monitor = csv_monitor
        self.active_jobs = {}  # job_id -> JobDetailWidget (persistent)
        self.setWindowTitle("Printer Monitor & Job Manager")
        self.resize(1000, 600)
        self.init_ui()
        self.csv_monitor.update_signal.connect(self.handle_csv_update)

    def init_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout()

        # Create a tab widget for Active and Completed jobs.
        self.tab_widget = QTabWidget()
        # Active jobs tab:
        self.active_tab = QWidget()
        active_layout = QHBoxLayout()
        # Left panel: Job list with Add and Edit buttons.
        left_panel = QVBoxLayout()
        self.job_list = QListWidget()
        self.job_list.itemClicked.connect(self.load_job_details)
        left_panel.addWidget(QLabel("Active Jobs"))
        left_panel.addWidget(self.job_list)
        btn_layout = QHBoxLayout()
        add_job_btn = QPushButton("Add New Job")
        add_job_btn.clicked.connect(self.open_job_form)
        edit_job_btn = QPushButton("Edit Job")
        edit_job_btn.clicked.connect(self.edit_job)
        btn_layout.addWidget(add_job_btn)
        btn_layout.addWidget(edit_job_btn)
        left_panel.addLayout(btn_layout)
        active_layout.addLayout(left_panel, 1)
        # Right panel: Job detail view (persistent)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(QLabel("Select a job to view details."))
        active_layout.addWidget(self.detail_stack, 3)
        self.active_tab.setLayout(active_layout)
        self.tab_widget.addTab(self.active_tab, "Active Jobs")
        # Completed jobs tab:
        self.completed_tab = CompletedJobsWidget(self.db_manager)
        self.tab_widget.addTab(self.completed_tab, "Completed Jobs")
        main_layout.addWidget(self.tab_widget)
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)
        self.load_jobs_from_db()

    def load_jobs_from_db(self):
        jobs = self.db_manager.get_active_jobs()
        self.job_list.clear()
        for job in jobs:
            item_text = f"{job[2]} - {job[1]} (Printer: {job[6]})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, job)
            self.job_list.addItem(item)

    def open_job_form(self):
        dialog = JobFormDialog(self.db_manager, self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.job_data
            job_id = self.db_manager.add_job(
                data["customer"], data["job_ticket"], data["inlay_type"],
                data["quantity"], data["labels_per_roll"], data["printer_name"]
            )
            QMessageBox.information(self, "Job Added", f"Job ID {job_id} has been added.")
            self.load_jobs_from_db()

    def edit_job(self):
        selected_items = self.job_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Edit Job", "Please select a job to edit.")
            return
        job = selected_items[0].data(Qt.UserRole)
        dialog = EditJobDialog(job, self.db_manager, self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.job_data
            self.db_manager.update_job(job[0], data["customer"], data["job_ticket"],
                                       data["inlay_type"], data["quantity"], data["labels_per_roll"], data["printer_name"])
            QMessageBox.information(self, "Job Updated", "Job has been updated.")
            self.load_jobs_from_db()

    def load_job_details(self, item):
        job = item.data(Qt.UserRole)
        job_id = job[0]
        if job_id in self.active_jobs:
            self.detail_stack.setCurrentWidget(self.active_jobs[job_id])
        else:
            job_detail = JobDetailWidget(job, self.db_manager)
            self.active_jobs[job_id] = job_detail
            self.detail_stack.addWidget(job_detail)
            self.detail_stack.setCurrentWidget(job_detail)

    def handle_csv_update(self, update_data):
        for job_detail in self.active_jobs.values():
            job_detail.update_rolls(update_data)

# =============================================================================
# Main entry point
# =============================================================================
def main():
    monitored_dir = r"C:\Users\Encoding 3\Desktop\Encoding Void Reports"
    db_path = os.path.join(monitored_dir, "printer_jobs.db")
    if not os.path.exists(monitored_dir):
        os.makedirs(monitored_dir)
    db_manager = DatabaseManager(db_path)
    csv_monitor = WatchdogCSVMonitor(monitored_dir)
    app = QApplication(sys.argv)
    main_win = MainWindow(db_manager, csv_monitor)
    main_win.show()
    csv_monitor.start()
    try:
        sys.exit(app.exec_())
    finally:
        csv_monitor.stop()

if __name__ == '__main__':
    main()
