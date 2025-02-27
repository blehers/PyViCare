from PyViCare.PyViCareAbstractOAuthManager import AbstractViCareOAuthManager
from PyViCare.PyViCareUtils import PyViCareInvalidCredentialsError
import requests
import re
import pickle
import os
import pkce
from pickle import UnpicklingError
from requests_oauthlib import OAuth2Session
import logging
from contextlib import suppress

logger = logging.getLogger('ViCare')
logger.addHandler(logging.NullHandler())

AUTHORIZE_URL = 'https://iam.viessmann.com/idp/v2/authorize'
TOKEN_URL = 'https://iam.viessmann.com/idp/v2/token'
REDIRECT_URI = "vicare://oauth-callback/everest"
VIESSMANN_SCOPE = ["IoT User"]


class ViCareOAuthManager(AbstractViCareOAuthManager):
    def __init__(self, username, password, client_id, token_file):
        self.username = username
        self.password = password
        self.token_file = token_file
        self.client_id = client_id
        self.oauth = self.__restore_oauth_session_from_token(token_file)

    def __restore_oauth_session_from_token(self, token_file):
        existing_token = self._deserializeToken(token_file)
        if existing_token is not None:
            return OAuth2Session(self.client_id, token=existing_token)

        return self.__createNewSession(self.username, self.password, token_file)

    def __createNewSession(self, username, password, token_file=None):
        """Create a new oAuth2 sessions
        Viessmann tokens expire after 3600s (60min)
        Parameters
        ----------
        username : str
            e-mail address
        password : str
            password
        token_file: str
            path to serialize the token (will restore if already existing). No serialisation if not present

        Returns
        -------
        oauth:
            oauth sessions object
        """
        oauth = OAuth2Session(
            self.client_id, redirect_uri=REDIRECT_URI, scope=VIESSMANN_SCOPE)
        base_authorization_url, _ = oauth.authorization_url(AUTHORIZE_URL)

        code_verifier, code_challenge = pkce.generate_pkce_pair()
        authorization_url = f'{base_authorization_url}&code_challenge={code_challenge}&code_challenge_method=S256'
        logger.debug(f"Auth URL is: {authorization_url}")

        header = {'Content-Type': 'application/x-www-form-urlencoded'}
        response = requests.post(
            authorization_url, headers=header, auth=(username, password), allow_redirects=False)

        if 'Location' not in response.headers:
            logger.debug(f'Response: {response}')
            raise PyViCareInvalidCredentialsError()

        redirect_location = response.headers['Location']
        logger.debug(f"Redirect location is: {redirect_location}")
        match = re.match(
            r"(?P<uri>.+?)\?code=(?P<code>[^&]+)", redirect_location)
        if match is None or match.group('uri') != REDIRECT_URI:
            raise PyViCareInvalidCredentialsError()

        code = match.group('code')
        result = requests.post(url=TOKEN_URL, data={
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'redirect_uri': REDIRECT_URI,
            'code': code,
            'code_verifier': code_verifier
        }
        ).json()

        if 'access_token' not in result:
            logger.debug(f"Invalid result after redirect {result}")
            raise PyViCareInvalidCredentialsError()

        token_dict = {
            'access_token': result['access_token'],
            'token_type': 'bearer'
        }
        oauth = OAuth2Session(client_id=self.client_id, token=token_dict)
        logger.debug(f"Token received: {oauth.token}")
        self._serializeToken(oauth.token, token_file)
        logger.info("New token created")
        return oauth

    def renewToken(self):
        logger.info("Token expired, renewing")
        self.oauth = self.__createNewSession(
            self.username, self.password, self.token_file)
        logger.info("Token renewed successfully")

    def _serializeToken(self, oauth, token_file):
        logger.debug("Start serial")
        if token_file is None:
            logger.debug("Skip serial, no file provided.")
            return

        with open(token_file, mode='wb') as binary_file:
            pickle.dump(oauth, binary_file)

        logger.info("Token serialized to %s" % token_file)

    def _deserializeToken(self, token_file):
        if (token_file == None) or not os.path.isfile(token_file):
            logger.debug(
                "Token file argument not provided or file does not exist")
            return None

        logger.info("Token file exists")
        with suppress(UnpicklingError):
            with open(token_file, mode='rb') as binary_file:
                s_token = pickle.load(binary_file)
                logger.info("Token restored from file")
                return s_token
        logger.warning("Could not restore token")
        return None
