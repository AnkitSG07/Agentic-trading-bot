"""
Telegram Notification Service
Sends real-time trade alerts, P&L updates, and system notifications.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger("notifications.telegram")


class TelegramNotifier:
    """
    Send trading alerts via Telegram Bot API.
    
    Setup:
    1. Create bot via @BotFather
    2. Get BOT_TOKEN and CHAT_ID
    3. Set in .env file
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.enabled = bool(bot_token and chat_id)

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                )
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    # ── Trade Alerts ─────────────────────────────────────────────────────────

    async def trade_entry(
        self, symbol: str, side: str, quantity: int,
        price: Decimal, strategy: str, confidence: float,
        sl: Optional[Decimal] = None, target: Optional[Decimal] = None,
    ) -> None:
        emoji = "🟢" if side == "BUY" else "🔴"
        msg = (
            f"{emoji} <b>TRADE ENTRY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{symbol}</b> | {side}\n"
            f"📦 Qty: {quantity} @ ₹{price:,.2f}\n"
            f"🎯 Strategy: {strategy}\n"
            f"📊 Confidence: {confidence:.0%}\n"
        )
        if sl:
            msg += f"🛡️ SL: ₹{sl:,.2f}\n"
        if target:
            msg += f"✅ Target: ₹{target:,.2f}\n"
        msg += f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        await self.send(msg)

    async def trade_exit(
        self, symbol: str, side: str, quantity: int,
        entry_price: Decimal, exit_price: Decimal,
        pnl: Decimal, reason: str = "TARGET",
    ) -> None:
        pnl_emoji = "✅" if pnl > 0 else "❌"
        pnl_arrow = "📈" if pnl > 0 else "📉"
        msg = (
            f"{pnl_emoji} <b>TRADE EXIT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{symbol}</b> | {reason}\n"
            f"📦 Qty: {quantity}\n"
            f"💰 Entry: ₹{entry_price:,.2f} → Exit: ₹{exit_price:,.2f}\n"
            f"{pnl_arrow} P&L: ₹{pnl:+,.2f}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send(msg)

    async def stop_loss_hit(self, symbol: str, price: Decimal, loss: Decimal) -> None:
        msg = (
            f"🛑 <b>STOP LOSS HIT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{symbol}</b>\n"
            f"💥 SL triggered @ ₹{price:,.2f}\n"
            f"📉 Loss: ₹{loss:,.2f}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send(msg)

    async def kill_switch_alert(self, reason: str, daily_pnl: float) -> None:
        msg = (
            f"🚨 <b>KILL SWITCH TRIGGERED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Reason: {reason}\n"
            f"📉 Daily P&L: {daily_pnl:+.2f}%\n"
            f"🛑 All trading STOPPED\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send(msg)

    async def daily_summary(
        self,
        total_pnl: float,
        pnl_pct: float,
        total_trades: int,
        win_rate: float,
        drawdown: float,
    ) -> None:
        emoji = "📈" if total_pnl >= 0 else "📉"
        msg = (
            f"{emoji} <b>DAILY SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 P&L: ₹{total_pnl:+,.0f} ({pnl_pct:+.2f}%)\n"
            f"📊 Trades: {total_trades} | Win Rate: {win_rate:.1f}%\n"
            f"📉 Max Drawdown: {drawdown:.2f}%\n"
            f"📅 {datetime.now().strftime('%d %b %Y')}"
        )
        await self.send(msg)

    async def system_alert(self, level: str, message: str) -> None:
        emoji_map = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🔥"}
        emoji = emoji_map.get(level, "📢")
        msg = (
            f"{emoji} <b>SYSTEM {level}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{message}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send(msg)
