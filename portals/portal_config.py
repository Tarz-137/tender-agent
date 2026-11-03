from dataclasses import dataclass


@dataclass(frozen=True)
class PortalConfig:
    """Configuration for one tender portal deployment."""

    key: str
    name: str
    base_url: str
    platform: str
    enabled: bool = True
    closing_window_days: int = 14
    request_delay_seconds: float = 1.0
    discovery_strategy: str = "latest_and_closing"

_PORTAL_SPECS: dict[str, tuple[str, str]] = {
    "cppp": ("CPPP", "https://eprocure.gov.in/eprocure/app"),
     "cppp_epublish": (
        "CPPP ePublishing",
        "https://eprocure.gov.in/epublish/app",
    ),
    "madhya_pradesh": ("Madhya Pradesh", "https://mptenders.gov.in/nicgep/app"),
    "jammu_kashmir": ("Jammu and Kashmir", "https://jktenders.gov.in/nicgep/app"),
    "tamil_nadu": ("Tamil Nadu", "https://tntenders.gov.in/nicgep/app"),
    "haryana": ("Haryana", "https://etenders.hry.nic.in/nicgep/app"),
    "defence": ("Defence", "https://defproc.gov.in/nicgep/app"),
    "rajasthan": ("Rajasthan", "https://eproc.rajasthan.gov.in/nicgep/app"),
    "new_delhi": ("New Delhi", "https://govtprocurement.delhi.gov.in/nicgep/app"),
    "uttar_pradesh": ("Uttar Pradesh", "https://etender.up.nic.in/nicgep/app"),
    "chandigarh": ("Chandigarh", "https://etenders.chd.nic.in/nicgep/app"),
    "maharashtra": ("Maharashtra", "https://mahatenders.gov.in/nicgep/app"),
    "coal_india": ("Coal India", "https://coalindiatenders.nic.in/nicgep/app"),
    "himachal_pradesh": ("Himachal Pradesh", "https://hptenders.gov.in/nicgep/app"),
    "jharkhand": ("Jharkhand", "https://jharkhandtenders.gov.in/nicgep/app"),
    "west_bengal": ("West Bengal", "https://wbtenders.gov.in/nicgep/app"),
    "punjab": ("Punjab", "https://eproc.punjab.gov.in/nicgep/app"),
    "uttarakhand": ("Uttarakhand", "https://uktenders.gov.in/nicgep/app"),
    "kerala": ("Kerala", "https://etenders.kerala.gov.in/nicgep/app"),
    "assam": ("Assam", "https://assamtenders.gov.in/nicgep/app"),
    "goa": ("Goa", "https://eprocure.goa.gov.in/nicgep/app"),
    "odisha": ("Odisha", "https://tendersodisha.gov.in/nicgep/app"),
}


PORTALS: dict[str, PortalConfig] = {
    key: PortalConfig(
        key=key,
        name=name,
        base_url=base_url,
        platform="gepnic",
        discovery_strategy=(
            "organisation"
            if key == "cppp_epublish"
            else "latest_and_closing"
        )
    )
    for key, (name, base_url) in _PORTAL_SPECS.items()
}
