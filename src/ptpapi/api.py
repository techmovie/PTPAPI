#!/bin/env python
"""The entrypoint module for access the API"""
import re
import os
import pickle
import logging

from bs4 import BeautifulSoup as bs4
from six.moves import configparser, html_parser
import requests

import ptpapi
from ptpapi.config import config
from ptpapi.session import session
from ptpapi.user import CurrentUser
from ptpapi.error import PTPAPIException

LOGGER = logging.getLogger(__name__)


def login(**kwargs):
    """Simple helper function"""
    return API(**kwargs)


class API(object):
    """Used for instantiating an object that can access the API"""
    def __init__(self, username=None, password=None, passkey=None):
        j = None
        self.cookies_file = os.path.expanduser(config.get('Main', 'cookiesFile'))
        LOGGER.info("Initiating login sequence.")
        password = (password or config.get('PTP', 'password'))
        username = (username or config.get('PTP', 'username'))
        passkey = (passkey or config.get('PTP', 'passkey'))
        if os.path.isfile(self.cookies_file):
            self.__load_cookies()
            # A really crude test to see if we're logged in
            session.max_redirects = 1
            try:
                req = session.base_get('torrents.php')
            except requests.exceptions.TooManyRedirects:
                if os.path.isfile(self.cookies_file):
                    os.remove(self.cookies_file)
                session.cookies = requests.cookies.RequestsCookieJar()
            session.max_redirects = 3
        if not os.path.isfile(self.cookies_file):
            if not password or not passkey or not username:
                raise PTPAPIException("Not enough info provided to log in.")
            try:
                req = session.base_post('ajax.php?action=login',
                                        data={"username": username,
                                              "password": password,
                                              "passkey": passkey})
                j = req.json()
            except ValueError:
                if req.status_code == 200:
                    raise PTPAPIException("Could not parse returned json data.")
                else:
                    if req.status_code == 429:
                        LOGGER.critical(req.text.strip())
                    req.raise_for_status()
            if j["Result"] != "Ok":
                raise PTPAPIException("Failed to log in. Please check the username, password and passkey. Response: %s" % j)
            self.__save_cookie()
            # Get some information that will be useful for later
            req = session.base_get('index.php')
        ptpapi.util.raise_for_cloudflare(req.text)
        LOGGER.info("Login successful.")
        self.current_user_id = re.search(r'user.php\?id=(\d+)', req.text).group(1)
        self.auth_key = re.search(r'auth=([0-9a-f]{32})', req.text).group(1)

    def logout(self):
        """Forces a logout."""
        os.remove(self.cookies_file)
        return session.base_get('logout.php', params={'auth': self.auth_key})

    def __save_cookie(self):
        """Save requests' cookies to a file"""
        with open(self.cookies_file, 'wb') as fileh:
            LOGGER.debug("Pickling HTTP cookies to %s", self.cookies_file)
            pickle.dump(requests.utils.dict_from_cookiejar(session.cookies), fileh)

    def __load_cookies(self):
        """Reload requests' cookies"""
        with open(self.cookies_file, 'rb') as fileh:
            LOGGER.debug("Unpickling HTTP cookies from file %s", self.cookies_file)
            session.cookies = requests.utils.cookiejar_from_dict(pickle.load(fileh))

    def current_user(self):
        """Helper function to get the current user"""
        return CurrentUser(self.current_user_id)

    def search(self, filters):
        """Perform a movie search"""
        if 'name' in filters:
            filters['searchstr'] = filters['name']
        filters['json'] = 'noredirect'
        ret_array = []
        for movie in session.base_get('torrents.php', params=filters).json()['Movies']:
            if 'Directors' not in movie:
                movie['Directors'] = []
            if 'ImdbId' not in movie:
                movie['ImdbId'] = '0'
            movie['Title'] = html_parser.HTMLParser().unescape(movie['Title'])
            ret_array.append(ptpapi.Movie(data=movie))
        return ret_array

    def need_for_seed(self, filters={}):
        """List torrents that need seeding"""
        data = ptpapi.util.snarf_cover_view_data(session.base_get("needforseed.php", params=filters).content)
        torrents = []
        for m in data:
            torrent = m['GroupingQualities'][0]['Torrents'][0]
            torrent['Link'] = config.get('Main', 'baseURL') + bs4(torrent['Title'], 'lxml').find('a')['href']
            torrents.append(torrent)
        return torrents

    def contest_leaders(self):
        """Get data on who's winning"""
        LOGGER.debug("Fetching contest leaderboard")
        soup = bs4(session.base_get("contestleaders.php").content, "html.parser")
        ret_array = []
        for cell in soup.find('table', class_='table--panel-like').find('tbody').find_all('tr'):
            ret_array.append((cell.find_all('td')[1].get_text(), cell.find_all('td')[2].get_text()))
        return ret_array

    def collage(self, coll_id, search_terms={}):
        """Simplistic representation of a collage, might be split out later"""
        search_terms['id'] = coll_id
        req = session.base_get('collages.php', params=search_terms)
        movies = []
        for movie in ptpapi.util.snarf_cover_view_data(req.text):
            movie['Torrents'] = []
            for group in movie['GroupingQualities']:
                movie['Torrents'].extend(group['Torrents'])
            movies.append(ptpapi.Movie(data=movie))
        return movies

    def log(self):
        """Gets the PTP log"""
        soup = bs4(session.base_get('log.php').content, "html.parser")
        ret_array = []
        for message in soup.find('table').find('tbody').find_all('tr'):
            ret_array.append((message.find('span', class_='time')['title'],
                              message.find('span', class_='log__message').get_text().lstrip().encode('UTF-8')))
        return ret_array

