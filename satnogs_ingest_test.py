"""
SatNOGS DB telemetry smoke test (Ingestion stage, CS467)

What it does:
  1. Hits /api/telemetry/ with your token (proves the key works)
  2. Prints the fields of the first few frames
  3. Parses the AX.25 header of each frame by hand (callsigns + payload)
  4. Follows pagination for a couple pages so you can see how it works
"""
import os
import sys
import requests

BASE = "https://db.satnogs.org/api/telemetry/"
TOKEN = os.environ.get("SATNOGS_DB_TOKEN")


def parse_ax25(frame_hex: str):
    """Minimal AX.25 UI-frame parser. Returns (dest, src, payload) or None."""
    try:
        b = bytes.fromhex(frame_hex)
    except ValueError:
        return None
    addrs, i = [], 0
    # Address fields are 7 bytes each; callsign chars are shifted left 1 bit.
    # The low bit of the 7th byte marks the last address.
    while i + 7 <= len(b):
        call = "".join(chr(c >> 1) for c in b[i:i + 6]).strip()
        ssid = (b[i + 6] >> 1) & 0x0F
        addrs.append(f"{call}-{ssid}" if ssid else call)
        last = b[i + 6] & 0x01
        i += 7
        if last:
            break
    if len(addrs) < 2 or i + 2 > len(b):
        return None
    control, pid = b[i], b[i + 1]
    if control != 0x03 or pid != 0xF0:   # 0x03/0xF0 = UI frame, no layer 3
        return None
    return addrs[0], addrs[1], b[i + 2:]


def main():
    if not TOKEN:
        sys.exit("Set SATNOGS_DB_TOKEN first (DB token, NOT the Network token).")
    norad = sys.argv[1] if len(sys.argv) > 1 else "25544"

    headers = {"Authorization": f"Token {TOKEN}"}
    params = {"satellite": norad, "format": "json"}
    url, page = BASE, 0

    while url and page < 2:
        r = requests.get(url, headers=headers, params=params if page == 0 else None, timeout=30)
        print(f"\nGET {r.url} -> HTTP {r.status_code}")
        if r.status_code == 401:
            sys.exit("401: bad/wrong token. Make sure it's the DB token.")
        if r.status_code == 400:
            sys.exit("400: probably missing/invalid satellite NORAD ID.")
        r.raise_for_status()

        data = r.json()
        frames = data["results"] if isinstance(data, dict) else data
        print(f"{len(frames)} frames on this page")

        for f in frames[:3]:
            print("-" * 60)
            for k in ("norad_cat_id", "timestamp", "observer", "app_source", "transmitter"):
                print(f"{k:>13}: {f.get(k)}")
            print(f"{'frame (hex)':>13}: {f.get('frame', '')[:80]}...")
            parsed = parse_ax25(f.get("frame", ""))
            if parsed:
                dest, src, payload = parsed
                print(f"{'AX.25':>13}: {src} -> {dest}, payload {len(payload)} bytes")
                print(f"{'payload':>13}: {payload[:60]!r}")
            else:
                print(f"{'AX.25':>13}: not a plain AX.25 UI frame (needs a sat-specific decoder)")

        # Pagination: check the JSON 'next' field or the HTTP Link header
        url = (data.get("next") if isinstance(data, dict) else None) or r.links.get("next", {}).get("url")
        page += 1

    print("\nDone. If you saw frames above, your token and the endpoint both work.")


if __name__ == "__main__":
    main()
