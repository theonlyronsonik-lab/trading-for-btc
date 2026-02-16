import requests
import os

try:
    import config
except ImportError:
    config = None

class TelegramNotifier:
    def __init__(self):
        # Read from Railway Variables, fallback to config.py for local
        raw_token = os.getenv('TELEGRAM_BOT_TOKEN') or (config.TELEGRAM_BOT_TOKEN if config else None)
        raw_chat_id = os.getenv('TELEGRAM_CHAT_ID') or (config.TELEGRAM_CHAT_ID if config else None)

        # .strip() removes hidden spaces or newlines that cause 404 errors
        self.bot_token = str(raw_token).strip() if raw_token else None
        self.chat_id = str(raw_chat_id).strip() if raw_chat_id else None
        
        if not self.bot_token or not self.chat_id:
            print("⚠️ TELEGRAM ERROR: Credentials missing.")
            self.base_url = None
        else:
            self.base_url = f'https://api.telegram.org/bot{self.bot_token}/sendMessage'

    def send_message(self, message):
        if not self.base_url: return
        payload = {'chat_id': self.chat_id, 'text': message, 'parse_mode': 'Markdown'}
        try:
            response = requests.post(self.base_url, data=payload, timeout=5)
            response.raise_for_status()
        except Exception as e:
            print(f"Telegram Failed: {e}")

    def send_trade_signal(self, trade_type, entry_price, stop_loss, take_profit, symbol):
        msg = (f"🚀 *NEW {trade_type.upper()} SIGNAL*\n"
               f"Symbol: #{symbol.replace('/', '_')}\n"
               f"Entry: {entry_price:.5f}\n"
               f"SL: {stop_loss:.5f}\n"
               f"TP: {take_profit:.5f}")
        self.send_message(msg)
        
