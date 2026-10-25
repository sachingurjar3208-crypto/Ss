HALLOWEEN THEME v2 - frame GitHub me nahi, /bgadder se
=====================================================

Sirf 4 files replace karni hain (koi image GitHub me nahi jaati):
  card_db.py, card_style2.py, cardmaker_cog.py, cardstats_cog.py

STEPS
 1. 4 files repo me copy -> git push -> VPS: git pull && systemctl restart ssbot
 2. Halloween frame ki image /bgadder se add karo (naam e.g. HalloweenFrame).
    Image PNG/WEBP ho, bahar ka hissa transparent (jaisa tumne bheja).
 3. Card banao:
    /eventplayermaker ... background:HalloweenFrame design:Style 2 eventname:Halloween theme:Halloween
    /cardmaker ...        background:HalloweenFrame theme:Halloween
    /editcard playername:X background:HalloweenFrame theme:Halloween
    /cardstats player:X background:HalloweenFrame theme:Halloween

RULES
 - theme:Halloween me jo background choose karoge wahi frame banta hai.
 - Frame ke corners transparent hone chahiye. Agar normal (opaque) purple background choose kar diya
   to bot use frame nahi maanta aur card normal purple banta hai (crash nahi).
 - theme:Normal = wapas purple card.
 - Halloween hamesha Style 2 me banta hai.
