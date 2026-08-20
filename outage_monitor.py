#!/usr/bin/env python3
"""Overvåk strømbrudd i Østfold og send e-post om nyhetsverdige endringer.

Programmet bruker bare Python-standardbiblioteket. Det er laget for GitHub
Actions, men kan også kjøres lokalt.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ELVIA_SERVICE = (
    "https://services-eu1.arcgis.com/AcdYbPzrkOfBOQDL/arcgis/rest/services/"
    "avbrudd2_offentlig_visning/FeatureServer/1/query"
)
ELVIA_MAP = "https://www.elvia.no/strombruddskart/"

NORGESNETT_SERVICE = (
    "https://utility.arcgis.com/usrsvcs/servers/"
    "d76865927ade4b598be0004b14c5bc93/rest/services/DRS/"
    "Stromstans_public/MapServer/1/query"
)
NORGESNETT_MAP = (
    "https://norgesnett.maps.arcgis.com/apps/dashboards/"
    "ec73745cd7ca4d42ab5bf99072b77753"
)
MUNICIPALITY_SERVICE = (
    "https://services7.arcgis.com/P4kTx2a4fSxXXbpD/ArcGIS/rest/services/"
    "Kommuner/FeatureServer/0/query"
)

OSTFOLD_MUNICIPALITIES = (
    "Aremark",
    "Fredrikstad",
    "Halden",
    "Hvaler",
    "Indre Østfold",
    "Marker",
    "Moss",
    "Rakkestad",
    "Råde",
    "Sarpsborg",
    "Skiptvet",
    "Våler",
)
NORGESNETT_OSTFOLD_MUNICIPALITIES = {"Fredrikstad", "Hvaler"}

MIN_CUSTOMERS = int(os.getenv("MIN_CUSTOMERS", "50"))
MIN_COMBINED_CUSTOMERS = int(os.getenv("MIN_COMBINED_CUSTOMERS", "100"))
MIN_COUNT_INCREASE = int(os.getenv("MIN_COUNT_INCREASE", "100"))
MIN_DELAY_SECONDS = int(os.getenv("MIN_DELAY_MINUTES", "60")) * 60
ELVIA_SCOPE = (os.getenv("ELVIA_SCOPE") or "ostfold").strip().casefold()
MONITOR_LABEL = os.getenv("MONITOR_LABEL") or (
    "Hele Elvias nettområde" if ELVIA_SCOPE == "all" else "Østfold"
)
CRITICAL_KEYWORDS = {
    word.strip().casefold()
    for word in os.getenv(
        "CRITICAL_KEYWORDS",
        "sykehus,legevakt,vannverk,pumpestasjon,omsorgssenter,sykehjem,"
        "nødsentral,nødetat,jernbane",
    ).split(",")
    if word.strip()
}
OSLO = ZoneInfo("Europe/Oslo")
USER_AGENT = "ostfold-strombrudd-monitor/1.0 (journalistisk overvaking)"


@dataclass(frozen=True)
class Outage:
    key: str
    source: str
    source_id: str
    municipality: str
    place: str
    customers: int
    kind: str
    started_at: str | None
    expected_end: str | None
    description: str
    source_url: str
    longitude: float | None = None
    latitude: float | None = None

    @property
    def is_unplanned(self) -> bool:
        return self.kind == "unplanned"


@dataclass(frozen=True)
class Alert:
    category: str
    headline: str
    details: str
    outages: tuple[Outage, ...]


def _iso_from_millis(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(float(value) / 1000, tz=UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _format_time(value: str | None) -> str:
    parsed = _parse_iso(value)
    if not parsed:
        return "ikke oppgitt"
    return parsed.astimezone(OSLO).strftime("%d.%m.%Y kl. %H.%M")


def fetch_json(url: str, params: dict[str, Any], retries: int = 3) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.load(response)
            if "error" in payload:
                raise RuntimeError(f"ArcGIS-feil: {payload['error']}")
            return payload
        except Exception as exc:  # Nettverksfeil må prøves igjen.
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"Klarte ikke å hente {url}: {last_error}") from last_error


def fetch_elvia() -> list[Outage]:
    if ELVIA_SCOPE == "all":
        where = "1=1"
    elif ELVIA_SCOPE == "ostfold":
        quoted = ",".join(
            f"'{name.replace(chr(39), chr(39) * 2)}'"
            for name in OSTFOLD_MUNICIPALITIES
        )
        where = f"kommune IN ({quoted})"
    else:
        raise RuntimeError(
            f"Ukjent ELVIA_SCOPE={ELVIA_SCOPE!r}. Bruk 'all' eller 'ostfold'."
        )
    payload = fetch_json(
        ELVIA_SERVICE,
        {
            "where": where,
            "outFields": (
                "OBJECTID,antallkunder,kommune,poststed,strombruddoppdaget,"
                "utkoblingstart,utkoblingslutt,avbruddstype,nettstasjon"
            ),
            "returnGeometry": "false",
            "resultRecordCount": 2000,
            "f": "json",
        },
    )
    outages: list[Outage] = []
    for feature in payload.get("features", []):
        attrs = feature.get("attributes", {})
        source_id = str(attrs.get("OBJECTID", ""))
        raw_type = str(attrs.get("avbruddstype") or "").casefold()
        kind = "planned" if "planned" in raw_type or "planlagt" in raw_type else "unplanned"
        started = attrs.get("strombruddoppdaget") or attrs.get("utkoblingstart")
        station = str(attrs.get("nettstasjon") or "").strip()
        outages.append(
            Outage(
                key=f"elvia:{source_id}",
                source="Elvia",
                source_id=source_id,
                municipality=str(attrs.get("kommune") or "Ukjent"),
                place=str(attrs.get("poststed") or "Ukjent sted"),
                customers=int(attrs.get("antallkunder") or 0),
                kind=kind,
                started_at=_iso_from_millis(started),
                expected_end=_iso_from_millis(attrs.get("utkoblingslutt")),
                description=f"Nettstasjon {station}" if station else "",
                source_url=ELVIA_MAP,
            )
        )
    return outages


def lookup_municipality(longitude: float, latitude: float) -> str | None:
    payload = fetch_json(
        MUNICIPALITY_SERVICE,
        {
            "geometry": f"{longitude},{latitude}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "kommunenavn",
            "returnGeometry": "false",
            "f": "json",
        },
        retries=2,
    )
    features = payload.get("features", [])
    if not features:
        return None
    return str(features[0].get("attributes", {}).get("kommunenavn") or "") or None


def fetch_norgesnett() -> list[Outage]:
    payload = fetch_json(
        NORGESNETT_SERVICE,
        {
            "where": "1=1",
            "outFields": (
                "STROMSTANSID,REFNR,FRA_DATO,TIL_DATO,CNT,LOGGTYPE,BESKRIVELSE"
            ),
            "returnGeometry": "true",
            "outSR": 4326,
            "resultRecordCount": 2000,
            "f": "json",
        },
    )
    outages: list[Outage] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry", {})
        longitude = float(geometry.get("x", 0))
        latitude = float(geometry.get("y", 0))

        # Billig forhåndsfilter: Norgesnetts øvrige områder ligger utenfor denne boksen.
        if not (10.45 <= longitude <= 11.35 and 58.75 <= latitude <= 59.42):
            continue
        municipality = lookup_municipality(longitude, latitude)
        if municipality not in NORGESNETT_OSTFOLD_MUNICIPALITIES:
            continue

        attrs = feature.get("attributes", {})
        source_id = str(attrs.get("STROMSTANSID") or attrs.get("REFNR") or "")
        raw_type = str(attrs.get("LOGGTYPE") or "").casefold()
        kind = "planned" if "planlagt" in raw_type else "unplanned"
        description = str(attrs.get("BESKRIVELSE") or "").strip()
        outages.append(
            Outage(
                key=f"norgesnett:{source_id}",
                source="Norgesnett",
                source_id=source_id,
                municipality=municipality,
                place=f"{municipality} ({latitude:.4f}, {longitude:.4f})",
                customers=int(attrs.get("CNT") or 0),
                kind=kind,
                started_at=_iso_from_millis(attrs.get("FRA_DATO")),
                expected_end=_iso_from_millis(attrs.get("TIL_DATO")),
                description=description,
                source_url=NORGESNETT_MAP,
                longitude=longitude,
                latitude=latitude,
            )
        )
    return outages


def load_state(path: Path) -> dict[str, Outage]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            key: Outage(**value)
            for key, value in payload.get("outages", {}).items()
        }
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Ugyldig tilstandsfil {path}: {exc}") from exc


def save_state(path: Path, outages: dict[str, Outage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_outages = {key: asdict(outages[key]) for key in sorted(outages)}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("outages", {}) == serialized_outages:
                return
        except (OSError, ValueError, TypeError):
            # En skadet fil skal erstattes med gyldig tilstand nedenfor.
            pass
    payload = {
        "version": 1,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "outages": serialized_outages,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(rendered, encoding="utf-8")


def _critical(outage: Outage) -> bool:
    haystack = f"{outage.place} {outage.description}".casefold()
    return any(word in haystack for word in CRITICAL_KEYWORDS)


def _end_delayed(old: Outage, new: Outage) -> bool:
    old_end = _parse_iso(old.expected_end)
    new_end = _parse_iso(new.expected_end)
    return bool(old_end and new_end and (new_end - old_end).total_seconds() >= MIN_DELAY_SECONDS)


def detect_alerts(
    current: dict[str, Outage],
    previous: dict[str, Outage],
    successful_sources: set[str],
) -> list[Alert]:
    alerts: list[Alert] = []
    individually_alerted: set[str] = set()

    for key, outage in sorted(current.items()):
        if outage.source not in successful_sources or not outage.is_unplanned:
            continue
        old = previous.get(key)
        if old is None:
            if outage.customers >= MIN_CUSTOMERS or _critical(outage):
                reason = (
                    f"Nytt uplanlagt strømbrudd med {outage.customers} berørte kunder."
                )
                if _critical(outage) and outage.customers < MIN_CUSTOMERS:
                    reason += " Beskrivelsen treffer et kritisk nøkkelord."
                alerts.append(
                    Alert(
                        category="new",
                        headline=f"Nytt strømbrudd i {outage.municipality}",
                        details=reason,
                        outages=(outage,),
                    )
                )
                individually_alerted.add(key)
            continue

        reasons: list[str] = []
        if outage.customers - old.customers >= MIN_COUNT_INCREASE:
            reasons.append(
                f"Antall berørte har økt fra {old.customers} til {outage.customers}."
            )
        elif old.customers > 0 and outage.customers >= old.customers * 2:
            reasons.append(
                f"Antall berørte er minst doblet, fra {old.customers} til {outage.customers}."
            )
        if _end_delayed(old, outage):
            reasons.append(
                "Forventet rettetid er utsatt fra "
                f"{_format_time(old.expected_end)} til {_format_time(outage.expected_end)}."
            )
        if outage.description and outage.description != old.description:
            reasons.append(f"Ny beskrivelse: {outage.description}")
        if reasons:
            alerts.append(
                Alert(
                    category="update",
                    headline=f"Vesentlig utvikling i {outage.municipality}",
                    details=" ".join(reasons),
                    outages=(outage,),
                )
            )
            individually_alerted.add(key)

    # Flere små, samtidige brudd kan samlet være nyhetsverdige.
    municipalities = sorted(
        {
            outage.municipality
            for outage in (*current.values(), *previous.values())
            if outage.source in successful_sources
        }
    )
    for municipality in municipalities:
        group = tuple(
            outage
            for outage in current.values()
            if outage.source in successful_sources
            and outage.is_unplanned
            and outage.municipality == municipality
            and outage.key not in individually_alerted
            and outage.customers < MIN_CUSTOMERS
        )
        if len(group) < 2 or sum(item.customers for item in group) < MIN_COMBINED_CUSTOMERS:
            continue
        previous_keys = {
            item.key
            for item in previous.values()
            if item.source in successful_sources
            and item.is_unplanned
            and item.municipality == municipality
            and item.customers < MIN_CUSTOMERS
        }
        current_keys = {item.key for item in group}
        previous_total = sum(previous[key].customers for key in previous_keys if key in previous)
        current_total = sum(item.customers for item in group)
        if current_keys != previous_keys or (
            previous_total < MIN_COMBINED_CUSTOMERS <= current_total
        ):
            alerts.append(
                Alert(
                    category="combined",
                    headline=f"Flere samtidige strømbrudd i {municipality}",
                    details=(
                        f"{len(group)} mindre brudd berører samlet {current_total} kunder."
                    ),
                    outages=tuple(sorted(group, key=lambda item: item.customers, reverse=True)),
                )
            )

    # Et større brudd som forsvinner fra en vellykket kilde tolkes som rettet.
    for key, old in sorted(previous.items()):
        if (
            old.source in successful_sources
            and old.is_unplanned
            and old.customers >= MIN_CUSTOMERS
            and key not in current
        ):
            alerts.append(
                Alert(
                    category="resolved",
                    headline=f"Strømmen tilbake i {old.municipality}",
                    details=(
                        f"Et tidligere brudd som berørte {old.customers} kunder "
                        "er ikke lenger registrert som aktivt."
                    ),
                    outages=(old,),
                )
            )
    return alerts


def _news_value(alert: Alert) -> str:
    affected = sum(outage.customers for outage in alert.outages)
    if alert.category == "resolved":
        return "Relevant oppfølging dersom avisen allerede har omtalt bruddet."
    if affected >= 1000:
        return "Høy nyhetsverdi: omfattende avbrudd som bør sjekkes umiddelbart."
    if affected >= 250:
        return "Tydelig lokal nyhetsverdi; sjekk årsak og berørte områder."
    return "Mulig kort nyhetsmelding; vurder sted, varighet og tidspunkt."


def render_email(alerts: Iterable[Alert]) -> tuple[str, str]:
    alerts = list(alerts)
    municipalities = sorted(
        {outage.municipality for alert in alerts for outage in alert.outages}
    )
    if len(alerts) == 1:
        subject = f"[Strømbrudd] {alerts[0].headline}"
    else:
        subject = f"[Strømbrudd] {len(alerts)} hendelser i {', '.join(municipalities)}"

    lines = [f"STRØMBRUDDVARSEL – {MONITOR_LABEL.upper()}", ""]
    for alert in alerts:
        lines.extend([alert.headline, alert.details])
        for outage in alert.outages:
            lines.extend(
                [
                    f"- Kommune: {outage.municipality}",
                    f"- Sted: {outage.place}",
                    f"- Berørte kunder: {outage.customers}",
                    f"- Registrert: {_format_time(outage.started_at)}",
                    f"- Forventet rettet: {_format_time(outage.expected_end)}",
                    f"- Kilde: {outage.source} – {outage.source_url}",
                ]
            )
            if outage.description:
                lines.append(f"- Beskrivelse: {outage.description}")
        lines.extend([f"Nyhetsvurdering: {_news_value(alert)}", ""])
    lines.append("Opplysningene bør verifiseres hos nettselskapet før publisering.")
    return subject, "\n".join(lines)


def _email_config() -> dict[str, Any]:
    required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_TO"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Mangler e-postinnstillinger: " + ", ".join(missing)
        )
    username = os.environ["SMTP_USERNAME"]
    return {
        "host": os.environ["SMTP_HOST"],
        "port": int(os.getenv("SMTP_PORT") or "587"),
        "security": (os.getenv("SMTP_SECURITY") or "starttls").casefold(),
        "username": username,
        "password": os.environ["SMTP_PASSWORD"],
        "sender": os.getenv("EMAIL_FROM") or username,
        "recipients": [
            address.strip()
            for address in os.environ["EMAIL_TO"].replace(";", ",").split(",")
            if address.strip()
        ],
    }


def send_email(subject: str, body: str) -> None:
    config = _email_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["sender"]
    message["To"] = ", ".join(config["recipients"])
    message.set_content(body)

    context = ssl.create_default_context()
    if config["security"] == "ssl":
        with smtplib.SMTP_SSL(
            config["host"], config["port"], timeout=30, context=context
        ) as smtp:
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)
        return

    with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
        smtp.ehlo()
        if config["security"] == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config["username"], config["password"])
        smtp.send_message(message)


def fetch_all(previous: dict[str, Outage]) -> tuple[dict[str, Outage], set[str]]:
    current: dict[str, Outage] = {}
    successful_sources: set[str] = set()
    errors: list[str] = []
    for source, fetcher in (("Elvia", fetch_elvia), ("Norgesnett", fetch_norgesnett)):
        try:
            fetched = fetcher()
            current.update({outage.key: outage for outage in fetched})
            successful_sources.add(source)
        except Exception as exc:
            errors.append(f"{source}: {exc}")
            # Behold gammel tilstand for feilet kilde, slik at det ikke sendes falsk
            # melding om at strømmen er tilbake.
            current.update(
                {key: outage for key, outage in previous.items() if outage.source == source}
            )
    if not successful_sources:
        raise RuntimeError("Begge kildene feilet. " + " | ".join(errors))
    if errors:
        print("ADVARSEL: " + " | ".join(errors), file=sys.stderr)
    return current, successful_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("state/outages.json"),
        help="Sti til JSON-fil med forrige kjøring.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hent og vurder data, men ikke send e-post eller endre tilstand.",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send en testmelding uten å hente strømbrudd.",
    )
    args = parser.parse_args()

    if args.test_email:
        send_email(
            "[Strømbrudd] Test av e-postvarsling",
            "E-postvarslingen for strømbrudd i Østfold virker.",
        )
        print("Testmelding sendt.")
        return 0

    previous = load_state(args.state)
    current, successful_sources = fetch_all(previous)
    alerts = detect_alerts(current, previous, successful_sources)
    if alerts:
        subject, body = render_email(alerts)
        if args.dry_run:
            print(subject)
            print(body)
        else:
            send_email(subject, body)
            print(f"Sendte {len(alerts)} varsel/varsler.")
    else:
        print("Ingen nye strømbrudd som oppfyller varslingskriteriene.")

    if not args.dry_run:
        save_state(args.state, current)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FEIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
