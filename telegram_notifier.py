import requests
import os
import sys

# Try to import config for local testing, but don't fail if it doesn't exist (for Railway)
try:
    import config
except ImportError:
    config = None

class TelegramNotifier:
    def __init__(self):
        # 1. Try getting variables from Railway/Environment first
        # 2. If not found, try getting them from config.py
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN') or (config.TELEGRAM_BOT_TOKEN if config else None)
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID') or (config.TELEGRAM_CHAT_ID if config else None)
        
        # Safety check: Print warning if tokens are missing
        if not self.bot_token or not self.chat_id:
            print("WARNING: Telegram Bot Token or Chat ID is missing! Messages will not be sent.")
            self.base_url = None
        else:
            self.base_url = f'https://api.telegram.org/bot{self.bot_token}/sendMessage'

    def send_message(self, message):
        """
        Sends a text message to the configured Telegram chat.
        """
        if not self.base_url:
            print("Telegram not configured. Skipping message.")
            return

        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'Markdown' 
        }
        try:
            response = requests.post(self.base_url, data=payload, timeout=5)
            response.raise_for_status() 
            print(f"Telegram message sent successfully.")
        except requests.exceptions.RequestException as e:
            print(f"Error sending Telegram message: {e}")

    def send_trade_signal(self, trade_type, entry_price, stop_loss, take_profit, symbol):
        """
        Formats and sends a trade signal to the Telegram chat.
        """
        signal_message = (
            f"*NEW {trade_type.upper()} SIGNAL for {symbol}!*\n\n"
            f"*Entry Price:* {entry_price:.5f}\n"
            f"*Stop Loss:* {stop_loss:.5f}\n"
            f"*Take Profit:* {take_profit:.5f}\n"
            f"_Always manage your risk!_"
        )
        self.send_message(signal_message)
