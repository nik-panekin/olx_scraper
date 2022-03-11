import re
import time
import logging
import copy
from urllib.parse import urlparse

import requests

from .tor_proxy import TorProxy, TOR_SOCKS_PROXIES
from .free_proxy import FreeProxy

# Timeout for web server response (seconds)
TIMEOUT = 5

# Maximum retries count for executing request if an error occurred
MAX_RETRIES = 3

# The delay after executing an HTTP request (seconds)
# SLEEP_TIME = 1
SLEEP_TIME = 0.5

# HTTP headers for making the scraper more "human-like"
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 6.1; rv:94.0) '
                   'Gecko/20100101 Firefox/94.0'),
    'Accept': '*/*',
}

ICANHAZIP_URL = 'http://icanhazip.com'

PROXY_TYPE_FREE = 'free'
PROXY_TYPE_TOR = 'tor'

class HttpRequest():
    def __init__(self, headers: dict=HEADERS, max_retries: int=MAX_RETRIES,
                 timeout: float=TIMEOUT, sleep_time: float=SLEEP_TIME,
                 proxies=None, proxy_test_url: str=None):
        # These attributes may be changed directly
        self.headers = copy.deepcopy(headers)
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep_time = sleep_time
        self.proxies = proxies
        self.proxy_test_url = proxy_test_url

        # Don't change these atrributes from outside the class instance
        self.tor_proxy = TorProxy()
        self.free_proxy = FreeProxy()
        self.proxy_index = -1
        self.proxy = self._get_next_proxy()

    def _get_next_proxy(self):
        if self.proxies == None:
            return None
        elif isinstance(self.proxies, dict):
            return self.proxies
        elif isinstance(self.proxies, list):
            self.proxy_index += 1
            self.proxy_index = self.proxy_index % len(self.proxies)
            return self.proxies[self.proxy_index]
        elif self.proxies == PROXY_TYPE_FREE:
            logging.info('Searching for free proxies.')
            proxy = self.free_proxy.get_proxy(self.proxy_test_url)
            return {'http': proxy, 'https': proxy}
        elif self.proxies == PROXY_TYPE_TOR:
            logging.info('Starting TOR.')
            self.tor_proxy.restart()
            return TOR_SOCKS_PROXIES

    def rotate_proxy(self):
        logging.info('Changing proxy (if possible).')
        self.proxy = self._get_next_proxy()
        logging.info('Now using IP: ' + self.get_ip())

    def _request(self, func, **args) -> requests.Response:
        return_status_code = args['return_status_code']
        del args['return_status_code']

        args['headers'] = self.headers
        args['timeout'] = self.timeout
        args['proxies'] = self.proxy
        for attempt in range(0, self.max_retries):
            try:
                r = func(**args)
            except requests.exceptions.RequestException:
                time.sleep(self.sleep_time)
            else:
                time.sleep(self.sleep_time)

                if r.status_code != requests.codes.ok:
                    logging.error(f'Error {r.status_code} '
                                  + f'while accessing {args["url"]}.')
                    return (None, r.status_code) if return_status_code else None

                return (r, r.status_code) if return_status_code else r

        logging.error("Can't execute HTTP request while accessing "
                      + args['url'])
        return (None, None) if return_status_code else None

    def get(self, url: str, params: dict = None, return_status_code=False):
        args = {
            'url': url,
            'params': params,
            'return_status_code': return_status_code,
        }
        func = requests.get
        return self._request(func=func, **args)

    def post(self, url: str, data: dict = None, return_status_code=False):
        args = {
            'url': url,
            'data': data,
            'return_status_code': return_status_code,
        }
        func = requests.post
        return self._request(func=func, **args)

    def get_ip(self) -> str:
        ip = self.get(ICANHAZIP_URL)
        if ip == None:
            return None

        return ip.text.strip()

    def get_html(self, url: str, params: dict = None,
                 return_status_code=False) -> str:
        r, status_code = self.get(url, params=params, return_status_code=True)
        if r == None:
            return (None, status_code) if return_status_code else None

        return (r.text, status_code) if return_status_code else r.text

    def get_json(self, url: str, params: dict=None,
                 return_status_code=False) -> dict:
        r, status_code = self.get(url, params=params, return_status_code=True)
        if r == None:
            return (None, status_code) if return_status_code else None

        try:
            json = r.json()
        except Exception:
            logging.exception(f'Error while getting JSON from URL [{url}].')
            return (None, status_code) if return_status_code else None

        return (json, status_code) if return_status_code else json

    def check_url(self, url: str) -> bool:
        r = self.get(url)
        if r == None:
            return False

        # Soft checking for redirect
        base_url_part = re.sub(r'^www\.', '', urlparse(url.lower()).netloc)
        base_url_part = base_url_part.split('.')[0]
        if base_url_part not in r.url.lower():
            return False

        return True

    # Retrieve an image from URL and save it to a file
    def save_image(self, url: str, filename: str) -> bool:
        r = self.get(url)

        try:
            with open(filename, 'wb') as f:
                f.write(r.content)
        except OSError:
            logging.exception(f"Can't save the image to the file {filename}.")
            return False
        except Exception:
            logging.exception(f'Failure while retrieving an image from {url}.')
            return False

        return True

# For testing
def main():
    logging.basicConfig(level=logging.INFO)
    request = HttpRequest(proxies=PROXY_TYPE_FREE)
    print(request.get_ip())

if __name__ == '__main__':
    main()
