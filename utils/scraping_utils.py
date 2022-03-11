import csv
import json
import re
import os
import os.path
import logging
import logging.handlers
import unicodedata

import xlsxwriter
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

# Directory name for saving log files
LOG_FOLDER = 'logs'

# Log file name
LOG_NAME = 'scraper.log'

# Full path to the log file
LOG_PATH = os.path.join(LOG_FOLDER, LOG_NAME)

# Maximum log file size
LOG_SIZE = 2 * 1024 * 1024

# Log files count for cyclic rotation
LOG_BACKUPS = 2

# Common text for displaying while script is shutting down
FATAL_ERROR_STR = 'Fatal error. Shutting down.'

# Characters not allowed in filenames
FORBIDDEN_CHAR_RE = r'[<>:"\/\\\|\?\*]'

NL = '\r\n'
LT = '\r\n'
CSV_DELIMITER = ','

LAST_PROCESSED_PAGE_FILENAME = 'last_processed_page.txt'

# Setting up configuration for logging
def setup_logging():
    logFormatter = logging.Formatter(
        fmt='[%(asctime)s] %(filename)s:%(lineno)d %(levelname)s - %(message)s',
        datefmt='%d.%m.%Y %H:%M:%S')
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.INFO)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)

    if not os.path.exists(LOG_FOLDER):
        try:
            os.mkdir(LOG_FOLDER)
        except OSError:
            logging.warning("Can't create log folder.")

    if os.path.exists(LOG_FOLDER):
        fileHandler = logging.handlers.RotatingFileHandler(
            LOG_PATH, mode='a', encoding='utf-8', maxBytes=LOG_SIZE,
            backupCount=LOG_BACKUPS)
        fileHandler.setFormatter(logFormatter)
        rootLogger.addHandler(fileHandler)

def fix_filename(filename: str, subst_char: str='_') -> str:
    return re.sub(FORBIDDEN_CHAR_RE, subst_char, filename)

def remove_umlauts(text: str) -> str:
    return (unicodedata.normalize('NFKD', text)
            .encode('ASCII', 'ignore')
            .decode('utf-8'))

def clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip())

def _get_plain_text(root_tag: Tag) -> str:
    text = ''

    for tag in root_tag.children:
        if isinstance(tag, NavigableString):
            text += re.sub(r'\s+', ' ', str(tag))
        else:
            if tag.name == 'br':
                text += '\n'
            elif tag.name == 'p':
                text += '\n'
                text += _get_plain_text(tag)
                text += '\n'
            elif tag.name in ('ul', 'ol'):
                text += '\n'
                text += _get_plain_text(tag)
            elif tag.name == 'li':
                text += '- ' + _get_plain_text(tag)
                text += '\n'
            else:
                text += _get_plain_text(tag)

    return text

def get_plain_text(root_tag: Tag) -> str:
    plain_text = re.sub(r' +', ' ', _get_plain_text(root_tag).strip())
    return '\n'.join([line.strip() for line in plain_text.split('\n')])

def clean_phone(phone: str) -> str:
    return re.sub(r'\s+|-|\(|\)', '', phone)

def swap_scheme(url: str) -> str:
    if url.startswith('http://'):
        return re.sub(r'^http://', 'https://', url)
    else:
        return re.sub(r'^https://', 'http://', url)

# Saves last processed page
def save_last_page(page: int) -> bool:
    try:
        with open(LAST_PROCESSED_PAGE_FILENAME, 'w') as f:
            f.write(str(page))
    except OSError:
        logging.exception("Can't save last processed page to a file.")
        return False
    return True

# Loads previously saved last processed page
def load_last_page() -> int:
    page = 0
    if os.path.exists(LAST_PROCESSED_PAGE_FILENAME):
        try:
            with open(LAST_PROCESSED_PAGE_FILENAME, 'r') as f:
                page = int(f.read())
        except OSError:
            logging.warning("Can't load last processed page from file.")
        except ValueError:
            logging.exception(f'File {LAST_PROCESSED_PAGE_FILENAME} '
                              'is currupted.')
    return page

# Saving prepared item data to a CSV file
def save_item_csv(item: dict, columns: list, filename: str,
                  first_item=False) -> bool:
    try:
        with open(filename, 'w' if first_item else 'a',
                  newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=CSV_DELIMITER, lineterminator=LT)
            if first_item:
                writer.writerow(columns)
            writer.writerow([item.get(key, '') for key in columns])
    except OSError:
        logging.exception(f"Can't write to CSV file {filename}.")
        return False
    except Exception as e:
        logging.exception('Scraped data saving fault.')
        return False

    return True

# Saves prepared items list to a CSV file
def save_items_csv(items: list, columns: list, filename: str) -> bool:
    for index, item in enumerate(items):
        if not save_item_csv(item, columns, filename,
                             first_item = (index == 0)):
            return False

    return True

def save_items_xlsx(items: list, columns: list, filename: str) -> bool:
    try:
        workbook = xlsxwriter.Workbook(filename)
    except Exception:
        logging.exception(f"Can't create {filename} workbook.")
        return False

    bold = workbook.add_format({'bold': True})
    worksheet = workbook.add_worksheet()

    for column, title in enumerate(columns):
        worksheet.write(0, column, title, bold)

    for item_index, item in enumerate(items):
        for column, key in enumerate(columns):
            worksheet.write(item_index + 1, column, str(item.get(key, '')))

    try:
        workbook.close()
    except Exception:
        logging.exception(f"Can't save {filename} workbook.")
        return False

    return True

def load_items_csv(filename: str, columns: list) -> list:
    if not os.path.exists(filename):
        return []

    items = []

    try:
        with open(filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=CSV_DELIMITER, lineterminator=LT)
            next(reader)
            for row in reader:
                item = {}
                for index, key in enumerate(columns):
                    item[key] = row[index]
                items.append(item)
    except OSError:
        logging.exception(f"Can't read CSV file {filename}.")
    except Exception:
        logging.exception('CVS file reading fault.')

    return items

# Saves item list to a JSON file
def save_items_json(items: list, filename: str) -> bool:
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=4)
    except OSError:
        logging.exception(f"Can't write to the file {filename}.")
        return False

    return True

def load_items_json(filename: str) -> list:
    try:
        with open(filename, encoding='utf-8') as f:
            items = json.load(f)
    except OSError:
        logging.warning(f"Can't load the file {filename}.")
        return []

    return items
