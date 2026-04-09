#!/bin/bash
# Quick test script for Marcel v2 endpoint

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
HOST="localhost"
PORT="7421"
USER="${MARCEL_USER:-shaun}"
TOKEN="${MARCEL_API_TOKEN:-test-token}"

# Help message
if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ -z "$1" ]; then
    echo "Usage: ./test_v2.sh 'your message here'"
    echo ""
    echo "Examples:"
    echo "  ./test_v2.sh 'Hello Marcel!'"
    echo "  ./test_v2.sh 'List files in current directory'"
    echo "  ./test_v2.sh 'Read the README.md file'"
    echo "  ./test_v2.sh 'Show git status'"
    echo ""
    echo "Environment variables:"
    echo "  MARCEL_USER       - User slug (default: shaun)"
    echo "  MARCEL_API_TOKEN  - API token (default: test-token)"
    echo ""
    echo "Make sure Marcel is running: make serve"
    exit 0
fi

MESSAGE="$*"

# Check if server is running
if ! curl -s "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
    echo -e "${RED}❌ Marcel server is not running!${NC}"
    echo -e "${YELLOW}Start it with: make serve${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Marcel server is running${NC}"
echo -e "${BLUE}📤 Message: ${MESSAGE}${NC}"
echo ""

# Create temporary Python script
TEMP_SCRIPT=$(mktemp /tmp/marcel_test_XXXXXX.py)

cat > "$TEMP_SCRIPT" << 'EOFPYTHON'
import asyncio
import json
import sys
import websockets

async def send_message(host, port, user, token, message):
    url = f'ws://{host}:{port}/v2/chat'

    try:
        async with websockets.connect(url) as ws:
            # Send message
            await ws.send(json.dumps({
                'token': token,
                'user': user,
                'text': message,
                'channel': 'cli',
            }))

            # Receive responses
            print("\033[0;32m💬 Response:\033[0m\n")

            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                    data = json.loads(msg)
                    msg_type = data.get('type')

                    if msg_type == 'started':
                        conv_id = data.get('conversation')
                        print(f"\033[0;36m🆕 Conversation: {conv_id}\033[0m\n")

                    elif msg_type == 'text_message_start':
                        pass  # Just start collecting text

                    elif msg_type == 'token':
                        print(data.get('text', ''), end='', flush=True)

                    elif msg_type == 'text_message_end':
                        print()  # Newline after text block

                    elif msg_type == 'tool_call_start':
                        tool = data.get('tool_name', 'unknown')
                        print(f"\n\033[1;33m🔧 Tool: {tool}\033[0m", end='', flush=True)

                    elif msg_type == 'tool_call_result':
                        is_error = data.get('is_error', False)
                        if is_error:
                            print(" \033[0;31m❌ Error\033[0m")
                        else:
                            print(" \033[0;32m✓\033[0m")

                    elif msg_type == 'done':
                        cost = data.get('cost_usd')
                        if cost:
                            print(f"\n\n\033[0;32m✅ Done (${cost:.4f})\033[0m")
                        else:
                            print("\n\n\033[0;32m✅ Done\033[0m")
                        break

                    elif msg_type == 'error':
                        error_msg = data.get('message', 'Unknown error')
                        print(f"\n\n\033[0;31m❌ Error: {error_msg}\033[0m")
                        break

                except asyncio.TimeoutError:
                    print("\n\n\033[0;31m❌ Timeout waiting for response\033[0m")
                    break

    except websockets.exceptions.WebSocketException as e:
        print(f"\033[0;31m❌ WebSocket error: {e}\033[0m")
        return False
    except Exception as e:
        print(f"\033[0;31m❌ Error: {e}\033[0m")
        return False

    return True

if __name__ == '__main__':
    host = sys.argv[1]
    port = sys.argv[2]
    user = sys.argv[3]
    token = sys.argv[4]
    message = sys.argv[5]

    success = asyncio.run(send_message(host, port, user, token, message))
    sys.exit(0 if success else 1)
EOFPYTHON

# Run the test
uv run python "$TEMP_SCRIPT" "$HOST" "$PORT" "$USER" "$TOKEN" "$MESSAGE"
EXIT_CODE=$?

# Cleanup
rm -f "$TEMP_SCRIPT"

exit $EXIT_CODE
