import time
import re
import os
import logging
from urllib.parse import urlparse, quote_plus

from bs4 import BeautifulSoup

from .http_request import HttpRequest
from .scraping_utils import swap_scheme

GOOGLE_SEARCH_URL = 'https://www.google.com/search?q='
GOOGLE_SEARCH_DELAY = 5
GOOGLE_CAPTCHA_SIGNATURE = 'captcha-form'

# Only files with these extensions are treated as webpages by the crawler
HTML_EXTENSIONS = [
    '.htm', '.html', '.asp', '.aspx', '.cgi', '.php', '.pl', '.py'
]

EMAIL_RE = re.compile(r'\b([A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,6})\b')

PHONE_RE = re.compile(r'(\D|^)(\+(9[976]\d|8[987530]\d|6[987]\d|5[90]\d|42\d|'
                      r'3[875]\d|2[98654321]\d|9[8543210]|8[6421]|6[6543210]|'
                      r'5[87654321]|4[987654310]|3[9643210]|2[70]|7|1)'
                      r'\d{1,14})(\D|$)')

PHONE_CONTEXT_RE = re.compile(
    r'(tel|phone|phone.?number|mobile|mob)\D{0,3}((9[976]\d|8[987530]\d|'
    r'6[987]\d|5[90]\d|42\d|3[875]\d|2[98654321]\d|9[8543210]|8[6421]|'
    r'6[6543210]|5[87654321]|4[987654310]|3[9643210]|2[70]|7|1)\d{1,14})(\D|$)'
)

MIN_PHONE_LEN = 8

MAX_RECURSION_DEPTH = 1

driver = None # Global Selenium Webdriver
request = HttpRequest() # Global HttpRequest object

############################## Crawler Functions ##############################

def get_host_url(url: str) -> str:
    return '{}://{}'.format(urlparse(url).scheme, urlparse(url).netloc)

# The function returns the list of the links to html pages only
def get_internal_links(soup: BeautifulSoup, url: str) -> list:
    host_url = get_host_url(url)
    href_re = re.compile(
        f'^(https?://(www.)?{urlparse(url).netloc}|(?!https?://))')

    internal_links = []

    for link in soup.find_all('a'):
        href = link.get('href', '').strip()
        if href.startswith('www.'):
            href = 'https://' + href

        if not href or not re.findall(href_re, href):
            continue

        if (href.startswith('#')
            or href.startswith('tel:')
            or href.startswith('viber://')
            or href.startswith('whatsapp://')
                or href.startswith('skype:')):
            continue

        if href.startswith('/'):
            href = host_url + href
        elif not href.startswith('http'):
            href = host_url + '/' + href

        # Mediafiles and such stuff should be skipped
        ext = os.path.splitext(urlparse(href).path)[1]
        if ext and ext not in HTML_EXTENSIONS:
            continue

        # Javascripts should be skipped
        if href.startswith('javascript'):
            continue

        # Links to different places of the same page should be treated as
        # one link
        if '#' in href:
            href = href.split('#')[0]

        if ((href not in internal_links)
                and (swap_scheme(href) not in internal_links)):
            internal_links.append(href)

    return internal_links

# The emails parameter serves both for input and output
# The function returns nothing
def find_distinct_emails(text: str, emails: list):
    for match in re.findall(EMAIL_RE, text):
        if match.lower() not in emails:
            emails.append(match.lower())

# The phones parameter serves both for input and output
# The function returns nothing
def find_distinct_phones(text: str, phones: list):
    text = re.sub(r'\s+|-|\(|\)|24/7', '', text)
    text = re.sub(r'\|+', '|', text)

    for match in re.findall(PHONE_RE, text):
        if (len(match[1]) >= MIN_PHONE_LEN) and (match[1] not in phones):
            phones.append(match[1])

    for match in re.findall(PHONE_CONTEXT_RE, text):
        phone = '+' + match[1]
        if (len(phone) >= MIN_PHONE_LEN) and (phone not in phones):
            phones.append(phone)

# The links, emails and phones parameters serve both for input and output
# The function returns nothing
def crawl(url: str, links: list, emails: list, phones: list, depth: int = 0):
    logging.info(f'Crawling page {url}')

    html = request.get_html(url)
    if not html:
        return

    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text(separator='|').lower()

    find_distinct_emails(text, emails)
    find_distinct_phones(text, phones)

    if depth >= MAX_RECURSION_DEPTH:
        return

    for link in get_internal_links(soup, url):
        if (link not in links) and (swap_scheme(link) not in links):
            links.append(link)
            crawl(link, links, emails, phones, depth = depth + 1)

def scrape_contact_data(url: str, force_recursive=False) -> dict:
    logging.info(f'Collecting contact data for site {url}')
    if not request.check_url(url):
        logging.warning(f'The site {url} not available.')
        return {
            'emails': [],
            'phones': [],
        }

    links = [get_host_url(url), get_host_url(url) + '/']
    emails = []
    phones = []

    if force_recursive:
        crawl(url, links, emails, phones)
    else:
        crawl(url, links, emails, phones, depth=MAX_RECURSION_DEPTH)
        if not emails or not phones:
            logging.info(f'Starting deep search for site {url}')
            crawl(url, links, emails, phones)

    return {
        'emails': emails,
        'phones': phones,
    }

########################### Google Search Functions ###########################

def google_search_items(netloc: str, query: str, find_items_func) -> list:
    global driver

    logging.info(f'Google search for "{netloc}"+{query}')

    request_url = GOOGLE_SEARCH_URL + quote_plus(f'"{netloc}" {query}')

    if driver == None:
        html = request.get_html(request_url)
        if not html:
            return []
    else:
        try:
            driver.get(request_url)
            time.sleep(GOOGLE_SEARCH_DELAY)

            if GOOGLE_CAPTCHA_SIGNATURE in driver.page_source:
                input('Solve CAPTCHA and press ENTER when ready...')
                time.sleep(GOOGLE_SEARCH_DELAY)
            html = driver.page_source
        except Exception:
            return []

    items = []
    soup = BeautifulSoup(html, 'lxml')

    try:
        for h3 in soup.find_all('h3'):
            text = h3.parent.parent.parent.get_text(separator='|').lower()
            if netloc in text:
                find_items_func(text, items)
    except AttributeError:
        logging.exception('Error while parsing Google Search results.')
        return []

    return items

def google_search_emails(netloc: str) -> list:
    return google_search_items(netloc, 'email', find_distinct_emails)

def google_search_phones(netloc: str) -> list:
    return google_search_items(netloc, 'phone', find_distinct_phones)
