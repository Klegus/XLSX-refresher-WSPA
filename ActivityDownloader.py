import requests
import os
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import custom_print
class WebpageDownloader:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7'
        }

    def _create_filename(self, url):
        parsed_url = urlparse(url)
        output_filename = parsed_url.netloc.replace('.', '_') + '.html'
        if parsed_url.path and parsed_url.path != '/':
            path_part = parsed_url.path.rstrip('/').split('/')[-1]
            if path_part:
                output_filename = path_part + '.html'
        return output_filename

    def _fix_relative_urls(self, soup, base_url):
        for tag in soup.find_all(['a', 'img', 'link', 'script']):
            for attr in ['href', 'src']:
                if tag.get(attr):
                    tag[attr] = urljoin(base_url, tag[attr])
        return soup

    def save_webpage(self, url, output_filename=None):
        try:
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            if output_filename is None:
                output_filename = self._create_filename(url)

            print(f"Pobieram stronę z: {url}")
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            soup = self._fix_relative_urls(soup, url)

            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(str(soup))

            abs_path = os.path.abspath(output_filename)
            print(f"Strona pomyślnie zapisana do: {abs_path}")
            return abs_path

        except requests.exceptions.RequestException as e:
            print(f"Błąd pobierania strony: {str(e)}")
            return None
        except IOError as e:
            print(f"Błąd zapisywania pliku: {str(e)}")
            return None
        except Exception as e:
            print(f"Nieoczekiwany błąd: {str(e)}")
            return None