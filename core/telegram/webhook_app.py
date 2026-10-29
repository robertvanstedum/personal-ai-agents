"""Construct the legacy Telegram webhook without credentials or server startup."""
import hmac
import threading

from flask import Flask, jsonify, request

from core.telegram.owner_policy import admits_callback, admits_message


def create_webhook_app(*, secret, resolve_owner, callback, voice, command, text,
                       start_thread=None):
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError("Webhook mode requires a configured secret")
    app = Flask(__name__)

    def threaded(target, message):
        threading.Thread(target=target, args=(message,), daemon=True).start()

    start_thread = start_thread or threaded

    @app.post("/webhook")
    def webhook():
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied.encode(), secret.encode()):
            return jsonify(error="Unauthorized"), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Invalid update"), 400
        try:
            owner = resolve_owner()
            if "callback_query" in body:
                query = body["callback_query"]
                if not admits_callback(query, owner):
                    return jsonify(error="Unauthorized"), 403
                callback(query)
            elif "message" in body:
                message = body["message"]
                if not admits_message(message, owner):
                    return jsonify(error="Unauthorized"), 403
                if "voice" in message:
                    start_thread(voice, message)
                elif isinstance(message.get("text"), str):
                    (command if message["text"].startswith("/") else text)(message)
            return jsonify(ok=True)
        except Exception:
            app.logger.warning("Telegram webhook dispatch failed")
            return jsonify(error="Dispatch failed"), 500

    @app.get("/health")
    def health():
        return jsonify(status="ok", mode="webhook")

    return app
