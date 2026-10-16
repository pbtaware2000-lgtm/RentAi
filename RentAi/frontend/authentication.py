"""SQLite-backed RentAI account and Streamlit session helpers."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.core.config import get_api_base_url, SettingsError

AUTH_STATE_KEYS=("rentai_id_token","rentai_refresh_token","rentai_expires_at","rentai_uid","rentai_role","rentai_display_name","rentai_email")
LOGIN_INPUT_KEYS=("rentai_login_email","rentai_login_password")
REGISTER_INPUT_KEYS=("rentai_register_name","rentai_register_email","rentai_register_password","rentai_register_confirm")
ROLE_PAGE_ACCESS={
"owner":{"Owner Dashboard","Home","Learning Module","Implementation Overview","Dataset & EDA","Rent Prediction","Rent Classification","Property Clustering","Property Search","Property Comparison","Location & Analytics","Property Insights","Algo Lab"},
"renter":{"Renter Dashboard","Home","Learning Module","Rent Prediction","Rent Classification","Property Search","Property Comparison","Location & Analytics","Property Insights"},
"broker":{"Broker Dashboard","Home","Learning Module","Rent Prediction","Rent Classification","Property Clustering","Property Search","Property Comparison","Location & Analytics","Property Insights","Algo Lab"},"admin":None}
class AuthenticationError(RuntimeError): pass

def normalize_email(email):
    value=email.strip() if isinstance(email,str) else ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",value): raise AuthenticationError("Enter a valid complete email address.")
    return value.lower()

def accessible_pages(role,page_names):
    if role not in ROLE_PAGE_ACCESS: raise AuthenticationError("This account does not have an approved RentAI role.")
    allowed=ROLE_PAGE_ACCESS[role]; return [name for name in page_names if allowed is None or name in allowed]
def page_is_allowed(role,page,page_names): return page in accessible_pages(role,page_names)

def _request(path,payload=None,token=None):
    try: base=get_api_base_url()
    except SettingsError as exc: raise AuthenticationError(str(exc)) from None
    data=json.dumps(payload).encode() if payload is not None else None
    headers={"Accept":"application/json","Content-Type":"application/json"}
    if token: headers["Authorization"]="Bearer "+token
    req=Request(base+path,data=data,headers=headers,method="POST" if payload is not None else "GET")
    try:
        with urlopen(req,timeout=12) as response: return json.loads(response.read().decode())
    except HTTPError as exc:
        try:
            payload=json.loads(exc.read().decode())
            detail=payload.get("detail") if isinstance(payload,dict) else None
        except (UnicodeDecodeError,json.JSONDecodeError,OSError): detail=None
        message=detail.strip() if isinstance(detail,str) and detail.strip() else None
        if exc.code==401 and message is None: message="Email or password is incorrect."
        raise AuthenticationError(message or "RentAI authentication service is unavailable.") from None
    except (URLError,TimeoutError,OSError): raise AuthenticationError("Could not reach RentAI. Check that the API is running.") from None
    except (UnicodeDecodeError,json.JSONDecodeError): raise AuthenticationError("RentAI returned an invalid response.") from None

def password_sign_in(email,password):
    if not password: raise AuthenticationError("Enter your email and password.")
    tokens=_request("/application/auth/sign-in",{"email":normalize_email(email),"password":password})
    if not isinstance(tokens,dict) or not isinstance(tokens.get("id_token"),str) or not tokens["id_token"].strip() or not isinstance(tokens.get("refresh_token"),str) or not tokens["refresh_token"].strip():
        raise AuthenticationError("RentAI returned an invalid authentication response.")
    return tokens
def password_sign_up(email,password,display_name,role="renter"):
    if len(password)<8: raise AuthenticationError("Choose a password with at least 8 characters.")
    return _request("/application/auth/sign-up",{"email":normalize_email(email),"password":password,"name":display_name,"role":role})
def provision_public_role(id_token,role): return {"role":role}
def fetch_verified_user(id_token): return _request("/application/auth/me",token=id_token)

def _clear_owner_profile_state(session):
    """Discard profile widget/cache state when the authenticated session changes."""
    for key in list(session):
        if str(key).startswith("owner_profile_"):
            session.pop(key, None)

def save_session(session,tokens,user):
    # Streamlit keeps keyed widget values across logout/login. Clear the cached
    # profile form so its values are initialized from the newly authenticated
    # Owner's API record, even when the same Owner signs in again.
    _clear_owner_profile_state(session)
    session["rentai_id_token"]=tokens["id_token"]; session["rentai_refresh_token"]=tokens["refresh_token"]
    session["rentai_expires_at"]=tokens.get("expires_in",2592000)+__import__("time").time()
    session["rentai_uid"]=user["uid"]; session["rentai_role"]=user["role"]; session["rentai_display_name"]=user.get("name",""); session["rentai_email"]=user.get("email","")
def clear_auth_session(session,*,clear_login_inputs=False,reset_page=True):
    _clear_owner_profile_state(session)
    for key in AUTH_STATE_KEYS: session.pop(key,None)
    if clear_login_inputs:
        for key in LOGIN_INPUT_KEYS: session.pop(key,None)
    if reset_page: session.pop("rentai_selected_role",None); session.pop("rentai_auth_view",None); session["page"]="Home"
def current_session_user(session,now=None):
    token=session.get("rentai_id_token")
    if not token: clear_auth_session(session,reset_page=False); return None
    try: user=fetch_verified_user(token)
    except AuthenticationError:
        clear_auth_session(session,reset_page=False); return None
    session.update({"rentai_uid":user["uid"],"rentai_role":user["role"],"rentai_display_name":user.get("name",""),"rentai_email":user.get("email","")})
    return user
