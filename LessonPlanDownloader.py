import os
import hashlib
import requests
from shared_utils import get_logger

logger = get_logger('LessonPlanDownloader')

# Shared session and download cache (module-level, shared across all instances)
_shared_session = None
_shared_session_user = None
_download_cache = {}  # url -> (file_path, checksum)


def _get_shared_session(username, password):
    """Get or create a shared authenticated session for PUW."""
    global _shared_session, _shared_session_user

    if _shared_session and _shared_session_user == username:
        return _shared_session

    session = requests.Session()
    url_login = "https://puw.wspa.pl/login/index.php"
    payload = {'password': password, 'username': username}
    headers = {'anchor': ''}

    response = session.post(url_login, headers=headers, data=payload)
    if response.ok:
        _shared_session = session
        _shared_session_user = username
        logger.info("Shared PUW session created")
        return session
    else:
        logger.error(f"Login failed: {response.status_code}")
        return None


class LessonPlanDownloader:
    def __init__(self, username, password, directory="", download_url=None):
        self.username = username
        self.password = password
        self.directory = directory
        self.file_save_path = None
        self.download_url = download_url

    def get_file_save_path(self):
        return self.file_save_path

    def calculate_checksum(self, file_path):
        hash_md5 = hashlib.new('md5', usedforsecurity=False)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def download_file(self):
        if not self.download_url:
            raise ValueError("Download URL not provided")

        # Check cache first - same URL = same file
        if self.download_url in _download_cache:
            cached_path, cached_checksum = _download_cache[self.download_url]
            if os.path.exists(cached_path):
                self.file_save_path = cached_path
                return cached_checksum

        # Use shared session (single login for all downloads)
        session = _get_shared_session(self.username, self.password)
        if not session:
            return None

        file_save_path = os.path.join(self.directory, "downloaded_file.xlsx")

        try:
            response = session.get(self.download_url, timeout=30)
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading the file: {e}")
            # Session might be expired, reset it
            global _shared_session
            _shared_session = None
            return None

        if response.ok:
            with open(file_save_path, 'wb') as file:
                file.write(response.content)
            self.file_save_path = os.path.abspath(file_save_path)

            checksum = self.calculate_checksum(self.file_save_path)

            # Cache the download
            _download_cache[self.download_url] = (self.file_save_path, checksum)

            return checksum
        else:
            logger.error(f"Error downloading the file: {response.status_code}")
            # If 403/401, session expired
            if response.status_code in (401, 403):
                _shared_session = None
            return None
