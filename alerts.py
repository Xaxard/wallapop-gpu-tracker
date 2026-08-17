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

# The API's condition enum, in the words Wallapop itself shows the seller.
# Printing the raw value ("as_good_as_new") in an alert is noise; an unknown
# key falls through unchanged so a new tier never blanks the line.
CONDITION_ES = {
    "un_opened": "Sin abrir",
    "in_box": "En caja",
    "new": "Nuevo",
    "as_good_as_new": "Como nuevo",
    "good": "Buen estado",
    "fair": "Aceptable",
    "has_given_it_all": "Ha dado lo mejor de si",
}


def _eur(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}€".replace(",", ".")


def _signed(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:+,.0f}€".replace(",", ".")


def _age_str(seconds: float | None) -> str | None:
    """Human phrasing of listing age. The owner cares a lot about recency,
    so this gets its own line whenever posted_at/age_seconds is available.
    """
    if seconds is None or seconds < 0:
        return None
    minutes = seconds / 60
    if minutes < 1:
        return "hace <1 min"
    if minutes < 60:
        return f"hace {round(minutes)} min"
    hours = minutes / 60
    if hours < 24:
        return f"hace {round(hours)} h"
    return f"hace {round(hours / 24)} d"


def build_caption(
    item: Item,
    deal: Deal,
    kind: str,
    previous_price: float | None,
    model_display: str | None,
) -> str:
    """The §9 alert body, as Telegram HTML.

    No emoji anywhere — plain-text labels instead, so it reads as a normal
    message rather than a wall of icons. Bold is reserved for the numbers
    that matter (prices, margins) so the caption stays scannable.
    """
    if kind == "price_drop":
        head = "<b>BAJADA DE PRECIO</b>"
    elif deal.priced:
        head = "<b>CHOLLO</b>"
    else:
        head = "<b>COINCIDENCIA</b>"
    if model_display:
        head += f" · {html.escape(model_display)}"

    lines = [head]

    price_line = f"Precio: <b>{_eur(item.price)}</b>"
    if previous_price and item.price is not None and previous_price > item.price:
        price_line += f"  (antes <s>{_eur(previous_price)}</s>)"
    lines.append(price_line)

    if deal.priced:
        lines.append(
            f"Oferta sugerida: <b>{_eur(deal.offer_price)}</b> · "
            f"neto envío {_signed(deal.net_shipped)} / mano {_signed(deal.net_in_person)}"
        )
        margin = f"Ref: <b>{_eur(deal.ref_price)}</b> · Techo: <b>{_eur(deal.ceiling_shipped)}</b>"
        if deal.net_shipped_at_asking is not None and deal.net_shipped_at_asking >= 0:
            margin += f" · a precio pedido: <b>{_signed(deal.net_shipped_at_asking)}</b>"
        if deal.is_seed:
            margin += "  <i>(seed)</i>"
        elif deal.n_comps:
            margin += f"  <i>(n={deal.n_comps})</i>"
        lines.append(margin)

    # Comp-pool provenance (median_days_to_sale / n_sold / n_reserved) is
    # landing on Deal from a concurrent agent's pricing.py work — read
    # defensively so this file doesn't break if the shape differs. n_sold and
    # n_reserved default to 0 rather than None on that dataclass, and 0 comps
    # means "no signal" here, so treat them (and median_days_to_sale) as
    # absent whenever they're falsy and omit the whole line rather than
    # print a placeholder.
    median_days = getattr(deal, "median_days_to_sale", None)
    n_sold = getattr(deal, "n_sold", None)
    n_reserved = getattr(deal, "n_reserved", None)
    comp_bits = []
    if median_days is not None:
        comp_bits.append(f"venta media {median_days:.0f} días")
    if n_sold:
        comp_bits.append(f"{n_sold} vendidos")
    if n_reserved:
        comp_bits.append(f"{n_reserved} reservados")
    if comp_bits:
        lines.append("Histórico: " + " · ".join(comp_bits))

    # Same story for Item: condition/brand/posted_at are a concurrent
    # agent's addition, so getattr defends against the field being missing.
    detail_bits = []
    condition = getattr(item, "condition", None)
    if condition:
        detail_bits.append(f"Estado: {html.escape(CONDITION_ES.get(condition, condition))}")
    brand = getattr(item, "brand", None)
    if brand:
        detail_bits.append(f"Marca: {html.escape(str(brand))}")
    # Storage is the single biggest price lever on a phone, so it belongs next
    # to the condition rather than buried in the title.
    storage = getattr(item, "storage", None)
    if storage:
        detail_bits.append(f"Capacidad: {html.escape(str(storage).upper())}")
    age = _age_str(getattr(item, "age_seconds", None))
    if age:
        detail_bits.append(f"Publicado {age}")
    if detail_bits:
        lines.append(" · ".join(detail_bits))

    where = []
    if item.location:
        where.append(html.escape(str(item.location)))
    if item.country and item.country.upper() != "ES":
        where.append(html.escape(item.country.upper()))
    if item.distance_km is not None:
        try:
            where.append(f"{float(item.distance_km):.0f} km")
        except (TypeError, ValueError):
            pass
    # can_ship reflects the seller's actual choice on this listing; `shipping`
    # is only the category's general capability, so prefer can_ship when the
    # (concurrent-agent-added) field is present.
    can_ship = getattr(item, "can_ship", None)
    if can_ship is None:
        can_ship = item.shipping
    where.append("envío disponible" if can_ship else "solo en mano")
    lines.append("Ubicación: " + " · ".join(where))

    lines.append("Hora: " + datetime.now(MADRID).strftime("%Y-%m-%d %H:%M"))
    lines.append("")
    lines.append(f"<b>{html.escape(item.title[:150])}</b>")

    if item.description:
        desc = " ".join(item.description.split())[:DESC_CHARS]
        lines.append(html.escape(desc))

    lines.append("")
    lines.append(html.escape(item.web_url))

    caption = "\n".join(lines)
    if len(caption) > CAPTION_LIMIT:
        overflow = len(caption) - CAPTION_LIMIT + 3
        # Trim the description, never the link or the numbers.
        if item.description and overflow < DESC_CHARS:
            # lines[-3] is the description: [..., desc, "", link]
            lines[-3] = lines[-3][: max(0, len(lines[-3]) - overflow)] + "…"
            caption = "\n".join(lines)
        else:
            # Every line's HTML tags open and close within that same line, so
            # cutting at the last newline can never sever one — a mid-tag cut
            # would make Telegram reject the whole message as unparsable HTML.
            truncated = caption[: CAPTION_LIMIT - 1]
            safe_cut = truncated.rfind("\n")
            caption = (truncated[:safe_cut] if safe_cut > 0 else truncated) + "…"
    return caption


def build_error_text(text: str) -> str:
    """Error-ping body. No emoji, same as the deal caption — a bare text
    label reads just as clearly as a warning icon and stays consistent.
    """
    return f"<b>wallapop-bot</b>\n<pre>{html.escape(text[:1500])}</pre>"


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
                "text": build_error_text(text),
                "parse_mode": "HTML",
            },
        )
