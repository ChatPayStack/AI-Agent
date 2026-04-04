import os
import time
import jwt
import requests
from dotenv import load_dotenv
load_dotenv()

api_key_id = os.getenv("COINBASE_API_ID")
api_key_secret = os.getenv("COINBASE_API_PRIVATE_KEY").replace("\\n", "\n")
BUSINESS_ADDRESS = os.getenv("BUSINESS_ADDRESS")

def generate_coinbase_jwt(method, host, path):
    
    now = int(time.time())

    payload = {
        "sub": api_key_id,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uri": f"{method} {host}{path}"
    }

    token = token = jwt.encode(
        payload,
        api_key_secret,
        algorithm="ES256",
        headers={
            "kid": api_key_id
        }
    )
    return token


def create_onramp_session(payment_amount, thread_id, payment_id, wallet_address):
    host = "api.cdp.coinbase.com"
    path = "/platform/v2/onramp/sessions"
    method = "POST"

    token = generate_coinbase_jwt(method, host, path)
    url = f"https://{host}{path}"

    payload = {
        "purchaseCurrency": "EURC",
        "destinationNetwork": "solana",
        "destinationAddress": wallet_address,  # ✅ user wallet
        "paymentAmount": str(payment_amount),
        "paymentCurrency": "EUR",
        "paymentMethod": "CARD",
        "country": "IE",
        "subdivision": "L",
        "redirectUrl": "https://t.me/AmfaCosmeticsBot",
        "partnerUserRef": f"{thread_id}:{payment_id}"
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)
    print(response.json())
    return response.json()
'''
if __name__ == "__main__":
    import uuid

    # Fake test values
    test_amount = "10"  # €10
    test_thread_id = "test-user"
    test_payment_id = str(uuid.uuid4())

    test_wallet_address = "6bGcrWXmdjsy2eRBntQDyY49AnFdbtBpKkZe5Zbyutf3"

    result = create_onramp_session(
        test_amount,
        test_thread_id,
        test_payment_id,
        test_wallet_address
    )

    print("\nFINAL RESULT:")
    print(result)
'''