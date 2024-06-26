"""
exposes the API for benchify
"""

import sys
import time
from typing import Any, Dict
import webbrowser
import ast
import requests
import jwt
#pylint:disable=import-error
import typer
#pylint:disable=import-error
from auth0.authentication.token_verifier \
    import TokenVerifier, AsymmetricSignatureVerifier

from rich import print as rprint
from rich.console import Console

#pylint:disable=import-error
from benchify.source_manipulation import \
    get_function_source, get_all_function_names

app = typer.Typer()

AUTH0_DOMAIN    = 'benchify.us.auth0.com'
AUTH0_CLIENT_ID = 'VessO49JLtBhlVXvwbCDkeXZX4mHNLFs'
ALGORITHMS      = ['RS256']
#pylint:disable=invalid-name
id_token        = None
#pylint:disable=invalid-name
current_user    = None

def validate_token(token_to_validate: str) -> Dict[str,Any]:
    """
    Verify the token and its precedence
    """
    jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    issuer = f"https://{AUTH0_DOMAIN}/"
    sign_verifier = AsymmetricSignatureVerifier(jwks_url)
    token_verifier = TokenVerifier(
        signature_verifier=sign_verifier,
        issuer=issuer,
        audience=AUTH0_CLIENT_ID)
    decoded_payload = token_verifier.verify(token_to_validate)
    return decoded_payload

#pylint:disable=too-few-public-methods
class AuthTokens:
    """
    id and access tokens
    """
    id_token: str = ""
    access_token: str = ""
    def __init__(self, my_id_token, access_token):
        self.id_token = my_id_token
        self.access_token = access_token

def login() -> AuthTokens:
    """
    Runs the device authorization flow and stores the user object in memory
    """
    device_code_payload = {
        'client_id': AUTH0_CLIENT_ID,
        'scope': 'openid profile'
    }
    login_timeout = 60
    try:
        device_code_response = requests.post(
            f"https://{AUTH0_DOMAIN}/oauth/device/code", 
            data=device_code_payload, timeout=login_timeout)
    except requests.exceptions.Timeout:
        rprint('Error generating the device code')
        #pylint:disable=raise-missing-from
        raise typer.Exit(code=1)

    if device_code_response.status_code != 200:
        rprint('Error generating the device code')
        raise typer.Exit(code=1)

    rprint('Device code successful')
    device_code_data = device_code_response.json()

    rprint(
        '1. On your computer or mobile device navigate to: ', 
        device_code_data['verification_uri_complete'])
    rprint('2. Enter the following code: ', device_code_data['user_code'])

    try:
        webbrowser.open(device_code_data['verification_uri_complete'], new=1)
    except webbrowser.Error as _browser_exception:
        pass

    token_payload = {
        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        'device_code': device_code_data['device_code'],
        'client_id': AUTH0_CLIENT_ID
    }

    authenticated = False
    while not authenticated:
        rprint('Authenticating ...')
        token_response = requests.post(
            f"https://{AUTH0_DOMAIN}/oauth/token", data=token_payload,timeout=None)

        token_data = token_response.json()
        if token_response.status_code == 200:
            rprint('✅ Authenticated!')
            _ = validate_token(token_data['id_token'])
            #pylint:disable=global-statement
            global current_user
            current_user = jwt.decode(
                token_data['id_token'],
                algorithms=ALGORITHMS,
                options={"verify_signature": False})

            authenticated = True
            # Save the current_user.

        elif token_data['error'] not in ('authorization_pending', 'slow_down'):
            rprint(token_data['error_description'])
            raise typer.Exit(code=1)
        else:
            time.sleep(device_code_data['interval'])
    return AuthTokens(
        my_id_token=token_data['id_token'],
        access_token=token_data['access_token']
    )

@app.command()
def authenticate():
    """
    login if not already
    """
    if current_user is None:
        login()
    rprint("✅ Logged in " + str(current_user))

#pylint:disable = too-many-return-statements
@app.command()
def analyze():
    """
    send the request to analyze the function specified by the command line arguments
    and show the results
    """
    if len(sys.argv) == 1:
        rprint("⬇️ Please specify the file to be analyzed.")
        return

    file = sys.argv[1]

    if current_user is None:
        auth_tokens = login()
        rprint(f"Welcome {current_user['name']}!")
    function_str = None

    try:
        rprint("Scanning " + file + " ...")
        # platform dependent encoding used
        #pylint:disable=unspecified-encoding
        with open(file, "r", encoding=None) as file_reading:
            function_str = file_reading.read()
            tree = ast.parse(function_str)
            # is there more than one function in the file?
            function_names = get_all_function_names(tree)
            if len(function_names) > 1:
                if len(sys.argv) == 2:
                    rprint("Since there is more than one function in the " + \
                        "file, please specify which one you want to " + \
                        "analyze, e.g., \n$ benchify sortlib.py " + function_names[1])
                    return

                function_name = sys.argv[2]
                function_str = get_function_source(
                    tree, function_name, function_str)
                if function_str:
                    pass
                else:
                    rprint(f"🔍 Function named {sys.argv[2]} not " + \
                        f"found in {file}.")
                    return
            elif len(function_names) == 1:
                function_str = get_function_source(
                    tree, function_names[0], function_str)
            else:
                rprint(f"There were no functions in {file}." + \
                    " Cannot continue 😢.")
                return
    except OSError as reading_exception:
        rprint(f"Encountered exception trying to read {file}: {reading_exception}." + \
            " Cannot continue 😢.")
        return
    except SyntaxError as reading_exception:
        rprint(f"Encountered exception trying to parse into ast {file}: {reading_exception}." + \
            " Cannot continue 😢.")
        return
    if function_str is None:
        rprint(f"Error attempting to read {file}." + \
            " Cannot continue 😢.")
        return

    console = Console()
    url = "https://benchify.cloud/analyze"
    params = {'test_func': function_str}
    headers = {'Authorization': f'Bearer {auth_tokens.id_token}'}
    expected_time = ("1 minute", 60)
    rprint(f"Analyzing.  Should take about {expected_time[0]} ...")
    try:
        # timeout 5 times longer than the expected, to account for above average times
        response = requests.get(url, params=params, headers=headers, timeout=expected_time[1]*5)
    except requests.exceptions.Timeout:
        rprint("Timed out")
    console.print(response.text)

if __name__ == "__main__":
    app()
