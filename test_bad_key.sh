#!/bin/bash
# Test negative case: bad API key

set -e

cd ~/Projects/personal-ai-agents
source venv/bin/activate

echo "=========================================="
echo "NEGATIVE TEST: Bad API Key"
echo "=========================================="
echo ""

# Save current key
echo "1. Saving current API key..."
GOOD_KEY=$(python -c "import keyring; print(keyring.get_password('anthropic', 'api_key'))")
echo "   ✓ Current key saved (starts with: ${GOOD_KEY:0:20}...)"
echo ""

# Set dummy key
echo "2. Setting dummy API key in keychain..."
python -c "import keyring; keyring.set_password('anthropic', 'api_key', 'sk-ant-dummy-bad-key-for-testing')"
echo "   ✓ Dummy key set: sk-ant-dummy-bad-key-for-testing"
echo ""

# Test with bad key (should fail)
echo "3. Running curator with bad API key..."
echo "   (This should fail with authentication error)"
echo ""

python curator_rss_v2.py --mode=ai 2>&1 || echo "   ✓ Failed as expected (exit code $?)"
echo ""

# Restore good key
echo "4. Restoring good API key..."
python -c "import keyring; keyring.set_password('anthropic', 'api_key', '$GOOD_KEY')"
echo "   ✓ Good key restored"
echo ""

# Verify restoration
echo "5. Verifying key restoration..."
RESTORED_KEY=$(python -c "import keyring; print(keyring.get_password('anthropic', 'api_key'))")
if [ "$GOOD_KEY" = "$RESTORED_KEY" ]; then
    echo "   ✓ Key successfully restored"
else
    echo "   ✗ Warning: Key restoration may have failed!"
fi
echo ""

echo "=========================================="
echo "TEST COMPLETE"
echo "=========================================="
