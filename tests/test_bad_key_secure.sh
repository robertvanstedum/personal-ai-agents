#!/bin/bash
# Test negative case: bad API key (SECURE VERSION)
# Doesn't store real key in variables or temp files

set -e

cd ~/Projects/personal-ai-agents
source venv/bin/activate

echo "=========================================="
echo "NEGATIVE TEST: Bad API Key (Secure)"
echo "=========================================="
echo ""

# Verify we have a good key first
echo "1. Checking for existing API key..."
python -c "import keyring; key = keyring.get_password('anthropic', 'api_key'); print('   ✓ Found key starting with:', key[:20] + '...') if key else exit('   ✗ No key found')"
echo ""

echo "⚠️  WARNING: This test will temporarily replace your API key."
echo "   You'll need to manually restore it after the test."
echo ""
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Test cancelled."
    exit 0
fi

echo ""
echo "2. Please save your API key somewhere safe (outside this script):"
echo "   Option A: Leave it in Keychain Access app (you can search for 'anthropic')"
echo "   Option B: Copy from: https://console.anthropic.com/settings/keys"
echo ""
read -p "Press Enter when you've secured your key..."
echo ""

# Set dummy key
echo "3. Setting dummy API key in keychain..."
python -c "import keyring; keyring.set_password('anthropic', 'api_key', 'sk-ant-dummy-bad-key-for-testing')"
echo "   ✓ Dummy key set: sk-ant-dummy-bad-key-for-testing"
echo ""

# Test with bad key (should fail)
echo "4. Running curator with bad API key..."
echo "   (This should fail with authentication error)"
echo ""

python curator_rss_v2.py --mode=ai 2>&1 || echo "   ✓ Failed as expected (exit code $?)"
echo ""

# Manual restoration
echo "=========================================="
echo "TEST COMPLETE"
echo "=========================================="
echo ""
echo "TO RESTORE YOUR API KEY:"
echo ""
echo "Option 1 (Recommended): Run the key storage script"
echo "  python store_api_key.py"
echo ""
echo "Option 2: Set it manually via Python"
echo "  python -c 'import keyring; keyring.set_password(\"anthropic\", \"api_key\", \"YOUR_KEY_HERE\")'"
echo ""
echo "Option 3: Check Keychain Access app"
echo "  Open Keychain Access → Search 'anthropic' → Restore from there"
echo ""
