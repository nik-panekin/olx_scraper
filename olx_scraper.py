import logging
import time
import sys
import os
from urllib.parse import urlparse
from configparser import ConfigParser

import keyboard
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, ElementClickInterceptedException
)

from utils.tor_proxy import TorProxy, TOR_SOCKS_PROXIES
from utils.http_request import HttpRequest
from utils.scraping_utils import (
    FATAL_ERROR_STR,

    setup_logging,
    clean_phone,

    save_items_csv,
    load_items_csv,

    save_items_json,
    load_items_json,

    save_items_xlsx,
)

HTTP_HOST = 'https://www.olx.ua'

LOGIN_URL = (
    HTTP_HOST + '/account/?ref[0][action]=myaccount&ref[0][method]=index'
)

API_OFFERS_URL = HTTP_HOST + '/api/v1/offers/{}'
API_PHONES_URL = API_OFFERS_URL + '/limited-phones/'
API_CATEGORIES_URL = HTTP_HOST + '/api/partner/categories'

LOGOUT_LINK = '/account/logout'
LOGOUT_URL = HTTP_HOST + LOGOUT_LINK + '/'

CONFIG_FILENAME = 'config.ini'
PROGRESS_FILENAME = 'progress.json'
SEARCH_LINKS_FILENAME = 'search_links.txt'

ACCOUNTS_FILENAME = 'accounts.csv'
ACCOUNTS_COLUMNS = ['login', 'password']

SLEEP_TIME = 0
PAGE_LOAD_TIMEOUT = 45
WAIT_TIMEOUT = 10
WAIT_CLICK = 1.0
# WAIT_FORBIDDEN_RETRY = 60
WAIT_FORBIDDEN_RETRY = 100

HOTKEY_TERMINATE = 'ctrl+alt+F12'

class ScraperOLX():
    def __init__(self):
        setup_logging()

        self.request = HttpRequest(sleep_time=SLEEP_TIME)
        self.api_request = HttpRequest(sleep_time=SLEEP_TIME)
        self.api_proxy_request = HttpRequest(sleep_time=SLEEP_TIME,
                                             proxies=TOR_SOCKS_PROXIES)
        self.api_v2_request = HttpRequest(sleep_time=SLEEP_TIME)
        self.api_v2_request.headers['Version'] = '2.0'

        self.tor_proxy = TorProxy()
        self.driver = None
        self.accounts = []
        self.categories = []

        # Progress variables
        self.account_index = 0
        self.search_link_index = 0
        self.page = 1

        # Configuration variables
        self.csv_filename = 'items.csv'
        self.xlsx_filename = 'items.xlsx'
        self.json_filename = 'items.json'
        self.image_dir = 'img'
        self.save_images = False
        self.restart_on_error = False
        self.use_tor = True
        self.search_links = []

        self.should_close = False
        keyboard.add_hotkey(HOTKEY_TERMINATE, self.close_query)

    def __del__(self):
        self.close_driver()

    def cleanup(self):
        keyboard.remove_hotkey(HOTKEY_TERMINATE)

    def close_query(self):
        logging.info('PROGRAM CLOSE QUERY RECEIVED. '
                     'PLEASE WAIT FOR CURRENT PAGE SCRAPING COMPLETION.')
        self.should_close = True

####################### SELENIUM WEBDRIVER INIT / CLOSE #######################

    def init_driver(self, tor_proxy=False) -> bool:
        self.close_driver()

        try:
            if tor_proxy:
                proxy = urlparse(TOR_SOCKS_PROXIES['https'])

                profile = webdriver.FirefoxProfile()
                profile.set_preference('network.proxy.type', 1)
                profile.set_preference('network.proxy.socks', proxy.hostname)
                profile.set_preference('network.proxy.socks_port', proxy.port)
                profile.set_preference('network.proxy.socks_remote_dns', False)
                profile.update_preferences()

                self.driver = webdriver.Firefox(firefox_profile=profile)
            else:
                self.driver = webdriver.Firefox()

            self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        except Exception:
            logging.exception('Error while webdriver initializing.')
            return False

        return True

    def close_driver(self):
        if self.driver != None:
            try:
                self.driver.quit()
            except Exception:
                logging.exception('Error while webdriver closing.')

            self.driver = None

############################### LOGIN / LOGOUT ################################

    def login_executed(self) -> bool:
        try:
            WebDriverWait(self.driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.XPATH, f"//a[contains(@href, '{LOGOUT_LINK}')]")
                )
            )
        except TimeoutException:
            return False
        except Exception:
            logging.exception('Error while checking login status.')
            return None

        return True

    def get_current_account(self):
        account = self.accounts[self.account_index]
        login = account['login']
        password = account['password']

        return login, password

    def execute_login(self, login='', password='',
                      driver_just_opened=False) -> bool:

        if not driver_just_opened:
            login_executed = self.login_executed()

            if login_executed:
                return True
            elif login_executed is None:
                return False

        try:
            self.driver.get(LOGIN_URL)
        except TimeoutException:
            logging.exception(f'Timeout while loading [{LOGIN_URL}] page.')
            return False

        try:
            login_input = self.driver.find_element(By.ID, 'userEmail')
        except NoSuchElementException:
            logging.exception("Can't locate email input.")
            return False

        try:
            password_input = self.driver.find_element(By.ID, 'userPass')
        except NoSuchElementException:
            logging.exception("Can't locate password input.")
            return False

        try:
            login_button = self.driver.find_element(By.ID, 'se_userLogin')
        except NoSuchElementException:
            logging.exception("Can't locate login button.")
            return False

        if not login or not password:
            login, password = self.get_current_account()

        login_input.send_keys(login)
        time.sleep(WAIT_CLICK)
        password_input.send_keys(password)
        time.sleep(WAIT_CLICK)
        login_button.click()
        time.sleep(WAIT_TIMEOUT)

        if self.login_executed():
            return True
        else:
            # Second try
            time.sleep(WAIT_TIMEOUT)
            if not self.login_executed():
                logging.error("Can't execute login.")
                return False

    def execute_logout(self) -> bool:
        login_executed = self.login_executed()

        if login_executed is None:
            return False
        elif not login_executed:
            return True

        try:
            self.driver.get(LOGOUT_URL)
        except TimeoutException:
            logging.exception(f'Timeout while loading [{LOGOUT_URL}] page.')
            return False

        login_executed = self.login_executed()

        if (login_executed is None) or login_executed:
            logging.error("Can't execute logout.")
            return False
        else:
            return True

    def execute_relogin(self, driver_just_opened=False) -> bool:
        logging.info('Executing re-login.')

        if self.account_index >= len(self.accounts) - 1:
            logging.warning('Out of accounts. Setting account cursor to zero.')
            self.account_index = 0
        else:
            self.account_index += 1

        if not self.save_progress():
            logging.warning("Can't save the next account index to the file.")

        if (not driver_just_opened) and (not self.execute_logout()):
            return False

        if not self.execute_login(driver_just_opened):
            return False

        return True

################################ ACCESS TOKEN #################################

    def add_auth_header(self, request: HttpRequest, auth_token: str):
        request.headers['Authorization'] = f'Bearer {auth_token}'

    def add_auth_headers(self, auth_token: str):
        self.add_auth_header(self.api_request, auth_token)
        self.add_auth_header(self.api_v2_request, auth_token)

    def get_access_token(self) -> str:
        try:
            cookies = self.driver.get_cookies()
        except Exception:
            logging.exception("Can't get cookies from the webdriver.")
            return None

        # For authorized access
        for cookie in cookies:
            # print(cookie)
            if cookie['name'] == 'access_token':
                return cookie['value']

        # For anonymous access
        for cookie in cookies:
            if cookie['name'] == 'a_access_token':
                return cookie['value']

        logging.error("Can't find 'access_token' cookie.")
        return None

    def get_random_item_url(self) -> str:
        html = self.request.get_html(HTTP_HOST)
        if not html:
            return None

        try:
            item_url = (BeautifulSoup(html, 'lxml')
                        .find('h4', class_='normal')
                        .a['href'])
        except (AttributeError, KeyError):
            logging.exception('Error while parsing random item URL.')
            return None

        return item_url

    def init_token_anonymous(self) -> bool:
        logging.info('Getting anonymous API token.')
        logging.info('Starting TOR.')
        self.tor_proxy.restart(wait=True)
        if not self.tor_proxy.test_ok():
            logging.error('Testing TOR: ERROR.')
            return False
        else:
            logging.info('Testing TOR: OK.')

        if not self.init_driver(tor_proxy=True):
            return False

        random_url = self.get_random_item_url()
        if random_url is None:
            self.close_driver()
            return False

        try:
            self.driver.get(random_url)
        except TimeoutException:
            logging.exception(f'Timeout while loading [{random_url}] page.')
            self.close_driver()
            return False

        access_token = self.get_access_token()
        if access_token is None:
            self.close_driver()
            return False

        self.add_auth_header(self.api_proxy_request, access_token)
        self.close_driver()
        return True

    def init_token_personal(self) -> bool:
        logging.info('Getting personal API token.')

        if not self.init_driver():
            return False

        if self.api_request.headers.get('Authorization'):
            if not self.execute_relogin(driver_just_opened=True):
                self.close_driver()
                return False
        else:
            if not self.execute_login(driver_just_opened=True):
                self.close_driver()
                return False

        access_token = self.get_access_token()
        if access_token is None:
            self.close_driver()
            return False

        self.add_auth_headers(access_token)
        self.close_driver()
        return True

################################ INIT METHODS #################################

    def str_to_bool(self, value: str) -> bool:
        if value.strip().lower() in ['true', '1']:
            return True
        else:
            return False

    def load_config(self) -> bool:
        parser = ConfigParser()

        try:
            parser.read(CONFIG_FILENAME, encoding='utf-8')
        except Exception:
            logging.exception("Can't read config file.")
            return False

        csv_filename = parser.get('paths', 'csv_filename', fallback=None)
        if csv_filename is None:
            logging.error("Can't read config value: csv_filename.")
            return False
        else:
            self.csv_filename = csv_filename

        xlsx_filename = parser.get('paths', 'xlsx_filename', fallback=None)
        if xlsx_filename is None:
            logging.error("Can't read config value: xlsx_filename.")
            return False
        else:
            self.xlsx_filename = xlsx_filename

        json_filename = parser.get('paths', 'json_filename', fallback=None)
        if json_filename is None:
            logging.error("Can't read config value: json_filename.")
            return False
        else:
            self.json_filename = json_filename

        image_dir = parser.get('paths', 'image_dir', fallback=None)
        if image_dir is None:
            logging.error("Can't read config value: image_dir.")
            return False
        else:
            self.image_dir = image_dir

        save_images = parser.get('general', 'save_images', fallback=None)
        if save_images is None:
            logging.error("Can't read config value: save_images.")
            return False
        else:
            self.save_images = self.str_to_bool(save_images)

        restart_on_error = parser.get('general', 'restart_on_error',
                                      fallback=None)
        if restart_on_error is None:
            logging.error("Can't read config value: restart_on_error.")
            return False
        else:
            self.restart_on_error = self.str_to_bool(restart_on_error)

        use_tor = parser.get('general', 'use_tor', fallback=None)
        if use_tor is None:
            logging.error("Can't read config value: use_tor.")
            return False
        else:
            self.use_tor = self.str_to_bool(use_tor)

        try:
            with open(SEARCH_LINKS_FILENAME, encoding='utf-8') as f:
                lines = f.readlines()
        except OSError:
            logging.error(f"Can't load the file {SEARCH_LINKS_FILENAME}.")
            return False

        self.search_links.clear()
        for line in lines:
            line = line.strip()
            if line:
                self.search_links.append(line)

        if not self.search_links:
            logging.error('The search links list is empty.')
            return False

        return True

    def remove_if_exists(self, filename) -> bool:
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                logging.warning(f"Can't remove the file {filename}.")
                return False
            else:
                logging.info(f'The file {filename} was removed.')

        return True

    def save_progress(self) -> bool:
        progress = {
            'account_index': self.account_index,
            'search_link_index': self.search_link_index,
            'page': self.page,
        }

        return save_items_json(progress, PROGRESS_FILENAME)

    def load_progress(self) -> bool:
        if os.path.exists(PROGRESS_FILENAME):
            progress = load_items_json(PROGRESS_FILENAME)
            if not progress:
                return False

            self.account_index = progress['account_index']
            self.search_link_index = progress['search_link_index']
            self.page = progress['page']

        return True

    def reset_progress(self) -> bool:
        logging.info('Clearing progress. '
                     'Starting the entire process from the beginning.')

        if (self.remove_if_exists(PROGRESS_FILENAME) and
                self.remove_if_exists(self.json_filename)):
            return True
        else:
            logging.error('Clearing progress failure.')
            return False

    def load_accounts(self) -> bool:
        self.accounts = load_items_csv(ACCOUNTS_FILENAME, ACCOUNTS_COLUMNS)
        if not self.accounts:
            logging.error('No accounts credentials loaded.')
            return False

        return True

    def init_categories(self) -> bool:
        logging.info('Retrieving categories list.')
        json = self.api_v2_request.get_json(API_CATEGORIES_URL)
        if json is None:
            return False

        if json.get('error'):
            logging.error('Error while API request: ' + str(json['error']))
            return False

        self.categories.clear()

        try:
            for category in json['data']:
                self.categories.append({
                    'id': category['id'],
                    'parent_id': category['parent_id'],
                    'name': category['name'],
                })
        except Exception:
            logging.exception('Error while parsing categories.')
            return False

        return True

    def get_category(self, category_id: int) -> dict:
        for category in self.categories:
            if category['id'] == category_id:
                return category

        return None

    def get_breadcrumbs(self, category_id: int) -> str:
        category = self.get_category(category_id)
        if category == None:
            return None
        breadcrumbs = [category['name']]

        while category['parent_id'] != 0:
            category = self.get_category(category['parent_id'])
            if category == None:
                return None
            breadcrumbs.insert(0, category['name'])

        return ' >> '.join(breadcrumbs)

    def init(self, reset_progress=False) -> bool:
        logging.info('Starting scraping process.')

        if not self.load_config():
            return False

        if reset_progress and not self.reset_progress():
            return False

        if not os.path.exists(self.image_dir):
            try:
                os.mkdir(self.image_dir)
            except OSError:
                logging.warning("Can't create images folder.")

        if not(self.load_accounts() and
               self.load_progress() and
               self.init_token_personal() and
               self.init_categories()):
            return False

        if self.use_tor and not self.init_token_anonymous():
            return False

        return True

    def _check_accounts(self) -> bool:
        if not (self.load_accounts() and self.init_driver()):
            return False

        if self.execute_login(driver_just_opened=True):
            login, password = self.get_current_account()
            logging.info('Successful authorization:\n'
                         + f'login: {login}\npassword: {password}')
        else:
            self.close_driver()
            return False

        while self.account_index < len(self.accounts) - 1:
            if self.execute_relogin():
                login, password = self.get_current_account()
                logging.info('Successful authorization:\n'
                             + f'login: {login}\npassword: {password}')

                wait_time = 3 * WAIT_TIMEOUT
                logging.info(f'Waiting {wait_time} seconds '
                             'before next authorization.')
                time.sleep(wait_time)
            else:
                self.close_driver()
                return False

        self.close_driver()
        return True

    # def _check_accounts(self) -> bool:
    #     if not self.load_accounts():
    #         return False

    #     while self.account_index < len(self.accounts) - 1:
    #         if self.init_token_personal():
    #             login, password = self.get_current_account()
    #             logging.info('Successful authorization:\n'
    #                          + f'login: {login}\npassword: {password}')

    #             wait_time = 3 * WAIT_TIMEOUT
    #             logging.info(f'Waiting {wait_time} seconds '
    #                          'before next authorization.')
    #             time.sleep(wait_time)
    #         else:
    #             return False

    #     return True

    def check_accounts(self) -> bool:
        logging.info('Checking user accounts. It may take a while.')

        if self._check_accounts():
            logging.info('All accounts are valid.')
            return True
        else:
            login, password = self.get_current_account()
            logging.error('Error while checking accounts.'
                          'The last account credentials are:\n'
                          + f'login: {login}\npassword: {password}')
            return False

############################## SCRAPING METHODS ###############################

    def get_page_count(self, url: str) -> int:
        html = self.request.get_html(url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'lxml')

        page_link_last = soup.find('a', attrs={'data-cy': 'page-link-last'})
        if page_link_last:
            try:
                page_count = int(page_link_last.span.get_text())
            except (AttributeError, ValueError):
                logging.exception('Error while parsing page count.')
                return None
        else:
            page_count = 1

        return page_count

    # First page index is 1 (not 0), last page index is page count
    def get_item_ids(self, base_url: str, page: int) -> list:
        html = self.request.get_html(f'{base_url}?page={page}')
        if not html:
            return None

        item_ids = []

        try:
            div_tags = (
                BeautifulSoup(html, 'lxml')
                .find('table', id='offers_table')
                .find_all('div', class_='offer-wrapper')
            )

            for div_tag in div_tags:
                item_ids.append(int(div_tag.table['data-id']))
        except (AttributeError, KeyError, ValueError):
            logging.exception('Error while parsing item ids.')
            return None

        return item_ids

    def scrape_phones(self, item_id: int, anonymous: bool = True) -> list:
        if anonymous:
            logging.info('Retrieving phones as non-protected '
                         + f'(item id = {item_id}).')
            request = self.api_proxy_request
            init_token = self.init_token_anonymous
        else:
            logging.info('Retrieving phones as protected '
                         + f'(item id = {item_id}).')
            request = self.api_request
            init_token = self.init_token_personal

        while True:
            json, status_code = request.get_json(
                API_PHONES_URL.format(item_id), return_status_code=True)

            if json is None:
                if status_code is None:
                    return None

                if status_code == requests.codes.too_many_requests:
                    logging.info("Can't retrieve phones: access disallowed. "
                                 'New access token required.')
                    if not init_token():
                        logging.error('Error when generating new token.')
                        return None
                    continue
                elif status_code == requests.codes.forbidden and not anonymous:
                    logging.info('Maybe too many requests. Waiting for '
                                 + f'{WAIT_FORBIDDEN_RETRY} seconds.')
                    time.sleep(WAIT_FORBIDDEN_RETRY)
                    continue
                else:
                    return None

            if json.get('error'):
                logging.error(
                    'Unexpected error when retrieving phones via API request '
                    + f'(item id = {item_id}):' + str(json['error']))
                return None

            try:
                phones = json['data']['phones']
            except Exception:
                logging.exception('Error while parsing phones JSON. '
                                  + f'Item id = {item_id}.')
                return None
            else:
                return phones

        return None

    def format_date_time(self, date_time_text: str) -> str:
        return date_time_text.split('+')[0].replace('T', ' ')

    def scrape_item(self, item_id: int) -> dict:
        logging.info(f'Scraping item (id = {item_id}).')

        item = {
            'id': item_id,
            'url': '',
            'title': '',
            'category': '',
            'last_refresh_time': '',
            'created_time': '',
            'price': 'N/A',
            'state': 'N/A',
            'description': '',
            'city': '',
            'region': '',
            'photos': '', # Splitted with ', '
            'contact_name': '',
            'contact_phones': 'N/A', # Splitted with ', '
            'user_id': '',
            'user_name': '',
            'user_created': '',
            'user_last_seen': '',
        }

        json, status_code = self.api_request.get_json(
            API_OFFERS_URL.format(item_id), return_status_code=True)

        if (status_code and ((status_code == requests.codes.gone) or
                             (status_code == requests.codes.not_found))):
            logging.warning('The requested item not found. Skipping.')
            return False

        if json is None:
            return None

        if json.get('error'):
            logging.error('Error while API request: ' + str(json['error']))
            return None

        try:
            item['url'] = json['data']['url']
            item['title'] = json['data']['title']

            breadcrumbs = self.get_breadcrumbs(json['data']['category']['id'])
            if breadcrumbs is None:
                logging.error("Error while building 'breadcrumbs' string.")
                return None
            item['category'] = breadcrumbs

            item['last_refresh_time'] = self.format_date_time(
                json['data']['last_refresh_time'])

            item['created_time'] = self.format_date_time(
                json['data']['created_time'])

            for param in json['data']['params']:
                if param['key'] == 'price':
                    item['price'] = param['value']['label']
                elif param['key'] == 'state':
                    item['state'] = param['value']['label']

            item['description'] = (json['data']['description']
                                   .replace('\n', '').replace('\r', ''))

            item['city'] = json['data']['location']['city']['name']
            item['region'] = json['data']['location']['region']['name']

            if json['data']['photos']:
                photo_urls = []
                for photo in json['data']['photos']:
                    photo_url = (photo['link']
                                 .replace('{width}', str(photo['width']))
                                 .replace('{height}', str(photo['height'])))
                    photo_urls.append(photo_url)

                item['photos'] = ', '.join(photo_urls)

                if self.save_images:
                    logging.info(f'Saving item images (id = {item_id}).')

                    images_path = os.path.join(self.image_dir, str(item_id))

                    if not os.path.exists(images_path):
                        try:
                            os.mkdir(images_path)
                        except OSError:
                            logging.error("Can't create images folder "
                                          f'for item with id = {item_id}.')

                    if os.path.exists(images_path):
                        for index, photo in enumerate(json['data']['photos']):
                            photo_url = photo_urls[index]
                            photo_filename = os.path.join(
                                images_path, photo['filename'] + '.jpg')
                            self.request.save_image(photo_url, photo_filename)

            item['contact_name'] = json['data']['contact']['name']

            if json['data']['contact']['phone']:
                anonymous = False
                if not json['data']['protect_phone'] and self.use_tor:
                    anonymous = True
                phones = self.scrape_phones(item_id, anonymous=anonymous)

                if phones is None:
                    return None

                for i in range(len(phones)):
                    phones[i] = clean_phone(phones[i])
                    if len(phones[i]) == 10:
                        phones[i] = '+38' + phones[i]
                    elif len(phones[i]) == 12 and phones[i].startswith('380'):
                        phones[i] = '+' + phones[i]

                item['contact_phones'] = ', '.join(phones)

            item['user_id'] = json['data']['user']['id']
            item['user_name'] = json['data']['user']['name']
            item['user_created'] = self.format_date_time(
                json['data']['user']['created'])
            item['user_last_seen'] = self.format_date_time(
                json['data']['user']['last_seen'])
        except Exception:
            logging.exception('Error while parsing item JSON.')
            return None

        return item

    def item_is_scraped(self, items: list, item_id: int) -> bool:
        for item in items:
            if item['id'] == item_id:
                logging.info(f'The item with id = {item_id} '
                             'is already scraped. Skipping.')
                return True
        return False

    def scrape_all_items(self) -> list:
        if os.path.exists(self.json_filename):
            logging.info('Loading previous scraping result.')
            items = load_items_json(self.json_filename)
        else:
            items = []

        while self.search_link_index < len(self.search_links):
            base_url = self.search_links[self.search_link_index]
            logging.info(f'Scraping search request: {base_url}.')

            page_count = self.get_page_count(base_url)
            if page_count == None:
                return None
            logging.info(f'Total page count: {page_count}.')

            while self.page <= page_count:
                logging.info('Scraping items '
                             + f'for page {self.page} of {page_count}.')

                item_ids = self.get_item_ids(base_url, self.page)
                if item_ids == None:
                    return None

                for item_id in item_ids:
                    if self.item_is_scraped(items, item_id):
                        continue
                    item = self.scrape_item(item_id)
                    if item is None:
                        return None
                    if not item:
                        continue

                    items.append(item)

                logging.info(f'Items currently scraped: {len(items)}.')
                if save_items_json(items, self.json_filename):
                    saving_result = 'OK'
                else:
                    saving_result = 'FAILURE'
                logging.info('Saving intermediate results for page '
                             + f'{self.page}: {saving_result}.')

                self.page += 1
                if not self.save_progress():
                    logging.warning("Can't save the next page number.")

                if self.should_close:
                    return None

            self.page = 1
            self.search_link_index += 1
            if not self.save_progress():
                logging.warning("Can't save the next category index.")

        return items

    def get_columns(self, item: dict) -> list:
        return list(item.keys())

    def _execute_scraping(self) -> bool:
        try:
            items = self.scrape_all_items()
            if self.should_close:
                logging.info('Scraping process stopped by user.')
                return True

            if items is None:
                logging.error(FATAL_ERROR_STR)
                return False

            logging.info('Scraping process complete. Now saving the results.')

            if not save_items_csv(items, self.get_columns(items[0]),
                                  self.csv_filename):
                logging.error(FATAL_ERROR_STR)
                return False

            if not save_items_xlsx(items, self.get_columns(items[0]),
                                   self.xlsx_filename):
                logging.error(FATAL_ERROR_STR)
                return False

            logging.info('Saving complete.')
        except Exception:
            logging.exception(FATAL_ERROR_STR)
            return False

        return True

    def execute_scraping(self) -> bool:
        if self.restart_on_error:
            logging.info("Automatic 'restart-on-error' mode activated.")
            while not self._execute_scraping():
                logging.info('Restarting scraping process '
                             'after a critical fail.')
                time.sleep(WAIT_TIMEOUT)

            return True
        else:
            return self._execute_scraping()

############################# PROGRAM ENTRY POINT #############################

def main():
    scraper = ScraperOLX()

    if '--check-accounts' in sys.argv:
        scraper.check_accounts()
        return

    if '--reset-progress' in sys.argv:
        reset_progress = True
    else:
        reset_progress = False

    if not scraper.init(reset_progress=reset_progress):
        logging.error(FATAL_ERROR_STR)
        scraper.cleanup()
        return

    scraper.execute_scraping()
    scraper.cleanup()

if __name__ == '__main__':
    main()
