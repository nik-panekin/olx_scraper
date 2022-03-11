import subprocess
import time

import requests

TOR_EXECUTABLE_PATH = 'C:/Tor/Tor/tor.exe'

TOR_SOCKS_PROXIES = {
    'http': 'socks5://127.0.0.1:9050',
    'https': 'socks5://127.0.0.1:9050'
}

TOR_STARTUP_TIME = 15

HTTP_BIN_HOST = 'https://httpbin.org/'

class TorProxy():
    def __init__(self, executable_path: str=TOR_EXECUTABLE_PATH):
        self.executable_path = executable_path
        self.process = None

    def __del__(self):
        self.terminate()

    def restart(self, wait: bool=False) -> bool:
        self.terminate()
        self.process = subprocess.Popen(args=[self.executable_path],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        if wait:
            time.sleep(TOR_STARTUP_TIME)

    def is_running(self) -> bool:
        return self.process != None and self.process.poll() == None

    def terminate(self):
        if self.is_running():
            self.process.terminate()

    def test_ok(self) -> bool:
        if self.is_running():
            try:
                r = requests.get(HTTP_BIN_HOST, proxies=TOR_SOCKS_PROXIES)
            except requests.exceptions.RequestException:
                return False

            if r.status_code != requests.codes.ok:
               return False

            return True

        return False

    def get_output(self) -> str:
        if self.process != None and self.process.poll() != None:
            return self.process.stdout.read().decode('ascii', 'ignore')
        else:
            return None
