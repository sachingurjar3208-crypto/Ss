"""DM every Premium member about My Player.

  venv/bin/python tools/premium_dm.py          -> TEST: only the owner gets it (also shows how many Premium users)
  venv/bin/python tools/premium_dm.py --send   -> every Premium member
"""
import asyncio
import os
import re
import subprocess
import sys

import aiohttp

OWNER = 1317288099075850243
GUILD = 1556644438917382150
ROLE = "1556957776415035423"
D = "<:energydrink:1572435428928135230>"

MSG = f"""💎 **New Premium Perk: MY PLAYER** 💎

Create **your own cricket card**. Only YOU will ever have it: it can't be bought, spawned, traded or sold.

**1️⃣ Create your player**
Type `rcmyp create <name>` (example: `rcmyp create Rahul Singh`)
Then pick your role, batting hand, bowling type and country:
• All-Rounder: Bat 60 / Bowl 60
• Batter: Bat 60 / Bowl 10
• Bowler: Bat 10 / Bowl 60
• Wicketkeeper: Bat 60 / Bowl 10
Your player starts at **60 OVR** and goes straight into your squad.

**2️⃣ Get Energy Drinks {D}**
• `rcdaily` → **+1** drink every day
• `rcweekly` → **+20** drinks every week
(Premium members only)

**3️⃣ Upgrade your player**
Type `rcmyp upgrade` to use your drinks and level up your player, all the way to **95 OVR**. Early levels are quick, higher levels need more drinks.

**4️⃣ Add your photo 📸**
Type `rcmyp photo` and attach your image. The owner checks it and approves it, then it shows on your card. (Until then your card shows a gold silhouette.)

**Other commands**
• `rcmyp`: see your card, stats and drinks
• If Premium ends, your card stays and still plays. Only upgrades are locked until you renew.

💎 **Bonus:** Premium members can now use **every Summary Theme**: type `rcsb`.

Enjoy, and good luck! 🏏"""


def token() -> str:
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if os.path.exists(here):
        for line in open(here):
            m = re.match(r"\s*DISCORD_TOKEN\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                return m.group(1)
    if os.getenv("DISCORD_TOKEN"):
        return os.environ["DISCORD_TOKEN"]
    env = subprocess.run(["systemctl", "show", "ssbot", "-p", "Environment"], capture_output=True, text=True).stdout
    m = re.search(r"DISCORD_TOKEN=(\S+)", env)
    if not m:
        sys.exit("Token not found")
    return m.group(1)


async def main(send_all: bool):
    h = {"Authorization": f"Bot {token()}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(headers=h) as s:
        users, after = [], "0"
        while True:
            async with s.get(f"https://discord.com/api/v10/guilds/{GUILD}/members?limit=1000&after={after}") as r:
                batch = await r.json()
            if not isinstance(batch, list) or not batch:
                break
            users += [int(m["user"]["id"]) for m in batch if ROLE in m["roles"] and not m["user"].get("bot")]
            after = batch[-1]["user"]["id"]
            if len(batch) < 1000:
                break
        print("Premium users:", len(users))
        targets = users if send_all else [OWNER]
        ok = fail = 0
        for uid in targets:
            async with s.post("https://discord.com/api/v10/users/@me/channels", json={"recipient_id": str(uid)}) as r:
                ch = (await r.json()).get("id")
            async with s.post(f"https://discord.com/api/v10/channels/{ch}/messages", json={"content": MSG}) as r:
                if r.status == 200:
                    ok += 1
                else:
                    fail += 1
                    print("failed", uid, r.status)
            await asyncio.sleep(1.5)
        print(f"{'SENT TO ALL' if send_all else 'TEST (owner only)'}: sent {ok}, failed {fail}")


if __name__ == "__main__":
    asyncio.run(main("--send" in sys.argv))
