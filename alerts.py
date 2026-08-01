"""Telegram delivery (raw Bot API over httpx — no extra dependency needed)."""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import config
from pricing import Deal
from wallapop_client import Item

log = logging.getLogger("alerts")

API = "https://api.telegram.org/bot{token}/{method}"
MADRID = ZoneInfo("Europe/Madrid")

# Telegram truncates photo captions at 1024 characters.
CAPTION_LIMIT = 1024
DESC_CHARS = 200


def _eur(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}€".replace(",", ".")


def _signed(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:+,.0f}€".replace(",", ".")


def build_caption(
    item: Item,
    deal: Deal,
    kind: str,
    previous_price: float | None,
    model_display: str | None,
) -> str:
    """The §9 alert body, as Telegram HTML."""
    if kind == "price_drop":
        head = "🔻 <b>PRICE DROP</b>"
    elif deal.priced:
        head = "🟢 <b>DEAL</b>"
    else:
        head = "🔎 <b>MATCH</b>"
    if model_display:
        head += f" · {html.escape(model_display)}"

    lines = [head]

    price_line = f"💶 <b>{_eur(item.price)}</b>"
    if previous_price and item.price is not None and previous_price > item.price:
        price_line += f"   <s>{_eur(previous_price)}</s>"
    lines.append(price_line)

    if deal.priced:
        margin = (
            f"📊 ref {_eur(deal.ref_price)} · techo {_eur(deal.ceiling_shipped)} · "
            f"neto {_signed(deal.net_shipped)} (envío) / {_signed(deal.net_in_person)} (mano)"
        )
        if deal.is_seed:
            margin += "  ⚠️ <i>seed</i>"
        elif deal.n_comps:
            margin += f"  <i>n={deal.n_comps}</i>"
        lines.append(margin)

    where = []
    if item.location:
        where.append(html.escape(str(item.location)))
    if item.distance_km is not None:
        try:
            where.append(f"{float(item.distance_km):.0f} km")
        except (TypeError, ValueError):
            pass
    where.append("envío disponible" if item.shipping else "solo en mano")
    lines.append("📍 " + " · ".join(where))

    lines.append("🕒 " + datetime.now(MADRID).strftime("%Y-%m-%d %H:%M"))
    lines.append("")
    lines.append(f"<b>{html.escape(item.title[:150])}</b>")

    if item.description:
        desc = " ".join(item.description.split())[:DESC_CHARS]
        lines.append(html.escape(desc))

    lines.append("")
    lines.append(f"🔗 {html.escape(item.web_url)}")

    caption = "\n".join(lines)
    if len(caption) > CAPTION_LIMIT:
        overflow = len(caption) - CAPTION_LIMIT + 3
        # Trim the description, never the link or the numbers.
        if item.description and overflow < DESC_CHARS:
            # lines[-3] is the description: [..., desc, "", link]
            lines[-3] = lines[-3][: max(0, len(lines[-3]) - overflow)] + "…"
            caption = "\n".join(lines)
        else:
            caption = caption[: CAPTION_LIMIT - 1] + "…"
    return caption


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token or config.TELEGRAM_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        self._client = httpx.Client(timeout=config.HTTP_TIMEOUT)
        self._last_send = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Telegram":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_send
        if elapsed < config.TELEGRAM_RATE_DELAY:
            time.sleep(config.TELEGRAM_RATE_DELAY - elapsed)
        self._last_send = time.monotonic()

    def _post(self, method: str, payload: dict) -> bool:
        url = API.format(token=self.token, method=method)
        try:
            resp = self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            log.error("Telegram %s failed: %s", method, exc)
            return False
        if resp.status_code == 200:
            return True
        log.error("Telegram %s -> %s: %s", method, resp.status_code, resp.text[:300])
        return False

    def send_alert(
        self,
        item: Item,
        deal: Deal,
        kind: str,
        previous_price: float | None = None,
        model_display: str | None = None,
    ) -> bool:
        caption = build_caption(item, deal, kind, previous_price, model_display)

        if config.DRY_RUN:
            log.info("[DRY RUN] would send:\n%s", caption)
            return True

        self._throttle()
        if item.image_url:
            sent = self._post(
                "sendPhoto",
                {
                    "chat_id": self.chat_id,
                    "photo": item.image_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
            )
            if sent:
                return True
            # A rejected image URL must not cost us the alert.
            log.warning("sendPhoto failed for %s — falling back to text", item.item_id)

        return self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )

    def send_error(self, text: str) -> None:
        """Surface failures instead of dying silently in a cron runner."""
        if config.DRY_RUN:
            log.info("[DRY RUN] error ping: %s", text)
            return
        self._throttle()
        self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": f"⚠️ <b>wallapop-bot</b>\n<pre>{html.escape(text[:1500])}</pre>",
                "parse_mode": "HTML",
            },
        )
