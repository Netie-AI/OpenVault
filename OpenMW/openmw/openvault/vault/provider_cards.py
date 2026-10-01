"""Catalog provider cards: Get key, paste once, test and add.

Cards exist only for catalog providers. DeepSeek is omitted (founder UI rule).
Cloudflare Workers AI has no catalog row. Local hops are not Free or Premium.
Premium cards are a plain paste field. No subscription claim, no ChatGPT sign-in.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from openmw.openvault.vault.key_add import AddResult
from openmw.openvault.vault.providers import PROVIDER_CATALOG, ProviderSpec

# Founder UI rule: no DeepSeek card. Cloudflare is not in the catalog.
_NO_CARD_IDS = frozenset({"deepseek"})
_ICON_DIR = Path(__file__).resolve().parent.parent / "static" / "provider_cards"
_PREMIUM_NOTE = "Paste your API key."


class CardView(TypedDict):
    id: str
    name: str
    register_url: str
    icon: str


class SectionView(TypedDict):
    id: str
    title: str
    note: str
    cards: list[CardView]


class CardsPayload(TypedDict):
    sections: list[SectionView]


@dataclass(frozen=True)
class ProviderCard:
    id: str
    name: str
    register_url: str
    section: str

    @property
    def icon(self) -> str:
        return f"/provider-cards/icons/{self.id}.svg"


def _section_for(spec: ProviderSpec) -> str | None:
    if spec.id in _NO_CARD_IDS or "cloudflare" in spec.id:
        return None
    if spec.tier == "paid":
        return "premium"
    if spec.tier in ("free", "freemium"):
        return "free"
    return None


def iter_cards() -> tuple[ProviderCard, ...]:
    """Free, then the rest of Free, then Premium, in catalog order."""
    cards: list[ProviderCard] = []
    for spec in PROVIDER_CATALOG:
        section = _section_for(spec)
        if section is None:
            continue
        cards.append(
            ProviderCard(
                id=spec.id,
                name=spec.name,
                register_url=spec.register_url,
                section=section,
            )
        )
    return tuple(cards)


def icon_path(icon_name: str) -> Path | None:
    """Local SVG for a card id. Rejects anything that is not that file."""
    if not icon_name.endswith(".svg"):
        return None
    stem = icon_name[: -len(".svg")]
    if stem not in {card.id for card in iter_cards()}:
        return None
    path = (_ICON_DIR / icon_name).resolve()
    try:
        path.relative_to(_ICON_DIR.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def cards_payload() -> CardsPayload:
    sections: list[SectionView] = []
    for section_id, title in (("free", "Free"), ("premium", "Premium")):
        rows = [card for card in iter_cards() if card.section == section_id]
        sections.append(
            {
                "id": section_id,
                "title": title,
                "note": _PREMIUM_NOTE if section_id == "premium" else "",
                "cards": [
                    {
                        "id": card.id,
                        "name": card.name,
                        "register_url": card.register_url,
                        "icon": card.icon,
                    }
                    for card in rows
                ],
            }
        )
    return {"sections": sections}


def add_response(result: AddResult) -> dict[str, str]:
    """Label, masked id, and outcome. The provider key is not a field."""
    if result.ok:
        outcome = "added"
    elif result.duplicate:
        outcome = "duplicate"
    else:
        outcome = result.error or "probe failed"
    return {
        "label": result.label,
        "masked_id": result.masked_id,
        "outcome": outcome,
    }


def _href(url: str) -> str:
    if url.startswith("https://") or url.startswith("http://"):
        return html.escape(url, quote=True)
    return ""


def _card_html(card: ProviderCard) -> str:
    name = html.escape(card.name)
    href = _href(card.register_url)
    icon = html.escape(card.icon, quote=True)
    note = ""
    if card.section == "premium":
        note = f'<p class="note">{html.escape(_PREMIUM_NOTE)}</p>'
    return (
        f'<article class="provider-card" data-provider="{html.escape(card.id, quote=True)}"'
        f' data-section="{card.section}">'
        f'<img src="{icon}" alt="" width="40" height="40">'
        f"<h3>{name}</h3>"
        f"{note}"
        f'<a class="get-key" href="{href}" target="_blank" rel="noopener noreferrer">Get key</a>'
        '<form class="card-add" method="post" action="/api/keys/cards">'
        "<label>API key"
        '<input type="password" name="secret" autocomplete="off" spellcheck="false">'
        "</label>"
        '<button type="submit">Test and add</button>'
        "</form>"
        '<p class="outcome"></p>'
        "</article>"
    )


def render_cards_page() -> str:
    """Server-rendered cards. The key is not in the page."""
    blocks: list[str] = []
    for section_id, title in (("free", "Free"), ("premium", "Premium")):
        rows = "".join(_card_html(card) for card in iter_cards() if card.section == section_id)
        blocks.append(
            f'<section id="provider-cards-{section_id}" data-section="{section_id}">'
            f"<h2>{title}</h2>{rows}</section>"
        )
    body = "".join(blocks)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenVault providers</title>
<style>
  body {{ margin: 0; font: 16px/1.4 system-ui, sans-serif; background: #101612; color: #eef4f0; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 28px 16px 64px; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 16px; }}
  h2 {{ font-size: 1.15rem; margin: 22px 0 10px; }}
  .provider-card {{
    border: 1px solid #2a362e; border-radius: 16px; padding: 14px; background: #1a221c;
  }}
  .provider-card h3 {{ margin: 8px 0 4px; font-size: 1rem; }}
  .note {{ color: #9aa8a0; font-size: 0.88rem; margin: 0 0 8px; }}
  a {{ color: #8fd0b0; }}
  label {{ display: block; margin-top: 10px; font-size: 0.8rem; color: #9aa8a0; }}
  input {{ width: 100%; margin-top: 4px; box-sizing: border-box; padding: 8px; border-radius: 8px;
    border: 1px solid #2a362e; background: #0e1410; color: inherit; }}
  button {{ margin-top: 10px; border: 0; border-radius: 999px; padding: 8px 12px; font-weight: 600;
    background: #1f8a5b; color: #fff; cursor: pointer; }}
  .outcome {{ min-height: 1.2em; font-size: 0.85rem; color: #9aa8a0; }}
</style>
</head>
<body>
<main>
<h1>Providers</h1>
{body}
</main>
<script>
document.querySelectorAll("form.card-add").forEach(function (form) {{
  form.addEventListener("submit", function (ev) {{
    ev.preventDefault();
    var card = form.closest("article");
    var field = form.querySelector("input[type=password]");
    var out = card.querySelector(".outcome");
    var secret = field.value;
    var provider = card.getAttribute("data-provider") || "";
    field.value = "";
    out.textContent = "";
    fetch("/api/keys/cards", {{
      method: "POST",
      headers: {{ "content-type": "application/json", "accept": "application/json" }},
      body: JSON.stringify({{ provider: provider, secret: secret }})
    }}).then(function (res) {{
      return res.json();
    }}).then(function (data) {{
      var label = String((data && data.label) || "");
      var masked = String((data && data.masked_id) || "");
      var outcome = String((data && data.outcome) || "");
      if (secret && (label + masked + outcome).indexOf(secret) !== -1) {{
        label = "";
        masked = "";
        outcome = "";
      }}
      secret = "";
      out.textContent = [label, masked, outcome].filter(Boolean).join(" ");
    }}).catch(function () {{
      secret = "";
      out.textContent = "test call failed (unreachable)";
    }});
  }});
}});
</script>
</body>
</html>
"""
