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
        
        elif parsed.path == '/deepdive':
            # Trigger deep dive analysis
            params = urllib.parse.parse_qs(parsed.query)
            rank = params.get('rank', [''])[0]
            interest = params.get('interest', [''])[0]
            focus = params.get('focus', [''])[0]
            
            if not rank:
                self.send_error(400, "Missing rank")
                return
            
            if not interest:
                self.send_error(400, "Missing interest")
                return
            
            print(f"\n🔍 Deep dive requested for article #{rank}")
            print(f"   Interest: {interest[:100]}...")
            if focus:
                print(f"   Focus: {focus[:100]}...")
            result = self.trigger_deepdive(rank, interest, focus)
            
            # Send response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            self.wfile.write(json.dumps(result).encode())
        
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
    
    def trigger_deepdive(self, rank, interest, focus=''):
        """Trigger deep dive analysis for an article"""
        try:
            from datetime import datetime
            
            # Use curator_feedback.py bookmark command (which triggers deep dive)
            # Convert rank to date-rank format: YYYY-MM-DD-N
            today = datetime.now().strftime("%Y-%m-%d")
            date_rank = f"{today}-{rank}"
            
            cmd = ['python', 'curator_feedback.py', 'bookmark', date_rank]
            
            # Run in virtual environment
            venv_python = Path(__file__).parent / 'venv' / 'bin' / 'python'
            if venv_python.exists():
                cmd[0] = str(venv_python)
            
            # Input: interest reason + optional focus areas (matches interactive prompts)
            input_text = interest + "\n" + focus + "\n"
            
            result = subprocess.run(
                cmd,
                input=input_text.encode(),
                capture_output=True,
                cwd=Path(__file__).parent,
                timeout=60  # Deep dives take longer
            )
            
            if result.returncode == 0:
                output = result.stdout.decode()
                print(f"Deep dive output:\n{output}")
                
                # Extract file path - look for MD path first, then convert to HTML
                import re
                md_match = re.search(r'Saved to: (interests/[^\s]+\.md)', output)
                
                if md_match:
                    md_path = md_match.group(1)
                    html_path = md_path.replace('.md', '.html')
                    
                    # Verify HTML file exists
                    html_full_path = Path(__file__).parent / html_path
                    if html_full_path.exists():
                        print(f"✅ Deep dive HTML found: {html_path}")
                        return {
                            'success': True,
                            'message': 'Deep dive complete!',
                            'html_path': html_path
                        }
                    else:
                        print(f"⚠️  HTML not found at {html_full_path}")
                        return {
                            'success': True,
                            'message': f'Deep dive saved as {md_path}'
                        }
                else:
                    print(f"⚠️  Could not extract path from output")
                    return {
                        'success': True,
                        'message': 'Deep dive complete (check terminal)'
                    }
            else:
                error = result.stderr.decode()
                print(f"❌ Deep dive error: {error}")
                return {
                    'success': False,
                    'message': f'Failed: {error[:100]}'
                }
        
        except subprocess.TimeoutExpired:
            return {'success': False, 'message': 'Timeout (>60s)'}
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
