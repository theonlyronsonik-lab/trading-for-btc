# %%writefile telegram_notifier.py
import requests
# In a deployed environment, you would use environment variables:
# import os
# TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
# TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# For local testing, ensure config.py is present or define them here temporarily:
import config # Assumes config.py is in the same directory

class TelegramNotifier:
    def __init__(self):
        # Use environment variables for deployment, fallback to config.py for local testing/development
        self.bot_token = config.TELEGRAM_BOT_TOKEN # os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = config.TELEGRAM_CHAT_ID # os.getenv('TELEGRAM_CHAT_ID')
        self.base_url = f'https://api.telegram.org/bot{self.bot_token}/sendMessage'

    def send_message(self, message):
        """
        Sends a text message to the configured Telegram chat.
        """
        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'Markdown' # Optional: allows for bold, italics, etc. in messages
        }
        try:
            response = requests.post(self.base_url, data=payload, timeout=5)
            response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
            print(f"Telegram message sent successfully: {message[:50]}...")
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

