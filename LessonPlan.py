from LessonPlanDownloader import LessonPlanDownloader
import os, time
from colorama import init, Style
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
import requests
import datetime
from html import escape as html_escape
import pytz
import json
import pymongo
from shared_utils import get_logger


init(autoreset=True)  
import pandas as pd
import openpyxl
import re

# Setup logger
logger = get_logger('LessonPlan')

# Bump when HTML generation changes - stored plans with another version get
# re-processed even if the Excel file itself did not change.
# v7: cell text is HTML-escaped (the plan HTML is rendered as markup in the browser)
# v8: meeting calendar from the sheet header stored with the plan ("zjazdy")
PARSER_VERSION = 8

_DAY_NAMES = {
    'PONIEDZIALEK': 'Poniedziałek', 'WTOREK': 'Wtorek', 'SRODA': 'Środa',
    'CZWARTEK': 'Czwartek', 'PIATEK': 'Piątek', 'SOBOTA': 'Sobota', 'NIEDZIELA': 'Niedziela',
}
_TIME_SLOT_RE = re.compile(r'^\s*(\d{1,2})[.:]?(\d{2})\s*[-–—]\s*(\d{1,2})[.:]?(\d{2})')


def config_fingerprint(plan_config):
    """Hash of config fields that change the generated HTML."""
    import hashlib
    relevant = {k: plan_config.get(k) for k in ("sheet_name", "groups", "group_aliases", "category", "mixed")}
    return hashlib.md5(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode(), usedforsecurity=False).hexdigest()


def _squash(text):
    """Compare key tolerant to manual typing: letters/digits only, no diacritics."""
    import unicodedata
    t = unicodedata.normalize('NFKD', str(text or '').lower().replace('ł', 'l'))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in t if not unicodedata.combining(c)))


def _day_name(value):
    """Weekday for a header cell, tolerating typos like 'PIATEK' or 'Sobota.'"""
    key = _squash(value).upper()
    if not key or len(key) > 14:
        return None
    if key in _DAY_NAMES:
        return _DAY_NAMES[key]
    from difflib import get_close_matches
    close = get_close_matches(key, _DAY_NAMES.keys(), n=1, cutoff=0.8)
    return _DAY_NAMES[close[0]] if close else None

class LessonPlan(LessonPlanDownloader):
    def __init__(self, username, password, mongo_uri, plan_config, directory=""):
        super().__init__(username, password, directory, plan_config["download_url"])
        self.plan_config = plan_config
        # Replace both spaces and underscores in faculty with hyphens
        faculty_name = plan_config['faculty'].replace(' ', '-').replace('_', '-')
        self.collection_name = f"plans_{faculty_name}_{plan_config['name'].lower().replace(' ', '_')}"
        self.sheet_name = plan_config["sheet_name"]
        self.plans_directory = os.path.join(
            os.getenv("PLANS_DIRECTORY", "lesson_plans"),
            self.plan_config["name"].replace(" ", "_"),
        )
        os.makedirs(self.plans_directory, exist_ok=True)
        self.converted_lesson_plan = None
        # Handle both old (string) and new (dict) formats for groups
        self.groups = {}
        self.group_column_counts = {}  # Store expected column counts for mixed plans
        raw_groups = plan_config.get("groups") or {}

        # Also check for groups_column_info at the top level (alternative format)
        groups_column_info = plan_config.get("groups_column_info", {})

        for key, value in raw_groups.items():
            if isinstance(value, dict):
                # New format: {"identifier": "...", "columns": N}
                self.groups[key] = value["identifier"]
                self.group_column_counts[key] = value.get("columns", None)
            else:
                # Old format: just a string identifier
                self.groups[key] = value
                # Try to get column count from groups_column_info if available
                self.group_column_counts[key] = groups_column_info.get(key, None)

        # Store mixed flag for MongoDB
        self.is_mixed = plan_config.get("mixed", False)
        self.group_columns = {}
        self.save_to_mongodb = os.getenv("SAVE_TO_MONGODB", "true").lower() == "true"
        self.save_to_file = os.getenv("SAVE_TO_FILE", "false").lower() == "true"
        self.plans_directory = os.getenv("PLANS_DIRECTORY", "lesson_plans")
        self.schedule_type = plan_config.get(
            "category", "st"
        )  # Default to standard schedule
        clean_excel_value = plan_config.get("clean_excel", os.getenv("CLEAN_EXCEL_FILE", "false"))
        if isinstance(clean_excel_value, str):
            self.clean_excel_enabled = clean_excel_value.lower() == "true"
        else:
            self.clean_excel_enabled = bool(clean_excel_value)

        # Per-plan processing caches (refreshed for each downloaded file)
        self._sheet_df_named_cache = None
        self._sheet_df_raw_cache = None
        self._sheet_df_cache_file = None
        self._weekday_column_map_cache = None

        if self.save_to_mongodb:
            try:
                self.mongo_client = pymongo.MongoClient(mongo_uri)
                self.db = self.mongo_client[os.getenv("MONGO_DB", "Lesson_dev")]
                logger.info("Successfully connected to MongoDB")
            except pymongo.errors.ConnectionFailure as e:
                logger.error(f"Could not connect to MongoDB: {e}")
            except Exception as e: 
                logger.error(f"An error occurred: {e}")
        if not plan_config.get("groups"):
            # Empty dict {} or None -> treat as whole faculty
            self.groups = {"cały kierunek": "all"}
        else:
            raw_groups = plan_config["groups"]
            self.groups = {}
            for key, value in raw_groups.items():
                if isinstance(value, dict):
                    self.groups[key] = value["identifier"]
                else:
                    self.groups[key] = value

        self.group_columns = {}

    def _invalidate_processing_cache(self):
        self._sheet_df_named_cache = None
        self._sheet_df_raw_cache = None
        self._sheet_df_cache_file = None
        self._weekday_column_map_cache = None

    def _get_sheet_df(self, header_none=False):
        if not self.converted_lesson_plan:
            return None

        cache_attr = "_sheet_df_raw_cache" if header_none else "_sheet_df_named_cache"

        if (
            self._sheet_df_cache_file == self.converted_lesson_plan
            and getattr(self, cache_attr) is not None
        ):
            return getattr(self, cache_attr)

        read_header = None if header_none else 0
        df = pd.read_excel(
            self.converted_lesson_plan,
            sheet_name=self.sheet_name,
            header=read_header,
        )

        if self._sheet_df_cache_file != self.converted_lesson_plan:
            # New source file: reset both caches first.
            self._sheet_df_named_cache = None
            self._sheet_df_raw_cache = None
            self._weekday_column_map_cache = None
            self._sheet_df_cache_file = self.converted_lesson_plan

        setattr(self, cache_attr, df)
        return df

    def _get_weekday_column_map(self):
        if self._weekday_column_map_cache is None and self.converted_lesson_plan:
            self._weekday_column_map_cache = self.detect_weekday_columns(
                self.converted_lesson_plan, self.sheet_name
            )
        return self._weekday_column_map_cache or {}

    def _generate_groups_html(self, file_path):
        """Build HTML for every group straight from the Excel sheet (openpyxl).

        Handles merged cells (multi-slot lessons, lectures shared by groups),
        groups with several sub-columns per day and takes day names from the
        sheet itself. Returns {group_name: html} or None when the layout is
        not recognised (caller falls back to the legacy pandas pipeline).
        """
        wb = openpyxl.load_workbook(file_path)
        try:
            if self.sheet_name not in wb.sheetnames:
                return None
            ws = wb[self.sheet_name]

            # Merged ranges: every covered cell reads the top-left value
            merged = {}
            for rng in ws.merged_cells.ranges:
                value = ws.cell(rng.min_row, rng.min_col).value
                for r in range(rng.min_row, rng.max_row + 1):
                    for c in range(rng.min_col, rng.max_col + 1):
                        merged[(r, c)] = value

            def cell(r, c):
                return merged[(r, c)] if (r, c) in merged else ws.cell(r, c).value

            max_col, max_row = ws.max_column, ws.max_row

            # Row with day names - the one naming the most distinct days
            days_row, day_starts = None, []
            for r in range(1, 13):
                found = []
                for c in range(2, max_col + 1):
                    day = _day_name(cell(r, c))
                    if day and (not found or found[-1][1] != day):
                        found.append((c, day))
                if len(found) > len(day_starts):
                    days_row, day_starts = r, found
            if not day_starts:
                return None

            # Header row under the days: GRUPA row if present, used for group mapping
            whole_programme = list(self.groups) == ["cały kierunek"]
            header_row, group_cols = None, {}
            if not whole_programme:
                aliases = self.plan_config.get("group_aliases") or {}
                wanted = {name: {_squash(ident)} | {_squash(a) for a in aliases.get(name, [])}
                          for name, ident in self.groups.items()}
                best_hits = 0
                for r in range(days_row + 1, days_row + 5):
                    row_keys = {_squash(cell(r, c)) for c in range(1, max_col + 1)}
                    hits = sum(1 for keys in wanted.values() if keys & row_keys)
                    if hits > best_hits:
                        header_row, best_hits = r, hits
                if header_row is None:
                    return None
                for name, keys in wanted.items():
                    group_cols[name] = [c for c in range(2, max_col + 1)
                                        if _squash(cell(header_row, c)) in keys]
                if any(not cols for cols in group_cols.values()):
                    missing = [n for n, cols in group_cols.items() if not cols]
                    logger.warning(f"Groups not found in sheet header: {missing}")
                    return None

            # Column span of each day: up to the next day; last day ends at the
            # last column that has a group header (or content)
            header_probe = header_row or days_row + 1
            last_col = max((c for c in range(2, max_col + 1)
                            if cell(header_probe, c) or cell(days_row, c)), default=max_col)
            spans = []
            for i, (start, day) in enumerate(day_starts):
                end = day_starts[i + 1][0] - 1 if i + 1 < len(day_starts) else last_col
                spans.append((day, start, end))
            if whole_programme:
                group_cols = {"cały kierunek": list(range(spans[0][1], spans[-1][2] + 1))}

            # Time slot rows
            time_rows = []
            stop_words = ['program studiów', 'uwaga', 'uwagi:', 'praktyka zawodowa']
            for r in range((header_row or days_row) + 1, max_row + 1):
                label = str(cell(r, 1) or '').strip()
                if any(sw in label.lower() for sw in stop_words):
                    break
                m = _TIME_SLOT_RE.match(label)
                if m:
                    h1, m1, h2, m2 = m.groups()
                    time_rows.append((r, f"{int(h1)}<sup>{m1}</sup> - {int(h2)}<sup>{m2}</sup>"))
            if not time_rows:
                return None

            # Exams and extra information printed above / below the table
            try:
                from plan_notes import parse_notes

                def row_texts(rows):
                    texts = []
                    for r in rows:
                        for c in range(1, max_col + 1):
                            v = cell(r, c)
                            if v is not None and str(v).strip() and str(v) not in texts:
                                texts.append(str(v))
                    return texts
                last_time_row = time_rows[-1][0]
                self.last_notes = parse_notes(row_texts(range(1, days_row)),
                                              row_texts(range(last_time_row + 1, max_row + 1)))
                from zjazdy import parse_header_calendar
                self.last_zjazdy = parse_header_calendar('\n'.join(row_texts(range(1, days_row))))
            except Exception as e:
                logger.warning(f"Could not parse notes around the table: {e}")
                self.last_notes = None
                self.last_zjazdy = None

            result = {}
            for group, cols in group_cols.items():
                parts = ["<table border='1'>\n<tr>\n<th><b>Godziny</b></th>\n"]
                parts += [f"<th><b>{html_escape(day)}</b></th>\n" for day, _, _ in spans]
                parts.append("</tr>\n")
                rows_written = 0
                for r, time_label in time_rows:
                    texts_per_day = []
                    for _, start, end in spans:
                        texts = []
                        for c in (c for c in cols if start <= c <= end):
                            value = cell(r, c)
                            text = str(value).strip() if value is not None else ''
                            if text and text not in texts:
                                texts.append(text)
                        texts_per_day.append("\n\n".join(html_escape(t) for t in texts))
                    if not any(texts_per_day):
                        continue
                    parts.append(f"<tr>\n<td>{time_label}</td>\n")
                    parts += [f"<td>{t}</td>\n" for t in texts_per_day]
                    parts.append("</tr>\n")
                    rows_written += 1
                parts.append("</table>")
                # A group with no classes this semester is still a recognised layout
                result[group] = "".join(parts) if rows_written else (
                    "<p>Brak zajęć w planie tej grupy w bieżącym semestrze.</p>")
            logger.info(f"Generated HTML from sheet for {len(result)} groups "
                        f"({', '.join(d for d, _, _ in spans)})")
            return result
        finally:
            wb.close()

    def _record_changes(self, collection, collection_name, new_groups, new_checksum):
        """Diff the new groups against the latest stored version and store the changes
        in plan_changes. Never blocks saving the new version."""
        try:
            from plan_differ import diff_plans, save_diff_to_db
            latest = collection.find_one({"groups": {"$exists": True}}, sort=[("_id", -1)])
            if not latest or not latest.get("groups"):
                return
            changes = diff_plans(latest["groups"], new_groups)
            if changes:
                save_diff_to_db(self.db, collection_name, self.plan_config["name"], changes,
                                latest.get("checksum", ""), new_checksum)
                logger.info(f"Detected {len(changes)} changes in {collection_name}")
        except Exception as e:
            logger.warning(f"Diff failed (non-critical): {e}")

    def _generate_html_direct(self, checksum):
        """Generate HTML table directly from Excel using openpyxl.
        Used for plans without groups ('cały kierunek') to bypass the complex pipeline."""
        import openpyxl as xl

        file_path = self.file_save_path or os.path.join(self.directory or '', 'downloaded_file.xlsx')
        if not os.path.exists(file_path):
            return None

        wb = xl.load_workbook(file_path)
        if self.sheet_name not in wb.sheetnames:
            wb.close()
            return None

        ws = wb[self.sheet_name]

        # Find the row with day names (PONIEDZIAŁEK etc.)
        days_row = None
        day_names = {
            'st': ['PONIEDZIAŁEK', 'WTOREK', 'ŚRODA', 'CZWARTEK', 'PIĄTEK'],
            'nst': ['PIĄTEK', 'SOBOTA', 'NIEDZIELA'],
            'nst-online': ['SOBOTA', 'NIEDZIELA'],
        }
        expected_days = day_names.get(self.schedule_type, day_names['st'])

        for r in range(1, 7):
            for c in range(1, ws.max_column + 1):
                v = ws.cell(r, c).value
                if v and str(v).upper().strip() in expected_days:
                    days_row = r
                    break
            if days_row:
                break

        if not days_row:
            wb.close()
            return None

        # Collect day columns
        day_cols = []
        for c in range(1, ws.max_column + 1):
            v = ws.cell(days_row, c).value
            if v and str(v).upper().strip() in expected_days:
                day_cols.append((c, str(v).strip()))

        # Find time column (usually col 1) and data start row (row after days)
        time_col = 1
        data_start = days_row + 2  # skip GRUPA row

        # Find GODZ row
        for r in range(days_row + 1, days_row + 4):
            v = ws.cell(r, 1).value
            if v and 'GODZ' in str(v).upper():
                data_start = r + 1
                break

        # Build HTML table
        headers = ['Godziny'] + [name for _, name in day_cols]
        html = "<table border='1'>\n<tr>\n"
        for h in headers:
            html += f"<th><b>{html_escape(h)}</b></th>\n"
        html += "</tr>\n"

        # Stop words — rows with these indicate end of schedule data
        stop_words = ['program studiów', 'uwaga', 'uwagi:', 'praktyka zawodowa']

        for r in range(data_start, ws.max_row + 1):
            time_val = ws.cell(r, time_col).value
            if not time_val:
                continue

            time_str = str(time_val).strip()

            # Stop if we hit non-schedule content
            if any(sw in time_str.lower() for sw in stop_words):
                break

            if not any(c.isdigit() for c in time_str):
                continue

            # Format time with superscript
            import re
            time_formatted = re.sub(r'(\d+)(\d{2})', r'\1<sup>\2</sup>', html_escape(time_str))

            html += "<tr>\n"
            html += f"<td>{time_formatted}</td>\n"

            for col_idx, _ in day_cols:
                cell_val = ws.cell(r, col_idx).value
                cell_text = str(cell_val).strip() if cell_val else ''
                # Skip non-schedule content in data cells
                if cell_text and any(sw in cell_text.lower() for sw in ['egzamin', 'program studiów', 'praktyka zawodowa']):
                    cell_text = ''
                html += f"<td>{html_escape(cell_text)}</td>\n"

            html += "</tr>\n"

        html += "</table>"
        wb.close()

        logger.info(f"Generated direct HTML table for 'cały kierunek': {len(headers)-1} days, from row {data_start}")
        return html

    def get_schedule_headers(self, num_columns):
        """Return appropriate headers based on schedule type and actual number of columns"""
        base_headers = {
            "st": ["Godziny", "Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek"],
            "nst": ["Godziny", "Piątek", "Sobota", "Niedziela"],
            "nst-online": ["Godziny", "Sobota", "Niedziela"],
        }

        # Get base headers for schedule type
        headers = base_headers.get(self.schedule_type, base_headers["st"])

        # If we have more columns than headers, add numbered columns
        while len(headers) < num_columns:
            headers.append(f"Column_{len(headers)}")

        # If we have more headers than columns, trim the headers
        headers = headers[:num_columns]

        return headers

    def detect_weekday_columns(self, excel_file, sheet_name):
        """Dynamically detect which columns represent which weekdays by scanning headers"""
        import openpyxl

        wb = openpyxl.load_workbook(excel_file, read_only=True)
        ws = wb[sheet_name]

        # Day patterns to search for (case-insensitive)
        day_patterns = {
            'PONIEDZIAŁEK': 'Poniedziałek',
            'PONIEDZIALEK': 'Poniedziałek',  # Without Polish characters
            'WTOREK': 'Wtorek',
            'ŚRODA': 'Środa',
            'SRODA': 'Środa',  # Without Polish characters
            'CZWARTEK': 'Czwartek',
            'PIĄTEK': 'Piątek',
            'PIATEK': 'Piątek',  # Without Polish characters
            'SOBOTA': 'Sobota',
            'NIEDZIELA': 'Niedziela'
        }

        # Search for day headers in first 10 rows (sometimes headers are lower)
        day_column_map = {}  # {column_index: day_name}

        # Track which columns have been found
        found_days = set()

        for row_idx in range(1, 11):  # Check more rows
            row = ws[row_idx]
            for col_idx, cell in enumerate(row, 1):  # Start from 1 to match Excel column numbering
                if cell.value and isinstance(cell.value, str):
                    value = str(cell.value).upper().strip()

                    # Check if this cell contains a day name
                    for pattern, day_name in day_patterns.items():
                        if pattern in value or value == pattern:
                            # Found a day header
                            if col_idx not in day_column_map:
                                day_column_map[col_idx] = day_name
                                found_days.add(day_name)
                                logger.debug(f"Detected {day_name} in Excel column {col_idx} (row {row_idx}, pattern: '{pattern}', cell value: '{cell.value}')")
                                break  # Stop checking patterns for this cell

        # Log summary
        if day_column_map:
            logger.debug(f"Successfully detected {len(day_column_map)} day columns")
            logger.debug(f"Day column mapping: {day_column_map}")
        else:
            logger.debug("No day headers detected in Excel file")

        wb.close()
        return day_column_map

    def expand_to_full_week_mixed(self, df_filtered, column_indices, expected_columns=None):
        """
        Expand DataFrame for mixed plans to include all weekdays.
        Missing days will have EMPTY columns, not duplicated data.
        Uses column count information to determine exact placement.

        Args:
            df_filtered: DataFrame with actual data
            column_indices: Excel column indices where data was found
            expected_columns: Expected number of columns for this group (from plans.json)
        """
        import pandas as pd

        # Define weekdays for each schedule type
        weekdays_map = {
            "st": ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek"],
            "nst": ["Piątek", "Sobota", "Niedziela"],
            "nst-online": ["Sobota", "Niedziela"]
        }
        weekdays = weekdays_map.get(self.schedule_type, weekdays_map["st"])

        # Get the expected number of columns for this group
        actual_col_count = len(column_indices) - 1  # Exclude time column
        logger.debug(f"Group has {actual_col_count} actual columns (excluding time)")

        # If expected columns is specified, validate and limit
        if expected_columns and actual_col_count > expected_columns:
            logger.warning(f"Found {actual_col_count} columns but expected {expected_columns}. Using first {expected_columns} columns.")
            # Limit to expected columns + time column
            column_indices = column_indices[:expected_columns + 1]
            actual_col_count = expected_columns

        # Try to detect which columns correspond to which days
        detected_days = {}
        try:
            if hasattr(self, 'converted_lesson_plan') and self.converted_lesson_plan:
                all_detected_days = self._get_weekday_column_map()

                # Filter to only include columns we're actually using
                for col_idx in column_indices[1:]:  # Skip time column
                    if col_idx in all_detected_days:
                        detected_days[col_idx] = all_detected_days[col_idx]
                        logger.debug(f"Column {col_idx} contains data for {all_detected_days[col_idx]}")

                if detected_days:
                    logger.debug(f"Successfully mapped {len(detected_days)} columns to weekdays")
                else:
                    logger.debug("No weekday headers found for the data columns")
        except Exception as e:
            logger.warning(f"Could not detect weekdays dynamically: {e}")

        # If detection failed, use positional mapping based on column positions
        # CRITICAL: Only use positional mapping if day detection completely failed
        if not detected_days and actual_col_count > 0:
            logger.warning(f"Day detection failed! Falling back to positional mapping for {actual_col_count} columns")
            logger.warning(f"Column indices (excluding time): {column_indices[1:]}")
            logger.warning("CAUTION: Positional mapping may be incorrect for sheets with non-standard layouts!")

            # For st (standard) schedules with 5 weekdays
            if len(weekdays) == 5:
                # Map based on Excel column position to weekday
                # Excel columns typically follow pattern:
                # Col 0: Time
                # Col 1: Monday (or first group Monday)
                # Col 2: Tuesday (or first group Tuesday)
                # Col 3: Wednesday (or first group Wednesday)
                # Col 4: Thursday (or first group Thursday)
                # Col 5: Friday (or first group Friday)
                # Col 6: Monday (second group Monday) etc.

                for col_idx in column_indices[1:]:  # Skip time column
                    # Subtract 1 because column 0 is time, column 1 is first data column
                    # Then use modulo 5 to find day of week
                    day_index = (col_idx - 1) % 5

                    # Map to weekday
                    if day_index < len(weekdays):
                        detected_days[col_idx] = weekdays[day_index]
                        logger.debug(f"Column {col_idx} -> {weekdays[day_index]} (position {day_index})")

                # Special handling for specific patterns
                if actual_col_count == 3:
                    # Check if it's the common Tue/Thu/Fri pattern
                    # This happens when columns are at positions like 3,5,6 (indices for Tue,Thu,Fri)
                    cols_list = list(column_indices[1:])

                    # Calculate day indices for each column
                    day_indices = [(c - 1) % 5 for c in cols_list]
                    logger.debug(f"3-column pattern detected. Day indices: {day_indices}")

                    # Common patterns:
                    # [1, 3, 4] = Tue, Thu, Fri
                    # [0, 2, 4] = Mon, Wed, Fri
                    # [1, 2, 3] = Tue, Wed, Thu

                    # Already mapped correctly above, just log for verification
                    mapped_days = [detected_days.get(c, "?") for c in cols_list]
                    logger.debug(f"Mapped to days: {mapped_days}")

            elif len(weekdays) == 3:
                # For nst (non-standard) with 3 weekdays (Fri, Sat, Sun)
                for i, col_idx in enumerate(column_indices[1:]):
                    if i < len(weekdays):
                        detected_days[col_idx] = weekdays[i]
                        logger.debug(f"Column {col_idx} -> {weekdays[i]}")

            # Log the final mapping
            logger.debug(f"Final day mapping: {detected_days}")

        # Create new DataFrame with all weekdays
        new_df = pd.DataFrame()

        # Always keep the time column
        new_df["Godziny"] = df_filtered.iloc[:, 0]

        # Map existing columns to their detected days (no duplication!)
        column_to_day = {}

        # Use detected days if available
        if detected_days:
            logger.debug(f"Using detected day mapping: {detected_days}")
            logger.debug(f"DataFrame has {len(df_filtered.columns)} columns")
            logger.debug(f"column_indices[1:] (Excel columns) = {column_indices[1:]}")

            # CRITICAL FIX: When we do df.iloc[:, [0, 5, 14, 19]], the resulting DataFrame
            # has columns at indices [0, 1, 2, 3], NOT [0, 5, 14, 19]!
            # We need to map: Excel column → DataFrame column index → Day name

            for df_col_idx, excel_col_idx in enumerate(column_indices[1:], 1):  # Start from 1 to skip time column
                logger.debug(f"Processing: DataFrame col_idx={df_col_idx}, Excel col_idx={excel_col_idx}")

                if excel_col_idx in detected_days:
                    day = detected_days[excel_col_idx]

                    # Log sample data from this DataFrame column
                    sample_data = df_filtered.iloc[:3, df_col_idx].tolist() if len(df_filtered) >= 3 else df_filtered.iloc[:, df_col_idx].tolist()
                    logger.debug(f"  Excel column {excel_col_idx} -> DataFrame column index {df_col_idx}")
                    logger.debug(f"  Detected as day: {day}")
                    logger.debug(f"  Sample data from df_filtered.iloc[:, {df_col_idx}]: {sample_data}")

                    if day not in column_to_day:  # Prevent duplicate mapping
                        column_to_day[day] = df_filtered.iloc[:, df_col_idx]
                        logger.debug(f"  ✓ Mapped DataFrame column {df_col_idx} to weekday '{day}'")
                    else:
                        logger.warning(f"Day {day} already mapped, skipping Excel column {excel_col_idx}")
                else:
                    logger.warning(f"Excel column {excel_col_idx} not in detected days mapping")
        else:
            logger.warning("No detected days available, data may be placed incorrectly")

        # Add columns for all weekdays
        logger.debug(f"Creating final DataFrame with weekdays: {weekdays}")
        for day in weekdays:
            if day in column_to_day:
                # Use existing data for this day
                sample_final = column_to_day[day].iloc[:3].tolist() if len(column_to_day[day]) >= 3 else column_to_day[day].tolist()
                new_df[day] = column_to_day[day]
                logger.debug(f"Added data for {day}, sample: {sample_final}")
            else:
                # Create empty column for missing day - CRUCIAL!
                new_df[day] = pd.Series([""] * len(new_df), dtype=object)
                logger.debug(f"Added EMPTY column for {day}")

        logger.debug(f"Expanded from {len(df_filtered.columns)} to {len(new_df.columns)} columns")
        logger.debug(f"Days with data: {list(column_to_day.keys())}")
        logger.debug(f"Empty days: {[d for d in weekdays if d not in column_to_day]}")

        return new_df

    def expand_to_full_week(self, df_filtered, column_indices):
        """
        Original expand function for normal (non-mixed) plans.
        This function was originally intended for normal plans where a group
        might be missing some day columns that need to be filled in.
        """

        # Define weekdays for each schedule type
        weekdays_map = {
            "st": ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek"],
            "nst": ["Piątek", "Sobota", "Niedziela"],
            "nst-online": ["Sobota", "Niedziela"]
        }

        weekdays = weekdays_map.get(self.schedule_type, weekdays_map["st"])
        num_days = len(weekdays)

        # Create a new DataFrame with all weekdays
        import pandas as pd
        new_df = pd.DataFrame()

        # Always keep the time column
        new_df["Godziny"] = df_filtered.iloc[:, 0]

        # Try to detect weekday columns dynamically first
        day_column_map = {}
        try:
            if hasattr(self, 'converted_lesson_plan') and self.converted_lesson_plan:
                detected_days = self.detect_weekday_columns(self.converted_lesson_plan, self.sheet_name)
                # Map our column indices to detected days
                for col_idx in column_indices[1:]:
                    if col_idx in detected_days:
                        day_column_map[col_idx] = detected_days[col_idx]
                        logger.debug(f"Column {col_idx} detected as {detected_days[col_idx]}")
        except Exception as e:
            logger.warning(f"Could not detect weekdays dynamically: {e}")

        # If dynamic detection failed or incomplete, use pattern-based mapping
        if not day_column_map:
            logger.debug("Using pattern-based day mapping")
            for col_idx in column_indices[1:]:  # Skip time column
                # Common patterns: 2-6 (Mon-Fri), 11-15 (Mon-Fri), etc.
                # Base column for groups typically starts at 2
                base_col = 2
                # Find which "week group" this column belongs to
                week_group = (col_idx - base_col) // num_days
                position_in_week = (col_idx - base_col) % num_days

                if 0 <= position_in_week < len(weekdays):
                    day_name = weekdays[position_in_week]
                    day_column_map[col_idx] = day_name
                    logger.debug(f"Column {col_idx} mapped to {day_name} (pattern-based)")

        # Now create the full week DataFrame
        column_data_map = {}  # Map day names to data
        for idx, col_idx in enumerate(column_indices[1:], 1):  # Skip time column
            if col_idx in day_column_map:
                day = day_column_map[col_idx]
                if day not in column_data_map:
                    column_data_map[day] = []
                column_data_map[day].append(df_filtered.iloc[:, idx])

        # Add columns for all weekdays
        for day in weekdays:
            if day in column_data_map:
                # If we have multiple columns for the same day, use the first one
                # (this handles cases where a day appears multiple times)
                new_df[day] = column_data_map[day][0]
            else:
                # No data for this day - add empty column
                new_df[day] = pd.Series([None] * len(new_df), dtype=object)

        logger.debug(f"Expanded from {len(column_indices)} columns to {len(new_df.columns)} columns (full week)")
        return new_df

    def process_and_save_plan(self):
        """Process and save the lesson plan, returns checksum if plan was processed"""
        new_checksum = self.download_file()
        if not new_checksum:
            logger.error("Failed to download file.")
            return None

        # Check if category is None - if so, only check checksum and save basic info
        if self.schedule_type is None:
            if self.save_to_mongodb:
                # Replace both spaces and underscores in faculty with hyphens
                faculty_name = self.plan_config['faculty'].replace(' ', '-').replace('_', '-')
                collection_name = f"plans_{faculty_name}_{self.plan_config['name'].lower().replace(' ', '_')}"
                collection = self.db[collection_name]
                
                # Check if this checksum already exists
                existing_plan = collection.find_one({"checksum": new_checksum})
                if existing_plan:
                    logger.info(f"Plan with checksum {new_checksum} already exists in {collection_name}. Skipping save.")
                    return False
                
                # Save basic plan info without groups
                current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message_html = f"""
                <div class="plan-message">
                    <p>Przepraszamy, ale ten plan nie jest obecnie możliwy do przetworzenia.</p>
                    <p>Możemy jedynie sprawdzić jego ostatnią aktualizację. Jeśli coś się zmieni w przyszłości, poinformujemy o tym.</p>
                    <p><a href="{html_escape(self.plan_config['download_url'], quote=True)}" class="download-btn" target="_blank" rel="noopener noreferrer">Pobierz oryginalny plan</a></p>
                </div>
                """
                
                plans_data = {
                    "timestamp": current_datetime,
                    "checksum": new_checksum,
                    "parser_version": PARSER_VERSION,
                    "config_fingerprint": config_fingerprint(self.plan_config),
                    "plan_name": self.plan_config["name"],
                    "category": None,
                    "groups": {
                        "cały kierunek": message_html
                    },
                    "url": self.plan_config["download_url"]
                }
                
                result = collection.insert_one(plans_data)
                logger.info(f"Saved basic plan info to MongoDB collection", extra={"collection": collection_name, "doc_id": str(result.inserted_id)})
            return new_checksum

        should_process = True

        if self.save_to_mongodb:
            # Use plan-specific collection
            # Replace both spaces and underscores in faculty with hyphens
            faculty_name = self.plan_config['faculty'].replace(' ', '-').replace('_', '-')
            collection_name = f"plans_{faculty_name}_{self.plan_config['name'].lower().replace(' ', '_')}"
            collection = self.db[collection_name]
            # Check MongoDB for changes - exclude discord_config document
            latest_plan = collection.find_one(
                {
                    "plan_name": self.plan_config["name"],
                    "_id": {"$ne": "discord_config"}
                }, 
                sort=[("timestamp", -1)]
            )

            latest_checksum = (
                latest_plan.get("checksum").split("_")[0]
                if latest_plan and latest_plan.get("checksum")
                else None
            )

            same_parser = (latest_plan and latest_plan.get("parser_version") == PARSER_VERSION
                           and latest_plan.get("config_fingerprint") == config_fingerprint(self.plan_config))
            if latest_checksum and latest_checksum == new_checksum and same_parser:
                logger.info(
                    f"Plan has not changed (MongoDB check in {collection_name}, checksum: {new_checksum})."
                )
                return False
            else:
                logger.info(
                    f"Plan has changed or no previous plan found (old checksum: {latest_checksum}, new checksum: {new_checksum})"
                )
                should_process = True

        if should_process:
            logger.info(f"Processing plan for {self.plan_config['name']}")

            # Sheet-based generator for all plans (merged cells, sub-columns,
            # day names from the sheet); legacy pipeline below is the fallback
            try:
                file_path = self.file_save_path or os.path.join(self.directory or '', 'downloaded_file.xlsx')
                groups_html = self._generate_groups_html(file_path)
                if groups_html:
                    if self.save_to_mongodb:
                        faculty_name = self.plan_config['faculty'].replace(' ', '-').replace('_', '-')
                        collection_name = f"plans_{faculty_name}_{self.plan_config['name'].lower().replace(' ', '_')}"
                        collection = self.db[collection_name]
                        current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        document = {
                            "timestamp": current_datetime,
                            "checksum": new_checksum,
                            "plan_name": self.plan_config["name"],
                            "category": self.schedule_type,
                            "groups": groups_html,
                            "notes": getattr(self, "last_notes", None),
                            # meeting dates listed in the sheet header (e-learning programmes)
                            "zjazdy": getattr(self, "last_zjazdy", None),
                            "parser_version": PARSER_VERSION,
                            "config_fingerprint": config_fingerprint(self.plan_config),
                        }
                        if self.is_mixed:
                            document["mixed"] = True
                            document["group_column_info"] = self.group_column_counts
                        self._record_changes(collection, collection_name, groups_html, new_checksum)
                        collection.insert_one(document)
                        logger.info(f"Saved plan via sheet generator to {collection_name}")
                    return new_checksum
                logger.warning("Sheet generator did not recognise the layout, using legacy pipeline")
            except Exception as e:
                logger.warning(f"Sheet generator failed, falling back to legacy pipeline: {e}")

            try:
                self._invalidate_processing_cache()

                # Always process the downloaded file
                self.unmerge_and_fill_data()
                if self.clean_excel_enabled:
                    self.clean_excel_file()
                else:
                    logger.debug(
                        "Skipping clean_excel_file() for this plan (clean_excel disabled)",
                        extra={"plan_name": self.plan_config.get("name")}
                    )

                # Process groups
                self.find_group_columns_with_similarity()

                # Get all groups from instance
                groups_to_process = self.groups.keys() if self.groups else []

                processed_groups = []
                failed_groups = []
                processed_group_data = {}
                raw_df = self._get_sheet_df(header_none=True)

                # Process each group
                for group_name in groups_to_process:
                    try:
                        df_group = self.get_lessons_for_group(group_name, source_df=raw_df)
                        if df_group is not None and not df_group.empty:
                            processed_group_data[group_name] = df_group
                            # Save group data only when file output is enabled.
                            if self.save_to_file:
                                self.save_group_lessons(group_name, df_group)
                            processed_groups.append(group_name)
                        else:
                            logger.warning(
                                f"No data available for group - DataFrame is empty",
                                extra={
                                    "group_name": group_name,
                                    "plan_name": self.plan_config.get('name'),
                                    "has_columns": group_name in self.group_columns,
                                    "column_count": len(self.group_columns.get(group_name, []))
                                }
                            )
                            failed_groups.append(group_name)
                    except Exception as group_error:
                        logger.error(
                            f"Error processing group {group_name}: {str(group_error)}"
                        )
                        failed_groups.append(group_name)
                        continue

                if failed_groups:
                    logger.warning(
                        f"Failed to process groups",
                        extra={
                            "failed_groups": failed_groups,
                            "plan_name": self.plan_config.get('name'),
                            "total_groups": len(groups_to_process),
                            "successful_groups": len(processed_groups)
                        }
                    )

                # Save to MongoDB if enabled
                if self.save_to_mongodb:
                    if processed_groups:
                        self.convert_to_html_and_save_to_db(
                            new_checksum,
                            precomputed_group_data=processed_group_data,
                            failed_groups=failed_groups,
                        )
                    else:
                        # No groups processed but still save checksum to avoid re-processing
                        faculty_name = self.plan_config['faculty'].replace(' ', '-').replace('_', '-')
                        collection_name = f"plans_{faculty_name}_{self.plan_config['name'].lower().replace(' ', '_')}"
                        collection = self.db[collection_name]
                        current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        collection.insert_one({
                            "timestamp": current_datetime,
                            "checksum": new_checksum,
                            "parser_version": PARSER_VERSION,
                            "config_fingerprint": config_fingerprint(self.plan_config),
                            "plan_name": self.plan_config["name"],
                            "category": self.schedule_type,
                            "groups": {"cały kierunek": "<p>Plan nie zawiera danych do wyświetlenia.</p>"},
                        })
                        logger.info(f"Saved empty plan with checksum to prevent re-processing")

                return new_checksum

            except Exception as e:
                logger.error(f"Error processing plan: {str(e)}")
                import traceback

                traceback.print_exc()
                return None

        return new_checksum

    def get_converted_lesson_plan(self):
        return self.converted_lesson_plan

    def get_groups(self):
        return self.groups

    def unmerge_and_fill_data(self):
        if not self.file_save_path:
            logger.error("No file has been downloaded yet. Please run download_file() first.")
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
        self._invalidate_processing_cache()
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
            logger.error("No converted file found. Please run unmerge_and_fill_data() first.")
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
                    # df = df.apply(self.clean_text)

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
                    self._invalidate_processing_cache()
                    break
                except PermissionError:
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                    else:
                        raise

            return True

        except Exception as e:
            logger.error(f"An error occurred while cleaning the file: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
            return False

    def find_group_columns(self):
        if not self.converted_lesson_plan:
            logger.error("No converted file found. Please run unmerge_and_fill_data() first.")
            return False

        try:
            df = self._get_sheet_df(header_none=False)
            for key, identifier in self.groups.items():
                # Use the full identifier (with \n characters) for searching
                columns = df.columns[df.isin([identifier]).any()].tolist()
                self.group_columns[key] = columns

                # Log if column count doesn't match expected for mixed plans
                if hasattr(self, 'is_mixed') and self.is_mixed and hasattr(self, 'group_column_counts'):
                    expected = self.group_column_counts.get(key)
                    if expected:
                        actual = len(columns)
                        if actual != expected:
                            logger.warning(f"Group '{key}': Expected {expected} columns, found {actual}")

            return self.group_columns

        except Exception as e:
            logger.error(f"An error occurred while finding group columns: {str(e)}")
            return None

    def find_group_columns_with_similarity(self):
        if not self.converted_lesson_plan:
            logger.error("No converted file found. Please run unmerge_and_fill_data() first.")
            return False

        def is_matching_group(text, pattern, similarity_threshold=1.0):
            """
            Sprawdza czy tekst odpowiada wzorcowi grupy z zadanym progiem podobieństwa
            """
            if not isinstance(text, str) or not isinstance(pattern, str):
                return False

            # Normalizacja tekstu
            def normalize_text(t):
                t = " ".join(t.split())
                return t.lower().strip()

            text_normalized = normalize_text(text)
            pattern_normalized = normalize_text(pattern)

            if text_normalized == pattern_normalized:
                return True

            similarity = SequenceMatcher(
                None, text_normalized, pattern_normalized
            ).ratio()
            return similarity >= similarity_threshold

        def verify_columns(columns, schedule_type):
            """
            Sprawdza czy znalezione kolumny są prawidłowe dla danego typu planu
            """
            if hasattr(self, 'is_mixed') and self.is_mixed:
                logger.debug(
                    f"Mixed plan - accepting columns without strict verification",
                    extra={
                        "column_count": len(columns),
                        "schedule_type": schedule_type
                    }
                )
                return len(columns) > 0 and not all("Column_" in col for col in columns)

            expected_columns = {
                "st": 6,
                "nst": 4,
                "nst-online": 3,
            }

            expected = expected_columns.get(schedule_type, 6)
            found = len(columns)

            if found != expected:
                return False

            has_numbered = any("Column_" in col for col in columns)
            if has_numbered:
                return False

            return True

        try:
            df = self._get_sheet_df(header_none=False)

            if len(self.groups) == 1 and "cały kierunek" in self.groups:
                days_upper = {
                    "st": ["PONIEDZIAŁEK", "WTOREK", "ŚRODA", "CZWARTEK", "PIĄTEK"],
                    "nst": ["PIĄTEK", "SOBOTA", "NIEDZIELA"],
                    "nst-online": ["SOBOTA", "NIEDZIELA"],
                }
                schedule_days = days_upper.get(self.schedule_type, days_upper["st"])

                matching_columns = []
                used_columns = set()

                for column in df.columns:
                    if column in used_columns:
                        continue

                    # Check column header name (for named headers)
                    col_upper = str(column).upper().strip()
                    if col_upper in schedule_days:
                        matching_columns.append(column)
                        used_columns.add(column)
                        continue

                    # Check cell values (for raw headers)
                    unique_values = df[column].dropna().unique()
                    for value in unique_values:
                        value_str = str(value).upper().strip()
                        if value_str in schedule_days:
                            matching_columns.append(column)
                            used_columns.add(column)
                            break

                # Fallback: if no day names found, take all columns except the first one
                if not matching_columns:
                    all_cols = list(df.columns)
                    if len(all_cols) > 1:
                        matching_columns = all_cols[1:]
                        logger.info(f"No day columns found by name, using all {len(matching_columns)} data columns for 'cały kierunek'")

                # Convert column names to "Unnamed: X" format (index-based)
                # so get_lessons_for_group can parse them with header_none=True
                indexed_columns = []
                all_cols = list(df.columns)
                for col in matching_columns:
                    idx = all_cols.index(col) if col in all_cols else None
                    if idx is not None:
                        indexed_columns.append(f"Unnamed: {idx}")
                    else:
                        indexed_columns.append(col)

                indexed_columns.sort(
                    key=lambda x: int(x.split(".")[-1]) if isinstance(x, str) and "." in x else (int(x.split(": ")[-1]) if ": " in str(x) else 0)
                )
                self.group_columns["cały kierunek"] = indexed_columns
                logger.info(f"Mapped 'cały kierunek' to columns: {indexed_columns}")
                return self.group_columns

            # Standardowa logika dla zdefiniowanych grup
            used_columns = set()
            group_columns = {}
            backup_columns = {}

            for group_name, group_identifier in self.groups.items():

                for similarity_threshold in [x / 100.0 for x in range(100, 84, -1)]:
                    matching_columns = []
                    used_columns = set()

                    for column in df.columns:
                        if column in used_columns:
                            continue

                        unique_values = df[column].dropna().unique()

                        for value in unique_values:
                            if is_matching_group(
                                str(value), group_identifier, similarity_threshold
                            ):
                                matching_columns.append(column)
                                used_columns.add(column)
                                break

                    if matching_columns:
                        if verify_columns(matching_columns, self.schedule_type):
                            group_columns[group_name] = matching_columns
                            break
                        else:
                            if group_name not in backup_columns:
                                backup_columns[group_name] = matching_columns

                if group_name not in group_columns and group_name in backup_columns:
                    group_columns[group_name] = backup_columns[group_name]
                    logger.debug(f"Using backup columns", extra={"group_name": group_name, "columns": backup_columns[group_name]})
                elif group_name not in group_columns:
                    logger.warning(
                        f"No columns found for group",
                        extra={
                            "group_name": group_name,
                            "identifier": group_identifier,
                            "plan_name": self.plan_config.get('name'),
                            "schedule_type": self.schedule_type,
                            "available_columns": len(df.columns)
                        }
                    )

            self.group_columns = group_columns
            return self.group_columns

        except Exception as e:
            logger.error(f"An error occurred while finding group columns: {str(e)}")
            return None

    def get_lessons_for_group(self, group_name, expected_columns=None, source_df=None):
        if not self.converted_lesson_plan:
            logger.error("No converted file found. Please run unmerge_and_fill_data() first.")
            return None

        if not self.group_columns:
            self.find_group_columns_with_similarity()

        try:
            df = source_df if source_df is not None else self._get_sheet_df(header_none=True)

            if group_name not in self.group_columns:
                logger.warning(f"Group not found in columns", extra={"group_name": group_name})
                return None

            # Extract column numbers from column names
            group_col_indices = []
            for col_name in self.group_columns[group_name]:
                col_str = str(col_name)
                try:
                    # "Unnamed: 5" -> 5
                    if "Unnamed:" in col_str:
                        group_col_indices.append(int(col_str.split(":")[-1].strip()))
                        continue
                    # "Unnamed.5" or similar dotted format -> last number
                    if "." in col_str:
                        group_col_indices.append(int(col_str.split(".")[-1]))
                        continue
                except (ValueError, IndexError):
                    pass

            if not group_col_indices:
                logger.warning(f"Could not extract column indices for {group_name}, raw: {self.group_columns[group_name][:3]}")
                return None

            columns_to_extract = [0] + sorted(group_col_indices)

            # Extract columns
            df_filtered = df.iloc[:, columns_to_extract].copy()

            # Remove semester information rows (vectorized per-column string matching).
            df_filtered_as_str = df_filtered.astype(str)
            semester_mask = (
                df_filtered_as_str
                .apply(lambda col: col.str.contains(r"semestr|zjazd", case=False, regex=True, na=False))
                .any(axis=1)
            )
            df_filtered = df_filtered[~semester_mask]

            # Find the first row with time information
            time_row_index = None
            for idx, row in df_filtered.iterrows():
                if isinstance(row[0], str) and any(
                    pattern in row[0].lower()
                    for pattern in [
                        "godz",
                        "godziny",
                        "7",
                        "8",
                        "9",
                        "10",
                        "11",
                        "12",
                        "13",
                        "14",
                        "15",
                        "16",
                        "17",
                        "18",
                        "19",
                        "20",
                    ]
                ):
                    time_row_index = idx
                    break

            if time_row_index is not None:
                # Remove all rows before the time row
                df_filtered = df_filtered.iloc[time_row_index:]
            

            # Remove header rows that contain "godz" or "GODZ"
            df_filtered = df_filtered[
                ~df_filtered[0]
                .astype(str)
                .str.contains(r"godz\.|GODZ\.", case=False, regex=True)
            ]

            # Improved empty row removal (vectorized).
            normalized = df_filtered.astype(str).apply(lambda col: col.str.strip().str.lower())
            has_content_mask = ~normalized.isin(["", "nan", "none", "nat"]).all(axis=1)
            df_filtered = df_filtered[has_content_mask]

            # Remove rows where all group columns (excluding time column) are NaN
            df_filtered = df_filtered.dropna(subset=df_filtered.columns[1:], how="all")

            # For mixed plans, expand to full week with EMPTY columns for missing days
            if hasattr(self, 'is_mixed') and self.is_mixed:
                # Pass group name to get expected columns if available
                expected_cols = None
                if hasattr(self, 'group_column_counts') and group_name in self.group_column_counts:
                    expected_cols = self.group_column_counts[group_name]
                    logger.debug(f"Group {group_name} expected to have {expected_cols} columns")

                # Expand DataFrame to include all weekdays with empty columns
                df_filtered = self.expand_to_full_week_mixed(df_filtered, columns_to_extract, expected_cols)
                logger.debug(f"Mixed plan expanded to full week: {list(df_filtered.columns)}")
            else:
                # For normal plans, use sequential headers
                headers = self.get_schedule_headers(len(df_filtered.columns))
                df_filtered.columns = headers

            # Reset index
            df_filtered = df_filtered.reset_index(drop=True)

            if df_filtered.empty:
                logger.warning(f"No data found for group", extra={"group_name": group_name})
                return None

            # Verify that we have all expected time slots
            expected_time_slots = [
                "725- 810",
                "815- 900",
                "905- 950",
                "1000-1045",
                "1050- 1135",
                "1145- 1230",
                "1235- 1320",
                "1330- 1415",
                "1420- 1505",
                "1515- 1600",
                "1605- 1650",
                "1700- 1745",
                "1750- 1835",
                "1845- 1930",
                "1935- 2020",
                "2030- 2115",
            ]

            missing_slots = []
            for slot in expected_time_slots:
                if not any(
                    df_filtered["Godziny"].astype(str).str.contains(slot, regex=False)
                ):
                    missing_slots.append(slot)

            if missing_slots:
                logger.debug(f"Missing time slots for {group_name}: {missing_slots}")
            if not df_filtered.empty:
                last_row_time = str(df_filtered.iloc[-1]["Godziny"]).strip()
                if not any(
                    time_pattern in last_row_time
                    for time_pattern in [
                        "725-",
                        "815-",
                        "905-",
                        "1000-",
                        "1050-",
                        "1145-",
                        "1235-",
                        "1330-",
                        "1420-",
                        "1515-",
                        "1605-",
                        "1700-",
                        "1750-",
                        "1845-",
                        "1935-",
                        "2030-",
                    ]
                ):
                    df_filtered = df_filtered.iloc[:-1]
            return df_filtered

        except Exception as e:
            logger.error(
                f"An error occurred while getting lessons for group '{group_name}': {str(e)}"
            )
            import traceback

            traceback.print_exc()
            return None

    def save_group_lessons(self, group_name, df):
        if df is None or df.empty:
            logger.warning(f"No data to save for group '{group_name}'.")
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
            # Sanitize sheet name - Excel doesn't allow: : / \ ? * [ ]
            sheet_name = group_name
            for char in [':', '/', '\\', '?', '*', '[', ']']:
                sheet_name = sheet_name.replace(char, '_')
            # Truncate sheet name to 31 characters
            sheet_name = sheet_name[:31]

            # Save the DataFrame to an Excel file
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name=sheet_name)

        except Exception as e:
            logger.debug(f"Could not save group lessons to file: {str(e)}")

    @staticmethod
    def get_column_letter(column_number):
        """Convert a column number to a column letter (A, B, C, ..., Z, AA, AB, ...)."""
        dividend = column_number
        column_letter = ""
        while dividend > 0:
            dividend, remainder = divmod(dividend - 1, 26)
            column_letter = chr(65 + remainder) + column_letter
        return column_letter

    def convert_to_html_and_save_to_db(self, checksum, precomputed_group_data=None, failed_groups=None):
        if not self.group_columns:
            logger.error("No group columns found. Please run find_group_columns() first.")
            return

        current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Initialize the plans data structure
        plans_data = {
            "timestamp": current_datetime,
            "checksum": checksum,
            "parser_version": PARSER_VERSION,
            "config_fingerprint": config_fingerprint(self.plan_config),
            "plan_name": self.plan_config["name"],
            "category": self.schedule_type,
            "groups": {},
        }

        # Add mixed flag if this is a mixed plan
        if hasattr(self, 'is_mixed') and self.is_mixed:
            plans_data["mixed"] = True
            # Also store column counts for frontend
            if hasattr(self, 'group_column_counts'):
                plans_data["group_column_info"] = self.group_column_counts

        processed_groups = []
        failed_groups = failed_groups[:] if failed_groups else []
        group_data_map = precomputed_group_data.copy() if precomputed_group_data else {}

        # Process all groups once and reuse the same DataFrames for HTML and pickle output.
        if not group_data_map:
            raw_df = self._get_sheet_df(header_none=True)
            if self.groups:
                for group_name in self.groups.keys():
                    try:
                        df = self.get_lessons_for_group(group_name, source_df=raw_df)
                        if df is not None and not df.empty:
                            group_data_map[group_name] = df
                        else:
                            failed_groups.append(group_name)
                            logger.warning(f"No data available for group: {group_name}")
                    except Exception as e:
                        failed_groups.append(group_name)
                        logger.error(f"Error processing group {group_name}: {str(e)}")
                        continue
            else:
                # Handle case where there are no specific groups (entire course)
                try:
                    df = self.get_lessons_for_group("cały kierunek", source_df=raw_df)
                    if df is not None and not df.empty:
                        group_data_map["cały kierunek"] = df
                        logger.info("Successfully processed HTML for entire course")
                except Exception as e:
                    logger.error(f"Error processing entire course: {str(e)}")

        # Build HTML output from already computed DataFrames
        for group_name, df in group_data_map.items():
            html = self.generate_html_table(df)
            plans_data["groups"][group_name] = html
            processed_groups.append(group_name)

        # Only save to MongoDB if we have processed at least one group
        if processed_groups:
            if self.save_to_mongodb:
                try:
                    # Use plan-specific collection
                    # Replace both spaces and underscores in faculty with hyphens
                    faculty_name = self.plan_config['faculty'].replace(' ', '-').replace('_', '-')
                    collection_name = f"plans_{faculty_name}_{self.plan_config['name'].lower().replace(' ', '_')}"
                    collection = self.db[collection_name]

                    # Check if this checksum already exists
                    existing_plan = collection.find_one({"checksum": checksum})
                    if existing_plan:
                        logger.info(
                            f"Plan with checksum {checksum} already exists in {collection_name}. Skipping save."
                        )
                        return

                    self._record_changes(collection, collection_name, plans_data["groups"], checksum)

                    # Insert the new plan with all groups
                    result = collection.insert_one(plans_data)
                    logger.info(
                        f"Saved plans to MongoDB collection {collection_name} with id: {result.inserted_id}"
                    )

                    if failed_groups:
                        logger.warning(f"Failed to process groups: {', '.join(failed_groups)}")

                    # Verify the saved data
                    saved_plan = collection.find_one({"_id": result.inserted_id})
                    if saved_plan:
                        saved_groups = list(saved_plan.get("groups", {}).keys())
                        logger.info(
                            f"Verified saved groups in MongoDB: {', '.join(saved_groups)}"
                        )
                    else:
                        logger.warning("Warning: Could not verify saved data")

                except Exception as e:
                    logger.error(f"Error saving to MongoDB: {str(e)}")
                    import traceback

                    traceback.print_exc()

        # Save to files if enabled
        if self.save_to_file:
            logger.info("Saving plans to files...")
            for group_name in processed_groups:
                file_name = f"{current_datetime.replace(':', '-')}_{group_name}.pkl"
                file_path = os.path.join(self.plans_directory, file_name)
                df = group_data_map.get(group_name)
                if df is not None:
                    df.to_pickle(file_path)
                    logger.info(f"Saved {group_name} plan to file: {file_path}")

        return bool(processed_groups)

    def generate_html_table(self, df):
        parts = ["<table border='1'>\n", "<tr>\n"]

        # Add header row
        for col in df.columns:
            bolded_header = " ".join(f"<b>{html_escape(word)}</b>" for word in str(col).split())
            parts.append(f"<th>{bolded_header}</th>\n")
        parts.append("</tr>\n")

        # Add data rows
        for _, row in df.iterrows():
            parts.append("<tr>\n")
            for i, cell in enumerate(row):
                formatted_cell = self.format_cell(cell, is_time_column=(i == 0))
                parts.append(f"<td>{formatted_cell}</td>\n")
            parts.append("</tr>\n")

        parts.append("</table>")
        return "".join(parts)

    def format_cell(self, cell,  is_time_column):
        if pd.isna(cell):
            return ""
        cell = str(cell)
        if is_time_column and cell != "godziny":
            parts = cell.replace(" ", "").split("-")
            if len(parts) == 2:
                start, end = parts
                formatted_start = self.format_time(html_escape(start))
                formatted_end = self.format_time(html_escape(end))
                return f"{formatted_start} - {formatted_end}"
        return html_escape(cell)

    def format_time(self, time):
        if len(time) == 3:
            return f"{time[0]}<sup>{time[1:]}</sup>"
        elif len(time) == 4:
            return f"{time[:2]}<sup>{time[2:]}</sup>"
        return time

    def full_action(self):
        """Execute the complete processing workflow"""
        try:
            checksum = self.process_and_save_plan()
            if checksum:
                logger.info(f"Successfully completed processing with checksum: {checksum}")
            else:
                logger.warning(
                    "Processing completed but no new data was saved (no changes or errors occurred)"
                )
        except Exception as e:
            logger.error(f"Error during full action: {str(e)}")
            import traceback

            traceback.print_exc()
