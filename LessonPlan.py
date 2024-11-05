from LessonPlanDownloader import LessonPlanDownloader
import os, requests, time
from colorama import init, Fore, Style
from difflib import SequenceMatcher


init(autoreset=True)  # Initialize colorama
import pandas as pd
import openpyxl
import os, re
import pymongo
from datetime import datetime


class LessonPlan(LessonPlanDownloader):
    def __init__(self, username, password, mongo_uri, plan_config, directory=""):
        super().__init__(username, password, directory, plan_config["download_url"])
        self.plan_config = plan_config
        self.sheet_name = plan_config["sheet_name"]
        self.plans_directory = os.path.join(
            os.getenv("PLANS_DIRECTORY", "lesson_plans"),
            self.plan_config["name"].replace(" ", "_"),
        )
        os.makedirs(self.plans_directory, exist_ok=True)
        self.converted_lesson_plan = None
        self.groups = plan_config["groups"]
        self.group_columns = {}
        self.save_to_mongodb = os.getenv("SAVE_TO_MONGODB", "true").lower() == "true"
        self.save_to_file = os.getenv("SAVE_TO_FILE", "true").lower() == "true"
        self.plans_directory = os.getenv("PLANS_DIRECTORY", "lesson_plans")
        self.schedule_type = plan_config.get("category", "st")  # Default to standard schedule

        if self.save_to_mongodb:
            try:
                self.mongo_client = pymongo.MongoClient(mongo_uri)
                self.db = self.mongo_client["Lesson_dev"]
                print("Successfully connected to MongoDB")
            except pymongo.errors.ConnectionFailure as e:
                print(f"Could not connect to MongoDB: {e}")
            except Exception as e:
                print(f"An error occurred: {e}")
    def get_schedule_headers(self):
        """Return appropriate headers based on schedule type"""
        headers = {
            "st": ["Godziny", "Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek"],
            "nst": ["Godziny", "Piątek", "Sobota", "Niedziela"],
            "nst-puw": ["Godziny", "Sobota", "Niedziela"]
        }
        return headers.get(self.schedule_type, headers["st"])
    def process_and_save_plan(self):
        """Process and save the lesson plan, returns checksum if plan was processed"""
        new_checksum = self.download_file()
        if not new_checksum:
            print("Failed to download file.")
            return None

        should_process = True

        if self.save_to_mongodb:
            # Use plan-specific collection
            collection_name = f"plans_{self.plan_config['name'].lower().replace(' ', '_').replace('-', '_')}"
            collection = self.db[collection_name]
            # Check MongoDB for changes
            latest_plan = collection.find_one(
                {"plan_name": self.plan_config["name"]}, sort=[("timestamp", -1)]
            )

            latest_checksum = (
                latest_plan.get("checksum").split("_")[0]
                if latest_plan and latest_plan.get("checksum")
                else None
            )

            if latest_checksum and latest_checksum == new_checksum:
                print(
                    f"Plan has not changed (MongoDB check in {collection_name}, checksum: {new_checksum})."
                )
                return False  # Plan się nie zmienił
            else:
                print(
                    f"Plan has changed or no previous plan found (old checksum: {latest_checksum}, new checksum: {new_checksum})"
                )
                should_process = True
        else:
            # Check local files for changes
            if os.path.exists(self.plans_directory):
                files = [
                    f for f in os.listdir(self.plans_directory) if f.endswith(".pkl")
                ]
                if files:
                    latest_file = max(
                        files,
                        key=lambda x: os.path.getctime(
                            os.path.join(self.plans_directory, x)
                        ),
                    )
                    print(f"Found latest local file: {latest_file}")

        if should_process:
            print(f"Processing plan for {self.plan_config['name']}")

            # Always process the downloaded file
            self.unmerge_and_fill_data()
            self.clean_excel_file()

            # Read the entire plan as DataFrame
            try:
                # Process individual groups
                self.find_group_columns_with_similarity()
                for group in self.groups.keys():
                    df_group = self.get_lessons_for_group(group)
                    if df_group is not None and not df_group.empty:
                        self.save_group_lessons(group, df_group)
                    else:
                        print(f"No data available for group '{group}'.")

                if self.save_to_mongodb:
                    self.convert_to_html_and_save_to_db(new_checksum)

            except Exception as e:
                print(f"Error processing plan: {str(e)}")
                return None

        return new_checksum

    def get_converted_lesson_plan(self):
        return self.converted_lesson_plan

    def get_groups(self):
        return self.groups

    def unmerge_and_fill_data(self):
        if not self.file_save_path:
            print("No file has been downloaded yet. Please run download_file() first.")
            return False
        wb = openpyxl.load_workbook(self.file_save_path)
        ws = wb[self.sheet_name]

        merged_cells = list(ws.merged_cells)

        for merged_range in merged_cells:
            cell_range = str(merged_range)
            min_col, min_row, max_col, max_row = merged_range.bounds
            merged_value = ws.cell(row=min_row, column=min_col).value
            ws.unmerge_cells(cell_range)

            for row in range(min_row, max_row + 1):
                for col in range(min_col, max_col + 1):
                    ws.cell(row=row, column=col).value = merged_value

        dir_path = os.path.dirname(self.file_save_path)
        file_name = os.path.basename(self.file_save_path)
        new_file_name = "unmerged_" + file_name
        self.converted_lesson_plan = os.path.join(dir_path, new_file_name)

        wb.save(self.converted_lesson_plan)
        print(
            f"Unmerged file saved as: {self.converted_lesson_plan}{Style.RESET_ALL}"
        )
        return True

    @staticmethod
    def clean_text(text):
        if isinstance(text, str):
            text = text.replace("\n", " ")
            text = re.sub(" +", " ", text)
            text = text.replace("\t", " ")
            return text.strip()
        return text

    def clean_excel_file(self):
        if not self.converted_lesson_plan:
            print("No converted file found. Please run unmerge_and_fill_data() first.")
            return False

        file_name, file_extension = os.path.splitext(self.converted_lesson_plan)
        temp_file = file_name + "_temp" + file_extension

        try:
            with pd.ExcelFile(self.converted_lesson_plan) as xls:
                sheet_names = xls.sheet_names
                wb = openpyxl.Workbook()
                wb.remove(wb.active)
                for sheet_name in sheet_names:
                    # Read data into DataFrame
                    df = pd.read_excel(xls, sheet_name=sheet_name)

                    # Clean text data - 
                    #df = df.apply(self.clean_text)

                    # Create new worksheet
                    ws = wb.create_sheet(title=sheet_name)

                    # Write headers
                    for col_num, value in enumerate(df.columns.values, 1):
                        ws.cell(row=1, column=col_num, value=value)

                    # Write data
                    for row_num, row in enumerate(df.values, 2):
                        for col_num, value in enumerate(row, 1):
                            cell = ws.cell(row=row_num, column=col_num, value=value)
                            # Reset all formatting
                            cell.font = openpyxl.styles.Font()
                            cell.fill = openpyxl.styles.PatternFill()
                            cell.border = openpyxl.styles.Border()
                            cell.alignment = openpyxl.styles.Alignment(wrap_text=False)
                            cell.number_format = "General"

                # Save workbook
                wb.save(temp_file)

            # Wait briefly for file operations to complete
            time.sleep(1)

            # Replace original file with cleaned version
            max_attempts = 5
            for attempt in range(max_attempts):
                try:
                    os.remove(self.converted_lesson_plan)
                    os.rename(temp_file, self.converted_lesson_plan)
                    break
                except PermissionError:
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                    else:
                        raise

            print(f"Cleaned file saved as: {self.converted_lesson_plan}")
            return True

        except Exception as e:
            print(f"An error occurred while cleaning the file: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False

    def find_group_columns(self):
        if not self.converted_lesson_plan:
            print("No converted file found. Please run unmerge_and_fill_data() first.")
            return False

        try:
            df = pd.read_excel(self.converted_lesson_plan, sheet_name=self.sheet_name)
            for key, value in self.groups.items():
                columns = df.columns[df.isin([value]).any()].tolist()
                self.group_columns[key] = columns

            return self.group_columns

        except Exception as e:
            print(f"An error occurred while finding group columns: {str(e)}")
            return None
        
    def find_group_columns_with_similarity(self):
        if not self.converted_lesson_plan:
            print("No converted file found. Please run unmerge_and_fill_data() first.")
            return False

        try:
            df = pd.read_excel(self.converted_lesson_plan, sheet_name=self.sheet_name)
            used_columns = set()
            group_columns = {}

            def calculate_similarity(text1, text2):
                if not isinstance(text1, str) or not isinstance(text2, str):
                    return 0.0
                
                # Normalize texts
                text1 = ' '.join(text1.lower().split())
                text2 = ' '.join(text2.lower().split())
                
                # Remove common prefixes
                text1 = text1.replace('sp.:', '').replace('sps.:', '').strip()
                text2 = text2.replace('sp.:', '').replace('sps.:', '').strip()
                
                # Remove newlines but keep group numbers
                text1 = re.sub(r'\s*\n\s*', ' ', text1)
                text2 = re.sub(r'\s*\n\s*', ' ', text2)
                
                return SequenceMatcher(None, text1, text2).ratio()

            def is_matching_group(text, pattern, group_number=None):
                similarity = calculate_similarity(str(text), str(pattern))
                
                if similarity < 0.85:  
                    return False
                    
                if group_number:
                    group_pattern = rf"gr+upa\s*{group_number}\b"
                    has_group = bool(re.search(group_pattern, str(text).lower()))
                    return has_group
                    
                return True

            for group_name, group_identifier in self.groups.items():
                matching_columns = []
                
                group_number = None
                group_match = re.search(r'gr+upa\s*(\d+)', group_identifier.lower())
                if group_match:
                    group_number = group_match.group(1)
                
                for column in df.columns:
                    if column in used_columns:
                        continue
                        
                    unique_values = df[column].dropna().unique()
                    
                    for value in unique_values:
                        if is_matching_group(value, group_identifier, group_number):
                            matching_columns.append(column)
                            used_columns.add(column)
                            break

                group_columns[group_name] = matching_columns

            self.group_columns = group_columns
            return self.group_columns

        except Exception as e:
            print(f"An error occurred while finding group columns: {str(e)}")
            return None

    def get_lessons_for_group(self, group_name):
      if not self.converted_lesson_plan:
          print("No converted file found. Please run unmerge_and_fill_data() first.")
          return None

      if not self.group_columns:
          self.find_group_columns_with_similarity()

      try:
          print(f"\nReading Excel file for group: {group_name}")
          df = pd.read_excel(
              self.converted_lesson_plan, sheet_name=self.sheet_name, header=None
          )

          if group_name not in self.group_columns:
              print(f"Group '{group_name}' not found.")
              return None

          # Extract column numbers from the column names
          group_col_indices = []
          for col_name in self.group_columns[group_name]:
              # Extract the number after the last dot
              col_number = int(col_name.split('.')[-1]) if '.' in col_name else None
              if col_number is not None:
                  group_col_indices.append(col_number)

          if not group_col_indices:
              print(f"Could not extract column numbers for group {group_name}")
              return None

          print(f"Found column indices for {group_name}: {group_col_indices}")
          
          # Add time column (always first column) and sort indices
          columns_to_extract = [0] + group_col_indices
          print(f"Columns to extract: {columns_to_extract}")
          
          # Extract columns
          df_filtered = df.iloc[:, columns_to_extract]

          # Remove title rows
          df_filtered = df_filtered[
              ~df_filtered.apply(
                  lambda row: row.astype(str)
                  .str.contains("INFORMATYKA.*SEMESTR", case=False, regex=True)
                  .any(),
                  axis=1,
              )
          ]

          # Remove header rows and reset index
          df_filtered = df_filtered.iloc[4:-1]
          df_filtered = df_filtered[
              ~df_filtered[0]
              .astype(str)
              .str.contains("godz.|GODZ.", case=False, regex=True)
          ]

          # Remove rows where all group columns are NaN
          df_filtered = df_filtered.dropna(subset=df_filtered.columns[1:], how="all")

          # Set headers
          headers = self.get_schedule_headers()
          df_filtered.columns = headers[: len(df_filtered.columns)]

          # Reset index
          df_filtered = df_filtered.reset_index(drop=True)

          return df_filtered

      except Exception as e:
          print(
              f"An error occurred while getting lessons for group '{group_name}': {str(e)}"
          )
          import traceback
          traceback.print_exc()
          return None

    def save_group_lessons(self, group_name, df):
        if df is None or df.empty:
            print(f"No data to save for group '{group_name}'.")
            return

        # Create a directory for group files if it doesn't exist
        group_dir = os.path.join(
            os.path.dirname(self.converted_lesson_plan), "group_lessons"
        )
        os.makedirs(group_dir, exist_ok=True)

        # Create a file name for the group
        file_name = f"{group_name.replace(' ', '_')}_lessons.xlsx"
        file_path = os.path.join(group_dir, file_name)

        try:
            # Truncate sheet name to 31 characters
            sheet_name = group_name[:31]

            # Save the DataFrame to an Excel file
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name=sheet_name)

            print(f"Lessons for group '{group_name}' saved to {file_path}")
            if len(group_name) > 31:
                print(f"Note: Sheet name was truncated to '{sheet_name}'")
        except Exception as e:
            print(
                f"An error occurred while saving lessons for group '{group_name}': {str(e)}"
            )

    @staticmethod
    def get_column_letter(column_number):
        """Convert a column number to a column letter (A, B, C, ..., Z, AA, AB, ...)."""
        dividend = column_number
        column_letter = ""
        while dividend > 0:
            dividend, remainder = divmod(dividend - 1, 26)
            column_letter = chr(65 + remainder) + column_letter
        return column_letter

    def convert_to_html_and_save_to_db(self, checksum):
        if not self.group_columns:
            print("No group columns found. Please run find_group_columns() first.")
            return

        current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plans_data = {
            "timestamp": current_datetime,
            "checksum": checksum,  # Just store the raw checksum
            "plan_name": self.plan_config["name"],
            "category": self.schedule_type,
            "groups": {},
        }

        # Create plans directory if saving to files is enabled
        if self.save_to_file:
            plan_specific_directory = os.path.join(
                self.plans_directory, self.plan_config["name"].replace(" ", "_")
            )
            os.makedirs(plan_specific_directory, exist_ok=True)
            self.plans_directory = plan_specific_directory

        for group_name in self.groups.keys():
            df = self.get_lessons_for_group(group_name)
            if df is None or df.empty:
                print(f"No data available for group '{group_name}'.")
                continue

            html = self.generate_html_table(df)
            plans_data["groups"][group_name] = html

            # Save DataFrame to file if enabled
            if self.save_to_file:
                file_name = f"{current_datetime.replace(':', '-')}_{group_name}.pkl"
                file_path = os.path.join(self.plans_directory, file_name)
                df.to_pickle(file_path)
                print(f"Lesson plan for {group_name} saved to file: {file_path}")

        # Save to MongoDB if enabled
        if self.save_to_mongodb:

            collection_name = f"plans_{self.plan_config['name'].lower().replace(' ','_').replace('-', '_')}"
            collection = self.db[collection_name]

            # Check if this checksum already exists in this collection
            existing_plan = collection.find_one({"checksum": checksum})
            if existing_plan:
                print(
                    f"Plan with checksum {checksum} alreadyexists in collection {collection_name}. Skipping save."
                )
                return

            # Insert the new plan
            collection.insert_one(plans_data)
            print(
                f"Lesson plans for {self.plan_config['name']} timestamp {current_datetime} saved to MongoDB collection        {collection_name} with checksum {checksum}."
            )

    def generate_html_table(self, df):
        html = "<table border='1'>\n"

        # Add header row
        html += "<tr>\n"
        for col in df.columns:
            html += f"<th>{' '.join([f'<b>{word}</b>' for word in col.split()])}</th>\n"
        html += "</tr>\n"

        # Add data rows
        for _, row in df.iterrows():
            html += "<tr>\n"
            for i, cell in enumerate(row):
                formatted_cell = self.format_cell(
                    cell, is_header=False, is_time_column=(i == 0)
                )
                html += f"<td>{formatted_cell}</td>\n"
            html += "</tr>\n"

        html += "</table>"
        return html

    def format_cell(self, cell, is_header, is_time_column):
        if pd.isna(cell):
            return ""
        cell = str(cell)
        if is_time_column and cell != "godziny":
            parts = cell.replace(" ", "").split("-")
            if len(parts) == 2:
                start, end = parts
                formatted_start = self.format_time(start)
                formatted_end = self.format_time(end)
                return f"{formatted_start} - {formatted_end}"
        return cell

    def format_time(self, time):
        if len(time) == 3:
            return f"{time[0]}<sup>{time[1:]}</sup>"
        elif len(time) == 4:
            return f"{time[:2]}<sup>{time[2:]}</sup>"
        return time

    def full_action(self):
        self.process_and_save_plan()
