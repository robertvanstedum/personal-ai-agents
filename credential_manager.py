#!/usr/bin/env python3
"""
Credential Manager - Secure credential storage and retrieval

Security layers:
1. macOS Keychain (preferred) - OS-level encryption
2. .env file (fallback) - gitignored, local only
3. Never store in code or git

Usage:
    from credential_manager import get_credentials
    
    creds = get_credentials("geopolitical_futures")
    username = creds["username"]
    password = creds["password"]
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional

def get_credentials(service: str) -> Dict[str, str]:
    """
    Get credentials for a service (keychain first, .env fallback)
    
    Args:
        service: Service name (e.g., "geopolitical_futures")
    
    Returns:
        Dict with "username" and "password" keys
    
    Raises:
        ValueError: If credentials not found
    """
    # Try keychain first
    try:
        import keyring
        username = keyring.get_password(service, "username")
        password = keyring.get_password(service, "password")
        
        if username and password:
            print(f"✅ Loaded {service} credentials from keychain")
            return {"username": username, "password": password}
    except ImportError:
        print("⚠️  keyring module not installed (pip install keyring)")
    except Exception as e:
        print(f"⚠️  Keychain access failed: {e}")
    
    # Fall back to .env file
    try:
        from dotenv import load_dotenv
        
        # Find .env in project root
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            
            username_key = f"{service.upper()}_USERNAME"
            password_key = f"{service.upper()}_PASSWORD"
            
            username = os.getenv(username_key)
            password = os.getenv(password_key)
            
            if username and password:
                print(f"✅ Loaded {service} credentials from .env")
                return {"username": username, "password": password}
        else:
            print(f"⚠️  .env file not found at {env_path}")
    except ImportError:
        print("⚠️  python-dotenv module not installed (pip install python-dotenv)")
    except Exception as e:
        print(f"⚠️  .env loading failed: {e}")
    
    # No credentials found
    raise ValueError(
        f"Credentials for '{service}' not found!\n\n"
        f"Setup options:\n"
        f"1. Store in keychain: python credential_manager.py store {service}\n"
        f"2. Create .env file with {service.upper()}_USERNAME and {service.upper()}_PASSWORD"
    )

def store_credentials(service: str, username: str, password: str) -> bool:
    """
    Store credentials in macOS Keychain
    
    Args:
        service: Service name
        username: Username
        password: Password
    
    Returns:
        True if successful
    """
    try:
        import keyring
        keyring.set_password(service, "username", username)
        keyring.set_password(service, "password", password)
        print(f"✅ Stored {service} credentials in keychain")
        return True
    except ImportError:
        print("❌ keyring module not installed (pip install keyring)")
        return False
    except Exception as e:
        print(f"❌ Failed to store credentials: {e}")
        return False

def delete_credentials(service: str) -> bool:
    """Delete credentials from keychain"""
    try:
        import keyring
        keyring.delete_password(service, "username")
        keyring.delete_password(service, "password")
        print(f"✅ Deleted {service} credentials from keychain")
        return True
    except Exception as e:
        print(f"⚠️  Could not delete: {e}")
        return False

# CLI interface for manual credential management
if __name__ == "__main__":
    import getpass
    
    if len(sys.argv) < 3:
        print("Usage:")
        print("  Store:   python credential_manager.py store <service>")
        print("  Test:    python credential_manager.py test <service>")
        print("  Delete:  python credential_manager.py delete <service>")
        print()
        print("Example:")
        print("  python credential_manager.py store geopolitical_futures")
        sys.exit(1)
    
    command = sys.argv[1]
    service = sys.argv[2]
    
    if command == "store":
        print(f"Storing credentials for: {service}")
        username = input("Username: ")
        password = getpass.getpass("Password: ")
        store_credentials(service, username, password)
        
    elif command == "test":
        try:
            creds = get_credentials(service)
            print(f"✅ Found credentials for {service}")
            print(f"   Username: {creds['username']}")
            print(f"   Password: {'*' * len(creds['password'])}")
        except ValueError as e:
            print(f"❌ {e}")
    
    elif command == "delete":
        confirm = input(f"Delete {service} credentials? (yes/no): ")
        if confirm.lower() == "yes":
            delete_credentials(service)
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
