#!/usr/bin/env python3
"""
curator_server.py - Simple HTTP server for handling feedback from web UI

Usage:
    python curator_server.py
    
Then open curator_latest.html in browser
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import urllib.parse
from pathlib import Path

class FeedbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle feedback requests"""
        # Parse URL
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == '/feedback':
            # Extract query parameters
            params = urllib.parse.parse_qs(parsed.query)
            action = params.get('action', [''])[0]  # like, dislike, save
            rank = params.get('rank', [''])[0]
            
            if not action or not rank:
                self.send_error(400, "Missing action or rank")
                return
            
            print(f"\n📥 Feedback received: {action} for article #{rank}")
            
            # For 'save', no prompt needed - just mark it
            if action == 'save':
                result = self.record_feedback(action, rank, "Saved from web UI")
            else:
                # For like/dislike, we'll use a default message for now
                # (Can enhance to show a prompt modal later)
                result = self.record_feedback(action, rank, f"{action}d from web UI - see article for details")
            
            # Send response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                'success': result['success'],
                'message': result['message']
            }
            self.wfile.write(json.dumps(response).encode())
        
        else:
            self.send_error(404, "Not found")
    
    def record_feedback(self, action, rank, reason):
        """Call curator_feedback.py to record feedback"""
        try:
            # Build command based on action
            if action == 'like':
                cmd = ['python', 'curator_feedback.py', 'like', rank]
            elif action == 'dislike':
                cmd = ['python', 'curator_feedback.py', 'dislike', rank]
            elif action == 'save':
                cmd = ['python', 'curator_feedback.py', 'save', rank]
            else:
                return {'success': False, 'message': f'Unknown action: {action}'}
            
            # Run in virtual environment
            venv_python = Path(__file__).parent / 'venv' / 'bin' / 'python'
            if venv_python.exists():
                cmd[0] = str(venv_python)
            
            # Execute with auto-response
            result = subprocess.run(
                cmd,
                input=reason.encode(),
                capture_output=True,
                cwd=Path(__file__).parent,
                timeout=30
            )
            
            if result.returncode == 0:
                print(f"✅ Feedback recorded successfully")
                return {
                    'success': True,
                    'message': f'Article #{rank} {action}d!'
                }
            else:
                error = result.stderr.decode()
                print(f"❌ Error: {error}")
                return {
                    'success': False,
                    'message': f'Error recording feedback: {error[:100]}'
                }
        
        except subprocess.TimeoutExpired:
            return {'success': False, 'message': 'Timeout waiting for feedback script'}
        except Exception as e:
            return {'success': False, 'message': f'Error: {str(e)}'}
    
    def log_message(self, format, *args):
        """Suppress default logging (we'll do our own)"""
        pass

def main():
    PORT = 8765
    
    print(f"""
🌐 Curator Feedback Server Starting...

📍 Server running at: http://localhost:{PORT}
📄 Open curator_latest.html in your browser
👍 Click feedback buttons to record preferences

Press Ctrl+C to stop
""")
    
    server = HTTPServer(('localhost', PORT), FeedbackHandler)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped")
        server.shutdown()

if __name__ == '__main__':
    main()
