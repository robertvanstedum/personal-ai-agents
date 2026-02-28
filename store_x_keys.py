#!/usr/bin/env python3
"""One-time script to store X API credentials in macOS keychain."""
import keyring

print("Paste each value and press Enter\n")
consumer_key        = input("Consumer Key:        ")
consumer_secret     = input("Consumer Secret:     ")
access_token        = input("Access Token:        ")
access_token_secret = input("Access Token Secret: ")

keyring.set_password('x_api', 'consumer_key',        consumer_key.strip())
keyring.set_password('x_api', 'consumer_secret',     consumer_secret.strip())
keyring.set_password('x_api', 'access_token',        access_token.strip())
keyring.set_password('x_api', 'access_token_secret', access_token_secret.strip())

print("\nStored. Verifying...")
ck  = keyring.get_password('x_api', 'consumer_key')
cs  = keyring.get_password('x_api', 'consumer_secret')
at  = keyring.get_password('x_api', 'access_token')
ats = keyring.get_password('x_api', 'access_token_secret')
print("consumer_key:        ", ck[:8]  + "..." if ck  else "MISSING")
print("consumer_secret:     ", cs[:8]  + "..." if cs  else "MISSING")
print("access_token:        ", at[:8]  + "..." if at  else "MISSING")
print("access_token_secret: ", ats[:8] + "..." if ats else "MISSING")
