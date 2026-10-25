HALLOWEEN THEME - kya badla
===========================

NEW FILE
  halloween_frame.png   Halloween frame (1037x1517, transparent bahar). card_style2.py ke bagal me rakho.

CHANGED FILES (same naam se replace karo)
  card_db.py        + column s2_theme (bot start par apne aap add hota hai)
                    + set_style2_options(theme=...)   + clone_card(theme=...)
  card_style2.py    + THEMES["halloween"]: frame + text colours. Normal cards bilkul pehle jaise (pixel-identical tested).
  cardmaker_cog.py  /cardmaker, /eventplayermaker, /editcard me naya option  theme: Normal / Halloween
  cardstats_cog.py  /cardstats me bhi  theme  option

HALLOWEEN CARD KA LOOK
  OVR + stat numbers   pumpkin orange (255,146,28)  + halka glow (OVR, name)
  Role / labels / name bone white (247,236,208)
  Lines                orange
  Frame                halloween_frame.png (purple shield + gold border ki jagah)
  Halloween hamesha Style 2 design me banta hai (theme choose karte hi apne aap Style 2 ho jata hai).
  Halloween card par `background` wala template dikhta nahi (frame apna hai), par command me pick karna padta hai.

USE
  /eventmaker eventname:Halloween 2026 ...                      (pehle ek baar event banao)
  /eventplayermaker foreground:<card> playername:Virat Kohli Halloween ovr:.. bat:.. bowl:.. background:<koi bhi>
                    design:Style 2 eventname:Halloween 2026 theme:Halloween
  /editcard playername:<card> theme:Halloween                   (purane card ko Halloween karna)
  /editcard playername:<card> theme:Normal                      (wapas purple)
  /cardstats player:<card> theme:Halloween pwr:.. run:.. ...    (stats ke saath)

DEPLOY
  1. Ye 5 files repo me copy karo (halloween_frame.png bhi, card_style2.py ke bagal me)
  2. git add . && git commit -m "Halloween theme" && git push
  3. VPS:  cd /root/ss-bot && git pull && systemctl restart ssbot
  4. Restart ke baad slash commands naye option ke saath sync hote hain (~1-2 min lag sakta hai)
  5. Purane cards ko Halloween karne ke baad /refreshcards ki zarurat nahi, /editcard khud card redraw karta hai.

NOTE
  Kisi card ka theme badalne ke baad squad / csview jaise jagah ki image cache apne aap warm ho jati hai (card_cache.warm).
  Naya theme baad me chahiye (Diwali, Christmas...): frame PNG daalo + card_style2.THEMES me ek entry (colours) jodo.
