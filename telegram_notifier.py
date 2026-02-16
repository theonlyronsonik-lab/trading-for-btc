import requests
import os

try:
    import config
except ImportError:
    config = None

class TelegramNotifier:
    def __init__(self):
        raw_token = os.getenv('TELEGRAM_BOT_TOKEN') or (config.TELEGRAM_BOT_TOKEN if config else None)
        raw_chat_id = os.getenv('TELEGRAM_CHAT_ID') or (config.TELEGRAM_CHAT_ID if config else None)

        self.bot_token = str(raw_token).strip() if raw_token else None
        self.chat_id = str(raw_chat_id).strip() if raw_chat_id else None
        self.last_update_id = 0
        
        if not self.bot_token or not self.chat_id:
            print("⚠️ TELEGRAM ERROR: Credentials missing.")
            self.base_url = None
        else:
            self.base_url = f'https://api.telegram.org/bot{self.bot_token}'

    def send_message(self, message):
        if not self.base_url: return
        url = f"{self.base_url}/sendMessage"
        payload = {'chat_id': self.chat_id, 'text': message, 'parse_mode': 'Markdown'}
        try:
            requests.post(url, data=payload, timeout=5).raise_for_status()
        except Exception as e:
            print(f"Telegram Send Failed: {e}")

    def check_for_commands(self, open_trades):
        """Checks for /status command from the user."""
        if not self.base_url: return
        
        url = f"{self.base_url}/getUpdates?offset={self.last_update_id + 1}&timeout=1"
        try:
            response = requests.get(url, timeout=5).json()
            for update in response.get("result", []):
                self.last_update_id = update["update_id"]
                message = update.get("message", {})
                text = message.get("text", "")
                user_id = str(message.get("from", {}).get("id", ""))

                # Security check: Only respond to YOUR chat ID
                if text == "/status" and user_id == self.chat_id:
                    self._send_status_report(open_trades)
        except Exception as e:
            print(f"Telegram Poll Error: {e}")

    def _send_status_report(self, open_trades):
        report = "📊 *Current Bot Status*\n"
        active = [s for s, t in open_trades.items() if t is not None]
        
        if not active:
            report += "No active trades. Scanning market..."
        else:
            for s in active:
                t = open_trades[s]
                report += f"🔹 *{s}*: {t['Type']} @ {t['EntryPrice']:.5f}\n"
        
        self.send_message(report)

    def send_trade_signal(self, trade_type, entry_price, stop_loss, take_profit, symbol):
        msg = (f"🚀 *NEW {trade_type.upper()} SIGNAL*\n"
               f"Symbol: #{symbol.replace('/', '_')}\n"
               f"Entry: {entry_price:.5f}\n"
               f"SL: {stop_loss:.5f} | TP: {take_profit:.5f}")
        self.send_message(msg)
        
