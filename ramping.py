# create_onramp_link.py
import os, time, jwt, requests
from dotenv import load_dotenv
from cdp.auth.utils.jwt import generate_jwt, JwtOptions
load_dotenv()

API_ID = os.getenv("COINBASE_API_ID")
PRIVATE_KEY = os.getenv("COINBASE_API_PRIVATE_KEY").replace("\\n", "\n")

def create_onramp_session():
    # 1️⃣ Create JWT for authentication
    payload = {"sub": API_ID, "iat": int(time.time())}
    token = generate_jwt(JwtOptions(
    api_key_id=API_ID,
    api_key_secret=PRIVATE_KEY,
    request_method="POST",
    request_host="api.cdp.coinbase.com",
    request_path="/platform/v2/onramp/sessions",

    expires_in=120  # optional (defaults to 120 seconds)
    ))
    print(token)
    url = "https://api.cdp.coinbase.com/platform/v2/onramp/sessions"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    body = {
    "purchaseCurrency": "USDC",
    "destinationNetwork": "solana",
    "destinationAddress": "8hwTWjptyobR8godtYv2iG6YVnHBixhZGxnrbd4pHfCd",
    "paymentAmount": "5",
    "paymentCurrency": "EUR",
    }

    # 3️⃣ Send API call
    response = requests.post(url, json=body, headers=headers)
    data = response.json()
    print(data)


if __name__ == "__main__":
    create_onramp_session()
