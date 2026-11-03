"""Convert a property's structured fields into knowledge chunks.

The admin edits structured fields (check-in time, Wi-Fi, house rules ...). These
are rendered into a system "profile" document so the same retrieval pipeline
answers questions about them. Sensitive fields (Wi-Fi password, access
instructions) become their own chunks flagged `is_sensitive`.
"""

from __future__ import annotations

from app.models import Property
from app.rag.chunking import Chunk
from app.schemas.property import SECTION_TITLES, SENSITIVE_FIELDS, PropertyDetails, field_label

PROFILE_FILENAME = "property-profile (auto-generated from structured fields)"


def build_profile_chunks(prop: Property) -> list[Chunk]:
    chunks: list[Chunk] = []

    def add(section: str, lines: list[str], sensitive: bool = False) -> None:
        lines = [ln for ln in lines if ln]
        if lines:
            text = f"{prop.name} - {section}\n" + "\n".join(lines)
            chunks.append(Chunk(index=len(chunks), section=section, text=text, is_sensitive=sensitive))

    basic = [
        f"Property name: {prop.name}",
        f"Location: {prop.location.name}, {prop.location.city}" if prop.location else "",
        f"Neighbourhood: {prop.neighbourhood}" if prop.neighbourhood else "",
        f"Address: {prop.address}" if prop.address else "",
        f"Bedrooms: {prop.bedrooms}" if prop.bedrooms is not None else "",
        f"Bathrooms: {prop.bathrooms:g}" if prop.bathrooms is not None else "",
        f"Maximum guests: {prop.max_guests}" if prop.max_guests is not None else "",
        f"Description: {prop.description}" if prop.description else "",
    ]
    if prop.is_placeholder:
        basic.append("NOTE: This is placeholder development data, not a real property.")
    add("Basic information", basic)
    if prop.amenities:
        add("Amenities", ["Available amenities: " + ", ".join(prop.amenities)])

    details = PropertyDetails.model_validate(prop.details or {})
    for section_key, title in SECTION_TITLES.items():
        section = getattr(details, section_key)
        normal: list[str] = []
        for field_name, value in section.model_dump().items():
            if not value:
                continue
            line = f"{field_label(field_name)}: {value}"
            if field_name in SENSITIVE_FIELDS:
                extra = []
                if field_name == "wifi_password" and details.facilities.wifi_name:
                    extra = [f"Wi-Fi network name: {details.facilities.wifi_name}"]
                add(f"{title} > {field_label(field_name)}", extra + [line], sensitive=True)
            else:
                normal.append(line)
        add(title, normal)
    return chunks
