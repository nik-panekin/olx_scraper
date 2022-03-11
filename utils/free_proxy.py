import logging
import time

import requests
from bs4 import BeautifulSoup

FREE_PROXY_HOST = 'https://free-proxy-list.net'
HTTP_BIN_HOST = 'https://httpbin.org/ip'
TIMEOUT = 5

PROXY_TYPE_ANONYMOUS = 'anonymous'
PROXY_TYPE_ELITE = 'elite proxy'
PROXY_TYPE_ANY = [PROXY_TYPE_ANONYMOUS, PROXY_TYPE_ELITE]

USED_PROXIES_MAX = 1000

TEST_ATTEMPTS = 5
TEST_DELAY = 2
TEST_URL = 'https://github.com/'
TEST_URL = 'https://zoon.ru/'

class FreeProxy():
    def __init__(self, proxy_type=PROXY_TYPE_ANONYMOUS):
        # key: proxy URL; value: times the proxy has been used
        self.used_proxies = {}
        self.proxy_type = proxy_type

    def get_sorted_proxies(self) -> list:
        parsed_proxies = self.parse_proxies()
        if parsed_proxies == None:
            return None

        sorted_proxies = [
            {'name': proxy, 'count': self.used_proxies.get(proxy, 0)}
            for proxy in parsed_proxies
        ]

        sorted_proxies.sort(key=lambda proxy: proxy['count'])

        return [proxy['name'] for proxy in sorted_proxies]

    def parse_proxies(self) -> list:
        proxies = []

        try:
            r = requests.get(FREE_PROXY_HOST)
            soup = BeautifulSoup(r.text, 'lxml')
            for row in soup.tbody.find_all('tr'):
                cells = [cell.get_text() for cell in row.find_all('td')]
                # Checking for proxy type and https support
                if cells[4] in self.proxy_type and cells[6] == 'yes':
                    proxies.append('http://' + cells[0] + ':' + cells[1])
        except Exception:
            logging.exception(f'Failure while parsing {FREE_PROXY_HOST}')
            return None

        return proxies

    def proxy_is_valid(self, proxy: str) -> bool:
        try:
            r = requests.get(HTTP_BIN_HOST, proxies={'https': proxy},
                             timeout=TIMEOUT)
        except Exception:
            logging.info(f"Can't access {HTTP_BIN_HOST} via proxy {proxy}.")
        else:
            try:
                ip = r.json()['origin']
            except Exception:
                logging.exception(f'Failure while accessing {HTTP_BIN_HOST} '
                                  + f'via proxy {proxy}: incorrest respond.')
            else:
                logging.info(f'Access via proxy was granted. New IP: {ip}.')
                return True

        return False

    def _execute_test(self, test_url: str, proxy: str):
        logging.info(f'Starting test sequence for {test_url}')

        for i in range(TEST_ATTEMPTS):
            try:
                r = requests.get(test_url, proxies={'https': proxy},
                                 timeout=TIMEOUT)
            except Exception:
                logging.info(f"Can't access {test_url} via proxy {proxy}.")
                return False

            if r.status_code != requests.codes.ok:
                logging.info(f'Error {r.status_code} '
                             + f'while accessing {test_url}')
                return False

            time.sleep(TEST_DELAY)

        logging.info(f'Testing result for {test_url}: OK.')
        return True

    def get_proxy(self, test_url: str=None) -> str:
        proxies = self.get_sorted_proxies()
        if not proxies:
            return None

        for proxy in proxies:
            if self.proxy_is_valid(proxy):
                if test_url and (not self._execute_test(test_url, proxy)):
                    continue

                self.used_proxies[proxy] = self.used_proxies.get(proxy, 0) + 1

                # For really long-time runs
                for key in list(self.used_proxies.keys())[USED_PROXIES_MAX:]:
                    del self.used_proxies[key]

                return proxy

        return None

# For testing
def main():
    logging.basicConfig(level=logging.INFO)
    proxy = FreeProxy()
    while True:
        proxy_url = proxy.get_proxy(test_url=TEST_URL)
        print(f'Good proxy: {proxy_url}')
        try:
            r = requests.get(TEST_URL, proxies={'https': proxy_url},
                             timeout=TIMEOUT)
            print(f'Status code: {r.status_code}')
        except Exception:
            logging.exception(f'Failure while accessing {TEST_URL}')

if __name__ == '__main__':
    main()
