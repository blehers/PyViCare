from PyViCare.PyViCareAbstractOAuthManager import AbstractViCareOAuthManager
from PyViCare.PyViCareUtils import PyViCareBrowserOAuthTimeoutReachedError, PyViCareInvalidCredentialsError
import requests
import re
import json
import os
import pkce
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
from requests_oauthlib import OAuth2Session
import webbrowser

logger = logging.getLogger('ViCare')
logger.addHandler(logging.NullHandler())

AUTHORIZE_URL = 'https://iam.viessmann.com/idp/v2/authorize'
TOKEN_URL = 'https://iam.viessmann.com/idp/v2/token'
REDIRECT_PORT = 51125
VIESSMANN_SCOPE = ["IoT User", "offline_access"]
API_BASE_URL = 'https://api.viessmann.com/iot/v1'
AUTH_TIMEOUT = 60 * 3


class ViCareBrowserOAuthManager(AbstractViCareOAuthManager):
    class Serv(BaseHTTPRequestHandler):
        def __init__(self, callback, *args):
            self.callback = callback
            BaseHTTPRequestHandler.__init__(self, *args)

        def do_GET(self):
            self.callback(self.path)
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(
                "Success. You can close this browser window now.".encode("utf-8"))

    def __init__(self, client_id, token_file):
        super().__init__()
        self.token_file = token_file
        self.client_id = client_id
        self.oauth = self.__load_or_create_new_session()

    def __load_or_create_new_session(self):
        restore_oauth = self.__restoreToken()
        if restore_oauth is not None:
            return restore_oauth
        return self.__execute_browser_authentication()            

    def __execute_browser_authentication(self):
        redirect_uri = f"http://localhost:{REDIRECT_PORT}"
        oauth = OAuth2Session(
            self.client_id, redirect_uri=redirect_uri, scope=VIESSMANN_SCOPE)
        base_authorization_url, _ = oauth.authorization_url(AUTHORIZE_URL)
        code_verifier, code_challenge = pkce.generate_pkce_pair()
        authorization_url = f'{base_authorization_url}&code_challenge={code_challenge}&code_challenge_method=S256'

        webbrowser.open(authorization_url)

        code = None

        def callback(path):
            nonlocal code
            match = re.match(r"(?P<uri>.+?)\?code=(?P<code>[^&]+)", path)
            code = match.group('code')

        def handlerWithCallbackWrapper(*args):
            ViCareBrowserOAuthManager.Serv(callback, *args)

        server = HTTPServer(('localhost', REDIRECT_PORT),
                            handlerWithCallbackWrapper)
        server.timeout = AUTH_TIMEOUT
        server.handle_request()

        if code is None:
            logger.debug("Timeout reached")
            raise PyViCareBrowserOAuthTimeoutReachedError()

        logger.debug(f"Code: {code}")

        result = requests.post(url=TOKEN_URL, data={
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'code': code,
            'code_verifier': code_verifier
        }
        ).json()

        return self.__build_oauth_session(result, after_redirect=True)

    def __storeToken(self, token):
        if (self.token_file == None):
            return None

        with open(self.token_file, mode='w') as json_file:
            json.dump(token, json_file)
            logger.info("Token stored to file")

    def __restoreToken(self):
        if (self.token_file == None) or not os.path.isfile(self.token_file):
            return None

        with open(self.token_file, mode='r') as json_file:
            token = json.load(json_file)
            logger.info("Token restored from file")
            return self.__build_oauth_session(token, after_redirect=False)

    def __build_oauth_session(self, result, after_redirect):
        if 'access_token' not in result and 'refresh_token' not in result:
            logger.debug(f"Invalid result after redirect {result}")
            if after_redirect:
                raise PyViCareInvalidCredentialsError()
            else:
                logger.info(f"Invalid credentials, create new session")
                return self.__execute_browser_authentication()

        logger.debug(f"configure oauth: {result}")
        oauth = OAuth2Session(client_id=self.client_id, token=result)
        self.__storeToken(result)
        return oauth

    def renewToken(self):
        token = self.oauth.token
        result = requests.post(url=TOKEN_URL, data={
            'grant_type': 'refresh_token',
            'client_id': self.client_id,
            'refresh_token': token['refresh_token'],
        }
        ).json()

        self.oauth = self.__build_oauth_session(result, after_redirect=False)
