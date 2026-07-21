#!/usr/bin/env python3
"""One-time script to store X OAuth 2.0 credentials in macOS keychain."""
import keyring

print("Paste each value and press Enter\n")
client_id     = input("OAuth 2.0 Client ID:     ")
client_secret = input("OAuth 2.0 Client Secret: ")

keyring.set_password('x_oauth2', 'client_id',     client_id.strip())
keyring.set_password('x_oauth2', 'client_secret', client_secret.strip())

print("\nStored. Verifying...")
cid = keyring.get_password('x_oauth2', 'client_id')
cs  = keyring.get_password('x_oauth2', 'client_secret')
print("client_id:     ", cid[:8] + "..." if cid else "MISSING")
print("client_secret: ", cs[:8]  + "..." if cs  else "MISSING")
