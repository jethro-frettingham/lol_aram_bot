"""
ARAM Mayhem Match Review Bot
Polls Riot API for recent ARAM games, generates AI commentary via Claude,
and posts rich Discord embeds.
"""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
import boto3
from datetime import datetime, timezone

# ── Constants ────────────────────────────────────────────────────────────────
QUEUE_ID_ARAM = 450          # Standard ARAM
QUEUE_ID_ARAM_MAYHEM = 900   # Arena / Mayhem variant (URF-ish ARAM events)
MAYHEM_QUEUE_IDS = {450, 900, 1020, 1300}  # All fun-mode ARAM-adjacent queues

REGION_ROUTING = {
    "OCE":  ("oc1",  "asia"),
    "NA":   ("na1",  "americas"),
    "EUW":  ("euw1", "europe"),
    "EUNE": ("eune1","europe"),
    "KR":   ("kr",   "asia"),
}

STAT_EMOJIS = {
    "kills":          "⚔️",
    "deaths":         "💀",
    "assists":        "🤝",
    "totalDamageDealtToChampions": "🔥",
    "totalDamageTaken":            "🛡️",
    "damageSelfMitigated":         "🔰",
    "totalHeal":                   "💚",
    "goldEarned":                  "💰",
    "visionScore":                 "👁️",
    "pentaKills":                  "🏆",
    "largestKillingSpree":         "🔥",
}

RANK_MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

# ── SSM helpers ───────────────────────────────────────────────────────────────
_ssm = boto3.client("ssm", region_name=os.environ["AWS_REGION"])
_param_cache: dict = {}

def get_param(name: str) -> str:
    if name not in _param_cache:
        resp = _ssm.get_parameter(Name=name, WithDecryption=True)
        _param_cache[name] = resp["Parameter"]["Value"]
    return _param_cache[name]

# ── DynamoDB (seen-games dedup) ───────────────────────────────────────────────
_ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
_table = _ddb.Table(os.environ["SEEN_GAMES_TABLE"])

def is_seen(match_id: str) -> bool:
    resp = _table.get_item(Key={"match_id": match_id})
    return "Item" in resp

def mark_seen(match_id: str):
    ttl = int(time.time()) + 60 * 60 * 24 * 30  # 30 days
    _table.put_item(Item={"match_id": match_id, "ttl": ttl})

# ── Riot API helpers ──────────────────────────────────────────────────────────
def riot_get(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers={"X-Riot-Token": api_key})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def get_puuid(game_name: str, tag_line: str, routing: str, api_key: str) -> str:
    url = (
        f"https://{routing}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
        f"{urllib.parse.quote(game_name)}/{urllib.parse.quote(tag_line)}"
    )
    print(f"Calling: {url}")
    return riot_get(url, api_key)["puuid"]

def get_recent_match_ids(puuid: str, routing: str, api_key: str, count: int = 5, any_queue: bool = False) -> list:
    if any_queue:
        url = (
            f"https://{routing}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
            f"{puuid}/ids?start=0&count={count}"
        )
    else:
        queue_param = "&".join(f"queue={q}" for q in MAYHEM_QUEUE_IDS)
        url = (
            f"https://{routing}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
            f"{puuid}/ids?{queue_param}&start=0&count={count}"
        )
    return riot_get(url, api_key)

def get_match(match_id: str, routing: str, api_key: str) -> dict:
    url = f"https://{routing}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    return riot_get(url, api_key)

# ── Data helpers ──────────────────────────────────────────────────────────────
def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def kda_str(k, d, a) -> str:
    ratio = (k + a) / max(d, 1)
    return f"{k}/{d}/{a} ({ratio:.1f} KDA)"

def get_augments(participant: dict) -> list[str]:
    """Extract augment names from participant data (Arena/Mayhem only)."""
    augments = []
    for i in range(1, 7):
        aug = participant.get(f"playerAugment{i}")
        if aug and aug != 0:
            augments.append(f"Augment {i}: `{aug}`")
    return augments

def build_stat_summary(p: dict) -> str:
    lines = []
    kda = kda_str(p["kills"], p["deaths"], p["assists"])
    lines.append(f"⚔️ **KDA:** {kda}")
    lines.append(f"🔥 **Damage dealt:** {fmt_num(p['totalDamageDealtToChampions'])}")
    lines.append(f"🛡️ **Damage taken:** {fmt_num(p['totalDamageTaken'])}")
    mitigated = p.get("damageSelfMitigated", 0)
    if mitigated:
        lines.append(f"🔰 **Mitigated:** {fmt_num(mitigated)}")
    healed = p.get("totalHeal", 0)
    if healed > 500:
        lines.append(f"💚 **Healed:** {fmt_num(healed)}")
    lines.append(f"💰 **Gold:** {fmt_num(p['goldEarned'])}")
    if p.get("pentaKills", 0):
        lines.append(f"🏆 **PENTA KILL!!**")
    elif p.get("quadraKills", 0):
        lines.append(f"🎖️ Quadra kill!")
    cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
    if cs:
        lines.append(f"🌾 **CS:** {cs}")
    return "\n".join(lines)

def player_summary_for_ai(p: dict, champion: str, won: bool) -> str:
    """Compact text description for Claude to roast/praise."""
    augments = get_augments(p)
    aug_str = ", ".join(augments) if augments else "none"
    return (
        f"Champion: {champion}, Result: {'WIN' if won else 'LOSS'}, "
        f"KDA: {p['kills']}/{p['deaths']}/{p['assists']}, "
        f"Damage dealt: {p['totalDamageDealtToChampions']}, "
        f"Damage taken: {p['totalDamageTaken']}, "
        f"Self-mitigated: {p.get('damageSelfMitigated', 0)}, "
        f"Healed: {p.get('totalHeal', 0)}, "
        f"Gold: {p['goldEarned']}, "
        f"Augments: {aug_str}, "
        f"Penta kills: {p.get('pentaKills', 0)}, "
        f"Largest spree: {p.get('largestKillingSpree', 0)}"
    )

# ── Claude AI commentary ──────────────────────────────────────────────────────
def generate_commentary(players_info: list[dict], match_won: bool) -> dict[str, str]:
    """
    Call Claude to generate a short witty comment per player.
    Returns {summoner_name: comment}
    """
    api_key = get_param(os.environ["ANTHROPIC_KEY_PARAM"])

    players_text = "\n".join(
        f"- {p['name']}: {p['summary']}" for p in players_info
    )
    outcome = "won" if match_won else "lost"

    prompt = (
        f"You are a witty, cheeky League of Legends analyst reviewing an ARAM Mayhem game "
        f"between a group of friends. The team {outcome} the match.\n\n"
        f"Players:\n{players_text}\n\n"
        f"Write ONE short (max 25 words), punchy, funny comment for each player based on their stats. "
        f"Be playful and roast them or praise them as appropriate. "
        f"Reference specific stats, augments, or moments. "
        f"Reply ONLY with a JSON object like: "
        f'{{"{players_info[0]["name"]}": "comment", ...}}'
    )

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())

    text = resp["content"][0]["text"].strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)

# ── Discord helpers ───────────────────────────────────────────────────────────
ROLE_COLORS = {
    True:  0x57F287,   # Green  = WIN
    False: 0xED4245,   # Red    = LOSS
}

POSITION_ICON = {
    "UTILITY": "🩺", "SUPPORT": "🩺",
    "BOTTOM":  "🏹", "ADC": "🏹",
    "MIDDLE":  "🎯", "MID": "🎯",
    "TOP":     "🪓",
    "JUNGLE":  "🌲",
    "NONE":    "🃏",
}

def build_discord_payload(
    match_id: str,
    match_data: dict,
    our_players: list[dict],    # list of participant dicts for our tracked summoners
    won: bool,
    commentary: dict[str, str],
    region_platform: str,
) -> dict:
    info = match_data["info"]
    duration_min = info["gameDuration"] // 60
    duration_sec = info["gameDuration"] % 60
    game_mode = info.get("gameMode", "ARAM")
    queue_id   = info.get("queueId", 450)

    # Map name
    game_mode_label = {
        450:  "🎲 ARAM",
        900:  "🌀 ARAM Mayhem (URF)",
        1020: "🌀 One for All",
        1300: "🎉 Nexus Blitz",
    }.get(queue_id, f"🎮 {game_mode}")

    timestamp = datetime.fromtimestamp(
        info["gameStartTimestamp"] / 1000, tz=timezone.utc
    ).isoformat()

    result_label = "🏆 VICTORY" if won else "💀 DEFEAT"
    color = ROLE_COLORS[won]

    # Header embed
    fields = []
    for p in our_players:
        sname = p["summonerName"]
        champ = p["championName"]
        stats = build_stat_summary(p)
        augments = get_augments(p)
        aug_field = "\n".join(f"✨ {a}" for a in augments) if augments else ""
        ai_comment = commentary.get(sname, "")
        pos_icon = POSITION_ICON.get(
            p.get("teamPosition", "NONE").upper(), "🃏"
        )

        field_value = stats
        if aug_field:
            field_value += f"\n\n**Augments**\n{aug_field}"
        if ai_comment:
            field_value += f"\n\n> 🤖 *{ai_comment}*"

        fields.append({
            "name": f"{pos_icon} **{sname}** — {champ}",
            "value": field_value[:1024],  # Discord field limit
            "inline": False,
        })

    # Build op.gg link for first tracked player
    opgg_name = our_players[0]["summonerName"].replace(" ", "%20")
    opgg_region = region_platform.lower()
    opgg_url = f"https://www.op.gg/summoners/{opgg_region}/{opgg_name}/matches/{match_id}"
    # Deeplol link
    deeplol_url = f"https://www.deeplol.gg/summoner/{opgg_region}/{opgg_name}"

    payload = {
        "embeds": [
            {
                "title": f"{result_label}  •  {game_mode_label}",
                "description": (
                    f"⏱️ **Duration:** {duration_min}m {duration_sec:02d}s\n"
                    f"📅 <t:{int(info['gameStartTimestamp']/1000)}:R>\n"
                    f"[📊 Full stats on op.gg]({opgg_url})  •  "
                    f"[🔬 Deep analysis]({deeplol_url})"
                ),
                "color": color,
                "fields": fields,
                "timestamp": timestamp,
                "footer": {
                    "text": f"Match ID: {match_id}  •  ARAM Mayhem Bot 🤖"
                },
            }
        ]
    }
    return payload

def post_to_discord(webhook_url: str, payload: dict):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        status = r.status
    if status not in (200, 204):
        raise RuntimeError(f"Discord returned HTTP {status}")

# ── Main handler ──────────────────────────────────────────────────────────────
def handler(event, context):
    riot_key     = get_param(os.environ["RIOT_KEY_PARAM"])
    webhook_url  = get_param(os.environ["DISCORD_WEBHOOK_PARAM"])
    region_code  = os.environ.get("REGION", "OCE")
    platform, routing = REGION_ROUTING[region_code]

    # Load tracked players: "GameName#TAG,GameName2#TAG2"
    players_raw = os.environ["TRACKED_PLAYERS"].split(",")
    tracked: list[tuple[str, str]] = []
    for entry in players_raw:
        entry = entry.strip()
        if "#" in entry:
            name, tag = entry.rsplit("#", 1)
            tracked.append((name.strip(), tag.strip()))

    if not tracked:
        print("No tracked players configured.")
        return

    # Resolve PUUIDs
    # Supports two formats in TRACKED_PLAYERS:
    #   GameName#TAG            -> resolved via Riot API (may be blocked from AWS IPs)
    #   GameName#TAG=<puuid>    -> PUUID hardcoded, skips API lookup entirely
    puuids: dict[str, str] = {}  # puuid -> display name
    for game_name, tag_line in tracked:
        # Check if PUUID is hardcoded after '='
        hardcoded_puuid = None
        if "=" in tag_line:
            tag_line, hardcoded_puuid = tag_line.split("=", 1)

        display = f"{game_name}#{tag_line}"
        if hardcoded_puuid:
            puuids[hardcoded_puuid] = display
            print(f"Using hardcoded PUUID for {display}")
        else:
            try:
                puuid = get_puuid(game_name, tag_line, routing, riot_key)
                puuids[puuid] = display
            except Exception as e:
                print(f"Failed to resolve {display}: {e}")

    if not puuids:
        print("No PUUIDs resolved.")
        return

    # Collect candidate match IDs across all tracked players
    # ANY_QUEUE=true fetches all recent games regardless of mode (useful for debugging)
    any_queue = os.environ.get("ANY_QUEUE", "false").lower() == "true"
    all_match_ids: set[str] = set()
    for puuid in puuids:
        try:
            ids = get_recent_match_ids(puuid, routing, riot_key, count=5, any_queue=any_queue)
            print(f"Recent match IDs for {puuids[puuid]}: {ids}")
            all_match_ids.update(ids)
        except Exception as e:
            print(f"Failed to fetch matches for {puuids[puuid]}: {e}")

    # Log queue IDs so we can see what modes were recently played
    for mid in list(all_match_ids)[:5]:
        try:
            m = get_match(mid, routing, riot_key)
            qid = m["info"].get("queueId")
            mode = m["info"].get("gameMode")
            in_list = qid in MAYHEM_QUEUE_IDS
            print(f"  Match {mid}: queueId={qid} mode={mode} - {'in watchlist' if in_list else 'skipping (not ARAM mode)'}")
        except Exception as e:
            print(f"  Could not inspect {mid}: {e}")

    new_matches = [mid for mid in all_match_ids if not is_seen(mid)]
    print(f"Found {len(new_matches)} new match(es) to process.")

    for match_id in new_matches:
        try:
            match_data = get_match(match_id, routing, riot_key)
            info = match_data["info"]

            # Filter to only participants who are our tracked players
            our_participants = []
            for p in info["participants"]:
                if p["puuid"] in puuids:
                    p["summonerName"] = puuids[p["puuid"]]
                    our_participants.append(p)

            if not our_participants:
                mark_seen(match_id)
                continue

            # Did our team win?
            our_team_id = our_participants[0]["teamId"]
            won = our_participants[0]["win"]

            # Build AI player summaries
            players_info = [
                {
                    "name": p["summonerName"],
                    "summary": player_summary_for_ai(p, p["championName"], won),
                }
                for p in our_participants
            ]

            try:
                commentary = generate_commentary(players_info, won)
            except Exception as e:
                print(f"AI commentary failed: {e}")
                commentary = {}

            payload = build_discord_payload(
                match_id, match_data, our_participants,
                won, commentary, platform
            )
            post_to_discord(webhook_url, payload)
            print(f"Posted match {match_id} to Discord.")

        except Exception as e:
            print(f"Error processing match {match_id}: {e}")
        finally:
            mark_seen(match_id)

        time.sleep(1)  # Be polite to Riot's rate limits

    return {"processed": len(new_matches)}